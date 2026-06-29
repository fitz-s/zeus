# Created: 2026-06-21
# Last audited: 2026-06-21
# Authority basis: Option C raw-precision representativeness center warming
#   (consult REQ-20260621-033315; forecast-gap-is-data-precision). RED-first TDD
#   for the shared raw_precision_center / raw_second_moment_weights repr channel:
#   floor-residual-FIRST-then-add-repr (Form A), cold-start rule, C/F invariance,
#   no-invention. Antibody: deleting the repr widening makes direction/cold-start RED.
"""RED-first TDD — representativeness in the RAW diagonal precision center basis.

These tests drive the shared helper that threads per-model grid-representativeness
variance (degC²) into the RAW second-moment precision denominator that produces the
SERVED traded center ``_mu_diagonal = Σ w_m·z_m``. The contract (consult §57-75):

  * direction      — a colder FAR member with larger repr loses weight ⇒ μ WARMS
  * magnitude      — exact formula match to Σ w z, tight tolerance
  * no-invention   — equal z ⇒ μ unmoved; warmer-far ⇒ μ COOLS (stays in envelope)
  * cold-start     — no raw m2 but positive repr ⇒ weights NOT equal; no repr ⇒ equal
  * low-n          — repr added AFTER EB shrink (floored residual first, then + repr)
  * C/F invariance — repr converted to native² basis ⇒ relative weights invariant
"""
from __future__ import annotations

import numpy as np
import pytest

from src.forecast.bayes_precision_fusion import KAPPA, LOWN_INFLATE, SIGMA_FLOOR
from src.forecast.center import MIN_SETTLED_N, raw_precision_center, raw_second_moment_weights


# ---------------------------------------------------------------------------
# Reference implementation of Form A (floor residual FIRST, then add repr) so the
# tests assert against an INDEPENDENT formula, not the production code's own math.
# ---------------------------------------------------------------------------
def _ref_weights(
    raw_m2_and_n: dict[str, tuple[float | None, int]],
    repr_m2_by_model: dict[str, float] | None,
    unit: str,
) -> dict[str, float]:
    models = list(raw_m2_and_n.keys())
    n = len(models)
    if n == 0:
        return {}
    u = (9.0 / 5.0) ** 2 if unit == "F" else 1.0
    floor_m2 = (SIGMA_FLOOR * SIGMA_FLOOR) * u
    equal_m2 = ((SIGMA_FLOOR * LOWN_INFLATE) ** 2) * u
    repr_by = repr_m2_by_model or {}

    def _repr_native(m: str) -> float:
        r = float(repr_by.get(m, 0.0) or 0.0)
        if not np.isfinite(r) or r <= 0.0:
            return 0.0
        return r  # caller supplies repr in the same basis as raw_m2 (no scaling here)

    precisions: dict[str, float] = {}
    have_signal = False
    for m in models:
        raw_m2, n_train = raw_m2_and_n[m]
        rr = _repr_native(m)
        try:
            raw_m2_f = float(raw_m2) if raw_m2 is not None else None
        except (TypeError, ValueError):
            raw_m2_f = None
        if raw_m2_f is None or not np.isfinite(raw_m2_f) or raw_m2_f <= 0.0 or n_train <= 0:
            if rr <= 0.0:
                base = equal_m2  # no signal at all for this member
            else:
                have_signal = True
                base = equal_m2  # cold-start: equal prior + repr below
        else:
            have_signal = True
            if n_train < MIN_SETTLED_N:
                lam = n_train / (n_train + KAPPA)
                base = lam * raw_m2_f + (1.0 - lam) * equal_m2
            else:
                base = raw_m2_f
        denom = max(base, floor_m2) + rr  # FORM A: floor residual first, then add repr
        precisions[m] = 1.0 / denom
    # have_signal true if any raw m2 OR any positive repr
    if not have_signal:
        eq = 1.0 / n
        return {m: eq for m in models}
    total = sum(precisions.values())
    return {m: v / total for m, v in precisions.items()}


# ============================================================================
# Backward-compatibility: repr=None is byte-identical to the pre-Option-C helper.
# ============================================================================
class TestBackwardCompatible:
    def test_none_repr_byte_identical(self):
        d = {"a": (0.5, 30), "b": (2.0, 40), "c": (None, 0)}
        base = raw_second_moment_weights(d, unit="C")
        with_none = raw_second_moment_weights(d, unit="C", repr_m2_by_model=None)
        assert base.keys() == with_none.keys()
        for m in base:
            assert base[m] == with_none[m]

    def test_empty_repr_dict_byte_identical(self):
        d = {"a": (0.5, 30), "b": (2.0, 40)}
        base = raw_second_moment_weights(d, unit="C")
        empty = raw_second_moment_weights(d, unit="C", repr_m2_by_model={})
        for m in base:
            assert base[m] == empty[m]

    def test_zero_repr_byte_identical(self):
        d = {"a": (0.5, 30), "b": (2.0, 40)}
        base = raw_second_moment_weights(d, unit="C")
        zeros = raw_second_moment_weights(d, unit="C", repr_m2_by_model={"a": 0.0, "b": 0.0})
        for m in base:
            assert abs(base[m] - zeros[m]) < 1e-15


# ============================================================================
# Direction + magnitude.
# ============================================================================
class TestDirection:
    def test_cold_far_member_loses_weight_center_warms(self):
        """z_far_cold colder; repr_far large ⇒ far loses weight ⇒ μ WARMS."""
        d = {"far": (1.0, 40), "near": (1.0, 40)}  # identical raw m2 + n
        z = {"far": 30.0, "near": 31.0}
        repr_by = {"far": 4.0, "near": 0.0}  # far cell is coarse/distant

        w_base = raw_second_moment_weights(d, unit="C")
        _, mu_base = raw_precision_center(d, z, unit="C")
        w_repr = raw_second_moment_weights(d, unit="C", repr_m2_by_model=repr_by)
        _, mu_repr = raw_precision_center(d, z, unit="C", repr_m2_by_model=repr_by)

        assert w_repr["near"] > w_base["near"], "near (warm) must gain weight"
        assert w_repr["far"] < w_base["far"], "far (cold) must lose weight"
        assert mu_repr > mu_base, f"center must warm: {mu_repr} !> {mu_base}"

    def test_magnitude_exact_formula(self):
        d = {"far": (0.5, 40), "near": (1.0, 40)}
        z = {"far": 28.0, "near": 31.0}
        repr_by = {"far": 4.0, "near": 0.0}
        ref = _ref_weights(d, repr_by, "C")
        got = raw_second_moment_weights(d, unit="C", repr_m2_by_model=repr_by)
        for m in ref:
            assert abs(got[m] - ref[m]) < 1e-12, f"{m}: {got[m]} != {ref[m]}"
        mu_ref = sum(ref[m] * z[m] for m in ref)
        _, mu_got = raw_precision_center(d, z, unit="C", repr_m2_by_model=repr_by)
        assert abs(mu_got - mu_ref) < 1e-12


# ============================================================================
# No-invention (single-truth, convex, no-debias).
# ============================================================================
class TestNoInvention:
    def test_equal_z_center_unmoved(self):
        """Equal member values: μ must not move regardless of repr."""
        d = {"far": (1.0, 40), "near": (1.0, 40)}
        z = {"far": 30.0, "near": 30.0}
        repr_by = {"far": 5.0, "near": 0.0}
        _, mu_base = raw_precision_center(d, z, unit="C")
        _, mu_repr = raw_precision_center(d, z, unit="C", repr_m2_by_model=repr_by)
        assert abs(mu_repr - mu_base) < 1e-12
        assert abs(mu_repr - 30.0) < 1e-12

    def test_warmer_far_member_cools_center(self):
        """If the FAR (penalized) member is WARMER, the same repr penalty COOLS μ."""
        d = {"far": (1.0, 40), "near": (1.0, 40)}
        z = {"far": 32.0, "near": 30.0}  # far is the warm one now
        repr_by = {"far": 4.0, "near": 0.0}
        _, mu_base = raw_precision_center(d, z, unit="C")
        _, mu_repr = raw_precision_center(d, z, unit="C", repr_m2_by_model=repr_by)
        assert mu_repr < mu_base, "penalizing the warm member must cool the center"

    def test_center_stays_in_member_envelope(self):
        d = {"far": (0.3, 40), "near": (5.0, 40), "mid": (1.0, 40)}
        z = {"far": 28.0, "near": 31.0, "mid": 29.5}
        repr_by = {"far": 6.0, "near": 0.0, "mid": 1.0}
        _, mu = raw_precision_center(d, z, unit="C", repr_m2_by_model=repr_by)
        assert min(z.values()) <= mu <= max(z.values())

    def test_equal_effective_denominators_no_move(self):
        """If repr is identical across members, weights are unchanged ⇒ μ unmoved."""
        d = {"a": (1.0, 40), "b": (1.0, 40)}
        z = {"a": 28.0, "b": 31.0}
        repr_by = {"a": 2.0, "b": 2.0}  # same repr both
        _, mu_base = raw_precision_center(d, z, unit="C")
        _, mu_repr = raw_precision_center(d, z, unit="C", repr_m2_by_model=repr_by)
        assert abs(mu_repr - mu_base) < 1e-12


# ============================================================================
# Cold-start: positive repr breaks equal weights when no raw history exists.
# ============================================================================
class TestColdStart:
    def test_no_history_positive_repr_breaks_equal_weights(self):
        """No raw m2 for any member, but positive repr_far ⇒ weights NOT equal."""
        d = {"far": (None, 0), "near": (None, 0)}
        z = {"far": 28.0, "near": 31.0}
        repr_by = {"far": 4.0, "near": 0.0}
        w = raw_second_moment_weights(d, unit="C", repr_m2_by_model=repr_by)
        assert abs(w["near"] - 0.5) > 1e-6, "repr must break the equal-weight tie"
        assert w["near"] > w["far"], "the near (no-repr) member must dominate"
        _, mu = raw_precision_center(d, z, unit="C", repr_m2_by_model=repr_by)
        # equal would be 29.5; warming must push it above 29.5 toward near=31
        assert mu > 29.5

    def test_no_history_no_repr_exact_equal(self):
        """No raw m2 AND no positive repr ⇒ exact equal 1/n (the absent-signal posture)."""
        d = {"a": (None, 0), "b": (None, 0), "c": (None, 0)}
        w = raw_second_moment_weights(d, unit="C", repr_m2_by_model={"a": 0.0})
        for v in w.values():
            assert abs(v - 1.0 / 3) < 1e-12

    def test_no_history_no_repr_dict_exact_equal(self):
        d = {"a": (None, 0), "b": (None, 0)}
        w = raw_second_moment_weights(d, unit="C", repr_m2_by_model=None)
        for v in w.values():
            assert abs(v - 0.5) < 1e-12


# ============================================================================
# Low-n: repr added AFTER the EB shrink (floor-residual-first), not swallowed.
# ============================================================================
class TestLowN:
    def test_repr_added_after_eb_shrink(self):
        """A thin-history member's repr must still bite (Form A: floor first, then +repr)."""
        n_thin = max(1, MIN_SETTLED_N - 1)
        d = {"thin_far": (0.2, n_thin), "deep_near": (0.2, 40)}
        z = {"thin_far": 28.0, "deep_near": 31.0}
        repr_by = {"thin_far": 5.0, "deep_near": 0.0}
        ref = _ref_weights(d, repr_by, "C")
        got = raw_second_moment_weights(d, unit="C", repr_m2_by_model=repr_by)
        for m in ref:
            assert abs(got[m] - ref[m]) < 1e-12
        # The thin far member, penalized by repr, must not dominate the deep near one.
        assert got["deep_near"] > got["thin_far"]

    def test_subfloor_residual_repr_not_swallowed(self):
        """Form A vs Form B: a sub-floor residual member's repr still lowers precision.

        With raw_m2 (0.1) below floor_m2 (0.64), Form B (max(m2+repr, floor)) would let
        a SMALL repr be swallowed by the floor; Form A (max(m2, floor)+repr) never does.
        Assert against the Form-A reference so reverting to Form B turns this RED.
        """
        d = {"a": (0.1, 40), "b": (0.1, 40)}  # both sub-floor residual
        z = {"a": 28.0, "b": 31.0}
        small_repr = {"a": 0.3, "b": 0.0}  # smaller than floor headroom
        ref = _ref_weights(d, small_repr, "C")
        got = raw_second_moment_weights(d, unit="C", repr_m2_by_model=small_repr)
        for m in ref:
            assert abs(got[m] - ref[m]) < 1e-12
        # The repr on "a" MUST move its weight below "b" (Form B would leave them equal).
        assert got["b"] > got["a"], "small repr on a sub-floor member must still bite"


# ============================================================================
# C/F invariance: same physical scenario in C and F ⇒ same relative weights.
# ============================================================================
class TestCelsiusFahrenheitInvariance:
    def test_celsius_fahrenheit_weight_invariance(self):
        """Same physical scenario in C and F ⇒ same relative weights.

        The CALLER supplies raw_m2 AND repr in the same unit² basis (the EXIT seam:
        degC² + unit-F floor; the ENTRY seam: native²·(9/5)² + native² repr). The
        physically-invariant case is: convert BOTH raw_m2 and repr to native² for F,
        and use the F floor. Then relative weights must match the C case (where both
        are degC² and the C floor applies) — because every term scales by the SAME u.
        """
        u = (9.0 / 5.0) ** 2
        d_c = {"far": (0.5, 40), "near": (1.0, 40)}
        repr_c = {"far": 4.0, "near": 0.0}
        # F caller pre-scales raw_m2 AND repr to native² (degC² · u), serves unit="F".
        d_f = {"far": (0.5 * u, 40), "near": (1.0 * u, 40)}
        repr_f = {"far": 4.0 * u, "near": 0.0}

        w_c = raw_second_moment_weights(d_c, unit="C", repr_m2_by_model=repr_c)
        w_f = raw_second_moment_weights(d_f, unit="F", repr_m2_by_model=repr_f)
        for m in w_c:
            assert abs(w_c[m] - w_f[m]) < 1e-12, (
                f"C/F weight not invariant for {m}: C={w_c[m]} F={w_f[m]}"
            )

    def test_repr_changes_f_weights(self):
        """Guard against a no-op: repr (in the F native² basis) must change F weights."""
        u = (9.0 / 5.0) ** 2
        d_f = {"far": (0.5 * u, 40), "near": (1.0 * u, 40)}
        base = raw_second_moment_weights(d_f, unit="F")
        with_repr = raw_second_moment_weights(
            d_f, unit="F", repr_m2_by_model={"far": 4.0 * u}
        )
        assert with_repr["far"] < base["far"], "repr must change F weights too"


# ============================================================================
# raw_precision_center returns (weights, mu) consistently.
# ============================================================================
class TestRawPrecisionCenterContract:
    def test_returns_weights_and_mu(self):
        d = {"a": (0.5, 40), "b": (2.0, 40)}
        z = {"a": 28.0, "b": 31.0}
        w, mu = raw_precision_center(d, z, unit="C")
        assert isinstance(w, dict)
        assert isinstance(mu, float)
        assert abs(mu - sum(w[m] * z[m] for m in w)) < 1e-12

    def test_weights_match_raw_second_moment_weights(self):
        """raw_precision_center weights == raw_second_moment_weights (single source)."""
        d = {"a": (0.5, 40), "b": (2.0, 40), "c": (1.0, 30)}
        z = {"a": 28.0, "b": 31.0, "c": 29.0}
        repr_by = {"a": 2.0, "b": 0.0, "c": 1.0}
        w_direct = raw_second_moment_weights(d, unit="C", repr_m2_by_model=repr_by)
        w_center, _ = raw_precision_center(d, z, unit="C", repr_m2_by_model=repr_by)
        for m in w_direct:
            assert abs(w_direct[m] - w_center[m]) < 1e-12

    def test_empty_inputs(self):
        w, mu = raw_precision_center({}, {}, unit="C")
        assert w == {}
        assert mu != mu or isinstance(mu, float)  # nan or float, no crash
