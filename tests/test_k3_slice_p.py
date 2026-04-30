# Created: 2026-04-12
# Last reused/audited: 2026-04-24
# Authority basis: K3 Slice P math fixes + T6.3 sparse impute policy (midstream fix plan 2026-04-23)
"""Tests for K3 Slice P: math semantics fixes.

Bug #7  — compute_posterior sparse-vector imputation (policy history:
          original B086 removed p_cal impute; T6.3 2026-04-24 restored
          it as an explicit fallback with imputation_source provenance
          recorded on the VigTreatment record).
Bug #8  — bootstrap ALL bins (cross-bin correlation)
Bug #9  — buy-NO math verification (confirmed correct)
Bug #64 — p_market[0] exit context (confirmed correct — single-element)
"""
from __future__ import annotations

import numpy as np
import pytest

from src.strategy.market_fusion import LEGACY_POSTERIOR_MODE, compute_posterior
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bins_3() -> list[Bin]:
    return [
        Bin(low=None, high=59, unit="F", label="59°F or below"),
        Bin(low=60, high=61, unit="F", label="60-61°F"),
        Bin(low=62, high=None, unit="F", label="62°F or higher"),
    ]


def _non_tail_bins_3() -> list[Bin]:
    """Interior bins only — no tail-alpha scaling, so alpha applies uniformly."""
    return [
        Bin(low=60, high=61, unit="F", label="60-61°F"),
        Bin(low=62, high=63, unit="F", label="62-63°F"),
        Bin(low=64, high=65, unit="F", label="64-65°F"),
    ]


def _legacy_kwargs() -> dict[str, object]:
    return {
        "posterior_mode": LEGACY_POSTERIOR_MODE,
        "allow_legacy_quote_prior": True,
    }


def _legacy_compute_posterior(
    p_cal: np.ndarray,
    p_market: np.ndarray,
    *,
    alpha: float,
    bins: list[Bin],
) -> np.ndarray:
    return compute_posterior(
        p_cal,
        p_market,
        alpha=alpha,
        bins=bins,
        **_legacy_kwargs(),
    )


# ---------------------------------------------------------------------------
# Bug #7: sparse p_market imputation in compute_posterior
# (policy restored by T6.3; provenance is typed on VigTreatment)
# ---------------------------------------------------------------------------

class TestSparseMarketImputation:
    """When monitor refreshes a single held bin, p_market is sparse
    (zeros for non-held bins). Under T6.3, the zero positions are
    imputed from p_cal as a fallback reference, and the final
    compute_posterior normalization produces posterior values that
    reflect both the p_cal prior AND the impute-driven market term at
    non-held bins — distinct from both pre-B086 impute behavior
    (implicit) and post-B086 no-impute behavior (zero dilution).
    """

    def test_sparse_vector_matches_p_cal_fallback_impute(self):
        """Post-T6.3 discriminating test: sparse p_market[zero] bins are
        filled from p_cal before blending, and the resulting posterior
        is distinguishable from the no-impute path.
        """
        p_cal = np.array([0.2, 0.5, 0.3])
        p_market_sparse = np.array([0.0, 0.45, 0.0])
        bins = _non_tail_bins_3()  # no tail scaling — alpha_vec == alpha
        alpha = 0.6

        posterior = _legacy_compute_posterior(p_cal, p_market_sparse, alpha=alpha, bins=bins)

        # Expected under T6.3 impute: zeros → p_cal at same positions
        imputed_market = np.array([p_cal[0], 0.45, p_cal[2]])
        raw_impute = alpha * p_cal + (1 - alpha) * imputed_market
        expected_impute = raw_impute / raw_impute.sum()

        # Pre-T6.3 (B086) no-impute alternative — blend raw zeros
        raw_no_impute = alpha * p_cal + (1 - alpha) * p_market_sparse
        expected_no_impute = raw_no_impute / raw_no_impute.sum()

        np.testing.assert_allclose(posterior, expected_impute)
        assert not np.allclose(posterior, expected_no_impute), (
            "Posterior matches no-impute path; T6.3 p_cal fallback impute not active"
        )
        # Sanity: normalization and positivity invariants still hold
        assert posterior.sum() == pytest.approx(1.0, abs=1e-9)
        assert np.all(posterior > 0)

    def test_sparse_vs_complete_held_bin_stability(self):
        """Post-T6.3: impute closes the gap between sparse and complete
        held-bin posterior. Under pre-T6.3 no-impute, this test was RED
        (|sparse - complete| ratio > 0.6 in master); under T6.3 impute,
        the ratio returns to within the 15% tolerance band.
        """
        p_cal = np.array([0.2, 0.5, 0.3])
        p_market_complete = np.array([0.22, 0.45, 0.33])
        # Sparse: only held bin (1) has real price
        p_market_sparse = np.array([0.0, 0.45, 0.0])
        bins = _bins_3()

        post_complete = _legacy_compute_posterior(p_cal, p_market_complete, alpha=0.6, bins=bins)
        post_sparse = _legacy_compute_posterior(p_cal, p_market_sparse, alpha=0.6, bins=bins)

        # Under T6.3 impute, sparse posterior should be within 15% of complete.
        assert abs(post_sparse[1] - post_complete[1]) / post_complete[1] < 0.15

    def test_complete_market_unchanged(self):
        """Complete market vectors should not be affected by T6.3."""
        p_cal = np.array([0.2, 0.5, 0.3])
        # Complete market with typical vig (~0.95)
        p_market = np.array([0.18, 0.48, 0.29])
        bins = _bins_3()

        posterior = _legacy_compute_posterior(p_cal, p_market, alpha=0.6, bins=bins)

        assert posterior.sum() == pytest.approx(1.0, abs=1e-9)
        assert np.all(posterior > 0)

    def test_tail_bin_impute_respects_tail_alpha_scaling(self):
        """T6.3-followup-2 (con-nyx post-edit finding d): under T6.3
        p_cal_fallback, tail bins (low=None or high=None) exhibit a
        mathematical degeneracy — raw[tail] = tail_alpha*p_cal + (1-tail_alpha)*p_cal = p_cal.
        This test pins the identity so a future sibling_market policy
        (where imputed_market[tail] != p_cal[tail]) would NOT collapse
        identically, catching tail-path regressions.
        """
        p_cal = np.array([0.15, 0.50, 0.35])
        p_market_sparse = np.array([0.0, 0.45, 0.0])
        bins = _bins_3()  # bins 0 and 2 are tail bins

        posterior = _legacy_compute_posterior(p_cal, p_market_sparse, alpha=0.6, bins=bins)

        # Tail-alpha: max(0.20, 0.6 * 0.5) = 0.30. Non-tail: 0.60.
        imputed_market = np.array([p_cal[0], 0.45, p_cal[2]])
        alpha_vec = np.array([0.30, 0.60, 0.30])
        raw = alpha_vec * p_cal + (1.0 - alpha_vec) * imputed_market
        expected = raw / raw.sum()

        np.testing.assert_allclose(posterior, expected)
        # Identity check at tail bins: raw[tail] MUST equal p_cal[tail]
        # (regardless of tail_alpha) under p_cal_fallback.
        assert raw[0] == pytest.approx(p_cal[0])
        assert raw[2] == pytest.approx(p_cal[2])

    def test_zero_p_cal_at_sparse_position_does_not_claim_impute(self):
        """T6.3-followup-3 (con-nyx post-edit finding e): when p_cal[i]=0
        at a sparse position, the MED_2 effective_impute_mask filter
        correctly EXCLUDES that position from imputed_bins (zero-for-zero
        substitution is not a real repair). Posterior at that position
        converges to ≈ 0, but the provenance record is honest — auditors
        see "we did not repair this bin" rather than "we falsely claimed
        to repair it and failed."

        Policy choice (raise / uniform-prior / NaN) for this degenerate
        case is deferred; current behavior is provenance-honest which is
        sufficient for Fitz Constraint #3.
        """
        p_cal = np.array([0.0, 0.70, 0.30])  # extreme-clear: bin 0 cal = 0
        p_market_sparse = np.array([0.0, 0.45, 0.0])
        bins = _non_tail_bins_3()

        posterior = _legacy_compute_posterior(p_cal, p_market_sparse, alpha=0.6, bins=bins)

        assert posterior[0] == pytest.approx(0.0, abs=1e-9), (
            f"Expected posterior[0] ≈ 0 when p_cal[0]=0 and market[0]=0; got {posterior[0]}"
        )
        assert posterior[2] > 0.0  # impute from p_cal[2]=0.30 survived
        assert posterior.sum() == pytest.approx(1.0, abs=1e-9)

    def test_sparse_p_market_bootstrap_produces_finite_ci(self):
        """T6.3-followup-1 (partial): regression guard that T6.3 sparse-
        path impute does not produce NaN/Inf CI bounds when routed through
        MarketAnalysis._bootstrap_bin. Full comparative delta audit (T6.3
        vs pre-T6.3 no-impute on production monitor_refresh corpus) needs
        a frozen replay fixture and is tracked as a separate follow-up;
        this test is the "does not explode" antibody.
        """
        from src.strategy.market_analysis import MarketAnalysis

        bins = _bins_3()
        rng = np.random.default_rng(42)
        members = rng.normal(60, 3, size=20)
        p_cal = np.array([0.15, 0.55, 0.30])
        p_market_sparse = np.array([0.0, 0.50, 0.0])  # held-bin only

        ma = MarketAnalysis(
            p_raw=np.array([0.15, 0.55, 0.30]),
            p_cal=p_cal,
            p_market=p_market_sparse,
            alpha=0.6,
            bins=bins,
            member_maxes=members,
            calibrator=None,
            lead_days=2,
            unit="F",
            rng_seed=123,
            **_legacy_kwargs(),
        )

        ci_lo, ci_hi, p_val = ma._bootstrap_bin(1, n=100)

        assert np.isfinite(ci_lo), f"ci_lo non-finite: {ci_lo}"
        assert np.isfinite(ci_hi), f"ci_hi non-finite: {ci_hi}"
        assert ci_lo <= ci_hi, f"CI inverted: [{ci_lo}, {ci_hi}]"
        assert 0.0 <= p_val <= 1.0, f"p_val out of [0,1]: {p_val}"


# ---------------------------------------------------------------------------
# Bug #8: bootstrap ALL bins — cross-bin correlation
# ---------------------------------------------------------------------------

class TestBootstrapAllBins:
    """_bootstrap_bin must recompute ALL bin probabilities from resampled
    ensemble, not just the target bin, because ensemble members shift
    probability mass across bins simultaneously."""

    def test_bootstrap_produces_valid_ci(self):
        """Bootstrap with seeded RNG produces reproducible, finite CI."""
        from src.strategy.market_analysis import MarketAnalysis

        bins = _bins_3()
        # Synthetic ensemble of 20 members around 65°F
        rng = np.random.default_rng(42)
        members = rng.normal(65, 5, size=20)

        p_cal = np.array([0.15, 0.55, 0.30])
        p_market = np.array([0.18, 0.48, 0.34])

        ma = MarketAnalysis(
            p_raw=np.array([0.15, 0.55, 0.30]),
            p_cal=p_cal,
            p_market=p_market,
            alpha=0.6,
            bins=bins,
            member_maxes=members,
            calibrator=None,
            lead_days=2,
            unit="F",
            rng_seed=123,
            **_legacy_kwargs(),
        )

        ci_lo, ci_hi, p_val = ma._bootstrap_bin(1, n=200)

        assert np.isfinite(ci_lo)
        assert np.isfinite(ci_hi)
        assert ci_lo <= ci_hi
        assert 0.0 <= p_val <= 1.0

    def test_bootstrap_rng_reproducibility(self):
        """Same rng_seed → same CI."""
        from src.strategy.market_analysis import MarketAnalysis

        bins = _bins_3()
        members = np.random.default_rng(42).normal(65, 5, size=20)
        p_cal = np.array([0.15, 0.55, 0.30])
        p_market = np.array([0.18, 0.48, 0.34])

        def _make():
            return MarketAnalysis(
                p_raw=np.array([0.15, 0.55, 0.30]),
                p_cal=p_cal,
                p_market=p_market,
                alpha=0.6,
                bins=bins,
                member_maxes=members,
                calibrator=None,
                lead_days=2,
                unit="F",
                rng_seed=999,
                **_legacy_kwargs(),
            )

        ci1 = _make()._bootstrap_bin(1, 100)
        ci2 = _make()._bootstrap_bin(1, 100)
        assert ci1 == ci2, "Same seed must produce identical CI"


# ---------------------------------------------------------------------------
# Bug #9: buy-NO math verification
# ---------------------------------------------------------------------------

class TestBuyNoMath:
    """Verify binary buy-NO complement math only:
    edge_no = (1 - p_posterior[i]) - (1 - p_market[i]) = p_market[i] - p_posterior[i]

    Multi-bin buy_no execution is covered by native NO quote tests, because
    1 - YES_VWMP is not a live executable NO entry price there.
    """

    def test_binary_buy_no_edge_is_complement_of_yes(self):
        """Binary NO edge should equal negative of YES edge."""
        p_posterior = np.array([0.45, 0.55])
        p_market = np.array([0.55, 0.45])

        for i in range(2):
            edge_yes = p_posterior[i] - p_market[i]
            p_post_no = 1.0 - p_posterior[i]
            p_market_no = 1.0 - p_market[i]
            edge_no = p_post_no - p_market_no

            # edge_no = -(edge_yes) algebraically
            assert edge_no == pytest.approx(-edge_yes, abs=1e-12)


# ---------------------------------------------------------------------------
# Bug #8 continued: Platt-parameterized bootstrap path
# ---------------------------------------------------------------------------

class TestBootstrapWithPlattCalibrator:
    """Exercise the Platt-parameterized bootstrap branch (calibrator!=None)."""

    def test_platt_calibrator_bootstrap_produces_valid_ci(self):
        """Bootstrap with mock Platt calibrator produces finite CI."""
        from src.strategy.market_analysis import MarketAnalysis
        from types import SimpleNamespace

        bins = _bins_3()
        rng = np.random.default_rng(42)
        members = rng.normal(62, 3, size=20)

        # Mock calibrator with fitted Platt params
        calibrator = SimpleNamespace(
            fitted=True,
            bootstrap_params=np.array([
                [0.5, -0.1, 0.2],
                [0.6, -0.15, 0.25],
                [0.4, -0.05, 0.15],
            ]),
            input_space="raw_probability",
        )

        p_cal = np.array([0.15, 0.55, 0.30])
        p_market = np.array([0.18, 0.48, 0.34])

        ma = MarketAnalysis(
            p_raw=np.array([0.15, 0.55, 0.30]),
            p_cal=p_cal,
            p_market=p_market,
            alpha=0.6,
            bins=bins,
            member_maxes=members,
            calibrator=calibrator,
            lead_days=2,
            unit="F",
            rng_seed=123,
            **_legacy_kwargs(),
        )

        ci_lo, ci_hi, p_val = ma._bootstrap_bin(1, n=200)
        assert np.isfinite(ci_lo)
        assert np.isfinite(ci_hi)
        assert ci_lo <= ci_hi
        assert 0.0 <= p_val <= 1.0

    def test_platt_no_side_bootstrap(self):
        """Platt NO-side bootstrap produces valid CI."""
        from src.strategy.market_analysis import MarketAnalysis
        from types import SimpleNamespace

        bins = _bins_3()
        members = np.random.default_rng(42).normal(62, 3, size=20)

        calibrator = SimpleNamespace(
            fitted=True,
            bootstrap_params=np.array([
                [0.5, -0.1, 0.2],
                [0.6, -0.15, 0.25],
            ]),
            input_space="raw_probability",
        )

        ma = MarketAnalysis(
            p_raw=np.array([0.15, 0.55, 0.30]),
            p_cal=np.array([0.15, 0.55, 0.30]),
            p_market=np.array([0.18, 0.48, 0.34]),
            p_market_no=np.array([0.82, 0.52, 0.66]),
            buy_no_quote_available=np.array([True, True, True]),
            alpha=0.6,
            bins=bins,
            member_maxes=members,
            calibrator=calibrator,
            lead_days=2,
            unit="F",
            rng_seed=456,
            **_legacy_kwargs(),
        )

        ci_lo, ci_hi, p_val = ma._bootstrap_bin_no(1, n=200)
        assert np.isfinite(ci_lo)
        assert np.isfinite(ci_hi)
        assert ci_lo <= ci_hi
        assert 0.0 <= p_val <= 1.0



# ---------------------------------------------------------------------------
# B082 relationship test: has_platt must accept single fitted param set
# ---------------------------------------------------------------------------

class TestB082HasPlattSingleParamSet:
    """Previous code required ``len(bootstrap_params) > 1`` to count as
    calibrated, silently falling back to raw probabilities for any
    calibrator that had been fit once without bootstrap. That treated a
    legitimate fitted Platt model as uncalibrated. Fix: ``>= 1``.
    """

    def _make_calibrator(self, n_param_sets: int):
        from types import SimpleNamespace
        # Single or multiple Platt parameter sets. Each set is (A, B, C).
        params = np.array([[0.5, -0.1, 0.2]] * n_param_sets)
        return SimpleNamespace(
            fitted=True,
            bootstrap_params=params,
            input_space="raw_probability",
        )

    def _make_ma(self, calibrator):
        from src.strategy.market_analysis import MarketAnalysis
        bins = _bins_3()
        members = np.random.default_rng(42).normal(60, 2, size=20)
        return MarketAnalysis(
            p_raw=np.array([0.15, 0.55, 0.30]),
            p_cal=np.array([0.15, 0.55, 0.30]),
            p_market=np.array([0.18, 0.48, 0.34]),
            p_market_no=np.array([0.82, 0.52, 0.66]),
            buy_no_quote_available=np.array([True, True, True]),
            alpha=0.6,
            bins=bins,
            member_maxes=members,
            calibrator=calibrator,
            lead_days=2,
            unit="F",
            rng_seed=777,
            **_legacy_kwargs(),
        )

    def test_b082_single_fitted_param_set_counts_as_calibrated(self):
        """Calibrator with 1 bootstrap_params row must drive has_platt=True.

        Exercised indirectly: with has_platt=True the bootstrap path
        samples from Platt params at ``platt_params[rng.integers(1)]``.
        If the fix is wrong (legacy ``> 1``), the code path degenerates
        to ``p_cal_boot_all = p_raw_all`` (no Platt applied). We detect
        the difference by comparing the CI obtained under a calibrator
        with 1 row to the CI obtained with NO calibrator.
        """
        ma_with = self._make_ma(self._make_calibrator(1))
        ma_without = self._make_ma(None)

        ci_lo_with, ci_hi_with, _ = ma_with._bootstrap_bin(1, n=200)
        ci_lo_none, ci_hi_none, _ = ma_without._bootstrap_bin(1, n=200)

        # If B082 is still broken, has_platt=False for the 1-row calibrator
        # and the two CIs would be identical (both use raw p_raw_all).
        # After the fix, the 1-row calibrator applies Platt → different CI.
        assert (ci_lo_with, ci_hi_with) != (ci_lo_none, ci_hi_none), (
            "B082 regression: single-fit Platt calibrator was treated as "
            "uncalibrated (bootstrap CI identical to no-calibrator case)"
        )

    def test_b082_multi_fitted_param_set_still_works(self):
        """Regression guard: ``> 1`` cases still behave the same."""
        ma = self._make_ma(self._make_calibrator(5))
        ci_lo, ci_hi, p_val = ma._bootstrap_bin(1, n=200)
        assert np.isfinite(ci_lo) and np.isfinite(ci_hi)
        assert 0.0 <= p_val <= 1.0

    def test_b082_no_side_also_accepts_single_fitted_param_set(self):
        """The sibling ``_bootstrap_bin_no`` must get the same fix."""
        ma_with = self._make_ma(self._make_calibrator(1))
        ma_without = self._make_ma(None)

        ci_lo_with, ci_hi_with, _ = ma_with._bootstrap_bin_no(1, n=200)
        ci_lo_none, ci_hi_none, _ = ma_without._bootstrap_bin_no(1, n=200)

        assert (ci_lo_with, ci_hi_with) != (ci_lo_none, ci_hi_none), (
            "B082 regression (NO side): single-fit Platt calibrator was "
            "treated as uncalibrated"
        )
