# Created: 2026-06-05
# Last reused/audited: 2026-06-05
# Authority basis: day0 phased plan P2 (architect 2026-06-05) — collapse the two
#   absorbing-mask implementations to ONE. The complete-but-dark
#   evaluate_day0_absorbing_boundary (src/strategy/live_inference/absorbing_boundary.py)
#   is a SINGLE-BIN classifier with an incompatible interface (bin_kind + hard_fact_gate,
#   the latter redundant with reactor.py:518 DAY0_HARD_FACT_AUTHORITY_BLOCKED) wired
#   NOWHERE; the live vector masker _edli_day0_mask_for_analysis is the sole wired
#   producer. P2 HARDENS the live masker (replaces the dead `1.0 if … else 1.0`
#   tautology branches with explicit correct logic — a pure refactor, ZERO behavioral
#   delta) and DELETES the dark module.
#
#   These tests pin the COMPLETE absorbing-boundary truth table across EVERY
#   (metric x bin-kind) combination, including the cross-shoulder cases the P0 suite
#   does not explicitly enumerate, so the hardened explicit logic is proven equivalent
#   to the physical truth and the dead branches cannot be reintroduced as inversions.
"""P2 relationship tests: complete absorbing-mask truth table (the surviving mask)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from src.engine.evaluator import _edli_day0_mask_for_analysis


def _analysis(bins):
    return SimpleNamespace(bins=[SimpleNamespace(low=lo, high=hi) for lo, hi in bins])


def _mask(metric, rounded, bins):
    return _edli_day0_mask_for_analysis(_analysis(bins), {"metric": metric, "rounded_value": rounded})


# ---------------------------------------------------------------------------
# Physical truth (integer-settled bins; rounded_value is an integer):
#   HIGH: bin impossible  <=>  bin.high finite AND bin.high < H  (entire range below the observed high)
#   LOW : bin impossible  <=>  bin.low  finite AND bin.low  > L  (entire range above the observed low)
# The open-shoulder on the "growth" side (open-high for HIGH, open-low for LOW)
# is NEVER impossible — that is the dead-branch site being hardened.
# ---------------------------------------------------------------------------


# ----- HIGH: open-high shoulder is ALWAYS live (dead-branch hardening site) ----
def test_high_open_high_shoulder_never_masked_even_far_above():
    # observed high 50, open-high "≥45" bin: still the live winner; high=None must keep it.
    m = _mask("high", 50.0, [(40, 41), (42, 43), (45, None)])
    assert m[-1] == 1.0, "HIGH open-high shoulder (high=None) must NEVER be masked"


def test_high_open_high_shoulder_live_when_observed_below_its_floor():
    # Even when the observed high is BELOW the shoulder floor, the shoulder stays
    # live (the high may still climb into it). high=None branch must yield 1.0.
    m = _mask("high", 30.0, [(28, 29), (40, None)])
    assert list(m) == [0.0, 1.0]  # 28-29 entirely below 30 dies; open-high lives


# ----- HIGH: cross-shoulder — an open-LOW bin under a HIGH market -----
def test_high_open_low_bin_dies_when_entirely_below_observed():
    # open-low "≤25" (low=None, high=25). Under HIGH with observed 30: 25 < 30 => impossible.
    m = _mask("high", 30.0, [(None, 25), (30, 31), (30, None)])
    assert m[0] == 0.0, "HIGH: open-low bin whose finite high < observed must die"
    assert m[1] == 1.0 and m[2] == 1.0


def test_high_open_low_bin_lives_when_high_at_or_above_observed():
    # open-low "≤30" (high=30) with observed 30: 30 is not < 30 => still reachable.
    m = _mask("high", 30.0, [(None, 30), (31, 32)])
    assert m[0] == 1.0, "HIGH: open-low bin with finite high == observed must stay live"


# ----- LOW: open-low shoulder is ALWAYS live (dead-branch hardening site) -----
def test_low_open_low_shoulder_never_masked_even_far_below():
    m = _mask("low", 5.0, [(None, 9), (10, 11), (12, 13)])
    assert m[0] == 1.0, "LOW open-low shoulder (low=None) must NEVER be masked"


def test_low_open_low_shoulder_live_when_observed_above_its_ceiling():
    m = _mask("low", 20.0, [(None, 8), (25, 26)])
    assert list(m) == [1.0, 0.0]  # open-low lives; 25-26 entirely above 20 dies


# ----- LOW: cross-shoulder — an open-HIGH bin under a LOW market -----
def test_low_open_high_bin_dies_when_entirely_above_observed():
    # open-high "≥25" (low=25, high=None). Under LOW observed 14: 25 > 14 => impossible.
    m = _mask("low", 14.0, [(None, 13), (13, 14), (25, None)])
    assert m[-1] == 0.0, "LOW: open-high bin whose finite low > observed must die"
    assert m[0] == 1.0 and m[1] == 1.0


def test_low_open_high_bin_lives_when_low_at_or_below_observed():
    # open-high "≥14" (low=14) with observed 14: 14 is not > 14 => still reachable.
    m = _mask("low", 14.0, [(None, 13), (14, None)])
    assert m[-1] == 1.0, "LOW: open-high bin with finite low == observed must stay live"


# ----- Boundary: the bin CONTAINING the observed value always lives -----
def test_high_bin_containing_observed_high_lives():
    # °F range bin 60-65 with observed high rounding to 62: 62 <= 65 => bin reachable.
    m = _mask("high", 62.0, [(56, 57), (58, 59), (60, 65)])
    assert m[-1] == 1.0


def test_low_bin_containing_observed_low_lives():
    m = _mask("low", 62.0, [(60, 65), (66, 67), (68, 69)])
    assert m[0] == 1.0


# ----- Relationship: mask -> posterior renorm puts exactly 0 on impossible bins -----
def test_full_truth_table_drives_posterior_to_zero_on_impossible_only():
    bins = [(None, 25), (28, 29), (30, 31), (40, None)]  # open-low, finite, finite, open-high
    m = _mask("high", 30.0, bins)
    # impossible under HIGH@30: open-low high25<30 (die), 28-29 high29<30 (die);
    # 30-31 lives, open-high lives.
    np.testing.assert_array_equal(m, [0.0, 0.0, 1.0, 1.0])
    p = np.full(len(bins), 0.25)
    posterior = (p * m) / (p * m).sum()
    assert posterior[0] == 0.0 and posterior[1] == 0.0
    assert posterior[2] > 0.0 and posterior[3] > 0.0
    assert abs(float(posterior.sum()) - 1.0) < 1e-12
