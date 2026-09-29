"""HTTP contract tests via FastAPI's TestClient (httpx)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

from conftest import base_payload

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_metrics_exposes_expected_names():
    client.get("/health")  # generate at least one request first
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    for name in (
        "http_requests_total", "http_request_duration_seconds",
        "jalani_intel_solve_seconds", "jalani_intel_plans_total",
        "jalani_intel_forecast_wape",
    ):
        assert name in body


def test_plan_round_trip_matches_contract_shape():
    payload = base_payload(horizon_ticks=16)
    r = client.post("/v1/plan", json=payload)
    assert r.status_code == 200
    body = r.json()

    for key in ("policy", "status", "solve_ms", "actions", "pipeline", "risk", "impact", "binding", "forecast"):
        assert key in body
    assert body["policy"] == "lp-v1"
    assert body["status"] in ("optimal", "infeasible", "error")
    assert isinstance(body["actions"], list)
    if body["actions"]:
        action = body["actions"][0]
        assert set(action) == {"route_id", "fuel_type", "quantity"}
    assert "wape" in body["forecast"] and "series" in body["forecast"]
    if body["risk"]:
        entry = body["risk"][0]
        for key in (
            "station_id", "fuel_type", "inventory", "capacity", "in_transit",
            "demand_next_8h", "hours_to_stockout", "p_stockout_8h", "tier",
            "confidence", "confidence_label", "unavoidable", "reasons",
        ):
            assert key in entry


def test_plan_tolerates_missing_optional_fields():
    minimal = {"tick": 10, "tick_minutes": 15, "stations": [], "depots": [], "routes": []}
    r = client.post("/v1/plan", json=minimal)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "optimal"
    assert body["actions"] == []


def test_plan_tolerates_extra_unknown_fields():
    payload = base_payload(horizon_ticks=8)
    payload["some_future_field"] = {"nested": True}
    payload["stations"][0]["some_new_station_field"] = 42
    r = client.post("/v1/plan", json=payload)
    assert r.status_code == 200


def test_forecast_round_trip():
    r = client.post("/v1/forecast", json={
        "tick": 100, "tick_minutes": 15, "demand_profile": "industrial",
        "demand_multiplier": 1.0, "history": {"ticks": [], "values": []}, "horizon": 8,
    })
    assert r.status_code == 200
    body = r.json()
    assert "wape" in body
    assert len(body["points"]) == 8
    for p in body["points"]:
        assert set(p) == {"tick", "forecast", "lo", "hi"}


def test_forecast_returns_prequential_history_with_numeric_forecasts():
    ticks = list(range(0, 40))
    values = [100.0 + (t % 5) for t in ticks]  # non-trivial but small history
    r = client.post("/v1/forecast", json={
        "tick": ticks[-1], "tick_minutes": 15, "demand_profile": "urban_high",
        "fuel_type": "DIESEL", "demand_multiplier": 1.0,
        "history": {"ticks": ticks, "values": values}, "horizon": 4,
    })
    assert r.status_code == 200
    body = r.json()
    assert "history" in body
    assert len(body["history"]) > 0
    for h in body["history"]:
        assert set(h) == {"tick", "actual", "forecast"}
        assert isinstance(h["forecast"], (int, float))
        assert isinstance(h["actual"], (int, float))
