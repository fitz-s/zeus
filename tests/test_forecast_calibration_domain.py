# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: may4math ForecastCalibrationDomain Phase 2.5 base contract
# Lifecycle: created=2026-05-04; last_reviewed=2026-05-04; last_reused=2026-05-04
# Purpose: Protect forecast calibration domain identity before live calibration wiring consumes it.
# Reuse: Confirm source/cycle/metric/domain fields still match may4math Phase 2.5 before extending integration.
"""Forecast calibration domain identity tests."""

from __future__ import annotations

import pytest

from src.calibration.forecast_calibration_domain import (
    ForecastCalibrationDomain,
    ForecastCalibrationDomainMismatch,
)


def _domain(**overrides: object) -> ForecastCalibrationDomain:
    values = {
        "source_id": "ecmwf_open_data",
        "data_version": "ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
        "source_cycle_hour_utc": 0,
        "horizon_profile": "full",
        "metric": "high",
        "cluster": "Tokyo",
        "season": "MAM",
        "input_space": "width_normalized_density",
        "city_local_cycle_hour": 9,
    }
    values.update(overrides)
    return ForecastCalibrationDomain(**values)  # type: ignore[arg-type]


def test_domain_key_includes_source_cycle_and_local_hour() -> None:
    domain = _domain(source_cycle_hour_utc=12, city_local_cycle_hour=21)

    assert "cycle12z" in domain.key
    assert "local21" in domain.key
    assert "ecmwf_open_data" in domain.key


def test_00z_and_12z_domains_do_not_match() -> None:
    forecast = _domain(source_cycle_hour_utc=12)
    calibrator = _domain(source_cycle_hour_utc=0)

    assert forecast.mismatch_fields(calibrator) == ("source_cycle_hour_utc",)
    with pytest.raises(ForecastCalibrationDomainMismatch, match="CALIBRATION_DOMAIN_MISMATCH"):
        forecast.assert_matches(calibrator)


def test_metric_mismatch_is_explicit() -> None:
    high = _domain(metric="high")
    low = _domain(metric="low")

    assert high.mismatch_fields(low) == ("metric",)


def test_invalid_cycle_hour_rejected() -> None:
    with pytest.raises(ValueError, match="source_cycle_hour_utc"):
        _domain(source_cycle_hour_utc=24)
