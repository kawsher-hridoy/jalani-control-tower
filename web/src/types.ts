// Types mirroring the backend -> web API contract (implementation_plan.md §6).
// Keep in sync with that document; do not hard-code topology assumptions here,
// only shapes.

export type FuelType = "DIESEL" | "PETROL" | "OCTANE";
export type OperatingMode = "NORMAL" | "DEGRADED" | "SAFE_HOLD";
export type Flag =
  | "FALLBACK_POLICY"
  | "STALE_DATA"
  | "STREAM_DOWN"
  | "INTEL_DISABLED"
  | "DB_ERROR"
  | "HISTORY_GAP";
export type AutonomyMode = "ADVISORY" | "SUPERVISED" | "AUTOPILOT";
export type RiskTier = "OK" | "WATCH" | "HIGH" | "CRITICAL";
export type ConfidenceLabel = "HIGH" | "MEDIUM" | "LOW";
export type RecStatus =
  | "PENDING"
  | "APPROVED"
  | "EXECUTING"
  | "EXECUTED"
  | "AUTO_EXECUTED"
  | "REJECTED"
  | "EXPIRED"
  | "FAILED"
  | "SUPERSEDED";
export type RecClass =
  | "ROUTINE"
  | "CROSS_REGION"
  | "RATIONING"
  | "LOW_CONFIDENCE"
  | "FALLBACK";
/** Same value space as RecClass; a recommendation can carry several. */
export type RecCondition = RecClass;

export type ComponentStatus = "UP" | "DEGRADED" | "DOWN";
export type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";
export type DepotStatus = "OPEN" | "CONSTRAINED";
export type StationStatus = "OPEN" | "OUTAGE";
export type RouteStatus = "AVAILABLE" | "DISRUPTED";
export type SupplyStatus = "SCHEDULED" | "DELAYED" | "ARRIVED";
export type ShipmentStatus =
  | "PENDING"
  | "IN_TRANSIT"
  | "ARRIVED"
  | "FAILED"
  | "CANCELLED";
export type SimEventStatus = "SCHEDULED" | "ACTIVE" | "RESOLVED";
export type InstanceStatus = "RUNNING" | "PAUSED";

export type DecisionKind =
  | "ALLOCATION"
  | "CANCEL"
  | "APPROVAL"
  | "REJECTION"
  | "EXPIRY"
  | "MODE_CHANGE"
  | "EVENT_INJECTED"
  | "FAULT_INJECTED"
  | "POLICY_FALLBACK"
  | "RESET_DETECTED";

export type IncidentType =
  | "DEMAND_SPIKE"
  | "ROUTE_DISRUPTION"
  | "STATION_OUTAGE"
  | "DEPOT_CONSTRAINT"
  | "SHIPMENT_DELAY"
  | "SUPPLY_SHORTFALL"
  | "FUEL_LOSS"
  | "SIMULATOR_FAULT"
  | "INTEL_DOWN"
  | "STALE_DATA"
  | "RESET"
  | "STOCKOUT_RISK";
export type IncidentSeverity = "INFO" | "WARNING" | "CRITICAL";
export type IncidentStatus = "OPEN" | "RESOLVED";

/**
 * Fuel-keyed numeric map. Never assume all three fuels are present -- the
 * string index signature keeps dynamic `Object.keys(...)` access and generic
 * `Record<string, number>` interop type-safe without per-call casts.
 */
export interface FuelMap {
  DIESEL?: number;
  PETROL?: number;
  OCTANE?: number;
  [key: string]: number | undefined;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  [key: string]: unknown;
}

export interface ApiErrorBody {
  detail?: ApiErrorDetail;
  error?: ApiErrorDetail;
}

export interface ComponentHealth {
  name: string;
  status: ComponentStatus;
  detail?: string | null;
}

export interface SimClientStatus {
  circuit: CircuitState;
  p95_ms: number;
  error_rate_1m: number;
  last_success_age_s: number;
}

export interface ApiStatusBlock {
  p95_ms: number;
  error_rate_1m: number;
  requests_1m: number;
}

export interface LoopStatus {
  last_tick: number;
  tick_lag: number;
  ticks_skipped: number;
  last_cycle_ms: number;
  last_plan_ms: number;
  plans_total: number;
}

export interface StatusResponse {
  version: string;
  git_sha: string;
  epoch: number;
  operating_mode: OperatingMode;
  flags: Flag[];
  autonomy_mode: AutonomyMode;
  policy: string;
  world: InstanceStatus | string;
  ready: boolean;
  insecure_defaults: boolean;
  components: ComponentHealth[];
  sim_client: SimClientStatus;
  api: ApiStatusBlock;
  loop: LoopStatus;
  data_age_s: number;
  stale: boolean;
}

export interface ReadyResponse {
  ready: boolean;
  checks: Record<string, unknown>;
}

export interface Instance {
  tick: number;
  sim_time: string;
  status: InstanceStatus | string;
  tick_minutes: number;
  seed: number;
  scenario_id: string;
}

export interface FuelLossBreakdown {
  depot_overflow: number;
  station_overflow: number;
  failed_shipment: number;
}

export interface Kpis {
  service_level: number;
  served_liters: number;
  unmet_liters: number;
  fuel_lost_liters: number;
  fuel_lost: FuelLossBreakdown;
  in_transit_liters: number;
  stations_at_risk: number;
  allocations_total: number;
  allocation_failures: number;
  pending_approvals: number;
  open_incidents: number;
}

export interface Region {
  id: string;
  name: string;
  demand_factor: number;
}

export interface Depot {
  id: string;
  name: string;
  region_id: string;
  status: DepotStatus;
  dispatch_capacity_per_tick: number;
  capacity: FuelMap;
  inventory: FuelMap;
  days_of_cover: FuelMap;
}

export interface Station {
  id: string;
  name: string;
  region_id: string;
  status: StationStatus;
  demand_profile: string;
  demand_multiplier: number;
  capacity: FuelMap;
  inventory: FuelMap;
  in_transit: FuelMap;
}

export interface DisruptionWindow {
  start_tick: number;
  end_tick: number;
}

export interface Route {
  id: string;
  source_depot_id: string;
  destination_station_id: string;
  transit_ticks: number;
  max_shipment: number;
  status: RouteStatus;
  next_disruption: DisruptionWindow | null;
}

export interface Supply {
  id: string;
  depot_id: string;
  fuel_type: FuelType;
  quantity: number;
  planned_tick: number;
  actual_tick: number | null;
  status: SupplyStatus;
}

export interface Shipment {
  id: number;
  idempotency_key: string;
  source_depot_id: string;
  destination_station_id: string;
  route_id: string;
  fuel_type: FuelType;
  quantity: number;
  created_tick: number;
  departure_tick: number;
  expected_arrival_tick: number;
  actual_arrival_tick: number | null;
  status: ShipmentStatus;
  failure_reason?: string | null;
}

export interface RiskRow {
  station_id: string;
  fuel_type: FuelType;
  inventory: number;
  capacity: number;
  in_transit: number;
  demand_next_8h: number;
  hours_to_stockout: number | null;
  p_stockout_8h: number;
  tier: RiskTier;
  confidence: number;
  confidence_label: ConfidenceLabel;
  unavoidable: boolean;
  reasons: string[];
}

export interface SimEvent {
  id: number;
  type: string;
  start_tick: number;
  end_tick: number;
  status: SimEventStatus;
  parameters: Record<string, unknown>;
}

export interface StateResponse {
  epoch: number;
  instance: Instance;
  operating_mode: OperatingMode;
  flags: Flag[];
  autonomy_mode: AutonomyMode;
  stale: boolean;
  data_age_s: number;
  kpis: Kpis;
  regions: Region[];
  depots: Depot[];
  stations: Station[];
  routes: Route[];
  supply: Supply[];
  shipments: Shipment[];
  risk: RiskRow[];
  events: SimEvent[];
}

export interface ForecastPoint {
  tick: number;
  actual: number | null;
  forecast: number | null;
  lo?: number | null;
  hi?: number | null;
}

export interface ForecastResponse {
  station_id: string;
  fuel_type: FuelType;
  wape: number;
  points: ForecastPoint[];
}

export interface RecAction {
  route_id: string;
  source_depot_id: string;
  destination_station_id: string;
  fuel_type: FuelType;
  quantity: number;
  transit_ticks: number;
  eta_tick: number;
}

export interface RecImpact {
  p_stockout_before: number;
  p_stockout_after: number;
  unmet_before_liters: number;
  unmet_after_liters: number;
  hours_to_stockout_before: number | null;
  hours_to_stockout_after: number | null;
}

export interface FailedAction {
  route_id: string;
  fuel_type: FuelType;
  quantity: number;
  code: string;
}

export interface Recommendation {
  id: string;
  epoch: number;
  created_tick: number;
  deadline_tick: number;
  status: RecStatus;
  rec_class: RecClass;
  revision: number;
  conditions: RecCondition[];
  requires_approval: boolean;
  station_id: string;
  fuel_type: FuelType;
  tier: RiskTier;
  actions: RecAction[];
  impact: RecImpact;
  confidence: number;
  confidence_label: ConfidenceLabel;
  signals: string[];
  constraints: string[];
  explanation: string;
  policy: string;
  decided_by: string | null;
  decided_at_tick: number | null;
  note: string | null;
  allocation_ids: (number | string)[];
  failed_actions: FailedAction[];
}

export interface Decision {
  id: number;
  epoch: number;
  tick: number;
  wall_time: string;
  kind: DecisionKind;
  actor: string;
  summary: string;
  recommendation_id: string | null;
  allocation_id: number | string | null;
  idempotency_key: string | null;
  policy: string | null;
  outcome: string;
}

export interface IncidentTimelineEntry {
  tick: number;
  text: string;
}

export interface Incident {
  id: string;
  epoch: number;
  type: IncidentType;
  severity: IncidentSeverity;
  status: IncidentStatus;
  title: string;
  summary: string;
  opened_tick: number;
  resolved_tick: number | null;
  opened_at: string;
  resolved_at: string | null;
  entities: string[];
  timeline: IncidentTimelineEntry[];
  brief?: string | null;
}

export interface Settings {
  autonomy_mode: AutonomyMode;
  policy: string;
  safety_z: number;
  constrained_factor: number;
  intel_enabled: boolean;
}

export interface ActiveFault {
  type: string;
  active?: boolean;
  duration_seconds?: number;
  parameters?: Record<string, unknown>;
  started_at?: string;
  [key: string]: unknown;
}

export type GameDayEventType =
  | "demand_spike"
  | "route_disruption"
  | "station_outage"
  | "depot_constraint"
  | "shipment_delay"
  | "supply_shortfall";

export type GameDayFaultType =
  | "latency"
  | "unavailable"
  | "error_rate"
  | "stale_data"
  | "stream_disconnect";

export interface GameDayEventRequest {
  type: GameDayEventType;
  start_in_ticks: number;
  duration_ticks: number;
  parameters: Record<string, unknown>;
}

export interface GameDayFaultRequest {
  type: GameDayFaultType;
  duration_seconds: number;
  parameters: Record<string, unknown>;
}

export type GameDaySimulationAction = "run" | "pause" | "step" | "reset";

export interface GameDaySimulationRequest {
  action: GameDaySimulationAction;
  ticks?: number;
}

// ---- /api/plan/preview (no auth; used by k6, not by any panel yet) -----------

export interface PlanPreviewAction {
  route_id: string;
  fuel_type: FuelType;
  quantity: number;
}

export interface PlanPreviewImpact {
  station_id: string;
  fuel_type: FuelType;
  p_stockout_before: number;
  p_stockout_after: number;
  unmet_before_liters: number;
  unmet_after_liters: number;
  hours_to_stockout_before: number | null;
  hours_to_stockout_after: number | null;
}

export interface PlanPreviewResponse {
  policy: string;
  status: string;
  solve_ms: number;
  cached: boolean;
  actions: PlanPreviewAction[];
  impact: PlanPreviewImpact[];
}
