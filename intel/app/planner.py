"""Rolling-horizon LP allocation planner (implementation_plan.md §7, §12.3).

scipy.optimize.linprog(method="highs") with sparse constraint matrices. Only
the t=0 decisions are executed by the backend (`actions`); t>0 decisions are
informational (`pipeline`). See Final-Project.md §4.4 for the model.

Variable layout (all continuous, >= 0), t = 0..H-1 relative to the request tick:
    x[route, fuel, t]     litres dispatched at t (only for allowed combos)
    I[station, fuel, t]   station inventory
    u[station, fuel, t]   unmet demand
    o[station, fuel, t]   station overflow (loss)
    v[station, fuel, t]   safety-stock gap
    J[depot, fuel, t]     depot inventory
    w[depot, fuel, t]     depot overflow (loss)
    z                     worst unmet share (fairness)
"""
from __future__ import annotations

import math
import time

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

from . import forecast as fc
from . import risk as rk

FUEL_TYPES = fc.FUEL_TYPES

# Must exceed H*W_SS_GAP (48*5=240) or the LP would withhold available fuel
# to reduce safety-stock penalties instead of serving demand.
W_UNMET = 1000.0
W_LOSS = 150.0
W_SS_GAP = 5.0
# Worst-unmet-share fairness (z): a tie-breaker among otherwise-equally-good
# solutions, not a guarantee of equitable allocation -- W_UNMET already
# dominates the primary objective.
W_FAIRNESS = 2000.0
W_TRANSIT = 0.01
# Tiny per-tick-of-delay tie-breaker: shipping now vs. an equally-good later
# departure on the same route costs the same W_TRANSIT, so without this the
# solver can arbitrarily defer an action into `pipeline` instead of `actions`.
EARLINESS_BIAS = 0.001
W_TERMINAL = -0.05
MIN_ACTION_LITERS = 200.0
SOLVE_TIME_LIMIT_S = 0.8


def _event_covers(event, tick_abs: int) -> bool:
    return event.status in ("SCHEDULED", "ACTIVE") and event.start_tick <= tick_abs < event.end_tick


def _ids_filter(event, key: str, entity_id: str) -> bool:
    ids = event.parameters.get(key) or []
    return (not ids) or (entity_id in ids)


class _Vars:
    """Registers LP variable indices and accumulates the objective vector."""

    def __init__(self) -> None:
        self.index: dict[tuple, int] = {}
        self.cost: list[float] = []

    def add(self, key: tuple, cost: float = 0.0) -> int:
        idx = len(self.cost)
        self.index[key] = idx
        self.cost.append(cost)
        return idx

    def add_cost(self, key: tuple, cost: float) -> None:
        idx = self.index.get(key)
        if idx is not None:
            self.cost[idx] += cost

    @property
    def n(self) -> int:
        return len(self.cost)


class _Rows:
    """Accumulates a sparse (row, col, val) linear system with a rhs vector."""

    def __init__(self) -> None:
        self.rows: list[int] = []
        self.cols: list[int] = []
        self.vals: list[float] = []
        self.rhs: list[float] = []
        self._n = 0

    def new_row(self, rhs: float) -> int:
        r = self._n
        self._n += 1
        self.rhs.append(rhs)
        return r

    def put(self, row: int, col: int, val: float) -> None:
        self.rows.append(row)
        self.cols.append(col)
        self.vals.append(val)

    def matrix(self, n_cols: int):
        if self._n == 0:
            return None, None
        m = sparse.coo_matrix((self.vals, (self.rows, self.cols)), shape=(self._n, n_cols))
        return m.tocsr(), np.array(self.rhs, dtype=float)


class _ArrivalStub:
    """Duck-types the fields of schemas.InTransit for a synthetic arrival."""

    __slots__ = ("station_id", "fuel_type", "quantity", "eta_tick")

    def __init__(self, station_id: str, fuel_type: str, quantity: float, eta_tick: int) -> None:
        self.station_id = station_id
        self.fuel_type = fuel_type
        self.quantity = quantity
        self.eta_tick = eta_tick


def solve_plan(req) -> dict:
    t_start = time.perf_counter()
    tick = req.tick
    tick_minutes = req.tick_minutes or 15
    H = max(1, req.settings.horizon_ticks or 48)
    # Risk rows and impact before/after project over their own horizon
    # (default 96 ticks = 24h at 15-min ticks), independent of the LP horizon,
    # so tiers like WATCH ("stockout < 16 h") stay observable even when the
    # LP horizon is shorter.
    risk_horizon = max(1, req.settings.risk_horizon_ticks or 96)
    safety_z = req.settings.safety_z if req.settings.safety_z is not None else 1.28
    constrained_factor = req.settings.constrained_factor
    if constrained_factor is None:
        constrained_factor = 0.5
    priorities = req.settings.priorities or {}

    stations = {s.id: s for s in req.stations}
    depots = {d.id: d for d in req.depots}
    routes = list(req.routes)
    routes_by_id = {r.id: r for r in routes}
    events = list(req.events)

    # --- forecast fit per station|fuel ---------------------------------------------
    fits: dict[tuple[str, str], fc.SeriesFit] = {}
    for s in req.stations:
        for f in FUEL_TYPES:
            series = req.demand_history.get(fc.series_key(s.id, f))
            ticks_h = series.ticks if series else []
            values_h = series.values if series else []
            multipliers_h = series.multipliers if series else None
            future_mult_h = series.future_multipliers if series else None
            fits[(s.id, f)] = fc.fit_series(
                ticks_h, values_h, tick_minutes, s.demand_profile, f, s.demand_multiplier,
                multipliers=multipliers_h, future_multipliers=future_mult_h, tick=tick,
            )

    def demand_hat(station, fuel: str, t: int) -> float:
        if station.status == "OUTAGE":
            return 0.0
        return fits[(station.id, fuel)].mean(tick + t, tick_minutes, station.demand_profile)

    # --- dynamic blocking (guards F6: a disruption starting now blocks t=0 too) ----
    def route_blocked(route, t: int) -> bool:
        if t == 0 and route.status != "AVAILABLE":
            return True
        tick_abs = tick + t
        for e in events:
            if e.type == "route_disruption" and _event_covers(e, tick_abs) and _ids_filter(e, "route_ids", route.id):
                return True
        return False

    def station_blocked(station, t: int) -> bool:
        if t == 0 and station.status == "OUTAGE":
            return True
        tick_abs = tick + t
        for e in events:
            if e.type == "station_outage" and _event_covers(e, tick_abs) and _ids_filter(e, "station_ids", station.id):
                return True
        return False

    def depot_constrained(depot, t: int) -> bool:
        if t == 0 and depot.status == "CONSTRAINED":
            return True
        tick_abs = tick + t
        for e in events:
            if e.type == "depot_constraint" and _event_covers(e, tick_abs) and _ids_filter(e, "depot_ids", depot.id):
                return True
        return False

    allowed: dict[tuple[str, str, int], bool] = {}
    for r in routes:
        dest = stations.get(r.destination_station_id)
        src_ok = r.source_depot_id in depots
        for f in FUEL_TYPES:
            for t in range(H):
                ok = src_ok and dest is not None and not route_blocked(r, t) and not station_blocked(dest, t)
                allowed[(r.id, f, t)] = ok

    # --- register variables ----------------------------------------------------------
    V = _Vars()
    x_keys: dict[tuple[str, str, int], int] = {}
    for r in routes:
        for f in FUEL_TYPES:
            for t in range(H):
                if allowed[(r.id, f, t)]:
                    x_keys[(r.id, f, t)] = V.add(("x", r.id, f, t), cost=W_TRANSIT * r.transit_ticks)

    I_keys: dict[tuple[str, str, int], int] = {}
    u_keys: dict[tuple[str, str, int], int] = {}
    o_keys: dict[tuple[str, str, int], int] = {}
    v_keys: dict[tuple[str, str, int], int] = {}
    for s in req.stations:
        for f in FUEL_TYPES:
            prio = priorities.get(f"{s.id}|{f}", 1.0)
            for t in range(H):
                I_keys[(s.id, f, t)] = V.add(("I", s.id, f, t))
                u_keys[(s.id, f, t)] = V.add(("u", s.id, f, t), cost=W_UNMET * prio)
                o_keys[(s.id, f, t)] = V.add(("o", s.id, f, t), cost=W_LOSS)
                v_keys[(s.id, f, t)] = V.add(("v", s.id, f, t), cost=W_SS_GAP)
            V.add_cost(("I", s.id, f, H - 1), W_TERMINAL)

    J_keys: dict[tuple[str, str, int], int] = {}
    w_keys: dict[tuple[str, str, int], int] = {}
    for d in req.depots:
        for f in FUEL_TYPES:
            for t in range(H):
                J_keys[(d.id, f, t)] = V.add(("J", d.id, f, t))
                w_keys[(d.id, f, t)] = V.add(("w", d.id, f, t), cost=W_LOSS)

    z_idx = V.add(("z",), cost=W_FAIRNESS)
    n = V.n

    eq = _Rows()
    ub = _Rows()

    # --- known inbound: in-transit arrivals + not-yet-arrived supply ---------------
    station_arrivals: dict[tuple[str, str], list[float]] = {}
    for it in req.in_transit:
        if it.station_id not in stations:
            continue
        arr = station_arrivals.setdefault((it.station_id, it.fuel_type), [0.0] * H)
        t = max(0, it.eta_tick - tick)
        if t < H:
            arr[t] += it.quantity

    depot_supply: dict[tuple[str, str], list[float]] = {}
    for sup in req.supply:
        if sup.depot_id not in depots:
            continue
        arr = depot_supply.setdefault((sup.depot_id, sup.fuel_type), [0.0] * H)
        t = max(0, sup.planned_tick - tick)
        if t < H:
            arr[t] += sup.quantity

    # --- station balance -------------------------------------------------------------
    for s in req.stations:
        inbound_routes = [r for r in routes if r.destination_station_id == s.id]
        min_transit = rk.min_open_transit(s.id, routes)
        lead = max(1, min_transit if min_transit != math.inf else 1)
        for f in FUEL_TYPES:
            i0 = float(s.inventory.get(f, 0.0))
            cap = float(s.capacity.get(f, 0.0))
            arr = station_arrivals.get((s.id, f), [0.0] * H)
            ss = safety_z * fits[(s.id, f)].sigma * math.sqrt(lead)
            for t in range(H):
                row = eq.new_row(0.0)
                eq.put(row, I_keys[(s.id, f, t)], 1.0)
                if t > 0:
                    eq.put(row, I_keys[(s.id, f, t - 1)], -1.0)
                eq.put(row, u_keys[(s.id, f, t)], -1.0)
                eq.put(row, o_keys[(s.id, f, t)], 1.0)
                for r in inbound_routes:
                    xk = x_keys.get((r.id, f, t - r.transit_ticks))
                    if xk is not None:
                        eq.put(row, xk, -1.0)
                dhat = demand_hat(s, f, t)
                eq.rhs[row] = (i0 if t == 0 else 0.0) + arr[t] - dhat

                row_u = ub.new_row(dhat)
                ub.put(row_u, u_keys[(s.id, f, t)], 1.0)
                if t == 0:
                    # Demand already reachable from stock on hand or committed arrivals
                    # cannot be withheld to shift service into a later tick.
                    row_now = ub.new_row(max(0.0, dhat - min(cap, i0 + arr[t])))
                    ub.put(row_now, u_keys[(s.id, f, t)], 1.0)

                # Capacity applies BEFORE this tick's consumption: the
                # simulator clips arrivals the instant they land, not after
                # demand is netted out of inventory. From the balance eq
                # above, I_t - u_t = I_{t-1} + arrivals_t - o_t, so bounding
                # I_t - u_t <= cap - Dhat_t is equivalent to bounding the
                # pre-consumption stock I_{t-1} + arrivals_t <= cap + o_t --
                # letting o_t absorb the excess as overflow loss exactly when
                # it actually occurs, instead of after fictitious consumption.
                row_cap = ub.new_row(cap - dhat)
                ub.put(row_cap, I_keys[(s.id, f, t)], 1.0)
                ub.put(row_cap, u_keys[(s.id, f, t)], -1.0)

                row_ss = ub.new_row(-ss)
                ub.put(row_ss, I_keys[(s.id, f, t)], -1.0)
                ub.put(row_ss, v_keys[(s.id, f, t)], -1.0)

    # --- depot balance -----------------------------------------------------------------
    for d in req.depots:
        outbound_routes = [r for r in routes if r.source_depot_id == d.id]
        for f in FUEL_TYPES:
            j0 = float(d.inventory.get(f, 0.0))
            cap = float(d.capacity.get(f, 0.0))
            sup = depot_supply.get((d.id, f), [0.0] * H)
            for t in range(H):
                row = eq.new_row(0.0)
                eq.put(row, J_keys[(d.id, f, t)], 1.0)
                if t > 0:
                    eq.put(row, J_keys[(d.id, f, t - 1)], -1.0)
                eq.put(row, w_keys[(d.id, f, t)], 1.0)
                for r in outbound_routes:
                    xk = x_keys.get((r.id, f, t))
                    if xk is not None:
                        eq.put(row, xk, 1.0)
                eq.rhs[row] = (j0 if t == 0 else 0.0) + sup[t]

                row_cap = ub.new_row(cap)
                ub.put(row_cap, J_keys[(d.id, f, t)], 1.0)

    # --- dispatch capacity per depot per tick (all fuels share the pipe) -------------
    for d in req.depots:
        outbound_routes = [r for r in routes if r.source_depot_id == d.id]
        for t in range(H):
            factor = constrained_factor if depot_constrained(d, t) else 1.0
            cap_t = d.dispatch_capacity_per_tick * factor
            if t == 0:
                cap_t = max(0.0, cap_t - req.pending_dispatch.get(d.id, 0.0))
            row = ub.new_row(cap_t)
            for r in outbound_routes:
                for f in FUEL_TYPES:
                    xk = x_keys.get((r.id, f, t))
                    if xk is not None:
                        ub.put(row, xk, 1.0)

    # --- fairness: sum_t u <= z * sum_t Dhat, per station/fuel -----------------------
    for s in req.stations:
        for f in FUEL_TYPES:
            total_d = sum(demand_hat(s, f, t) for t in range(H))
            if total_d <= 1e-9:
                continue
            row = ub.new_row(0.0)
            for t in range(H):
                ub.put(row, u_keys[(s.id, f, t)], 1.0)
            ub.put(row, z_idx, -total_d)

    A_eq, b_eq = eq.matrix(n)
    A_ub, b_ub = ub.matrix(n)
    c = np.array(V.cost, dtype=float)
    bounds = [(0, None)] * n

    result = linprog(
        c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds,
        method="highs", options={"time_limit": SOLVE_TIME_LIMIT_S},
    )
    solve_ms = (time.perf_counter() - t_start) * 1000.0

    ok = bool(result.success) and result.status == 0
    status = "optimal" if ok else ("infeasible" if result.status == 2 else "error")

    actions: list[dict] = []
    pipeline: list[dict] = []
    binding: list[dict] = []
    solved_x: dict[tuple[str, str, int], float] = {}
    # Internal-only (not part of the /v1/plan contract; PlanResponse's
    # extra="ignore" drops this over HTTP) t=0 LP variable dump so tests can
    # check the actual solved u/o/I without adding public API surface.
    debug_t0: dict[str, dict[tuple[str, str], float]] = {"u": {}, "o": {}, "I": {}}

    if ok:
        sol = result.x
        for key, idx in x_keys.items():
            val = sol[idx]
            if val > 1e-6:
                solved_x[key] = val

        for s in req.stations:
            for f in FUEL_TYPES:
                debug_t0["u"][(s.id, f)] = float(sol[u_keys[(s.id, f, 0)]])
                debug_t0["o"][(s.id, f)] = float(sol[o_keys[(s.id, f, 0)]])
                debug_t0["I"][(s.id, f)] = float(sol[I_keys[(s.id, f, 0)]])

        reserved_now: dict[tuple[str, str], float] = {}
        for (rid, f, t), val in solved_x.items():
            if t == 0:
                station = stations[routes_by_id[rid].destination_station_id]
                cell = (station.id, f)
                # The simulator validates station headroom at POST time, before
                # any in-transit consumption. Do not expose an unexecutable LP action.
                room = station.capacity.get(f, 0.0) - station.inventory.get(f, 0.0) - reserved_now.get(cell, 0.0)
                litres = math.floor(min(val, room))
                if litres >= MIN_ACTION_LITERS:
                    actions.append({"route_id": rid, "fuel_type": f, "quantity": float(litres)})
                    reserved_now[cell] = reserved_now.get(cell, 0.0) + litres
            elif val > MIN_ACTION_LITERS:
                pipeline.append({"route_id": rid, "fuel_type": f, "quantity": float(val), "tick": tick + t})

        binding = _binding_constraints(req, x_keys, I_keys, sol, depots, stations, routes, constrained_factor)

    # --- risk (independent of solve outcome) ------------------------------------------
    risk_rows = []
    for s in req.stations:
        for f in rk.fuels_for(s):
            fit = fits.get((s.id, f)) or fc.fit_series(
                [], [], tick_minutes, s.demand_profile, f, s.demand_multiplier, tick=tick,
            )
            risk_rows.append(
                rk.assess(s, f, routes, req.supply, req.in_transit, fit, tick, tick_minutes, risk_horizon, safety_z)
            )

    impact = _build_impact(req, risk_rows, solved_x, routes_by_id, fits, tick, tick_minutes, risk_horizon, safety_z)

    wapes = [fit.wape for fit in fits.values()]
    mean_wape = sum(wapes) / len(wapes) if wapes else 0.0
    tph = rk.ticks_per_hour(tick_minutes)
    n8 = max(1, math.ceil(8 * tph))
    forecast_series = {}
    for s in req.stations:
        for f in FUEL_TYPES:
            fit = fits[(s.id, f)]
            next_8h = sum(demand_hat(s, f, t) for t in range(min(n8, H)))
            sigma_8h = fit.sigma * math.sqrt(min(n8, H))
            forecast_series[fc.series_key(s.id, f)] = {"next_8h": next_8h, "sigma_8h": sigma_8h}

    return {
        "policy": "lp-v1",
        "status": status,
        "solve_ms": solve_ms,
        "actions": actions,
        "pipeline": pipeline,
        "risk": [rk.to_risk_entry(r) for r in risk_rows],
        "impact": impact,
        "binding": binding,
        "forecast": {"wape": mean_wape, "series": forecast_series},
        "_debug_t0": debug_t0,
    }


def _binding_constraints(req, x_keys, I_keys, sol, depots, stations, routes, constrained_factor) -> list[dict]:
    """Name tight constraints for stations that received a t=0 action."""
    pairs: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for (rid, f, t), idx in x_keys.items():
        if t != 0 or sol[idx] < MIN_ACTION_LITERS:
            continue
        route = next((r for r in routes if r.id == rid), None)
        if route is None:
            continue
        pairs.setdefault((route.destination_station_id, f), []).append((route.source_depot_id, route.transit_ticks))

    out = []
    for (sid, f), sources in pairs.items():
        constraints: list[str] = []
        for did in {d for d, _ in sources}:
            depot = depots.get(did)
            if depot is None:
                continue
            factor = constrained_factor if depot.status == "CONSTRAINED" else 1.0
            cap_t0 = max(0.0, depot.dispatch_capacity_per_tick * factor - req.pending_dispatch.get(did, 0.0))
            used = sum(
                sol[x_keys[(r.id, ff, 0)]]
                for r in routes if r.source_depot_id == did
                for ff in FUEL_TYPES if (r.id, ff, 0) in x_keys
            )
            if cap_t0 > 0 and used >= 0.99 * cap_t0:
                constraints.append(f"{did} dispatch capacity {cap_t0:,.0f} L/tick")
            j0 = float(depot.inventory.get(f, 0.0))
            if j0 > 1.0 and (j0 - used) <= max(0.02 * j0, 50.0):
                constraints.append(f"{did} stock near empty")
        station = stations.get(sid)
        if station is not None:
            cap = float(station.capacity.get(f, 0.0))
            transit = sources[0][1]
            idx = I_keys.get((sid, f, transit))
            if idx is not None and cap > 0 and sol[idx] >= 0.99 * cap:
                constraints.append(f"{sid} tank capacity {cap:,.0f} L tight at arrival")
        out.append({"station_id": sid, "fuel_type": f, "constraints": constraints})
    return out


def _build_impact(req, risk_rows, solved_x, routes_by_id, fits, tick, tick_minutes, H, safety_z) -> list[dict]:
    """Before/after impact for stations with an action plus all HIGH/CRITICAL ones."""
    stations = {s.id: s for s in req.stations}
    target_pairs: set[tuple[str, str]] = set()
    for row in risk_rows:
        if row["tier"] in ("HIGH", "CRITICAL"):
            target_pairs.add((row["station_id"], row["fuel_type"]))

    synthetic: list[_ArrivalStub] = []
    for (rid, f, t), val in solved_x.items():
        if val < MIN_ACTION_LITERS:
            continue
        route = routes_by_id.get(rid)
        if route is None:
            continue
        target_pairs.add((route.destination_station_id, f))
        synthetic.append(_ArrivalStub(route.destination_station_id, f, val, tick + t + route.transit_ticks))

    by_key = {(row["station_id"], row["fuel_type"]): row for row in risk_rows}
    routes = list(routes_by_id.values())
    impact = []
    for sid, f in target_pairs:
        station = stations.get(sid)
        before = by_key.get((sid, f))
        if station is None or before is None:
            continue
        fit = fits.get((sid, f))
        after_in_transit = list(req.in_transit) + [
            a for a in synthetic if a.station_id == sid and a.fuel_type == f
        ]
        after = rk.assess(station, f, routes, req.supply, after_in_transit, fit, tick, tick_minutes, H, safety_z)
        impact.append({
            "station_id": sid,
            "fuel_type": f,
            "p_stockout_before": before["p_stockout_8h"],
            "p_stockout_after": after["p_stockout_8h"],
            "unmet_before_liters": before["unmet_total"],
            "unmet_after_liters": after["unmet_total"],
            "hours_to_stockout_before": before["hours_to_stockout"],
            "hours_to_stockout_after": after["hours_to_stockout"],
        })
    return impact
