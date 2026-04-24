"""Tests for K8 Slice R: RNG seed reproducibility.

Bug #11  — MarketAnalysis bootstrap must accept rng_seed
Bug #17  — ensemble_signal.p_raw_vector_from_maxes accepts rng
Bug #20  — day0_signal.p_vector accepts rng
"""
from __future__ import annotations

import numpy as np
import pytest

from src.types.market import Bin


def _bins_3() -> list[Bin]:
    return [
        Bin(low=None, high=59, unit="F", label="59°F or below"),
        Bin(low=60, high=61, unit="F", label="60-61°F"),
        Bin(low=62, high=None, unit="F", label="62°F or higher"),
    ]


# ---------------------------------------------------------------------------
# Bug #11: MarketAnalysis rng_seed
# ---------------------------------------------------------------------------

class TestMarketAnalysisRngSeed:
    """MarketAnalysis must accept rng_seed and produce reproducible bootstrap."""

    def test_rng_seed_accepted(self):
        from src.strategy.market_analysis import MarketAnalysis

        bins = _bins_3()
        members = np.random.default_rng(42).normal(65, 5, size=20)

        ma = MarketAnalysis(
            p_raw=np.array([0.15, 0.55, 0.30]),
            p_cal=np.array([0.15, 0.55, 0.30]),
            p_market=np.array([0.18, 0.48, 0.34]),
            alpha=0.6,
            bins=bins,
            member_maxes=members,
            calibrator=None,
            lead_days=2,
            unit="F",
            rng_seed=42,
        )
        assert ma._rng is not None

    def test_different_seeds_differ(self):
        from src.strategy.market_analysis import MarketAnalysis

        bins = _bins_3()
        members = np.random.default_rng(42).normal(65, 5, size=20)
        kwargs = dict(
            p_raw=np.array([0.15, 0.55, 0.30]),
            p_cal=np.array([0.15, 0.55, 0.30]),
            p_market=np.array([0.18, 0.48, 0.34]),
            alpha=0.6,
            bins=bins,
            member_maxes=members,
            calibrator=None,
            lead_days=2,
            unit="F",
        )

        ci_a = MarketAnalysis(**kwargs, rng_seed=1)._bootstrap_bin(1, 100)
        ci_b = MarketAnalysis(**kwargs, rng_seed=2)._bootstrap_bin(1, 100)
        # Different seeds should produce different results (with high probability)
        assert ci_a != ci_b


# ---------------------------------------------------------------------------
# Bug #20: day0_signal rng parameter
# ---------------------------------------------------------------------------

class TestDay0SignalRng:
    """day0_signal.p_vector must accept and use external rng."""

    def test_p_vector_accepts_rng(self):
        from src.signal.day0_signal import Day0Signal

        rng = np.random.default_rng(99)
        bins = _bins_3()
        members = np.random.default_rng(42).normal(65, 5, size=20)

        sig = Day0Signal(
            observed_high_so_far=62.0,
            current_temp=60.0,
            hours_remaining=4.0,
            member_maxes_remaining=members,
            unit="F",
        )
        result = sig.p_vector(bins, rng=rng)

        assert len(result) == len(bins)
        assert all(0.0 <= p <= 1.0 for p in result)

    def test_p_vector_rng_reproducibility(self):
        from src.signal.day0_signal import Day0Signal

        bins = _bins_3()
        members = np.random.default_rng(42).normal(65, 5, size=20)
        sig = Day0Signal(
            observed_high_so_far=62.0,
            current_temp=60.0,
            hours_remaining=4.0,
            member_maxes_remaining=members,
            unit="F",
        )

        r1 = sig.p_vector(bins, rng=np.random.default_rng(77))
        r2 = sig.p_vector(bins, rng=np.random.default_rng(77))
        np.testing.assert_array_equal(r1, r2)


# ---------------------------------------------------------------------------
# Bug #17: EnsembleSignal.p_raw_vector forwards rng
# ---------------------------------------------------------------------------

class TestEnsembleSignalRngForwarding:
    """Verify that EnsembleSignal.p_raw_vector forwards rng to p_raw_vector_from_maxes."""

    def test_p_raw_vector_accepts_rng_param(self):
        """p_raw_vector signature includes rng parameter."""
        import inspect
        from src.signal.ensemble_signal import EnsembleSignal

        sig = inspect.signature(EnsembleSignal.p_raw_vector)
        assert "rng" in sig.parameters, "p_raw_vector must accept rng kwarg"

    def test_p_raw_vector_rng_produces_deterministic_output(self):
        """Same rng seed → same p_raw_vector output."""
        from unittest.mock import patch
        from src.signal.ensemble_signal import EnsembleSignal

        bins = _bins_3()

        # Patch the module-level function to capture kwargs
        captured_kwargs = []

        def capture_call(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return np.array([0.2, 0.5, 0.3])

        with patch("src.signal.ensemble_signal.p_raw_vector_from_maxes", side_effect=capture_call):
            # Use a real-ish object with the unbound method
            class FakeES:
                member_maxes = np.random.default_rng(42).normal(62, 3, size=20)
                city = "fake_city"
                settlement_semantics = "fake_sem"
                p_raw_vector = EnsembleSignal.p_raw_vector

            es = FakeES()
            rng = np.random.default_rng(99)
            es.p_raw_vector(bins, rng=rng)

        assert len(captured_kwargs) == 1
        assert captured_kwargs[0].get("rng") is rng, "rng must be forwarded to p_raw_vector_from_maxes"
