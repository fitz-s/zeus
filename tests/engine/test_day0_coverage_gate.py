# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: review5.23 P1-1 (Day0 observation coverage window proof)
"""Antibody tests for the Day0 observation coverage-window completeness gate.

review5.23 P1-1: production _fetch_wu_observation() sets coverage_status="OK"
regardless of window completeness.  _day0_observation_quality_rejection_reason()
was not checking coverage_status.

Post-fix:
  - _compute_day0_coverage_status() (pure helper in observation_client) returns
    WINDOW_INCOMPLETE when first sample arrives strictly more than
    _DAY0_COVERAGE_WINDOW_GRACE_HOURS hours after local midnight (first_hour > grace).
    At exactly grace_hours the sample is still within the window → "OK" / "LOW_COVERAGE".
  - _fetch_wu_observation() calls _compute_day0_coverage_status() and stores the result.
  - _day0_observation_quality_rejection_reason() blocks WINDOW_INCOMPLETE by
    default so evaluator rejects day0 entry candidates whose extrema cannot be
    trusted as complete local-day extrema.
  - Held-position monitor may explicitly accept WINDOW_INCOMPLETE as a
    one-sided observed bound: HIGH floor / LOW ceiling.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.config import City
from src.data.observation_client import (
    Day0ObservationContext,
    _DAY0_COVERAGE_WINDOW_GRACE_HOURS,
    _DAY0_MIN_SAMPLE_COUNT,
    _compute_day0_coverage_status,
)
from src.engine.evaluator import (
    _day0_observation_quality_rejection_reason,
    _day0_observation_source_rejection_reason,
)
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
    """P1-1: WINDOW_INCOMPLETE blocks entry quality gate by default."""

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

    def test_window_incomplete_can_pass_as_explicit_monitor_bound(self) -> None:
        """Held monitor can use incomplete-window observations as one-sided bounds.

        This does not claim full local-day extrema. It only allows the Day0
        signal to consume observed_high_so_far as a HIGH floor or low_so_far as
        a LOW ceiling while later maturity/exit gates decide authority.
        """
        obs = _make_obs(coverage_status="WINDOW_INCOMPLETE")
        reason = _day0_observation_quality_rejection_reason(
            _NYC,
            obs,
            HIGH_LOCALDAY_MAX,
            decision_time=_DECISION_TIME,
            allow_incomplete_window_bound=True,
        )
        assert reason is None

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

    def test_canonical_wu_extrema_source_does_not_require_current_temp(self) -> None:
        obs = Day0ObservationContext(
            high_so_far=89.0,
            low_so_far=73.0,
            current_temp=float("nan"),
            source="wu_icao_history",
            observation_time="2026-05-24T14:00:00+00:00",
            unit="F",
            station_id="KLGA",
            sample_count=8,
            first_sample_time="2026-05-24T05:00:00+00:00",
            last_sample_time="2026-05-24T14:00:00+00:00",
            coverage_status="OK",
            observation_available_at="2026-05-24T14:00:00+00:00",
        )

        assert _day0_observation_source_rejection_reason(_NYC, obs) is None
        assert (
            _day0_observation_quality_rejection_reason(
                _NYC, obs, HIGH_LOCALDAY_MAX, decision_time=_DECISION_TIME
            )
            is None
        )


class TestCoverageStatusConstants:
    """Verify the P1-1 constants have sensible values."""

    def test_window_grace_hours_is_positive_and_small(self) -> None:
        assert 0 < _DAY0_COVERAGE_WINDOW_GRACE_HOURS <= 4, (
            f"Grace window must be between 1 and 4 hours, got {_DAY0_COVERAGE_WINDOW_GRACE_HOURS}"
        )

    def test_min_sample_count_is_positive(self) -> None:
        assert _DAY0_MIN_SAMPLE_COUNT >= 1


class TestComputeDay0CoverageStatusBoundary:
    """Boundary tests for _compute_day0_coverage_status (pure helper).

    Pins the exact boundary semantics: first_hour STRICTLY GREATER THAN grace_hours
    triggers WINDOW_INCOMPLETE; exactly at grace_hours is still within the window.
    """

    _GRACE = _DAY0_COVERAGE_WINDOW_GRACE_HOURS
    _MIN = _DAY0_MIN_SAMPLE_COUNT

    def _dt(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 5, 24, hour, minute, 0, tzinfo=timezone.utc)

    def test_before_grace_window_is_ok(self) -> None:
        """T_P1_1e: first sample at 01:59 local → well within grace window → OK."""
        status = _compute_day0_coverage_status(self._dt(1, 59), n_samples=self._MIN)
        assert status == "OK", f"Expected OK for 01:59, got {status!r}"

    def test_exactly_at_grace_boundary_is_ok(self) -> None:
        """T_P1_1f: first sample exactly at grace_hours:00 → still within window → OK.

        The check is strictly-greater-than (not >=), so first_hour == grace_hours is OK.
        """
        status = _compute_day0_coverage_status(
            self._dt(self._GRACE, 0), n_samples=self._MIN
        )
        assert status == "OK", (
            f"Expected OK at exactly {self._GRACE}:00 (within grace window), got {status!r}"
        )

    def test_one_minute_past_grace_is_window_incomplete(self) -> None:
        """T_P1_1g: first sample at grace_hours:01 → strictly past grace → WINDOW_INCOMPLETE."""
        status = _compute_day0_coverage_status(
            self._dt(self._GRACE, 1), n_samples=self._MIN
        )
        assert status == "WINDOW_INCOMPLETE", (
            f"Expected WINDOW_INCOMPLETE at {self._GRACE}:01, got {status!r}"
        )

    def test_mid_morning_is_window_incomplete(self) -> None:
        """T_P1_1h: first sample at 08:00 → clearly outside window → WINDOW_INCOMPLETE."""
        status = _compute_day0_coverage_status(self._dt(8, 0), n_samples=self._MIN)
        assert status == "WINDOW_INCOMPLETE"

    def test_within_window_but_few_samples_is_low_coverage(self) -> None:
        """T_P1_1i: early first sample but too few → LOW_COVERAGE (not WINDOW_INCOMPLETE)."""
        status = _compute_day0_coverage_status(
            self._dt(1, 0), n_samples=self._MIN - 1
        )
        assert status == "LOW_COVERAGE", (
            f"Expected LOW_COVERAGE for {self._MIN - 1} samples, got {status!r}"
        )


def _make_gap_obs(
    *,
    gap_suspect_metrics=("high",),
    max_gap_minutes: float = 240.0,
    **overrides,
) -> Day0ObservationContext:
    base = dict(
        coverage_status="GAP_SUSPECT",
        high_so_far=65.0,
        low_so_far=55.0,
        current_temp=63.0,
        observation_time="2026-05-24T14:00:00+00:00",
        sample_count=8,
    )
    base.update(overrides)
    obs = _make_obs(**base)
    # dataclass is frozen+slots: rebuild with the gap fields.
    return Day0ObservationContext(
        high_so_far=obs.high_so_far,
        low_so_far=obs.low_so_far,
        current_temp=obs.current_temp,
        source=obs.source,
        observation_time=obs.observation_time,
        unit=obs.unit,
        station_id=obs.station_id,
        sample_count=obs.sample_count,
        first_sample_time=obs.first_sample_time,
        last_sample_time=obs.last_sample_time,
        coverage_status=obs.coverage_status,
        observation_available_at=obs.observation_available_at,
        max_gap_minutes=max_gap_minutes,
        gap_suspect_metrics=tuple(gap_suspect_metrics) if gap_suspect_metrics is not None else None,
    )


class TestGapSuspectEntryGate:
    """M-2/H-3: GAP_SUSPECT fails the ENTRY quality gate closed for the
    attributed metric only; the monitor bound-only escape hatch still serves."""

    def test_gap_suspect_high_blocks_high_entry(self) -> None:
        obs = _make_gap_obs(gap_suspect_metrics=("high",))
        reason = _day0_observation_quality_rejection_reason(
            _NYC, obs, HIGH_LOCALDAY_MAX, decision_time=_DECISION_TIME
        )
        assert reason is not None
        assert "gap-suspect" in reason

    def test_gap_suspect_high_does_not_block_low_entry(self) -> None:
        """Metric attribution: an afternoon hole must not block a LOW market."""
        obs = _make_gap_obs(gap_suspect_metrics=("high",))
        reason = _day0_observation_quality_rejection_reason(
            _NYC, obs, LOW_LOCALDAY_MIN, decision_time=_DECISION_TIME
        )
        if reason is not None:
            assert "gap-suspect" not in reason

    def test_gap_suspect_low_blocks_low_entry(self) -> None:
        obs = _make_gap_obs(gap_suspect_metrics=("low",))
        reason = _day0_observation_quality_rejection_reason(
            _NYC, obs, LOW_LOCALDAY_MIN, decision_time=_DECISION_TIME
        )
        assert reason is not None
        assert "gap-suspect" in reason

    def test_gap_suspect_without_attribution_fails_closed_for_all_metrics(self) -> None:
        """A GAP_SUSPECT status whose producer did not attribute metrics
        (gap_suspect_metrics=None) must block every metric."""
        obs = _make_gap_obs(gap_suspect_metrics=None)
        for metric in (HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN):
            reason = _day0_observation_quality_rejection_reason(
                _NYC, obs, metric, decision_time=_DECISION_TIME
            )
            assert reason is not None and "gap-suspect" in reason

    def test_gap_suspect_passes_as_explicit_monitor_bound(self) -> None:
        """Monitor path (allow_incomplete_window_bound=True): serve bound-only."""
        obs = _make_gap_obs(gap_suspect_metrics=("high",))
        reason = _day0_observation_quality_rejection_reason(
            _NYC,
            obs,
            HIGH_LOCALDAY_MAX,
            decision_time=_DECISION_TIME,
            allow_incomplete_window_bound=True,
        )
        assert reason is None
