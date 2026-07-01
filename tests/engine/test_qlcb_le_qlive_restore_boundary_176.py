# Created: 2026-06-04
# Last reused/audited: 2026-06-08
# Authority basis: docs/archive/2026-Q2/operations_historical/CONSOLIDATED_AUDIT_AND_PLAN_2026-06-04.md R5/#176
#   (lcb>point inversion) + "bin selection.md" §5.6 / §9 Hidden #2/#3 / §12.B.1 +
#   operator directive 2026-06-08 (S2 single-primary q_lcb from probability samples).
#
# LAW CHANGE (bin-selection S2, 2026-06-08): the #176 inversion was a SYMPTOM of the
# Hidden #2 disease — q_lcb was restored as `edge_ci_lower + cost`, which is NOT a
# probability lower bound and could land ABOVE its own point. The old fix was a
# clamp at the _generate_candidate_proofs boundary (`if q_lcb > q_value: q_lcb =
# q_value`). S2 CURES the disease at the source: q_lcb is now the lower quantile of
# the YES *probability* samples ALONE (canonical: _side_q_lcb_from_yes_samples /
# ProbabilityUncertainty; replacement: _replacement_no_lcb_for_bin via 1 - q_ucb_yes),
# and BOTH sides are clamped under their own point IN THE SEAM. The boundary clamp is
# therefore dead and was removed (no redundant gate — operator directive). This test
# now pins the invariant AT THE SEAM where q_lcb is born, for BOTH the YES and the
# native-NO leg, so `q_lcb_side <= q_point_side` is unconstructable at the source.
"""Relationship test for #176 / Hidden #2 — q_lcb <= q_point at the proof boundary.

The invariant is identical (`q_lcb_5pct <= q_posterior` on every recorded proof);
only the MECHANISM that guarantees it moved upstream. These tests drive the real
q-construction seam (not a mock that injects an impossible carrier value, which the
seam can no longer emit) with adversarial probability samples and assert the bound.
"""
from __future__ import annotations

import numpy as np
import types

from src.engine.event_reactor_adapter import (
    _side_q_lcb_from_yes_samples,
    _replacement_no_lcb_for_bin,
)


def test_canonical_seam_q_lcb_le_q_point_both_sides():
    """§12.B / #176 at the source: for adversarial YES samples whose lower tail sits
    above the live point, BOTH side q_lcbs are clamped under their own point.

    A bootstrap whose member resampling concentrates near the ceiling can push the
    5th percentile of q_yes ABOVE the live-inference point estimate (the two modules
    normalise differently — the exact #176 cross-module drift). The seam clamps the
    YES q_lcb under the YES point and the NO q_lcb under the NO point (1 - yes_point).
    """
    # YES samples tightly clustered at 0.90 (lower tail ~0.90) while the live point is
    # a deliberately LOWER 0.50 (normalisation divergence) — the #176 inversion setup.
    yes_samples = np.full(2000, 0.90)
    yes_point = 0.50
    q_lcb_yes, q_lcb_no = _side_q_lcb_from_yes_samples(yes_samples, q_yes_point=yes_point)

    # YES leg: lower bound cannot exceed its own (live) point.
    assert q_lcb_yes <= yes_point + 1e-9, (
        f"q_lcb_yes={q_lcb_yes:.6f} > yes_point={yes_point:.6f} (Hidden #2 inversion)"
    )
    # NO leg: lower bound cannot exceed the NO point (1 - yes_point).
    no_point = 1.0 - yes_point
    assert q_lcb_no <= no_point + 1e-9, (
        f"q_lcb_no={q_lcb_no:.6f} > no_point={no_point:.6f} (Hidden #2 inversion, NO leg)"
    )
    # Both bounds are valid probabilities.
    assert 0.0 <= q_lcb_yes <= 1.0
    assert 0.0 <= q_lcb_no <= 1.0


def test_canonical_seam_native_no_is_complement_quantile_not_point_complement():
    """Hidden #3 at the seam: q_lcb_no == 1 - q_ucb_yes (lower tail of 1 - q_yes),
    NOT 1 - q_lcb_yes. Proven with an ASYMMETRIC YES sample distribution so the two
    differ materially."""
    rng = np.random.default_rng(176)
    # Right-skewed YES samples in [0.1, 0.9]: lower tail (q_lcb_yes) and upper tail
    # (q_ucb_yes) are asymmetric, so 1 - q_ucb_yes != 1 - q_lcb_yes.
    yes_samples = np.clip(0.3 + 0.4 * rng.beta(2.0, 5.0, size=5000), 0.0, 1.0)
    yes_point = float(np.mean(yes_samples))
    q_lcb_yes, q_lcb_no = _side_q_lcb_from_yes_samples(yes_samples, q_yes_point=yes_point)

    from src.strategy.probability_uncertainty import upper_quantile, lower_quantile, no_side_samples

    q_ucb_yes = upper_quantile(yes_samples)
    # The native-NO robust lower bound IS the complement-quantile.
    assert abs(q_lcb_no - (1.0 - q_ucb_yes)) < 1e-9, (
        f"q_lcb_no={q_lcb_no:.6f} != 1 - q_ucb_yes={1.0 - q_ucb_yes:.6f}"
    )
    assert abs(q_lcb_no - lower_quantile(no_side_samples(yes_samples))) < 1e-9
    # And it is NOT the point-complement 1 - q_lcb_yes (Hidden #3) — materially apart.
    assert abs(q_lcb_no - (1.0 - q_lcb_yes)) > 1e-3, (
        f"q_lcb_no={q_lcb_no:.6f} collapsed onto 1 - q_lcb_yes={1.0 - q_lcb_yes:.6f} "
        "(Hidden #3: NO lower bound must be the complement quantile, not point complement)"
    )


def test_replacement_no_lcb_is_one_minus_qucb_and_le_no_point():
    """Replacement_0_1 native-NO authority (Hidden #3): q_lcb_no = 1 - q_ucb_yes from
    the bundle's q_ucb map, clamped under the NO point (1 - q_yes)."""
    # Bundle with a per-bin q_ucb map; q_ucb_yes=0.80 => q_lcb_no = 0.20.
    bundle = types.SimpleNamespace(q_ucb={"bin-1": 0.80}, provenance_json={})
    q_yes = 0.30
    no_lcb = _replacement_no_lcb_for_bin(bundle, bin_id="bin-1", q_yes=q_yes)
    assert abs(no_lcb - (1.0 - 0.80)) < 1e-9, f"expected 1 - q_ucb_yes=0.20, got {no_lcb:.6f}"
    # NO point is 1 - q_yes = 0.70; the bound is under it.
    assert no_lcb <= (1.0 - q_yes) + 1e-9

    # A bin with very high q_ucb_yes (0.99) yields a tiny NO lower bound (0.01) — the
    # native-NO authority is conservative exactly when YES is confident.
    bundle_hi = types.SimpleNamespace(q_ucb={"bin-2": 0.99}, provenance_json={})
    assert abs(_replacement_no_lcb_for_bin(bundle_hi, bin_id="bin-2", q_yes=0.95) - 0.01) < 1e-9

    # Absent a bundle q_ucb there is NO native NO authority -> 0.0 (fail-closed),
    # NEVER derived from the YES q_lcb (Hidden #4: native NO needs native evidence).
    bundle_none = types.SimpleNamespace(q_ucb=None, provenance_json={})
    assert _replacement_no_lcb_for_bin(bundle_none, bin_id="bin-3", q_yes=0.30) == 0.0
