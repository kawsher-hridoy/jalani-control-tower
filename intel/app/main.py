"""FastAPI app: POST /v1/plan, POST /v1/forecast, GET /health, GET /metrics."""
from __future__ import annotations

import json
import logging
import sys

from fastapi import FastAPI
from fastapi.responses import Response

from . import forecast as fc
from . import metrics
from . import planner
from .schemas import ForecastRequest, ForecastResponse, PlanRequest, PlanResponse

logger = logging.getLogger("jalani.intel")
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")

app = FastAPI(title="Jalani Intel", version="1.0.0")
app.middleware("http")(metrics.metrics_middleware)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics_endpoint() -> Response:
    body, content_type = metrics.render_metrics()
    return Response(content=body, media_type=content_type)


@app.post("/v1/plan", response_model=PlanResponse)
def plan(req: PlanRequest) -> dict:
    result = planner.solve_plan(req)
    metrics.jalani_intel_solve_seconds.observe(result["solve_ms"] / 1000.0)
    metrics.jalani_intel_plans_total.labels(status=result["status"]).inc()
    metrics.jalani_intel_forecast_wape.set(result["forecast"]["wape"])
    logger.info(json.dumps({
        "tick": req.tick,
        "status": result["status"],
        "solve_ms": round(result["solve_ms"], 2),
        "n_actions": len(result["actions"]),
    }))
    return result


@app.post("/v1/forecast", response_model=ForecastResponse)
def forecast(req: ForecastRequest) -> dict:
    # req.fuel_type (guide §8.5 daily litres per profile AND fuel) lets the
    # cold-start prior differ per fuel; None falls back to a fuel-agnostic
    # averaged prior. Real history quickly dominates either way.
    fit = fc.fit_series(
        req.history.ticks, req.history.values, req.tick_minutes,
        req.demand_profile, req.fuel_type, req.demand_multiplier,
        multipliers=req.multipliers, future_multipliers=req.future_multipliers,
        tick=req.tick,
    )
    points = fc.forecast_points(fit, req.tick, req.tick_minutes, req.demand_profile, req.horizon)
    metrics.jalani_intel_forecast_wape.set(fit.wape)
    return {"wape": fit.wape, "points": points, "history": fit.history}
