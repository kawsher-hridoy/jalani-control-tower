"""Request/response contract with the backend (implementation_plan.md §7).

All request models are deliberately tolerant: extra fields from the backend are
allowed and ignored, and optional lists/dicts default to empty so a partial
payload never fails validation.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class In(BaseModel):
    """Base for inbound (backend -> intel) models: tolerant of extra fields."""

    model_config = ConfigDict(extra="allow")


class Out(BaseModel):
    """Base for outbound (intel -> backend) models: exact contract shape."""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class Depot(In):
    id: str
    region_id: str = ""
    status: str = "OPEN"
    dispatch_capacity_per_tick: float = 0.0
    capacity: dict[str, float] = Field(default_factory=dict)
    inventory: dict[str, float] = Field(default_factory=dict)


class Station(In):
    id: str
    region_id: str = ""
    status: str = "OPEN"
    demand_profile: str = "unknown"
    demand_multiplier: float = 1.0
    capacity: dict[str, float] = Field(default_factory=dict)
    inventory: dict[str, float] = Field(default_factory=dict)


class Route(In):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int = 1
    max_shipment: float = 0.0
    status: str = "AVAILABLE"


class Supply(In):
    id: str = ""
    depot_id: str
    fuel_type: str
    quantity: float = 0.0
    planned_tick: int = 0
    status: str = "SCHEDULED"


class EventModel(In):
    id: int | str = 0
    type: str
    start_tick: int = 0
    end_tick: int = 0
    status: str = "ACTIVE"
    parameters: dict[str, Any] = Field(default_factory=dict)


class InTransit(In):
    station_id: str
    fuel_type: str
    quantity: float = 0.0
    eta_tick: int = 0


class DemandSeries(In):
    ticks: list[int] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    # Station demand_multiplier in effect at each observed tick, aligned
    # index-for-index with `ticks`/`values`.
    multipliers: list[float] | None = None
    # index k-1 = the multiplier expected at tick+k, for k=1..len (the backend
    # already divides a spike out after its end_tick).
    future_multipliers: list[float] | None = None


class PlanSettings(In):
    horizon_ticks: int = 48
    risk_horizon_ticks: int = 96
    safety_z: float = 1.28
    constrained_factor: float = 0.5
    priorities: dict[str, float] = Field(default_factory=dict)


class PlanRequest(In):
    epoch: int = 0
    tick: int = 0
    tick_minutes: int = 15
    depots: list[Depot] = Field(default_factory=list)
    stations: list[Station] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    supply: list[Supply] = Field(default_factory=list)
    events: list[EventModel] = Field(default_factory=list)
    in_transit: list[InTransit] = Field(default_factory=list)
    pending_dispatch: dict[str, float] = Field(default_factory=dict)
    demand_history: dict[str, DemandSeries] = Field(default_factory=dict)
    settings: PlanSettings = Field(default_factory=PlanSettings)


class ForecastRequest(In):
    tick: int = 0
    tick_minutes: int = 15
    demand_profile: str = "unknown"
    demand_multiplier: float = 1.0
    fuel_type: str | None = None
    multipliers: list[float] | None = None
    future_multipliers: list[float] | None = None
    history: DemandSeries = Field(default_factory=DemandSeries)
    horizon: int = 32


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class Action(Out):
    route_id: str
    fuel_type: str
    quantity: float


class PipelineAction(Out):
    route_id: str
    fuel_type: str
    quantity: float
    tick: int


class RiskEntry(Out):
    station_id: str
    fuel_type: str
    inventory: float
    capacity: float
    in_transit: float
    demand_next_8h: float
    hours_to_stockout: float | None
    # Approximate stockout risk (est.) -- a rough Gaussian-tail estimate from
    # a simple additive projection, NOT a calibrated probability.
    p_stockout_8h: float
    tier: str
    confidence: float
    confidence_label: str
    unavoidable: bool
    reasons: list[str] = Field(default_factory=list)


class ImpactEntry(Out):
    station_id: str
    fuel_type: str
    p_stockout_before: float
    p_stockout_after: float
    unmet_before_liters: float
    unmet_after_liters: float
    hours_to_stockout_before: float | None
    hours_to_stockout_after: float | None


class BindingEntry(Out):
    station_id: str
    fuel_type: str
    constraints: list[str] = Field(default_factory=list)


class ForecastSeriesSummary(Out):
    next_8h: float
    sigma_8h: float


class ForecastSummary(Out):
    wape: float
    series: dict[str, ForecastSeriesSummary] = Field(default_factory=dict)


class PlanResponse(Out):
    policy: str = "lp-v1"
    status: str
    solve_ms: float
    actions: list[Action] = Field(default_factory=list)
    pipeline: list[PipelineAction] = Field(default_factory=list)
    risk: list[RiskEntry] = Field(default_factory=list)
    impact: list[ImpactEntry] = Field(default_factory=list)
    binding: list[BindingEntry] = Field(default_factory=list)
    forecast: ForecastSummary


class ForecastPoint(Out):
    tick: int
    forecast: float
    lo: float
    hi: float


class ForecastHistoryPoint(Out):
    """Genuine one-step-ahead (prequential) forecast for an observed tick,
    made BEFORE that observation was absorbed into the level."""

    tick: int
    actual: float
    forecast: float


class ForecastResponse(Out):
    wape: float
    points: list[ForecastPoint] = Field(default_factory=list)
    history: list[ForecastHistoryPoint] = Field(default_factory=list)
