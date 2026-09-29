"""Plain-language explanations. Templates always work; GPT is optional and never on the decision path."""


def _pct(p) -> str:
    return "n/a" if p is None else f"{round(p * 100)}%"


def _h(h) -> str:
    return "not within the horizon" if h is None else f"{h:g} h"


def recommendation(rec: dict, names: dict[str, str]) -> str:
    st = names.get(rec["station_id"], rec["station_id"])
    parts = [f"{st} {rec['fuel_type'].lower()} is {rec['tier']}: stockout in "
             f"{_h(rec['impact'].get('hours_to_stockout_before'))}, "
             f"P(stockout within 8 h) {_pct(rec['impact'].get('p_stockout_before'))}."]
    moves = [f"{int(a['quantity']):,} L from {names.get(a['source_depot_id'], a['source_depot_id'])} "
             f"(arrives tick {a['eta_tick']})" for a in rec["actions"]]
    if moves:
        parts.append("Plan: send " + " and ".join(moves) + ".")
    imp = rec["impact"]
    parts.append(f"Expected: P(stockout) {_pct(imp.get('p_stockout_before'))} → {_pct(imp.get('p_stockout_after'))}, "
                 f"unmet {int(imp.get('unmet_before_liters') or 0):,} → {int(imp.get('unmet_after_liters') or 0):,} L.")
    why = {"CROSS_REGION": "It moves fuel between regions, so an operator must approve it.",
           "RATIONING": "Supply is running out, so this is a rationing decision for an operator.",
           "LOW_CONFIDENCE": "Confidence is low or the data is stale, so an operator must review it.",
           "FALLBACK": "The optimizer is unavailable; this comes from the backup rule."}.get(rec["rec_class"])
    if why:
        parts.append(why)
    return " ".join(parts)
