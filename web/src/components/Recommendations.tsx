// Recommendation cards: the most important panel. PENDING (and EXECUTING)
// recommendations are shown as active cards; everything else is recent
// history, collapsed behind a toggle.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Recommendation } from "../types";
import { approveRecommendation, rejectRecommendation } from "../api";
import { useAccess } from "../context/AccessContext";
import { ApiError } from "../lib/apiError";
import { fmtHours, fmtLiters, fmtNum, fmtPct, idToLabel } from "../lib/format";
import { ConfidenceChip, RecClassChip, RecStatusChip, TierChip } from "./chips";

function Spinner() {
  return (
    <span
      aria-label="executing"
      style={{
        display: "inline-block",
        width: 11,
        height: 11,
        border: "2px solid var(--border-hairline)",
        borderTopColor: "var(--series-1)",
        borderRadius: "50%",
        animation: "spin 0.8s linear infinite",
      }}
    />
  );
}

function deadlineLabel(deadlineTick: number | null | undefined, currentTick: number | undefined): string {
  if (deadlineTick === null || deadlineTick === undefined) return "no deadline set";
  if (currentTick === undefined) return `deadline tick ${deadlineTick}`;
  const k = deadlineTick - currentTick;
  return k >= 0
    ? `deadline tick ${deadlineTick} (in ${k} ticks)`
    : `deadline tick ${deadlineTick} (overdue by ${Math.abs(k)} ticks)`;
}

function routeActionLabel(a: Recommendation["actions"][number]): string {
  return `${idToLabel(a.source_depot_id)} → ${idToLabel(a.destination_station_id)}`;
}

function errorNotice(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "REVISION_CHANGED" || err.code === "REVALIDATION_CHANGED") {
      return "Plan changed: review again";
    }
    return `${err.code ? `${err.code}: ` : ""}${err.message}`;
  }
  return err instanceof Error ? err.message : "Request failed";
}

function RecommendationCard({ rec, currentTick }: { rec: Recommendation; currentTick: number | undefined }) {
  const access = useAccess();
  const qc = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);

  const canAct = access.hasOperatorToken || access.hasAdminToken;
  const actorName = access.operatorName || "operator";
  const isPending = rec.status === "PENDING";
  const isExecuting = rec.status === "EXECUTING";

  function refetch() {
    qc.invalidateQueries({ queryKey: ["recommendations"] });
  }

  const approve = useMutation({
    mutationFn: () => approveRecommendation(rec.id, actorName, "", rec.revision),
    onSuccess: () => {
      setNotice(null);
      refetch();
    },
    onError: (err) => {
      setNotice(errorNotice(err));
      refetch();
    },
  });

  const reject = useMutation({
    mutationFn: (reason: string) => rejectRecommendation(rec.id, actorName, reason),
    onSuccess: () => {
      setNotice(null);
      refetch();
    },
    onError: (err) => {
      setNotice(errorNotice(err));
      refetch();
    },
  });

  const actions = rec.actions ?? [];
  const failedActions = rec.failed_actions ?? [];
  const conditions = rec.conditions ?? [];
  const busy = approve.isPending || reject.isPending;

  return (
    <div className="panel p-3">
      <div className="flex flex-wrap items-center gap-1.5 mb-2">
        <RecStatusChip status={rec.status} />
        {isExecuting && <Spinner />}
        <RecClassChip recClass={rec.rec_class} />
        <TierChip tier={rec.tier} />
        {conditions.map((c) => (
          <RecClassChip key={c} recClass={c} />
        ))}
        <span className="text-muted text-xs ml-auto">rev {rec.revision ?? 1}</span>
      </div>

      <div className="text-sm mb-1">
        <strong>{idToLabel(rec.station_id)}</strong> · {rec.fuel_type}
      </div>
      <div className="text-xs text-muted mb-2">
        {deadlineLabel(rec.deadline_tick, currentTick)} · created tick {rec.created_tick}
      </div>

      {actions.length > 0 && (
        <div className="mb-2">
          <div className="text-xs text-muted uppercase tracking-wide mb-1">Actions</div>
          {actions.map((a, i) => (
            <div key={i} className="text-xs tabular mb-0.5">
              {routeActionLabel(a)} · {a.fuel_type} · {fmtNum(a.quantity)} L · ETA tick {a.eta_tick}
            </div>
          ))}
        </div>
      )}

      {failedActions.length > 0 && (
        <div
          className="mb-2 p-2 rounded"
          style={{ background: "rgba(208,59,59,0.12)", border: "1px solid rgba(208,59,59,0.35)" }}
        >
          <div className="text-xs" style={{ color: "#ff8080" }}>
            Partially executed: {Math.max(0, actions.length - failedActions.length)} of {actions.length} actions
          </div>
          {failedActions.map((f, i) => (
            <div key={i} className="text-xs text-secondary tabular">
              {idToLabel(f.route_id)} · {f.fuel_type} · {fmtNum(f.quantity)} L — {f.code}
            </div>
          ))}
        </div>
      )}

      <div className="mb-2">
        <div className="text-xs text-muted uppercase tracking-wide mb-1">Projected impact</div>
        <div className="text-xs tabular">
          Stockout risk: {fmtPct(rec.impact?.p_stockout_before)} → {fmtPct(rec.impact?.p_stockout_after)}
        </div>
        <div className="text-xs tabular">
          Unmet: {fmtLiters(rec.impact?.unmet_before_liters)} → {fmtLiters(rec.impact?.unmet_after_liters)}
        </div>
        <div className="text-xs tabular">
          Hours to stockout: {fmtHours(rec.impact?.hours_to_stockout_before)} →{" "}
          {fmtHours(rec.impact?.hours_to_stockout_after)}
        </div>
      </div>

      {rec.signals?.length > 0 && (
        <div className="mb-1 text-xs">
          <span className="text-muted">Signals: </span>
          {rec.signals.join("; ")}
        </div>
      )}
      {rec.constraints?.length > 0 && (
        <div className="mb-1 text-xs">
          <span className="text-muted">Constraints: </span>
          {rec.constraints.join("; ")}
        </div>
      )}
      {rec.explanation && <div className="text-xs text-secondary mt-1 mb-2">{rec.explanation}</div>}

      <div className="text-xs text-muted mb-2 flex items-center gap-1.5">
        confidence <ConfidenceChip label={rec.confidence_label} value={rec.confidence} />
      </div>

      {rec.decided_by && (
        <div className="text-xs text-muted mb-2">
          decided by {rec.decided_by} at tick {rec.decided_at_tick ?? "—"}
          {rec.note ? ` — "${rec.note}"` : ""}
        </div>
      )}

      {notice && (
        <div
          className="text-xs mb-2 p-2 rounded"
          style={{
            background: "rgba(250,178,25,0.12)",
            border: "1px solid rgba(250,178,25,0.35)",
            color: "var(--status-warning)",
          }}
        >
          {notice}
        </div>
      )}

      {isPending && (
        <div className="flex gap-2">
          <button
            className="btn btn-primary"
            disabled={!canAct || busy}
            title={canAct ? undefined : "Enter an operator or admin token in Access"}
            onClick={() => approve.mutate()}
          >
            {approve.isPending ? "Approving…" : "Approve"}
          </button>
          <button
            className="btn btn-danger"
            disabled={!canAct || busy}
            title={canAct ? undefined : "Enter an operator or admin token in Access"}
            onClick={() => {
              const reason = window.prompt("Reason for rejecting?", "") ?? "";
              reject.mutate(reason);
            }}
          >
            {reject.isPending ? "Rejecting…" : "Reject"}
          </button>
        </div>
      )}
      {isExecuting && <div className="text-xs text-muted">Executing — actions disabled until this settles.</div>}
    </div>
  );
}

export function RecommendationsPanel({
  recommendations,
  currentTick,
}: {
  recommendations: Recommendation[];
  currentTick: number | undefined;
}) {
  const [showHistory, setShowHistory] = useState(false);
  const active = recommendations.filter((r) => r.status === "PENDING" || r.status === "EXECUTING");
  const history = recommendations
    .filter((r) => r.status !== "PENDING" && r.status !== "EXECUTING")
    .sort((a, b) => b.created_tick - a.created_tick);

  return (
    <section className="panel m-3 p-4">
      <h2 className="text-sm text-muted uppercase tracking-wide mt-0 mb-3">Recommendations</h2>
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}>
        {active.length === 0 && <div className="text-muted text-sm">no pending recommendations</div>}
        {active.map((r) => (
          <RecommendationCard key={r.id} rec={r} currentTick={currentTick} />
        ))}
      </div>

      <button className="btn mt-3" onClick={() => setShowHistory((v) => !v)}>
        {showHistory ? "Hide" : "Show"} history ({history.length})
      </button>
      {showHistory && (
        <div className="grid gap-3 mt-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}>
          {history.map((r) => (
            <RecommendationCard key={r.id} rec={r} currentTick={currentTick} />
          ))}
        </div>
      )}
    </section>
  );
}
