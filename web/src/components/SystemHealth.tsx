// System health: component list, circuit breaker, simulator latency/error
// rate, control-loop timing, readiness, build info, and links out to
// Grafana / Prometheus (same host, fixed ports per docker-compose.yml).

import type { StatusResponse } from "../types";
import { fmtMs, fmtPct } from "../lib/format";
import { CircuitChip, Chip, ComponentChip } from "./chips";

function observabilityHost(): string {
  try {
    return window.location.hostname || "localhost";
  } catch {
    return "localhost";
  }
}

export function SystemHealthPanel({ status }: { status: StatusResponse | undefined }) {
  const host = observabilityHost();
  const grafanaUrl = `http://${host}:3000`;
  const prometheusUrl = `http://${host}:9090`;

  return (
    <section className="panel p-4 flex-1 min-w-[320px]">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm text-muted uppercase tracking-wide m-0">System health</h2>
        <div className="flex gap-2">
          <a className="btn" href={grafanaUrl} target="_blank" rel="noreferrer">
            Grafana
          </a>
          <a className="btn" href={prometheusUrl} target="_blank" rel="noreferrer">
            Prometheus
          </a>
        </div>
      </div>

      {!status ? (
        <div className="text-muted text-sm">no status data yet</div>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5 mb-3">
            {(status.components ?? []).map((c) => (
              <span key={c.name} className="flex items-center gap-1.5 text-xs">
                {c.name}
                <ComponentChip status={c.status} />
              </span>
            ))}
            {(status.components ?? []).length === 0 && <span className="text-muted text-xs">no components reported</span>}
          </div>

          <div className="grid gap-2 text-xs mb-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
            <div>
              <div className="text-muted">Sim circuit</div>
              <CircuitChip state={status.sim_client?.circuit} />
            </div>
            <div>
              <div className="text-muted">Sim p95</div>
              <div className="tabular">{fmtMs(status.sim_client?.p95_ms)}</div>
            </div>
            <div>
              <div className="text-muted">Sim error rate (1m)</div>
              <div className="tabular">{fmtPct(status.sim_client?.error_rate_1m)}</div>
            </div>
            <div>
              <div className="text-muted">API p95</div>
              <div className="tabular">{fmtMs(status.api?.p95_ms)}</div>
            </div>
            <div>
              <div className="text-muted">API error rate (1m)</div>
              <div className="tabular">{fmtPct(status.api?.error_rate_1m)}</div>
            </div>
            <div>
              <div className="text-muted">Tick lag</div>
              <div className="tabular">{status.loop?.tick_lag ?? "—"}</div>
            </div>
            <div>
              <div className="text-muted">Ticks skipped</div>
              <div className="tabular">{status.loop?.ticks_skipped ?? "—"}</div>
            </div>
            <div>
              <div className="text-muted">Last cycle</div>
              <div className="tabular">{fmtMs(status.loop?.last_cycle_ms)}</div>
            </div>
            <div>
              <div className="text-muted">Ready</div>
              <Chip tone={status.ready ? "good" : "critical"}>{status.ready ? "yes" : "no"}</Chip>
            </div>
          </div>

          <div className="text-xs text-muted flex flex-wrap gap-3">
            <span>version {status.version || "—"}</span>
            <span>git {status.git_sha ? status.git_sha.slice(0, 7) : "—"}</span>
            <span>policy {status.policy || "—"}</span>
            {status.insecure_defaults && <Chip tone="warning">default tokens</Chip>}
          </div>
        </>
      )}
    </section>
  );
}
