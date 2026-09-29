"""Fair policy benchmark on the lab simulator (same seed, same events, same code path as the live loop).

Evidence class: a benchmark counterfactual. Each policy is AUTO-EXECUTED (no human approvals), so this measures
the policy, not the supervised system. Scenario events are injected AT their start tick, so no policy gets
foreknowledge of a "surprise". Outcomes come from the simulator itself (/v1/metrics, audit log).

Usage (inside the backend container):
    python -m app.benchmark --sim http://sim-lab:8000 --ticks 288 --scenario baseline|crisis
"""
import argparse
import asyncio
import json
import time
from collections import defaultdict

import httpx

from . import executor, heuristic
from .config import settings
from .loop import build_plan_request
from .models import DemandRow
from .simclient import SimClient
from .state import consumption_fn, fetch_snapshot

IMAGE = "asifmahmoud414/bup-fuel-supply-simulator:1.0.0"

SCENARIOS = {
    "baseline": [],
    "crisis": [
        {"type": "demand_spike", "start_tick": 20, "duration_ticks": 24,
         "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}},
        {"type": "route_disruption", "start_tick": 40, "duration_ticks": 16,
         "parameters": {"route_ids": ["route-gazipur-tongi"]}},
        {"type": "shipment_delay", "start_tick": 50, "duration_ticks": 4,
         "parameters": {"delay_ticks": 8, "depot_ids": ["depot-gazipur"], "fuel_types": ["DIESEL"]}},
    ],
}


async def run_policy(policy: str, sim_url: str, intel_url: str, ticks: int, scenario: str) -> dict:
    sim = SimClient(sim_url, use_breaker=False, retries=1)
    intel = httpx.AsyncClient(base_url=intel_url, timeout=5.0)
    await sim.request("POST", "/admin/pause")
    await sim.request("POST", "/admin/reset")
    await sim.request("POST", "/admin/pause")
    instance = await sim.request("GET", "/v1/instance")
    regions = await sim.request("GET", "/v1/regions")
    history: dict = defaultdict(list)
    mult_logs: dict = defaultdict(list)
    lost = {"depot_overflow": 0.0, "station_overflow": 0.0, "failed_shipment": 0.0}
    cursor, intel_errors, posted, gated_cells, t0 = 0, 0, 0, 0, time.time()
    for tick in range(ticks):
        for ev in SCENARIOS[scenario]:
            if ev["start_tick"] == tick:  # revealed only when it happens
                await sim.request("POST", "/admin/events", json=ev)
        snap = await fetch_snapshot(sim, None, regions, verify=False)
        for st in snap.stations:
            if not mult_logs[st.id] or mult_logs[st.id][-1][1] != st.demand_multiplier:
                mult_logs[st.id].append((snap.tick, st.demand_multiplier))
        rows = await sim.request("GET", "/v1/demand-history?limit=24")
        for r in sorted((DemandRow(**x) for x in rows), key=lambda r: r.tick):
            series = history[f"{r.station_id}|{r.fuel_type}"]
            if not series or series[-1][0] < r.tick:
                series.append((r.tick, r.demand_liters))
                del series[:-192]
        if policy != "none":
            for alloc_id in executor.guard_targets(snap):
                await sim.request("POST", f"/v1/allocations/{alloc_id}/cancel")
            plan = None
            if policy == "lp":
                try:
                    resp = await intel.post("/v1/plan", json=build_plan_request(snap, history, 1, settings, mult_logs))
                    body = resp.json()
                    plan = body if body.get("status") == "optimal" else None
                except (httpx.HTTPError, ValueError):
                    plan = None
                intel_errors += plan is None
            backup = heuristic.plan(snap, history, settings.horizon_ticks, settings.safety_z,
                                    settings.constrained_factor, mult_logs)
            if plan is None:
                plan = backup
            else:  # the same gate as the live loop, so the benchmark measures what actually runs
                plan = heuristic.gate(plan, backup, snap)
                gated_cells += len(plan.get("gated", []))
            # race_margin=0: the lab world is paused and stepped, so the tick can't move between read and write
            bodies = executor.prepare(snap, plan["actions"], f"b{tick}", settings.constrained_factor,
                                      consumption_fn(snap, history, mult_logs), race_margin=0)
            results = await executor.post_all(sim, bodies)
            posted += sum(1 for r in results if r[1] is not None)
        audit = await sim.request("GET", "/admin/audit?limit=100")
        supply = {s.id: s.quantity for s in snap.supply}
        allocs = {str(a.id): a.quantity for a in snap.allocations}
        for row in sorted((r for r in audit if r["id"] > cursor), key=lambda r: r["id"]):
            cursor = row["id"]
            meta = row.get("metadata_json") or {}
            if row["action"] == "supply.arrived" and row["entity_id"] in supply:
                lost["depot_overflow"] += max(0.0, supply[row["entity_id"]] - float(meta.get("added", 0)))
            if row["action"] == "allocation.arrived" and row["entity_id"] in allocs:
                lost["station_overflow"] += max(0.0, allocs[row["entity_id"]] - float(meta.get("received", 0)))
        await sim.request("POST", "/admin/step")
    final = await fetch_snapshot(sim, None, regions, verify=False)
    lost["failed_shipment"] = sum(a.quantity for a in final.allocations if a.status == "FAILED")
    m = final.metrics
    await sim.close()
    await intel.aclose()
    return {"policy": policy, "scenario": scenario, "ticks": ticks, "service_level": round(m.service_level, 4),
            "served_liters": round(m.served_demand_liters), "unmet_liters": round(m.unmet_demand_liters),
            "fuel_lost_liters": {k: round(v) for k, v in lost.items()},
            "allocations": posted, "allocation_failures": m.allocation_failures, "intel_errors": intel_errors, "lp_gated_cells": gated_cells,
            "runtime_s": round(time.time() - t0, 1),
            "protocol": {"image": IMAGE, "seed": instance.get("seed"), "scenario_id": instance.get("scenario_id"),
                         "tick_minutes": instance.get("tick_minutes"), "git_sha": settings.git_sha,
                         "events": SCENARIOS[scenario], "events_injected": "at start tick (no foreknowledge)",
                         "execution": "policy auto-executed (no human approvals)",
                         "policy_config": {"horizon_ticks": settings.horizon_ticks, "safety_z": settings.safety_z,
                                           "constrained_factor": settings.constrained_factor}}}


def table(rows: list[dict]) -> str:
    out = ["| Policy | Service level | Served L | Unmet L | Fuel lost L (depot / station / failed) | Allocations | Runtime |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        fl = r["fuel_lost_liters"]
        out.append(f"| {r['policy']} | {r['service_level'] * 100:.1f}% | {r['served_liters']:,} | {r['unmet_liters']:,} | "
                   f"{fl['depot_overflow']:,} / {fl['station_overflow']:,} / {fl['failed_shipment']:,} | "
                   f"{r['allocations']} | {r['runtime_s']} s |")
    return "\n".join(out)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", default=settings.sim_lab_url)
    ap.add_argument("--intel", default=settings.intel_url)
    ap.add_argument("--ticks", type=int, default=288)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="baseline")
    ap.add_argument("--policies", default="none,heuristic,lp")
    args = ap.parse_args()
    rows = [await run_policy(p, args.sim, args.intel, args.ticks, args.scenario) for p in args.policies.split(",")]
    proto = rows[0]["protocol"] if rows else {}
    print(f"\n### Scenario: {args.scenario} · {args.ticks} ticks · seed {proto.get('seed')} · {IMAGE} · "
          f"git {settings.git_sha} · policies auto-executed, events revealed at their start tick\n")
    print(table(rows))
    print("\n" + json.dumps(rows, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
