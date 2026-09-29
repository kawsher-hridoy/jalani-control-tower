"""forecast.py unit tests."""
from __future__ import annotations

from app import forecast as fc


def test_industrial_peak_forecast_exceeds_off_peak():
    tick_minutes = 15
    # 08:00 is within industrial's busy window (06-17); 22:00 is off-peak.
    peak_tick = (8 * 60) // tick_minutes
    off_tick = (22 * 60) // tick_minutes
    assert fc.hour_of_day(peak_tick, tick_minutes) == 8
    assert fc.hour_of_day(off_tick, tick_minutes) == 22

    fit = fc.fit_series([], [], tick_minutes, "industrial", "DIESEL")
    peak = fit.mean(peak_tick, tick_minutes, "industrial")
    off = fit.mean(off_tick, tick_minutes, "industrial")
    assert peak > off
    assert peak / off == fc.shape_factor("industrial", 8) / fc.shape_factor("industrial", 22)


def test_short_history_falls_back_to_prior_level():
    fit = fc.fit_series([1, 2, 3], [100.0, 110.0, 90.0], 15, "urban_high", "DIESEL")
    prior = fc.prior_level_per_tick("urban_high", "DIESEL", 15)
    assert fit.level == prior
    assert fit.wape == 0.0


def test_empty_history_prior_differs_by_fuel_type():
    # guide Sec 8.5: daily litres differ per profile AND per fuel. With no
    # history, req.fuel_type must select the right prior instead of a single
    # fuel-agnostic number for everything.
    tick_minutes = 15
    profile = "urban_high"
    diesel = fc.fit_series([], [], tick_minutes, profile, "DIESEL")
    petrol = fc.fit_series([], [], tick_minutes, profile, "PETROL")
    octane = fc.fit_series([], [], tick_minutes, profile, "OCTANE")
    assert len({diesel.level, petrol.level, octane.level}) == 3
    # urban_high: PETROL 10500, DIESEL 8500, OCTANE 5600 L/day.
    assert petrol.level > diesel.level > octane.level

    # fuel=None (contract allows it) falls back to a fuel-agnostic average,
    # distinct from any single fuel's own prior.
    unknown_fuel = fc.fit_series([], [], tick_minutes, profile, None)
    assert unknown_fuel.level not in (diesel.level, petrol.level, octane.level)


def test_forecast_rescales_correctly_at_spike_start():
    """A spike just started: the last 2 raw observations already show the
    higher (multiplier=1.8) demand. The next forecast must land near
    180*shape, NOT ~324 (the old bug's double multiplication)."""
    tick_minutes = 15
    profile = "flat"  # unrecognized profile -> shape_factor is 1.0 everywhere
    n_base = 40
    ticks = list(range(0, n_base)) + [n_base, n_base + 1]
    values = [100.0] * n_base + [180.0, 180.0]
    multipliers = [1.0] * n_base + [1.8, 1.8]
    future_multipliers = [1.8] * 10
    now = ticks[-1]

    fit = fc.fit_series(
        ticks, values, tick_minutes, profile, "DIESEL",
        demand_multiplier=1.8, multipliers=multipliers,
        future_multipliers=future_multipliers, tick=now,
    )
    forecast = fit.mean(now + 1, tick_minutes, profile)
    assert abs(forecast - 180.0) <= 0.15 * 180.0
    assert abs(forecast - 324.0) > 0.15 * 324.0


def test_forecast_rescales_correctly_at_spike_end():
    """A spike (multiplier=1.8) just ended: future_multipliers reverts to
    1.0. Because history was normalized by the multiplier that was actually
    in effect at observation time, the underlying level already tracks the
    notional (multiplier-free) ~100 demand, so the forecast returns to
    ~100*shape immediately -- not stuck near the raw 180 average."""
    tick_minutes = 15
    profile = "flat"
    n_base = 40
    ticks = list(range(0, n_base))
    values = [180.0] * n_base
    multipliers = [1.8] * n_base
    future_multipliers = [1.0] * 10
    now = ticks[-1]

    fit = fc.fit_series(
        ticks, values, tick_minutes, profile, "DIESEL",
        demand_multiplier=1.0, multipliers=multipliers,
        future_multipliers=future_multipliers, tick=now,
    )
    forecast = fit.mean(now + 1, tick_minutes, profile)
    assert abs(forecast - 100.0) <= 0.15 * 100.0


def test_forecast_points_are_never_negative_at_lo():
    fit = fc.fit_series([], [], 15, "regional", "OCTANE")
    points = fc.forecast_points(fit, 100, 15, "regional", horizon=8)
    assert len(points) == 8
    assert all(p["lo"] >= 0.0 for p in points)
    assert all(p["hi"] > p["forecast"] > p["lo"] or p["lo"] == 0.0 for p in points)
    assert [p["tick"] for p in points] == list(range(101, 109))
