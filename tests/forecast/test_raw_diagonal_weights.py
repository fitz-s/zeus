# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md §2
#   ("Wire walk_forward_model_weights to raw second-moment") + consult_resolution_ledger_2026-06-17.md
#   BLOCKER 3 (precision/covariance basis = raw 2nd-moment under RAW; NOT demeaned var/se/equal).
"""RED-on-revert tests for the RAW diagonal precision weights (FINAL no-shadow §2).

``walk_forward_model_weights`` must form ``w_m ∝ 1/max(Ê[(x−Y)²], SIGMA_FLOOR²)`` from
``RawModelMember.walk_forward_raw_m2_native`` — the RAW second moment (bias² INCLUDED), NOT
the inverse demeaned variance / SE, NOT equal weights. These tests go RED if the basis is
reverted to the old equal-weight (``walk_forward_se_native`` never set → 1/n) or to any
``np.var`` / ``np.std`` / demeaned basis:

  * ``test_divergent_raw_m2_diverges_from_equal_upweighting_lowest`` — distinct per-model raw
    second moments ⇒ the weights are NOT 1/n and the model with the SMALLEST Ê[(x−Y)²] (the
    most precise) carries the LARGEST weight. Reverting to equal weights makes this RED.
  * ``test_absent_history_collapses_to_equal`` — members with no raw_m2 / n==0 ⇒ exactly 1/n.
  * ``test_thin_history_shrinks_toward_equal`` — a thin (n < MIN_TRAIN) but very precise member
    does NOT fully dominate; its weight is between equal and the deep-history full-precision
    weight (the EB shrink-to-equal). Reverting the shrink lets a thin member dominate.
  * ``test_weights_are_nonneg_and_sum_to_one`` — the INV-C1 convexity precondition holds.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from src.forecast.bayes_precision_fusion import MIN_TRAIN, SIGMA_FLOOR
from src.forecast.center import walk_forward_model_weights

# Reuse the Tokyo fixtures from the envelope test (one fixture vocabulary).
from tests.forecast.test_center_envelope import _case, _member


def _member_m2(model_id: str, value: float, raw_m2, n: int):
    base = _member(model_id, value)
    return replace(base, walk_forward_raw_m2_native=raw_m2, walk_forward_n=n)


def test_divergent_raw_m2_diverges_from_equal_upweighting_lowest():
    # Three deep-history members with DISTINCT raw second moments. n >= MIN_TRAIN so no shrink.
    # m_best has the SMALLEST E[r^2] (most precise) -> it MUST carry the largest weight.
    members = [
        _member_m2("m_best", 21.0, raw_m2=1.0, n=MIN_TRAIN + 50),   # precise
        _member_m2("m_mid", 22.0, raw_m2=4.0, n=MIN_TRAIN + 50),    # mid
        _member_m2("m_worst", 23.0, raw_m2=16.0, n=MIN_TRAIN + 50),  # noisy
    ]
    w = walk_forward_model_weights(_case(), members)

    # NOT equal weights (the reverted equal-weight basis would give 1/3 each).
    assert not np.allclose(w, np.full(3, 1.0 / 3.0)), (
        "weights collapsed to equal — the RAW second-moment basis was reverted to equal-weight"
    )
    # The lowest E[r^2] model carries the largest weight; strictly monotone in 1/E[r^2].
    assert w[0] > w[1] > w[2], f"weights not monotone in precision: {w}"
    # Quantitatively the basis is 1/max(E[r^2], floor^2): with floor^2 = 0.64 < 1.0, the
    # precisions are 1/1, 1/4, 1/16 = 1, 0.25, 0.0625 -> normalized.
    expected = np.array([1.0, 0.25, 0.0625])
    expected = expected / expected.sum()
    assert np.allclose(w, expected, atol=1e-9), (
        f"weights {w} are not 1/E[r^2] normalized {expected} — a demeaned-var / se basis would differ"
    )


def test_absent_history_collapses_to_equal():
    # No raw_m2 signal on any member (None / n==0) -> exactly equal 1/n (the dormant-seam posture).
    members = [
        _member_m2("a", 21.0, raw_m2=None, n=0),
        _member_m2("b", 22.0, raw_m2=None, n=0),
        _member_m2("c", 23.0, raw_m2=None, n=0),
        _member_m2("d", 24.0, raw_m2=None, n=0),
    ]
    w = walk_forward_model_weights(_case(), members)
    assert np.allclose(w, np.full(4, 0.25)), f"absent history did not give equal 1/n: {w}"


def test_thin_history_shrinks_toward_equal():
    # A thin (n=2 << MIN_TRAIN) but VERY precise member must NOT fully dominate; the EB
    # shrink pulls its effective E[r^2] toward the equal-precision floor. A deep noisy member
    # is its counterweight. The thin member's weight is strictly between the equal weight and
    # the weight it WOULD get with no shrink.
    thin_precise = _member_m2("thin", 21.0, raw_m2=0.5, n=2)        # tiny n, tiny E[r^2]
    deep_noisy = _member_m2("deep", 23.0, raw_m2=9.0, n=MIN_TRAIN + 100)
    w = walk_forward_model_weights(_case(), [thin_precise, deep_noisy])

    equal = 0.5
    # WITHOUT shrink the thin member's precision (1/max(0.5, 0.64)=1/0.64≈1.5625) would crush
    # the deep member (1/9≈0.111) -> weight ≈ 0.93. WITH the EB shrink toward equal at n=2 the
    # thin member is pulled DOWN. Assert it is below the no-shrink dominance and above equal.
    assert w[0] > equal, "thin precise member should still outweigh a deep noisy one"
    assert w[0] < 0.90, (
        f"thin member weight {w[0]:.3f} ~ the no-shrink dominance — the low-n EB shrink was reverted"
    )


def test_weights_are_nonneg_and_sum_to_one():
    members = [
        _member_m2("a", 21.0, raw_m2=1.0, n=MIN_TRAIN + 1),
        _member_m2("b", 22.0, raw_m2=None, n=0),       # mixed: one absent
        _member_m2("c", 23.0, raw_m2=4.0, n=3),        # one thin
    ]
    w = walk_forward_model_weights(_case(), members)
    assert np.all(w >= 0.0), f"negative weight: {w}"
    assert pytest.approx(float(w.sum()), abs=1e-12) == 1.0


def test_floor_caps_precision_of_a_subfloor_raw_m2():
    # A member with a raw second moment BELOW the floor^2 cannot earn infinite precision: it is
    # capped at 1/floor^2, so two such members (one at 0.01, one at 0.10, both << 0.64) get the
    # SAME (floored) precision and therefore equal weight.
    members = [
        _member_m2("tiny1", 21.0, raw_m2=0.01, n=MIN_TRAIN + 10),
        _member_m2("tiny2", 22.0, raw_m2=0.10, n=MIN_TRAIN + 10),
    ]
    w = walk_forward_model_weights(_case(), members)
    assert np.allclose(w, np.full(2, 0.5)), (
        f"sub-floor raw_m2 not capped at 1/floor^2 (got {w}); the certainty cap was removed"
    )
