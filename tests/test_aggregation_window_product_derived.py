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
    evaluate_horizon_coverage,
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
# Relationship 3: the requirement is HONEST; the 144h cap lives on the FETCH
# list (STEP_HOURS) + the coverage GATE, NOT on the requirement. An over-horizon
# target (window genuinely needs steps > 144h) must produce an honest required
# set that the coverage gate BLOCKS — never a silently-truncated set that reads
# as COMPLETE/LIVE_ELIGIBLE.
#
# SEV-1 antibody (wave-critic 2026-05-29): the prior contract capped the
# REQUIREMENT itself (`required_period_end_steps(max_step_hours=144)`), which
# truncated Western-hemisphere D+5 windows to <=144 and let the coverage gate
# read them as COMPLETE — a silent fail-open on a live-money path. The fix moves
# the cap off the requirement: the requirement is always honest, and
# evaluate_horizon_coverage(required, live_max=144) blocks anything the fetch
# cannot cover. East vs West asymmetry is the load-bearing case: Eastern cities'
# D+5 local day ends ~135h UTC (covered); Western cities' D+5 local day extends
# to ~151-153h UTC (NOT covered at 144h) -> must BLOCK, not silently pass.
# ---------------------------------------------------------------------------

# What STEP_HOURS actually fetches (3h grid 3..144).
LIVE_MAX_OPENDATA = OPENDATA_MAX_STEP_HOURS


def test_eastern_d5_within_cap_is_live_eligible() -> None:
    # Tokyo (UTC+9): a D+5 LOCAL day ends ~135h UTC — within the 144h fetch cap.
    scope = build_forecast_target_scope(
        city_id="TOKYO",
        city_name="Tokyo",
        city_timezone="Asia/Tokyo",
        target_local_date=date(2026, 6, 6),  # ~D+5 from 2026-06-01 00z
        temperature_metric="low",
        source_cycle_time=_issue(2026, 6, 1, 0),
        data_version=OPENDATA_LOW_DV,
    )
    assert max(scope.required_step_hours) <= OPENDATA_MAX_STEP_HOURS, (
        f"Tokyo D+5 should fit within {OPENDATA_MAX_STEP_HOURS}h; "
        f"got max {max(scope.required_step_hours)}"
    )
    decision = evaluate_horizon_coverage(
        required_steps=scope.required_step_hours,
        live_max_step_hours=LIVE_MAX_OPENDATA,
    )
    assert decision.status == "LIVE_ELIGIBLE", decision.reason_codes


def test_western_d5_over_cap_blocks_not_silently_complete() -> None:
    # SEV-1 antibody. Seattle (America/Los_Angeles = UTC-7 in June): a D+5 LOCAL
    # day extends to ~151-153h UTC (the negative UTC offset pushes the local day
    # later in UTC). The 144h fetch cap does NOT cover it. The requirement MUST
    # be honest (max > 144) so the coverage gate BLOCKS — it must never silently
    # truncate to <=144 and read as LIVE_ELIGIBLE.
    scope = build_forecast_target_scope(
        city_id="SEATTLE",
        city_name="Seattle",
        city_timezone="America/Los_Angeles",
        target_local_date=date(2026, 6, 6),  # ~D+5 from 2026-06-01 00z
        temperature_metric="low",
        source_cycle_time=_issue(2026, 6, 1, 0),
        data_version=OPENDATA_LOW_DV,
    )
    over = [s for s in scope.required_step_hours if s > OPENDATA_MAX_STEP_HOURS]
    assert over, (
        "Western D+5 requirement was silently truncated to <=144h — the coverage "
        "gate cannot see the uncovered tail and will fail OPEN (the SEV-1 bug). "
        f"required={scope.required_step_hours}"
    )
    decision = evaluate_horizon_coverage(
        required_steps=scope.required_step_hours,
        live_max_step_hours=LIVE_MAX_OPENDATA,
    )
    assert decision.status == "BLOCKED", (
        f"Western D+5 must BLOCK at the {OPENDATA_MAX_STEP_HOURS}h cap, got "
        f"{decision.status}: {decision.reason_codes}"
    )
    assert "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in decision.reason_codes


def test_tigge_required_steps_not_capped_at_144h() -> None:
    # TIGGE is the historical archive on its own (full) range — uncapped. The
    # requirement was never OpenData-capped; this stays true after the fix.
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
        "TIGGE D+10 scope must still request steps beyond 144h."
    )
