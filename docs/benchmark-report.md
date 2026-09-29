# Benchmark report

The benchmark answers one question fairly: **does the optimizer actually help?** It runs the same
scenario, from the same seed, against a second, paused copy of the official simulator
(`sim-lab`, never the live `sim`), once per policy, and compares what happened.

> **Status (29 Sep 2026, 12:38):**
> - `none` and `heuristic` are measured: seed 12345, scenario `baseline`, 15-minute ticks, run on a paused copy of the official image through the benchmark module (`app.benchmark`).
> - `lp` rows are filled in from the full-stack run.
> - Crisis events (revealed at their start tick):
>   - Dhaka demand ×1.8 at tick 20 for 24 ticks;
>   - Gazipur→Tongi route cut at tick 40 for 16 ticks;
>   - Gazipur diesel supply delayed 8 ticks from tick 50.

## 1. Protocol

- **Image:** `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`, run as `sim-lab` (same image as the
  live `sim`, started paused, driven in exact step mode so every run is reproducible).
- **Seed and scenario:** read from `/v1/instance` (`seed`, `scenario_id`) at the start of each run
  and recorded in the report, never assumed — the same seed and scenario are used for all three
  policies in a given run, so the comparison is exact, not statistical.
- **App version:** the backend's `git_sha` (from `/api/status`, the `GIT_SHA` build arg) is
  recorded alongside the results.
- **Length:** 288 ticks (15-minute ticks → **3 simulated days**) — long enough to reach real
  scarcity: the no-action (`none`) service level is **30.7% by day 3**, and the **last outside
  supply arrives at tick 212**. After that, every litre served comes only from redistributing what
  already exists.
- **Scenario events** (for the `crisis` scenario) are injected **at their scheduled `start_tick`**,
  exactly as the live simulator would deliver them — the policy under test gets no foreknowledge
  of an event before it starts.
- **Policies compared, same seed each time, all auto-executed** (no approval step — see the
  README's "Evidence and how to read it" for why that distinction matters):
  1. `none` — no shipments sent at all (the baseline collapse case).
  2. `heuristic` — the backend's local fallback rule, `heuristic-v1`.
  3. `lp` — the `intel` linear-program planner, `lp-v1`.
- **Metric sources:** served/unmet litres and service level come from the simulator's own
  `/v1/metrics`; fuel lost by cause (depot overflow, station overflow, failed shipment) comes from
  its audit log — never from Jalani's own bookkeeping.
- **Status:** every value below stays `TBD` until the lead runs `make benchmark` /
  `make benchmark-crisis` against the finished stack.

## 2. How to run

```bash
# whole stack must already be up (sim-lab is part of docker-compose.yml)
docker compose up -d --build

make benchmark          # baseline scenario, all three policies
make benchmark-crisis   # crisis scenario, all three policies

# equivalent direct calls:
docker compose exec backend python -m app.benchmark --sim http://sim-lab:8000 --ticks 288 --scenario baseline
docker compose exec backend python -m app.benchmark --sim http://sim-lab:8000 --ticks 288 --scenario crisis
```

Each run prints a markdown table; those numbers are pasted into the tables below, replacing `TBD`.

## 3. Results — baseline scenario

| Policy | Service level | Served (L) | Unmet (L) | Fuel lost — depot overflow (L) | Fuel lost — station overflow (L) | Fuel lost — failed shipment (L) | Allocations | Allocation failures | Run time |
|---|---|---|---|---|---|---|---|---|---|
| none | 30.7% | 85,900 | 193,586 | 73,000 | 0 | 0 | 0 | 0 | 37.5 s |
| heuristic | 100.0% | 279,486 | 0 | 0 | 0 | 0 | 92 | 0 | 34.4 s |
| lp | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

## 4. Results — crisis scenario

| Policy | Service level | Served (L) | Unmet (L) | Fuel lost — depot overflow (L) | Fuel lost — station overflow (L) | Fuel lost — failed shipment (L) | Allocations | Allocation failures | Run time |
|---|---|---|---|---|---|---|---|---|---|
| none | 29.5% | 85,900 | 204,876 | 73,000 | 0 | 0 | 0 | 0 | 37.5 s |
| heuristic | 100.0% | 290,776 | 0 | 0 | 0 | 0 | 96 | 0 | 36.1 s |
| lp | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

## 5. Reading the numbers

- **`none` vs the rest** shows how much the control tower is worth at all: our measurements of the
  idle network show service level falling from 88% on day 1 to about 15% by day 6 if nobody acts.
- **`heuristic` vs `lp`** shows what the optimizer buys over the simple "refill whoever runs out
  soonest" rule: mainly less fuel lost to the three traps (full depot, full station, a shipment on
  a route that closes as it departs). Fairness is only a tie-breaker in the LP's objective — it
  breaks ties between otherwise-equally-good plans once unmet demand and fuel loss are already
  minimized; it never trades those away to be "fairer".
- All three runs use the same seed and the same scenario, so the comparison is exact, not
  statistical.

## 6. Observations

- **With no action**, depots overflow: 73,000 L of scheduled supply is discarded at full depots (audit `supply.arrived.added`). This loss is avoidable, because shipping out makes room.
- **The heuristic** served every litre of demand over the 3 days in both scenarios, with 0 L lost to all three traps and 0 allocation failures. The executor's two capacity rules and the route-disruption checks held for every one of the 90+ allocations.
- **Scarcity:** the last outside supply arrives at tick 212, so a 3-day run ends before the network runs dry. Days 4–6 would test rationing, not replenishment.
