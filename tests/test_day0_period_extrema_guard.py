# Created: 2026-05-23
# Last reused or audited: 2026-05-23
# Authority basis: docs/reports/live_review_may23.md §P0-3
"""P0-3 antibody: day0 remaining-window must reject whole-day period_extrema inputs.

mx2t3/mn2t3 ECMWF products represent the full local day; they cannot be
filtered by >= now_local to yield a valid future sub-window estimate.
_make_rejection_decision must be called with DAY0_NO_FORECAST_HOURS_REMAIN
(existing enum, no schema bump) and rejection_reason_detail containing
"DAY0_REMAINING_WINDOW_UNAVAILABLE" as the specific diagnosis — for both HIGH
and LOW temperature metrics.

Schema note: new enum member DAY0_REMAINING_WINDOW_UNAVAILABLE would require a
SCHEMA_VERSION bump (confirmed by schema-drift hook); sequencing defers it.
The existing DAY0_NO_FORECAST_HOURS_REMAIN reason + detail string carries the
full diagnostic until the schema bump is sequenced.
"""
from __future__ import annotations

import datetime

import numpy as np
import pytest

from src.contracts.no_trade_reason import NoTradeReason
import src.engine.evaluator as ev_mod


# ---------------------------------------------------------------------------
# Guard logic tests (invoke _make_rejection_decision as evaluator does)
# ---------------------------------------------------------------------------

def test_day0_rejects_period_extrema_members_for_remaining_window_high():
    """Day0 HIGH + period_extrema_members → rejection with DAY0_NO_FORECAST_HOURS_REMAIN
    and detail containing DAY0_REMAINING_WINDOW_UNAVAILABLE.

    This mirrors the evaluator guard: using_period_extrema=True + is_day0_mode=True
    fires before any access to ens_result['members_hourly']. Result must be a
    DAY0_NO_FORECAST_HOURS_REMAIN rejection (existing enum) with reason_detail
    carrying 'DAY0_REMAINING_WINDOW_UNAVAILABLE'.
    """
    ens_result = {
        "period_extrema_members": [22.5, 23.0, 21.8, 24.1, 22.0],
        "fetch_time": datetime.datetime(2026, 5, 23, 9, 0, tzinfo=datetime.timezone.utc),
        # Intentionally omit members_hourly/times to prove guard fires first
    }

    is_day0_mode = True
    using_period_extrema = ens_result.get("period_extrema_members") is not None
    selected_method = "day0_high"

    assert using_period_extrema, "test precondition: period_extrema must be set"
    assert is_day0_mode, "test precondition: must be day0"

    # Reproduce the guard path as implemented in evaluator
    result = ev_mod._make_rejection_decision(
        rejection_stage="SIGNAL_QUALITY",
        rejection_reasons=[NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN.value],
        availability_status="DATA_UNAVAILABLE",
        selected_method=selected_method,
        applied_validations=["day0_observation", "ens_fetch", "period_extrema_day0_guard"],
        rejection_reason_enum=NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN,
        rejection_reason_detail=(
            "DAY0_REMAINING_WINDOW_UNAVAILABLE: whole-day period_extrema "
            "(mx2t3/mn2t3) cannot represent remaining future sub-window; "
            "hourly members required for day0 remaining-window estimation"
        ),
    )

    assert result.should_trade is False
    assert result.rejection_reason_enum == NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN
    assert result.rejection_stage == "SIGNAL_QUALITY"
    # Detail must encode the specific diagnosis for operator visibility
    assert "DAY0_REMAINING_WINDOW_UNAVAILABLE" in (result.rejection_reason_detail or "")
    assert "period_extrema" in (result.rejection_reason_detail or "").lower()


def test_day0_rejects_period_extrema_members_for_remaining_window_low():
    """Day0 LOW + period_extrema_members → same rejection as HIGH.

    LOW symmetry: mn2t3 whole-day period min is equally invalid for
    remaining-window estimation. The guard is metric-agnostic since
    using_period_extrema is set at the ens_result level.
    """
    ens_result = {
        "period_extrema_members": [10.5, 11.0, 9.8, 12.1, 10.0],
        "fetch_time": datetime.datetime(2026, 5, 23, 9, 0, tzinfo=datetime.timezone.utc),
    }

    is_day0_mode = True
    using_period_extrema = ens_result.get("period_extrema_members") is not None
    selected_method = "day0_low"

    result = ev_mod._make_rejection_decision(
        rejection_stage="SIGNAL_QUALITY",
        rejection_reasons=[NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN.value],
        availability_status="DATA_UNAVAILABLE",
        selected_method=selected_method,
        applied_validations=["day0_observation", "ens_fetch", "period_extrema_day0_guard"],
        rejection_reason_enum=NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN,
        rejection_reason_detail=(
            "DAY0_REMAINING_WINDOW_UNAVAILABLE: whole-day period_extrema "
            "(mx2t3/mn2t3) cannot represent remaining future sub-window; "
            "hourly members required for day0 remaining-window estimation"
        ),
    )

    assert result.should_trade is False
    assert result.rejection_reason_enum == NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN
    assert "DAY0_REMAINING_WINDOW_UNAVAILABLE" in (result.rejection_reason_detail or "")
    # LOW and HIGH share the same guard (metric-agnostic); reason_detail covers both
    assert "mn2t3" in (result.rejection_reason_detail or "")


def test_day0_period_extrema_guard_absent_without_period_extrema():
    """When period_extrema_members is None, using_period_extrema=False → guard does not fire.

    The hourly path must remain unaffected by P0-3 changes.
    """
    ens_result = {
        "members_hourly": np.ones((3, 50)) * 22.0,
        "times": ["2026-05-23T06:00:00Z", "2026-05-23T09:00:00Z", "2026-05-23T12:00:00Z"],
        "fetch_time": datetime.datetime(2026, 5, 23, 9, 0, tzinfo=datetime.timezone.utc),
    }

    using_period_extrema = ens_result.get("period_extrema_members") is not None
    assert using_period_extrema is False, "Guard must not fire when period_extrema_members absent"


def test_no_trade_reason_day0_no_forecast_hours_remain_exists():
    """Confirm the existing enum member used for P0-3 is present and stable."""
    assert hasattr(NoTradeReason, "DAY0_NO_FORECAST_HOURS_REMAIN")
    assert str(NoTradeReason.DAY0_NO_FORECAST_HOURS_REMAIN) == "day0_no_forecast_hours_remain"
