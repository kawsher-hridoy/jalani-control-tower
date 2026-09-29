// Small, reusable status/identity chips shared across panels. Every chip
// always renders a text label -- color alone never carries meaning here
// (color-blind-safe by construction).

import type { ReactNode } from "react";
import type {
  CircuitState,
  ComponentStatus,
  ConfidenceLabel,
  DepotStatus,
  Flag,
  IncidentSeverity,
  OperatingMode,
  RecClass,
  RecStatus,
  RiskTier,
  RouteStatus,
  StationStatus,
} from "../types";

export type ChipTone =
  | "good"
  | "warning"
  | "serious"
  | "critical"
  | "neutral"
  | "cat-1"
  | "cat-2"
  | "cat-3"
  | "cat-4"
  | "cat-5";

export function Chip({
  tone,
  children,
  title,
}: {
  tone: ChipTone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function tierTone(tier: RiskTier | undefined | null): ChipTone {
  switch (tier) {
    case "CRITICAL":
      return "critical";
    case "HIGH":
      return "serious";
    case "WATCH":
      return "warning";
    case "OK":
      return "good";
    default:
      return "neutral";
  }
}
export function TierChip({ tier }: { tier: RiskTier | undefined | null }) {
  return <Chip tone={tierTone(tier)}>{tier ?? "—"}</Chip>;
}

export function modeTone(mode: OperatingMode | undefined | null): ChipTone {
  switch (mode) {
    case "NORMAL":
      return "good";
    case "DEGRADED":
      return "warning";
    case "SAFE_HOLD":
      return "critical";
    default:
      return "neutral";
  }
}
export function ModePill({ mode }: { mode: OperatingMode | undefined | null }) {
  return <Chip tone={modeTone(mode)}>{mode ?? "UNKNOWN"}</Chip>;
}

export function componentTone(s: ComponentStatus | undefined | null): ChipTone {
  switch (s) {
    case "UP":
      return "good";
    case "DEGRADED":
      return "warning";
    case "DOWN":
      return "critical";
    default:
      return "neutral";
  }
}
export function ComponentChip({ status }: { status: ComponentStatus | undefined | null }) {
  return <Chip tone={componentTone(status)}>{status ?? "—"}</Chip>;
}

export function circuitTone(c: CircuitState | undefined | null): ChipTone {
  switch (c) {
    case "CLOSED":
      return "good";
    case "HALF_OPEN":
      return "warning";
    case "OPEN":
      return "critical";
    default:
      return "neutral";
  }
}
export function CircuitChip({ state }: { state: CircuitState | undefined | null }) {
  return <Chip tone={circuitTone(state)}>{state ?? "—"}</Chip>;
}

export function depotStatusTone(s: DepotStatus | undefined | null): ChipTone {
  if (s === "CONSTRAINED") return "warning";
  if (s === "OPEN") return "good";
  return "neutral";
}
export function DepotStatusChip({ status }: { status: DepotStatus | undefined | null }) {
  return <Chip tone={depotStatusTone(status)}>{status ?? "—"}</Chip>;
}

export function stationStatusTone(s: StationStatus | undefined | null): ChipTone {
  if (s === "OUTAGE") return "critical";
  if (s === "OPEN") return "good";
  return "neutral";
}
export function StationStatusChip({ status }: { status: StationStatus | undefined | null }) {
  return <Chip tone={stationStatusTone(status)}>{status ?? "—"}</Chip>;
}

export function routeStatusTone(s: RouteStatus | undefined | null): ChipTone {
  if (s === "DISRUPTED") return "critical";
  if (s === "AVAILABLE") return "good";
  return "neutral";
}
export function RouteStatusChip({ status }: { status: RouteStatus | undefined | null }) {
  return <Chip tone={routeStatusTone(status)}>{status ?? "—"}</Chip>;
}

export function recStatusTone(s: RecStatus | undefined | null): ChipTone {
  switch (s) {
    case "PENDING":
    case "EXECUTING":
      return "warning";
    case "APPROVED":
    case "EXECUTED":
    case "AUTO_EXECUTED":
      return "good";
    case "REJECTED":
    case "FAILED":
      return "critical";
    case "EXPIRED":
    case "SUPERSEDED":
      return "neutral";
    default:
      return "neutral";
  }
}
export function RecStatusChip({ status }: { status: RecStatus | undefined | null }) {
  return <Chip tone={recStatusTone(status)}>{status ?? "—"}</Chip>;
}

const classToneOrder: Record<RecClass, ChipTone> = {
  ROUTINE: "cat-1",
  CROSS_REGION: "cat-2",
  RATIONING: "cat-3",
  LOW_CONFIDENCE: "cat-4",
  FALLBACK: "cat-5",
};
export function RecClassChip({ recClass }: { recClass: RecClass | undefined | null }) {
  if (!recClass) return <Chip tone="neutral">—</Chip>;
  return <Chip tone={classToneOrder[recClass] ?? "neutral"}>{recClass.replace(/_/g, " ")}</Chip>;
}

export function confidenceTone(label: ConfidenceLabel | undefined | null): ChipTone {
  switch (label) {
    case "HIGH":
      return "good";
    case "MEDIUM":
      return "warning";
    case "LOW":
      return "serious";
    default:
      return "neutral";
  }
}
export function ConfidenceChip({
  label,
  value,
}: {
  label: ConfidenceLabel | undefined | null;
  value?: number | null;
}) {
  return (
    <Chip tone={confidenceTone(label)}>
      {label ?? "—"}
      {typeof value === "number" ? ` (${(value * 100).toFixed(0)}%)` : ""}
    </Chip>
  );
}

export function flagLabel(flag: Flag): string {
  return flag.replace(/_/g, " ");
}
export function FlagChip({ flag }: { flag: Flag }) {
  return (
    <Chip tone="warning" title={flag}>
      {flagLabel(flag)}
    </Chip>
  );
}

export function severityTone(sev: IncidentSeverity | undefined | null): ChipTone {
  switch (sev) {
    case "CRITICAL":
      return "critical";
    case "WARNING":
      return "warning";
    case "INFO":
      return "neutral";
    default:
      return "neutral";
  }
}
export function SeverityChip({ severity }: { severity: IncidentSeverity | undefined | null }) {
  return <Chip tone={severityTone(severity)}>{severity ?? "—"}</Chip>;
}
