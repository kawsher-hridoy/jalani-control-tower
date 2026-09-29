"""Validated simulator payloads. Invalid data is rejected before it reaches any decision."""
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

FUELS = ("DIESEL", "PETROL", "OCTANE")
EPS = 1.0


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


def _check_stock(capacity: dict[str, float], inventory: dict[str, float], who: str) -> None:
    for fuel, qty in inventory.items():
        cap = capacity.get(fuel)
        if qty < -EPS or (cap is not None and qty > cap + EPS):
            raise ValueError(f"{who}: {fuel} inventory {qty} outside [0, {cap}]")


class Instance(_Base):
    id: int
    scenario_id: str
    seed: int
    sim_time: str
    tick: int
    tick_minutes: int
    status: str


class Region(_Base):
    id: str
    name: str
    demand_factor: float


class Depot(_Base):
    id: str
    name: str
    region_id: str
    status: str
    dispatch_capacity_per_tick: float
    capacity: dict[str, float]
    inventory: dict[str, float]

    @model_validator(mode="after")
    def _stock(self):
        _check_stock(self.capacity, self.inventory, self.id)
        return self


class Station(_Base):
    id: str
    name: str
    region_id: str
    status: str
    demand_profile: str
    demand_multiplier: float
    capacity: dict[str, float]
    inventory: dict[str, float]

    @model_validator(mode="after")
    def _stock(self):
        _check_stock(self.capacity, self.inventory, self.id)
        return self


class Route(_Base):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int
    max_shipment: float
    status: str


class Supply(_Base):
    id: str
    depot_id: str
    fuel_type: str
    quantity: float
    planned_tick: int
    actual_tick: int | None = None
    status: str

    @model_validator(mode="after")
    def _qty(self):
        if self.quantity < 0:
            raise ValueError(f"{self.id}: negative quantity")
        return self


class Event(_Base):
    id: int
    type: str
    start_tick: int
    end_tick: int
    status: str
    parameters: dict[str, Any] = {}


class Allocation(_Base):
    id: int
    idempotency_key: str
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: str
    quantity: float
    created_tick: int
    departure_tick: int | None = None
    expected_arrival_tick: int | None = None
    actual_arrival_tick: int | None = None
    status: str
    failure_reason: str | None = None


class DemandRow(_Base):
    station_id: str
    fuel_type: str
    tick: int
    demand_liters: float
    served_liters: float
    unmet_liters: float


class SimMetrics(_Base):
    served_demand_liters: float
    unmet_demand_liters: float
    service_level: float
    allocation_liters: float
    allocation_failures: int


class Snapshot(BaseModel):
    """One consistent view of the world at a tick."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    instance: Instance
    regions: list[Region]
    depots: list[Depot]
    stations: list[Station]
    routes: list[Route]
    supply: list[Supply]
    events: list[Event]
    allocations: list[Allocation]
    metrics: SimMetrics
    stale: bool = False
    consistent: bool = True
    fetched_at: float = 0.0

    @model_validator(mode="after")
    def _refs(self):
        depots = {d.id for d in self.depots}
        stations = {s.id for s in self.stations}
        for r in self.routes:
            if r.source_depot_id not in depots or r.destination_station_id not in stations:
                raise ValueError(f"route {r.id} references unknown depot/station")
        return self

    @property
    def tick(self) -> int:
        return self.instance.tick
