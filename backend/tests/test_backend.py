import asyncio
import json

import httpx
import pytest

from app import autonomy, executor, heuristic
from app.config import Settings
from app.loop import Engine, EngineError
from app.simclient import CircuitBreaker, SimClient, SimError, parse_error
from app.state import future_multipliers, hour_of, route_blocked, simple_forecast
from app.store import Store
from tests.conftest import world

TONGI = "route-gazipur-tongi"
KARNA = "route-patiya-karnaphuli"


def mock_client(handler) -> SimClient:
    client = SimClient("http://sim")
    client.http = httpx.AsyncClient(base_url="http://sim", transport=httpx.MockTransport(handler))
    return client


# ------------------------------------------------------------------ simulator client
def test_parse_error_shapes():
    fault = httpx.Response(503, json={"error": {"code": "FAULT_INJECTED", "message": "x"}})
    domain = httpx.Response(409, json={"detail": {"code": "ROUTE_DISRUPTED", "message": "y"}})
    assert parse_error(fault)[0] == "FAULT_INJECTED"
    assert parse_error(domain)[0] == "ROUTE_DISRUPTED"


def test_breaker_opens_and_half_opens():
    b = CircuitBreaker(threshold=2, reset_seconds=0.0)
    b.failure()
    b.failure()
    assert b.state == "OPEN"
    assert b.allow() and b.state == "HALF_OPEN"
    b.success()
    assert b.state == "CLOSED"


def test_retry_reuses_idempotency_key_and_recovers():
    calls = []

    def handler(request):
        calls.append(request.content)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED"}})
        return httpx.Response(201, json={"id": 7})

    out = asyncio.run(mock_client(handler).request("POST", "/v1/allocations", json={"idempotency_key": "k1"}))
    assert out == {"id": 7} and len(calls) == 2 and calls[0] == calls[1]


def test_lost_response_retry_creates_one_allocation():
    """The simulator accepts the POST but the response is lost: the retry must not create a second shipment."""
    ledger: dict = {}

    def handler(request):
        body = json.loads(request.content)
        key = body["idempotency_key"]
        if key not in ledger:
            ledger[key] = {"id": len(ledger) + 1, **body}
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(201, json=ledger[key])  # idempotent replay

    out = asyncio.run(mock_client(handler).request("POST", "/v1/allocations",
                                                   json={"idempotency_key": "k1", "quantity": 500}))
    assert out["id"] == 1 and len(ledger) == 1


def test_domain_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(409, json={"detail": {"code": "INSUFFICIENT_INVENTORY"}})

    client = mock_client(handler)
    with pytest.raises(SimError) as e:
        asyncio.run(client.request("POST", "/v1/allocations", json={}))
    assert e.value.code == "INSUFFICIENT_INVENTORY" and len(calls) == 1 and client.breaker.state == "CLOSED"


def test_non_idempotent_admin_post_is_never_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED"}})

    with pytest.raises(SimError):
        asyncio.run(mock_client(handler).request("POST", "/admin/events", json={"type": "demand_spike"}))
    assert len(calls) == 1  # a retried event POST could inject the crisis twice


# ------------------------------------------------------------------ executor
def test_prepare_splits_and_respects_rules(snap):
    bodies = executor.prepare(snap, [{"route_id": TONGI, "fuel_type": "DIESEL", "quantity": 13000}], "r1")
    assert sum(b["quantity"] for b in bodies) <= 13000
    assert all(b["quantity"] <= 6500 for b in bodies)
    assert len({b["idempotency_key"] for b in bodies}) == len(bodies)
    big = executor.prepare(snap, [{"route_id": TONGI, "fuel_type": "DIESEL", "quantity": 50000}], "r2")
    assert sum(b["quantity"] for b in big) <= 12000  # Gazipur dispatch capacity per tick


def test_arrival_fits_but_current_tank_does_not():
    """Reviewer's example: cap 15,000, stock 14,000, 3,000 L used before arrival, 4,000 L proposed.
    The arrival projection fits (15,000) but the simulator's POST rule (14,000 + q <= 15,000) caps it at 1,000."""
    s = world()
    st = next(x for x in s.stations if x.id == "station-karnaphuli")
    st.capacity["PETROL"], st.inventory["PETROL"] = 15000, 14000
    bodies = executor.prepare(s, [{"route_id": KARNA, "fuel_type": "PETROL", "quantity": 4000}], "r",
                              consumption=lambda station, fuel, ticks: 3000.0)
    assert sum(b["quantity"] for b in bodies) == 1000


def test_arrival_rule_counts_fuel_already_on_the_way():
    alloc = {"id": 5, "idempotency_key": "k", "source_depot_id": "depot-gazipur", "destination_station_id": "station-tongi",
             "route_id": TONGI, "fuel_type": "DIESEL", "quantity": 10000, "created_tick": 9, "status": "IN_TRANSIT"}
    s = world(allocations=[alloc], station_inv=2000.0)  # cap 18,000: room now 16,000, room at arrival 6,000
    bodies = executor.prepare(s, [{"route_id": TONGI, "fuel_type": "DIESEL", "quantity": 12000}], "r")
    assert sum(b["quantity"] for b in bodies) == 6000


def test_competing_shipments_share_station_and_dispatch_budgets():
    s = world(station_inv=12000.0)  # Tongi diesel room 6,000
    two_same_station = executor.prepare(s, [{"route_id": TONGI, "fuel_type": "DIESEL", "quantity": 5000},
                                            {"route_id": TONGI, "fuel_type": "DIESEL", "quantity": 5000}], "r")
    assert sum(b["quantity"] for b in two_same_station) <= 6000
    s2 = world(station_inv=0.0)
    gazipur = executor.prepare(s2, [{"route_id": TONGI, "fuel_type": "PETROL", "quantity": 9000},
                                    {"route_id": "route-gazipur-karnaphuli", "fuel_type": "DIESEL", "quantity": 5000}], "r")
    assert sum(b["quantity"] for b in gazipur) <= 12000  # one depot's dispatch capacity across both routes


def _cut(start: int, end: int, status: str = "SCHEDULED") -> dict:
    return {"id": 1, "type": "route_disruption", "start_tick": start, "end_tick": end, "status": status,
            "parameters": {"route_ids": [TONGI]}}


def test_no_dispatch_into_disruption_starting_now():
    s = world(tick=10, events=[_cut(10, 20)])
    assert executor.prepare(s, [{"route_id": TONGI, "fuel_type": "DIESEL", "quantity": 3000}], "r") == []


def test_race_margin_blocks_a_disruption_starting_next_tick():
    s = world(tick=10, events=[_cut(11, 20)])
    act = [{"route_id": TONGI, "fuel_type": "DIESEL", "quantity": 3000}]
    assert executor.prepare(s, act, "r", race_margin=1) == []
    assert executor.prepare(s, act, "r", race_margin=0) != []  # stepped benchmark: no race possible


def test_route_is_eligible_again_after_the_disruption_ends():
    route = next(r for r in world().routes if r.id == TONGI)
    assert not route_blocked(world(tick=21, events=[_cut(10, 20, "ACTIVE")]), route, 21)
    assert not route_blocked(world(tick=15, events=[_cut(10, 20, "RESOLVED")]), route, 15)
    assert route_blocked(world(tick=15, events=[_cut(10, 20, "ACTIVE")]), route, 15)


def test_guard_targets_pending_on_closing_route():
    alloc = {"id": 5, "idempotency_key": "k", "source_depot_id": "depot-gazipur", "destination_station_id": "station-tongi",
             "route_id": TONGI, "fuel_type": "DIESEL", "quantity": 1000, "created_tick": 10, "status": "PENDING"}
    assert executor.guard_targets(world(tick=10, events=[_cut(10, 20)], allocations=[alloc])) == [5]


# ------------------------------------------------------------------ forecast and heuristic
def _industrial_history(values_by_tick):
    return {"station-tongi|DIESEL": [(t, v) for t, v in values_by_tick]}


def test_forecast_spike_start_is_counted_once():
    s = world(tick=32)
    st = next(x for x in s.stations if x.id == "station-tongi")
    st.demand_multiplier = 1.8
    shape = heuristic_shape
    hist = _industrial_history([(t, (180 if t >= 30 else 100) * shape(t)) for t in range(32)])
    fc = simple_forecast(hist, st, "DIESEL", 32, 15, 4, [(0, 1.0), (30, 1.8)], [1.8] * 4)
    assert fc[0] == pytest.approx(180 * shape(33), rel=0.02)  # not 1.8 squared


def test_forecast_normalizes_after_spike_ends():
    s = world(tick=32)
    st = next(x for x in s.stations if x.id == "station-tongi")
    st.demand_multiplier = 1.0
    hist = _industrial_history([(t, 180 * heuristic_shape(t)) for t in range(32)])
    fc = simple_forecast(hist, st, "DIESEL", 32, 15, 4, [(0, 1.8), (32, 1.0)], [1.0] * 4)
    assert fc[0] == pytest.approx(100 * heuristic_shape(33), rel=0.02)


def heuristic_shape(t: int) -> float:
    h = hour_of(t, 15)
    return 1.55 if 6 <= h <= 17 else 0.45


def test_future_multipliers_divide_out_a_spike_at_its_end():
    spike = {"id": 2, "type": "demand_spike", "start_tick": 5, "end_tick": 13, "status": "ACTIVE",
             "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}}
    s = world(tick=10, events=[spike])
    st = next(x for x in s.stations if x.id == "station-tongi")
    st.demand_multiplier = 1.8
    assert future_multipliers(s, st, 4) == pytest.approx([1.8, 1.8, 1.0, 1.0])


def test_heuristic_ships_to_low_station_within_limits():
    s = world(station_inv=300.0)
    hist = {"station-tongi|DIESEL": [(t, 220.0) for t in range(0, 10)]}
    plan = heuristic.plan(s, hist)
    tongi = [a for a in plan["actions"] if a["route_id"] == TONGI and a["fuel_type"] == "DIESEL"]
    assert tongi and tongi[0]["quantity"] <= 18000 - 300
    assert all(a["quantity"] <= 12000 for a in plan["actions"])
    assert any(r["hours_to_stockout"] is None or r["hours_to_stockout"] > 12 for r in plan["risk"])  # 24 h horizon


# ------------------------------------------------------------------ autonomy
def test_most_restrictive_condition_wins():
    worst = autonomy.conditions("heuristic-v1", cross_region=True, confidence_label="LOW", stale=False, rationing=False)
    assert worst == ["LOW_CONFIDENCE", "CROSS_REGION", "FALLBACK"]
    assert autonomy.display_class(worst) == "LOW_CONFIDENCE"
    assert autonomy.requires_approval(worst, "CRITICAL", True, "AUTOPILOT", "NORMAL")
    rationing_fallback = autonomy.conditions("heuristic-v1", False, "MEDIUM", False, rationing=True)
    assert autonomy.requires_approval(rationing_fallback, "CRITICAL", False, "AUTOPILOT", "NORMAL")


def test_autonomy_table():
    assert autonomy.requires_approval(["CROSS_REGION"], "CRITICAL", True, "SUPERVISED", "NORMAL")
    assert not autonomy.requires_approval(["CROSS_REGION"], "CRITICAL", True, "AUTOPILOT", "NORMAL")
    assert not autonomy.requires_approval([], "HIGH", False, "SUPERVISED", "NORMAL")
    assert autonomy.requires_approval([], "HIGH", False, "ADVISORY", "NORMAL")
    assert not autonomy.requires_approval(["FALLBACK"], "CRITICAL", False, "SUPERVISED", "NORMAL")
    assert autonomy.requires_approval(["FALLBACK"], "HIGH", False, "SUPERVISED", "NORMAL")
    assert autonomy.requires_approval([], "HIGH", False, "SUPERVISED", "DEGRADED")
    assert not autonomy.requires_approval([], "CRITICAL", False, "SUPERVISED", "DEGRADED")
    assert autonomy.requires_approval([], "CRITICAL", False, "AUTOPILOT", "SAFE_HOLD")
    stale = autonomy.conditions("lp-v1", False, "HIGH", stale=True, rationing=False)
    assert stale == ["LOW_CONFIDENCE"] and autonomy.requires_approval(stale, "CRITICAL", False, "AUTOPILOT", "NORMAL")


# ------------------------------------------------------------------ approvals (engine with a mock simulator)
def _serve(snap, path: str):
    parts = {"/v1/instance": snap.instance, "/v1/regions": snap.regions, "/v1/stations": snap.stations,
             "/v1/depots": snap.depots, "/v1/routes": snap.routes, "/v1/supply-arrivals": snap.supply,
             "/v1/events": snap.events, "/v1/allocations": snap.allocations, "/v1/metrics": snap.metrics}
    val = parts[path]
    return [x.model_dump() for x in val] if isinstance(val, list) else val.model_dump()


def _engine(tmp_path, snap, reject_route: str | None = None):
    posts: list[dict] = []

    def handler(request):
        if request.method == "POST" and request.url.path == "/v1/allocations":
            body = json.loads(request.content)
            if body["route_id"] == reject_route:
                return httpx.Response(409, json={"detail": {"code": "DISPATCH_CAPACITY_EXCEEDED", "message": "x"}})
            posts.append(body)
            return httpx.Response(201, json={"id": len(posts), **body, "status": "PENDING", "created_tick": snap.tick})
        return httpx.Response(200, json=_serve(snap, request.url.path))

    eng = Engine(Settings(), Store(str(tmp_path / "t.db")))
    eng.sim.http = httpx.AsyncClient(base_url="http://sim", transport=httpx.MockTransport(handler))
    eng.snap, eng.last_tick = snap, snap.tick
    eng.regions = [r.model_dump() for r in snap.regions]
    return eng, posts


def _rec(actions: list[dict]) -> dict:
    return {"id": "rec-1-10-station-tongi-DIESEL", "epoch": 1, "created_tick": 10, "deadline_tick": 20,
            "status": "PENDING", "rec_class": "CROSS_REGION", "conditions": ["CROSS_REGION"], "revision": 1,
            "requires_approval": True, "station_id": "station-tongi", "fuel_type": "DIESEL", "tier": "CRITICAL",
            "actions": actions, "impact": {}, "confidence": 0.8, "confidence_label": "HIGH", "signals": [],
            "constraints": [], "explanation": "", "policy": "lp-v1", "decided_by": None, "decided_at_tick": None,
            "note": None, "allocation_ids": [], "failed_actions": [], "cross_region": True}


def _act(route: str, fuel: str, qty: float) -> dict:
    return {"route_id": route, "fuel_type": fuel, "quantity": qty, "transit_ticks": 2, "eta_tick": 12}


def test_two_simultaneous_approvals_execute_once(tmp_path):
    eng, posts = _engine(tmp_path, world())
    rec = _rec([_act(TONGI, "DIESEL", 3000)])
    eng.recs[rec["id"]] = rec

    async def both():
        return await asyncio.gather(eng.approve(rec["id"], "a", "", 1), eng.approve(rec["id"], "b", "", 1),
                                    return_exceptions=True)

    results = asyncio.run(both())
    assert len(posts) == 1 and rec["status"] == "EXECUTED"
    assert sum(isinstance(r, EngineError) and r.code == "NOT_PENDING" for r in results) == 1


def test_stale_revision_is_refused(tmp_path):
    eng, posts = _engine(tmp_path, world())
    rec = _rec([_act(TONGI, "DIESEL", 3000)])
    rec["revision"] = 3
    eng.recs[rec["id"]] = rec
    with pytest.raises(EngineError) as e:
        asyncio.run(eng.approve(rec["id"], "a", "", 2))
    assert e.value.code == "REVISION_CHANGED" and posts == []


def test_big_revalidation_change_is_shown_not_executed(tmp_path):
    eng, posts = _engine(tmp_path, world())
    rec = _rec([_act(TONGI, "DIESEL", 20000)])  # dispatch capacity allows only 12,000 now
    eng.recs[rec["id"]] = rec
    with pytest.raises(EngineError) as e:
        asyncio.run(eng.approve(rec["id"], "a", "", 1))
    assert e.value.code == "REVALIDATION_CHANGED" and posts == []
    assert rec["revision"] == 2 and rec["actions"][0]["quantity"] == 12000 and rec["status"] == "PENDING"


def test_partial_execution_is_recorded(tmp_path):
    eng, posts = _engine(tmp_path, world(), reject_route=KARNA)
    rec = _rec([_act(TONGI, "DIESEL", 3000), _act(KARNA, "PETROL", 3000)])
    eng.recs[rec["id"]] = rec
    out = asyncio.run(eng.approve(rec["id"], "a", "", 1))
    assert out["status"] == "EXECUTED" and len(out["allocation_ids"]) == 1
    assert [(f["route_id"], f["code"]) for f in out["failed_actions"]] == [(KARNA, "DISPATCH_CAPACITY_EXCEEDED")]


def test_lost_response_is_reconciled_from_the_ledger(tmp_path):
    eng, _ = _engine(tmp_path, world())
    rec = _rec([_act(TONGI, "DIESEL", 3000)])
    rec.update(status="FAILED", decided_by="a", failed_actions=[
        {"route_id": TONGI, "fuel_type": "DIESEL", "quantity": 3000.0, "code": "TIMEOUT", "idempotency_key": "k-lost"}])
    eng.recs[rec["id"]] = rec
    alloc = {"id": 9, "idempotency_key": "k-lost", "source_depot_id": "depot-gazipur",
             "destination_station_id": "station-tongi", "route_id": TONGI, "fuel_type": "DIESEL", "quantity": 3000,
             "created_tick": 10, "status": "PENDING"}
    eng.reconcile(world(allocations=[alloc]))
    assert rec["status"] == "EXECUTED" and rec["allocation_ids"] == [9] and rec["failed_actions"] == []


def test_pending_recommendation_stays_stable_across_small_replans(tmp_path):
    eng, _ = _engine(tmp_path, world())
    rec = _rec([_act(TONGI, "DIESEL", 3000)])
    eng.recs[rec["id"]] = rec
    eng._revise(rec, {**_rec([_act(TONGI, "DIESEL", 3100)]), "deadline_tick": 25})
    assert rec["revision"] == 1 and rec["actions"][0]["quantity"] == 3000 and rec["deadline_tick"] == 20
    eng._revise(rec, _rec([_act(TONGI, "DIESEL", 6000)]))
    assert rec["revision"] == 2 and rec["actions"][0]["quantity"] == 6000
