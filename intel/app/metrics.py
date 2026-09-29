"""Prometheus metrics for the intel service (implementation_plan.md §8)."""
from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

http_requests_total = Counter(
    "http_requests_total", "Total HTTP requests", ["handler", "method", "status"],
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "HTTP request duration in seconds", ["handler"],
)
jalani_intel_solve_seconds = Histogram(
    "jalani_intel_solve_seconds", "LP solve duration in seconds",
)
jalani_intel_plans_total = Counter(
    "jalani_intel_plans_total", "Plans computed by outcome", ["status"],
)
jalani_intel_forecast_wape = Gauge(
    "jalani_intel_forecast_wape", "Most recent forecast WAPE",
)


async def metrics_middleware(request, call_next):
    start = time.perf_counter()
    handler = request.url.path
    method = request.method
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        duration = time.perf_counter() - start
        http_request_duration_seconds.labels(handler=handler).observe(duration)
        http_requests_total.labels(handler=handler, method=method, status=str(status)).inc()


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
