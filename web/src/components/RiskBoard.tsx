// Risk board: every station/fuel row from state.risk, sorted worst-first.
// The p_stockout_8h column is deliberately never labelled "probability" --
// it is a model estimate, not a guarantee.

import type { RiskRow, RiskTier } from "../types";
import { fmtHours, fmtLiters, fmtPct, idToLabel } from "../lib/format";
import { ConfidenceChip, TierChip } from "./chips";

const TIER_ORDER: Record<RiskTier, number> = { CRITICAL: 0, HIGH: 1, WATCH: 2, OK: 3 };

export function RiskBoard({ risk }: { risk: RiskRow[] }) {
  const sorted = [...risk].sort((a, b) => (TIER_ORDER[a.tier] ?? 4) - (TIER_ORDER[b.tier] ?? 4));

  return (
    <section className="panel m-3 p-4">
      <h2 className="text-sm text-muted uppercase tracking-wide mt-0 mb-3">Risk board</h2>
      <div className="scrollbox">
        <table>
          <thead>
            <tr>
              <th>Station</th>
              <th>Fuel</th>
              <th>Inventory</th>
              <th>Hours to stockout</th>
              <th>Stockout risk (est., 8 h)</th>
              <th>Tier</th>
              <th>Confidence</th>
              <th>Reasons</th>
              <th>Unavoidable</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={9} className="text-muted">
                  no risk data yet
                </td>
              </tr>
            )}
            {sorted.map((r, i) => (
              <tr key={`${r.station_id}-${r.fuel_type}-${i}`}>
                <td>{idToLabel(r.station_id)}</td>
                <td>{r.fuel_type}</td>
                <td className="tabular">
                  {fmtLiters(r.inventory)} / {fmtLiters(r.capacity)}
                </td>
                <td className="tabular">{fmtHours(r.hours_to_stockout)}</td>
                <td className="tabular">{fmtPct(r.p_stockout_8h)}</td>
                <td>
                  <TierChip tier={r.tier} />
                </td>
                <td>
                  <ConfidenceChip label={r.confidence_label} value={r.confidence} />
                </td>
                <td className="text-secondary">{r.reasons?.length ? r.reasons.join("; ") : "—"}</td>
                <td>
                  {r.unavoidable ? (
                    <span className="chip chip-serious">yes</span>
                  ) : (
                    <span className="text-muted">no</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
