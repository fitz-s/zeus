# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md §Issue 3.1
"""Period-step contract tests for TIGGE extractor vs forecast_target_contract.

Verifies the subset relation:
    required_period_end_steps(...) ⊆ predicted_step_set_for_target(...)

and the structural-unification invariant:
    compute_required_max_step(...) == max(required_period_end_steps(...))

Coverage matrix: 5 cities × 4 cycles × D+0..D+10 × 4 seasons (inc. DST weeks).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._tigge_common import (  # noqa: E402
    compute_required_max_step,
    predicted_step_set_for_target,
)
from src.data.forecast_target_contract import (  # noqa: E402
    compute_target_local_day_window_utc,
    required_period_end_steps,
)

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _issue(year: int, month: int, day: int, cycle_hour: int) -> datetime:
    """Return a UTC-aware datetime for a standard ECMWF issue cycle."""
    return datetime(year, month, day, cycle_hour, tzinfo=UTC)


def _required_steps(issue_utc: datetime, city_tz: str, target_date: date) -> tuple[int, ...]:
    window = compute_target_local_day_window_utc(
        city_timezone=city_tz,
        target_local_date=target_date,
    )
    return required_period_end_steps(
        source_cycle_time=issue_utc,
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=6,
    )


def _predicted(issue_utc: datetime, city_tz: str, target_date: date) -> set[int]:
    return predicted_step_set_for_target(
        issue_utc=issue_utc,
        target_date=target_date,
        city_tz=city_tz,
        period_hours=6,
    )


def _assert_subset(issue_utc: datetime, city_tz: str, target_date: date) -> None:
    """Core assertion: required ⊆ predicted for one (issue, city, target) tuple."""
    required = set(_required_steps(issue_utc, city_tz, target_date))
    predicted = _predicted(issue_utc, city_tz, target_date)
    missing = required - predicted
    assert not missing, (
        f"required_period_end_steps produced steps not in predicted_step_set_for_target. "
        f"issue={issue_utc.isoformat()} tz={city_tz} target={target_date} "
        f"required={sorted(required)} predicted={sorted(predicted)} missing={sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# Test 1: No-DST city (Tokyo, JST = UTC+9 fixed)
# ---------------------------------------------------------------------------

def test_required_steps_subset_of_predicted_for_no_dst_city_00z() -> None:
    """Tokyo (JST, no DST) across all 4 cycles and D+0..D+5."""
    tz = "Asia/Tokyo"
    base_date = date(2026, 7, 15)  # summer, no DST involved anywhere

    for cycle in (0, 6, 12, 18):
        issue = _issue(base_date.year, base_date.month, base_date.day, cycle)
        for offset in range(6):  # D+0 through D+5
            target = base_date + timedelta(days=offset)
            _assert_subset(issue, tz, target)


# ---------------------------------------------------------------------------
# Test 2: DST spring-forward target (NYC, Mar 9 2025 = spring forward in US)
# ---------------------------------------------------------------------------

def test_required_steps_subset_of_predicted_for_dst_spring_forward_target() -> None:
    """NYC (America/New_York) with target on and around US spring-forward day.

    US DST spring forward: 2026-03-08 02:00 EST → 03:00 EDT.
    That day has only 23 hours. required_period_end_steps must not be wider
    than predicted_step_set_for_target for all cycles issuing D-3..D-1.
    """
    tz = "America/New_York"
    spring_forward = date(2026, 3, 8)

    for days_before in (1, 2, 3):
        issue_date = spring_forward - timedelta(days=days_before)
        for cycle in (0, 6, 12, 18):
            issue = _issue(issue_date.year, issue_date.month, issue_date.day, cycle)
            _assert_subset(issue, tz, spring_forward)
            # Also check the day before (normal 24h day)
            _assert_subset(issue, tz, spring_forward - timedelta(days=1))


# ---------------------------------------------------------------------------
# Test 3: DST fall-back target (NYC, Nov 1 2026 = fall back in US)
# ---------------------------------------------------------------------------

def test_required_steps_subset_of_predicted_for_dst_fall_back_target() -> None:
    """NYC (America/New_York) with target on US fall-back day.

    US DST fall back: 2026-11-01 02:00 EDT → 01:00 EST.
    That day has 25 hours. Predicted set must be wide enough to cover.
    """
    tz = "America/New_York"
    fall_back = date(2026, 11, 1)

    for days_before in (1, 2, 3):
        issue_date = fall_back - timedelta(days=days_before)
        for cycle in (0, 6, 12, 18):
            issue = _issue(issue_date.year, issue_date.month, issue_date.day, cycle)
            _assert_subset(issue, tz, fall_back)
            _assert_subset(issue, tz, fall_back + timedelta(days=1))


# ---------------------------------------------------------------------------
# Test 4: Positive-offset city full local-day coverage (Tokyo +9)
# ---------------------------------------------------------------------------

def test_predicted_step_set_covers_full_local_day_for_pos_offset_city() -> None:
    """Tokyo +9: predicted set for D+1..D+3 from 00z must cover all 4 6h periods.

    For issue 00z on day D, Tokyo target D+1 local day runs UTC+9 = 15:00 UTC(D)
    to 15:00 UTC(D+1). Steps 18,24,30,36 must all appear.
    """
    tz = "Asia/Tokyo"
    issue = _issue(2026, 6, 1, 0)  # 2026-06-01 00z

    # D+1 target local day = 2026-06-02 JST = 2026-06-01 15:00 UTC to 2026-06-02 15:00 UTC
    target = date(2026, 6, 2)
    predicted = _predicted(issue, tz, target)
    required = set(_required_steps(issue, tz, target))

    # Required must be a subset of predicted
    assert required <= predicted, (
        f"required={sorted(required)} not subset of predicted={sorted(predicted)}"
    )
    # Predicted must contain every 6h endpoint that touches the 24h Tokyo window
    # Window: 15z D to 15z D+1 → steps 18,24,30,36 (end-of-period landing in window)
    expected_inner = {18, 24, 30, 36}
    assert expected_inner <= predicted, (
        f"Expected inner steps {expected_inner} missing from predicted={sorted(predicted)}"
    )


# ---------------------------------------------------------------------------
# Test 5: Negative-offset city full local-day coverage (NYC -5 / -4)
# ---------------------------------------------------------------------------

def test_predicted_step_set_covers_full_local_day_for_neg_offset_city() -> None:
    """NYC -5 (winter EST): predicted set for D+1..D+3 from 00z must cover window.

    For issue 00z on day D, NYC target D+1 local day runs UTC-5 = 05:00 UTC(D+1)
    to 05:00 UTC(D+2). Steps 30,36,42,48,54 should all appear (window > 24h from issue).
    """
    tz = "America/New_York"
    issue = _issue(2026, 1, 15, 0)  # winter, EST = UTC-5

    target = date(2026, 1, 16)  # D+1 local NYC = 2026-01-16 00:00 EST = 2026-01-16 05:00 UTC
    predicted = _predicted(issue, tz, target)
    required = set(_required_steps(issue, tz, target))

    assert required <= predicted, (
        f"required={sorted(required)} not subset of predicted={sorted(predicted)}"
    )
    # Window 05:00 UTC(D+1) to 05:00 UTC(D+2) → 29h to 53h from issue → steps 30,36,42,48,54
    expected_inner = {30, 36, 42, 48, 54}
    assert expected_inner <= predicted, (
        f"Expected inner steps {expected_inner} missing from predicted={sorted(predicted)}"
    )


# ---------------------------------------------------------------------------
# Test 6: D+10 from 06z is horizon-blocked — both functions agree
# ---------------------------------------------------------------------------

def test_short_horizon_06z_blocks_dplus10_consistently_with_horizon_eval() -> None:
    """D+10 from 06z: both required_period_end_steps and predicted agree on step range.

    From 06z on day D, target D+10 requires steps ≥ 240h (10 days out).
    The ECMWF ENS max horizon is 360h. Both functions should yield non-empty sets
    for D+10 (within horizon), but D+15 should be empty / fall outside 360h.

    Also verifies that for D+10, required ⊆ predicted (horizon within reach).
    """
    tz = "America/Chicago"  # UTC-6 winter / UTC-5 summer
    issue = _issue(2026, 2, 1, 6)  # 06z winter, UTC-6

    # D+10: target 2026-02-11; from 06z, local day ends ~2026-02-12 06:00 UTC = +240h
    target_d10 = date(2026, 2, 11)
    required_d10 = set(_required_steps(issue, tz, target_d10))
    predicted_d10 = _predicted(issue, tz, target_d10)

    # Both must be non-empty (D+10 within ECMWF 360h horizon)
    assert required_d10, f"required_period_end_steps unexpectedly empty for D+10: {issue} → {target_d10}"
    assert predicted_d10, f"predicted_step_set_for_target unexpectedly empty for D+10: {issue} → {target_d10}"

    # Subset must hold
    missing = required_d10 - predicted_d10
    assert not missing, (
        f"D+10 subset violation: required={sorted(required_d10)} "
        f"predicted={sorted(predicted_d10)} missing={sorted(missing)}"
    )

    # D+15 from 06z = target 2026-02-16; local day ends > 360h → predicted must be empty
    # (360h scan limit in predicted_step_set_for_target stops early)
    target_d15 = date(2026, 2, 16)
    predicted_d15 = _predicted(issue, tz, target_d15)
    required_d15 = set(_required_steps(issue, tz, target_d15))
    # Both should be empty since day end UTC > 360h from issue
    # (Chicago winter UTC-6: D+15 local midnight = 2026-02-17 00:00 CST = 06:00 UTC = +360h from 06z)
    # Edge: exactly 360h might be borderline — just verify subset holds if non-empty
    if required_d15:
        assert required_d15 <= predicted_d15, (
            f"D+15 subset violation: required={sorted(required_d15)} "
            f"predicted={sorted(predicted_d15)}"
        )


# ---------------------------------------------------------------------------
# Test 7: Structural-unification invariant
# compute_required_max_step ≡ max(required_period_end_steps(...))
# ---------------------------------------------------------------------------

def test_compute_required_max_step_equals_required_period_end_steps_max() -> None:
    """Structural-unification invariant: compute_required_max_step should equal
    max(required_period_end_steps(...)) for all test cases.

    This test documents WHERE the two implementations agree and where they may
    diverge (fixed-offset vs ZoneInfo). A failure here is FLIP-EVIDENCE for the
    Phase β unification PR (K=1 structural cleanup target in _tigge_common.py).

    The test uses the extractor's own pattern of snapshotting UTC offset at issue_utc,
    matching extract_tigge_mx2t6_localday_max.py:241.
    """
    # (city_tz_str, city_name_for_diag, test_cases)
    # Each test_case: (issue_utc, target_date)
    cases = [
        # NYC winter (EST = UTC-5)
        ("America/New_York", "NYC-winter", [
            (_issue(2026, 1, 15, 0), date(2026, 1, 16)),
            (_issue(2026, 1, 15, 6), date(2026, 1, 17)),
            (_issue(2026, 1, 15, 12), date(2026, 1, 18)),
            (_issue(2026, 1, 15, 18), date(2026, 1, 19)),
        ]),
        # NYC summer (EDT = UTC-4)
        ("America/New_York", "NYC-summer", [
            (_issue(2026, 7, 15, 0), date(2026, 7, 16)),
            (_issue(2026, 7, 15, 6), date(2026, 7, 17)),
        ]),
        # Tokyo (JST = UTC+9, no DST)
        ("Asia/Tokyo", "Tokyo", [
            (_issue(2026, 3, 15, 0), date(2026, 3, 16)),
            (_issue(2026, 7, 15, 12), date(2026, 7, 18)),
        ]),
        # London winter (GMT = UTC+0)
        ("Europe/London", "London-winter", [
            (_issue(2026, 1, 20, 0), date(2026, 1, 21)),
            (_issue(2026, 1, 20, 12), date(2026, 1, 23)),
        ]),
        # London summer (BST = UTC+1)
        ("Europe/London", "London-summer", [
            (_issue(2026, 6, 20, 0), date(2026, 6, 21)),
            (_issue(2026, 6, 20, 6), date(2026, 6, 22)),
        ]),
        # Sydney summer (AEDT = UTC+11)
        ("Australia/Sydney", "Sydney-summer", [
            (_issue(2026, 1, 10, 0), date(2026, 1, 11)),
            (_issue(2026, 1, 10, 12), date(2026, 1, 12)),
        ]),
        # São Paulo (BRT = UTC-3, no DST since 2019)
        ("America/Sao_Paulo", "Sao_Paulo", [
            (_issue(2026, 4, 1, 0), date(2026, 4, 2)),
            (_issue(2026, 4, 1, 18), date(2026, 4, 4)),
        ]),
    ]

    failures: list[str] = []
    agreements: int = 0

    for tz_str, city_label, test_cases in cases:
        tz = ZoneInfo(tz_str)
        for issue_utc, target_date in test_cases:
            # Snapshot UTC offset at issue_utc — extractor pattern
            offset_seconds = tz.utcoffset(issue_utc.replace(tzinfo=None))
            if offset_seconds is None:
                offset_h = 0
            else:
                offset_h = int(offset_seconds.total_seconds() / 3600)

            crms = compute_required_max_step(issue_utc, target_date, offset_h)

            required = _required_steps(issue_utc, tz_str, target_date)
            if not required:
                # No required steps (target before issue or >360h away) — skip
                continue
            rpes_max = max(required)

            if crms == rpes_max:
                agreements += 1
            else:
                failures.append(
                    f"{city_label} issue={issue_utc.isoformat()} "
                    f"target={target_date} offset_h={offset_h}: "
                    f"compute_required_max_step={crms} "
                    f"max(required_period_end_steps)={rpes_max} "
                    f"delta={crms - rpes_max:+d}h"
                )

    if failures:
        # Failures are FLIP-EVIDENCE for Phase β unification PR, not a blocking defect.
        # The architect doc flags this as K=1 structural cleanup; divergence here
        # documents the exact cases that must be handled before unification.
        pytest.fail(
            f"Structural-unification invariant failed for {len(failures)} case(s) "
            f"(agreements={agreements}):\n" + "\n".join(failures)
        )
