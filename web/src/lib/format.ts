// Small, defensive formatting helpers. Every function tolerates null/undefined
// so components never need to guard before calling them.

export function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  });
}

export function fmtLiters(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "— L";
  return `${fmtNum(n, digits)} L`;
}

export function fmtPct(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

export function fmtHours(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "no stockout projected";
  return `${n.toFixed(1)} h`;
}

export function fmtMs(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n.toFixed(1)} ms`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return "—";
  return s
    .toLowerCase()
    .split(/[_\s]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

export function idToLabel(id: string | null | undefined): string {
  if (!id) return "—";
  const parts = id.split("-");
  // Drop a leading type prefix like "station-" / "depot-" / "route-" when present.
  const rest = parts.length > 1 ? parts.slice(1) : parts;
  return rest
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}
