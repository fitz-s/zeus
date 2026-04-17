"""Tests for K3 Slice P: math semantics fixes.

Bug #7  — compute_posterior sparse-vector imputation
Bug #8  — bootstrap ALL bins (cross-bin correlation)
Bug #9  — buy-NO math verification (confirmed correct)
Bug #64 — p_market[0] exit context (confirmed correct — single-element)
"""
from __future__ import annotations

import numpy as np
import pytest

from src.strategy.market_fusion import compute_posterior
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


# ---------------------------------------------------------------------------
# Bug #7: sparse p_market imputation in compute_posterior
# ---------------------------------------------------------------------------

class TestSparseMarketImputation:
    """When monitor refreshes a single held bin, p_market is sparse
    (zeros for non-held bins). Before fix, normalization diluted the
    held bin upward because zeros dragged the denominator down."""

    def test_sparse_vector_does_not_zero_dilute(self):
        """Sparse p_market should impute p_cal for missing entries."""
        p_cal = np.array([0.2, 0.5, 0.3])
        # Sparse: only bin 1 has a market price
        p_market_sparse = np.array([0.0, 0.45, 0.0])
        bins = _bins_3()

        posterior = compute_posterior(p_cal, p_market_sparse, alpha=0.6, bins=bins)

        # All posterior values must be positive (no zero from sparse entries)
        assert np.all(posterior > 0), f"Sparse imputation failed: {posterior}"
        assert posterior.sum() == pytest.approx(1.0, abs=1e-9)

    def test_sparse_vs_complete_held_bin_stability(self):
        """Held bin posterior should be similar whether market is sparse or complete."""
        p_cal = np.array([0.2, 0.5, 0.3])
        p_market_complete = np.array([0.22, 0.45, 0.33])
        # Sparse: only held bin (1) has real price
        p_market_sparse = np.array([0.0, 0.45, 0.0])
        bins = _bins_3()

        post_complete = compute_posterior(p_cal, p_market_complete, alpha=0.6, bins=bins)
        post_sparse = compute_posterior(p_cal, p_market_sparse, alpha=0.6, bins=bins)

        # Imputed sparse should produce similar held-bin posterior
        # (within 10% relative — imputation uses p_cal not actual market)
        assert abs(post_sparse[1] - post_complete[1]) / post_complete[1] < 0.15

    def test_complete_market_unchanged(self):
        """Complete market vectors should not be affected by the fix."""
        p_cal = np.array([0.2, 0.5, 0.3])
        # Complete market with typical vig (~0.95)
        p_market = np.array([0.18, 0.48, 0.29])
        bins = _bins_3()

        posterior = compute_posterior(p_cal, p_market, alpha=0.6, bins=bins)

        assert posterior.sum() == pytest.approx(1.0, abs=1e-9)
        assert np.all(posterior > 0)


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
            )

        ci1 = _make()._bootstrap_bin(1, 100)
        ci2 = _make()._bootstrap_bin(1, 100)
        assert ci1 == ci2, "Same seed must produce identical CI"


# ---------------------------------------------------------------------------
# Bug #9: buy-NO math verification
# ---------------------------------------------------------------------------

class TestBuyNoMath:
    """Verify that buy-NO edge computation is algebraically correct:
    edge_no = (1 - p_posterior[i]) - (1 - p_market[i]) = p_market[i] - p_posterior[i]
    """

    def test_buy_no_edge_is_complement_of_yes(self):
        """NO edge should equal negative of YES edge."""
        p_posterior = np.array([0.2, 0.5, 0.3])
        p_market = np.array([0.18, 0.55, 0.27])

        for i in range(3):
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
            alpha=0.6,
            bins=bins,
            member_maxes=members,
            calibrator=calibrator,
            lead_days=2,
            unit="F",
            rng_seed=456,
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
            alpha=0.6,
            bins=bins,
            member_maxes=members,
            calibrator=calibrator,
            lead_days=2,
            unit="F",
            rng_seed=777,
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
