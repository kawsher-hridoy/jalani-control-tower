// Incidents + Decisions: two tabs sharing one panel to save screen space.

import { useState } from "react";
import type { Decision, Incident } from "../types";
import { idToLabel } from "../lib/format";
import { SeverityChip, Chip } from "./chips";

function IncidentRow({ incident }: { incident: Incident }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b" style={{ borderColor: "var(--gridline)" }}>
      <button
        className="w-full text-left py-2 flex items-center gap-2 bg-transparent border-0 cursor-pointer"
        style={{ color: "inherit" }}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-muted text-xs">{open ? "▾" : "▸"}</span>
        <SeverityChip severity={incident.severity} />
        <Chip tone={incident.status === "OPEN" ? "warning" : "good"}>{incident.status}</Chip>
        <strong className="text-sm">{incident.title}</strong>
        <span className="text-muted text-xs ml-auto whitespace-nowrap">
          opened tick {incident.opened_tick}
          {incident.resolved_tick !== null ? ` · resolved tick ${incident.resolved_tick}` : ""}
        </span>
      </button>
      {open && (
        <div className="pb-3 pl-6">
          <p className="text-secondary text-xs mb-2">{incident.summary}</p>
          {incident.entities?.length > 0 && (
            <div className="text-xs text-muted mb-2">entities: {incident.entities.map(idToLabel).join(", ")}</div>
          )}
          {incident.brief && <p className="text-xs text-secondary mb-2">{incident.brief}</p>}
          <ul className="m-0 pl-4 text-xs text-secondary">
            {(incident.timeline ?? []).length === 0 && <li className="text-muted">no timeline entries</li>}
            {(incident.timeline ?? []).map((e, i) => (
              <li key={i}>
                tick {e.tick}: {e.text}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function DecisionsList({ decisions }: { decisions: Decision[] }) {
  return (
    <div className="scrollbox">
      <table>
        <thead>
          <tr>
            <th>Tick</th>
            <th>Kind</th>
            <th>Actor</th>
            <th>Summary</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {decisions.length === 0 && (
            <tr>
              <td colSpan={5} className="text-muted">
                no decisions yet
              </td>
            </tr>
          )}
          {decisions.map((d) => (
            <tr key={d.id}>
              <td className="tabular">{d.tick}</td>
              <td>{d.kind}</td>
              <td>{d.actor}</td>
              <td className="text-secondary">{d.summary}</td>
              <td className="text-muted">{d.outcome}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function IncidentsDecisionsPanel({
  incidents,
  decisions,
}: {
  incidents: Incident[];
  decisions: Decision[];
}) {
  const [tab, setTab] = useState<"incidents" | "decisions">("incidents");
  const openCount = incidents.filter((i) => i.status === "OPEN").length;

  return (
    <section className="panel p-4 flex-1 min-w-[320px]">
      <div className="flex items-center gap-2 mb-3">
        <button className={`btn ${tab === "incidents" ? "btn-primary" : ""}`} onClick={() => setTab("incidents")}>
          Incidents {openCount > 0 ? `(${openCount} open)` : ""}
        </button>
        <button className={`btn ${tab === "decisions" ? "btn-primary" : ""}`} onClick={() => setTab("decisions")}>
          Decisions
        </button>
      </div>
      {tab === "incidents" ? (
        <div className="scrollbox">
          {incidents.length === 0 && <div className="text-muted text-sm">no incidents yet</div>}
          {incidents
            .slice()
            .sort((a, b) => (a.status === b.status ? b.opened_tick - a.opened_tick : a.status === "OPEN" ? -1 : 1))
            .map((i) => (
              <IncidentRow key={i.id} incident={i} />
            ))}
        </div>
      ) : (
        <DecisionsList decisions={decisions.slice().sort((a, b) => b.tick - a.tick)} />
      )}
    </section>
  );
}
