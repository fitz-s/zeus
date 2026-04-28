"""Tests for MarketAnalysis and market fusion.

Covers:
1. VWMP calculation and edge case (total_size=0)
2. compute_alpha with various maturity levels and adjustments
3. MarketAnalysis.find_edges with known mispricing → edge found
4. Fair-priced market → no edges (CI crosses zero)
"""

import numpy as np
import pytest

from src.strategy.market_fusion import vwmp, compute_alpha, compute_posterior
from src.strategy.market_analysis import MarketAnalysis
from src.calibration.platt import ExtendedPlattCalibrator
from src.types import Bin, BinEdge
from src.types.temperature import TemperatureDelta


class TestVWMP:
    def test_equal_sizes(self):
        """Equal bid/ask sizes → VWMP = mid-price."""
        result = vwmp(0.45, 0.55, 100.0, 100.0)
        assert result == pytest.approx(0.50, abs=0.001)

    def test_bid_heavy(self):
        """Large bid → VWMP closer to ask."""
        result = vwmp(0.45, 0.55, 1000.0, 100.0)
        assert result > 0.50  # Closer to ask

    def test_ask_heavy(self):
        """Large ask → VWMP closer to bid."""
        result = vwmp(0.45, 0.55, 100.0, 1000.0)
        assert result < 0.50  # Closer to bid

    def test_zero_size_fallback(self):
        """VWMP with total size = 0 must fail closed, not fabricate mid-price."""
        with pytest.raises(ValueError, match="Illiquid market"):
            vwmp(0.45, 0.55, 0.0, 0.0)


class TestComputeAlpha:
    def test_level_1_base(self):
        a = compute_alpha(1, TemperatureDelta(3.0, "F"), "AGREE", 3, 24.0, authority_verified=True).value
        assert a == pytest.approx(0.65, abs=0.01)

    def test_level_4_base(self):
        a = compute_alpha(4, TemperatureDelta(3.0, "F"), "AGREE", 3, 24.0, authority_verified=True).value
        assert a == pytest.approx(0.25, abs=0.01)

    def test_conflict_reduces_alpha(self):
        spread = TemperatureDelta(3.0, "F")
        a_agree = compute_alpha(1, spread, "AGREE", 3, 24.0, authority_verified=True).value
        a_conflict = compute_alpha(1, spread, "CONFLICT", 3, 24.0, authority_verified=True).value
        assert a_conflict < a_agree

    def test_fresh_market_increases_alpha(self):
        """hours_since_open < 6 → +0.15 total (0.10 + 0.05)."""
        spread = TemperatureDelta(3.0, "F")
        a_old = compute_alpha(2, spread, "AGREE", 3, 48.0, authority_verified=True).value
        a_fresh = compute_alpha(2, spread, "AGREE", 3, 4.0, authority_verified=True).value
        assert a_fresh > a_old

    def test_clamped_floor(self):
        """Alpha should never go below 0.20."""
        a = compute_alpha(4, TemperatureDelta(8.0, "F"), "CONFLICT", 7, 48.0, authority_verified=True).value
        assert a >= 0.20

    def test_clamped_ceiling(self):
        """Alpha should never exceed 0.85."""
        a = compute_alpha(1, TemperatureDelta(1.0, "F"), "AGREE", 1, 2.0, authority_verified=True).value
        assert a <= 0.85

    def test_rejects_float_spread(self):
        with pytest.raises(TypeError):
            compute_alpha(1, 3.0, "AGREE", 3, 24.0, authority_verified=True)

    @pytest.mark.parametrize(
        "agreement, expected_alpha",
        [
            ("NOT_CHECKED", 0.65),    # no penalty — treated same as AGREE
            ("AGREE", 0.65),          # no penalty
            ("SOFT_DISAGREE", 0.55),  # -0.10 penalty
        ],
        ids=["not_checked", "agree", "soft_disagree"],
    )
    def test_p9_model_agreement_alpha_adjustment(self, agreement, expected_alpha):
        """P9: model_agreement field drives alpha penalty correctly."""
        a = compute_alpha(
            1, TemperatureDelta(3.0, "F"), agreement, 3, 24.0, authority_verified=True
        ).value
        assert a == pytest.approx(expected_alpha, abs=0.01)


class TestComputePosterior:
    def test_alpha_half(self):
        """α=0.5 → posterior = average of model and market."""
        p_cal = np.array([0.6, 0.4])
        p_market = np.array([0.4, 0.6])
        result = compute_posterior(p_cal, p_market, 0.5)
        np.testing.assert_array_almost_equal(result, [0.5, 0.5])

    def test_alpha_one(self):
        """α=1.0 → posterior = model."""
        p_cal = np.array([0.8, 0.2])
        p_market = np.array([0.3, 0.7])
        result = compute_posterior(p_cal, p_market, 1.0)
        np.testing.assert_array_almost_equal(result, p_cal)

    def test_vig_removed_before_blend(self):
        p_cal = np.array([0.60, 0.30, 0.10])
        p_market = np.array([0.54, 0.36, 0.18])

        result = compute_posterior(p_cal, p_market, 0.4)
        expected = 0.4 * p_cal + 0.6 * (p_market / p_market.sum())
        legacy_post_blend = (0.4 * p_cal + 0.6 * p_market)
        legacy_post_blend = legacy_post_blend / legacy_post_blend.sum()

        np.testing.assert_allclose(result, expected)
        assert not np.allclose(result, legacy_post_blend)

    def test_sparse_monitor_market_vector_imputes_missing_sibling_prices(self):
        """T2.c (closed by T6.3, 2026-04-24): when p_market has zero entries
        (missing sibling prices from sparse monitor snapshot), compute_posterior
        imputes those zeros from p_cal as a fallback reference before the
        alpha blend. The imputation source is recorded on the VigTreatment
        record (imputation_source='p_cal_fallback').

        The p_cal fixture is intentionally asymmetric (0.20 vs 0.30 at the
        zero-filled positions) so the test discriminates between genuine
        p_cal impute and any symmetric sibling-snapshot behavior that would
        coincidentally produce equal values at positions 0 and 2.
        """
        p_cal = np.array([0.20, 0.50, 0.30])  # asymmetric — kills silent-sibling-equivalence ambiguity
        p_market = np.array([0.00, 0.95, 0.00])

        result = compute_posterior(p_cal, p_market, 0.5)
        imputed_market = np.array([0.20, 0.95, 0.30])  # zeros replaced by p_cal at same positions
        raw = 0.5 * p_cal + 0.5 * imputed_market
        expected = raw / raw.sum()
        # Pre-T6.3 no-impute path: blend raw sparse [0,0.95,0] directly
        incorrectly_zero_filled = 0.5 * p_cal + 0.5 * np.array([0.0, 0.95, 0.0])
        incorrectly_zero_filled = incorrectly_zero_filled / incorrectly_zero_filled.sum()

        np.testing.assert_allclose(result, expected)
        # Discriminate from pre-T6.3 no-impute behavior
        assert not np.allclose(result, incorrectly_zero_filled)
        # Assert asymmetry survives the blend — proves p_cal was the reference
        # (symmetric sibling_snapshot would have produced result[0] == result[2])
        assert result[0] != pytest.approx(result[2]), (
            f"Expected asymmetric posterior (p_cal was asymmetric); got {result}. "
            "Symmetric result implies impute source was not p_cal."
        )
        # Strong discriminator: under p_cal impute at zero-filled positions,
        # raw[i] = alpha*p_cal[i] + (1-alpha)*p_cal[i] = p_cal[i], so the
        # ratio result[0]/result[2] == p_cal[0]/p_cal[2] survives normalization
        # (alpha-independent). A different impute source (e.g. a real sibling
        # market snapshot) would produce a different ratio even if asymmetric.
        assert result[0] / result[2] == pytest.approx(p_cal[0] / p_cal[2], abs=0.01), (
            f"Expected result[0]/result[2] = p_cal[0]/p_cal[2] = "
            f"{p_cal[0]/p_cal[2]:.4f}; got {result[0]/result[2]:.4f}. "
            "A different sibling source would produce a different ratio."
        )

    def test_tail_alpha_scale_applies_per_bin_and_normalizes(self):
        bins = [
            Bin(low=None, high=32, label="32°F or below", unit="F"),
            Bin(low=33, high=34, label="33-34°F", unit="F"),
        ]
        result = compute_posterior(
            np.array([1.0, 0.0]),
            np.array([0.5, 0.5]),
            0.8,
            bins=bins,
        )

        np.testing.assert_array_almost_equal(result, [0.875, 0.125])
        assert result.sum() == pytest.approx(1.0)

    def test_tail_alpha_uses_de_vigged_market_before_blend(self):
        bins = [
            Bin(low=None, high=32, label="32°F or below", unit="F"),
            Bin(low=33, high=34, label="33-34°F", unit="F"),
        ]
        p_cal = np.array([1.0, 0.0])
        p_market = np.array([0.648, 0.432])
        result = compute_posterior(p_cal, p_market, 0.8, bins=bins)

        alpha_vec = np.array([0.4, 0.8])
        raw = alpha_vec * p_cal + (1.0 - alpha_vec) * (p_market / p_market.sum())
        expected = raw / raw.sum()

        np.testing.assert_allclose(result, expected)

    def test_tail_alpha_scale_applies_to_buy_yes_bootstrap_ci(self):
        bins = [
            Bin(low=None, high=32, label="32°F or below", unit="F"),
            Bin(low=39, high=40, label="39-40°F", unit="F"),
        ]
        ma = MarketAnalysis(
            p_raw=np.array([1.0, 0.0]),
            p_cal=np.array([1.0, 0.0]),
            p_market=np.array([0.5, 0.5]),
            alpha=0.8,
            bins=bins,
            member_maxes=np.array([30.0, 30.0, 30.0]),
            unit="F",
        )
        ma._sigma = 0.0

        ci_lo, ci_hi, p_value = ma._bootstrap_bin(0, 5)

        # Verify posterior with p_cal[0]=1.0 yields 0.875 (was _posterior_with_bootstrapped_bin)
        assert compute_posterior(np.array([1.0, 0.0]), np.array([0.5, 0.5]), 0.8, bins=bins)[0] == pytest.approx(0.875)
        assert ci_lo == pytest.approx(0.375)
        assert ci_hi == pytest.approx(0.375)
        assert p_value == 0.0

    def test_tail_alpha_scale_applies_to_buy_no_bootstrap_ci(self):
        bins = [
            Bin(low=None, high=32, label="32°F or below", unit="F"),
            Bin(low=39, high=40, label="39-40°F", unit="F"),
        ]
        ma = MarketAnalysis(
            p_raw=np.array([0.0, 1.0]),
            p_cal=np.array([0.0, 1.0]),
            p_market=np.array([0.5, 0.5]),
            alpha=0.8,
            bins=bins,
            member_maxes=np.array([40.0, 40.0, 40.0]),
            unit="F",
        )
        ma._sigma = 0.0

        ci_lo, ci_hi, p_value = ma._bootstrap_bin_no(0, 5)

        # Verify posterior with p_cal[0]=0.0 yields 0.25 (was _posterior_with_bootstrapped_bin)
        assert compute_posterior(np.array([0.0, 1.0]), np.array([0.5, 0.5]), 0.8, bins=bins)[0] == pytest.approx(0.25)
        assert ci_lo == pytest.approx(0.25)
        assert ci_hi == pytest.approx(0.25)
        assert p_value == 0.0


class TestMarketAnalysis:
    def _make_bins(self) -> list[Bin]:
        return [
            Bin(low=None, high=32, label="32 or below", unit="F"),
            Bin(low=33, high=34, label="33-34", unit="F"),
            Bin(low=35, high=36, label="35-36", unit="F"),
            Bin(low=37, high=38, label="37-38", unit="F"),
            Bin(low=39, high=40, label="39-40", unit="F"),
            Bin(low=41, high=42, label="41-42", unit="F"),
            Bin(low=43, high=44, label="43-44", unit="F"),
            Bin(low=45, high=46, label="45-46", unit="F"),
            Bin(low=47, high=48, label="47-48", unit="F"),
            Bin(low=49, high=50, label="49-50", unit="F"),
            Bin(low=51, high=None, label="51 or higher", unit="F"),
        ]

    def test_mispriced_market_finds_edges(self):
        """Model says center bin is 30% but market prices at 10% → edge exists."""
        np.random.seed(42)
        bins = self._make_bins()

        # Model: strong peak at bin 4 (39-40)
        p_raw = np.array([0.02, 0.05, 0.10, 0.20, 0.30, 0.20, 0.08, 0.03, 0.01, 0.005, 0.005])
        p_cal = p_raw.copy()  # Assume identity calibration
        # Market: underprices bin 4
        p_market = np.array([0.05, 0.08, 0.10, 0.12, 0.10, 0.12, 0.10, 0.08, 0.08, 0.08, 0.09])

        member_maxes = np.random.default_rng(42).normal(40, 2, 51)

        ma = MarketAnalysis(
            p_raw=p_raw, p_cal=p_cal, p_market=p_market,
            alpha=0.65, bins=bins, member_maxes=member_maxes, lead_days=3.0,
        )

        edges = ma.find_edges(n_bootstrap=100)
        # Should find at least one edge (bin 4 is underpriced by market)
        assert len(edges) > 0
        # At least one edge should be buy_yes on a center bin
        yes_edges = [e for e in edges if e.direction == "buy_yes"]
        assert len(yes_edges) > 0

    def test_fair_priced_no_edges(self):
        """If model and market agree, no edges should be found."""
        np.random.seed(42)
        bins = self._make_bins()

        p = np.array([0.05, 0.08, 0.12, 0.18, 0.24, 0.15, 0.08, 0.04, 0.03, 0.02, 0.01])
        member_maxes = np.random.default_rng(42).normal(40, 3, 51)

        ma = MarketAnalysis(
            p_raw=p, p_cal=p.copy(), p_market=p.copy(),
            alpha=0.65, bins=bins, member_maxes=member_maxes, lead_days=3.0,
        )

        edges = ma.find_edges(n_bootstrap=100)
        # Should find zero or very few edges (CI should cross zero)
        assert len(edges) <= 2  # Allow small noise effects

    def test_vig_computed(self):
        bins = self._make_bins()
        p_market = np.array([0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.10])
        member_maxes = np.ones(51) * 40.0

        ma = MarketAnalysis(
            p_raw=p_market, p_cal=p_market, p_market=p_market,
            alpha=0.5, bins=bins, member_maxes=member_maxes,
        )
        assert ma.vig == pytest.approx(1.0, abs=0.01)

    def test_market_analysis_keeps_raw_vig_but_posterior_uses_clean_market(self):
        bins = self._make_bins()[1:4]
        p_cal = np.array([0.60, 0.30, 0.10])
        p_market = np.array([0.54, 0.36, 0.18])
        member_maxes = np.ones(51) * 40.0

        ma = MarketAnalysis(
            p_raw=p_cal,
            p_cal=p_cal,
            p_market=p_market,
            alpha=0.4,
            bins=bins,
            member_maxes=member_maxes,
        )

        assert ma.vig == pytest.approx(1.08)
        np.testing.assert_allclose(
            ma.p_posterior,
            0.4 * p_cal + 0.6 * (p_market / p_market.sum()),
        )
