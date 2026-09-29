"""REST API for the operator console (contract: implementation plan §6)."""
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import settings
from .loop import Engine, EngineError
from .simclient import SimError
from .state import future_multipliers, in_transit, multiplier_at, next_disruption, simple_forecast

router = APIRouter(prefix="/api")
EVENT_TYPES = ("demand_spike", "route_disruption", "station_outage", "depot_constraint", "shipment_delay",
               "supply_shortfall")
FAULT_TYPES = ("latency", "unavailable", "error_rate", "stale_data", "stream_disconnect")


def engine(request: Request) -> Engine:
    return request.app.state.engine


def _unauthorized(what: str) -> HTTPException:
    return HTTPException(401, {"code": "UNAUTHORIZED", "message": f"{what} token required"})


def require_operator(x_operator_token: str | None = Header(None), x_admin_token: str | None = Header(None)) -> None:
    if not (x_operator_token == settings.operator_token or x_admin_token == settings.admin_token):
        raise _unauthorized("Operator")


def require_admin(x_admin_token: str | None = Header(None)) -> None:
    if x_admin_token != settings.admin_token:
        raise _unauthorized("Admin")


def _raise(e: EngineError):
    raise HTTPException(e.status, {"code": e.code, "message": e.message})


class ApproveBody(BaseModel):
    operator: str = Field("operator", max_length=60)
    note: str = Field("", max_length=300)
    revision: int | None = None  # the revision the operator saw; a newer one returns 409 REVISION_CHANGED


class RejectBody(BaseModel):
    operator: str = Field("operator", max_length=60)
    reason: str = Field("", max_length=300)


class ModeBody(BaseModel):
    autonomy_mode: Literal["ADVISORY", "SUPERVISED", "AUTOPILOT"]


class EventBody(BaseModel):
    type: Literal[EVENT_TYPES]  # type: ignore[valid-type]
    start_in_ticks: int = Field(0, ge=0, le=500)
    duration_ticks: int = Field(12, gt=0, le=1000)
    parameters: dict[str, Any] = {}


class FaultBody(BaseModel):
    type: Literal[FAULT_TYPES]  # type: ignore[valid-type]
    duration_seconds: int = Field(60, gt=0, le=3600)
    parameters: dict[str, Any] = {}


class IntelBody(BaseModel):
    enabled: bool


class SimBody(BaseModel):
    action: Literal["run", "pause", "step", "reset"]
    ticks: int = Field(1, ge=1, le=96)


@router.get("/health")
def health() -> dict:
    """Liveness only: the process answers HTTP."""
    return {"status": "ok"}


def insecure_defaults() -> bool:
    return any(t.startswith("change-me") for t in (settings.operator_token, settings.admin_token))


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Readiness: a world snapshot is loaded, the control loop ran recently, and the database accepts writes."""
    checks = engine(request).readiness()
    ok = all(checks.values())
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)


@router.get("/status")
def status(request: Request) -> dict:
    e = engine(request)
    br = e.sim.breaker.state if e.sim.breaker else "CLOSED"
    sim_state = {"SAFE_HOLD": "DOWN", "DEGRADED": "DEGRADED"}.get(e.mode, "UP")
    intel_state = "DOWN" if not e.intel_enabled else {"UP": "UP", "DOWN": "DOWN"}.get(e.intel_status, "DEGRADED")
    gpt_ok = bool(settings.azure_endpoint and settings.azure_key)
    rolling = request.app.state.api_rolling
    data_age = round(max(0.0, __import__("time").time() - e.sim.last_success), 1) if e.sim.last_success else None
    return {
        "version": settings.version, "git_sha": settings.git_sha, "epoch": e.epoch,
        "operating_mode": e.mode, "flags": e.flags(), "autonomy_mode": e.autonomy_mode,
        "policy": "heuristic-v1" if e.fallback else "lp-v1",
        "components": [
            {"name": "backend", "status": "UP", "detail": f"epoch {e.epoch}, tick {e.last_tick}"},
            {"name": "simulator", "status": sim_state, "detail": f"circuit {br}, p95 {e.sim.rolling.p95()} ms"},
            {"name": "intel", "status": intel_state,
             "detail": "disabled by Game Day" if not e.intel_enabled else
             ("heuristic fallback active" if e.fallback else "LP optimizer (lp-v1)")},
            {"name": "database", "status": "DOWN" if e.store.error else "UP",
             "detail": e.store.error or f"SQLite ({len(e.store.buffer)} buffered writes)"},
            {"name": "stream", "status": "DOWN" if e.stream_down else "UP",
             "detail": "SSE down: polling every 0.4 s" if e.stream_down else "SSE connected"},
            {"name": "sim_lab", "status": "UP" if e.sim_lab_up else "DOWN", "detail": "benchmark simulator"},
            {"name": "gpt", "status": "UP" if gpt_ok else "DEGRADED",
             "detail": "Azure OpenAI configured" if gpt_ok else "not configured: template explanations"},
        ],
        "sim_client": {"circuit": br, "p95_ms": e.sim.rolling.p95(), "error_rate_1m": e.sim.rolling.error_rate(),
                       "last_success_age_s": data_age},
        "api": {"p95_ms": rolling.p95(), "error_rate_1m": rolling.error_rate(), "requests_1m": rolling.count()},
        "loop": e.loop_stats, "data_age_s": data_age, "stale": e.sim.stale,
        "world": e.world, "ready": all(e.readiness().values()), "insecure_defaults": insecure_defaults(),
    }


@router.get("/state")
def state(request: Request) -> dict:
    e = engine(request)
    snap = e.snap
    if snap is None:
        raise HTTPException(503, {"code": "NO_DATA", "message": "No simulator snapshot yet"})
    transit = in_transit(snap)
    cover = e.depot_cover()
    risk = e.risk_rows()
    recs = e.recs.values()
    import time as _t
    return {
        "epoch": e.epoch, "instance": snap.instance.model_dump(),
        "operating_mode": e.mode, "flags": e.flags(), "autonomy_mode": e.autonomy_mode, "stale": e.sim.stale,
        "data_age_s": round(_t.time() - e.sim.last_success, 1) if e.sim.last_success else None,
        "kpis": {
            "service_level": snap.metrics.service_level, "served_liters": snap.metrics.served_demand_liters,
            "unmet_liters": snap.metrics.unmet_demand_liters,
            "fuel_lost_liters": round(sum(e.fuel_lost.values()), 1),
            "fuel_lost": {k: round(v, 1) for k, v in e.fuel_lost.items()},
            "in_transit_liters": round(sum(transit.values()), 1),
            "stations_at_risk": len({r["station_id"] for r in risk if r["tier"] in ("HIGH", "CRITICAL")}),
            "allocations_total": len(snap.allocations), "allocation_failures": snap.metrics.allocation_failures,
            "pending_approvals": sum(1 for r in recs if r["status"] == "PENDING"),
            "open_incidents": len(e.open_keys),
        },
        "regions": [r.model_dump() for r in snap.regions],
        "depots": [{**d.model_dump(), "days_of_cover": cover.get(d.id, {})} for d in snap.depots],
        "stations": [{**s.model_dump(), "in_transit": {f: round(transit.get((s.id, f), 0.0), 1) for f in s.capacity}}
                     for s in snap.stations],
        "routes": [{**r.model_dump(), "next_disruption": next_disruption(snap, r)} for r in snap.routes],
        "supply": [s.model_dump() for s in sorted(snap.supply, key=lambda s: s.planned_tick)],
        "shipments": [a.model_dump() for a in sorted(snap.allocations, key=lambda a: a.id, reverse=True)[:40]],
        "risk": risk,
        "events": [ev.model_dump() for ev in snap.events],
    }


@router.get("/forecast")
async def forecast(request: Request, station_id: str, fuel_type: str) -> dict:
    e = engine(request)
    if e.snap is None:
        raise HTTPException(503, {"code": "NO_DATA", "message": "No simulator snapshot yet"})
    station = next((s for s in e.snap.stations if s.id == station_id), None)
    if station is None:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "Unknown station"})
    series = e.history.get(f"{station_id}|{fuel_type}", [])
    tick, tm = e.snap.tick, e.snap.instance.tick_minutes
    log = e.mult_logs.get(station_id, [])
    fut = future_multipliers(e.snap, station, 32)
    points = [{"tick": t, "actual": round(v, 2), "forecast": None, "lo": None, "hi": None} for t, v in series[-48:]]
    wape = None
    try:
        resp = await e.intel.post("/v1/forecast", json={
            "tick": tick, "tick_minutes": tm, "demand_profile": station.demand_profile, "fuel_type": fuel_type,
            "demand_multiplier": station.demand_multiplier,
            "history": {"ticks": [t for t, _ in series], "values": [v for _, v in series]},
            "multipliers": [multiplier_at(log, t, station.demand_multiplier) for t, _ in series],
            "future_multipliers": fut, "horizon": 32})
        resp.raise_for_status()
        body = resp.json()
        wape = body.get("wape")
        one_step = {h["tick"]: h.get("forecast") for h in body.get("history") or []}
        for pt in points:
            f = one_step.get(pt["tick"])
            pt["forecast"] = round(f, 2) if isinstance(f, (int, float)) else None
        future = [{"tick": p["tick"], "actual": None, "forecast": p["forecast"], "lo": p.get("lo"), "hi": p.get("hi")}
                  for p in body.get("points", [])]
    except Exception:
        vals = simple_forecast(e.history, station, fuel_type, tick, tm, 32, log, fut)
        future = [{"tick": tick + i + 1, "actual": None, "forecast": round(v, 2), "lo": round(v * 0.85, 2),
                   "hi": round(v * 1.15, 2)} for i, v in enumerate(vals)]
    return {"station_id": station_id, "fuel_type": fuel_type, "wape": wape, "points": points + future}


@router.post("/plan/preview")
async def plan_preview(request: Request) -> dict:
    e = engine(request)
    if e.snap is None:
        raise HTTPException(503, {"code": "NO_DATA", "message": "No simulator snapshot yet"})
    key = (e.epoch, e.snap.tick)
    if e.preview_cache is None or e.preview_cache[0] != key:
        async with e.preview_lock:  # single flight: at most one extra solve per tick, whatever the request rate
            if e.preview_cache is None or e.preview_cache[0] != key:
                plan = await e.make_plan(e.snap, record=False)
                e.preview_cache = (key, {k: plan.get(k) for k in ("policy", "status", "solve_ms", "actions", "impact")})
                return {**e.preview_cache[1], "cached": False}
    return {**e.preview_cache[1], "cached": True}


@router.get("/recommendations")
def recommendations(request: Request, status: str | None = None, limit: int = Query(50, ge=1, le=300)) -> list:
    recs = sorted(engine(request).recs.values(), key=lambda r: (r["created_tick"], r["id"]), reverse=True)
    if status:
        recs = [r for r in recs if r["status"] == status.upper()]
    return [{k: v for k, v in r.items() if k != "cross_region"} for r in recs[:limit]]


@router.post("/recommendations/{rec_id}/approve", dependencies=[Depends(require_operator)])
async def approve(request: Request, rec_id: str, body: ApproveBody) -> dict:
    try:
        return await engine(request).approve(rec_id, body.operator, body.note, body.revision)
    except EngineError as e:
        _raise(e)


@router.post("/recommendations/{rec_id}/reject", dependencies=[Depends(require_operator)])
def reject(request: Request, rec_id: str, body: RejectBody) -> dict:
    try:
        return engine(request).reject(rec_id, body.operator, body.reason)
    except EngineError as e:
        _raise(e)


@router.get("/decisions")
def decisions(request: Request, limit: int = Query(100, ge=1, le=500)) -> list:
    return list(reversed(engine(request).decisions))[:limit]


@router.get("/incidents")
def incidents(request: Request, limit: int = Query(100, ge=1, le=500)) -> list:
    items = sorted(engine(request).incidents.values(),
                   key=lambda i: (i["status"] != "OPEN", -(i["opened_tick"] or 0)))
    return items[:limit]


@router.get("/settings")
def get_settings(request: Request) -> dict:
    e = engine(request)
    return {"autonomy_mode": e.autonomy_mode, "policy": "heuristic-v1" if e.fallback else "lp-v1",
            "safety_z": settings.safety_z, "constrained_factor": settings.constrained_factor,
            "intel_enabled": e.intel_enabled}


@router.put("/mode", dependencies=[Depends(require_admin)])
def put_mode(request: Request, body: ModeBody) -> dict:
    e = engine(request)
    if body.autonomy_mode != e.autonomy_mode:
        e.record("MODE_CHANGE", "operator:admin", f"Autonomy mode {e.autonomy_mode} → {body.autonomy_mode}")
        e.autonomy_mode = body.autonomy_mode
    return get_settings(request)


async def _admin(e: Engine, method: str, path: str, body: Any = None) -> Any:
    try:
        return await e.admin.request(method, path, json=body)
    except SimError as err:
        raise HTTPException(502, {"code": err.code or "SIMULATOR_ERROR", "message": str(err)})


@router.post("/gameday/event", dependencies=[Depends(require_admin)])
async def gameday_event(request: Request, body: EventBody) -> dict:
    e = engine(request)
    start = max(0, e.last_tick) + body.start_in_ticks
    created = await _admin(e, "POST", "/admin/events", {"type": body.type, "start_tick": start,
                                                        "duration_ticks": body.duration_ticks,
                                                        "parameters": body.parameters})
    e.record("EVENT_INJECTED", "operator:admin", f"Game Day: {body.type} from tick {start} for "
             f"{body.duration_ticks} ticks {body.parameters or ''}")
    e.dirty = True
    return created


@router.post("/gameday/fault", dependencies=[Depends(require_admin)])
async def gameday_fault(request: Request, body: FaultBody) -> dict:
    e = engine(request)
    created = await _admin(e, "POST", "/admin/faults", body.model_dump())
    e.record("FAULT_INJECTED", "operator:admin", f"Game Day: fault {body.type} for {body.duration_seconds}s")
    e.dirty = True
    return created


@router.post("/gameday/faults/clear", dependencies=[Depends(require_admin)])
async def gameday_clear(request: Request) -> dict:
    e = engine(request)
    await _admin(e, "POST", "/admin/faults/clear")
    e.record("FAULT_INJECTED", "operator:admin", "Game Day: all faults cleared")
    return {"status": "cleared"}


@router.get("/gameday/faults")
async def gameday_faults(request: Request) -> list:
    rows = await _admin(engine(request), "GET", "/admin/faults")
    return [f for f in rows or [] if f.get("active")]


@router.post("/gameday/intel", dependencies=[Depends(require_admin)])
def gameday_intel(request: Request, body: IntelBody) -> dict:
    e = engine(request)
    e.intel_enabled = body.enabled
    e.record("MODE_CHANGE", "operator:admin", f"Game Day: optimizer {'enabled' if body.enabled else 'disabled'}")
    return {"intel_enabled": e.intel_enabled}


@router.post("/gameday/simulation", dependencies=[Depends(require_admin)])
async def gameday_sim(request: Request, body: SimBody) -> dict:
    e = engine(request)
    if body.action == "step":
        for _ in range(body.ticks):  # /admin/step advances exactly one tick; it is never retried
            await _admin(e, "POST", "/admin/step")
    else:
        await _admin(e, "POST", f"/admin/{body.action}")
    if body.action == "reset":
        e.reset_hint = True
    e.record("MODE_CHANGE", "operator:admin", f"Game Day: simulation {body.action}"
             + (f" {body.ticks} tick(s)" if body.action == "step" else ""))
    e.dirty = True
    e.tick_event.set()
    return await _admin(e, "GET", "/v1/instance")
