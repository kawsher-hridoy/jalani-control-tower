import pytest

from app.models import Snapshot


def world(tick: int = 10, events: list | None = None, allocations: list | None = None, station_inv: float = 2000.0):
    return Snapshot(
        instance={"id": 1, "scenario_id": "baseline", "seed": 12345, "sim_time": "2026-01-01T02:30:00", "tick": tick,
                  "tick_minutes": 15, "status": "RUNNING"},
        regions=[{"id": "region-dhaka", "name": "Dhaka", "demand_factor": 1.0},
                 {"id": "region-chattogram", "name": "Chattogram", "demand_factor": 1.08}],
        depots=[{"id": "depot-gazipur", "name": "Gazipur Depot", "region_id": "region-dhaka", "status": "OPEN",
                 "dispatch_capacity_per_tick": 12000, "capacity": {"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
                 "inventory": {"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000}},
                {"id": "depot-patiya", "name": "Patiya Depot", "region_id": "region-chattogram", "status": "OPEN",
                 "dispatch_capacity_per_tick": 11000, "capacity": {"DIESEL": 85000, "PETROL": 65000, "OCTANE": 40000},
                 "inventory": {"DIESEL": 55000, "PETROL": 42000, "OCTANE": 24000}}],
        stations=[{"id": "station-tongi", "name": "Tongi", "region_id": "region-dhaka", "status": "OPEN",
                   "demand_profile": "industrial", "demand_multiplier": 1.0,
                   "capacity": {"DIESEL": 18000, "PETROL": 9000, "OCTANE": 6000},
                   "inventory": {"DIESEL": station_inv, "PETROL": 6000, "OCTANE": 3500}},
                  {"id": "station-karnaphuli", "name": "Karnaphuli", "region_id": "region-chattogram", "status": "OPEN",
                   "demand_profile": "highway", "demand_multiplier": 1.0,
                   "capacity": {"DIESEL": 14000, "PETROL": 15000, "OCTANE": 9000},
                   "inventory": {"DIESEL": 8500, "PETROL": 9500, "OCTANE": 5200}}],
        routes=[{"id": "route-gazipur-tongi", "source_depot_id": "depot-gazipur", "destination_station_id": "station-tongi",
                 "transit_ticks": 2, "max_shipment": 6500, "status": "AVAILABLE"},
                {"id": "route-patiya-karnaphuli", "source_depot_id": "depot-patiya",
                 "destination_station_id": "station-karnaphuli", "transit_ticks": 2, "max_shipment": 7000,
                 "status": "AVAILABLE"},
                {"id": "route-gazipur-karnaphuli", "source_depot_id": "depot-gazipur",
                 "destination_station_id": "station-karnaphuli", "transit_ticks": 4, "max_shipment": 5000,
                 "status": "AVAILABLE"}],
        supply=[], events=events or [], allocations=allocations or [],
        metrics={"served_demand_liters": 0, "unmet_demand_liters": 0, "service_level": 1.0, "allocation_liters": 0,
                 "allocation_failures": 0},
    )


@pytest.fixture
def snap():
    return world()
