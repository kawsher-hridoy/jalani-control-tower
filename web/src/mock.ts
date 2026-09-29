// Realistic sample data matching the backend -> web API contract
// (implementation_plan.md §6), enabled with ?mock=1 while the backend is
// being built in parallel. Everything here is illustrative, not measured.
//
// Topology: 2 depots (Gazipur/Dhaka, Patiya/Chattogram), 4 stations
// (Mirpur, Tongi, Karnaphuli, Cox's Bazar), 6 routes. Karnaphuli PETROL is
// the headline CRITICAL row with a pending CROSS_REGION recommendation,
// mirroring Final-Project.md §3.4's example card.

import type {
  ActiveFault,
  Decision,
  Depot,
  ForecastPoint,
  ForecastResponse,
  FuelType,
  Incident,
  Instance,
  InstanceStatus,
  Kpis,
  PlanPreviewResponse,
  ReadyResponse,
  Recommendation,
  Region,
  RiskRow,
  Route,
  Settings,
  SimEvent,
  Station,
  StateResponse,
  StatusResponse,
  Supply,
  Shipment,
} from "./types";
import { ApiError } from "./lib/apiError";

// ---------------------------------------------------------------------------
// Deterministic pseudo-random helpers (seeded by station|fuel so the same
// pair always renders the same forecast shape across polls).
// ---------------------------------------------------------------------------

function hashSeed(s: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------------------
// A slowly-advancing mock clock so the console looks alive without a backend.
// Game Day's simulation controls (run/pause/step/reset) act on this clock so
// the PAUSED banner and world pill are demonstrable in ?mock=1 too.
// ---------------------------------------------------------------------------

const START_TICK = 118;
const START_TIME = Date.parse("2026-01-02T05:30:00Z");
const TICK_MINUTES = 15;
let mockTick = START_TICK;
let lastAdvanceMs = Date.now();
let mockWorldStatus: InstanceStatus = "RUNNING";

function currentTick(): number {
  if (mockWorldStatus === "PAUSED") return mockTick;
  const now = Date.now();
  if (now - lastAdvanceMs > 4000) {
    mockTick += 1;
    lastAdvanceMs = now;
  }
  return mockTick;
}

function simTimeForTick(tick: number): string {
  const ms = START_TIME + (tick - START_TICK) * TICK_MINUTES * 60 * 1000;
  return new Date(ms).toISOString();
}

// ---------------------------------------------------------------------------
// Static topology
// ---------------------------------------------------------------------------

const REGIONS: Region[] = [
  { id: "region-dhaka", name: "Dhaka Division", demand_factor: 1.0 },
  { id: "region-chattogram", name: "Chattogram Division", demand_factor: 1.0 },
];

function buildDepots(): Depot[] {
  return [
    {
      id: "depot-gazipur",
      name: "Gazipur Depot",
      region_id: "region-dhaka",
      status: "OPEN",
      dispatch_capacity_per_tick: 12000,
      capacity: { DIESEL: 90000, PETROL: 70000, OCTANE: 45000 },
      inventory: { DIESEL: 58000, PETROL: 41000, OCTANE: 26000 },
      days_of_cover: { DIESEL: 3.2, PETROL: 4.1, OCTANE: 5.0 },
    },
    {
      id: "depot-patiya",
      name: "Patiya Depot",
      region_id: "region-chattogram",
      status: "CONSTRAINED",
      dispatch_capacity_per_tick: 9000,
      capacity: { DIESEL: 70000, PETROL: 55000, OCTANE: 30000 },
      inventory: { DIESEL: 21000, PETROL: 13800, OCTANE: 9600 },
      days_of_cover: { DIESEL: 1.9, PETROL: 1.5, OCTANE: 2.1 },
    },
  ];
}

function buildStations(): Station[] {
  return [
    {
      id: "station-mirpur",
      name: "Mirpur Fuel Station",
      region_id: "region-dhaka",
      status: "OPEN",
      demand_profile: "urban_high",
      demand_multiplier: 1.0,
      capacity: { DIESEL: 15000, PETROL: 14000, OCTANE: 9000 },
      inventory: { DIESEL: 9400, PETROL: 8800, OCTANE: 5100 },
      in_transit: { DIESEL: 0, PETROL: 0, OCTANE: 0 },
    },
    {
      id: "station-tongi",
      name: "Tongi Fuel Station",
      region_id: "region-dhaka",
      status: "OPEN",
      demand_profile: "industrial",
      demand_multiplier: 1.0,
      capacity: { DIESEL: 13000, PETROL: 9000, OCTANE: 6000 },
      inventory: { DIESEL: 3200, PETROL: 4100, OCTANE: 2600 },
      in_transit: { DIESEL: 0, PETROL: 0, OCTANE: 0 },
    },
    {
      id: "station-karnaphuli",
      name: "Karnaphuli Fuel Station",
      region_id: "region-chattogram",
      status: "OPEN",
      demand_profile: "industrial",
      demand_multiplier: 1.8,
      capacity: { DIESEL: 12000, PETROL: 11000, OCTANE: 6000 },
      inventory: { DIESEL: 4300, PETROL: 1950, OCTANE: 2200 },
      in_transit: { DIESEL: 0, PETROL: 2000, OCTANE: 0 },
    },
    {
      id: "station-coxsbazar",
      name: "Cox's Bazar Fuel Station",
      region_id: "region-chattogram",
      status: "OPEN",
      demand_profile: "tourist_seasonal",
      demand_multiplier: 1.0,
      capacity: { DIESEL: 10000, PETROL: 9000, OCTANE: 5000 },
      inventory: { DIESEL: 6100, PETROL: 5300, OCTANE: 2100 },
      in_transit: { DIESEL: 0, PETROL: 0, OCTANE: 0 },
    },
  ];
}

function buildRoutes(tick: number): Route[] {
  return [
    {
      id: "route-gazipur-mirpur",
      source_depot_id: "depot-gazipur",
      destination_station_id: "station-mirpur",
      transit_ticks: 2,
      max_shipment: 7000,
      status: "AVAILABLE",
      next_disruption: null,
    },
    {
      id: "route-gazipur-tongi",
      source_depot_id: "depot-gazipur",
      destination_station_id: "station-tongi",
      transit_ticks: 1,
      max_shipment: 6000,
      status: "DISRUPTED",
      next_disruption: null,
    },
    {
      id: "route-patiya-karnaphuli",
      source_depot_id: "depot-patiya",
      destination_station_id: "station-karnaphuli",
      transit_ticks: 2,
      max_shipment: 5000,
      status: "AVAILABLE",
      next_disruption: null,
    },
    {
      id: "route-patiya-coxsbazar",
      source_depot_id: "depot-patiya",
      destination_station_id: "station-coxsbazar",
      transit_ticks: 3,
      max_shipment: 4000,
      status: "AVAILABLE",
      next_disruption: null,
    },
    {
      id: "route-gazipur-karnaphuli",
      source_depot_id: "depot-gazipur",
      destination_station_id: "station-karnaphuli",
      transit_ticks: 4,
      max_shipment: 7000,
      status: "AVAILABLE",
      next_disruption: null,
    },
    {
      id: "route-patiya-mirpur",
      source_depot_id: "depot-patiya",
      destination_station_id: "station-mirpur",
      transit_ticks: 4,
      max_shipment: 5000,
      status: "AVAILABLE",
      next_disruption: { start_tick: tick + 12, end_tick: tick + 22 },
    },
  ];
}

function buildSupply(): Supply[] {
  return [
    {
      id: "supply-101",
      depot_id: "depot-gazipur",
      fuel_type: "DIESEL",
      quantity: 12000,
      planned_tick: 64,
      actual_tick: null,
      status: "DELAYED",
    },
    {
      id: "supply-205",
      depot_id: "depot-patiya",
      fuel_type: "PETROL",
      quantity: 8000,
      planned_tick: 144,
      actual_tick: null,
      status: "SCHEDULED",
    },
  ];
}

function buildShipments(tick: number): Shipment[] {
  return [
    {
      id: 1043,
      idempotency_key: `j1-${tick}-route-patiya-coxsbazar-DIESEL-0`,
      source_depot_id: "depot-patiya",
      destination_station_id: "station-coxsbazar",
      route_id: "route-patiya-coxsbazar",
      fuel_type: "DIESEL",
      quantity: 1500,
      created_tick: tick,
      departure_tick: tick,
      expected_arrival_tick: tick + 3,
      actual_arrival_tick: null,
      status: "PENDING",
      failure_reason: null,
    },
    {
      id: 1042,
      idempotency_key: "j1-116-route-patiya-karnaphuli-PETROL-0",
      source_depot_id: "depot-patiya",
      destination_station_id: "station-karnaphuli",
      route_id: "route-patiya-karnaphuli",
      fuel_type: "PETROL",
      quantity: 2000,
      created_tick: tick - 2,
      departure_tick: tick - 2,
      expected_arrival_tick: tick,
      actual_arrival_tick: null,
      status: "IN_TRANSIT",
      failure_reason: null,
    },
    {
      id: 1041,
      idempotency_key: "j1-114-route-gazipur-mirpur-DIESEL-0",
      source_depot_id: "depot-gazipur",
      destination_station_id: "station-mirpur",
      route_id: "route-gazipur-mirpur",
      fuel_type: "DIESEL",
      quantity: 3000,
      created_tick: tick - 4,
      departure_tick: tick - 4,
      expected_arrival_tick: tick - 2,
      actual_arrival_tick: tick - 2,
      status: "ARRIVED",
      failure_reason: null,
    },
    {
      id: 1040,
      idempotency_key: "j1-110-route-patiya-coxsbazar-OCTANE-0",
      source_depot_id: "depot-patiya",
      destination_station_id: "station-coxsbazar",
      route_id: "route-patiya-coxsbazar",
      fuel_type: "OCTANE",
      quantity: 1200,
      created_tick: tick - 8,
      departure_tick: tick - 8,
      expected_arrival_tick: tick - 5,
      actual_arrival_tick: tick - 5,
      status: "ARRIVED",
      failure_reason: null,
    },
    {
      id: 1039,
      idempotency_key: "j1-108-route-gazipur-tongi-DIESEL-0",
      source_depot_id: "depot-gazipur",
      destination_station_id: "station-tongi",
      route_id: "route-gazipur-tongi",
      fuel_type: "DIESEL",
      quantity: 2500,
      created_tick: tick - 10,
      departure_tick: tick - 10,
      expected_arrival_tick: tick - 9,
      actual_arrival_tick: null,
      status: "FAILED",
      failure_reason: "ROUTE_DISRUPTED",
    },
    {
      id: 1038,
      idempotency_key: "j1-106-route-gazipur-karnaphuli-PETROL-0",
      source_depot_id: "depot-gazipur",
      destination_station_id: "station-karnaphuli",
      route_id: "route-gazipur-karnaphuli",
      fuel_type: "PETROL",
      quantity: 4000,
      created_tick: tick - 12,
      departure_tick: tick - 12,
      expected_arrival_tick: tick - 8,
      actual_arrival_tick: tick - 8,
      status: "ARRIVED",
      failure_reason: null,
    },
    {
      id: 1037,
      idempotency_key: "j1-104-route-patiya-mirpur-DIESEL-0",
      source_depot_id: "depot-patiya",
      destination_station_id: "station-mirpur",
      route_id: "route-patiya-mirpur",
      fuel_type: "DIESEL",
      quantity: 1800,
      created_tick: tick - 14,
      departure_tick: tick - 14,
      expected_arrival_tick: tick - 10,
      actual_arrival_tick: null,
      status: "CANCELLED",
      failure_reason: "ROUTE_DISRUPTION_GUARD",
    },
  ];
}

function buildRisk(): RiskRow[] {
  return [
    {
      station_id: "station-karnaphuli",
      fuel_type: "PETROL",
      inventory: 1950,
      capacity: 11000,
      in_transit: 2000,
      demand_next_8h: 4780,
      hours_to_stockout: 3.0,
      p_stockout_8h: 0.99,
      tier: "CRITICAL",
      confidence: 0.82,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: ["petrol demand +9% vs forecast", "regional demand spike x1.8 active"],
    },
    {
      station_id: "station-tongi",
      fuel_type: "DIESEL",
      inventory: 3200,
      capacity: 13000,
      in_transit: 0,
      demand_next_8h: 2600,
      hours_to_stockout: 6.4,
      p_stockout_8h: 0.61,
      tier: "HIGH",
      confidence: 0.71,
      confidence_label: "MEDIUM",
      unavoidable: true,
      reasons: ["route-gazipur-tongi disrupted", "no alternate route within lead time"],
    },
    {
      station_id: "station-karnaphuli",
      fuel_type: "OCTANE",
      inventory: 2200,
      capacity: 6000,
      in_transit: 0,
      demand_next_8h: 1500,
      hours_to_stockout: 9.8,
      p_stockout_8h: 0.34,
      tier: "WATCH",
      confidence: 0.78,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: ["regional demand spike x1.8 active"],
    },
    {
      station_id: "station-coxsbazar",
      fuel_type: "PETROL",
      inventory: 5300,
      capacity: 9000,
      in_transit: 0,
      demand_next_8h: 2100,
      hours_to_stockout: 16.9,
      p_stockout_8h: 0.18,
      tier: "WATCH",
      confidence: 0.8,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: ["route-patiya-mirpur disruption forecast ahead"],
    },
    {
      station_id: "station-karnaphuli",
      fuel_type: "DIESEL",
      inventory: 4300,
      capacity: 12000,
      in_transit: 0,
      demand_next_8h: 1900,
      hours_to_stockout: 15.2,
      p_stockout_8h: 0.21,
      tier: "WATCH",
      confidence: 0.8,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: [],
    },
    {
      station_id: "station-tongi",
      fuel_type: "PETROL",
      inventory: 4100,
      capacity: 9000,
      in_transit: 0,
      demand_next_8h: 1500,
      hours_to_stockout: 22.0,
      p_stockout_8h: 0.08,
      tier: "OK",
      confidence: 0.85,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: [],
    },
    {
      station_id: "station-tongi",
      fuel_type: "OCTANE",
      inventory: 2600,
      capacity: 6000,
      in_transit: 0,
      demand_next_8h: 780,
      hours_to_stockout: 28.0,
      p_stockout_8h: 0.04,
      tier: "OK",
      confidence: 0.86,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: [],
    },
    {
      station_id: "station-mirpur",
      fuel_type: "DIESEL",
      inventory: 9400,
      capacity: 15000,
      in_transit: 0,
      demand_next_8h: 2900,
      hours_to_stockout: 25.9,
      p_stockout_8h: 0.03,
      tier: "OK",
      confidence: 0.88,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: [],
    },
    {
      station_id: "station-mirpur",
      fuel_type: "PETROL",
      inventory: 8800,
      capacity: 14000,
      in_transit: 0,
      demand_next_8h: 2600,
      hours_to_stockout: 27.1,
      p_stockout_8h: 0.02,
      tier: "OK",
      confidence: 0.88,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: [],
    },
    {
      station_id: "station-mirpur",
      fuel_type: "OCTANE",
      inventory: 5100,
      capacity: 9000,
      in_transit: 0,
      demand_next_8h: 1100,
      hours_to_stockout: 37.0,
      p_stockout_8h: 0.01,
      tier: "OK",
      confidence: 0.9,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: [],
    },
    {
      station_id: "station-coxsbazar",
      fuel_type: "DIESEL",
      inventory: 6100,
      capacity: 10000,
      in_transit: 0,
      demand_next_8h: 1400,
      hours_to_stockout: 34.9,
      p_stockout_8h: 0.01,
      tier: "OK",
      confidence: 0.89,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: [],
    },
    {
      station_id: "station-coxsbazar",
      fuel_type: "OCTANE",
      inventory: 2100,
      capacity: 5000,
      in_transit: 0,
      demand_next_8h: 620,
      hours_to_stockout: 27.1,
      p_stockout_8h: 0.03,
      tier: "OK",
      confidence: 0.87,
      confidence_label: "HIGH",
      unavoidable: false,
      reasons: [],
    },
  ];
}

function buildEvents(): SimEvent[] {
  return [
    {
      id: 1,
      type: "demand_spike",
      start_tick: 108,
      end_tick: 140,
      status: "ACTIVE",
      parameters: { multiplier: 1.8, station_ids: ["station-karnaphuli"] },
    },
    {
      id: 2,
      type: "route_disruption",
      start_tick: 114,
      end_tick: 132,
      status: "ACTIVE",
      parameters: { route_ids: ["route-gazipur-tongi"] },
    },
    {
      id: 3,
      type: "shipment_delay",
      start_tick: 84,
      end_tick: 90,
      status: "RESOLVED",
      parameters: { depot_ids: ["depot-patiya"], fuel_types: ["PETROL"], delay_ticks: 4 },
    },
  ];
}

function buildKpis(): Kpis {
  return {
    service_level: 0.94,
    served_liters: 186400,
    unmet_liters: 2830,
    fuel_lost_liters: 640,
    fuel_lost: { depot_overflow: 120, station_overflow: 380, failed_shipment: 140 },
    in_transit_liters: 9800,
    stations_at_risk: 2,
    allocations_total: 47,
    allocation_failures: 1,
    pending_approvals: 1,
    open_incidents: 2,
  };
}

function buildInstance(tick: number): Instance {
  return {
    tick,
    sim_time: simTimeForTick(tick),
    status: mockWorldStatus,
    tick_minutes: TICK_MINUTES,
    seed: 12345,
    scenario_id: "baseline",
  };
}

// ---------------------------------------------------------------------------
// Public mock endpoints
// ---------------------------------------------------------------------------

export function getMockState(): StateResponse {
  const tick = currentTick();
  return {
    epoch: 1,
    instance: buildInstance(tick),
    operating_mode: "NORMAL",
    flags: [],
    autonomy_mode: mockSettings.autonomy_mode,
    stale: false,
    data_age_s: 0.4,
    kpis: buildKpis(),
    regions: REGIONS,
    depots: buildDepots(),
    stations: buildStations(),
    routes: buildRoutes(tick),
    supply: buildSupply(),
    shipments: buildShipments(tick),
    risk: buildRisk(),
    events: buildEvents(),
  };
}

export function getMockStatus(): StatusResponse {
  const tick = currentTick();
  return {
    version: "0.1.0-mock",
    git_sha: "mock000",
    epoch: 1,
    operating_mode: "NORMAL",
    flags: [],
    autonomy_mode: mockSettings.autonomy_mode,
    policy: mockSettings.intel_enabled ? "lp-v1" : "heuristic-v1",
    world: mockWorldStatus,
    ready: true,
    insecure_defaults: true,
    components: [
      { name: "backend", status: "UP", detail: "ok (mock)" },
      { name: "simulator", status: "UP", detail: `tick ${tick}` },
      { name: "intel", status: mockSettings.intel_enabled ? "UP" : "DOWN", detail: mockSettings.intel_enabled ? "lp-v1 solving" : "disabled via Game Day" },
      { name: "database", status: "UP", detail: "sqlite ok" },
      { name: "stream", status: "UP", detail: "sse connected" },
      { name: "sim_lab", status: "DOWN", detail: "not started" },
      { name: "gpt", status: "DEGRADED", detail: "azure key not configured" },
    ],
    sim_client: { circuit: "CLOSED", p95_ms: 12.3, error_rate_1m: 0.0, last_success_age_s: 0.4 },
    api: { p95_ms: 5.1, error_rate_1m: 0.0, requests_1m: 118 },
    loop: { last_tick: tick, tick_lag: 0, ticks_skipped: 3, last_cycle_ms: 45.2, last_plan_ms: 20.1, plans_total: 340 },
    data_age_s: 0.4,
    stale: false,
  };
}

export function getMockReady(): ReadyResponse {
  return { ready: true, checks: { simulator: "ok", database: "ok", intel: mockSettings.intel_enabled ? "ok" : "disabled" } };
}

export function getMockPlanPreview(): PlanPreviewResponse {
  return {
    policy: mockSettings.intel_enabled ? "lp-v1" : "heuristic-v1",
    status: "optimal",
    solve_ms: 18.4,
    cached: false,
    actions: [{ route_id: "route-gazipur-karnaphuli", fuel_type: "PETROL", quantity: 5000 }],
    impact: [
      {
        station_id: "station-karnaphuli",
        fuel_type: "PETROL",
        p_stockout_before: 0.99,
        p_stockout_after: 0.07,
        unmet_before_liters: 2830,
        unmet_after_liters: 40,
        hours_to_stockout_before: 3.0,
        hours_to_stockout_after: null,
      },
    ],
  };
}

export function getMockForecast(stationId: string, fuelType: string): ForecastResponse {
  const tick = currentTick();
  const rnd = mulberry32(hashSeed(`${stationId}|${fuelType}`));
  const historyLen = 48;
  const horizon = 32;
  const baseLevel = 1000 + Math.floor(rnd() * 2500);

  const dayShape = (t: number): number => {
    const hourOfDay = ((t * TICK_MINUTES) / 60) % 24;
    return 0.75 + 0.35 * Math.sin(((hourOfDay - 6) / 24) * Math.PI * 2) ** 2;
  };

  const points: ForecastPoint[] = [];
  for (let idx = 0; idx <= historyLen + horizon; idx++) {
    const i = idx - historyLen; // -historyLen .. horizon
    const t = tick + i;
    const level = baseLevel * dayShape(t);
    if (i <= 0) {
      const noise = (rnd() - 0.5) * 0.16;
      const actual = Math.max(0, level * (1 + noise));
      const hasForecast = idx >= 3; // earliest few history ticks: no model yet
      const forecast = hasForecast ? Math.max(0, level * (1 + noise * 0.3)) : null;
      points.push({
        tick: t,
        actual: Math.round(actual),
        forecast: forecast === null ? null : Math.round(forecast),
        lo: null,
        hi: null,
      });
    } else {
      const forecast = Math.max(0, level);
      const sigma = Math.max(forecast * (0.1 + i * 0.004), 30);
      points.push({
        tick: t,
        actual: null,
        forecast: Math.round(forecast),
        lo: Math.round(Math.max(0, forecast - 1.28 * sigma)),
        hi: Math.round(forecast + 1.28 * sigma),
      });
    }
  }

  return {
    station_id: stationId,
    fuel_type: (fuelType as FuelType) || "DIESEL",
    wape: Math.round((0.04 + rnd() * 0.05) * 1000) / 1000,
    points,
  };
}

// ---- Recommendations (mutable so Approve/Reject visibly work in mock mode) --

let mockRecommendations: Recommendation[] = [
  {
    id: "rec-1-112-station-karnaphuli-PETROL",
    epoch: 1,
    created_tick: 112,
    deadline_tick: 120,
    status: "PENDING",
    rec_class: "CROSS_REGION",
    revision: 1,
    conditions: ["CROSS_REGION"],
    requires_approval: true,
    station_id: "station-karnaphuli",
    fuel_type: "PETROL",
    tier: "CRITICAL",
    actions: [
      {
        route_id: "route-gazipur-karnaphuli",
        source_depot_id: "depot-gazipur",
        destination_station_id: "station-karnaphuli",
        fuel_type: "PETROL",
        quantity: 5000,
        transit_ticks: 4,
        eta_tick: 116,
      },
      {
        route_id: "route-patiya-karnaphuli",
        source_depot_id: "depot-patiya",
        destination_station_id: "station-karnaphuli",
        fuel_type: "PETROL",
        quantity: 2000,
        transit_ticks: 2,
        eta_tick: 114,
      },
    ],
    impact: {
      p_stockout_before: 0.99,
      p_stockout_after: 0.07,
      unmet_before_liters: 2830,
      unmet_after_liters: 40,
      hours_to_stockout_before: 3.0,
      hours_to_stockout_after: null,
    },
    confidence: 0.82,
    confidence_label: "HIGH",
    signals: [
      "petrol demand +9% vs forecast",
      "Chattogram petrol 4.2 days of cover",
      "next Patiya petrol supply: tick 144",
    ],
    constraints: ["Patiya petrol: only 2,000 L above its reserve floor", "cross-region transit: 4 ticks"],
    explanation:
      "Karnaphuli petrol demand is running 9% above forecast after the regional demand spike. Local Patiya stock covers only part of the gap, so the plan draws the remainder from Gazipur across regions, arriving before the projected stockout.",
    policy: "lp-v1",
    decided_by: null,
    decided_at_tick: null,
    note: null,
    allocation_ids: [],
    failed_actions: [],
  },
  {
    id: "rec-2-100-station-mirpur-DIESEL",
    epoch: 1,
    created_tick: 100,
    deadline_tick: 102,
    status: "AUTO_EXECUTED",
    rec_class: "ROUTINE",
    revision: 1,
    conditions: ["ROUTINE"],
    requires_approval: false,
    station_id: "station-mirpur",
    fuel_type: "DIESEL",
    tier: "WATCH",
    actions: [
      {
        route_id: "route-gazipur-mirpur",
        source_depot_id: "depot-gazipur",
        destination_station_id: "station-mirpur",
        fuel_type: "DIESEL",
        quantity: 3000,
        transit_ticks: 2,
        eta_tick: 102,
      },
    ],
    impact: {
      p_stockout_before: 0.31,
      p_stockout_after: 0.04,
      unmet_before_liters: 0,
      unmet_after_liters: 0,
      hours_to_stockout_before: 14.0,
      hours_to_stockout_after: null,
    },
    confidence: 0.88,
    confidence_label: "HIGH",
    signals: ["routine within-region top-up"],
    constraints: [],
    explanation: "Routine within-region top-up, executed automatically under Supervised autonomy.",
    policy: "lp-v1",
    decided_by: "auto",
    decided_at_tick: 100,
    note: null,
    allocation_ids: [4021],
    failed_actions: [
      { route_id: "route-gazipur-mirpur", fuel_type: "DIESEL", quantity: 500, code: "DISPATCH_CAPACITY_EXCEEDED" },
    ],
  },
  {
    id: "rec-3-94-station-coxsbazar-OCTANE",
    epoch: 1,
    created_tick: 94,
    deadline_tick: 99,
    status: "REJECTED",
    rec_class: "RATIONING",
    revision: 2,
    conditions: ["RATIONING"],
    requires_approval: true,
    station_id: "station-coxsbazar",
    fuel_type: "OCTANE",
    tier: "HIGH",
    actions: [
      {
        route_id: "route-patiya-coxsbazar",
        source_depot_id: "depot-patiya",
        destination_station_id: "station-coxsbazar",
        fuel_type: "OCTANE",
        quantity: 900,
        transit_ticks: 3,
        eta_tick: 97,
      },
    ],
    impact: {
      p_stockout_before: 0.55,
      p_stockout_after: 0.28,
      unmet_before_liters: 300,
      unmet_after_liters: 180,
      hours_to_stockout_before: 7.0,
      hours_to_stockout_after: 10.0,
    },
    confidence: 0.6,
    confidence_label: "MEDIUM",
    signals: ["low octane supply region-wide"],
    constraints: ["no scheduled octane supply in horizon"],
    explanation:
      "No octane supply is scheduled within the horizon, so the plan can only ration what is left. Operator rejected pending a manual reallocation.",
    policy: "lp-v1",
    decided_by: "operator:duty-manager",
    decided_at_tick: 96,
    note: "Holding for the 13:00 tanker; do not ration yet.",
    allocation_ids: [],
    failed_actions: [],
  },
  {
    id: "rec-4-80-station-tongi-PETROL",
    epoch: 1,
    created_tick: 80,
    deadline_tick: 84,
    status: "EXPIRED",
    rec_class: "LOW_CONFIDENCE",
    revision: 1,
    conditions: ["LOW_CONFIDENCE"],
    requires_approval: true,
    station_id: "station-tongi",
    fuel_type: "PETROL",
    tier: "WATCH",
    actions: [
      {
        route_id: "route-gazipur-tongi",
        source_depot_id: "depot-gazipur",
        destination_station_id: "station-tongi",
        fuel_type: "PETROL",
        quantity: 1200,
        transit_ticks: 1,
        eta_tick: 82,
      },
    ],
    impact: {
      p_stockout_before: 0.24,
      p_stockout_after: 0.1,
      unmet_before_liters: 0,
      unmet_after_liters: 0,
      hours_to_stockout_before: 18.0,
      hours_to_stockout_after: null,
    },
    confidence: 0.42,
    confidence_label: "LOW",
    signals: ["forecast error above threshold after tick 76 reset"],
    constraints: [],
    explanation: "Confidence was too low for auto-execution and no operator responded before the deadline; expired.",
    policy: "lp-v1",
    decided_by: null,
    decided_at_tick: null,
    note: null,
    allocation_ids: [],
    failed_actions: [],
  },
  {
    id: "rec-5-116-station-tongi-DIESEL",
    epoch: 1,
    created_tick: 116,
    deadline_tick: 118,
    status: "EXECUTING",
    rec_class: "FALLBACK",
    revision: 1,
    conditions: ["FALLBACK", "LOW_CONFIDENCE"],
    requires_approval: false,
    station_id: "station-tongi",
    fuel_type: "DIESEL",
    tier: "HIGH",
    actions: [
      {
        route_id: "route-gazipur-tongi",
        source_depot_id: "depot-gazipur",
        destination_station_id: "station-tongi",
        fuel_type: "DIESEL",
        quantity: 1800,
        transit_ticks: 1,
        eta_tick: 117,
      },
    ],
    impact: {
      p_stockout_before: 0.58,
      p_stockout_after: 0.19,
      unmet_before_liters: 0,
      unmet_after_liters: 0,
      hours_to_stockout_before: 6.2,
      hours_to_stockout_after: null,
    },
    confidence: 0.55,
    confidence_label: "MEDIUM",
    signals: ["intel unavailable; heuristic-v1 in control"],
    constraints: [],
    explanation: "Intel is unreachable, so heuristic-v1 planned this routine top-up. Dispatch is in progress.",
    policy: "heuristic-v1",
    decided_by: "auto",
    decided_at_tick: 116,
    note: null,
    allocation_ids: [4030],
    failed_actions: [],
  },
];

export function getMockRecommendations(status?: string): Recommendation[] {
  const sorted = [...mockRecommendations].sort((a, b) => b.created_tick - a.created_tick);
  if (status) return sorted.filter((r) => r.status === status);
  return sorted.slice(0, 50);
}

export function mockApprove(id: string, operator: string, note: string, revision: number): Recommendation {
  const rec = mockRecommendations.find((r) => r.id === id);
  if (!rec) throw new ApiError("Recommendation not found", 404, "NOT_FOUND");
  if (rec.status !== "PENDING") {
    throw new ApiError(`Recommendation is ${rec.status}, not PENDING`, 409, "NOT_PENDING");
  }
  if (revision !== undefined && rec.revision !== revision) {
    throw new ApiError("The plan changed since this card was loaded", 409, "REVISION_CHANGED");
  }
  const updated: Recommendation = {
    ...rec,
    status: "EXECUTED",
    decided_by: `operator:${operator || "unknown"}`,
    decided_at_tick: currentTick(),
    note: note || null,
    allocation_ids: [9001, 9002],
  };
  mockRecommendations = mockRecommendations.map((r) => (r.id === id ? updated : r));
  return updated;
}

export function mockReject(id: string, operator: string, reason: string): Recommendation {
  const rec = mockRecommendations.find((r) => r.id === id);
  if (!rec) throw new ApiError("Recommendation not found", 404, "NOT_FOUND");
  if (rec.status !== "PENDING") {
    throw new ApiError(`Recommendation is ${rec.status}, not PENDING`, 409, "NOT_PENDING");
  }
  const updated: Recommendation = {
    ...rec,
    status: "REJECTED",
    decided_by: `operator:${operator || "unknown"}`,
    decided_at_tick: currentTick(),
    note: reason || null,
  };
  mockRecommendations = mockRecommendations.map((r) => (r.id === id ? updated : r));
  return updated;
}

// ---- Decisions --------------------------------------------------------------

export function getMockDecisions(limit = 100): Decision[] {
  const tick = currentTick();
  const now = Date.now();
  const rows: Decision[] = [
    {
      id: 9,
      epoch: 1,
      tick: tick - 1,
      wall_time: new Date(now - 1000).toISOString(),
      kind: "ALLOCATION",
      actor: "auto",
      summary: "Dispatched 3,000 L DIESEL depot-gazipur -> station-mirpur (ROUTINE)",
      recommendation_id: "rec-2-100-station-mirpur-DIESEL",
      allocation_id: 4021,
      idempotency_key: "j1-100-route-gazipur-mirpur-DIESEL-0",
      policy: "lp-v1",
      outcome: "OK",
    },
    {
      id: 8,
      epoch: 1,
      tick: tick - 6,
      wall_time: new Date(now - 6000).toISOString(),
      kind: "REJECTION",
      actor: "operator:duty-manager",
      summary: "Rejected rationing plan for Cox's Bazar OCTANE",
      recommendation_id: "rec-3-94-station-coxsbazar-OCTANE",
      allocation_id: null,
      idempotency_key: null,
      policy: "lp-v1",
      outcome: "OK",
    },
    {
      id: 7,
      epoch: 1,
      tick: tick - 10,
      wall_time: new Date(now - 10000).toISOString(),
      kind: "EVENT_INJECTED",
      actor: "operator:duty-manager",
      summary: "Game Day: demand_spike x1.8 on region-chattogram",
      recommendation_id: null,
      allocation_id: null,
      idempotency_key: null,
      policy: null,
      outcome: "OK",
    },
    {
      id: 6,
      epoch: 1,
      tick: tick - 14,
      wall_time: new Date(now - 14000).toISOString(),
      kind: "EXPIRY",
      actor: "system",
      summary: "Recommendation rec-4-80-station-tongi-PETROL expired at deadline tick 84",
      recommendation_id: "rec-4-80-station-tongi-PETROL",
      allocation_id: null,
      idempotency_key: null,
      policy: "lp-v1",
      outcome: "OK",
    },
    {
      id: 5,
      epoch: 1,
      tick: tick - 20,
      wall_time: new Date(now - 20000).toISOString(),
      kind: "CANCEL",
      actor: "system",
      summary: "Cancelled PENDING allocation on route-gazipur-tongi (guard: disruption starts this tick)",
      recommendation_id: null,
      allocation_id: 3987,
      idempotency_key: "j1-108-route-gazipur-tongi-DIESEL-0",
      policy: "lp-v1",
      outcome: "OK",
    },
    {
      id: 4,
      epoch: 1,
      tick: tick - 24,
      wall_time: new Date(now - 24000).toISOString(),
      kind: "POLICY_FALLBACK",
      actor: "system",
      summary: "Intel unavailable twice in a row; switched to heuristic-v1",
      recommendation_id: null,
      allocation_id: null,
      idempotency_key: null,
      policy: "heuristic-v1",
      outcome: "OK",
    },
    {
      id: 3,
      epoch: 1,
      tick: tick - 30,
      wall_time: new Date(now - 30000).toISOString(),
      kind: "MODE_CHANGE",
      actor: "operator:duty-manager",
      summary: "Autonomy mode changed ADVISORY -> SUPERVISED",
      recommendation_id: null,
      allocation_id: null,
      idempotency_key: null,
      policy: null,
      outcome: "OK",
    },
    {
      id: 2,
      epoch: 1,
      tick: tick - 34,
      wall_time: new Date(now - 34000).toISOString(),
      kind: "FAULT_INJECTED",
      actor: "operator:duty-manager",
      summary: "Game Day: latency fault 1500ms for 30s",
      recommendation_id: null,
      allocation_id: null,
      idempotency_key: null,
      policy: null,
      outcome: "OK",
    },
    {
      id: 1,
      epoch: 1,
      tick: tick - 40,
      wall_time: new Date(now - 40000).toISOString(),
      kind: "ALLOCATION",
      actor: "auto",
      summary: "Dispatched 4,000 L PETROL depot-gazipur -> station-karnaphuli (CROSS_REGION drill)",
      recommendation_id: null,
      allocation_id: 3900,
      idempotency_key: "j1-83-route-gazipur-karnaphuli-PETROL-0",
      policy: "lp-v1",
      outcome: "OK",
    },
  ];
  return rows.slice(0, limit);
}

// ---- Incidents ---------------------------------------------------------------

export function getMockIncidents(): Incident[] {
  const tick = currentTick();
  return [
    {
      id: "inc-4",
      epoch: 1,
      type: "DEMAND_SPIKE",
      severity: "CRITICAL",
      status: "OPEN",
      title: "Demand spike: Karnaphuli petrol +80%",
      summary:
        "Regional demand multiplier raised to 1.8x for Chattogram stations, pushing Karnaphuli petrol toward stockout.",
      opened_tick: 108,
      resolved_tick: null,
      opened_at: simTimeForTick(108),
      resolved_at: null,
      entities: ["station-karnaphuli", "region-chattogram"],
      timeline: [
        { tick: 108, text: "Demand multiplier set to 1.8x for region-chattogram" },
        { tick: 112, text: "Recommendation rec-1-112-station-karnaphuli-PETROL generated (CROSS_REGION)" },
        { tick, text: "Awaiting operator approval; deadline tick 120" },
      ],
      brief:
        "Petrol demand at Karnaphuli rose sharply after a regional spike. A cross-region shipment plan is pending approval before the projected stockout.",
    },
    {
      id: "inc-3",
      epoch: 1,
      type: "ROUTE_DISRUPTION",
      severity: "WARNING",
      status: "OPEN",
      title: "Route disrupted: Gazipur -> Tongi",
      summary: "route-gazipur-tongi is disrupted; one PENDING shipment was cancelled and refunded by the guard.",
      opened_tick: 114,
      resolved_tick: null,
      opened_at: simTimeForTick(114),
      resolved_at: null,
      entities: ["route-gazipur-tongi", "station-tongi"],
      timeline: [
        { tick: 114, text: "Route disruption started" },
        { tick: 114, text: "Guard cancelled PENDING allocation j1-108-route-gazipur-tongi-DIESEL-0 (refunded)" },
      ],
      brief: null,
    },
    {
      id: "inc-2",
      epoch: 1,
      type: "SUPPLY_SHORTFALL",
      severity: "INFO",
      status: "RESOLVED",
      title: "Patiya petrol supply delayed",
      summary: "Scheduled petrol supply at Patiya slipped from tick 86 to tick 90.",
      opened_tick: 84,
      resolved_tick: 90,
      opened_at: simTimeForTick(84),
      resolved_at: simTimeForTick(90),
      entities: ["depot-patiya"],
      timeline: [
        { tick: 84, text: "Supply delay detected (planned tick 86)" },
        { tick: 90, text: "Supply arrived; incident resolved" },
      ],
      brief: null,
    },
    {
      id: "inc-1",
      epoch: 1,
      type: "STALE_DATA",
      severity: "WARNING",
      status: "RESOLVED",
      title: "Simulator data stale for 30s",
      summary: "Game Day fault drill: stale_data for 30 seconds.",
      opened_tick: 60,
      resolved_tick: 66,
      opened_at: simTimeForTick(60),
      resolved_at: simTimeForTick(66),
      entities: [],
      timeline: [
        { tick: 60, text: "STALE_DATA flag raised; operating mode DEGRADED" },
        { tick: 66, text: "Fault cleared; operating mode returned to NORMAL" },
      ],
      brief: null,
    },
  ];
}

// ---- Settings & Game Day ------------------------------------------------------

let mockSettings: Settings = {
  autonomy_mode: "SUPERVISED",
  policy: "lp-v1",
  safety_z: 1.28,
  constrained_factor: 0.5,
  intel_enabled: true,
};

let mockFaults: ActiveFault[] = [];

export function getMockSettings(): Settings {
  return { ...mockSettings };
}

export function mockSetMode(autonomyMode: string): Settings {
  mockSettings = { ...mockSettings, autonomy_mode: autonomyMode as Settings["autonomy_mode"] };
  return { ...mockSettings };
}

export function mockGamedayEvent(payload: unknown): { status: string; event: unknown } {
  return { status: "scheduled", event: payload };
}

export function mockGamedayFault(payload: {
  type?: string;
  duration_seconds?: number;
  parameters?: Record<string, unknown>;
}): { status: string; fault: ActiveFault } {
  const fault: ActiveFault = {
    type: payload?.type ?? "unknown",
    active: true,
    duration_seconds: payload?.duration_seconds,
    parameters: payload?.parameters ?? {},
  };
  mockFaults = [...mockFaults.filter((f) => f.type !== fault.type), fault];
  return { status: "injected", fault };
}

export function mockGamedayFaultsClear(): { status: string } {
  mockFaults = [];
  return { status: "cleared" };
}

export function mockGamedayFaultsList(): ActiveFault[] {
  return [...mockFaults];
}

export function mockGamedayIntel(enabled: boolean): { intel_enabled: boolean } {
  mockSettings = { ...mockSettings, intel_enabled: enabled };
  return { intel_enabled: mockSettings.intel_enabled };
}

export function mockGamedaySimulation(
  action: "run" | "pause" | "step" | "reset",
  ticks?: number,
): { status: string } {
  if (action === "run") {
    mockWorldStatus = "RUNNING";
  } else if (action === "pause") {
    mockWorldStatus = "PAUSED";
  } else if (action === "step") {
    mockTick += Math.max(1, ticks ?? 1);
    lastAdvanceMs = Date.now();
  } else if (action === "reset") {
    mockTick = START_TICK;
    lastAdvanceMs = Date.now();
    mockWorldStatus = "RUNNING";
  }
  return { status: mockWorldStatus };
}
