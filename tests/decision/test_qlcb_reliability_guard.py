# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md
#   §"THE q_lcb RELIABILITY GUARD — exact form" + step 6. The guard serves
#   q_safe = min(band.q_lcb, L_g) on known deep OOF cells and abstains (q_safe=0)
#   only on thin / missing cells.
"""RED-on-revert tests for the q_lcb empirical reliability guard (FINAL no-shadow §6).

  * a WELL-CALIBRATED cell (n >= N_MIN, realized hit-rate well above the bucket floor) ⇒ the
    guard SERVES q_safe = min(band_q_lcb, L_g), trade=True, NOT abstained.
  * a MISCALIBRATED deep cell (realized hit-rate below the bucket floor) ⇒ q_safe is
    continuously deflated to min(band_q_lcb, L_g), not zeroed by a second binary veto.
  * a THIN cell (n < N_MIN) ⇒ abstained even if the point hit-rate looks high (Wilson lower
    bound on a thin sample is conservative AND the N_MIN gate fires).
  * an UNKNOWN cell with no artifact ⇒ INERT pass-through (q_safe == band_q_lcb, trade=True,
    NOT abstained) — byte-identical to pre-guard behavior.
  * a MISSING cell inside an active artifact ⇒ abstain; an active live artifact cannot silently
    authorize a side/bin it did not grade.

Reverting the guard to "serve band.q_lcb unconditionally" makes the abstain cases RED.
"""
from __future__ import annotations

import math

from src.decision import qlcb_reliability_guard as guard_mod
from src.decision.qlcb_reliability_guard import (
    N_MIN,
    apply_guard,
    qlcb_bucket,
    wilson_lower_bound_95,
)


def test_wilson_lower_bound_is_conservative_on_thin_samples():
    # 9/10 = 0.9 point, but the 95% Wilson lower bound is well below 0.9 (thin sample).
    lo_thin = wilson_lower_bound_95(9, 10)
    lo_deep = wilson_lower_bound_95(900, 1000)
    assert 0.0 <= lo_thin < 0.9
    assert lo_deep > lo_thin  # the deep sample at the same rate has a TIGHTER lower bound
    assert wilson_lower_bound_95(5, 0) == 0.0  # degenerate n


def test_well_calibrated_cell_serves_min_band_and_Lg():
    # A deep cell whose realized hit-rate (0.85) sits well above the bucket floor for a
    # band_q_lcb of 0.72 (bucket [0.7, 0.8) -> floor 0.7). L_g for 0.85 over n=500 is ~0.82.
    band_q_lcb = 0.72
    bucket_idx, bucket_floor = qlcb_bucket(band_q_lcb)
    key = f"high|L1|YES|modal|qb{bucket_idx}"
    table = {key: (500, 0.85)}
    v = apply_guard(
        band_q_lcb=band_q_lcb, metric="high", lead_days=1.0,
        side="YES", bin_position="modal", reliability_table=table,
    )
    assert v.trade is True
    assert v.abstained is False
    assert v.basis == "OOF_WILSON_95"
    # q_safe = min(band_q_lcb, L_g). L_g ~ 0.82 > 0.72 -> q_safe == band_q_lcb.
    assert v.L_g > bucket_floor
    assert math.isclose(v.q_safe, min(band_q_lcb, v.L_g), rel_tol=1e-9)


def test_miscalibrated_deep_cell_deflates_to_wilson_not_zero():
    # A deep cell whose realized hit-rate (0.55) is FAR below the bucket floor (0.7) for a
    # band_q_lcb of 0.72 -> L_g << floor. The guard corrects the lower bound to L_g; it
    # does not add a binary veto. Route cost decides whether that deflated q still trades.
    band_q_lcb = 0.72
    bucket_idx, _floor = qlcb_bucket(band_q_lcb)
    key = f"high|L1|YES|modal|qb{bucket_idx}"
    table = {key: (500, 0.55)}
    v = apply_guard(
        band_q_lcb=band_q_lcb, metric="high", lead_days=1.0,
        side="YES", bin_position="modal", reliability_table=table,
    )
    assert v.abstained is False
    assert v.trade is True
    assert 0.0 < v.q_safe < band_q_lcb
    assert math.isclose(v.q_safe, min(band_q_lcb, v.L_g), rel_tol=1e-9)


def test_thin_cell_abstains_even_with_high_point_rate():
    # n < N_MIN -> the N_MIN gate fires regardless of the point hit-rate -> abstain.
    band_q_lcb = 0.72
    bucket_idx, _floor = qlcb_bucket(band_q_lcb)
    key = f"high|L1|YES|modal|qb{bucket_idx}"
    table = {key: (N_MIN - 1, 1.0)}  # perfect but thin
    v = apply_guard(
        band_q_lcb=band_q_lcb, metric="high", lead_days=1.0,
        side="YES", bin_position="modal", reliability_table=table,
    )
    assert v.abstained is True
    assert v.q_safe == 0.0


def test_unknown_cell_is_inert_passthrough():
    # Empty table (artifact absent / cell unseen) -> INERT: serve band_q_lcb, trade=True.
    v = apply_guard(
        band_q_lcb=0.61, metric="high", lead_days=1.0,
        side="YES", bin_position="nonmodal", reliability_table={},
    )
    assert v.basis == "INERT"
    assert v.abstained is False
    assert v.trade is True
    assert v.q_safe == 0.61


def test_injected_active_empty_table_abstains():
    """An active artifact with zero usable cells is not the same as artifact absence."""

    v = apply_guard(
        band_q_lcb=0.61,
        metric="high",
        lead_days=1.0,
        side="YES",
        bin_position="nonmodal",
        reliability_table={},
        reliability_artifact_active=True,
    )

    assert v.basis == "OOF_WILSON_95_MISSING_CELL"
    assert v.abstained is True
    assert v.trade is False
    assert v.q_safe == 0.0


def test_missing_cell_inside_active_table_abstains():
    # Once an artifact is active, an unseen side-aware cell is not authority.
    active_table = {"high|L1|YES|modal|qb1": (500, 0.5)}
    v = apply_guard(
        band_q_lcb=0.76,
        metric="high",
        lead_days=1.0,
        side="NO",
        bin_position="nonmodal",
        reliability_table=active_table,
    )
    assert v.basis == "OOF_WILSON_95_MISSING_CELL"
    assert v.abstained is True
    assert v.trade is False
    assert v.q_safe == 0.0


def test_side_specific_cell_required_for_no_claim():
    band_q_lcb = 0.72
    bucket_idx, _bucket_floor = qlcb_bucket(band_q_lcb)
    yes_only = {f"high|L1|YES|nonmodal|qb{bucket_idx}": (500, 0.90)}
    no_missing = apply_guard(
        band_q_lcb=band_q_lcb,
        metric="high",
        lead_days=1.0,
        side="NO",
        bin_position="nonmodal",
        reliability_table=yes_only,
    )
    assert no_missing.abstained is True

    no_table = {f"high|L1|NO|nonmodal|qb{bucket_idx}": (500, 0.90)}
    no_licensed = apply_guard(
        band_q_lcb=band_q_lcb,
        metric="high",
        lead_days=1.0,
        side="NO",
        bin_position="nonmodal",
        reliability_table=no_table,
    )
    assert no_licensed.abstained is False
    assert no_licensed.basis == "OOF_WILSON_95"


def test_guard_is_inert_when_artifact_absent_default_load(tmp_path, monkeypatch):
    # With NO injected table and a missing artifact path, _load_reliability_table is empty ->
    # the guard is INERT for any cell (the current live state — byte-identical to pre-guard).
    monkeypatch.setattr(
        guard_mod, "_QLCB_OOF_RELIABILITY_PATH", str(tmp_path / "does_not_exist.json")
    )
    guard_mod.reset_reliability_cache()
    v = apply_guard(band_q_lcb=0.88, metric="low", lead_days=2.0, bin_position="modal")
    assert v.basis == "INERT"
    assert v.q_safe == 0.88
    guard_mod.reset_reliability_cache()


def test_present_malformed_artifact_is_active_fail_closed(tmp_path, monkeypatch):
    artifact = tmp_path / "qlcb_oof_reliability.json"
    artifact.write_text("{not-json")
    monkeypatch.setattr(guard_mod, "_QLCB_OOF_RELIABILITY_PATH", str(artifact))
    guard_mod.reset_reliability_cache()

    v = apply_guard(band_q_lcb=0.88, metric="low", lead_days=2.0, bin_position="modal")

    assert v.basis == "OOF_WILSON_95_MISSING_CELL"
    assert v.abstained is True
    assert v.q_safe == 0.0
    status = guard_mod.reliability_artifact_status()
    assert status["active"] is True
    assert status["status"] == "ACTIVE_INVALID"
    guard_mod.reset_reliability_cache()
