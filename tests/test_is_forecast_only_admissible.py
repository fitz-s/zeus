# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: docs/operations consolidated timeliness/tradeability fix (architect design);
#                  src/strategy/market_phase.py is_forecast_only_admissible canonical predicate.
"""RED→GREEN T4: canonical timeliness predicate is_forecast_only_admissible.

The cheap source-form (no market boundary) must be a CONSERVATIVE LOWER BOUND
of the full reactor phase verdict: if cheap rejects, full must also reject.
This monotonicity guarantees applying the cheap predicate at a source boundary
can never starve a candidate the full reactor would have admitted.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.strategy.market_phase import (
    is_forecast_only_admissible,
    market_phase_admits,
)


# A representative grid of IANA timezones spanning UTC offsets and hemispheres.
_TZS = [
    "America/Chicago",
    "America/New_York",
    "Europe/London",
    "Europe/Warsaw",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Australia/Sydney",
    "Pacific/Auckland",
]


def _as_of_grid():
    # Span several days and several intraday hours around midnight boundaries.
    base = datetime(2026, 6, 4, 0, 0, 0, tzinfo=timezone.utc)
    for day_off in range(-2, 3):
        for hour in (0, 6, 11, 12, 13, 18, 23):
            yield base.replace(day=4 + day_off, hour=hour)


def test_cheap_form_rejects_already_settled_local_day():
    # target local day in the past at as_of → cheap rejects.
    tz = "America/Chicago"
    as_of = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)  # Chicago local 2026-06-05 07:00
    assert (
        is_forecast_only_admissible(
            target_local_date=date(2026, 6, 4),
            city_timezone=tz,
            as_of_utc=as_of,
        )
        is False
    )


def test_cheap_form_admits_strictly_future_local_day():
    tz = "America/Chicago"
    as_of = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)  # Chicago local 2026-06-04 07:00
    assert (
        is_forecast_only_admissible(
            target_local_date=date(2026, 6, 5),
            city_timezone=tz,
            as_of_utc=as_of,
        )
        is True
    )


def test_cheap_form_rejects_same_local_day():
    # Whole target local day must still be in the future; same-day → reject.
    tz = "America/Chicago"
    as_of = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)  # Chicago local 2026-06-04
    assert (
        is_forecast_only_admissible(
            target_local_date=date(2026, 6, 4),
            city_timezone=tz,
            as_of_utc=as_of,
        )
        is False
    )


def test_unknown_timezone_fails_closed():
    assert (
        is_forecast_only_admissible(
            target_local_date=date(2026, 6, 5),
            city_timezone="Not/AZone",
            as_of_utc=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        )
        is False
    )
    assert (
        is_forecast_only_admissible(
            target_local_date=date(2026, 6, 5),
            city_timezone="",
            as_of_utc=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        )
        is False
    )


def test_monotonicity_cheap_reject_implies_full_reject():
    """T4 core: across the grid, cheap==False ⟹ full==False.

    Full form uses the F1 fallback end anchor (12:00 UTC of target_date) so it
    is the byte-identical reactor verdict for a market with no explicit timing.
    """
    for tz in _TZS:
        for target_date in (date(2026, 6, 3), date(2026, 6, 4), date(2026, 6, 5)):
            for as_of in _as_of_grid():
                cheap = is_forecast_only_admissible(
                    target_local_date=target_date,
                    city_timezone=tz,
                    as_of_utc=as_of,
                )
                full = is_forecast_only_admissible(
                    target_local_date=target_date,
                    city_timezone=tz,
                    as_of_utc=as_of,
                    polymarket_end_utc=datetime(
                        target_date.year, target_date.month, target_date.day,
                        12, 0, 0, tzinfo=timezone.utc,
                    ),
                )
                if cheap is False:
                    assert full is False, (
                        f"monotonicity violated: cheap rejected but full admitted "
                        f"tz={tz} target={target_date} as_of={as_of.isoformat()}"
                    )


def test_full_form_byte_identical_to_market_phase_admits():
    """Full form must delegate to the same authority market_phase_admits uses,
    so the universe/reactor/source all share one verdict."""
    tz = "Europe/Warsaw"
    target = date(2026, 6, 5)
    end_utc = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    for as_of in _as_of_grid():
        full = is_forecast_only_admissible(
            target_local_date=target,
            city_timezone=tz,
            as_of_utc=as_of,
            polymarket_end_utc=end_utc,
        )
        # Reconstruct the market_phase_for_decision verdict directly.
        from src.strategy.market_phase import (
            market_phase_for_decision,
            MarketPhase,
        )
        expected = (
            market_phase_for_decision(
                target_local_date=target,
                city_timezone=tz,
                decision_time_utc=as_of,
                polymarket_start_utc=None,
                polymarket_end_utc=end_utc,
            )
            == MarketPhase.PRE_SETTLEMENT_DAY
        )
        assert full == expected, f"full-form diverged from phase authority at {as_of}"
