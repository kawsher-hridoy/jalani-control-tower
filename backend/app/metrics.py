"""Prometheus metrics (names are fixed by the implementation plan §8)."""
import time
from collections import deque

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["handler", "method", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["handler"],
                         buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5))
SIM_REQUESTS = Counter("jalani_sim_requests_total", "Simulator requests", ["endpoint", "outcome"])
SIM_LATENCY = Histogram("jalani_sim_request_seconds", "Simulator request latency", ["endpoint"],
                        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4))
CIRCUIT_STATE = Gauge("jalani_circuit_state", "Simulator circuit (0 closed, 1 half-open, 2 open)")
OPERATING_MODE = Gauge("jalani_operating_mode", "0 NORMAL, 1 DEGRADED, 2 SAFE_HOLD")
FALLBACK_ACTIVE = Gauge("jalani_fallback_active", "1 when the heuristic fallback policy is in use")
FALLBACK_ACTIVATIONS = Counter("jalani_fallback_activations_total", "Switches to the fallback policy")
DATA_AGE = Gauge("jalani_data_age_seconds", "Age of the last valid snapshot")
SIM_TICK = Gauge("jalani_sim_tick", "Latest processed simulator tick")
TICK_LAG = Gauge("jalani_tick_lag", "Ticks skipped in the last cycle")
LOOP_SECONDS = Histogram("jalani_loop_seconds", "Control loop cycle time",
                         buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5))
PLAN_SECONDS = Histogram("jalani_plan_seconds", "Planning time (intel or heuristic)",
                         buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2))
DECISIONS = Counter("jalani_decisions_total", "Decisions recorded", ["kind", "actor"])
RECS_PENDING = Gauge("jalani_recommendations_pending", "Recommendations waiting for an operator")
ALLOC_REJECTED = Counter("jalani_allocations_rejected_total", "Allocations rejected by the simulator", ["code"])
GUARD_CANCELS = Counter("jalani_guard_cancellations_total", "Pending shipments cancelled by the guard")
SERVICE_LEVEL = Gauge("jalani_service_level", "Simulator service level")
UNMET = Gauge("jalani_unmet_liters", "Unmet demand since start")
FUEL_LOST = Gauge("jalani_fuel_lost_liters", "Fuel lost since start", ["cause"])
IN_TRANSIT = Gauge("jalani_in_transit_liters", "Fuel in transit")
STOCKOUT_P = Gauge("jalani_stockout_probability", "P(stockout within 8 h)", ["station", "fuel"])
HOURS_TO_STOCKOUT = Gauge("jalani_hours_to_stockout", "Hours to stockout (48 = none in horizon)", ["station", "fuel"])
STATIONS_AT_RISK = Gauge("jalani_stations_at_risk", "Stations with a HIGH or CRITICAL fuel")
INCIDENTS_OPEN = Gauge("jalani_incidents_open", "Open incidents")
ALERTS = Counter("jalani_alerts_total", "Incidents opened", ["type"])
FORECAST_WAPE = Gauge("jalani_forecast_wape", "Forecast WAPE reported by the planner")


class Rolling:
    """Rolling window of (timestamp, latency_ms, ok) for p95 and error rate in /api/status."""

    def __init__(self, window_s: float = 60.0, maxlen: int = 5000):
        self.window_s = window_s
        self.items: deque = deque(maxlen=maxlen)

    def add(self, ms: float, ok: bool) -> None:
        self.items.append((time.time(), ms, ok))

    def _recent(self):
        cutoff = time.time() - self.window_s
        return [i for i in self.items if i[0] >= cutoff]

    def p95(self) -> float:
        vals = sorted(i[1] for i in self._recent())
        return round(vals[int(0.95 * (len(vals) - 1))], 1) if vals else 0.0

    def error_rate(self) -> float:
        rec = self._recent()
        return round(sum(1 for i in rec if not i[2]) / len(rec), 4) if rec else 0.0

    def count(self) -> int:
        return len(self._recent())
LP_GATED = Counter("jalani_lp_gated_cells_total", "Urgent station/fuel cells the LP left uncovered, filled by the backup rule")
