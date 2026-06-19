# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md
#   §"THE q_lcb RELIABILITY GUARD — exact form" + step 6. The guard serves
#   q_safe = min(band.q_lcb, L_g) and abstains (q_safe=0) on a thin / below-floor OOF cell.
"""RED-on-revert tests for the q_lcb empirical reliability guard (FINAL no-shadow §6).

  * a WELL-CALIBRATED cell (n >= N_MIN, realized hit-rate well above the bucket floor) ⇒ the
    guard SERVES q_safe = min(band_q_lcb, L_g), trade=True, NOT abstained.
  * a MISCALIBRATED cell (realized hit-rate below the bucket floor) ⇒ q_safe deflated to 0,
    abstained=True.
  * a THIN cell (n < N_MIN) ⇒ abstained even if the point hit-rate looks high (Wilson lower
    bound on a thin sample is conservative AND the N_MIN gate fires).
  * an UNKNOWN cell (artifact absent / cell not in table) ⇒ INERT pass-through (q_safe ==
    band_q_lcb, trade=True, NOT abstained) — byte-identical to pre-guard behavior.

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
    key = f"high|L1|modal|qb{bucket_idx}"
    table = {key: (500, 0.85)}
    v = apply_guard(
        band_q_lcb=band_q_lcb, metric="high", lead_days=1.0,
        bin_position="modal", reliability_table=table,
    )
    assert v.trade is True
    assert v.abstained is False
    assert v.basis == "OOF_WILSON_95"
    # q_safe = min(band_q_lcb, L_g). L_g ~ 0.82 > 0.72 -> q_safe == band_q_lcb.
    assert v.L_g > bucket_floor
    assert math.isclose(v.q_safe, min(band_q_lcb, v.L_g), rel_tol=1e-9)


def test_miscalibrated_cell_abstains_q_safe_zero():
    # A deep cell whose realized hit-rate (0.55) is FAR below the bucket floor (0.7) for a
    # band_q_lcb of 0.72 -> L_g << floor -> NOT licensed -> abstain, q_safe = 0.
    band_q_lcb = 0.72
    bucket_idx, _floor = qlcb_bucket(band_q_lcb)
    key = f"high|L1|modal|qb{bucket_idx}"
    table = {key: (500, 0.55)}
    v = apply_guard(
        band_q_lcb=band_q_lcb, metric="high", lead_days=1.0,
        bin_position="modal", reliability_table=table,
    )
    assert v.abstained is True
    assert v.trade is False
    assert v.q_safe == 0.0


def test_thin_cell_abstains_even_with_high_point_rate():
    # n < N_MIN -> the N_MIN gate fires regardless of the point hit-rate -> abstain.
    band_q_lcb = 0.72
    bucket_idx, _floor = qlcb_bucket(band_q_lcb)
    key = f"high|L1|modal|qb{bucket_idx}"
    table = {key: (N_MIN - 1, 1.0)}  # perfect but thin
    v = apply_guard(
        band_q_lcb=band_q_lcb, metric="high", lead_days=1.0,
        bin_position="modal", reliability_table=table,
    )
    assert v.abstained is True
    assert v.q_safe == 0.0


def test_unknown_cell_is_inert_passthrough():
    # Empty table (artifact absent / cell unseen) -> INERT: serve band_q_lcb, trade=True.
    v = apply_guard(
        band_q_lcb=0.61, metric="high", lead_days=1.0,
        bin_position="nonmodal", reliability_table={},
    )
    assert v.basis == "INERT"
    assert v.abstained is False
    assert v.trade is True
    assert v.q_safe == 0.61


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
