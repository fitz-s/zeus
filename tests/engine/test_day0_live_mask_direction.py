# Created: 2026-06-05
# Last reused or audited: 2026-06-05
# Authority basis: day0 deep investigation 2026-06-05 (architect) — P0 of the phased day0 plan.
#   The LIVE day0 absorbing mask `_edli_day0_mask_for_analysis` (src/engine/evaluator.py:2531) was
#   directionally correct but pinned by ZERO relationship tests, and carries dead `1.0 if … else 1.0`
#   branches (:2543/:2548) + bare `>`/`<` (:2544/:2549) that are latent INVERSION sites: a careless
#   HIGH/LOW transpose or `>`/`<` flip would silently invert which bins survive and produce a
#   wrong-side trade with no test failure (the eligibility gate guards provenance, not the prob
#   vector). These tests pin the physical absorbing-boundary direction so an inversion is
#   UNCONSTRUCTABLE (fails loudly), and reproduce the #98 catastrophe (Paris 2026-06-01 low=14°C,
#   system bought NO on the observed-low bin). RELATIONSHIP tests: observed-so-far → mask → posterior.
"""P0 direction/relationship tests for the LIVE day0 absorbing mask.

Physical truth the mask must encode:
  HIGH market — the daily high only RISES. Observed-high-so-far = H ⇒ final high ≥ H ⇒ any bin whose
                entire range is BELOW H is impossible (mask 0); the bin containing H and bins above
                stay live; an open-high "≥X" shoulder stays live.
  LOW market  — the daily low only FALLS. Observed-low-so-far = L ⇒ final low ≤ L ⇒ any bin whose
                entire range is ABOVE L is impossible (mask 0); the bin containing L and bins below
                stay live; an open-low "≤X" shoulder stays live.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from src.engine.evaluator import _edli_day0_mask_for_analysis


def _analysis(bins):
    """A minimal stand-in: the mask only reads analysis.bins[i].low / .high and len()."""
    return SimpleNamespace(bins=[SimpleNamespace(low=lo, high=hi) for lo, hi in bins])


def _mask(metric, rounded, bins):
    return _edli_day0_mask_for_analysis(_analysis(bins), {"metric": metric, "rounded_value": rounded})


# ---------------------------------------------------------------------------
# HIGH — observed high locks out bins entirely BELOW it; never the ones above.
# ---------------------------------------------------------------------------
def test_high_zeros_bins_entirely_below_observed():
    # observed high-so-far = 30. Bins fully below 30 (28,29) impossible; 30 and above stay live.
    m = _mask("high", 30.0, [(28, 28), (29, 29), (30, 30), (31, 31), (31, None)])
    np.testing.assert_array_equal(m, [0.0, 0.0, 1.0, 1.0, 1.0])


def test_high_never_zeros_a_bin_at_or_above_observed():
    # DIRECTION PIN: a `>`/`<` or HIGH/LOW flip would start zeroing bins ABOVE the observed.
    # For HIGH, NO bin whose range is at/above the observed high may be masked out.
    bins = [(24, 24), (25, 25), (26, 26), (27, None)]
    m = _mask("high", 25.0, bins)
    # observed 25: only the strictly-below bin (24) dies; 25, 26, open-high stay live.
    assert m[0] == 0.0
    assert list(m[1:]) == [1.0, 1.0, 1.0], "HIGH mask must NEVER zero a bin at/above the observed high"


def test_high_open_high_shoulder_always_kept():
    # An open-high "≥X" bin is the winner once the high climbs into it — must always survive.
    m = _mask("high", 40.0, [(38, 38), (39, 39), (39, None)])
    assert m[-1] == 1.0


# ---------------------------------------------------------------------------
# LOW — observed low locks out bins entirely ABOVE it; never the ones below.
#   This is the #98 reproduction (Paris 2026-06-01 low=14°C wrong-side buy_no).
# ---------------------------------------------------------------------------
def test_low_zeros_bins_entirely_above_observed_paris_98():
    # #98: LOW market, observed low-so-far = 14°C. Final low ≤ 14 ⇒ bins above 14 (15,16) impossible;
    # 13 and 14 stay live. The "14°C" bin (the observed value) MUST stay live — zeroing it (or any
    # bin ≤ observed) is the inversion that lets a phantom buy_no edge form on the locked-in outcome.
    bins = [(None, 13), (13, 13), (14, 14), (15, 15), (16, 16)]
    m = _mask("low", 14.0, bins)
    np.testing.assert_array_equal(m, [1.0, 1.0, 1.0, 0.0, 0.0])
    # The observed-low bin itself is in the live support (final low could land exactly there):
    assert m[2] == 1.0, "#98: the observed-low bin must NOT be masked out (it is achievable)"


def test_low_never_zeros_a_bin_at_or_below_observed():
    bins = [(None, 10), (11, 11), (12, 12), (13, 13)]
    m = _mask("low", 12.0, bins)
    # observed 12: only the strictly-above bin (13) dies; open-low, 11, 12 stay live.
    assert m[-1] == 0.0
    assert list(m[:-1]) == [1.0, 1.0, 1.0], "LOW mask must NEVER zero a bin at/below the observed low"


def test_low_open_low_shoulder_always_kept():
    m = _mask("low", 5.0, [(None, 7), (8, 8), (9, 9)])
    assert m[0] == 1.0


# ---------------------------------------------------------------------------
# Relationship boundary: mask → p_posterior. Impossible bins carry exactly 0 mass after renorm.
# ---------------------------------------------------------------------------
def test_mask_drives_posterior_mass_to_zero_on_impossible_bins():
    bins = [(13, 13), (14, 14), (15, 15), (16, 16)]
    m = _mask("low", 14.0, bins)
    p = np.full(len(bins), 0.25)            # any prior
    posterior = p * m
    posterior = posterior / posterior.sum()  # the live renorm (evaluator.py:2585-2588)
    assert posterior[2] == 0.0 and posterior[3] == 0.0, "bins above the observed low must hold 0 posterior"
    assert posterior[0] > 0.0 and posterior[1] > 0.0
    assert abs(float(posterior.sum()) - 1.0) < 1e-12


# ---------------------------------------------------------------------------
# No observation → no mask (all-ones; the mask must never fabricate a constraint).
# ---------------------------------------------------------------------------
def test_missing_rounded_value_is_all_ones():
    m = _edli_day0_mask_for_analysis(_analysis([(13, 13), (14, 14)]), {"metric": "low"})
    np.testing.assert_array_equal(m, [1.0, 1.0])
