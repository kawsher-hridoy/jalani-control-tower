# Jalani Control Tower

An operations center that watches a simulated national fuel supply network, predicts stockouts, and plans and sends shipments automatically, with a human approving the decisions that matter.

[![CI](https://github.com/kawsher-hridoy/jalani-control-tower/actions/workflows/ci.yml/badge.svg)](https://github.com/kawsher-hridoy/jalani-control-tower/actions/workflows/ci.yml)

> **Simulation only.** Jalani operates a simulated fuel network provided for this competition. It does not connect to, monitor, or control any real fuel infrastructure, vehicle, or pipeline.

---

## 1. The problem

In the simulated network, fuel stations hold less than one day of stock. We measured what happens if nobody sends any fuel at all:

| End of day | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| Customers served | 88% | 46% | 31% | 23% | 19% | 15% |

Outside supply also runs out early: deliveries stop arriving around **day 2.2** of the simulation. After that, every litre has to be moved from where it is to where it's needed, or it's gone.

On top of that, we found three ways the network silently destroys fuel that aren't obvious from the brief:

1. **A full depot throws away new supply.** If nobody empties a depot in time, the next delivery that doesn't fit is lost — **73,000 litres** in our idle test.
2. **A full station throws away deliveries.** A shipment that doesn't fit in a station's tank is partly discarded on arrival.
3. **A shipment sent just as a road closes fails, and its fuel is gone.** If a route disruption starts on the same tick a truck departs, that shipment fails outright and the fuel is not refunded — unless it is cancelled first.

Jalani Control Tower exists to solve all of this: decide what to send, where, and when, avoid the three traps above, and keep working when parts of the system — the network, the optimizer, or our own services — break.

## 2. What it does

Jalani follows one loop, once per simulator tick:

| Step | What happens |
|---|---|
| **Observe** | Reads the whole network from the simulator every tick and checks the data makes sense before using it. |
| **Detect** | Notices demand spikes, road closures, station outages, late or short supply, and any fuel that was lost. |
| **Predict** | Forecasts demand per station and fuel, and turns that into hours-to-empty and a chance of running out. |
| **Decide** | An optimizer plans shipments for the next 12 hours, respecting every capacity and routing rule. |
| **Simulate** | Shows the expected effect of a decision before it is sent ("risk of stockout: 99% → 7%"). |
| **Act** | Sends shipments safely; retries never create a duplicate. |
| **Monitor** | Dashboards show the health of the network and of Jalani itself. |
| **Recover** | When something fails, Jalani drops to a safer mode and comes back on its own once the failure clears. |

## 3. Architecture

```
                     ┌──────────────┐
   operator/judge ──▶│  web         │
                     │  nginx +     │  /api/*
                     │  React (UI)  │──────────┐
                     │  :8080       │          │
                     └──────────────┘          ▼
                                        ┌────────────────────┐        plan/impact       ┌──────────────┐
                                        │  backend            │──────────────────────▶ │  intel        │
                                        │  control loop + API  │◀────────────────────── │  forecast ·   │
                                        │  + SQLite            │      risk · actions     │  risk · LP    │
                                        │  :8081 (host)         │                        │  (internal)   │
                                        └──────────┬──────────┘                        └──────────────┘
                                snapshots, SSE hints │  POST /v1/allocations, cancel
                                                     ▼
                                  ┌───────────────┐     ┌───────────────┐
                                  │  sim          │     │  sim-lab      │
                                  │  official     │     │  official     │
                                  │  simulator    │     │  image,       │
                                  │  image        │     │  paused       │
                                  │  :8000        │     │  :8001        │
                                  │  (live world) │     │  (benchmark)  │
                                  └───────────────┘     └───────────────┘

   ┌────────────┐   scrape /metrics every 5s   ┌──────────────┐
   │ backend    │─────────────────────────────▶│              │
   │ intel      │─────────────────────────────▶│  prometheus  │──▶ grafana (dashboard + alerts)
   │ cadvisor   │─────────────────────────────▶│              │
   └────────────┘                              └──────────────┘
```

- **sim** is the organizer's simulator: the live world. Jalani only ever talks to it over HTTP and never modifies its image.
- **sim-lab** is the same image, started paused, used only for the benchmark (§8) — it never touches the live world.
- **backend** owns the control loop (poll, validate, detect, guard, plan, decide, execute), the API the UI talks to, and a SQLite store for decisions, recommendations and incidents. It runs as a single uvicorn worker, so exactly one control loop instance ever writes a shipment — never two in a race.
- **intel** is stateless: demand forecasting, stockout risk, and a linear-program shipment planner. If it's down, the backend switches to a built-in fallback rule automatically.
- **web** is a single-page React app built and served by nginx, which also proxies `/api/*` to the backend.
- **prometheus** scrapes the backend, intel and cAdvisor every 5 seconds; **grafana** ships with a dashboard and alert rules already wired in.

## 4. Features

- A control loop that reads the simulator every tick, forecasts demand, and plans shipments with a linear-program optimizer — falling back automatically to a simpler rule if the optimizer is slow or unavailable.
- A one-page control room: KPI strip, network map, station risk board, recommendation cards with approve/reject, incident feed, decision history, system health panel, and a Game Day panel for injecting faults and events live.
- Three autonomy modes (Advisory, Supervised, Autopilot): routine, high-confidence refills can run themselves, while moving fuel between regions, rationing, and low-confidence calls wait for a human — each with a deadline.
- A safety guard that cancels a shipment before the road under it closes, so its fuel is refunded instead of lost — best effort while the world is running (a lost race becomes a counted fuel loss, never a hidden one); while paused, it is exact every time.
- Resilience built in from the start: a safe-hold mode when the simulator is unreachable, a degraded mode when data is stale, retries with idempotency keys, a circuit breaker, automatic polling if the live event stream drops, and an automatic switch to the fallback policy if the intelligence service goes down.
- Full observability: Prometheus metrics from every service, a provisioned Grafana dashboard, and alert rules for every failure mode above.
- Evidence instead of claims: a benchmark that replays the exact same scenario with no action, the simple rule, and the optimizer, on a second, paused copy of the simulator — plus load test reports.

## 5. Quick start

```bash
git clone https://github.com/kawsher-hridoy/jalani-control-tower.git
cd jalani-control-tower
cp .env.example .env
# edit .env and set your own OPERATOR_TOKEN, ADMIN_TOKEN and GRAFANA_ADMIN_PASSWORD
docker compose up -d --build
```

| What | URL |
|---|---|
| Control room UI | http://localhost:8080 |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Simulator console (organizer image) | http://localhost:8000/admin |
| Backend status API | http://localhost:8081/api/status |

## 6. Configuration

All settings come from environment variables (see `.env.example`); every one has a safe default.

| Variable | Default | Meaning |
|---|---|---|
| `SIMULATION_SPEED` | `2` | Simulator speed multiplier (ticks per second). |
| `TICK_MINUTES` | `15` | Simulated minutes per tick. |
| `SIMULATOR_START_MODE` | `running` | Whether the live simulator starts running or paused. |
| `OPERATOR_TOKEN` | `change-me-operator` | Required as `X-Operator-Token` to approve or reject a recommendation. |
| `ADMIN_TOKEN` | `change-me-admin` | Required as `X-Admin-Token` for mode changes and the Game Day panel (also passes operator checks). |
| `AUTONOMY_MODE` | `SUPERVISED` | Starting autonomy mode: `ADVISORY`, `SUPERVISED` or `AUTOPILOT`. |
| `PLAN_HORIZON_TICKS` | `48` | How far ahead the LP plans and commits: 48 ticks = 12 h at the default 15-minute tick. Stockout risk itself is projected further ahead, over a 24 h window, independent of this setting. |
| `SAFETY_Z` | `1.28` | Safety-stock z-score (about a 90% cover target under demand uncertainty). |
| `CONSTRAINED_FACTOR` | `0.5` | Dispatch-capacity multiplier applied to a depot marked `CONSTRAINED`. |
| `AZURE_OPENAI_ENDPOINT` / `_API_KEY` / `_DEPLOYMENT` / `_API_VERSION` | empty | Optional: plain-language explanations via Azure OpenAI. Left empty, Jalani uses template text instead. |
| `GRAFANA_ADMIN_PASSWORD` | `change-me` | Grafana admin login. |
| `WEB_PORT` / `BACKEND_PORT` / `SIM_PORT` / `SIM_LAB_PORT` / `GRAFANA_PORT` / `PROMETHEUS_PORT` | `8080` / `8081` / `8000` / `8001` / `3000` / `9090` | Host ports. |
| `GIT_SHA` | `dev` | Build identifier shown in the UI and `/api/status`. |

## 7. Operator guide

- **Access dialog:** the UI asks for the operator and admin tokens on first use (the same values as `.env`) and keeps them for the session. The operator token approves or rejects recommendations; the admin token also switches mode and drives the Game Day panel.
- **Autonomy — one table.** Every recommendation is tagged with zero or more **conditions**. All applicable conditions are evaluated together, and **the most restrictive requirement wins**:

  | Condition | Meaning |
  |---|---|
  | `CROSS_REGION` | The source depot's region differs from the destination station's region |
  | `FALLBACK` | The plan came from the local heuristic (intel down or disabled), not the LP |
  | `LOW_CONFIDENCE` | Forecast confidence is LOW, or the data behind it is stale |
  | `RATIONING` | No supply is left anywhere in the horizon and projected unmet demand is still > 0 |

  | Autonomy mode | Runs automatically | Always waits for a human |
  |---|---|---|
  | **Advisory** | nothing | everything |
  | **Supervised** (default) | a recommendation with **no conditions at all**, or a **FALLBACK-only** one that is CRITICAL and within one region | any CROSS_REGION, RATIONING or LOW_CONFIDENCE condition; a FALLBACK case that isn't CRITICAL and within-region |
  | **Autopilot** | everything except RATIONING and LOW_CONFIDENCE | RATIONING and LOW_CONFIDENCE, always |

  The **operating mode** narrows this further, underneath the table above: **SAFE_HOLD** stops every write regardless of autonomy mode or condition, and **DEGRADED** only auto-executes a CRITICAL, within-region recommendation whose sole condition (if any) is FALLBACK — everything else waits for NORMAL to return.
- **Approvals:** each recommendation waiting for a decision shows why it was proposed, its expected effect, a confidence level, and a **deadline** — the last tick it can still help. Every recommendation carries a `revision`; approving one whose revision has moved on since the operator last looked at it returns `409 REVISION_CHANGED` instead of silently executing a different plan than the one that was reviewed. Missing the deadline marks it EXPIRED automatically.
- **Game Day panel:** lets an operator inject the same faults and events the resilience table below describes (station outage, route disruption, demand spike, simulator faults, an intel outage) to see, live, how the system responds, and to clear them again afterwards.

## 8. Demo runbook (7 minutes)

Set `SIMULATION_SPEED=1` in `.env` for the live demo. The default (`2`) is tuned for unattended
operation, not a scripted walkthrough: at speed 2, 7 minutes of "Run" time is 840 ticks — about
**8.75 simulated days** — which blows straight past the point that actually matters, since the
**last outside supply arrives at tick 212**. Everything after that is pure redistribution of what
already exists, which makes a poor narrative. Either way, the demo stays under full manual
control, because the simulator (and every deadline, which is expressed in ticks) freezes with it:

1. Game Day → **Reset world**, then **Pause**.
2. Advance with **Step N** (a fixed number of ticks) or **Run** for a few seconds, then **Pause**.
3. Pause while explaining — nothing changes and nothing is written while the world is paused.
4. Approve recommendations while paused: deadlines are in **ticks**, not wall-clock time, so they
   never expire mid-explanation.

**Suggested sequence:** normal operations → Game Day demand-spike preset (Dhaka ×1.8) → approve
the cross-region recommendation it produces → Game Day route-cut preset (Tongi) → fault drill
(`error_rate` + stop `intel`) → Grafana → benchmark and k6 numbers from `docs/`.

## 9. Resilience

| # | Injection | Expected result |
|---|---|---|
| 1 | `unavailable`, 30 s | **SAFE_HOLD** within about 5 s, zero POSTs to the simulator while it's down; back to **NORMAL** within about 10 s of the fault ending |
| 2 | `error_rate` 0.5, 60 s | Retries visible in `jalani_sim_requests_total{outcome="fault"}`; **0** duplicate idempotency keys. Only GETs, allocation POSTs and allocation cancels are ever retried — event, fault and step POSTs never are |
| 3a | `latency` 1500 ms | **DEGRADED** (simulator p95 > 1 s); the circuit breaker does **not** trip — timeouts are 2 s for GET / 3 s for POST, both above 1.5 s |
| 3b | `latency` 2500 ms | Requests exceed the timeouts → circuit breaker **OPEN** → **SAFE_HOLD** |
| 4 | `stale_data` | **DEGRADED** + `STALE_DATA` flag. Stale data also adds the `LOW_CONFIDENCE` condition to affected recommendations, so they wait for a human even in Autopilot |
| 5 | `stream_disconnect` | New SSE connections are rejected, and `STREAM_DOWN` appears on the next reconnect attempt (an already-open stream can keep running). Polling continues regardless, so ticks are still processed |
| 6 | `docker compose stop intel` (or the Game Day intel toggle) | `FALLBACK_POLICY` after 2 consecutive failed calls. Intel is re-probed every 5 s, so loop time stays bounded, and the service returns to `lp-v1` after 2 consecutive successes |
| 7 | Route disruption on a route with PENDING shipments | **Paused:** the guard cancels them and the fuel is refunded, every time. **Running:** best effort — a lost race shows up as a FAILED shipment, counted as fuel loss, never hidden |
| 8 | `demand_spike`, Dhaka ×1.8 | `DEMAND_SPIKE` incident opens; the forecast scales once (normalized by the multiplier at the time it's observed, not squared); risk rises accordingly |
| — | Invalid or out-of-range data | Rejected by schema/sanity checks, an alert is raised, the last good state is kept |
| — | Simulator is reset | Tick moves backwards or the instance changes → new epoch, full re-read, incident opened |

## 10. Observability

- **Prometheus** scrapes `backend`, `intel` and `cadvisor` every 5 seconds. Metric families include HTTP traffic and latency, the simulator client (requests, latency, circuit state), the control loop (tick lag, decisions, guard actions), domain KPIs (service level, unmet litres, fuel lost by cause, stockout risk (est.) and hours-to-stockout per station/fuel), and intelligence quality (forecast WAPE, solve time).
- **Grafana** ships with one provisioned dashboard, *Jalani Control Tower*, with five rows: **Operations** (service level, losses, risk board), **Decisions & intelligence** (decision rate, approvals, fallback usage, forecast quality), **Service health** (operating mode, circuit state, latency, request rate), **Infrastructure** (container CPU and memory), and **Load test** (live k6 throughput when a test is running).
- **Alerts** (Prometheus rules) fire on: the circuit breaker opening, SAFE_HOLD, DEGRADED, the fallback policy being active, stale data, critical stockout risk, any fuel lost, a high backend error rate, high p95 latency, and the control loop falling behind the simulator.

## 11. Evidence and how to read it

Every number Jalani shows or reports is exactly one of three kinds — know which one you're looking at:

| Kind | What it means | Where it comes from |
|---|---|---|
| **(a) Measured outcome** | Something that actually happened in the simulator | `/v1/metrics` or the simulator's own audit log — service level, served/unmet litres, fuel lost by cause, allocation counts |
| **(b) Model estimate** | A projection from the forecast or risk model, not an observed fact | Labelled **"stockout risk (est.)"**, "forecast", or "projected impact" in the UI and API — e.g. a recommendation's `impact.p_stockout_before/after` |
| **(c) Benchmark counterfactual** | What would have happened under a different policy, replayed from the same seed | [`docs/benchmark-report.md`](docs/benchmark-report.md) — `none` vs `heuristic` vs `lp`, each run on the separate, paused `sim-lab` simulator |

**On the benchmark specifically:** it measures the policy running **auto-executed** — every action the heuristic or the LP proposes is applied immediately, with no approval step. That is *not* the same thing as the supervised system an operator actually runs, where CROSS_REGION, RATIONING and LOW_CONFIDENCE recommendations wait for a human before anything is sent. The benchmark answers "is the planner itself any good?", not "what does the supervised product achieve in practice" — the live demo and the resilience drills answer that second question.

- [`docs/loadtest-report.md`](docs/loadtest-report.md) — k6 results for the dashboard read path and the decision (planning) path, plus how the control loop behaves under that load: latency percentiles, throughput, error rate and loop timing.
- [`docs/benchmark-report.md`](docs/benchmark-report.md) — the same-seed, auto-executed comparison of *no action* vs the *heuristic* vs the *optimizer*, run on the paused `sim-lab` copy of the simulator.

## 12. Security

Everything here is a simulation, but the stack still runs on a real VPS with a real network
interface, so it's locked down like one.

- **Loopback by default.** `docker-compose.yml` publishes the simulator console (`8000`), the
  benchmark simulator (`8001`), the backend API (`8081`) and Prometheus (`9090`) on `127.0.0.1`
  only — reachable from the host itself, never from the internet or the LAN. Only the web UI
  (`8080`) and Grafana (`3000`, anonymous **Viewer** access) are published on all interfaces,
  because judges need to reach those two directly.
- **Docker bypasses UFW.** Docker inserts its own `iptables`/`nftables` rules ahead of UFW's, so a
  port published as `0.0.0.0:PORT` is reachable from the internet even if UFW never `allow`s it.
  The loopback binding above — not the firewall — is what actually keeps `8000` / `8001` / `8081`
  / `9090` private. UFW still matters for SSH and as a second layer.
- **Reach the private ports from a laptop over an SSH tunnel, instead of opening them:**
  ```bash
  ssh -L 8000:127.0.0.1:8000 -L 9090:127.0.0.1:9090 user@vps
  # then open http://localhost:8000/admin and http://localhost:9090 locally
  ```
- **Change the secrets before any public deploy.** `OPERATOR_TOKEN`, `ADMIN_TOKEN` and
  `GRAFANA_ADMIN_PASSWORD` ship as `change-me*` placeholders in `.env.example`. Leave any of them
  unchanged and `/api/status` reports `"insecure_defaults": true`; the UI shows a warning banner
  until every one is edited in `.env`.

## 13. Manual VPS deployment

```bash
# Ubuntu 22.04/24.04, 4 GB RAM
curl -fsSL https://get.docker.com | sh            # Docker Engine + compose plugin
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/kawsher-hridoy/jalani-control-tower.git && cd jalani-control-tower
cp .env.example .env && nano .env                  # change OPERATOR_TOKEN, ADMIN_TOKEN, GRAFANA_ADMIN_PASSWORD; add Azure keys if used
docker compose up -d --build                       # first build takes about 3–5 min
docker compose ps && curl -s localhost:8081/api/health
sudo ufw allow 22 && sudo ufw allow 8080 && sudo ufw allow 3000 && sudo ufw enable   # UI + Grafana only — never 8000/8001/8081/9090
# Need the simulator console or Prometheus from a laptop? Tunnel instead of opening a port:
#   ssh -L 8000:127.0.0.1:8000 -L 9090:127.0.0.1:9090 user@vps
# Update later: git pull && docker compose up -d --build
```

The whole stack uses about 1.2 GB of RAM, comfortably inside a 4 GB VPS.

## 14. Assumptions

- **Demand forecast starting point:** the only information Jalani uses that isn't read live from the simulator is the documented per-station demand pattern (busy/quiet hours) from the organizer's guide, used purely as a *starting prior* for the forecast. From the first tick onward, the forecast learns from live demand and adjusts itself; nothing about capacities, routes, or the supply schedule is hard-coded.
- **`CONSTRAINED_FACTOR = 0.5`:** when a depot is marked `CONSTRAINED`, Jalani treats its per-tick dispatch capacity as half of normal until the depot recovers.
- **Priorities:** stations and fuels are weighted equally by default; an operator can raise the priority of a specific station/fuel pair (for example, a hospital or an industrial contract), which increases its weight against unmet demand in the optimizer.

## 15. Tech stack

| Part | Technology |
|---|---|
| Backend (control loop + API) | Python, FastAPI, SQLite |
| Intelligence service | Python, FastAPI, SciPy (HiGHS linear programming) |
| Web UI | React, Vite, TypeScript, served by nginx |
| Monitoring | Prometheus, Grafana, cAdvisor |
| Load testing | k6 |
| CI | GitHub Actions (lint, tests, builds) |
| Deployment | Docker Compose |
| Explanations (optional) | Azure OpenAI, with a template fallback |

## 16. Repo layout

```
jalani-control-tower/
├─ backend/          FastAPI control loop, API, executor, autonomy, SQLite store, tests
├─ intel/            FastAPI forecast, risk and LP planner service, tests
├─ web/              React + Vite UI, Dockerfile, nginx config
├─ observability/
│  ├─ prometheus/    prometheus.yml, alerts.yml
│  └─ grafana/       provisioning (datasource + dashboard provider) and the dashboard JSON
├─ loadtest/k6/       k6 scripts: dashboard read path, decision path
├─ docs/              loadtest-report.md, benchmark-report.md
├─ .github/workflows/ CI pipeline
├─ docker-compose.yml
├─ .env.example
└─ Makefile
```

## License

MIT
