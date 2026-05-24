# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: review5.23 P1-1 (Day0 observation coverage window proof)
"""Antibody tests for the Day0 observation coverage-window completeness gate.

review5.23 P1-1: production _fetch_wu_observation() sets coverage_status="OK"
regardless of window completeness.  _day0_observation_quality_rejection_reason()
was not checking coverage_status.

Post-fix:
  - _fetch_wu_observation() computes WINDOW_INCOMPLETE when first sample is
    outside the local-day-start grace window (>= _DAY0_COVERAGE_WINDOW_GRACE_HOURS
    hours after midnight).
  - _day0_observation_quality_rejection_reason() blocks WINDOW_INCOMPLETE so
    evaluator rejects day0 candidates whose extrema cannot be trusted.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.config import City
from src.data.observation_client import (
    Day0ObservationContext,
    _DAY0_COVERAGE_WINDOW_GRACE_HOURS,
    _DAY0_MIN_SAMPLE_COUNT,
)
from src.engine.evaluator import _day0_observation_quality_rejection_reason
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


_NYC = City(
    name="NYC", lat=40.7772, lon=-73.8726,
    timezone="America/New_York", cluster="US-Northeast",
    settlement_unit="F", wu_station="KLGA",
)

_DECISION_TIME = datetime(2026, 5, 24, 15, 0, 0, tzinfo=timezone.utc)


def _make_obs(
    *,
    coverage_status: str = "OK",
    high_so_far: float = 65.0,
    low_so_far: float = 55.0,
    current_temp: float = 63.0,
    observation_time: str = "2026-05-24T14:00:00+00:00",
    sample_count: int = 8,
) -> Day0ObservationContext:
    return Day0ObservationContext(
        high_so_far=high_so_far,
        low_so_far=low_so_far,
        current_temp=current_temp,
        source="wu_api",
        observation_time=observation_time,
        unit="F",
        station_id="KLGA",
        sample_count=sample_count,
        first_sample_time="2026-05-24T05:00:00+00:00",
        last_sample_time="2026-05-24T14:00:00+00:00",
        coverage_status=coverage_status,
        observation_available_at="2026-05-24T14:00:00+00:00",
    )


class TestCoverageWindowGate:
    """P1-1: WINDOW_INCOMPLETE must block quality gate; OK must pass through."""

    def test_window_incomplete_returns_rejection(self) -> None:
        """T_P1_1a: WINDOW_INCOMPLETE coverage_status → quality rejection returned."""
        obs = _make_obs(coverage_status="WINDOW_INCOMPLETE")
        reason = _day0_observation_quality_rejection_reason(
            _NYC, obs, HIGH_LOCALDAY_MAX, decision_time=_DECISION_TIME
        )
        assert reason is not None, (
            "Expected rejection reason for WINDOW_INCOMPLETE coverage, got None"
        )
        assert "WINDOW_INCOMPLETE" in reason.upper() or "incomplete" in reason.lower(), (
            f"Rejection reason must mention incomplete window, got: {reason!r}"
        )

    def test_window_incomplete_blocks_low_metric_too(self) -> None:
        """T_P1_1b: WINDOW_INCOMPLETE blocks for low-temperature metric too."""
        obs = _make_obs(coverage_status="WINDOW_INCOMPLETE")
        reason = _day0_observation_quality_rejection_reason(
            _NYC, obs, LOW_LOCALDAY_MIN, decision_time=_DECISION_TIME
        )
        assert reason is not None

    def test_ok_coverage_passes_quality_gate(self) -> None:
        """T_P1_1c: OK coverage_status is not blocked by the window gate."""
        obs = _make_obs(coverage_status="OK")
        reason = _day0_observation_quality_rejection_reason(
            _NYC, obs, HIGH_LOCALDAY_MAX, decision_time=_DECISION_TIME
        )
        # May still be rejected for other reasons (stale, missing fields), but
        # NOT for coverage window.  If quality gate is None, the window check passed.
        if reason is not None:
            assert "incomplete" not in reason.lower() and "WINDOW_INCOMPLETE" not in reason.upper(), (
                f"OK coverage_status must not trigger window rejection, got: {reason!r}"
            )

    def test_missing_coverage_status_does_not_block(self) -> None:
        """T_P1_1d: missing/unknown coverage_status does not trigger WINDOW_INCOMPLETE block.

        Pre-existing observations that never set coverage_status must not be
        retroactively blocked — only explicit WINDOW_INCOMPLETE is gated.
        """
        obs = _make_obs(coverage_status="UNKNOWN")
        reason = _day0_observation_quality_rejection_reason(
            _NYC, obs, HIGH_LOCALDAY_MAX, decision_time=_DECISION_TIME
        )
        if reason is not None:
            assert "incomplete" not in reason.lower()


class TestCoverageStatusConstants:
    """Verify the P1-1 constants have sensible values."""

    def test_window_grace_hours_is_positive_and_small(self) -> None:
        assert 0 < _DAY0_COVERAGE_WINDOW_GRACE_HOURS <= 4, (
            f"Grace window must be between 1 and 4 hours, got {_DAY0_COVERAGE_WINDOW_GRACE_HOURS}"
        )

    def test_min_sample_count_is_positive(self) -> None:
        assert _DAY0_MIN_SAMPLE_COUNT >= 1
