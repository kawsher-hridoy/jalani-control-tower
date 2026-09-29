"""Demand forecaster: hour-of-day shape prior + EWMA-learned level.

Reference: implementation_plan.md §12.1, Final-Project.md §4.1,
integration guide §8.5-8.6.

    D_hat[s,f,t] = level[s,f] * shape[profile, hour_of_day(t)] * m_future(t)

`level` is an EWMA of observed demand normalized by shape(hour) AND by the
demand_multiplier that was actually in effect at the moment of each
observation (`obs_level = v / (shape * m_t)`). This keeps `level` tracking the
station's notional (multiplier-free) demand intensity at all times, including
mid-spike and right after a spike ends, so no separate "has the EWMA caught up
yet" heuristic is needed: the multiplier is divided out at observation time
and re-applied (as the *expected* future multiplier) only once, when
forecasting.

For a future tick, the multiplier applied is `m_future(t)`:
  - `future_multipliers[t - tick - 1]` if the caller supplied that list
    (index k-1 = the multiplier expected at tick+k, for k=1..len; past the end
    of the list, its last value is reused) -- `tick` here is the reference
    "now" tick (`ref_tick` on `SeriesFit`);
  - otherwise the current `demand_multiplier`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

ALPHA = 0.1
SIGMA_FLOOR_FRAC = 0.10
WAPE_WINDOW = 48

FUEL_TYPES = ("DIESEL", "PETROL", "OCTANE")

# guide §8.5: daily litres per demand profile.
PRIOR_DAILY_LITERS: dict[str, dict[str, float]] = {
    "urban_high": {"DIESEL": 8500.0, "PETROL": 10500.0, "OCTANE": 5600.0},
    "industrial": {"DIESEL": 14000.0, "PETROL": 4500.0, "OCTANE": 2200.0},
    "highway": {"DIESEL": 10500.0, "PETROL": 11000.0, "OCTANE": 6200.0},
    "regional": {"DIESEL": 7200.0, "PETROL": 7600.0, "OCTANE": 3600.0},
}
# Fallback for a profile/fuel this table doesn't know: the mean daily litres
# across the four documented profiles for that fuel. The fixed world never
# needs this, but the contract asks for tolerant handling of unknown profiles.
_FALLBACK_DAILY: dict[str, float] = {
    fuel: sum(p.get(fuel, 0.0) for p in PRIOR_DAILY_LITERS.values()) / len(PRIOR_DAILY_LITERS)
    for fuel in FUEL_TYPES
}


def hour_of_day(tick: int, tick_minutes: int) -> int:
    return ((tick * tick_minutes) // 60) % 24


def shape_factor(profile: str, hour: int) -> float:
    """guide §8.6 hour-of-day factors (inclusive integer hour ranges)."""
    if profile == "industrial":
        return 1.55 if 6 <= hour <= 17 else 0.45
    if profile == "highway":
        return 1.35 if (6 <= hour <= 9 or 16 <= hour <= 20) else 0.75
    if profile == "urban_high":
        return 1.45 if (7 <= hour <= 9 or 16 <= hour <= 20) else 0.70
    if profile == "regional":
        return 1.25 if 7 <= hour <= 20 else 0.65
    return 1.0


def prior_level_per_tick(profile: str, fuel: str | None, tick_minutes: int) -> float:
    """guide §8.5: daily litres -> per-tick level = daily * tick_minutes / 1440.

    `fuel=None` (the /v1/forecast contract's fuel_type is optional) falls back
    to the mean daily litres across the three fuels for this profile, since we
    genuinely don't know which fuel this series is. This only matters for the
    cold-start case (< 4 history points); with real history the EWMA quickly
    dominates the prior regardless. When `fuel` IS given, the priors differ by
    fuel (e.g. urban_high: DIESEL 8500, PETROL 10500, OCTANE 5600 L/day).
    """
    table = PRIOR_DAILY_LITERS.get(profile)
    if fuel is None:
        source = table or _FALLBACK_DAILY
        daily = sum(source.values()) / len(source)
    else:
        daily = (table or {}).get(fuel, _FALLBACK_DAILY.get(fuel, 8000.0))
    return daily * tick_minutes / 1440.0


@dataclass
class SeriesFit:
    level: float
    sigma: float
    wape: float
    n_points: int
    ref_tick: int = 0
    demand_multiplier: float = 1.0
    future_multipliers: list[float] | None = None
    # Prequential (one-step-ahead) forecast made BEFORE each observation was
    # absorbed into the level, for the last WAPE_WINDOW observed ticks:
    # [{"tick": t, "actual": v, "forecast": level_at_the_time * shape * m_t}].
    history: list[dict] = field(default_factory=list)

    def _multiplier_at(self, t: int) -> float:
        if self.future_multipliers:
            k = t - self.ref_tick
            if k <= 0:
                return self.demand_multiplier
            idx = min(k - 1, len(self.future_multipliers) - 1)
            return self.future_multipliers[idx]
        return self.demand_multiplier

    def mean(self, tick: int, tick_minutes: int, profile: str) -> float:
        hour = hour_of_day(tick, tick_minutes)
        return self.level * shape_factor(profile, hour) * self._multiplier_at(tick)


def fit_series(
    ticks: list[int],
    values: list[float],
    tick_minutes: int,
    profile: str,
    fuel: str | None,
    demand_multiplier: float = 1.0,
    multipliers: list[float] | None = None,
    future_multipliers: list[float] | None = None,
    tick: int = 0,
) -> SeriesFit:
    """Fit level/sigma/WAPE, normalizing each observation by the multiplier
    that was actually in effect AT OBSERVATION TIME (`multipliers[i]`, aligned
    with `ticks`/`values`; if `multipliers` is missing, `demand_multiplier` is
    used for every point). `tick` is the reference "now" tick used to align
    `future_multipliers` for later `.mean()` calls.
    """
    prior = prior_level_per_tick(profile, fuel, tick_minutes)
    sigma_floor = max(SIGMA_FLOOR_FRAC * prior, 1e-6)

    if not values or len(values) < 4:
        return SeriesFit(
            level=prior, sigma=sigma_floor, wape=0.0, n_points=len(values),
            ref_tick=tick, demand_multiplier=demand_multiplier,
            future_multipliers=future_multipliers,
        )

    if multipliers is not None and len(multipliers) == len(values):
        triples = sorted(zip(ticks, values, multipliers), key=lambda p: p[0])
    else:
        triples = sorted(
            ((t, v, demand_multiplier) for t, v in zip(ticks, values)),
            key=lambda p: p[0],
        )

    level = prior
    sigma2 = sigma_floor * sigma_floor
    window_ticks = {t for t, _, _ in triples[-WAPE_WINDOW:]}
    abs_err_sum = 0.0
    abs_actual_sum = 0.0
    history: list[dict] = []

    for t, v, m_t in triples:
        hour = hour_of_day(t, tick_minutes)
        s = shape_factor(profile, hour)
        # One-step-ahead forecast made with the multiplier of THIS
        # observation, before `level` absorbs it -- the genuine prequential
        # residual (also what /v1/forecast reports back as `history`).
        one_tick_forecast = level * s * m_t
        resid = v - one_tick_forecast
        sigma2 = ALPHA * resid * resid + (1 - ALPHA) * sigma2
        if t in window_ticks:
            abs_err_sum += abs(resid)
            abs_actual_sum += abs(v)
            history.append({"tick": t, "actual": v, "forecast": one_tick_forecast})
        denom = s * m_t
        obs_level = v / denom if denom > 1e-9 else v
        level = ALPHA * obs_level + (1 - ALPHA) * level

    sigma = max(math.sqrt(sigma2), SIGMA_FLOOR_FRAC * max(level, 1e-9))
    wape = abs_err_sum / abs_actual_sum if abs_actual_sum > 1e-9 else 0.0

    return SeriesFit(
        level=level, sigma=sigma, wape=wape, n_points=len(values),
        ref_tick=tick, demand_multiplier=demand_multiplier,
        future_multipliers=future_multipliers, history=history,
    )


def forecast_points(
    fit: SeriesFit,
    tick: int,
    tick_minutes: int,
    profile: str,
    horizon: int,
    z: float = 1.28,
) -> list[dict]:
    """Next `horizon` points strictly after `tick` (tick+1 .. tick+horizon)."""
    points = []
    for k in range(1, horizon + 1):
        t = tick + k
        mean = fit.mean(t, tick_minutes, profile)
        sd = fit.sigma
        points.append({
            "tick": t,
            "forecast": mean,
            "lo": max(0.0, mean - z * sd),
            "hi": mean + z * sd,
        })
    return points


def series_key(station_id: str, fuel_type: str) -> str:
    return f"{station_id}|{fuel_type}"
