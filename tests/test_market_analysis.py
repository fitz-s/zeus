# Lifecycle: created=2026-04-30; last_reviewed=2026-05-01; last_reused=2026-05-01
# Purpose: Regression coverage for market-analysis edge math, posterior modes, and executable quote authority.
# Reuse: Run when market fusion, edge scan, bootstrap CI, or buy_no quote authority changes.
# Created: 2026-04-30
# Last reused/audited: 2026-05-01
# Authority basis: first-principles safety implementation 2026-04-30

"""Tests for MarketAnalysis and market fusion.

Covers:
1. VWMP calculation and edge case (total_size=0)
2. compute_alpha with various maturity levels and adjustments
3. MarketAnalysis.find_edges with known mispricing → edge found
4. Fair-priced market → no edges (CI crosses zero)
"""

import numpy as np
import pytest

from src.strategy.market_fusion import (
    LEGACY_POSTERIOR_MODE,
    MODEL_ONLY_POSTERIOR_MODE,
    YES_FAMILY_DEVIG_SHADOW_MODE,
    MarketPriorDistribution,
    compute_alpha,
    compute_posterior,
    vwmp,
)
from src.strategy.market_analysis import MarketAnalysis
from src.calibration.platt import ExtendedPlattCalibrator, WIDTH_NORMALIZED_SPACE
from src.types import Bin, BinEdge
from src.types.temperature import TemperatureDelta


def _legacy_kwargs() -> dict[str, object]:
    return {
        "posterior_mode": LEGACY_POSTERIOR_MODE,
        "allow_legacy_quote_prior": True,
    }


def _legacy_compute_posterior(
    p_cal: np.ndarray,
    p_market: np.ndarray,
    alpha: float,
    *,
    bins: list[Bin] | None = None,
) -> np.ndarray:
    return compute_posterior(
        p_cal,
        p_market,
        alpha,
        bins=bins,
        **_legacy_kwargs(),
    )


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
        result = _legacy_compute_posterior(p_cal, p_market, 0.5)
        np.testing.assert_array_almost_equal(result, [0.5, 0.5])

    def test_alpha_one(self):
        """α=1.0 → posterior = model."""
        p_cal = np.array([0.8, 0.2])
        p_market = np.array([0.3, 0.7])
        result = _legacy_compute_posterior(p_cal, p_market, 1.0)
        np.testing.assert_array_almost_equal(result, p_cal)

    def test_vig_removed_before_blend(self):
        p_cal = np.array([0.60, 0.30, 0.10])
        p_market = np.array([0.54, 0.36, 0.18])

        result = _legacy_compute_posterior(p_cal, p_market, 0.4)
        expected = 0.4 * p_cal + 0.6 * (p_market / p_market.sum())
        legacy_post_blend = (0.4 * p_cal + 0.6 * p_market)
        legacy_post_blend = legacy_post_blend / legacy_post_blend.sum()

        np.testing.assert_allclose(result, expected)
        assert not np.allclose(result, legacy_post_blend)

    def test_model_only_rejects_market_quote_input(self):
        p_cal = np.array([0.65, 0.35])
        raw_quote_vector = np.array([0.40, 0.60])

        with pytest.raises(TypeError, match="model_only_v1"):
            compute_posterior(p_cal, raw_quote_vector, 0.5)

        with pytest.raises(TypeError, match="model_only_v1"):
            compute_posterior(
                p_cal,
                raw_quote_vector,
                0.5,
                posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
            )

    def test_model_only_posterior_is_independent_of_executable_quote_shape(self):
        p_cal = np.array([0.65, 0.35])
        cheap_top_of_book = vwmp(0.10, 0.20, 10.0, 100.0)
        expensive_top_of_book = vwmp(0.70, 0.80, 100.0, 10.0)
        assert cheap_top_of_book != pytest.approx(expensive_top_of_book)

        cheap_case = compute_posterior(
            p_cal,
            None,
            0.25,
            posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
        )
        expensive_case = compute_posterior(
            p_cal,
            None,
            0.25,
            posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
        )

        np.testing.assert_allclose(cheap_case, p_cal)
        np.testing.assert_allclose(expensive_case, p_cal)

    def test_corrected_market_prior_requires_named_distribution(self):
        p_cal = np.array([0.70, 0.30])
        raw_quote_vector = np.array([0.40, 0.60])

        with pytest.raises(TypeError, match="MarketPriorDistribution"):
            compute_posterior(
                p_cal,
                raw_quote_vector,
                0.5,
                posterior_mode=YES_FAMILY_DEVIG_SHADOW_MODE,
            )

        prior = MarketPriorDistribution(
            probabilities=(0.40, 0.60),
            bin_labels=("cold", "warm"),
            prior_id="shadow-devig:test-family",
            estimator_version="yes_family_devig_v1_shadow",
            source_quote_hashes=("quote-hash-a", "quote-hash-b"),
            family_complete=True,
            side_convention="YES_FAMILY",
            vig_treatment="yes_family_devig",
            freshness_status="FRESH",
            liquidity_filter_status="PASS",
            neg_risk_policy="included",
            validated_for_live=False,
        )
        result = compute_posterior(
            p_cal,
            prior,
            0.5,
            posterior_mode=YES_FAMILY_DEVIG_SHADOW_MODE,
        )

        np.testing.assert_allclose(result, [0.55, 0.45])

    def test_market_prior_distribution_requires_quote_lineage_and_complete_family(self):
        kwargs = {
            "probabilities": (0.40, 0.60),
            "bin_labels": ("cold", "warm"),
            "prior_id": "shadow-devig:test-family",
            "estimator_version": YES_FAMILY_DEVIG_SHADOW_MODE,
            "source_quote_hashes": ("quote-hash-a", "quote-hash-b"),
            "family_complete": True,
            "side_convention": "YES_FAMILY",
            "vig_treatment": "yes_family_devig",
            "freshness_status": "FRESH",
            "liquidity_filter_status": "PASS",
            "neg_risk_policy": "included",
            "validated_for_live": False,
        }

        with pytest.raises(ValueError, match="source_quote_hashes"):
            MarketPriorDistribution(**{**kwargs, "source_quote_hashes": ()})
        with pytest.raises(ValueError, match="complete YES-family"):
            MarketPriorDistribution(**{**kwargs, "family_complete": False})

    def test_legacy_quote_prior_can_be_disabled_at_call_boundary(self):
        p_cal = np.array([0.70, 0.30])
        raw_quote_vector = np.array([0.40, 0.60])

        with pytest.raises(ValueError, match="legacy VWMP market prior is disabled"):
            compute_posterior(
                p_cal,
                raw_quote_vector,
                0.5,
                posterior_mode=LEGACY_POSTERIOR_MODE,
                allow_legacy_quote_prior=False,
            )

    def test_sparse_monitor_market_vector_imputes_missing_sibling_prices(self):
        """T2.c (closed by T6.3, 2026-04-24): when p_market has zero entries
        (missing sibling prices from sparse monitor snapshot), compute_posterior
        imputes those zeros from p_cal only in explicit legacy mode. Corrected
        modes reject sparse held-token vectors rather than treating them as a
        full family prior.

        The p_cal fixture is intentionally asymmetric (0.20 vs 0.30 at the
        zero-filled positions) so the test discriminates between genuine
        p_cal impute and any symmetric sibling-snapshot behavior that would
        coincidentally produce equal values at positions 0 and 2.
        """
        p_cal = np.array([0.20, 0.50, 0.30])  # asymmetric — kills silent-sibling-equivalence ambiguity
        p_market = np.array([0.00, 0.95, 0.00])

        result = compute_posterior(
            p_cal,
            p_market,
            0.5,
            **_legacy_kwargs(),
        )
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

    def test_corrected_prior_rejects_sparse_monitor_vector(self):
        p_cal = np.array([0.20, 0.50, 0.30])
        held_token_only_quote = np.array([0.00, 0.95, 0.00])

        with pytest.raises(TypeError, match="raw quote/VWMP vectors are forbidden"):
            compute_posterior(
                p_cal,
                held_token_only_quote,
                0.5,
                posterior_mode=YES_FAMILY_DEVIG_SHADOW_MODE,
            )

    def test_tail_alpha_scale_applies_per_bin_and_normalizes(self):
        bins = [
            Bin(low=None, high=32, label="32°F or below", unit="F"),
            Bin(low=33, high=34, label="33-34°F", unit="F"),
        ]
        result = _legacy_compute_posterior(
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
        result = _legacy_compute_posterior(p_cal, p_market, 0.8, bins=bins)

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
            **_legacy_kwargs(),
        )
        ma._sigma = 0.0

        ci_lo, ci_hi, p_value = ma._bootstrap_bin(0, 5)

        # Verify posterior with p_cal[0]=1.0 yields 0.875 (was _posterior_with_bootstrapped_bin)
        assert _legacy_compute_posterior(
            np.array([1.0, 0.0]),
            np.array([0.5, 0.5]),
            0.8,
            bins=bins,
        )[0] == pytest.approx(0.875)
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
            p_market_no=np.array([0.5, 0.5]),
            buy_no_quote_available=np.array([True, True]),
            alpha=0.8,
            bins=bins,
            member_maxes=np.array([40.0, 40.0, 40.0]),
            unit="F",
            **_legacy_kwargs(),
        )
        ma._sigma = 0.0

        ci_lo, ci_hi, p_value = ma._bootstrap_bin_no(0, 5)

        # Verify posterior with p_cal[0]=0.0 yields 0.25 (was _posterior_with_bootstrapped_bin)
        assert _legacy_compute_posterior(
            np.array([0.0, 1.0]),
            np.array([0.5, 0.5]),
            0.8,
            bins=bins,
        )[0] == pytest.approx(0.25)
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

    @pytest.mark.parametrize(
        "field,bad_values,match",
        [
            ("p_raw", np.array([np.nan, 1.0]), "p_raw must be finite"),
            ("p_cal", np.array([0.5, np.inf]), "p_cal must be finite"),
            ("p_market", np.array([0.5, -0.1]), "p_market must be non-negative"),
            ("p_raw", np.array([1.2, 0.1]), "p_raw components must be <= 1"),
            ("p_cal", np.array([0.2, 0.2]), "p_cal must sum to 1.0"),
            ("p_market", np.array([1.2, 0.2]), "p_market components must be <= 1"),
        ],
    )
    def test_rejects_invalid_probability_vectors(self, field, bad_values, match):
        bins = [
            Bin(low=None, high=32, label="32 or below", unit="F"),
            Bin(low=33, high=None, label="33 or higher", unit="F"),
        ]
        kwargs = {
            "p_raw": np.array([0.5, 0.5]),
            "p_cal": np.array([0.5, 0.5]),
            "p_market": np.array([0.5, 0.5]),
            "alpha": 0.5,
            "bins": bins,
            "member_maxes": np.array([30.0, 31.0, 32.0]),
            "unit": "F",
        }
        kwargs[field] = bad_values

        with pytest.raises(ValueError, match=match):
            MarketAnalysis(**kwargs)

    def test_rejects_nonfinite_member_extrema_before_bootstrap(self):
        bins = [
            Bin(low=None, high=32, label="32 or below", unit="F"),
            Bin(low=33, high=None, label="33 or higher", unit="F"),
        ]

        with pytest.raises(ValueError, match="member_maxes must be finite"):
            MarketAnalysis(
                p_raw=np.array([0.5, 0.5]),
                p_cal=np.array([0.5, 0.5]),
                p_market=np.array([0.5, 0.5]),
                alpha=0.5,
                bins=bins,
                member_maxes=np.array([30.0, np.nan, 32.0]),
                unit="F",
            )

    def test_model_only_market_analysis_keeps_quote_out_of_posterior(self):
        bins = [
            Bin(low=None, high=32, label="32 or below", unit="F"),
            Bin(low=33, high=None, label="33 or higher", unit="F"),
        ]
        p_cal = np.array([0.65, 0.35])
        executable_quotes = np.array([0.10, 0.90])

        ma = MarketAnalysis(
            p_raw=p_cal,
            p_cal=p_cal,
            p_market=executable_quotes,
            alpha=0.0,
            bins=bins,
            member_maxes=np.array([30.0, 31.0, 32.0]),
            unit="F",
        )

        np.testing.assert_allclose(ma.p_posterior, p_cal)
        assert ma.vig == pytest.approx(1.0)

    def test_model_only_without_execution_prices_cannot_scan_edges(self):
        bins = [
            Bin(low=None, high=32, label="32 or below", unit="F"),
            Bin(low=33, high=None, label="33 or higher", unit="F"),
        ]
        p_cal = np.array([0.65, 0.35])

        ma = MarketAnalysis(
            p_raw=p_cal,
            p_cal=p_cal,
            p_market=None,
            alpha=1.0,
            bins=bins,
            member_maxes=np.array([30.0, 31.0, 32.0]),
            unit="F",
        )

        with pytest.raises(ValueError, match="requires executable YES-side market prices"):
            ma.find_edges(n_bootstrap=1)
        with pytest.raises(ValueError, match="buy_yes bootstrap requires executable YES-side"):
            ma._bootstrap_bin(0, 1)

    def test_binary_buy_no_complement_is_diagnostic_not_executable(self):
        bins = [
            Bin(low=None, high=32, label="32 or below", unit="F"),
            Bin(low=33, high=None, label="33 or higher", unit="F"),
        ]
        p_cal = np.array([0.20, 0.80])

        ma = MarketAnalysis(
            p_raw=p_cal,
            p_cal=p_cal,
            p_market=np.array([0.40, 0.60]),
            alpha=1.0,
            bins=bins,
            member_maxes=np.array([30.0, 31.0, 32.0]),
            unit="F",
        )

        assert ma.supports_buy_no_edges(0) is False
        assert ma.buy_no_complement_diagnostic_price(0) == pytest.approx(0.60)
        with pytest.raises(ValueError, match="buy_no is not executable"):
            ma.buy_no_market_price(0)
        with pytest.raises(ValueError, match="buy_no bootstrap requires executable NO-side"):
            ma._bootstrap_bin_no(0, 1)

    def test_buy_no_uses_native_no_quote_when_available(self):
        bins = [
            Bin(low=None, high=32, label="32 or below", unit="F"),
            Bin(low=33, high=None, label="33 or higher", unit="F"),
        ]
        p_cal = np.array([0.20, 0.80])

        ma = MarketAnalysis(
            p_raw=p_cal,
            p_cal=p_cal,
            p_market=np.array([0.40, 0.60]),
            p_market_no=np.array([0.68, 0.32]),
            buy_no_quote_available=np.array([True, False]),
            alpha=1.0,
            bins=bins,
            member_maxes=np.array([30.0, 31.0, 32.0]),
            unit="F",
        )

        assert ma.supports_buy_no_edges(0) is True
        assert ma.buy_no_market_price(0) == pytest.approx(0.68)
        assert ma.supports_buy_no_edges(1) is False

    def test_market_analysis_corrected_prior_uses_named_distribution(self):
        bins = [
            Bin(low=30, high=31, label="30-31", unit="F"),
            Bin(low=32, high=33, label="32-33", unit="F"),
        ]
        p_cal = np.array([0.70, 0.30])
        prior = MarketPriorDistribution(
            probabilities=(0.40, 0.60),
            bin_labels=("30-31", "32-33"),
            prior_id="shadow-devig:test-family",
            estimator_version=YES_FAMILY_DEVIG_SHADOW_MODE,
            source_quote_hashes=("quote-hash-a", "quote-hash-b"),
            family_complete=True,
            side_convention="YES_FAMILY",
            vig_treatment="yes_family_devig",
            freshness_status="FRESH",
            liquidity_filter_status="PASS",
            neg_risk_policy="included",
            validated_for_live=False,
        )

        ma = MarketAnalysis(
            p_raw=p_cal,
            p_cal=p_cal,
            p_market=np.array([0.25, 0.75]),
            alpha=0.5,
            bins=bins,
            member_maxes=np.array([30.0, 31.0, 32.0]),
            unit="F",
            posterior_mode=YES_FAMILY_DEVIG_SHADOW_MODE,
            market_prior=prior,
        )

        np.testing.assert_allclose(ma.p_posterior, [0.55, 0.45])

    def test_width_normalized_bootstrap_keeps_open_shoulders_raw(self):
        bins = [
            Bin(low=None, high=32, label="32 or below", unit="F"),
            Bin(low=33, high=None, label="33 or higher", unit="F"),
        ]
        calibrator = ExtendedPlattCalibrator()
        calibrator.fitted = True
        calibrator.A = 1.0
        calibrator.B = 0.0
        calibrator.C = 0.0
        calibrator.input_space = WIDTH_NORMALIZED_SPACE
        calibrator.bootstrap_params = [(1.0, 0.0, 0.0)]

        ma = MarketAnalysis(
            p_raw=np.array([1.0, 0.0]),
            p_cal=np.array([1.0, 0.0]),
            p_market=np.array([0.5, 0.5]),
            alpha=0.8,
            bins=bins,
            member_maxes=np.array([30.0, 30.0, 30.0]),
            calibrator=calibrator,
            unit="F",
            rng_seed=1,
            **_legacy_kwargs(),
        )
        ma._sigma = 0.0

        ci_lo, ci_hi, p_value = ma._bootstrap_bin(0, 5)

        assert np.isfinite([ci_lo, ci_hi, p_value]).all()

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
            **_legacy_kwargs(),
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
            **_legacy_kwargs(),
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
            **_legacy_kwargs(),
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
            **_legacy_kwargs(),
        )

        assert ma.vig == pytest.approx(1.08)
        np.testing.assert_allclose(
            ma.p_posterior,
            0.4 * p_cal + 0.6 * (p_market / p_market.sum()),
        )
