// Thin fetch layer for the backend -> web contract (implementation_plan.md §6).
// Every function returns real data when the backend is reachable, or mock
// data when the page is loaded with ?mock=1. Auth headers are attached from
// whatever tokens are currently saved (src/lib/auth.ts).

import type {
  Decision,
  ForecastResponse,
  Incident,
  PlanPreviewResponse,
  ReadyResponse,
  Recommendation,
  Settings,
  StateResponse,
  StatusResponse,
} from "./types";
import { ApiError } from "./lib/apiError";
import { loadAccess } from "./lib/auth";
import * as mock from "./mock";

export function isMockMode(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("mock") === "1";
  } catch {
    return false;
  }
}

async function parseErrorBody(res: Response): Promise<ApiError> {
  let message = `${res.status} ${res.statusText}`;
  let code: string | undefined;
  try {
    const body = await res.json();
    const d = body?.detail ?? body?.error;
    if (d?.message) message = String(d.message);
    if (d?.code) code = String(d.code);
  } catch {
    // Body wasn't JSON (or was empty); keep the status-line message.
  }
  return new ApiError(message, res.status, code);
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) throw await parseErrorBody(res);
  return (await res.json()) as T;
}

type AuthKind = "operator" | "admin" | "both";

function authHeaders(kind: AuthKind): HeadersInit {
  const { operatorToken, adminToken } = loadAccess();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if ((kind === "operator" || kind === "both") && operatorToken) headers["X-Operator-Token"] = operatorToken;
  if ((kind === "admin" || kind === "both") && adminToken) headers["X-Admin-Token"] = adminToken;
  return headers;
}

// ---- Core polling endpoints --------------------------------------------------

export async function fetchState(): Promise<StateResponse> {
  if (isMockMode()) return mock.getMockState();
  return getJson<StateResponse>("/api/state");
}

export async function fetchStatus(): Promise<StatusResponse> {
  if (isMockMode()) return mock.getMockStatus();
  return getJson<StatusResponse>("/api/status");
}

// GET /api/ready is optional for the UI (no panel depends on it); kept here
// so the fetch layer matches the full contract.
export async function fetchReady(): Promise<ReadyResponse> {
  if (isMockMode()) return mock.getMockReady();
  return getJson<ReadyResponse>("/api/ready");
}

// POST /api/plan/preview is primarily a k6 target, not a UI panel; kept here
// for contract completeness.
export async function planPreview(): Promise<PlanPreviewResponse> {
  if (isMockMode()) return mock.getMockPlanPreview();
  return getJson<PlanPreviewResponse>("/api/plan/preview", { method: "POST" });
}

export async function fetchForecast(stationId: string, fuelType: string): Promise<ForecastResponse> {
  if (isMockMode()) return mock.getMockForecast(stationId, fuelType);
  const params = new URLSearchParams({ station_id: stationId, fuel_type: fuelType });
  return getJson<ForecastResponse>(`/api/forecast?${params.toString()}`);
}

export async function fetchRecommendations(status?: string): Promise<Recommendation[]> {
  if (isMockMode()) return mock.getMockRecommendations(status);
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  return getJson<Recommendation[]>(`/api/recommendations${q}`);
}

export async function approveRecommendation(
  id: string,
  operator: string,
  note: string,
  revision: number,
): Promise<Recommendation> {
  if (isMockMode()) return mock.mockApprove(id, operator, note, revision);
  return getJson<Recommendation>(`/api/recommendations/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    headers: authHeaders("operator"),
    body: JSON.stringify({ operator, note, revision }),
  });
}

export async function rejectRecommendation(
  id: string,
  operator: string,
  reason: string,
): Promise<Recommendation> {
  if (isMockMode()) return mock.mockReject(id, operator, reason);
  return getJson<Recommendation>(`/api/recommendations/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    headers: authHeaders("operator"),
    body: JSON.stringify({ operator, reason }),
  });
}

export async function fetchDecisions(limit = 100): Promise<Decision[]> {
  if (isMockMode()) return mock.getMockDecisions(limit);
  return getJson<Decision[]>(`/api/decisions?limit=${limit}`);
}

export async function fetchIncidents(): Promise<Incident[]> {
  if (isMockMode()) return mock.getMockIncidents();
  return getJson<Incident[]>("/api/incidents");
}

export async function fetchSettings(): Promise<Settings> {
  if (isMockMode()) return mock.getMockSettings();
  return getJson<Settings>("/api/settings");
}

export async function putMode(autonomyMode: string): Promise<Settings> {
  if (isMockMode()) return mock.mockSetMode(autonomyMode);
  return getJson<Settings>("/api/mode", {
    method: "PUT",
    headers: authHeaders("admin"),
    body: JSON.stringify({ autonomy_mode: autonomyMode }),
  });
}

// ---- Game Day (admin) ---------------------------------------------------------

export interface GameDayEventPayload {
  type: string;
  start_in_ticks: number;
  duration_ticks: number;
  parameters: Record<string, unknown>;
}

export interface GameDayFaultPayload {
  type: string;
  duration_seconds: number;
  parameters: Record<string, unknown>;
}

export async function gamedayEvent(payload: GameDayEventPayload): Promise<unknown> {
  if (isMockMode()) return mock.mockGamedayEvent(payload);
  return getJson(`/api/gameday/event`, {
    method: "POST",
    headers: authHeaders("admin"),
    body: JSON.stringify(payload),
  });
}

export async function gamedayFault(payload: GameDayFaultPayload): Promise<unknown> {
  if (isMockMode()) return mock.mockGamedayFault(payload);
  return getJson(`/api/gameday/fault`, {
    method: "POST",
    headers: authHeaders("admin"),
    body: JSON.stringify(payload),
  });
}

export async function gamedayFaultsClear(): Promise<{ status: string }> {
  if (isMockMode()) return mock.mockGamedayFaultsClear();
  return getJson(`/api/gameday/faults/clear`, { method: "POST", headers: authHeaders("admin") });
}

export async function gamedayFaultsList(): Promise<import("./types").ActiveFault[]> {
  if (isMockMode()) return mock.mockGamedayFaultsList();
  return getJson(`/api/gameday/faults`, { headers: authHeaders("admin") });
}

export async function gamedayIntel(enabled: boolean): Promise<{ intel_enabled: boolean }> {
  if (isMockMode()) return mock.mockGamedayIntel(enabled);
  return getJson(`/api/gameday/intel`, {
    method: "POST",
    headers: authHeaders("admin"),
    body: JSON.stringify({ enabled }),
  });
}

export async function gamedaySimulation(
  action: "run" | "pause" | "step" | "reset",
  ticks?: number,
): Promise<{ status: string }> {
  if (isMockMode()) return mock.mockGamedaySimulation(action, ticks);
  return getJson(`/api/gameday/simulation`, {
    method: "POST",
    headers: authHeaders("admin"),
    body: JSON.stringify(ticks !== undefined ? { action, ticks } : { action }),
  });
}
