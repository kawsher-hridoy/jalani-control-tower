"""Snapshot fetching and derived values shared by the control loop, the API and the benchmark."""
import asyncio
import math
import time
from collections import defaultdict

from .models import (Allocation, Depot, Event, Instance, Region, Route, SimMetrics, Snapshot, Station,
                     Supply)
from .simclient import SimClient

HOUR_FACTORS = {
    "industrial": lambda h: 1.55 if 6 <= h <= 17 else 0.45,
    "highway": lambda h: 1.35 if (6 <= h <= 9 or 16 <= h <= 20) else 0.75,
    "urban_high": lambda h: 1.45 if (7 <= h <= 9 or 16 <= h <= 20) else 0.70,
    "regional": lambda h: 1.25 if 7 <= h <= 20 else 0.65,
}
ACTIVE_EVENT = ("SCHEDULED", "ACTIVE")


def hour_of(tick: int, tick_minutes: int) -> int:
    return (tick * tick_minutes // 60) % 24


async def fetch_snapshot(sim: SimClient, instance: dict | None = None, regions: list | None = None,
                         verify: bool = True) -> Snapshot:
    """Read the world in parallel. Raises SimError or pydantic ValidationError.

    Parallel reads can straddle a tick boundary, so the instance is read again afterwards: if the tick moved,
    the snapshot is marked `consistent=False` and the caller must not write shipments based on it."""
    if instance is None and verify:
        instance = await sim.request("GET", "/v1/instance")
    names = ["/v1/stations", "/v1/depots", "/v1/routes", "/v1/supply-arrivals", "/v1/events",
             "/v1/allocations", "/v1/metrics"]
    if instance is None:
        names.append("/v1/instance")
    if regions is None:
        names.append("/v1/regions")
    stale_before = sim.stale_seen
    results = await asyncio.gather(*(sim.request("GET", n) for n in names))
    data = dict(zip(names, results))
    inst = instance or data["/v1/instance"]
    consistent = True
    if verify:
        after = await sim.request("GET", "/v1/instance")
        consistent = after.get("tick") == inst.get("tick")
    return Snapshot(
        instance=Instance(**inst),
        regions=[Region(**r) for r in (regions if regions is not None else data["/v1/regions"])],
        depots=[Depot(**d) for d in data["/v1/depots"]],
        stations=[Station(**s) for s in data["/v1/stations"]],
        routes=[Route(**r) for r in data["/v1/routes"]],
        supply=[Supply(**s) for s in data["/v1/supply-arrivals"]],
        events=[Event(**e) for e in data["/v1/events"]],
        allocations=[Allocation(**a) for a in data["/v1/allocations"]],
        metrics=SimMetrics(**data["/v1/metrics"]),
        stale=sim.stale_seen > stale_before or sim.stale,
        consistent=consistent,
        fetched_at=time.time(),
    )


def event_targets(ev: Event, key: str) -> list[str]:
    return list(ev.parameters.get(key) or [])


def route_blocked(snap: Snapshot, route: Route, at_tick: int) -> bool:
    """True if the route is disrupted now or a disruption covers `at_tick` (including one starting now)."""
    if route.status != "AVAILABLE":
        return True
    for ev in snap.events:
        if ev.type != "route_disruption" or ev.status not in ACTIVE_EVENT:
            continue
        targets = event_targets(ev, "route_ids")
        if (not targets or route.id in targets) and ev.start_tick <= at_tick < ev.end_tick:
            return True
    return False


def next_disruption(snap: Snapshot, route: Route) -> dict | None:
    best = None
    for ev in snap.events:
        if ev.type != "route_disruption" or ev.status not in ACTIVE_EVENT:
            continue
        targets = event_targets(ev, "route_ids")
        if (not targets or route.id in targets) and (best is None or ev.start_tick < best["start_tick"]):
            best = {"start_tick": ev.start_tick, "end_tick": ev.end_tick}
    return best


def in_transit(snap: Snapshot) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = defaultdict(float)
    for a in snap.allocations:
        if a.status in ("PENDING", "IN_TRANSIT"):
            out[(a.destination_station_id, a.fuel_type)] += a.quantity
    return out


def in_transit_list(snap: Snapshot) -> list[dict]:
    transit = {r.id: r.transit_ticks for r in snap.routes}
    out = []
    for a in snap.allocations:
        if a.status in ("PENDING", "IN_TRANSIT"):
            eta = a.expected_arrival_tick or (a.created_tick + transit.get(a.route_id, 2))
            out.append({"station_id": a.destination_station_id, "fuel_type": a.fuel_type,
                        "quantity": a.quantity, "eta_tick": eta})
    return out


def pending_dispatch(snap: Snapshot) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for a in snap.allocations:
        if a.status == "PENDING" and a.created_tick == snap.tick:
            out[a.source_depot_id] += a.quantity
    return dict(out)


def mean_demand_per_tick(history: dict[str, list[tuple[int, float]]], key: str, window: int = 96) -> float:
    rows = history.get(key, [])[-window:]
    return sum(v for _, v in rows) / len(rows) if rows else 0.0


def days_of_cover(snap: Snapshot, history: dict[str, list[tuple[int, float]]]) -> dict[str, dict[str, float | None]]:
    per_day = 1440 / snap.instance.tick_minutes
    out: dict[str, dict[str, float | None]] = {}
    for d in snap.depots:
        stations = [s.id for s in snap.stations if s.region_id == d.region_id]
        out[d.id] = {}
        for fuel, inv in d.inventory.items():
            daily = sum(mean_demand_per_tick(history, f"{s}|{fuel}") for s in stations) * per_day
            out[d.id][fuel] = round(inv / daily, 2) if daily > 0 else None
    return out


def spike_targets(ev: Event, station: Station) -> bool:
    sids, rids = event_targets(ev, "station_ids"), event_targets(ev, "region_ids")
    return (not sids and not rids) or station.id in sids or station.region_id in rids


def multiplier_at(log: list[tuple[int, float]], t: int, default: float) -> float:
    """The demand multiplier in effect at tick t, from the (tick, multiplier) changes seen in snapshots."""
    value = log[0][1] if log else default
    for tick, m in log:
        if tick > t:
            break
        value = m
    return value


def future_multipliers(snap: Snapshot, station: Station, horizon: int) -> list[float]:
    """Expected multiplier for tick+1 .. tick+horizon: the current one, with each active spike divided out after
    its end_tick and each scheduled spike applied inside its window (guide §7.8: spikes multiply, then divide)."""
    out = []
    for k in range(1, horizon + 1):
        t = snap.tick + k
        m = station.demand_multiplier
        for ev in snap.events:
            if ev.type != "demand_spike" or not spike_targets(ev, station):
                continue
            factor = max(float(ev.parameters.get("multiplier", 1.5)), 0.01)
            if ev.status == "ACTIVE" and ev.end_tick is not None and t >= ev.end_tick:
                m /= factor
            elif ev.status == "SCHEDULED" and ev.start_tick <= t < (ev.end_tick or 10 ** 9):
                m *= factor
        out.append(max(m, 0.01))
    return out


def simple_forecast(history: dict[str, list[tuple[int, float]]], station: Station, fuel: str, tick: int,
                    tick_minutes: int, horizon: int, mult_log: list[tuple[int, float]] | None = None,
                    future: list[float] | None = None) -> list[float]:
    """Heuristic forecast (used when intel is down): recent level x hour-of-day shape x multiplier.

    Each observation is divided by the multiplier in effect when it was observed, and the forecast is multiplied
    by the multiplier expected at each future tick, so a demand spike is counted once (never squared)."""
    shape = HOUR_FACTORS.get(station.demand_profile, lambda h: 1.0)
    rows = history.get(f"{station.id}|{fuel}", [])[-32:]
    now = station.demand_multiplier
    if rows:
        level = sum(v / (shape(hour_of(t, tick_minutes)) * max(multiplier_at(mult_log or [], t, now), 0.01))
                    for t, v in rows) / len(rows)
    else:
        level = 100.0 / max(now, 0.01)
    fut = future or []
    return [level * shape(hour_of(tick + k, tick_minutes)) * (fut[k - 1] if k - 1 < len(fut) else (fut[-1] if fut else now))
            for k in range(1, horizon + 1)]


def consumption_fn(snap: Snapshot, history: dict, mult_logs: dict | None = None):
    """Litres a station is expected to use before a shipment with `ticks` transit arrives (for the executor)."""
    def use(station: Station, fuel: str, ticks: int) -> float:
        fut = future_multipliers(snap, station, ticks)
        return sum(simple_forecast(history, station, fuel, snap.tick, snap.instance.tick_minutes, ticks,
                                   (mult_logs or {}).get(station.id), fut))
    return use


def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
