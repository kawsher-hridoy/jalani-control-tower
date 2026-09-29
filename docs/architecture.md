# Architecture

Jalani Control Tower is an operations center that sits on top of the BUP Fuel Supply Simulator.
It watches the simulated network, predicts stockouts, plans shipments, and executes them, with a human
approving the consequential decisions. It talks to the simulator only over HTTP and never modifies it.

> SIMULATION: not real fuel infrastructure.

## 1. System view

Everything runs from one `docker compose up -d --build` on the `jalani` network.

```mermaid
flowchart LR
  op(["Operator / judge<br/>browser"])
  k6(["k6 load test"])
  gpt["Azure OpenAI<br/>(optional, explanations only)"]

  subgraph stack["docker compose · network jalani"]
    direction LR
    web["<b>web</b><br/>React + Vite on nginx<br/>host :8080"]
    backend["<b>backend</b><br/>FastAPI control tower<br/>loop · guard · autonomy · executor<br/>host :8081"]
    intel["<b>intel</b><br/>FastAPI<br/>forecast · risk · LP planner (HiGHS)<br/>internal :8090"]
    db[("SQLite<br/>decisions · recommendations · incidents<br/>volume jalani-data")]
    sim["<b>sim</b><br/>BUP Fuel Supply Simulator 1.0.0<br/>live world · host :8000"]
    lab["<b>sim-lab</b><br/>same image, paused<br/>benchmark only · host :8001"]
    prom["<b>prometheus</b><br/>host :9090"]
    graf["<b>grafana</b><br/>provisioned dashboard<br/>host :3000"]
    cad["<b>cadvisor</b><br/>container CPU / memory"]
  end

  op -->|"UI :8080"| web
  op -->|"dashboards :3000"| graf
  op -.->|"simulator console :8000/admin"| sim

  web -->|"/api/* proxy<br/>polls every 1–3 s"| backend
  backend -->|"poll /v1/instance every 400 ms<br/>snapshot GETs · SSE /v1/stream<br/>POST /v1/allocations"| sim
  backend -->|"Game Day<br/>/admin/events · /admin/faults"| sim
  backend -->|"POST /v1/plan<br/>1.5 s timeout"| intel
  backend -->|"write-behind, buffered on error"| db
  backend -.->|"3 s timeout,<br/>template fallback"| gpt
  backend -->|"benchmark in step mode<br/>none vs heuristic vs LP"| lab

  prom -->|"scrape /metrics every 5 s"| backend
  prom -->|"scrape"| intel
  prom -->|"scrape"| cad
  graf -->|"PromQL"| prom
  k6 -->|"GET /api/state · status · recommendations<br/>POST /api/plan/preview"| backend
  k6 -.->|"remote write results"| prom
```

| Service | Role | Exposed to the host |
|---|---|---|
| `web` | One-page control room; nginx serves the SPA and proxies `/api/` to the backend | :8080 |
| `backend` | Owns the control loop, all simulator writes, approvals, incidents and metrics | :8081 (keep closed on a VPS) |
| `intel` | Stateless decision engine: forecast, stockout risk, LP plan | none |
| `sim` | The organizer's simulator, the live world being operated | :8000 |
| `sim-lab` | A second simulator, paused, used only by the benchmark | :8001 (keep closed on a VPS) |
| `prometheus`, `grafana`, `cadvisor` | Metrics, dashboards, container resource usage | :9090, :3000 |

## 2. Control loop (one pass per simulator tick)

```mermaid
flowchart TD
  poll["Poll /v1/instance<br/>every 400 ms"] --> newtick{"New tick?"}
  newtick -- "no" --> poll
  newtick -- "yes" --> snap["Parallel snapshot<br/>stations · depots · routes · supply · events<br/>allocations · metrics · demand history · audit"]
  snap --> valid{"Valid?<br/>pydantic + invariants"}
  valid -- "no" --> degraded["Keep last good state<br/>flag STALE_DATA / DEGRADED"] --> poll
  valid -- "yes" --> epoch["Epoch check<br/>tick fell or seed changed = reset"]
  epoch --> hist["Update demand history<br/>and fuel-loss totals from audit"]
  hist --> detect["Detect: diff snapshots<br/>open / resolve incidents"]
  detect --> guard["Guard (best effort while running):<br/>cancel PENDING allocations<br/>on routes disrupted now (refund)"]
  guard --> plan{"intel healthy<br/>and enabled?"}
  plan -- "yes" --> lp["intel /v1/plan<br/>policy lp-v1"]
  plan -- "no" --> heur["Local heuristic-v1<br/>flag FALLBACK_POLICY"]
  lp --> recs
  heur --> recs
  recs["Build recommendations<br/>group by station + fuel, classify<br/>ROUTINE · CROSS_REGION · RATIONING · LOW_CONFIDENCE · FALLBACK"]
  recs --> auto{"Autonomy mode × class<br/>× operating mode"}
  auto -- "auto" --> exec
  auto -- "needs a human" --> pending["PENDING with deadline<br/>shown in the UI"]
  pending -- "operator approves" --> reval["Re-validate on the<br/>latest snapshot"] --> exec
  exec["Executor<br/>split by max_shipment · clip to simulator rules<br/>deterministic idempotency key · POST with retry"]
  exec --> record["Record decisions (SQLite)<br/>expire overdue recs · update metrics"]
  record --> poll
```

## 3. Decision engine (intel)

```mermaid
flowchart LR
  req["PlanRequest<br/>snapshot + in-transit + pending dispatch<br/>+ demand history + settings"] --> fc
  fc["Forecast<br/>level (EWMA) × hour-of-day shape × multiplier<br/>σ and WAPE"] --> risk
  risk["Risk<br/>projected stock, stockout risk (est.)<br/>tier · confidence · unavoidable"] --> lp
  lp["LP (scipy HiGHS, 48 ticks, 0.8 s limit)<br/>min unmet + fuel loss + safety gap<br/>+ worst-share fairness + transit"] --> resp
  resp["PlanResponse<br/>actions now · pipeline · risk<br/>impact before/after · binding constraints"]
```

The LP respects the simulator rules the backend measured (lead time = transit, no shipping on routes
disrupted at departure, station and depot capacity, dispatch capacity per departure tick), so its
actions are valid before the executor clips them again.

Two different horizons are in play: **stockout risk is projected 24 h ahead**, but the **LP only
plans and commits 12 h (48 ticks) ahead** within that wider view — a longer look at the danger, a
shorter one for what it actually ships. Fairness (the "worst-share" term above) enters the
objective only as a **tie-breaker**, after unmet demand and fuel loss are already minimized — it
never trades either of those away to even out the split. The backend itself runs as a single
uvicorn worker, so exactly one control loop instance ever writes a shipment; there is no multi-worker
race to guard against.

## 4. Operating modes (resilience)

```mermaid
stateDiagram-v2
  [*] --> NORMAL
  NORMAL --> DEGRADED: stale header, data age over 5 s, or sim p95 over 1 s
  DEGRADED --> NORMAL: fresh data again
  NORMAL --> SAFE_HOLD: circuit breaker open, or no snapshot for 10 s
  DEGRADED --> SAFE_HOLD: circuit breaker open, or no snapshot for 10 s
  SAFE_HOLD --> NORMAL: breaker closes and a fresh snapshot arrives
  note right of SAFE_HOLD
    No writes to the simulator.
    UI shows the last good state and its age.
  end note
  note right of DEGRADED
    Auto-execute only ROUTINE CRITICAL.
    Everything else waits for a human.
  end note
```

`FALLBACK_POLICY` is a flag, not a mode: it is set when intel fails twice in a row (or is switched off in
Game Day, re-probed every 5 s), and clears after two consecutive successful intel calls. It can be combined with any mode.

| Simulator fault | How it is detected | What the control tower does |
|---|---|---|
| `unavailable` | Consecutive failures open the circuit breaker | SAFE_HOLD, no writes, recovers automatically |
| `error_rate` | Fault responses counted per endpoint | Retries with jitter and the **same** idempotency key, so no duplicate shipments |
| `latency` 1500 ms | Simulator p95 > 1 s, still under both timeouts | DEGRADED; the circuit breaker does **not** trip |
| `latency` 2500 ms | Exceeds the 2 s GET / 3 s POST timeouts | Circuit breaker OPENs → SAFE_HOLD |
| `stale_data` | `X-Simulator-Stale` header | DEGRADED + `STALE_DATA`; also adds `LOW_CONFIDENCE` to affected recommendations |
| `stream_disconnect` | New SSE connections rejected | `STREAM_DOWN` on the next reconnect attempt; polling keeps processing ticks (an already-open stream may stay up) |
| intel down | `/v1/plan` fails twice in a row, or disabled via Game Day | `FALLBACK_POLICY`; re-probed every 5 s, returns to `lp-v1` after 2 consecutive successes |

## 5. Recommendation lifecycle (human in the loop)

```mermaid
stateDiagram-v2
  [*] --> PENDING: needs approval
  [*] --> AUTO_EXECUTED: allowed by autonomy rules
  PENDING --> APPROVED: operator approves
  APPROVED --> EXECUTED: re-validation passes, allocations created
  PENDING --> REJECTED: operator rejects with a reason
  PENDING --> EXPIRED: deadline tick passes
  PENDING --> SUPERSEDED: newer rec for the same station and fuel
  EXECUTED --> FAILED: simulator rejects the allocation
  AUTO_EXECUTED --> FAILED: simulator rejects the allocation
```

See the README, §7 "Operator guide", for the one authoritative table: conditions
(`CROSS_REGION` / `FALLBACK` / `LOW_CONFIDENCE` / `RATIONING`) evaluated per recommendation, the
autonomy mode × condition matrix, and how SAFE_HOLD/DEGRADED narrow it further. A recommendation
also carries a `revision`; approving one whose revision has since moved on returns
`409 REVISION_CHANGED` rather than executing a plan the operator never actually saw.

Every approval, rejection, expiry, mode change, Game Day action and policy fallback is written to the
decision history with its actor (`auto`, `operator:<name>` or `system`).
