# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast product-window policies from period-extrema/sample/anchor drift.
# Reuse: Run before changing replacement local-day extraction, readiness, replay, or posterior dependencies.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement product-window policy tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.replacement_forecast_product_window import replacement_forecast_product_window_policy


UTC = timezone.utc
OPENMETEO_ECMWF_IFS9_ANCHOR_LABEL = (
    "Open-Meteo ECMWF ecmwf_ifs 9km/0.1 deterministic forecast "
    "soft spatial anchor"
)
OPENMETEO_ECMWF_IFS9_AIFS_SOFT_ANCHOR_LABEL = (
    "Open-Meteo ECMWF ecmwf_ifs 9km/0.1 deterministic forecast "
    "soft spatial anchor plus AIFS ENS sampled-2t posterior"
)


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 6, hour, tzinfo=UTC)


def _policy(label: str, metric: str = "high"):
    return replacement_forecast_product_window_policy(
        label,
        metric,
        source_cycle_time=_dt(0),
        target_window_start_utc=_dt(0),
        target_window_end_utc=datetime(2026, 6, 7, 0, tzinfo=UTC),
    )


def test_aifs_sampled_2t_window_uses_valid_time_samples_not_period_extrema() -> None:
    policy = _policy("A1", "high")

    assert policy.source_id == "ecmwf_aifs_ens"
    assert policy.data_version == "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max"
    assert policy.aggregation_window_policy == "sampled_2t_6h_local_calendar_day"
    assert policy.measurement_object == "aifs_ens_member_sampled_2t_6h"
    assert policy.expected_step_hours == (0, 6, 12, 18)
    assert policy.required_valid_time_stride_hours == 6
    assert policy.requires_member_vectors is True
    assert policy.requires_anchor_value is False
    assert policy.requires_dependency_posteriors is False
    assert policy.raw_ensemble_eligible is True
    assert policy.is_period_extrema is False


def test_ifs_ens_0p1_period_extrema_window_is_separate_from_aifs_sampled_policy() -> None:
    period = _policy("R1", "high")
    since_prev = _policy("R2", "low")

    assert period.aggregation_window_policy == "period_3h_local_calendar_day"
    assert period.expected_step_hours == tuple(range(3, 25, 3))
    assert period.required_valid_time_stride_hours == 3
    assert period.requires_member_vectors is True
    assert period.raw_ensemble_eligible is True
    assert period.is_period_extrema is True

    assert since_prev.aggregation_window_policy == "since_prev_postproc_local_calendar_day"
    assert since_prev.expected_step_hours == tuple(range(3, 25, 3))
    assert since_prev.required_valid_time_stride_hours is None
    assert since_prev.requires_member_vectors is True
    assert since_prev.is_period_extrema is True


def test_openmeteo_anchor_window_requires_anchor_value_not_member_vectors() -> None:
    policy = _policy(OPENMETEO_ECMWF_IFS9_ANCHOR_LABEL, "low")

    assert policy.source_id == "openmeteo_ecmwf_ifs_9km"
    assert policy.aggregation_window_policy == "deterministic_local_calendar_day_anchor"
    assert policy.expected_step_hours == ()
    assert policy.required_valid_time_stride_hours is None
    assert policy.requires_member_vectors is False
    assert policy.requires_anchor_value is True
    assert policy.requires_dependency_posteriors is False
    assert policy.raw_ensemble_eligible is False
    assert policy.is_period_extrema is False


def test_derived_soft_anchor_window_requires_dependencies_not_raw_steps() -> None:
    policy = _policy(OPENMETEO_ECMWF_IFS9_AIFS_SOFT_ANCHOR_LABEL, "high")

    assert policy.source_id == "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
    assert policy.aggregation_window_policy == (
        "aifs_sampled_2t_6h_plus_deterministic_anchor_local_calendar_day"
    )
    assert policy.expected_step_hours == ()
    assert policy.requires_member_vectors is False
    assert policy.requires_anchor_value is True
    assert policy.requires_dependency_posteriors is True
    assert policy.raw_ensemble_eligible is False
    assert policy.is_period_extrema is False


def test_replacement_window_policy_fails_closed_for_baseline_control_and_bad_time() -> None:
    with pytest.raises(ValueError, match="baseline products"):
        _policy("B0", "high")

    with pytest.raises(ValueError, match="no high data_version"):
        _policy("C1", "high")

    with pytest.raises(ValueError, match="timezone-aware"):
        replacement_forecast_product_window_policy(
            "A1",
            "high",
            source_cycle_time=datetime(2026, 6, 6, 0),
            target_window_start_utc=_dt(0),
            target_window_end_utc=datetime(2026, 6, 7, 0, tzinfo=UTC),
        )
