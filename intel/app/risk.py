"""Stockout risk projection per station x fuel (implementation_plan.md §12.2).

`assess` runs a simple additive mass-balance projection over the horizon using
the forecaster's mean/sigma, and derives tier, confidence and "unavoidable"
from it. It also returns a couple of internal-only fields (`unmet_total`,
`stockout_idx`) that the planner reuses to build the `impact` section without
recomputing the projection twice; `to_risk_entry` strips those back out to the
exact `/v1/plan` risk-entry contract shape.

The caller (planner.solve_plan) passes `settings.risk_horizon_ticks` (default
96 = 24h at 15-min ticks) as `horizon` here, independent of the shorter LP
planning horizon, so longer-dated tiers such as WATCH ("stockout < 16 h")
stay observable.

`p_stockout_8h` is an approximate stockout risk (est.): a Gaussian-tail
estimate from this simple additive projection, not a calibrated probability.
"""
from __future__ import annotations

import math

from . import forecast as fc

FUEL_TYPES = fc.FUEL_TYPES

# Public contract fields, in order -- everything else `assess` returns is
# internal bookkeeping for the planner's impact section.
_PUBLIC_FIELDS = (
    "station_id", "fuel_type", "inventory", "capacity", "in_transit",
    "demand_next_8h", "hours_to_stockout", "p_stockout_8h", "tier",
    "confidence", "confidence_label", "unavoidable", "reasons",
)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def fuels_for(entity) -> list[str]:
    keys = set(entity.capacity) | set(entity.inventory)
    return sorted(keys) if keys else list(FUEL_TYPES)


def ticks_per_hour(tick_minutes: int) -> float:
    return 60.0 / tick_minutes if tick_minutes > 0 else 4.0


def routes_into(station_id: str, routes) -> list:
    return [r for r in routes if r.destination_station_id == station_id]


def min_open_transit(station_id: str, routes) -> float:
    open_routes = [r for r in routes_into(station_id, routes) if r.status == "AVAILABLE"]
    if not open_routes:
        return math.inf
    return min(r.transit_ticks for r in open_routes)


def to_risk_entry(d: dict) -> dict:
    return {k: d[k] for k in _PUBLIC_FIELDS}


def assess(
    station,
    fuel: str,
    routes,
    supply,
    in_transit,
    fit: "fc.SeriesFit",
    tick: int,
    tick_minutes: int,
    horizon: int,
    safety_z: float = 1.28,
) -> dict:
    capacity = float(station.capacity.get(fuel, 0.0))
    inventory = float(station.inventory.get(fuel, 0.0))
    in_transit_qty = sum(
        it.quantity for it in in_transit if it.station_id == station.id and it.fuel_type == fuel
    )

    if station.status == "OUTAGE":
        # No active demand (served drops to 0): tier reflects the raw stock
        # ratio only, not a stockout projection. Documented simplification --
        # see the intel report for rationale.
        ratio = inventory / capacity if capacity > 0 else 0.0
        if ratio <= 0.05:
            tier = "CRITICAL"
        elif ratio <= 0.2:
            tier = "HIGH"
        elif ratio <= 0.5:
            tier = "WATCH"
        else:
            tier = "OK"
        return {
            "station_id": station.id, "fuel_type": fuel, "inventory": inventory,
            "capacity": capacity, "in_transit": in_transit_qty, "demand_next_8h": 0.0,
            "hours_to_stockout": None, "p_stockout_8h": 0.0, "tier": tier,
            "confidence": 1.0, "confidence_label": "HIGH", "unavoidable": False,
            "reasons": ["station outage"], "unmet_total": 0.0, "stockout_idx": None,
        }

    tph = ticks_per_hour(tick_minutes)
    n8 = max(1, math.ceil(8 * tph))

    arrivals = [0.0] * horizon
    for it in in_transit:
        if it.station_id != station.id or it.fuel_type != fuel:
            continue
        t = max(0, it.eta_tick - tick)
        if t < horizon:
            arrivals[t] += it.quantity

    demand = [fit.mean(tick + t, tick_minutes, station.demand_profile) for t in range(horizon)]

    means: list[float] = []
    mean = inventory
    cum_demand_8h = 0.0
    for t in range(horizon):
        mean = mean + arrivals[t] - demand[t]
        means.append(mean)
        if t < n8:
            cum_demand_8h += demand[t]
    sds = [fit.sigma * math.sqrt(t + 1) for t in range(horizon)]

    stockout_idx = None
    hours_to_stockout = None
    for t, m in enumerate(means):
        if m < 0:
            stockout_idx = t
            hours_to_stockout = t * tick_minutes / 60.0
            break

    # Approximate stockout risk (est.), not a calibrated probability: the max
    # Gaussian-tail estimate over the next 8h of the additive projection above.
    p8 = 0.0
    for t in range(min(n8, horizon)):
        sd = sds[t] if sds[t] > 1e-9 else 1e-9
        p8 = max(p8, norm_cdf(-means[t] / sd))

    if p8 >= 0.8 or (hours_to_stockout is not None and hours_to_stockout < 4):
        tier = "CRITICAL"
    elif p8 >= 0.5 or (hours_to_stockout is not None and hours_to_stockout < 8):
        tier = "HIGH"
    elif p8 >= 0.2 or (hours_to_stockout is not None and hours_to_stockout < 16):
        tier = "WATCH"
    else:
        tier = "OK"

    confidence = 1.0 - min(1.0, max(0.0,
        0.5 * fit.wape / 0.2 + (0.3 if abs(station.demand_multiplier - 1.0) > 1e-9 else 0.0)
    ))
    if confidence >= 0.75:
        confidence_label = "HIGH"
    elif confidence >= 0.5:
        confidence_label = "MEDIUM"
    else:
        confidence_label = "LOW"

    min_transit = min_open_transit(station.id, routes)
    arrival_before = stockout_idx is not None and any(
        (it.eta_tick - tick) < stockout_idx
        for it in in_transit
        if it.station_id == station.id and it.fuel_type == fuel
    )
    unavoidable = bool(
        stockout_idx is not None
        and not arrival_before
        and (min_transit == math.inf or stockout_idx < min_transit)
    )

    blocked_route_ids = [r.id for r in routes_into(station.id, routes) if r.status == "DISRUPTED"]
    source_depots = {r.source_depot_id for r in routes_into(station.id, routes)}
    delayed_supply = any(
        s.fuel_type == fuel and s.status == "DELAYED" and s.depot_id in source_depots
        for s in supply
    )

    reasons: list[str] = []
    if abs(station.demand_multiplier - 1.0) > 1e-9:
        reasons.append(f"demand x{station.demand_multiplier:.2g}")
    for rid in blocked_route_ids:
        reasons.append(f"route {rid} disrupted")
    if delayed_supply:
        reasons.append("supply delayed")
    if hours_to_stockout is not None:
        reasons.append(f"stockout in {hours_to_stockout:.1f} h")

    # Simple clipped simulation (inventory never negative) for the "unmet
    # litres over the horizon" figure used by the planner's impact section.
    unmet_total = 0.0
    inv = inventory
    for t in range(horizon):
        avail = inv + arrivals[t]
        served = min(avail, demand[t])
        unmet_total += demand[t] - served
        inv = max(0.0, avail - served)

    return {
        "station_id": station.id, "fuel_type": fuel, "inventory": inventory,
        "capacity": capacity, "in_transit": in_transit_qty, "demand_next_8h": cum_demand_8h,
        "hours_to_stockout": hours_to_stockout, "p_stockout_8h": p8, "tier": tier,
        "confidence": confidence, "confidence_label": confidence_label,
        "unavoidable": unavoidable, "reasons": reasons,
        "unmet_total": unmet_total, "stockout_idx": stockout_idx,
    }
