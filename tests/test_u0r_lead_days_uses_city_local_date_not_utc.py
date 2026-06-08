# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R_BAYES_SPEC.md §3 (lead is fixed-lead in the CITY-LOCAL calendar; the
#   lead bucket / regional eligibility / sigma all key off the city-local decision date).
#   Fitz Constraint #4 (data provenance / DST-aware local time): computed_at is UTC; the
#   lead must be computed against the city-local date, never the UTC date — cross-timezone
#   the UTC date is off-by-one (Tokyo example in the brief).
"""BLOCKER 6 — lead_days must be computed in the CITY-LOCAL date, not the UTC date.

The materializer override computed lead_days = target_local_date - computed_at.date() (UTC)
while tz_name was read but unused. For a city east of UTC (Tokyo), computed_at at
2026-06-03T16:30Z is local 2026-06-04, so a 2026-06-04 target is lead 0 — but the UTC date is
06-03, giving lead 1 (off by one). For a city west of UTC (US Pacific), the error flips the
other way. Wrong lead -> wrong lead bucket / regional eligibility / sigma.

This test calls the override's lead computation through a tiny seam and asserts the city-local
lead for Tokyo (UTC+9), US Pacific (UTC-7 DST), and Europe/Paris (UTC+2 DST).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.replacement_forecast_materializer import _u0r_city_local_lead_days

UTC = timezone.utc


@pytest.mark.parametrize(
    "tz_name, computed_at, target_local_date, expected_lead",
    [
        # Tokyo UTC+9: 2026-06-03T16:30Z = local 2026-06-04 -> target 06-04 is lead 0 (UTC date
        # 06-03 would wrongly give lead 1).
        ("Asia/Tokyo", datetime(2026, 6, 3, 16, 30, tzinfo=UTC), date(2026, 6, 4), 0),
        ("Asia/Tokyo", datetime(2026, 6, 3, 16, 30, tzinfo=UTC), date(2026, 6, 5), 1),
        # US Pacific (PDT UTC-7 in June): 2026-06-04T02:00Z = local 2026-06-03 19:00 -> target
        # 06-04 is lead 1 (UTC date 06-04 would wrongly give lead 0).
        ("America/Los_Angeles", datetime(2026, 6, 4, 2, 0, tzinfo=UTC), date(2026, 6, 4), 1),
        ("America/Los_Angeles", datetime(2026, 6, 4, 2, 0, tzinfo=UTC), date(2026, 6, 5), 2),
        # Europe/Paris (CEST UTC+2 in June): 2026-06-03T23:30Z = local 2026-06-04 01:30 -> target
        # 06-04 is lead 0 (UTC date 06-03 would wrongly give lead 1).
        ("Europe/Paris", datetime(2026, 6, 3, 23, 30, tzinfo=UTC), date(2026, 6, 4), 0),
    ],
)
def test_lead_days_uses_city_local_date(tz_name, computed_at, target_local_date, expected_lead) -> None:
    assert (
        _u0r_city_local_lead_days(
            computed_at=computed_at, target_local_date=target_local_date, tz_name=tz_name
        )
        == expected_lead
    )


def test_lead_days_never_negative() -> None:
    """A target_local_date BEFORE the city-local computed date floors at 0 (max(0, ...))."""
    assert (
        _u0r_city_local_lead_days(
            computed_at=datetime(2026, 6, 4, 2, 0, tzinfo=UTC),
            target_local_date=date(2026, 6, 1),
            tz_name="Asia/Tokyo",
        )
        == 0
    )
