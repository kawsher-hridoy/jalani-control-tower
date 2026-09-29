"""Turns planned actions into validated simulator allocations, and runs the pending-shipment guard."""
import math
from typing import Callable

from . import metrics
from .models import Snapshot, Station
from .simclient import SimClient, SimError
from .state import in_transit, pending_dispatch, route_blocked

MIN_CHUNK = 100.0
ARRIVAL_CONSUMPTION_SHARE = 0.8  # only count 80 % of the forecast use before arrival, so a forecast miss can't overflow

Consumption = Callable[[Station, str, int], float]


def prepare(snap: Snapshot, actions: list[dict], key_prefix: str, constrained_factor: float = 0.5,
            consumption: Consumption | None = None, race_margin: int = 1) -> list[dict]:
    """Check every action against the simulator's rules on the latest snapshot; clip, reserve budgets, split.

    Two station-capacity rules must both hold:
      POST rule    inventory + batch + q <= capacity   (guide §5.2: the simulator rejects the request otherwise)
      arrival rule inventory + in_transit + batch + q - 0.8 * use_until_arrival <= capacity
                   (F5: otherwise the station silently discards the overflow when the shipment lands)
    Depot stock, dispatch capacity and station space are reserved after each action, so several actions in
    one batch never oversubscribe them. `race_margin` ticks: also refuse a route whose disruption starts on
    the next tick, in case the simulator advances between this snapshot and the POST (F6: that shipment
    would FAIL and its fuel would be lost).
    """
    tick = snap.tick
    routes = {r.id: r for r in snap.routes}
    depots = {d.id: d for d in snap.depots}
    stations = {s.id: s for s in snap.stations}
    pend = pending_dispatch(snap)
    transit = in_transit(snap)
    depot_left = {d.id: dict(d.inventory) for d in snap.depots}
    dispatch_left = {d.id: d.dispatch_capacity_per_tick * (constrained_factor if d.status == "CONSTRAINED" else 1.0)
                     - pend.get(d.id, 0.0) for d in snap.depots}
    station_added: dict[tuple[str, str], float] = {}
    bodies = []
    for a in actions:
        r = routes.get(a.get("route_id", ""))
        fuel = a.get("fuel_type")
        if r is None or any(route_blocked(snap, r, tick + k) for k in range(race_margin + 1)):
            continue
        d, s = depots.get(r.source_depot_id), stations.get(r.destination_station_id)
        if d is None or s is None or s.status != "OPEN" or d.status not in ("OPEN", "CONSTRAINED"):
            continue
        if fuel not in s.capacity or fuel not in d.inventory:
            continue
        added = station_added.get((s.id, fuel), 0.0)
        use = ARRIVAL_CONSUMPTION_SHARE * consumption(s, fuel, r.transit_ticks) if consumption else 0.0
        post_room = s.capacity[fuel] - s.inventory[fuel] - added
        arrival_room = s.capacity[fuel] - (s.inventory[fuel] + transit.get((s.id, fuel), 0.0) + added - use)
        q = min(float(a.get("quantity", 0.0)), depot_left[d.id][fuel], dispatch_left[d.id], post_room, arrival_room)
        q = math.floor(q)
        if q < MIN_CHUNK:
            continue
        n = max(1, math.ceil(q / r.max_shipment))
        chunk = math.floor(q / n)
        for i in range(n):
            bodies.append({"idempotency_key": f"{key_prefix}-{r.id}-{fuel}-{i}"[:150], "source_depot_id": d.id,
                           "destination_station_id": s.id, "route_id": r.id, "fuel_type": fuel,
                           "quantity": float(chunk)})
        depot_left[d.id][fuel] -= chunk * n
        dispatch_left[d.id] -= chunk * n
        station_added[(s.id, fuel)] = added + chunk * n
    return bodies


def totals(bodies: list[dict]) -> dict[tuple[str, str], float]:
    """Litres per (route, fuel) in a prepared batch (chunks merged back together)."""
    out: dict[tuple[str, str], float] = {}
    for b in bodies:
        out[(b["route_id"], b["fuel_type"])] = out.get((b["route_id"], b["fuel_type"]), 0.0) + b["quantity"]
    return out


async def post_all(sim: SimClient, bodies: list[dict]) -> list[tuple[dict, dict | None, str | None]]:
    """POST each allocation. Retries reuse the same body and idempotency key, so a retry after a lost
    response returns the allocation the simulator already created instead of creating a second one."""
    results = []
    for body in bodies:
        try:
            created = await sim.request("POST", "/v1/allocations", json=body)
            results.append((body, created, None))
        except SimError as e:
            code = e.code or e.kind.upper()
            metrics.ALLOC_REJECTED.labels(code).inc()
            results.append((body, None, code))
    return results


def guard_targets(snap: Snapshot, race_margin: int = 0) -> list[int]:
    """PENDING allocations whose route is closed now (or within `race_margin` ticks): they would FAIL and lose fuel,
    while a cancel refunds it (F6). Best effort: a disruption injected after this check can still win the race."""
    routes = {r.id: r for r in snap.routes}
    out = []
    for a in snap.allocations:
        r = routes.get(a.route_id)
        if a.status == "PENDING" and r is not None and any(
                route_blocked(snap, r, snap.tick + k) for k in range(race_margin + 1)):
            out.append(a.id)
    return out
