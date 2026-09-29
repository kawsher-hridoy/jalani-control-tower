"""Detects changes between snapshots and turns them into incidents."""
from .models import Snapshot

EVENT_INCIDENT = {"demand_spike": "DEMAND_SPIKE", "route_disruption": "ROUTE_DISRUPTION",
                  "station_outage": "STATION_OUTAGE", "depot_constraint": "DEPOT_CONSTRAINT",
                  "shipment_delay": "SHIPMENT_DELAY", "supply_shortfall": "SUPPLY_SHORTFALL"}
TARGET_KEYS = ("route_ids", "station_ids", "region_ids", "depot_ids", "fuel_types")


def _targets(params: dict) -> list[str]:
    out = []
    for k in TARGET_KEYS:
        out += list(params.get(k) or [])
    return out


def event_title(ev, snap: Snapshot) -> tuple[str, str, str]:
    targets = _targets(ev.parameters) or ["all"]
    label = ev.type.replace("_", " ")
    title = f"{label.capitalize()}: {', '.join(targets)} (ticks {ev.start_tick}–{ev.end_tick})"
    extra = []
    if "multiplier" in ev.parameters:
        extra.append(f"demand x{ev.parameters['multiplier']}")
    if "delay_ticks" in ev.parameters:
        extra.append(f"supply delayed {ev.parameters['delay_ticks']} ticks")
    if "factor" in ev.parameters:
        extra.append(f"supply cut to {round(float(ev.parameters['factor']) * 100)}%")
    severity = "WARNING"
    if ev.type == "station_outage":
        severity = "CRITICAL"
    if ev.type == "route_disruption":
        single = {s.id for s in snap.stations if sum(1 for r in snap.routes if r.destination_station_id == s.id) == 1}
        hit = [r for r in snap.routes if not ev.parameters.get("route_ids") or r.id in ev.parameters["route_ids"]]
        if any(r.destination_station_id in single for r in hit):
            severity = "CRITICAL"
            extra.append("hits a single-route station: stock it up before the cut")
    summary = f"{label.capitalize()} injected from tick {ev.start_tick} to {ev.end_tick}. " + "; ".join(extra)
    return title, summary.strip(), severity


def run(engine, prev: Snapshot | None, snap: Snapshot) -> None:
    prev_events = {e.id: e for e in prev.events} if prev else {}
    for ev in snap.events:
        key = f"event-{ev.id}"
        before = prev_events.get(ev.id)
        if before is None and ev.status != "RESOLVED":
            title, summary, severity = event_title(ev, snap)
            engine.open_incident(key, EVENT_INCIDENT.get(ev.type, "SIMULATOR_EVENT"), severity, title, summary,
                                 _targets(ev.parameters))
        elif before is not None and before.status != ev.status:
            if ev.status == "ACTIVE":
                engine.note_incident(key, f"event active until tick {ev.end_tick}")
            elif ev.status == "RESOLVED":
                engine.resolve_incident(key, "event resolved")
    if prev:
        prev_supply = {s.id: s for s in prev.supply}
        for s in snap.supply:
            p = prev_supply.get(s.id)
            if p is None:
                continue
            if s.planned_tick > p.planned_tick:
                engine.open_incident(f"delay-{s.id}", "SHIPMENT_DELAY", "WARNING",
                                     f"Supply {s.id} delayed to tick {s.planned_tick}",
                                     f"{int(s.quantity):,} L of {s.fuel_type.lower()} for {s.depot_id} moved from tick "
                                     f"{p.planned_tick} to {s.planned_tick}.", [s.depot_id])
            if s.quantity < p.quantity - 1:
                engine.open_incident(f"shortfall-{s.id}", "SUPPLY_SHORTFALL", "WARNING",
                                     f"Supply {s.id} cut to {int(s.quantity):,} L",
                                     f"{s.fuel_type.lower()} delivery for {s.depot_id} reduced from "
                                     f"{int(p.quantity):,} L to {int(s.quantity):,} L.", [s.depot_id])
            if s.status == "ARRIVED" and p.status != "ARRIVED":
                engine.resolve_incident(f"delay-{s.id}", "supply arrived")
                engine.resolve_incident(f"shortfall-{s.id}", "supply arrived")
