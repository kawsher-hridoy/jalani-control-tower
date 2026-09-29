import type { Kpis } from "../types";
import { fmtLiters, fmtNum, fmtPct } from "../lib/format";

function Tile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "warning" | "critical" | undefined;
}) {
  const valueStyle =
    tone === "critical"
      ? { color: "var(--status-critical)" }
      : tone === "warning"
        ? { color: "var(--status-warning)" }
        : undefined;
  return (
    <div className="panel px-4 py-3 min-w-[140px] flex-1">
      <div className="text-muted text-xs uppercase tracking-wide">{label}</div>
      <div className="tabular text-2xl font-semibold mt-1" style={valueStyle}>
        {value}
      </div>
      {sub && <div className="text-muted text-xs mt-1">{sub}</div>}
    </div>
  );
}

export function KpiStrip({ kpis }: { kpis: Kpis | undefined }) {
  const stationsAtRisk = kpis?.stations_at_risk ?? 0;
  const pending = kpis?.pending_approvals ?? 0;
  const incidents = kpis?.open_incidents ?? 0;
  const failures = kpis?.allocation_failures ?? 0;

  return (
    <div className="mx-3 mb-3 flex flex-wrap gap-3">
      <Tile label="Service level" value={fmtPct(kpis?.service_level, 1)} sub={`served ${fmtLiters(kpis?.served_liters)}`} />
      <Tile label="Unmet demand" value={fmtLiters(kpis?.unmet_liters)} tone={kpis && kpis.unmet_liters > 0 ? "warning" : undefined} />
      <Tile
        label="Fuel lost"
        value={fmtLiters(kpis?.fuel_lost_liters)}
        tone={kpis && kpis.fuel_lost_liters > 0 ? "critical" : undefined}
        sub={
          kpis
            ? `depot ${fmtNum(kpis.fuel_lost.depot_overflow)} · station ${fmtNum(kpis.fuel_lost.station_overflow)} · failed ${fmtNum(kpis.fuel_lost.failed_shipment)}`
            : undefined
        }
      />
      <Tile label="In transit" value={fmtLiters(kpis?.in_transit_liters)} />
      <Tile label="Stations at risk" value={fmtNum(stationsAtRisk)} tone={stationsAtRisk > 0 ? "warning" : undefined} />
      <Tile label="Pending approvals" value={fmtNum(pending)} tone={pending > 0 ? "warning" : undefined} />
      <Tile label="Open incidents" value={fmtNum(incidents)} tone={incidents > 0 ? "warning" : undefined} />
      <Tile label="Allocation failures" value={fmtNum(failures)} tone={failures > 0 ? "critical" : undefined} />
    </div>
  );
}
