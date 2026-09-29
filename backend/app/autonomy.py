"""Builds recommendations from a plan and decides which ones need a human (Final-Project §4.7)."""
import math

from . import explain
from .models import Snapshot

MODES = ("ADVISORY", "SUPERVISED", "AUTOPILOT")


# Conditions that make a recommendation consequential, most restrictive first (also the display order).
CONDITIONS = ("RATIONING", "LOW_CONFIDENCE", "CROSS_REGION", "FALLBACK")

# The one authoritative permission table (implementation plan §12.5). True = a human must approve.
# None = automatic only for a CRITICAL within-region top-up.
NEEDS_HUMAN = {
    "ROUTINE":        {"ADVISORY": True, "SUPERVISED": False, "AUTOPILOT": False},
    "CROSS_REGION":   {"ADVISORY": True, "SUPERVISED": True,  "AUTOPILOT": False},
    "FALLBACK":       {"ADVISORY": True, "SUPERVISED": None,  "AUTOPILOT": False},
    "LOW_CONFIDENCE": {"ADVISORY": True, "SUPERVISED": True,  "AUTOPILOT": True},
    "RATIONING":      {"ADVISORY": True, "SUPERVISED": True,  "AUTOPILOT": True},
}


def conditions(policy: str, cross_region: bool, confidence_label: str, stale: bool, rationing: bool) -> list[str]:
    """Every condition that applies. Stale data counts as low confidence."""
    found = {"RATIONING": rationing, "LOW_CONFIDENCE": confidence_label == "LOW" or stale,
             "CROSS_REGION": cross_region, "FALLBACK": policy.startswith("heuristic")}
    return [c for c in CONDITIONS if found[c]]


def display_class(conds: list[str]) -> str:
    return conds[0] if conds else "ROUTINE"


def requires_approval(conds: list[str], tier: str, cross_region: bool, autonomy_mode: str,
                      operating_mode: str) -> bool:
    """Hard limits first, then every applicable condition: the most restrictive requirement wins."""
    if operating_mode == "SAFE_HOLD":
        return True
    if operating_mode == "DEGRADED" and (set(conds) - {"FALLBACK"} or tier != "CRITICAL" or cross_region):
        return True
    for c in conds or ["ROUTINE"]:
        rule = NEEDS_HUMAN[c][autonomy_mode]
        if rule is None:
            rule = not (tier == "CRITICAL" and not cross_region)
        if rule:
            return True
    return False


def build(plan: dict, snap: Snapshot, epoch: int, horizon: int) -> list[dict]:
    tick, tm = snap.tick, snap.instance.tick_minutes
    tph = 60 / tm
    routes = {r.id: r for r in snap.routes}
    depots = {d.id: d for d in snap.depots}
    stations = {s.id: s for s in snap.stations}
    names = {**{d.id: d.name for d in snap.depots}, **{s.id: s.name for s in snap.stations}}
    risk = {(r["station_id"], r["fuel_type"]): r for r in plan.get("risk", [])}
    impact = {(i["station_id"], i["fuel_type"]): i for i in plan.get("impact", [])}
    binding = {(b["station_id"], b["fuel_type"]): b.get("constraints", []) for b in plan.get("binding", [])}
    supply_left = any(s.status != "ARRIVED" and s.planned_tick <= tick + horizon for s in snap.supply)
    groups: dict[tuple[str, str], list[dict]] = {}
    for a in plan.get("actions", []):
        r = routes.get(a["route_id"])
        if r is None or a.get("quantity", 0) < 100:
            continue
        groups.setdefault((r.destination_station_id, a["fuel_type"]), []).append(
            {"route_id": r.id, "source_depot_id": r.source_depot_id, "destination_station_id": r.destination_station_id,
             "fuel_type": a["fuel_type"], "quantity": float(a["quantity"]), "transit_ticks": r.transit_ticks,
             "eta_tick": tick + r.transit_ticks})
    recs = []
    for (sid, fuel), acts in groups.items():
        st = stations.get(sid)
        if st is None:
            continue
        rk = risk.get((sid, fuel), {})
        imp = dict(impact.get((sid, fuel), {}))
        imp.setdefault("p_stockout_before", rk.get("p_stockout_8h"))
        imp.setdefault("hours_to_stockout_before", rk.get("hours_to_stockout"))
        for k in ("p_stockout_after", "unmet_before_liters", "unmet_after_liters", "hours_to_stockout_after"):
            imp.setdefault(k, None)
        imp.pop("cross_region", None)
        cross = any(depots[a["source_depot_id"]].region_id != st.region_id for a in acts)
        rationing = not supply_left and (imp.get("unmet_after_liters") or 0) > 0
        conds = conditions(plan.get("policy", "lp-v1"), cross, rk.get("confidence_label", "MEDIUM"),
                           snap.stale, rationing)
        tier = rk.get("tier", "WATCH")
        hts = rk.get("hours_to_stockout")
        transit = max(a["transit_ticks"] for a in acts)
        if hts is None:
            deadline = tick + 8
        else:
            deadline = max(tick + 1, tick + math.ceil(hts * tph) - transit)
        signals = list(rk.get("reasons", []))
        if st.demand_multiplier != 1 and not any("demand x" in x for x in signals):
            signals.append(f"demand x{st.demand_multiplier:g}")
        for a in acts:
            d = depots[a["source_depot_id"]]
            if d.status == "CONSTRAINED":
                signals.append(f"{d.name} constrained")
        rec = {"id": f"rec-{epoch}-{tick}-{sid}-{fuel}", "epoch": epoch, "created_tick": tick, "deadline_tick": deadline,
               "status": "PENDING", "rec_class": display_class(conds), "conditions": conds, "revision": 1,
               "requires_approval": False, "station_id": sid,
               "fuel_type": fuel, "tier": tier, "actions": acts, "impact": imp,
               "confidence": rk.get("confidence", 0.6), "confidence_label": rk.get("confidence_label", "MEDIUM"),
               "signals": signals, "constraints": binding.get((sid, fuel), []), "explanation": "",
               "policy": plan.get("policy", "lp-v1"), "decided_by": None, "decided_at_tick": None, "note": None,
               "allocation_ids": [], "failed_actions": [], "cross_region": cross}
        rec["explanation"] = explain.recommendation(rec, names)
        recs.append(rec)
    return recs
