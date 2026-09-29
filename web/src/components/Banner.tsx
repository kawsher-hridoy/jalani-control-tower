import type { OperatingMode } from "../types";
import { Chip } from "./chips";

export function Banner({
  backendUnreachable,
  operatingMode,
  worldPaused,
}: {
  backendUnreachable: boolean;
  operatingMode: OperatingMode | undefined;
  worldPaused: boolean;
}) {
  if (!backendUnreachable && !worldPaused && (!operatingMode || operatingMode === "NORMAL")) return null;

  return (
    <div className="mx-3 mb-3 flex flex-col gap-2">
      {backendUnreachable && (
        <div className="panel border-l-4 px-4 py-2 text-sm" style={{ borderLeftColor: "var(--status-critical)" }}>
          <strong>Backend unreachable</strong> — showing last known data. Retrying in the background; nothing is
          being sent to the simulator from this console right now.
        </div>
      )}
      {worldPaused && (
        <div className="panel border-l-4 px-4 py-2 text-sm flex items-center gap-3" style={{ borderLeftColor: "var(--status-warning)" }}>
          <Chip tone="warning">PAUSED</Chip>
          <span>World paused: time is frozen, deadlines too.</span>
        </div>
      )}
      {operatingMode && operatingMode !== "NORMAL" && (
        <div className="panel border-l-4 px-4 py-2 text-sm flex items-center gap-3" style={{ borderLeftColor: "var(--status-warning)" }}>
          <Chip tone={operatingMode === "SAFE_HOLD" ? "critical" : "warning"}>{operatingMode}</Chip>
          {operatingMode === "DEGRADED" && (
            <span>Routine automation is limited — most recommendations wait for human review until data freshens up.</span>
          )}
          {operatingMode === "SAFE_HOLD" && (
            <span>No shipments are being sent until the simulator recovers. All actions are on hold.</span>
          )}
        </div>
      )}
    </div>
  );
}
