# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: critic v2 B2 spot-check + ecmwf_open_data 3h-native authority
#                  (see commit 209575b2: "switch from deprecated mx2t6 to mx2t3")
"""B2: east-Asia c12 step boundary spot-check for local-calendar-day max.

Critic v2 B2 BLOCKER: ECMWF c12 cycle (issue at 12:00 UTC) for an east-Asia
city (Tokyo, UTC+9, no DST) puts the issue at 21:00 local same day. The local
calendar day window for ``target_date = issue_date + 1`` therefore starts at
15:00 UTC (= 00:00 next-day Tokyo) and ends at 15:00 UTC the day after
(= 00:00 +2 days Tokyo).

Concrete step-boundary semantics (verified against
src.data.forecast_target_contract.required_period_end_steps + period_hours=3):

  c12 issue 2026-07-14 12:00 UTC, target = 2026-07-15 (Tokyo local)
    window = [2026-07-14 15:00 UTC, 2026-07-15 15:00 UTC]  (24h)
    step=3 ends AT window_start (15:00 UTC) — boundary, NOT included
    step=6..27 land inside the window (8 steps total, 3h-native cadence)

  c00 issue 2026-07-15 00:00 UTC, target = 2026-07-15 (Tokyo local)
    Tokyo local issue = 2026-07-15 09:00 (already mid-day target)
    window starts 2026-07-14 15:00 UTC, ends 2026-07-15 15:00 UTC
    step=3 → valid_time = 03:00 UTC = 12:00 Tokyo same day → IN window
    No cross-day boundary hop for c00 step=3.

This pins the contract that:
  (1) c12 + east-Asia tz routes step contributions to issue_date+1 (NOT issue_date)
  (2) ≥4 step contributions cover the issue_date+1 local-day window
  (3) c00 + step=3 stays inside the same-day target window (no cross-day)
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.forecast_target_contract import (  # noqa: E402
    compute_target_local_day_window_utc,
    required_period_end_steps,
)
from scripts._tigge_common import predicted_step_set_for_target  # noqa: E402


TOKYO_TZ = "Asia/Tokyo"
TOKYO_LAT = 35.68
TOKYO_LON = 139.78
UTC = timezone.utc


def _issue_c12(year: int, month: int, day: int) -> datetime:
    """ECMWF c12 cycle issue at 12:00 UTC on the given calendar date."""
    return datetime(year, month, day, 12, tzinfo=UTC)


def _issue_c00(year: int, month: int, day: int) -> datetime:
    """ECMWF c00 cycle issue at 00:00 UTC on the given calendar date."""
    return datetime(year, month, day, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Validation 1: c12 + step contributes to issue_date+1 local-day max for Tokyo
# ---------------------------------------------------------------------------

def test_c12_step_contributes_to_issue_date_plus_one_for_tokyo():
    """c12 issue + 3h-native steps land in target_date = issue_date+1 (Tokyo).

    Issue 2026-07-14 12:00 UTC = 2026-07-14 21:00 Tokyo. The local-calendar-day
    window for target = 2026-07-15 starts at 15:00 UTC (= 00:00 Tokyo) and
    ends 24h later. Step contributions covering this window must come from
    steps after the boundary at step=3 (which ends precisely at window_start).
    """
    issue_utc = _issue_c12(2026, 7, 14)
    issue_tokyo = issue_utc.astimezone(ZoneInfo(TOKYO_TZ))
    assert issue_tokyo.hour == 21
    assert issue_tokyo.date() == date(2026, 7, 14), (
        "Tokyo local issue date must equal UTC issue date for c12"
    )

    target_date = date(2026, 7, 15)  # issue_date + 1
    window = compute_target_local_day_window_utc(
        city_timezone=TOKYO_TZ,
        target_local_date=target_date,
    )
    # Window start = 00:00 Tokyo = 15:00 UTC issue_date
    assert window.start_utc == datetime(2026, 7, 14, 15, tzinfo=UTC)
    assert window.end_utc == datetime(2026, 7, 15, 15, tzinfo=UTC)

    required_3h = required_period_end_steps(
        source_cycle_time=issue_utc,
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=3,
    )
    # step=3 ends at window_start (15:00 UTC) — boundary excluded by
    # contract's strict valid_time > window_start. Steps 6..27 cover the day.
    assert 3 not in required_3h, (
        "step=3 ends exactly at window_start (Tokyo 00:00) — must be excluded "
        "by the strict-greater contract; otherwise duplicate-day attribution "
        f"is possible. Got required={required_3h}"
    )
    assert 6 in required_3h, "step=6 must contribute (03:00 Tokyo next day)"
    assert 24 in required_3h, "step=24 must contribute (21:00 Tokyo target_date)"
    assert 27 in required_3h, "step=27 must close the local-day window"


# ---------------------------------------------------------------------------
# Validation 2: ≥4 step contributions cover the local-day max window
# ---------------------------------------------------------------------------

def test_c12_target_local_day_has_at_least_4_step_contributions():
    """Tokyo c12 → issue_date+1: must aggregate ≥4 3h-native step contributions.

    A 24h local-day window with 3h-native period cadence yields 8 steps.
    The ≥4 floor is the ENS-monotonic adequacy threshold: fewer than 4 steps
    means the local-day max is computed over a partial window and is not a
    valid daily extremum.

    Also pins the 6h-native cadence variant: ≥4 of the 8 3h-native must
    survive when projected to 6h-native (steps {6,12,18,24,30,...}).
    """
    issue_utc = _issue_c12(2026, 7, 14)
    target_date = date(2026, 7, 15)
    window = compute_target_local_day_window_utc(
        city_timezone=TOKYO_TZ,
        target_local_date=target_date,
    )

    required_3h = required_period_end_steps(
        source_cycle_time=issue_utc,
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=3,
    )
    assert len(required_3h) >= 4, (
        f"local-calendar-day max must aggregate ≥4 3h-native steps; "
        f"got {len(required_3h)} steps: {required_3h}"
    )
    assert len(required_3h) == 8, (
        f"24h window with 3h cadence (boundary excluded at start) → 8 steps; "
        f"got {required_3h}"
    )

    # 6h-native sanity: legacy mx2t6 path must also have ≥4 contributions.
    required_6h = required_period_end_steps(
        source_cycle_time=issue_utc,
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=6,
    )
    assert len(required_6h) >= 4, (
        f"6h-native local-day max also requires ≥4 step contributions; "
        f"got {len(required_6h)} steps: {required_6h}"
    )

    # And the predicted-step-set helper agrees with the contract on subset.
    predicted_6h = predicted_step_set_for_target(
        issue_utc=issue_utc,
        target_date=target_date,
        city_tz=TOKYO_TZ,
        period_hours=6,
    )
    missing = set(required_6h) - predicted_6h
    assert not missing, (
        f"6h-native predicted-set must cover all required steps; "
        f"missing={sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# Validation 3: c00 + step=3 same-day in Tokyo — no cross-day hop
# ---------------------------------------------------------------------------

def test_c00_step3_does_not_cross_day_boundary_for_tokyo():
    """c00 issue + step=3 in Tokyo lands SAME local day, not next day.

    Issue 2026-07-15 00:00 UTC = 2026-07-15 09:00 Tokyo. step=3 ends at
    03:00 UTC = 12:00 Tokyo same day — clearly inside the 2026-07-15
    local-day window, no cross-day attribution.
    """
    issue_utc = _issue_c00(2026, 7, 15)
    issue_tokyo = issue_utc.astimezone(ZoneInfo(TOKYO_TZ))
    assert issue_tokyo.hour == 9
    assert issue_tokyo.date() == date(2026, 7, 15)

    target_date = date(2026, 7, 15)  # same-day target
    window = compute_target_local_day_window_utc(
        city_timezone=TOKYO_TZ,
        target_local_date=target_date,
    )
    # Window: [2026-07-14 15:00 UTC, 2026-07-15 15:00 UTC]
    # step=3 valid_time = 03:00 UTC = 12:00 Tokyo same day → INSIDE window
    valid_time_step3 = issue_utc + timedelta(hours=3)
    assert window.start_utc < valid_time_step3 < window.end_utc, (
        "c00 step=3 must land strictly inside the same-day Tokyo window"
    )
    valid_tokyo = valid_time_step3.astimezone(ZoneInfo(TOKYO_TZ))
    assert valid_tokyo.date() == target_date, (
        f"c00 step=3 in Tokyo local must stay on target_date; "
        f"got {valid_tokyo.isoformat()}"
    )
    assert valid_tokyo.hour == 12

    required_3h = required_period_end_steps(
        source_cycle_time=issue_utc,
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=3,
    )
    assert 3 in required_3h, (
        f"c00 step=3 must contribute to same-day Tokyo local-day max; "
        f"got required={required_3h}"
    )


# ---------------------------------------------------------------------------
# Cross-validation: predicted-step-set agrees with contract for c12
# ---------------------------------------------------------------------------

def test_c12_predicted_set_matches_contract_for_tokyo_target_plus_one():
    """The extractor's predicted_step_set helper must agree with the contract.

    Sanity: required_period_end_steps ⊆ predicted_step_set_for_target for
    the Tokyo c12 → target_date+1 case. This protects against a future
    refactor of either side that would silently drop step contributions.
    """
    issue_utc = _issue_c12(2026, 7, 14)
    target_date = date(2026, 7, 15)
    window = compute_target_local_day_window_utc(
        city_timezone=TOKYO_TZ,
        target_local_date=target_date,
    )
    required_6h = set(required_period_end_steps(
        source_cycle_time=issue_utc,
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=6,
    ))
    predicted_6h = predicted_step_set_for_target(
        issue_utc=issue_utc,
        target_date=target_date,
        city_tz=TOKYO_TZ,
        period_hours=6,
    )
    assert required_6h.issubset(predicted_6h), (
        f"Tokyo c12 contract steps must be subset of extractor predicted set. "
        f"required={sorted(required_6h)} predicted={sorted(predicted_6h)} "
        f"missing={sorted(required_6h - predicted_6h)}"
    )


# ---------------------------------------------------------------------------
# Coordinate sanity: confirm the test really targets Tokyo (35.68N, 139.78E)
# ---------------------------------------------------------------------------

def test_tokyo_coordinates_and_offset_are_correct():
    """Sanity: Tokyo is at 35.68N, 139.78E and JST is fixed UTC+9 (no DST)."""
    # Confirm UTC+9 fixed offset by sampling 2 dates 6 months apart.
    summer = datetime(2026, 7, 15, 12, tzinfo=ZoneInfo(TOKYO_TZ))
    winter = datetime(2026, 1, 15, 12, tzinfo=ZoneInfo(TOKYO_TZ))
    summer_offset = summer.utcoffset()
    winter_offset = winter.utcoffset()
    assert summer_offset is not None and winter_offset is not None
    nine_hours = timedelta(hours=9)
    assert summer_offset == nine_hours, f"Tokyo summer offset must be UTC+9; got {summer_offset}"
    assert winter_offset == nine_hours, f"Tokyo winter offset must be UTC+9; got {winter_offset}"
    # Coordinate sanity: not asserting equality (cities.json is the canonical
    # source); just pinning that the test header documents what we mean.
    assert 35.0 <= TOKYO_LAT <= 36.0
    assert 139.0 <= TOKYO_LON <= 140.0
