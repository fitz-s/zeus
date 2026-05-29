# Created: 2026-05-08
# Last reused/audited: 2026-05-29
# Authority basis: Polymarket retired all markets beyond 5 days (2026-05-29 operator).
#   The OpenData fetch horizon is now capped at OPENDATA_MAX_STEP_HOURS (144h = D+5
#   plus the largest trading-city UTC offset). This RETIRES fix/#134's D+10/282h
#   contract: steps 150-282 are no longer fetched. This file is the NEW antibody that
#   makes any step > OPENDATA_MAX_STEP_HOURS UNCONSTRUCTABLE — STEP_HOURS is DERIVED
# Lifecycle: created=2026-05-08; last_reviewed=2026-05-29; last_reused=2026-05-29
# Purpose: STEP_HOURS coverage contract for the 5-day-cap regime — no step exceeds OPENDATA_MAX_STEP_HOURS=144 and fetch list covers completeness expectations.
# Reuse: Run after any change to STEP_HOURS list, OPENDATA_MAX_STEP_HOURS constant, or completeness expected_steps logic.
#   from the cap constant, so re-adding a >144h tail fails the coupling assertion.
"""STEP_HOURS coverage contract tests for ecmwf_open_data — 5-day-cap regime.

Verifies that:
1. max(STEP_HOURS) == OPENDATA_MAX_STEP_HOURS (the fetch list is coupled to the cap).
2. No step in STEP_HOURS exceeds OPENDATA_MAX_STEP_HOURS (no >144h tail).
3. STEP_HOURS is the pure 3h-stride grid 3..144 (no 6h tail beyond 144).
4. CROSS-MODULE: the completeness/readiness expected-step fallbacks never demand a
   step the fetch list no longer provides (fetch list ⊇ completeness expectation).
5. evaluate_horizon_coverage admits D+5 horizons at live_max=144 and blocks above it.
"""

from __future__ import annotations

import pytest

from src.data.ecmwf_open_data import STEP_HOURS
from src.data.forecast_target_contract import (
    OPENDATA_MAX_STEP_HOURS,
    evaluate_horizon_coverage,
)
from src.events.forecast_completeness import expected_steps_for_cycle
from src.events.triggers.forecast_snapshot_ready import ecmwf_open_data_expected_steps


def test_step_hours_max_is_opendata_cap() -> None:
    """The fetch list's max step must equal the OpenData cap constant.

    Couples STEP_HOURS to OPENDATA_MAX_STEP_HOURS — they cannot drift apart silently.
    """
    assert max(STEP_HOURS) == OPENDATA_MAX_STEP_HOURS, (
        f"max(STEP_HOURS)={max(STEP_HOURS)} != OPENDATA_MAX_STEP_HOURS="
        f"{OPENDATA_MAX_STEP_HOURS}. The fetch horizon and the cap constant must agree."
    )


def test_step_hours_no_step_above_cap() -> None:
    """No fetched step may exceed the cap — the core antibody for the 5-day regime.

    Polymarket markets beyond 5 days were retired; fetching steps 150-282 wasted
    bandwidth and made the >144h coverage path a dead, fail-closed trap. Re-adding
    any such step must turn this RED.
    """
    over = [s for s in STEP_HOURS if s > OPENDATA_MAX_STEP_HOURS]
    assert over == [], (
        f"STEP_HOURS contains steps above the {OPENDATA_MAX_STEP_HOURS}h cap: {over}. "
        "Polymarket retired >5-day markets; the fetch list must not exceed the cap."
    )


def test_step_hours_is_derived_pure_3h_grid() -> None:
    """STEP_HOURS must be exactly the 3h-stride grid 3..cap (no 6h tail).

    ECMWF Open Data enfo cf/pf publishes mx2t3/mn2t3 at 3h stride through 144h.
    Under the 5-day cap we fetch only that segment — no 150-282 6h tail.
    """
    assert STEP_HOURS == list(range(3, OPENDATA_MAX_STEP_HOURS + 3, 3)), (
        f"STEP_HOURS is not the pure 3h grid 3..{OPENDATA_MAX_STEP_HOURS}. "
        f"Got head={STEP_HOURS[:3]} tail={STEP_HOURS[-3:]}"
    )


def test_step_hours_starts_at_3() -> None:
    """STEP_HOURS must begin at 3h (A1+3h authority — no step 0 or 6 as first)."""
    assert STEP_HOURS[0] == 3


def test_completeness_expectations_within_cap() -> None:
    """CROSS-MODULE relationship antibody: completeness expectations ⊆ fetch list.

    The readiness/completeness fallbacks (expected_steps_for_cycle and
    ecmwf_open_data_expected_steps) must never demand a step the fetcher no longer
    provides. If they did, the fallback completeness path would be permanently
    fail-closed (it would expect 150-360h that STEP_HOURS no longer fetches).

    This couples three modules: ecmwf_open_data.STEP_HOURS,
    forecast_completeness.expected_steps_for_cycle,
    forecast_snapshot_ready.ecmwf_open_data_expected_steps. Drop the cap in one
    without the others and this turns RED.
    """
    fetch_set = set(STEP_HOURS)
    for cycle in (0, 12, 6, 18):
        comp = expected_steps_for_cycle(cycle)
        ready = ecmwf_open_data_expected_steps(cycle)
        comp_over = [s for s in comp if s > OPENDATA_MAX_STEP_HOURS]
        ready_over = [s for s in ready if s > OPENDATA_MAX_STEP_HOURS]
        assert comp_over == [], (
            f"expected_steps_for_cycle({cycle}) exceeds cap: {comp_over}"
        )
        assert ready_over == [], (
            f"ecmwf_open_data_expected_steps({cycle}) exceeds cap: {ready_over}"
        )
        # Completeness must not expect a fetchable-range step the fetcher omits.
        # Step 0 (analysis time) is intentionally in the candidate grid but below the
        # fetch floor (STEP_HOURS[0]==3) — it is always window-filtered before use, so
        # it is exempt. The load-bearing coherence is at/above the fetch floor.
        missing_from_fetch = [
            s for s in ready if s >= STEP_HOURS[0] and s not in fetch_set
        ]
        assert missing_from_fetch == [], (
            f"ecmwf_open_data_expected_steps({cycle}) expects fetchable-range steps "
            f"not in STEP_HOURS: {missing_from_fetch}"
        )


def test_evaluate_horizon_coverage_passes_for_d5_with_live_max_144() -> None:
    """D+5 horizons (steps up to ~132h) must be LIVE_ELIGIBLE at live_max=144."""
    decision = evaluate_horizon_coverage(
        required_steps=(120, 126, 132),
        live_max_step_hours=OPENDATA_MAX_STEP_HOURS,
    )
    assert decision.status == "LIVE_ELIGIBLE", (
        f"Expected LIVE_ELIGIBLE for D+5 steps at live_max={OPENDATA_MAX_STEP_HOURS}, "
        f"got {decision.status}: {decision.reason_codes}"
    )


def test_evaluate_horizon_coverage_blocks_above_cap() -> None:
    """evaluate_horizon_coverage must block when required steps exceed the cap."""
    decision = evaluate_horizon_coverage(
        required_steps=(OPENDATA_MAX_STEP_HOURS + 6,),
        live_max_step_hours=OPENDATA_MAX_STEP_HOURS,
    )
    assert decision.status == "BLOCKED"
    assert "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in decision.reason_codes


def test_cap_contraction_blocks_old_d10_steps() -> None:
    """Regression anchor: the OLD D+10 steps (228-252h) must now BLOCK at the 144 cap.

    Anchors that the 282h -> 144h contraction is load-bearing. Under fix/#134's old
    282h limit these were LIVE_ELIGIBLE; under the 5-day cap they must block.
    """
    decision = evaluate_horizon_coverage(
        required_steps=(228, 234, 240, 246, 252),
        live_max_step_hours=OPENDATA_MAX_STEP_HOURS,
    )
    assert decision.status == "BLOCKED", (
        "Old D+10 steps must block under the 144h cap — anchors the 282->144 "
        "contraction (Polymarket retired >5-day markets)."
    )


def test_download_output_path_filename_under_name_max() -> None:
    """Download filename must stay under NAME_MAX (255 bytes) on every supported FS.

    Regression anchor for the PR #94 fallout discovered 2026-05-09: a long joined
    step filename blew NAME_MAX on APFS/HFS+ (OSError 63). Filename now uses a short
    signature (range + count + sha8); fewer steps under the cap only strengthens this.
    """
    from datetime import date

    from src.data.ecmwf_open_data import _download_output_path

    path = _download_output_path(run_date=date(2026, 5, 9), run_hour=0, param="mx2t3")
    name_bytes = len(path.name.encode("utf-8"))
    assert name_bytes < 255, (
        f"Download filename is {name_bytes} bytes — exceeds NAME_MAX (255). "
        f"Filename: {path.name}"
    )


def test_step_hours_signature_is_stable() -> None:
    """Signature must be deterministic so cached GRIB files are reusable across restarts.

    The signature embeds min/max/count plus a sha8 of the comma-joined steps.
    Same STEP_HOURS -> same signature -> same filename -> cache hit.
    """
    from src.data.ecmwf_open_data import _step_hours_signature

    a = _step_hours_signature()
    b = _step_hours_signature()
    assert a == b
    # Format invariants: starts with min, contains 'to', count marker 'n', sha8 marker 'h'
    assert a.startswith(f"{min(STEP_HOURS)}to{max(STEP_HOURS)}_n{len(STEP_HOURS)}_h")
    assert len(a.split("_h")[-1]) == 8  # 8-hex-char digest
