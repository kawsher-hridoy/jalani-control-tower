"""The control loop: observe → detect → predict → decide → act → monitor → recover."""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError

from . import autonomy, detect, executor, heuristic, metrics
from .config import Settings
from .models import DemandRow, Snapshot
from .simclient import SimClient, SimError
from .state import (consumption_fn, days_of_cover, fetch_snapshot, future_multipliers, in_transit, in_transit_list,
                    multiplier_at, pending_dispatch)
from .store import Store

log = logging.getLogger("jalani.engine")
MODE_GAUGE = {"NORMAL": 0, "DEGRADED": 1, "SAFE_HOLD": 2}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def risk_horizon(snap: Snapshot) -> int:
    return max(1, round(24 * 60 / snap.instance.tick_minutes))  # 24 h, whatever the tick length


def demand_series(snap: Snapshot, history: dict, mult_logs: dict | None) -> dict:
    """History per series plus the multiplier at each observation and the expected future multipliers (§7)."""
    stations = {st.id: st for st in snap.stations}
    fut = {st.id: future_multipliers(snap, st, risk_horizon(snap)) for st in snap.stations}
    out = {}
    for key, rows in history.items():
        sid = key.split("|")[0]
        st = stations.get(sid)
        if st is None:
            continue
        log = (mult_logs or {}).get(sid, [])
        out[key] = {"ticks": [t for t, _ in rows], "values": [v for _, v in rows],
                    "multipliers": [multiplier_at(log, t, st.demand_multiplier) for t, _ in rows],
                    "future_multipliers": fut[sid]}
    return out


def build_plan_request(snap: Snapshot, history: dict, epoch: int, s: Settings, mult_logs: dict | None = None) -> dict:
    """The intel /v1/plan request (contract §7), shared by the engine and the benchmark."""
    return {
        "epoch": epoch, "tick": snap.tick, "tick_minutes": snap.instance.tick_minutes,
        "depots": [d.model_dump() for d in snap.depots], "stations": [x.model_dump() for x in snap.stations],
        "routes": [r.model_dump() for r in snap.routes],
        "supply": [x.model_dump() for x in snap.supply if x.status != "ARRIVED"],
        "events": [e.model_dump() for e in snap.events if e.status != "RESOLVED"],
        "in_transit": in_transit_list(snap), "pending_dispatch": pending_dispatch(snap),
        "demand_history": demand_series(snap, history, mult_logs),
        "settings": {"horizon_ticks": s.horizon_ticks, "risk_horizon_ticks": risk_horizon(snap), "safety_z": s.safety_z,
                     "constrained_factor": s.constrained_factor, "priorities": {}},
    }


class EngineError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Engine:
    def __init__(self, settings: Settings, store: Store):
        self.s = settings
        self.store = store
        self.sim = SimClient(settings.simulator_url)
        self.admin = SimClient(settings.simulator_url, use_breaker=False, retries=1)
        self.lab = SimClient(settings.sim_lab_url, use_breaker=False, retries=0)
        self.intel = httpx.AsyncClient(base_url=settings.intel_url, timeout=1.5)
        self.autonomy_mode = settings.autonomy_mode if settings.autonomy_mode in autonomy.MODES else "SUPERVISED"
        self.intel_enabled = True
        self.epoch = 0
        self.decisions: deque = deque(self.store.recent_decisions(500)[::-1], maxlen=500)
        self.decision_seq = max((d["id"] for d in self.decisions), default=0)
        self.inc_seq = 0
        self.mode = "NORMAL"
        self.stream_down = True
        self.fallback = False
        self.intel_status = "UNKNOWN"
        self.intel_fail_streak = 0
        self.intel_ok_streak = 0
        self.sim_lab_up = False
        self.invalid_streak = 0
        self.tick_event = asyncio.Event()
        self.exec_lock = asyncio.Lock()      # one writer: auto-execution and approvals never interleave
        self.preview_lock = asyncio.Lock()
        self.preview_cache: tuple | None = None
        self.dirty = False                   # set by Game Day / approvals: refresh before the next tick
        self.reset_hint = False              # set by the SSE "Simulation reset" notice
        self.world = "UNKNOWN"
        self.last_refresh = 0.0
        self.last_cycle_at = 0.0
        self.last_intel_try = 0.0
        self.loop_stats = {"last_tick": -1, "tick_lag": 0, "ticks_skipped": 0, "last_cycle_ms": 0.0,
                           "last_plan_ms": 0.0, "plans_total": 0}
        self.tasks: list[asyncio.Task] = []
        self._new_epoch_state()

    # ------------------------------------------------------------------ state
    def _new_epoch_state(self) -> None:
        self.epoch += 1
        self.snap: Snapshot | None = None
        self.prev: Snapshot | None = None
        self.regions: list | None = None
        self.seed: int | None = None
        self.last_tick = -1
        self.history: dict[str, list[tuple[int, float]]] = defaultdict(list)
        self.last_demand_tick = -1
        self.audit_cursor = 0
        self.fuel_lost = {"depot_overflow": 0.0, "station_overflow": 0.0, "failed_shipment": 0.0}
        self.failed_seen: set[int] = set()
        self.recs: dict[str, dict] = {}
        self.incidents: dict[str, dict] = {}
        self.open_keys: dict[str, str] = {}
        self.plan: dict | None = None
        self.mult_logs: dict[str, list[tuple[int, float]]] = defaultdict(list)
        self.max_alloc_id = 0
        self.history_gap_until = -1

    def new_epoch(self, reason: str) -> None:
        old = self.epoch
        self._new_epoch_state()
        self.record("RESET_DETECTED", "system", f"Simulator reset detected ({reason}); epoch {old} → {self.epoch}")
        self.open_incident("reset", "RESET", "INFO", "Simulator reset detected",
                           f"The world restarted ({reason}). State was reloaded from the simulator.", [])

    # -------------------------------------------------------------- recording
    def record(self, kind: str, actor: str, summary: str, **extra) -> dict:
        self.decision_seq += 1
        d = {"id": self.decision_seq, "epoch": self.epoch, "tick": self.last_tick, "wall_time": _now_iso(),
             "kind": kind, "actor": actor, "summary": summary, "recommendation_id": extra.get("recommendation_id"),
             "allocation_id": extra.get("allocation_id"), "idempotency_key": extra.get("idempotency_key"),
             "policy": extra.get("policy"), "outcome": extra.get("outcome", "OK")}
        self.decisions.append(d)
        self.store.add_decision(d)
        metrics.DECISIONS.labels(kind.lower(), actor.split(":")[0]).inc()
        log.info(json.dumps({"event": "decision", **d}))
        return d

    def open_incident(self, key: str, itype: str, severity: str, title: str, summary: str, entities: list) -> None:
        if key in self.open_keys:
            return
        self.inc_seq += 1
        inc = {"id": f"inc-{self.epoch}-{self.inc_seq}", "epoch": self.epoch, "type": itype, "severity": severity,
               "status": "OPEN", "title": title, "summary": summary, "opened_tick": self.last_tick,
               "resolved_tick": None, "opened_at": _now_iso(), "resolved_at": None, "entities": entities,
               "timeline": [{"tick": self.last_tick, "text": title}], "brief": None}
        self.incidents[inc["id"]] = inc
        self.open_keys[key] = inc["id"]
        self.store.upsert("incidents", inc)
        metrics.ALERTS.labels(itype).inc()
        log.info(json.dumps({"event": "incident_opened", "id": inc["id"], "type": itype, "title": title}))

    def note_incident(self, key: str, text: str) -> None:
        inc = self.incidents.get(self.open_keys.get(key, ""))
        if inc:
            inc["timeline"].append({"tick": self.last_tick, "text": text})
            self.store.upsert("incidents", inc)

    def resolve_incident(self, key: str, text: str) -> None:
        inc_id = self.open_keys.pop(key, None)
        inc = self.incidents.get(inc_id or "")
        if inc:
            inc.update(status="RESOLVED", resolved_tick=self.last_tick, resolved_at=_now_iso())
            inc["timeline"].append({"tick": self.last_tick, "text": text})
            self.store.upsert("incidents", inc)

    # ------------------------------------------------------------------- loop
    async def start(self) -> None:
        self.tasks = [asyncio.create_task(self.run()), asyncio.create_task(self.sse_loop()),
                      asyncio.create_task(self.lab_ping())]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for c in (self.sim, self.admin, self.lab):
            await c.close()
        await self.intel.aclose()

    async def run(self) -> None:
        while True:
            try:
                await self.cycle()
            except Exception:  # never let one bad cycle stop the loop
                log.exception("cycle failed")
            try:
                await asyncio.wait_for(self.tick_event.wait(), timeout=self.s.poll_seconds)
            except asyncio.TimeoutError:
                pass
            self.tick_event.clear()

    async def sse_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with self.sim.http.stream("GET", "/v1/stream", timeout=httpx.Timeout(None, connect=2.0)) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(f"stream status {resp.status_code}")
                    self.stream_down, backoff = False, 1.0
                    event = None
                    async for line in resp.aiter_lines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:") and event in ("simulation.tick", "simulator.notice"):
                            if event == "simulator.notice" and "reset" in line.lower():
                                self.reset_hint = True
                            self.tick_event.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.stream_down = True
            await asyncio.sleep(backoff)
            backoff = min(10.0, backoff * 2)

    async def lab_ping(self) -> None:
        while True:
            try:
                await self.lab.request("GET", "/v1/health")
                self.sim_lab_up = True
            except Exception:
                self.sim_lab_up = False
            await asyncio.sleep(10)

    async def cycle(self) -> None:
        t0 = time.perf_counter()
        self.last_cycle_at = time.time()
        try:
            inst = await self.sim.request("GET", "/v1/instance")
        except SimError:
            self.update_mode()
            return
        tick, seed = inst["tick"], inst["seed"]
        self.world = inst.get("status", "UNKNOWN")
        if self.snap is not None and (tick < self.last_tick or seed != self.seed or self.reset_hint):
            self.new_epoch("reset notice" if self.reset_hint else f"tick {self.last_tick} → {tick}")
        self.reset_hint = False
        if tick == self.last_tick:
            if self.dirty or (self.world == "PAUSED" and time.time() - self.last_refresh > 2):
                await self.refresh_same_tick()
            else:
                await self.guard_same_tick()
            self.update_mode()
            return
        try:
            if self.regions is None:
                self.regions = await self.sim.request("GET", "/v1/regions")
            snap = await fetch_snapshot(self.sim, inst, self.regions)
            if not snap.consistent:  # the reads straddled a tick: read once more, else show it but don't act on it
                snap = await fetch_snapshot(self.sim, None, self.regions)
                tick = snap.tick
        except SimError:
            self.update_mode()
            return
        except (ValidationError, KeyError, TypeError) as exc:
            self.invalid_streak += 1
            metrics.SIM_REQUESTS.labels("/v1/snapshot", "invalid").inc()
            log.warning(json.dumps({"event": "invalid_snapshot", "error": str(exc)[:300]}))
            if self.invalid_streak >= 3:
                self.open_incident("invalid-data", "SIMULATOR_FAULT", "WARNING", "Invalid simulator data rejected",
                                   "Snapshots failed validation and were rejected; the last good state is kept.", [])
            self.update_mode()
            return
        self.invalid_streak = 0
        self.resolve_incident("invalid-data", "valid data again")
        top = max((a.id for a in snap.allocations), default=0)
        if self.max_alloc_id and top < self.max_alloc_id:  # the allocation ledger restarted: a reset we missed
            self.new_epoch("allocation ledger restarted")
        self.max_alloc_id = max(self.max_alloc_id, top)
        skipped = max(0, tick - self.last_tick - 1) if self.last_tick >= 0 else 0
        self.loop_stats["ticks_skipped"] += skipped
        self.loop_stats["tick_lag"] = skipped
        self.prev, self.snap, self.last_tick, self.seed = self.snap, snap, tick, seed
        self.last_refresh, self.dirty = time.time(), False
        self.log_multipliers(snap)
        self.reconcile(snap)
        await self.update_demand(snap)
        await self.read_audit(snap)
        detect.run(self, self.prev, snap)
        await self.guard(snap)
        self.update_mode()
        plan = await self.make_plan(snap)
        self.plan = plan
        await self.apply_plan(plan, snap, execute=snap.consistent)
        self.expire(tick)
        self.update_metrics(snap, plan)
        self.loop_stats["last_tick"] = tick
        self.loop_stats["last_cycle_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        metrics.LOOP_SECONDS.observe(time.perf_counter() - t0)

    async def refresh_same_tick(self) -> None:
        """Paused world or a Game Day action: re-read and re-assess, but never auto-execute twice in one tick."""
        try:
            snap = await fetch_snapshot(self.sim, None, self.regions)
        except (SimError, ValidationError, KeyError, TypeError):
            return
        if snap.tick != self.last_tick:
            return  # the world moved on: the normal cycle handles the new tick
        self.prev, self.snap = self.snap, snap
        self.last_refresh, self.dirty = time.time(), False
        detect.run(self, self.prev, snap)
        await self.guard(snap)
        plan = await self.make_plan(snap)
        self.plan = plan
        await self.apply_plan(plan, snap, execute=False)
        self.update_metrics(snap, plan)

    def reconcile(self, snap: Snapshot) -> None:
        """A POST can succeed while its response is lost (timeout, fault). The ledger is the truth: if an allocation
        with that idempotency key exists, the action was executed. Plans already count it as in transit."""
        by_key = {a.idempotency_key: a.id for a in snap.allocations}
        for rec in self.recs.values():
            found = [f for f in rec.get("failed_actions", []) if f.get("idempotency_key") in by_key]
            if not found:
                continue
            for f in found:
                rec["failed_actions"].remove(f)
                rec["allocation_ids"].append(by_key[f["idempotency_key"]])
                self.record("ALLOCATION", "system", f"Reconciled: {int(f['quantity']):,} L via {f['route_id']} was "
                            f"accepted by the simulator although the response was lost ({f['code']})",
                            recommendation_id=rec["id"], allocation_id=by_key[f["idempotency_key"]],
                            idempotency_key=f["idempotency_key"], policy=rec["policy"])
            if rec["status"] == "FAILED":
                rec["status"] = "EXECUTED" if rec.get("decided_by") else "AUTO_EXECUTED"
            self.store.upsert("recommendations", rec)

    def log_multipliers(self, snap: Snapshot) -> None:
        for st in snap.stations:
            log = self.mult_logs[st.id]
            if not log or log[-1][1] != st.demand_multiplier:
                log.append((snap.tick, st.demand_multiplier))

    def consumption(self, snap: Snapshot):
        return consumption_fn(snap, self.history, self.mult_logs)

    # --------------------------------------------------------------- observe
    async def update_demand(self, snap: Snapshot) -> None:
        new_ticks = snap.tick - self.last_demand_tick if self.last_demand_tick >= 0 else 200
        n_series = max(1, sum(len(st.capacity) for st in snap.stations))
        limit = max(24, min(2000, n_series * new_ticks + 24))
        if self.last_demand_tick >= 0 and n_series * new_ticks > 2000:  # the outage was longer than one page covers
            self.history_gap_until = snap.tick + 192
        try:
            rows = await self.sim.request("GET", f"/v1/demand-history?limit={limit}")
            parsed = [DemandRow(**r) for r in rows]
        except (SimError, ValidationError):
            return
        fresh = sorted((r for r in parsed if r.tick > self.last_demand_tick), key=lambda r: r.tick)
        for r in fresh:
            series = self.history[f"{r.station_id}|{r.fuel_type}"]
            series.append((r.tick, r.demand_liters))
            if len(series) > 192:
                del series[: len(series) - 192]
        if fresh:
            self.last_demand_tick = fresh[-1].tick

    async def read_audit(self, snap: Snapshot) -> None:
        gap = snap.tick - self.last_tick if self.last_tick >= 0 else 1
        try:
            rows = await self.admin.request("GET", f"/admin/audit?limit={min(1000, 200 + 40 * max(gap, 1))}")
        except SimError:
            rows = []
        ids = [r.get("id", 0) for r in rows]
        if self.audit_cursor and ids and min(ids) > self.audit_cursor + 1:  # rows missed: loss totals incomplete
            self.history_gap_until = snap.tick + 192
        supply = {s.id: s for s in snap.supply}
        allocs = {str(a.id): a for a in snap.allocations}
        for row in sorted((r for r in rows if r.get("id", 0) > self.audit_cursor), key=lambda r: r["id"]):
            self.audit_cursor = row["id"]
            meta = row.get("metadata_json") or {}
            if row.get("action") == "supply.arrived" and row.get("entity_id") in supply:
                lost = supply[row["entity_id"]].quantity - float(meta.get("added", supply[row["entity_id"]].quantity))
                if lost > 1:
                    self.add_loss("depot_overflow", lost, f"{int(lost):,} L of supply {row['entity_id']} discarded "
                                  f"at a full depot ({supply[row['entity_id']].depot_id})")
            elif row.get("action") == "allocation.arrived" and row.get("entity_id") in allocs:
                a = allocs[row["entity_id"]]
                lost = a.quantity - float(meta.get("received", a.quantity))
                if lost > 1:
                    self.add_loss("station_overflow", lost,
                                  f"{int(lost):,} L discarded at a full station ({a.destination_station_id})")
        for a in snap.allocations:
            if a.status == "FAILED" and a.id not in self.failed_seen:
                self.failed_seen.add(a.id)
                self.add_loss("failed_shipment", a.quantity,
                              f"shipment {a.id} failed ({a.failure_reason}); {int(a.quantity):,} L lost")

    def add_loss(self, cause: str, litres: float, text: str) -> None:
        self.fuel_lost[cause] += litres
        key = f"fuel-loss-{cause}"
        self.open_incident(key, "FUEL_LOSS", "WARNING", f"Fuel lost: {cause.replace('_', ' ')}", text, [])
        self.note_incident(key, text)

    # ------------------------------------------------------------------ guard
    async def guard(self, snap: Snapshot) -> None:
        for alloc_id in executor.guard_targets(snap):
            try:
                await self.sim.request("POST", f"/v1/allocations/{alloc_id}/cancel")
                metrics.GUARD_CANCELS.inc()
                self.record("CANCEL", "auto", f"Guard cancelled pending shipment {alloc_id}: its route closes "
                            f"before departure (fuel refunded instead of lost)", allocation_id=alloc_id)
            except SimError as e:
                if e.code != "CANNOT_CANCEL":
                    log.warning(json.dumps({"event": "guard_cancel_failed", "id": alloc_id, "error": str(e)}))

    async def guard_same_tick(self) -> None:
        """Between ticks: re-read events so a disruption injected 'now' cannot destroy our pending shipments."""
        if self.snap is None or not any(a.status == "PENDING" for a in self.snap.allocations):
            return
        try:
            events = await self.sim.request("GET", "/v1/events")
            from .models import Event
            self.snap.events = [Event(**e) for e in events]
        except (SimError, ValidationError):
            return
        await self.guard(self.snap)

    # ------------------------------------------------------------ operating mode
    def update_mode(self) -> None:
        now = time.time()
        breaker_open = self.sim.breaker is not None and self.sim.breaker.state == "OPEN"
        age = now - self.sim.last_success if self.sim.last_success else 1e9
        if breaker_open or self.snap is None or age > 10:
            mode = "SAFE_HOLD"
        elif self.sim.stale or age > 5 or self.sim.rolling.p95() > 1000:
            mode = "DEGRADED"
        else:
            mode = "NORMAL"
        if mode != self.mode:
            self.record("MODE_CHANGE", "system", f"Operating mode {self.mode} → {mode}")
            if mode == "SAFE_HOLD":
                self.open_incident("mode-safe-hold", "SIMULATOR_FAULT", "CRITICAL", "Simulator unreachable: SAFE_HOLD",
                                   "No shipments are sent. The last good state is shown until the simulator recovers.", [])
            elif self.mode == "SAFE_HOLD":
                self.resolve_incident("mode-safe-hold", "simulator reachable again")
            if mode == "DEGRADED":
                self.open_incident("mode-degraded", "STALE_DATA" if self.sim.stale else "SIMULATOR_FAULT", "WARNING",
                                   "Degraded data: automation limited",
                                   "Data is stale or slow. Only critical routine top-ups run automatically.", [])
            elif self.mode == "DEGRADED":
                self.resolve_incident("mode-degraded", "data healthy again")
            self.mode = mode
        metrics.OPERATING_MODE.set(MODE_GAUGE[self.mode])
        metrics.CIRCUIT_STATE.set(self.sim.breaker.gauge if self.sim.breaker else 0)
        metrics.DATA_AGE.set(min(age, 1e6))

    def flags(self) -> list[str]:
        out = []
        if self.fallback:
            out.append("FALLBACK_POLICY")
        if self.sim.stale:
            out.append("STALE_DATA")
        if self.stream_down:
            out.append("STREAM_DOWN")
        if not self.intel_enabled:
            out.append("INTEL_DISABLED")
        if self.store.error:
            out.append("DB_ERROR")
        if self.snap is not None and self.snap.tick < self.history_gap_until:
            out.append("HISTORY_GAP")
        return out

    def readiness(self) -> dict[str, bool]:
        return {"snapshot": self.snap is not None, "loop_recent": time.time() - self.last_cycle_at < 10,
                "database": self.store.error is None}

    # ------------------------------------------------------------------- plan
    def plan_request(self, snap: Snapshot) -> dict:
        return build_plan_request(snap, self.history, self.epoch, self.s, self.mult_logs)

    async def make_plan(self, snap: Snapshot, record: bool = True) -> dict:
        t0 = time.perf_counter()
        plan = None
        # While in fallback, probe intel only every 5 s so a stalled intel can't slow every cycle.
        probe = self.intel_enabled and (not self.fallback or time.time() - self.last_intel_try >= 5)
        if probe:
            self.last_intel_try = time.time()
            try:
                resp = await self.intel.post("/v1/plan", json=self.plan_request(snap))
                resp.raise_for_status()
                body = resp.json()
                if body.get("status") == "optimal" and isinstance(body.get("actions"), list):
                    plan = body
                    self.intel_status = "UP"
                else:
                    self.intel_status = "DEGRADED"
            except (httpx.HTTPError, ValueError):
                self.intel_status = "DOWN"
        if record and (probe or not self.intel_enabled):
            if plan is not None:
                self.intel_fail_streak, self.intel_ok_streak = 0, self.intel_ok_streak + 1
                if self.fallback and self.intel_ok_streak >= 2:
                    self.fallback = False
                    self.record("POLICY_FALLBACK", "system", "Optimizer healthy again: back to lp-v1")
                    self.resolve_incident("intel-down", "optimizer recovered")
            else:
                self.intel_ok_streak, self.intel_fail_streak = 0, self.intel_fail_streak + 1
                if not self.fallback and (self.intel_fail_streak >= 2 or not self.intel_enabled):
                    self.fallback = True
                    metrics.FALLBACK_ACTIVATIONS.inc()
                    self.record("POLICY_FALLBACK", "system", "Optimizer unavailable: switched to heuristic-v1")
                    self.open_incident("intel-down", "INTEL_DOWN", "WARNING", "Optimizer unavailable: fallback policy",
                                       "The intel service did not answer. The backup rule (heuristic-v1) is planning; "
                                       "only critical within-region top-ups run automatically.", ["intel"])
        backup = heuristic.plan(snap, self.history, self.s.horizon_ticks, self.s.safety_z, self.s.constrained_factor,
                                self.mult_logs)
        if plan is None:
            plan = backup
        else:
            plan = heuristic.gate(plan, backup, snap)
            if plan.get("gated") and record:
                metrics.LP_GATED.inc(len(plan["gated"]))
                self.loop_stats["lp_gated_cells"] = self.loop_stats.get("lp_gated_cells", 0) + len(plan["gated"])
        dt = time.perf_counter() - t0
        metrics.PLAN_SECONDS.observe(dt)
        if record:
            self.loop_stats["last_plan_ms"] = round(dt * 1000, 1)
            self.loop_stats["plans_total"] += 1
            metrics.FALLBACK_ACTIVE.set(1 if self.fallback else 0)
        return plan

    # ------------------------------------------------------------------ decide
    def pending_for(self, station_id: str, fuel: str) -> dict | None:
        for r in self.recs.values():
            if r["status"] == "PENDING" and r["station_id"] == station_id and r["fuel_type"] == fuel:
                return r
        return None

    async def apply_plan(self, plan: dict, snap: Snapshot, execute: bool = True) -> None:
        """Pending recs are kept stable and only revised on a material change; the rest run automatically."""
        async with self.exec_lock:
            for rec in autonomy.build(plan, snap, self.epoch, self.s.horizon_ticks):
                needs = autonomy.requires_approval(rec["conditions"], rec["tier"], rec["cross_region"],
                                                   self.autonomy_mode, self.mode)
                existing = self.pending_for(rec["station_id"], rec["fuel_type"])
                if needs:
                    if existing:
                        self._revise(existing, rec)
                    else:
                        rec["requires_approval"] = True
                        self._add_rec(rec)
                    continue
                if not execute:
                    continue
                if existing:
                    existing["status"] = "SUPERSEDED"
                    self.store.upsert("recommendations", existing)
                bodies = executor.prepare(snap, rec["actions"], rec["id"], self.s.constrained_factor,
                                          self.consumption(snap))
                if not bodies or not await self.tick_still_fresh(snap):
                    continue
                results = await executor.post_all(self.sim, bodies)
                self._finish(rec, results, "AUTO_EXECUTED", "auto")
                self._add_rec(rec)

    async def tick_still_fresh(self, snap: Snapshot) -> bool:
        """Don't write if the world moved 2+ ticks since the snapshot (1 tick is covered by the race margin)."""
        try:
            inst = await self.sim.request("GET", "/v1/instance")
        except SimError:
            return False
        return inst["tick"] - snap.tick < 2

    @staticmethod
    def _material_change(old: list[dict], new: list[dict]) -> bool:
        o = {(a["route_id"], a["fuel_type"]): a["quantity"] for a in old}
        n = {(a["route_id"], a["fuel_type"]): a["quantity"] for a in new}
        return o.keys() != n.keys() or any(abs(n[k] - o[k]) > max(500.0, 0.1 * o[k]) for k in o)

    def _revise(self, existing: dict, rec: dict) -> None:
        """Keep the card the operator is reading stable; bump `revision` only when the decision really changed."""
        for k in ("tier", "confidence", "confidence_label", "signals", "constraints", "conditions", "rec_class"):
            existing[k] = rec[k]
        existing["deadline_tick"] = min(existing["deadline_tick"], rec["deadline_tick"])
        if self._material_change(existing["actions"], rec["actions"]):
            for k in ("actions", "impact", "explanation", "policy"):
                existing[k] = rec[k]
            existing["revision"] += 1
        self.store.upsert("recommendations", existing)

    def _add_rec(self, rec: dict) -> None:
        self.recs[rec["id"]] = rec
        if len(self.recs) > 300:
            for key in list(self.recs)[: len(self.recs) - 300]:
                if self.recs[key]["status"] != "PENDING":
                    del self.recs[key]
        self.store.upsert("recommendations", rec)

    def _finish(self, rec: dict, results: list, ok_status: str, actor: str) -> None:
        ok = [r for r in results if r[1] is not None]
        rec["allocation_ids"] = [r[1]["id"] for r in ok]
        rec["failed_actions"] = [{"route_id": b["route_id"], "fuel_type": b["fuel_type"], "quantity": b["quantity"],
                                  "code": code, "idempotency_key": b["idempotency_key"]}
                                 for b, created, code in results if created is None]
        rec["status"] = ok_status if ok else "FAILED"
        for body, created, code in results:
            summary = (f"{int(body['quantity']):,} L {body['fuel_type'].lower()} {body['source_depot_id']} → "
                       f"{body['destination_station_id']} via {body['route_id']}")
            self.record("ALLOCATION", actor, summary if created else f"Rejected: {summary}",
                        recommendation_id=rec["id"], allocation_id=created["id"] if created else None,
                        idempotency_key=body["idempotency_key"], policy=rec["policy"],
                        outcome="OK" if created else f"REJECTED:{code}")

    def expire(self, tick: int) -> None:
        for rec in list(self.recs.values()):
            if rec["status"] == "PENDING" and rec["deadline_tick"] is not None and rec["deadline_tick"] < tick:
                rec["status"] = "EXPIRED"
                self.store.upsert("recommendations", rec)
                self.record("EXPIRY", "system", f"Recommendation for {rec['station_id']} {rec['fuel_type']} expired "
                            f"at its deadline (tick {rec['deadline_tick']})", recommendation_id=rec["id"],
                            policy=rec["policy"])

    async def approve(self, rec_id: str, operator: str, note: str, revision: int | None = None) -> dict:
        """Serialized with auto-execution. Re-validates on fresh state; never silently executes a different plan."""
        async with self.exec_lock:
            rec = self.recs.get(rec_id)
            if rec is None:
                raise EngineError(404, "NOT_FOUND", "Recommendation not found")
            if rec["status"] != "PENDING":
                raise EngineError(409, "NOT_PENDING", f"Recommendation is {rec['status']}")
            if revision is not None and revision != rec["revision"]:
                raise EngineError(409, "REVISION_CHANGED",
                                  f"The plan changed to revision {rec['revision']} (you approved {revision}); review it again")
            if self.mode == "SAFE_HOLD" or self.snap is None:
                raise EngineError(409, "SIMULATOR_UNAVAILABLE", "The simulator is unreachable; try again after recovery")
            try:
                snap = await fetch_snapshot(self.sim, None, self.regions)
            except (SimError, ValidationError, KeyError, TypeError):
                raise EngineError(409, "SIMULATOR_UNAVAILABLE", "Could not read the current state; try again")
            key = f"{rec['id']}-r{rec['revision']}"
            bodies = executor.prepare(snap, rec["actions"], key, self.s.constrained_factor, self.consumption(snap))
            if not bodies:
                raise EngineError(409, "REVALIDATION_FAILED",
                                  "The plan is no longer valid for the current state (stock, route or tank space changed)")
            planned = sum(a["quantity"] for a in rec["actions"])
            valid = executor.totals(bodies)
            if sum(valid.values()) < 0.9 * planned:
                rec["actions"] = [{**a, "quantity": valid[(a["route_id"], a["fuel_type"])]}
                                  for a in rec["actions"] if (a["route_id"], a["fuel_type"]) in valid]
                rec["revision"] += 1
                self.store.upsert("recommendations", rec)
                raise EngineError(409, "REVALIDATION_CHANGED",
                                  f"Only {sum(valid.values()):,.0f} of {planned:,.0f} L is still valid; "
                                  f"review revision {rec['revision']}")
            rec.update(status="EXECUTING", decided_by=operator, decided_at_tick=snap.tick, note=note or None)
            results = await executor.post_all(self.sim, bodies)
            self._finish(rec, results, "EXECUTED", f"operator:{operator}")
            self.record("APPROVAL", f"operator:{operator}", f"Approved {rec['rec_class'].lower()} plan "
                        f"(revision {rec['revision']}) for {rec['station_id']} {rec['fuel_type']}",
                        recommendation_id=rec["id"], policy=rec["policy"])
            self.store.upsert("recommendations", rec)
            self.dirty = True
            return rec

    def reject(self, rec_id: str, operator: str, reason: str) -> dict:
        rec = self.recs.get(rec_id)
        if rec is None:
            raise EngineError(404, "NOT_FOUND", "Recommendation not found")
        if rec["status"] != "PENDING":
            raise EngineError(409, "NOT_PENDING", f"Recommendation is {rec['status']}")
        rec.update(status="REJECTED", decided_by=operator, decided_at_tick=self.last_tick, note=reason or None)
        self.record("REJECTION", f"operator:{operator}", f"Rejected plan for {rec['station_id']} {rec['fuel_type']}: "
                    f"{reason or 'no reason given'}", recommendation_id=rec["id"], policy=rec["policy"])
        self.store.upsert("recommendations", rec)
        return rec

    # ---------------------------------------------------------------- metrics
    def risk_rows(self) -> list[dict]:
        return (self.plan or {}).get("risk", [])

    def update_metrics(self, snap: Snapshot, plan: dict) -> None:
        metrics.SIM_TICK.set(snap.tick)
        metrics.TICK_LAG.set(self.loop_stats["tick_lag"])
        metrics.SERVICE_LEVEL.set(snap.metrics.service_level)
        metrics.UNMET.set(snap.metrics.unmet_demand_liters)
        for cause, litres in self.fuel_lost.items():
            metrics.FUEL_LOST.labels(cause).set(litres)
        metrics.IN_TRANSIT.set(sum(in_transit(snap).values()))
        at_risk, critical = set(), defaultdict(list)
        for r in plan.get("risk", []):
            metrics.STOCKOUT_P.labels(r["station_id"], r["fuel_type"]).set(r["p_stockout_8h"])
            metrics.HOURS_TO_STOCKOUT.labels(r["station_id"], r["fuel_type"]).set(
                r["hours_to_stockout"] if r["hours_to_stockout"] is not None else 48)
            if r["tier"] in ("HIGH", "CRITICAL"):
                at_risk.add(r["station_id"])
            if r["tier"] == "CRITICAL":
                critical[r["station_id"]].append(r)
        for s in snap.stations:
            key = f"risk-{s.id}"
            rows = critical.get(s.id)
            if rows:
                fuels = ", ".join(f"{x['fuel_type'].lower()} {round(x['p_stockout_8h'] * 100)}%" for x in rows)
                self.open_incident(key, "STOCKOUT_RISK", "CRITICAL", f"Stockout risk at {s.name}",
                                   f"P(stockout within 8 h): {fuels}.", [s.id])
            elif s.id not in at_risk:
                self.resolve_incident(key, "risk back to normal")
        metrics.STATIONS_AT_RISK.set(len(at_risk))
        metrics.RECS_PENDING.set(sum(1 for r in self.recs.values() if r["status"] == "PENDING"))
        metrics.INCIDENTS_OPEN.set(len(self.open_keys))
        wape = (plan.get("forecast") or {}).get("wape")
        if wape is not None:
            metrics.FORECAST_WAPE.set(wape)

    def depot_cover(self) -> dict:
        return days_of_cover(self.snap, self.history) if self.snap else {}
