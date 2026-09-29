// Game Day: presets, a generic event form, fault injection buttons, an
// intel on/off toggle, and simulation transport controls. Every call here
// needs an admin token; the result or error of the most recent call is
// shown inline underneath.

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { GameDayEventType, GameDayFaultType } from "../types";
import {
  fetchSettings,
  gamedayEvent,
  gamedayFault,
  gamedayFaultsClear,
  gamedayIntel,
  gamedaySimulation,
} from "../api";
import { useAccess } from "../context/AccessContext";
import { ApiError } from "../lib/apiError";
import { Chip } from "./chips";

const EVENT_TYPES: GameDayEventType[] = [
  "demand_spike",
  "route_disruption",
  "station_outage",
  "depot_constraint",
  "shipment_delay",
  "supply_shortfall",
];

const FAULT_BUTTONS: { type: GameDayFaultType; label: string; parameters: Record<string, unknown> }[] = [
  { type: "latency", label: "Latency +1.5s", parameters: { latency_ms: 1500 } },
  { type: "unavailable", label: "Unavailable", parameters: {} },
  { type: "error_rate", label: "Error rate 50%", parameters: { rate: 0.5 } },
  { type: "stale_data", label: "Stale data", parameters: {} },
  { type: "stream_disconnect", label: "Stream disconnect", parameters: {} },
];

function errText(err: unknown): string {
  if (err instanceof ApiError) return `${err.code ? `${err.code}: ` : ""}${err.message}`;
  return err instanceof Error ? err.message : String(err);
}

export function GameDayPanel() {
  const access = useAccess();
  const qc = useQueryClient();
  const settingsQuery = useQuery({ queryKey: ["settings"], queryFn: fetchSettings, refetchInterval: 5000 });

  const [busy, setBusy] = useState(false);
  const [lastLabel, setLastLabel] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const [eventType, setEventType] = useState<GameDayEventType>("demand_spike");
  const [startInTicks, setStartInTicks] = useState(0);
  const [durationTicks, setDurationTicks] = useState(24);
  const [parametersText, setParametersText] = useState("{}");
  const [parseError, setParseError] = useState<string | null>(null);

  const [stepTicks, setStepTicks] = useState(4);

  const canAct = access.hasAdminToken;

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(true);
    setLastLabel(label);
    setLastError(null);
    setLastResult(null);
    try {
      const res = await fn();
      setLastResult(JSON.stringify(res));
    } catch (err) {
      setLastError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  function runPreset(
    label: string,
    type: GameDayEventType,
    start_in_ticks: number,
    duration_ticks: number,
    parameters: Record<string, unknown>,
  ) {
    run(label, () => gamedayEvent({ type, start_in_ticks, duration_ticks, parameters }));
  }

  function submitCustomEvent() {
    let parsed: Record<string, unknown>;
    try {
      parsed = parametersText.trim() ? JSON.parse(parametersText) : {};
      setParseError(null);
    } catch (e) {
      setParseError(e instanceof Error ? e.message : "invalid JSON");
      return;
    }
    run("custom event", () =>
      gamedayEvent({ type: eventType, start_in_ticks: startInTicks, duration_ticks: durationTicks, parameters: parsed }),
    );
  }

  function toggleIntel() {
    const next = !(settingsQuery.data?.intel_enabled ?? true);
    run(`intel ${next ? "on" : "off"}`, async () => {
      const res = await gamedayIntel(next);
      qc.invalidateQueries({ queryKey: ["settings"] });
      return res;
    });
  }

  return (
    <section className="panel p-4 flex-1 min-w-[320px]">
      <h2 className="text-sm text-muted uppercase tracking-wide mt-0 mb-3">Game Day</h2>
      {!canAct && (
        <div className="text-xs text-muted mb-3">Enter an admin token in Access to use these controls.</div>
      )}

      <div className="flex flex-wrap gap-2 mb-3">
        <button
          className="btn"
          disabled={!canAct || busy}
          onClick={() =>
            runPreset("Dhaka demand spike ×1.8", "demand_spike", 0, 24, {
              region_ids: ["region-dhaka"],
              multiplier: 1.8,
            })
          }
        >
          Dhaka demand spike ×1.8
        </button>
        <button
          className="btn"
          disabled={!canAct || busy}
          onClick={() =>
            runPreset("Cut Tongi route", "route_disruption", 2, 16, { route_ids: ["route-gazipur-tongi"] })
          }
        >
          Cut Tongi route
        </button>
      </div>

      <details className="mb-3">
        <summary className="text-xs text-muted uppercase tracking-wide cursor-pointer">Custom event</summary>
        <div className="flex flex-wrap gap-2 mt-2 items-end">
          <label className="text-xs text-muted">
            Type
            <select
              className="block mt-1"
              value={eventType}
              onChange={(e) => setEventType(e.target.value as GameDayEventType)}
            >
              {EVENT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-muted">
            Start in ticks
            <input
              className="block mt-1 w-24"
              type="number"
              value={startInTicks}
              onChange={(e) => setStartInTicks(Number(e.target.value))}
            />
          </label>
          <label className="text-xs text-muted">
            Duration ticks
            <input
              className="block mt-1 w-24"
              type="number"
              value={durationTicks}
              onChange={(e) => setDurationTicks(Number(e.target.value))}
            />
          </label>
        </div>
        <label className="block text-xs text-muted mt-2">
          Parameters (JSON)
          <textarea
            className="block w-full mt-1"
            rows={3}
            value={parametersText}
            onChange={(e) => setParametersText(e.target.value)}
          />
        </label>
        {parseError && (
          <div className="text-xs mt-1" style={{ color: "#ff8080" }}>
            Invalid JSON: {parseError}
          </div>
        )}
        <button className="btn btn-primary mt-2" disabled={!canAct || busy} onClick={submitCustomEvent}>
          Send event
        </button>
      </details>

      <div className="mb-1 text-xs text-muted uppercase tracking-wide">Faults (30s each)</div>
      <div className="flex flex-wrap gap-2 mb-3">
        {FAULT_BUTTONS.map((f) => (
          <button
            key={f.type}
            className="btn"
            disabled={!canAct || busy}
            onClick={() => run(f.label, () => gamedayFault({ type: f.type, duration_seconds: 30, parameters: f.parameters }))}
          >
            {f.label}
          </button>
        ))}
        <button
          className="btn btn-danger"
          disabled={!canAct || busy}
          onClick={() => run("clear faults", () => gamedayFaultsClear())}
        >
          Clear faults
        </button>
      </div>

      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs text-muted uppercase tracking-wide">Intel</span>
        <Chip tone={settingsQuery.data?.intel_enabled ? "good" : "neutral"}>
          {settingsQuery.data?.intel_enabled ? "ON" : "OFF"}
        </Chip>
        <button className="btn" disabled={!canAct || busy} onClick={toggleIntel}>
          Toggle
        </button>
      </div>

      <div className="mb-1 text-xs text-muted uppercase tracking-wide">Simulation</div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button className="btn" disabled={!canAct || busy} onClick={() => run("run", () => gamedaySimulation("run"))}>
          Run
        </button>
        <button className="btn" disabled={!canAct || busy} onClick={() => run("pause", () => gamedaySimulation("pause"))}>
          Pause
        </button>
        <input
          className="w-16"
          type="number"
          min={1}
          max={96}
          value={stepTicks}
          onChange={(e) => setStepTicks(Number(e.target.value))}
        />
        <button
          className="btn"
          disabled={!canAct || busy}
          onClick={() => run(`step ${stepTicks}`, () => gamedaySimulation("step", stepTicks))}
        >
          Step N
        </button>
        <button
          className="btn btn-danger"
          disabled={!canAct || busy}
          onClick={() => run("reset world", () => gamedaySimulation("reset"))}
        >
          Reset world
        </button>
      </div>

      {(lastLabel || lastError) && (
        <div
          className="text-xs p-2 rounded"
          style={{
            background: lastError ? "rgba(208,59,59,0.12)" : "rgba(12,163,12,0.1)",
            border: `1px solid ${lastError ? "rgba(208,59,59,0.35)" : "rgba(12,163,12,0.3)"}`,
          }}
        >
          <strong>{lastLabel}</strong>: {lastError ? lastError : lastResult}
        </div>
      )}
    </section>
  );
}
