"""Shared fixtures: the real baseline world (integration guide §8) plus a
small deterministic synthetic demand history."""
from __future__ import annotations

import sys
import copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import forecast as fc

TICK_MINUTES = 15

DEPOTS = [
    {
        "id": "depot-gazipur", "region_id": "region-dhaka", "status": "OPEN",
        "dispatch_capacity_per_tick": 12000,
        "capacity": {"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
        "inventory": {"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
    },
    {
        "id": "depot-patiya", "region_id": "region-chattogram", "status": "OPEN",
        "dispatch_capacity_per_tick": 11000,
        "capacity": {"DIESEL": 85000, "PETROL": 65000, "OCTANE": 40000},
        "inventory": {"DIESEL": 55000, "PETROL": 42000, "OCTANE": 24000},
    },
]

STATIONS = [
    {
        "id": "station-mirpur", "region_id": "region-dhaka", "status": "OPEN",
        "demand_profile": "urban_high", "demand_multiplier": 1.0,
        "capacity": {"DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000},
        "inventory": {"DIESEL": 9000, "PETROL": 9000, "OCTANE": 5000},
    },
    {
        "id": "station-tongi", "region_id": "region-dhaka", "status": "OPEN",
        "demand_profile": "industrial", "demand_multiplier": 1.0,
        "capacity": {"DIESEL": 18000, "PETROL": 9000, "OCTANE": 6000},
        "inventory": {"DIESEL": 11000, "PETROL": 6000, "OCTANE": 3500},
    },
    {
        "id": "station-karnaphuli", "region_id": "region-chattogram", "status": "OPEN",
        "demand_profile": "highway", "demand_multiplier": 1.0,
        "capacity": {"DIESEL": 14000, "PETROL": 15000, "OCTANE": 9000},
        "inventory": {"DIESEL": 8500, "PETROL": 9500, "OCTANE": 5200},
    },
    {
        "id": "station-coxsbazar", "region_id": "region-chattogram", "status": "OPEN",
        "demand_profile": "regional", "demand_multiplier": 1.0,
        "capacity": {"DIESEL": 12000, "PETROL": 12000, "OCTANE": 7000},
        "inventory": {"DIESEL": 7500, "PETROL": 7500, "OCTANE": 4200},
    },
]

ROUTES = [
    {"id": "route-gazipur-mirpur", "source_depot_id": "depot-gazipur", "destination_station_id": "station-mirpur", "transit_ticks": 2, "max_shipment": 7000, "status": "AVAILABLE"},
    {"id": "route-gazipur-tongi", "source_depot_id": "depot-gazipur", "destination_station_id": "station-tongi", "transit_ticks": 2, "max_shipment": 6500, "status": "AVAILABLE"},
    {"id": "route-patiya-karnaphuli", "source_depot_id": "depot-patiya", "destination_station_id": "station-karnaphuli", "transit_ticks": 2, "max_shipment": 7000, "status": "AVAILABLE"},
    {"id": "route-patiya-coxsbazar", "source_depot_id": "depot-patiya", "destination_station_id": "station-coxsbazar", "transit_ticks": 3, "max_shipment": 6000, "status": "AVAILABLE"},
    {"id": "route-gazipur-karnaphuli", "source_depot_id": "depot-gazipur", "destination_station_id": "station-karnaphuli", "transit_ticks": 4, "max_shipment": 5000, "status": "AVAILABLE"},
    {"id": "route-patiya-mirpur", "source_depot_id": "depot-patiya", "destination_station_id": "station-mirpur", "transit_ticks": 4, "max_shipment": 5000, "status": "AVAILABLE"},
]

FUELS = ("DIESEL", "PETROL", "OCTANE")


def synthetic_history(tick: int, n: int = 96, tick_minutes: int = TICK_MINUTES) -> dict:
    """Noiseless shape*prior history for every station/fuel, ticks [tick-n, tick)."""
    history = {}
    for station in STATIONS:
        for fuel in FUELS:
            level = fc.prior_level_per_tick(station["demand_profile"], fuel, tick_minutes)
            ticks = list(range(tick - n, tick))
            values = [
                level * fc.shape_factor(station["demand_profile"], fc.hour_of_day(t, tick_minutes))
                for t in ticks
            ]
            history[f"{station['id']}|{fuel}"] = {"ticks": ticks, "values": values}
    return history


def base_payload(tick: int = 200, horizon_ticks: int = 48, **overrides) -> dict:
    payload = {
        "epoch": 1,
        "tick": tick,
        "tick_minutes": TICK_MINUTES,
        "depots": copy.deepcopy(DEPOTS),
        "stations": copy.deepcopy(STATIONS),
        "routes": copy.deepcopy(ROUTES),
        "supply": [],
        "events": [],
        "in_transit": [],
        "pending_dispatch": {},
        "demand_history": synthetic_history(tick),
        "settings": {"horizon_ticks": horizon_ticks, "safety_z": 1.28, "constrained_factor": 0.5, "priorities": {}},
    }
    payload.update(overrides)
    return payload
