import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { AutonomyMode, StateResponse, StatusResponse } from "../types";
import { putMode } from "../api";
import { useAccess } from "../context/AccessContext";
import { fmtDateTime } from "../lib/format";
import { Chip, FlagChip, ModePill } from "./chips";

const AUTONOMY_OPTIONS: AutonomyMode[] = ["ADVISORY", "SUPERVISED", "AUTOPILOT"];

export function Header({
  state,
  status,
}: {
  state: StateResponse | undefined;
  status: StatusResponse | undefined;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const access = useAccess();
  const qc = useQueryClient();

  const modeMutation = useMutation({
    mutationFn: (mode: AutonomyMode) => putMode(mode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["state"] });
    },
  });

  const instance = state?.instance;
  const flags = state?.flags ?? [];

  return (
    <header className="panel m-3 px-4 py-3 flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-3">
        <strong className="text-base whitespace-nowrap">Jalani Control Tower</strong>
        <Chip tone="critical" title="This console operates a simulated network only. No real fuel infrastructure is affected.">
          SIMULATION — not real fuel infrastructure
        </Chip>
      </div>

      <div className="tabular text-secondary text-sm whitespace-nowrap">
        {instance ? (
          <>
            tick <strong className="tabular text-white">{instance.tick}</strong>
            <span className="text-muted"> · </span>
            {fmtDateTime(instance.sim_time)}
            <span className="text-muted"> · </span>
            <Chip tone={instance.status === "RUNNING" ? "good" : "warning"}>{instance.status}</Chip>
          </>
        ) : (
          <span className="text-muted">no data yet</span>
        )}
      </div>

      <ModePill mode={state?.operating_mode} />

      {status?.world && (
        <Chip tone={status.world === "RUNNING" ? "good" : "warning"}>{status.world}</Chip>
      )}
      {status?.insecure_defaults && (
        <Chip tone="warning" title="Operator/admin tokens are still the shipped defaults">
          default tokens
        </Chip>
      )}

      <div className="flex gap-1.5 flex-wrap">
        {flags.map((f) => (
          <FlagChip key={f} flag={f} />
        ))}
      </div>

      <div className="ml-auto flex items-center gap-3 flex-wrap">
        <label className="text-xs text-muted flex items-center gap-1.5">
          Autonomy
          <select
            value={state?.autonomy_mode ?? "SUPERVISED"}
            disabled={!access.hasAdminToken || modeMutation.isPending}
            onChange={(e) => modeMutation.mutate(e.target.value as AutonomyMode)}
            title={
              access.hasAdminToken
                ? "Change autonomy mode (admin token required)"
                : "Enter an admin token in Access to change this"
            }
          >
            {AUTONOMY_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        {modeMutation.isError && <Chip tone="critical">{(modeMutation.error as Error)?.message ?? "failed"}</Chip>}
        <button className="btn" onClick={() => setDialogOpen(true)}>
          Access{access.operatorName ? ` · ${access.operatorName}` : ""}
        </button>
      </div>

      {dialogOpen && <AccessDialog onClose={() => setDialogOpen(false)} />}
    </header>
  );
}

function AccessDialog({ onClose }: { onClose: () => void }) {
  const access = useAccess();
  const [name, setName] = useState(access.operatorName);
  const [opToken, setOpToken] = useState(access.operatorToken);
  const [adminToken, setAdminToken] = useState(access.adminToken);

  function save() {
    access.update({ operatorName: name, operatorToken: opToken, adminToken });
    onClose();
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="panel p-5 w-[340px]" onClick={(e) => e.stopPropagation()}>
        <h3 className="mt-0 mb-2 text-base">Access</h3>
        <p className="text-muted text-xs">
          Stored only in this browser and sent as X-Operator-Token / X-Admin-Token headers on requests that need them.
        </p>
        <label className="block text-xs text-muted mt-3">
          Operator name
          <input
            className="block w-full mt-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. duty-manager"
          />
        </label>
        <label className="block text-xs text-muted mt-3">
          Operator token
          <input className="block w-full mt-1" value={opToken} onChange={(e) => setOpToken(e.target.value)} type="password" />
        </label>
        <label className="block text-xs text-muted mt-3">
          Admin token
          <input
            className="block w-full mt-1"
            value={adminToken}
            onChange={(e) => setAdminToken(e.target.value)}
            type="password"
          />
        </label>
        <div className="flex justify-end gap-2 mt-4">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={save}>
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
