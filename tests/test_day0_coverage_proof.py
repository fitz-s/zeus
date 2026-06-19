# Created: 2026-06-17
# Authority basis: operator delta-package v2 (real_upgrade #2) — Day0CoverageProof.
"""Contract tests for the richer Day0 coverage proof (max-gap, cadence, through-decision, DST)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.data.day0_coverage_proof import (
    Day0CoverageProof,
    compute_day0_coverage_proof,
    coverage_proof_from_first_sample,
    dst_day_length_hours,
)

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
D = date(2026, 6, 15)  # ordinary 24h day, no DST transition


def _local(h: int, m: int = 0) -> datetime:
    return datetime(2026, 6, 15, h, m, tzinfo=NY)


def _hourly_samples(start_local: datetime, n: int) -> list[datetime]:
    return [(start_local + timedelta(hours=i)).astimezone(UTC) for i in range(n)]


def test_full_through_decision() -> None:
    samples = _hourly_samples(_local(0, 30), 15)  # 00:30 .. 14:30 hourly
    proof = compute_day0_coverage_proof(
        target_local_date=D, tz=NY,
        decision_time_utc=_local(14, 45).astimezone(UTC),
        first_sample_local=_local(0, 30),
        last_sample_utc=samples[-1],
        sample_count=len(samples),
        sample_times_utc=samples,
        proof_source="aviationweather_metar",
    )
    assert proof.status == "FULL_THROUGH_DECISION"
    assert proof.is_full_through_decision
    assert proof.max_gap_minutes == 60.0
    assert proof.dst_day_length_hours == 24.0


def test_interior_gap_is_gap_incomplete() -> None:
    # 00:30..05:30 hourly, then a 4h hole, then 09:30..11:30 hourly
    times = _hourly_samples(_local(0, 30), 6) + _hourly_samples(_local(9, 30), 3)
    proof = compute_day0_coverage_proof(
        target_local_date=D, tz=NY,
        decision_time_utc=_local(11, 45).astimezone(UTC),
        first_sample_local=_local(0, 30),
        last_sample_utc=times[-1],
        sample_count=len(times),
        sample_times_utc=times,
    )
    assert proof.status == "GAP_INCOMPLETE"
    assert proof.max_gap_minutes == 240.0


def test_late_first_sample_is_window_incomplete() -> None:
    samples = _hourly_samples(_local(3, 0), 10)  # first sample 03:00 > 2h grace
    proof = compute_day0_coverage_proof(
        target_local_date=D, tz=NY,
        decision_time_utc=_local(12, 0).astimezone(UTC),
        first_sample_local=_local(3, 0),
        last_sample_utc=samples[-1],
        sample_count=len(samples),
        sample_times_utc=samples,
    )
    assert proof.status == "WINDOW_INCOMPLETE"


def test_too_few_samples_is_low_coverage() -> None:
    samples = _hourly_samples(_local(0, 30), 2)  # count 2 < min 4
    proof = compute_day0_coverage_proof(
        target_local_date=D, tz=NY,
        decision_time_utc=_local(2, 0).astimezone(UTC),
        first_sample_local=_local(0, 30),
        last_sample_utc=samples[-1],
        sample_count=2,
        sample_times_utc=samples,
    )
    assert proof.status == "LOW_COVERAGE"


def test_stale_tail_blocks_full() -> None:
    # gapless coverage but the last sample is hours before the decision -> not through-decision
    samples = _hourly_samples(_local(0, 30), 11)  # ends 10:30
    proof = compute_day0_coverage_proof(
        target_local_date=D, tz=NY,
        decision_time_utc=_local(16, 0).astimezone(UTC),  # 5.5h after last sample
        first_sample_local=_local(0, 30),
        last_sample_utc=samples[-1],
        sample_count=len(samples),
        sample_times_utc=samples,
    )
    assert proof.status == "GAP_INCOMPLETE"


def test_weak_constructor_never_full_through_decision() -> None:
    proof = coverage_proof_from_first_sample(
        _local(0, 30), 10, target_local_date=D, tz=NY,
    )
    assert proof.status != "FULL_THROUGH_DECISION"
    assert not proof.is_full_through_decision
    assert proof.max_gap_minutes is None
    assert proof.coverage_through_utc is None


def test_dst_day_lengths() -> None:
    assert dst_day_length_hours(date(2026, 6, 15), NY) == 24.0
    assert dst_day_length_hours(date(2025, 11, 2), NY) == 25.0   # fall-back
    assert dst_day_length_hours(date(2025, 3, 9), NY) == 23.0    # spring-forward
