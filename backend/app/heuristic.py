"""heuristic-v1: the fallback policy (used when intel is down) and the benchmark baseline."""
import math
import time

from .models import Snapshot
from .state import (future_multipliers, in_transit, in_transit_list, normal_cdf, pending_dispatch,
                    route_blocked, simple_forecast)


def project(inv: float, arrivals: list[float], forecast: list[float], ticks_per_hour: float):
    """Return (hours_to_stockout | None, P(stockout within 8 h), unmet litres over the horizon)."""
    mean, var, stock, unmet, hts, pmax = inv, 0.0, inv, 0.0, None, 0.0
    eight_h = int(8 * ticks_per_hour)
    for k, demand in enumerate(forecast, start=1):
        arr = arrivals[k] if k < len(arrivals) else 0.0
        mean += arr - demand
        var += (0.12 * demand) ** 2
        available = stock + arr
        unmet += max(0.0, demand - available)
        stock = max(0.0, available - demand)
        if hts is None and mean < 0:
            hts = round(k / ticks_per_hour, 2)
        if k <= eight_h:
            pmax = max(pmax, normal_cdf(-mean / max(math.sqrt(var), 1e-6)))
    return hts, round(pmax, 3), round(unmet, 1)


def tier(p: float, hts: float | None) -> str:
    h = hts if hts is not None else 1e9
    if p >= 0.8 or h < 4:
        return "CRITICAL"
    if p >= 0.5 or h < 8:
        return "HIGH"
    if p >= 0.2 or h < 16:
        return "WATCH"
    return "OK"


def plan(snap: Snapshot, history: dict, horizon: int = 48, safety_z: float = 1.28,
         constrained_factor: float = 0.5, mult_logs: dict | None = None) -> dict:
    t0 = time.perf_counter()
    tick, tm = snap.tick, snap.instance.tick_minutes
    tph = 60 / tm
    horizon = max(horizon, math.ceil(24 * tph))  # risk needs >= 16 h (WATCH tier); project 24 h
    transit_now = in_transit(snap)
    arrivals_by = {}
    for it in in_transit_list(snap):
        arr = arrivals_by.setdefault((it["station_id"], it["fuel_type"]), [0.0] * (horizon + 1))
        k = max(1, it["eta_tick"] - tick)
        if k <= horizon:
            arr[k] += it["quantity"]
    depots = {d.id: d for d in snap.depots}
    regions = {s.id: s.region_id for s in snap.stations}
    open_routes_into = {}
    for r in snap.routes:
        if not route_blocked(snap, r, tick):
            open_routes_into.setdefault(r.destination_station_id, []).append(r)

    risk, cells = [], []
    for s in snap.stations:
        fut = future_multipliers(snap, s, horizon)
        for fuel, cap in s.capacity.items():
            fc = simple_forecast(history, s, fuel, tick, tm, horizon, (mult_logs or {}).get(s.id), fut)
            arr = arrivals_by.get((s.id, fuel), [0.0] * (horizon + 1))
            inv = s.inventory.get(fuel, 0.0)
            hts, p8, unmet = project(inv, arr, fc, tph)
            reasons = []
            if s.status != "OPEN":
                reasons.append("station outage")
            if s.demand_multiplier != 1:
                reasons.append(f"demand x{s.demand_multiplier:g}")
            if hts is not None:
                reasons.append(f"stockout in {hts:g} h")
            routes_in = open_routes_into.get(s.id, [])
            min_transit = min((r.transit_ticks for r in routes_in), default=None)
            unavoidable = hts is not None and (min_transit is None or hts * tph < min_transit)
            if not routes_in:
                reasons.append("no open route")
            row = {"station_id": s.id, "fuel_type": fuel, "inventory": round(inv, 1), "capacity": cap,
                   "in_transit": round(transit_now.get((s.id, fuel), 0.0), 1),
                   "demand_next_8h": round(sum(fc[: int(8 * tph)]), 1), "hours_to_stockout": hts,
                   "p_stockout_8h": p8, "tier": tier(p8, hts), "confidence": 0.6, "confidence_label": "MEDIUM",
                   "unavoidable": unavoidable, "reasons": reasons}
            risk.append(row)
            cells.append((s, fuel, fc, arr, row))

    dispatch_left = {}
    pend = pending_dispatch(snap)
    for d in depots.values():
        factor = constrained_factor if d.status == "CONSTRAINED" else 1.0
        dispatch_left[d.id] = max(0.0, d.dispatch_capacity_per_tick * factor - pend.get(d.id, 0.0))
    depot_left = {d.id: dict(d.inventory) for d in depots.values()}

    actions, impact = [], []
    order = sorted(cells, key=lambda c: (c[4]["hours_to_stockout"] if c[4]["hours_to_stockout"] is not None else 1e9,
                                         -c[4]["p_stockout_8h"]))
    for s, fuel, fc, arr, row in order:
        if s.status != "OPEN":
            continue
        routes_in = sorted(open_routes_into.get(s.id, []),
                           key=lambda r: (r.transit_ticks, -depot_left[r.source_depot_id].get(fuel, 0.0)))
        for r in routes_in:
            lead = r.transit_ticks
            demand_to_arrival = sum(fc[:lead])
            mean_fc = sum(fc) / len(fc) if fc else 0.0
            ss = safety_z * 0.12 * mean_fc * math.sqrt(lead)
            stock_at_arrival = row["inventory"] + row["in_transit"] - demand_to_arrival
            reorder_point = sum(fc[lead: lead + int(8 * tph)]) + ss      # 8 h of cover after arrival
            if stock_at_arrival > reorder_point:
                break
            target = min(s.capacity[fuel] * 0.95, sum(fc[lead: lead + int(16 * tph)]) + ss)  # order up to 16 h
            need = target - stock_at_arrival
            headroom = min(s.capacity[fuel] - s.inventory[fuel], s.capacity[fuel] - max(0.0, stock_at_arrival))
            q = min(need, headroom, depot_left[r.source_depot_id].get(fuel, 0.0), dispatch_left[r.source_depot_id],
                    r.max_shipment * 2)
            if q < 300:
                continue
            q = math.floor(q)
            actions.append({"route_id": r.id, "fuel_type": fuel, "quantity": float(q)})
            depot_left[r.source_depot_id][fuel] -= q
            dispatch_left[r.source_depot_id] -= q
            arr2 = list(arr)
            if lead < len(arr2):
                arr2[lead] += q
            hts2, p2, unmet2 = project(row["inventory"], arr2, fc, tph)
            _, _, unmet1 = project(row["inventory"], arr, fc, tph)
            impact.append({"station_id": s.id, "fuel_type": fuel, "p_stockout_before": row["p_stockout_8h"],
                           "p_stockout_after": p2, "unmet_before_liters": unmet1, "unmet_after_liters": unmet2,
                           "hours_to_stockout_before": row["hours_to_stockout"], "hours_to_stockout_after": hts2,
                           "cross_region": depots[r.source_depot_id].region_id != regions[s.id]})
            break
    return {"policy": "heuristic-v1", "status": "optimal", "solve_ms": round((time.perf_counter() - t0) * 1000, 2),
            "actions": actions, "pipeline": [], "risk": risk, "impact": impact, "binding": [],
            "forecast": {"wape": None, "series": {}}}
