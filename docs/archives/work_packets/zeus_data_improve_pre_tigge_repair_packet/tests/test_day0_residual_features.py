from __future__ import annotations

from src.signal.day0_residual_features import daylight_progress, ensemble_remaining_quantiles, obs_age_minutes



def test_daylight_progress_midday() -> None:
    value = daylight_progress(
        "2026-04-11T12:00:00-05:00",
        "2026-04-11T06:00:00-05:00",
        "2026-04-11T18:00:00-05:00",
    )
    assert value == 0.5



def test_obs_age_minutes() -> None:
    value = obs_age_minutes(
        "2026-04-11T12:00:00+00:00",
        "2026-04-11T12:07:30+00:00",
    )
    assert value == 7.5



def test_ensemble_remaining_quantiles() -> None:
    q50, q90, spread = ensemble_remaining_quantiles(70.0, [69.0, 70.0, 71.0, 74.0])
    assert q50 == 0.5
    assert q90 is not None and q90 > q50
    assert spread is not None and spread >= 0.0
