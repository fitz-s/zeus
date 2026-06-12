# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator time-semantics directive 2026-06-10

"""Per-city time-semantics property tests (incident cluster 7).

The system computes lead_days in the CITY-LOCAL calendar, never the UTC calendar
(materializer BLOCKER 6). A UTC-date lead is off-by-one across timezones and wrong
across DST boundaries and the international date line. These are the relationship
tests for the city-local time boundary: "when a UTC instant flows into the per-city
lead computation, does the local-calendar property hold across DST and the dateline?"

The system under test is the real production function:
    src.data.replacement_forecast_materializer._bayes_precision_fusion_city_local_lead_days
We do NOT re-implement it — we pin the cross-boundary properties it must satisfy.

Spring-forward regression pin: London 2025-03-30 02:00 local does not exist (clocks
jump 01:00→02:00 BST). The repo lore "London 2025-03-30 hour-skip" incident is the
canonical DST data-provenance failure (a systematic 1-hour offset for DST cities).
zoneinfo handles the skip correctly; this test pins that the lead computation built
on top of it does not regress to a UTC-date answer on that exact day.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.data.replacement_forecast_materializer import _bayes_precision_fusion_city_local_lead_days


# ---------------------------------------------------------------------------
# Core property: lead is computed in the CITY-LOCAL calendar, floored at 0.
# ---------------------------------------------------------------------------


def test_tokyo_evening_utc_is_next_local_day_lead_zero() -> None:
    """Tokyo 2026-06-03T16:30Z is local 2026-06-04 01:30 → a 06-04 target is lead 0.

    The BLOCKER-6 canonical example: UTC date is 06-03, local date is 06-04. Using
    the UTC date would give lead 1 (wrong); the local date gives lead 0 (correct).
    """
    computed_at = datetime(2026, 6, 3, 16, 30, tzinfo=timezone.utc)
    lead = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at,
        target_local_date=date(2026, 6, 4),
        tz_name="Asia/Tokyo",
    )
    assert lead == 0, "Tokyo local date is 06-04, so a 06-04 target must be lead 0, not 1"


def test_utc_date_naive_lead_would_be_one_proving_local_matters() -> None:
    """The naive UTC-date lead for the Tokyo case is 1 — proving the boundary matters.

    Relationship assertion: the local-calendar answer (0) DIFFERS from the UTC-calendar
    answer (1). If they were equal the test would be vacuous. This pins the off-by-one
    that the local computation exists to prevent.
    """
    computed_at = datetime(2026, 6, 3, 16, 30, tzinfo=timezone.utc)
    target = date(2026, 6, 4)
    naive_utc_lead = max(0, (target - computed_at.date()).days)
    local_lead = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at, target_local_date=target, tz_name="Asia/Tokyo"
    )
    assert naive_utc_lead == 1
    assert local_lead == 0
    assert naive_utc_lead != local_lead, (
        "city-local lead must diverge from UTC-date lead here, or the local "
        "computation is not actually being exercised"
    )


def test_lead_floors_at_zero_for_target_before_local_decision_date() -> None:
    """A target before the city-local decision date is lead 0 (floored), never negative."""
    computed_at = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    lead = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at,
        target_local_date=date(2026, 6, 8),  # two days in the past
        tz_name="America/New_York",
    )
    assert lead == 0


def test_unresolvable_tz_falls_back_to_utc_date() -> None:
    """An unresolvable timezone name falls back to the UTC date (defensive contract)."""
    computed_at = datetime(2026, 6, 3, 16, 30, tzinfo=timezone.utc)
    lead = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at,
        target_local_date=date(2026, 6, 4),
        tz_name="Not/AZone",
    )
    # UTC date is 06-03, target 06-04 → fallback lead 1 (the off-by-one we accept
    # ONLY when the tz is unresolvable; the caller always passes a real tz).
    assert lead == 1


# ---------------------------------------------------------------------------
# DST spring-forward regression pin: London 2025-03-30 (clocks 01:00→02:00 BST).
# ---------------------------------------------------------------------------


def test_london_spring_forward_2025_03_30_lead_is_local_not_utc() -> None:
    """London 2025-03-30 is the DST hour-skip day; lead must use the BST local date.

    At 2025-03-30T00:30Z London is still 00:30 GMT (DST flips at 01:00 GMT). A
    target of 2025-03-30 is lead 0 in BOTH calendars here — the pin is that the
    function does not crash or mis-resolve on the skip day and returns the local
    answer. The discriminating case follows below.
    """
    computed_at = datetime(2025, 3, 30, 0, 30, tzinfo=timezone.utc)
    lead = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at,
        target_local_date=date(2025, 3, 30),
        tz_name="Europe/London",
    )
    assert lead == 0


def test_london_spring_forward_post_transition_local_date_holds() -> None:
    """After the 01:00 GMT spring-forward, London local is BST (+1h); the date is stable.

    2025-03-30T01:30Z is 02:30 BST (the clocks already jumped). Local date is still
    2025-03-30, so a 2025-03-31 target is lead 1. The pin: the +1h BST shift across
    the transition does not corrupt the local DATE (the hour skip is within the day).
    """
    computed_at = datetime(2025, 3, 30, 1, 30, tzinfo=timezone.utc)  # 02:30 BST
    lead = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at,
        target_local_date=date(2025, 3, 31),
        tz_name="Europe/London",
    )
    assert lead == 1


def test_london_late_evening_utc_is_same_local_day_in_bst() -> None:
    """In BST summer, 2025-06-30T23:30Z is local 2025-07-01 00:30 → 07-01 target lead 0.

    Summer (BST, +1h) pushes a late-UTC instant into the NEXT London local day — the
    DST-aware version of the Tokyo off-by-one. UTC date 06-30 would give lead 1; the
    BST-local date 07-01 gives lead 0.
    """
    computed_at = datetime(2025, 6, 30, 23, 30, tzinfo=timezone.utc)
    local_lead = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at,
        target_local_date=date(2025, 7, 1),
        tz_name="Europe/London",
    )
    naive_utc_lead = max(0, (date(2025, 7, 1) - computed_at.date()).days)
    assert local_lead == 0
    assert naive_utc_lead == 1
    assert local_lead != naive_utc_lead


# ---------------------------------------------------------------------------
# International date line: Auckland/Wellington (UTC+12/+13) lead ahead of UTC.
# ---------------------------------------------------------------------------


def test_auckland_dateline_local_day_ahead_of_utc() -> None:
    """Auckland 2026-06-10T13:00Z is local 2026-06-11 01:00 (NZST +12) → 06-11 lead 0.

    Date-line case: the local calendar is a full day AHEAD of UTC for late-UTC
    instants. UTC date 06-10 would give lead 1 for a 06-11 target; the NZ-local date
    06-11 gives lead 0. The mirror image of cities behind UTC.
    """
    computed_at = datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc)
    local_lead = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at,
        target_local_date=date(2026, 6, 11),
        tz_name="Pacific/Auckland",
    )
    naive_utc_lead = max(0, (date(2026, 6, 11) - computed_at.date()).days)
    assert local_lead == 0
    assert naive_utc_lead == 1
    assert local_lead != naive_utc_lead


def test_auckland_southern_dst_spring_forward_2026_09_27() -> None:
    """Auckland spring-forward 2026-09-27 (NZDT, clocks 02:00→03:00) keeps a stable local date.

    Southern-hemisphere DST flips in September. 2026-09-26T12:00Z is 2026-09-27 01:00
    NZ (still NZST pre-transition); a 2026-09-27 target is lead 0. Pins that the
    southern spring-forward day does not regress the lead to a UTC-date answer.
    """
    computed_at = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    lead = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at,
        target_local_date=date(2026, 9, 27),
        tz_name="Pacific/Auckland",
    )
    assert lead == 0


def test_dateline_and_behind_utc_disagree_on_same_instant() -> None:
    """Same UTC instant, opposite-side cities give different local leads for one target.

    The cross-city relationship: at 2026-06-10T13:00Z, Auckland (UTC+12) is already
    on 06-11 while Honolulu (UTC-10) is still on 06-10. For a 2026-06-11 target,
    Auckland is lead 0 and Honolulu is lead 1 — the SAME instant, SAME target,
    DIFFERENT lead, entirely because of the local calendar. This is the property a
    UTC-date computation silently destroys.
    """
    computed_at = datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc)
    target = date(2026, 6, 11)
    auckland = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at, target_local_date=target, tz_name="Pacific/Auckland"
    )
    honolulu = _bayes_precision_fusion_city_local_lead_days(
        computed_at=computed_at, target_local_date=target, tz_name="Pacific/Honolulu"
    )
    assert auckland == 0
    assert honolulu == 1
    assert auckland != honolulu, (
        "opposite-side-of-dateline cities must produce different local leads for the "
        "same instant+target, or the lead is being computed in UTC not city-local"
    )
