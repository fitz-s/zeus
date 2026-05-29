# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: D1-LOW aggregation-window product-derivation fix.
#   architecture/data_sources_registry_2026_05_08.yaml:86,91 (mx2t3/mn2t3 =
#   "in the last 3 hours"); ECMWF Open Data step144 mn2t3 GRIB field verified
#   lengthOfTimeRange=3 (2026-05-29). TIGGE mx2t6/mn2t6 stay 6h. Polymarket
#   markets capped at 5 days => OpenData required step set caps at 144h.
"""Relationship contract: the temperature-aggregation window must be derived
from the *product* (data_version param token), not from a single TIGGE-era
scalar shared across 3h and 6h products.

THE RELATIONSHIP (cross-module invariant):
    For any forecast product P, the aggregation window W(P) that the
    period-end-step contract uses MUST equal the physical aggregation window
    of P's GRIB fields:
        mx2t3 / mn2t3 (ECMWF Open Data, "in the last 3 hours")  -> 3h
        mx2t6 / mn2t6 (TIGGE archive,    "in the last 6 hours")  -> 6h

    A single scalar (e.g. STEP_HOURS=6 / AGGREGATION_WINDOW_HOURS=6) applied
    to a 3h product over-states the window by 3h and mis-classifies
    near-day-start fields inner<->boundary. The fix makes the wrong window
    UNCONSTRUCTABLE by keying the window on the product token.

These assertions are RED before the fix (build_forecast_target_scope hardcodes
period_hours=6 for the OpenData mx2t3/mn2t3 data_versions) and GREEN after.
The TIGGE half staying GREEN both before and after is the proof that the fix
does NOT global-flip 6->3 (which would break TIGGE mx2t6/mn2t6).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.forecast_target_contract import (
    aggregation_window_hours_for_data_version,
    build_forecast_target_scope,
)

UTC = timezone.utc

# Live data_version strings (src/contracts/ensemble_snapshot_provenance.py:77-78).
OPENDATA_HIGH_DV = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
OPENDATA_LOW_DV = "ecmwf_opendata_mn2t3_local_calendar_day_min_v1"
# TIGGE archive data_versions (src/data/ecmwf_open_data.py:1711,1716).
TIGGE_HIGH_DV = "tigge_mx2t6_local_calendar_day_max_v1"
TIGGE_LOW_DV = "tigge_mn2t6_local_calendar_day_min_v1"

# Polymarket markets cap at 5 days => OpenData required steps cap at 144h
# (the ECMWF Open Data 3h-stride window). Beyond-144h steps are no longer used.
OPENDATA_MAX_STEP_HOURS = 144


def _issue(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Relationship 1: the product -> window map itself.
# ---------------------------------------------------------------------------


def test_opendata_3h_products_derive_3h_window() -> None:
    assert aggregation_window_hours_for_data_version(OPENDATA_HIGH_DV) == 3
    assert aggregation_window_hours_for_data_version(OPENDATA_LOW_DV) == 3


def test_tigge_6h_products_derive_6h_window() -> None:
    assert aggregation_window_hours_for_data_version(TIGGE_HIGH_DV) == 6
    assert aggregation_window_hours_for_data_version(TIGGE_LOW_DV) == 6


def test_unknown_product_fails_closed() -> None:
    with pytest.raises(ValueError):
        aggregation_window_hours_for_data_version("some_unregistered_source_v1")


# ---------------------------------------------------------------------------
# Relationship 2: the window flows into build_forecast_target_scope.
# OpenData scope must include 3h-stride steps (non-6-multiples like 3,9,15,...);
# TIGGE scope must contain ONLY 6h-multiple steps. This is the cross-module
# property that the readiness/coverage path (ecmwf_open_data.py:852,
# forecast_fetch_plan.py:91) actually consumes via scope.required_step_hours.
# ---------------------------------------------------------------------------


def test_opendata_scope_uses_3h_stride_steps() -> None:
    scope = build_forecast_target_scope(
        city_id="TOKYO",
        city_name="Tokyo",
        city_timezone="Asia/Tokyo",  # UTC+9, no DST — Asian city
        target_local_date=date(2026, 6, 2),
        temperature_metric="high",
        source_cycle_time=_issue(2026, 6, 1, 0),
        data_version=OPENDATA_HIGH_DV,
    )
    steps = scope.required_step_hours
    assert steps, "OpenData scope must produce required steps"
    # 3h stride => at least one step that is NOT a multiple of 6.
    non_six_multiples = [s for s in steps if s % 6 != 0]
    assert non_six_multiples, (
        f"OpenData (mx2t3, 3h product) must yield 3h-stride steps; "
        f"got only 6h-multiples {sorted(steps)} — window not product-derived."
    )
    # Every step must still be a multiple of 3 (3h native grid).
    assert all(s % 3 == 0 for s in steps), f"non-3h-aligned step in {sorted(steps)}"


def test_opendata_low_dst_city_uses_3h_stride_steps() -> None:
    # DST city (America/New_York) for the LOW track — this is where the
    # near-day-start inner<->boundary mis-classification bites worst.
    scope = build_forecast_target_scope(
        city_id="NEW_YORK",
        city_name="New York",
        city_timezone="America/New_York",
        target_local_date=date(2026, 6, 2),  # EDT (UTC-4), summer
        temperature_metric="low",
        source_cycle_time=_issue(2026, 6, 1, 0),
        data_version=OPENDATA_LOW_DV,
    )
    steps = scope.required_step_hours
    assert steps, "OpenData LOW scope must produce required steps"
    assert any(s % 6 != 0 for s in steps), (
        f"OpenData LOW (mn2t3, 3h product) must yield 3h-stride steps; "
        f"got only 6h-multiples {sorted(steps)}."
    )


def test_tigge_scope_stays_6h_stride_no_global_flip() -> None:
    # The no-global-flip guard: TIGGE products MUST keep 6h-multiple-only steps.
    for dv in (TIGGE_HIGH_DV, TIGGE_LOW_DV):
        scope = build_forecast_target_scope(
            city_id="TOKYO",
            city_name="Tokyo",
            city_timezone="Asia/Tokyo",
            target_local_date=date(2026, 6, 2),
            temperature_metric="high" if "mx2t6" in dv else "low",
            source_cycle_time=_issue(2026, 6, 1, 0),
            data_version=dv,
        )
        steps = scope.required_step_hours
        assert steps, f"TIGGE scope must produce required steps for {dv}"
        offenders = [s for s in steps if s % 6 != 0]
        assert not offenders, (
            f"TIGGE ({dv}, 6h product) must stay 6h-stride; "
            f"found non-6h-multiple steps {sorted(offenders)} — illegal global flip 6->3."
        )


# ---------------------------------------------------------------------------
# Relationship 3: OpenData required step set caps at 144h (Polymarket 5-day
# market cap; beyond-144h ECMWF steps are no longer consumed). The contract
# must not require steps > 144h for OpenData, so a snapshot that covers
# 3..144h is not penalised for missing >144h fields.
# ---------------------------------------------------------------------------


def test_opendata_required_steps_cap_at_144h() -> None:
    # A far-horizon target (D+10 from 00z) would, on an uncapped scan, request
    # steps well beyond 144h. With the 5-day market cap, OpenData must not ask
    # for any step > 144h.
    scope = build_forecast_target_scope(
        city_id="TOKYO",
        city_name="Tokyo",
        city_timezone="Asia/Tokyo",
        target_local_date=date(2026, 6, 11),  # ~D+10 from 2026-06-01 00z
        temperature_metric="high",
        source_cycle_time=_issue(2026, 6, 1, 0),
        data_version=OPENDATA_HIGH_DV,
    )
    over_horizon = [s for s in scope.required_step_hours if s > OPENDATA_MAX_STEP_HOURS]
    assert not over_horizon, (
        f"OpenData required steps must cap at {OPENDATA_MAX_STEP_HOURS}h "
        f"(Polymarket 5-day cap); got over-horizon steps {sorted(over_horizon)}."
    )


def test_tigge_required_steps_not_capped_at_144h() -> None:
    # TIGGE is the historical archive on its own (full) range — the 144h cap
    # is OpenData-only and must NOT shrink TIGGE coverage.
    scope = build_forecast_target_scope(
        city_id="TOKYO",
        city_name="Tokyo",
        city_timezone="Asia/Tokyo",
        target_local_date=date(2026, 6, 11),  # ~D+10 from 2026-06-01 00z
        temperature_metric="high",
        source_cycle_time=_issue(2026, 6, 1, 0),
        data_version=TIGGE_HIGH_DV,
    )
    assert any(s > OPENDATA_MAX_STEP_HOURS for s in scope.required_step_hours), (
        "TIGGE D+10 scope must still request steps beyond 144h "
        "(OpenData 144h cap must not apply to the TIGGE archive)."
    )
