# Load test report

Jalani Control Tower is tested with [k6](https://k6.io) running in Docker, against the live stack
(`docker compose up -d --build`). Two scripts cover the two paths that matter most: reading the
dashboard, and computing a plan.

> **Status (29 Sep 2026):** dashboard read-path measurements are from k6 against the running stack.
> The decision-path test was not run; unmeasured cells are labelled accordingly.

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
| Requests | 22,986 |
| Requests/sec (rps) | 134.53 |
| Max VUs | 200 |
| Error rate | 0.92% (request failures; rate threshold passed) |
| Latency avg | 379.82 ms |
| Latency p50 | 245.04 ms |
| Latency p90 | 875.63 ms |
| Latency p95 | 1019.70 ms (threshold: < 300 ms, **failed**) |
| Latency p99 | 1233.84 ms |
| Latency max | 1768.16 ms |
| Backend CPU during run | not measured |
| Backend memory during run | not measured |
| Thresholds passed | **No** — latency failed; error-rate threshold passed (k6 exit 99) |

## 4. Results — decision path (`/api/plan/preview`)

| Metric | Value |
|---|---|
| Requests | not run (time) |
| Requests/sec (rps) | not run (time) |
| Error rate | not run (time) |
| Latency avg | not run (time) ms |
| Latency p50 | not run (time) ms |
| Latency p90 | not run (time) ms |
| Latency p95 | not run (time) ms (threshold: < 1000 ms) |
| Latency p99 | not run (time) ms |
| Latency max | not run (time) ms |
| Intel solve p95 during run (`jalani_intel_solve_seconds`) | not run (time) |
| Thresholds passed | not run (time) |

## 5. Control loop under load

The control loop runs on its own cadence, independent of the HTTP request/response cycle k6 drives
— this section checks the dashboard run; the decision path was not load-tested.

**Capture command** (run before, during and after each load test):
```bash
curl -s localhost:8081/api/status | python3 -c 'import json,sys; print(json.load(sys.stdin)["loop"])'
```

| Metric | Before | During | After |
|---|---|---|---|
| `loop.ticks_skipped` | 58 | 62 | 66 |
| `loop.tick_lag` | 0 | 1 | 0 |
| `loop.last_cycle_ms` | 275.2 | 570.1 | 188.8 |
| `loop.lp_gated_cells` | 8,682 | 10,124 | 10,562 |

**Note:** `POST /api/plan/preview` (the endpoint `decision.js` drives) is cached per tick with
single-flight — concurrent callers for the same tick share one in-flight solve — so no matter how
many virtual users call it, load adds **at most one extra LP solve per tick** beyond what the
control loop already runs on its own.

## 6. Observations

The before/during/after loop snapshots all reported `NORMAL lp-v1`. The during column is the sampled peak `last_cycle_ms` / `tick_lag` (sample 5 of 9, 20-second cadence); skipped ticks rose from 58 to 66 over the run. The 300 ms HTTP p95 target failed at 1019.70 ms; 0.92% request failures remained below the 1% threshold. No separate decision-path load test was run.
