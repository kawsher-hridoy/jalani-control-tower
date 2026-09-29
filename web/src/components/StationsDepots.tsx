// Stations & depots panel: plain CSS grid cards (no SVG map), plus a compact
// routes table. Every fuel lookup tolerates a missing key or a missing map.

import type { Depot, FuelMap, FuelType, Route, Station } from "../types";
import { fmtNum, idToLabel } from "../lib/format";
import { DepotStatusChip, RouteStatusChip, StationStatusChip } from "./chips";

const FUELS: FuelType[] = ["DIESEL", "PETROL", "OCTANE"];

function fuelMapGet(m: FuelMap | undefined | null, fuel: FuelType): number {
  const v = m?.[fuel];
  return typeof v === "number" ? v : 0;
}

function pct(inventory: number, capacity: number): number {
  if (!capacity || capacity <= 0) return 0;
  return Math.max(0, Math.min(100, (inventory / capacity) * 100));
}

function barTone(p: number): string {
  if (p < 15) return "var(--status-critical)";
  if (p < 35) return "var(--status-warning)";
  return "var(--status-good)";
}

function TankBar({
  fuel,
  inventory,
  capacity,
  inTransit,
}: {
  fuel: FuelType;
  inventory: number;
  capacity: number;
  inTransit: number;
}) {
  const p = pct(inventory, capacity);
  return (
    <div className="mb-2">
      <div className="flex justify-between text-xs text-muted mb-0.5">
        <span>{fuel}</span>
        <span className="tabular">
          {fmtNum(inventory)} / {fmtNum(capacity)} L{inTransit > 0 ? ` (+${fmtNum(inTransit)} in transit)` : ""}
        </span>
      </div>
      <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--gridline)" }}>
        <div className="h-full" style={{ width: `${p}%`, background: barTone(p) }} />
      </div>
    </div>
  );
}

function StationCard({ station }: { station: Station }) {
  return (
    <div className="panel p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <strong className="text-sm">{station.name || idToLabel(station.id)}</strong>
        <StationStatusChip status={station.status} />
      </div>
      <div className="text-xs text-muted mb-2">
        {station.demand_profile || "unknown profile"} · demand x{fmtNum(station.demand_multiplier ?? 1, 2)}
      </div>
      {FUELS.map((f) => (
        <TankBar
          key={f}
          fuel={f}
          inventory={fuelMapGet(station.inventory, f)}
          capacity={fuelMapGet(station.capacity, f)}
          inTransit={fuelMapGet(station.in_transit, f)}
        />
      ))}
    </div>
  );
}

function DepotCard({ depot }: { depot: Depot }) {
  return (
    <div className="panel p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <strong className="text-sm">{depot.name || idToLabel(depot.id)}</strong>
        <DepotStatusChip status={depot.status} />
      </div>
      <div className="text-xs text-muted mb-2">dispatch cap {fmtNum(depot.dispatch_capacity_per_tick)} L/tick</div>
      {FUELS.map((f) => (
        <div key={f} className="flex justify-between text-xs mb-1.5">
          <span className="text-muted">{f}</span>
          <span className="tabular">
            {fmtNum(fuelMapGet(depot.inventory, f))} / {fmtNum(fuelMapGet(depot.capacity, f))} L ·{" "}
            {fmtNum(depot.days_of_cover?.[f], 1)}d cover
          </span>
        </div>
      ))}
    </div>
  );
}

function routeLabel(route: Route): string {
  return `${idToLabel(route.source_depot_id)} → ${idToLabel(route.destination_station_id)}`;
}

function RoutesTable({ routes }: { routes: Route[] }) {
  return (
    <div className="scrollbox">
      <table>
        <thead>
          <tr>
            <th>Route</th>
            <th>Status</th>
            <th>Transit</th>
            <th>Max shipment</th>
            <th>Next disruption</th>
          </tr>
        </thead>
        <tbody>
          {routes.length === 0 && (
            <tr>
              <td colSpan={5} className="text-muted">
                no routes yet
              </td>
            </tr>
          )}
          {routes.map((r) => (
            <tr key={r.id}>
              <td>{routeLabel(r)}</td>
              <td>
                <RouteStatusChip status={r.status} />
              </td>
              <td className="tabular">{r.transit_ticks} ticks</td>
              <td className="tabular">{fmtNum(r.max_shipment)} L</td>
              <td className="tabular text-muted">
                {r.next_disruption ? `tick ${r.next_disruption.start_tick}–${r.next_disruption.end_tick}` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StationsDepotsPanel({
  stations,
  depots,
  routes,
}: {
  stations: Station[];
  depots: Depot[];
  routes: Route[];
}) {
  return (
    <section className="panel m-3 p-4">
      <h2 className="text-sm text-muted uppercase tracking-wide mt-0 mb-3">Stations &amp; depots</h2>
      <div className="grid gap-3 mb-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))" }}>
        {stations.map((s) => (
          <StationCard key={s.id} station={s} />
        ))}
        {stations.length === 0 && <div className="text-muted text-sm">no stations yet</div>}
      </div>
      <div className="grid gap-3 mb-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))" }}>
        {depots.map((d) => (
          <DepotCard key={d.id} depot={d} />
        ))}
        {depots.length === 0 && <div className="text-muted text-sm">no depots yet</div>}
      </div>
      <h3 className="text-xs text-muted uppercase tracking-wide mb-2">Routes</h3>
      <RoutesTable routes={routes} />
    </section>
  );
}
