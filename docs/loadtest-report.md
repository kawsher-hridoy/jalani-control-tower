# Load test report

Jalani Control Tower is tested with [k6](https://k6.io) running in Docker, against the live stack
(`docker compose up -d --build`). Two scripts cover the two paths that matter most: reading the
dashboard, and computing a plan.

> **Status:** this is the report template used during the build. The tables below are filled in
> with real numbers from a run against the finished stack; cells not yet measured say `TBD`.

## 1. Workload definitions

### 1.1 Dashboard read path — `loadtest/k6/dashboard.js`

Simulates operators and judges with the UI open, each polling the same three endpoints the web
app polls in a loop.

| Setting | Value |
|---|---|
| Executor | `ramping-vus` |
| Stages | 0 → 50 VUs (30 s) → 100 VUs (60 s) → 200 VUs (60 s) → 0 (20 s) |
| Requests per iteration | `GET /api/state`, `GET /api/status`, `GET /api/recommendations`, then 1 s think time |
| Checks | HTTP status is 200 |
| Thresholds | `http_req_failed` rate < 1%, `http_req_duration` p95 < 300 ms |
| Target | `BASE_URL` (default `http://localhost:8080`, the web/nginx port) |

### 1.2 Decision path — `loadtest/k6/decision.js`

Simulates concurrent plan requests, independent of response time, to find the planning path's
real capacity.

| Setting | Value |
|---|---|
| Executor | `ramping-arrival-rate` |
| Stages | 2 rps → 10 rps (40 s) → 20 rps (40 s) → hold 20 rps (40 s), about 2 minutes total |
| Request | `POST /api/plan/preview` with body `{}` and `Content-Type: application/json` |
| Checks | HTTP status is 200 |
| Thresholds | `http_req_duration` p95 < 1000 ms, custom `errors` rate < 2% |
| Target | `BASE_URL` (default `http://localhost:8080`, proxied to the backend) |

Both scripts write a JSON summary (`loadtest/results/dashboard-summary.json`,
`loadtest/results/decision-summary.json`) with avg/p50/p90/p95/p99/max latency, requests per
second and error rate, plus a short text report on stdout.

## 2. How to run

```bash
# whole stack must already be up
docker compose up -d --build

# dashboard read path
make loadtest
# equivalent to:
docker run --rm --network host -v "$PWD/loadtest:/loadtest" grafana/k6:0.54.0 run /loadtest/k6/dashboard.js

# decision path
make loadtest-decision
# equivalent to:
docker run --rm --network host -v "$PWD/loadtest:/loadtest" grafana/k6:0.54.0 run /loadtest/k6/decision.js
```

`--network host` lets the k6 container reach `localhost:8080` on the host, where `web` (nginx) is
published. The k6 image writes `handleSummary()` output as a non-root user, so if you run the raw
`docker run` command instead of `make loadtest`/`make loadtest-decision`, first run
`chmod 777 loadtest/results` once (the `make` targets already do this).

CPU and memory during the run are read from the Grafana **Infrastructure** row (or the
Prometheus `container_cpu_usage_seconds_total` / `container_memory_usage_bytes` series for
`name=~"jalani.*"`).

## 3. Results — dashboard read path

| Metric | Value |
|---|---|
| Requests | TBD |
| Requests/sec (rps) | TBD |
| Error rate | TBD |
| Latency avg | TBD ms |
| Latency p50 | TBD ms |
| Latency p90 | TBD ms |
| Latency p95 | TBD ms (threshold: < 300 ms) |
| Latency p99 | TBD ms |
| Latency max | TBD ms |
| Backend CPU during run | TBD |
| Backend memory during run | TBD |
| Thresholds passed | TBD |

## 4. Results — decision path (`/api/plan/preview`)

| Metric | Value |
|---|---|
| Requests | TBD |
| Requests/sec (rps) | TBD |
| Error rate | TBD |
| Latency avg | TBD ms |
| Latency p50 | TBD ms |
| Latency p90 | TBD ms |
| Latency p95 | TBD ms (threshold: < 1000 ms) |
| Latency p99 | TBD ms |
| Latency max | TBD ms |
| Intel solve p95 during run (`jalani_intel_solve_seconds`) | TBD |
| Thresholds passed | TBD |

## 5. Control loop under load

The control loop runs on its own cadence, independent of the HTTP request/response cycle k6 drives
— this section checks that load on the dashboard and decision paths doesn't starve it.

**Capture command** (run before, during and after each load test):
```bash
curl -s localhost:8081/api/status | jq .loop
```

| Metric | Before | During | After |
|---|---|---|---|
| `loop.ticks_skipped` | TBD | TBD | TBD |
| `loop.tick_lag` | TBD | TBD | TBD |
| `loop.last_cycle_ms` | TBD | TBD | TBD |
| Fallback activations (`jalani_fallback_activations_total`) | TBD | TBD | TBD |

**Note:** `POST /api/plan/preview` (the endpoint `decision.js` drives) is cached per tick with
single-flight — concurrent callers for the same tick share one in-flight solve — so no matter how
many virtual users call it, load adds **at most one extra LP solve per tick** beyond what the
control loop already runs on its own.

## 6. Observations

TBD — notes on where latency grows, whether the circuit breaker or SAFE_HOLD triggered under
load, and any tuning done as a result.
