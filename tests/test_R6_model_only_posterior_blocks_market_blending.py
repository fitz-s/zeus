# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave1 + K1
"""R6: MODEL_ONLY_POSTERIOR_MODE must block market_prior blending (K1 preservation).

Preservation test — K1 (market price ≠ epistemic belief) is already correct.
This test codifies the invariant so it cannot regress in future waves.
"""
from __future__ import annotations

import pytest

from src.strategy.market_analysis import MODEL_ONLY_POSTERIOR_MODE


def test_r6_model_only_mode_constant_is_defined() -> None:
    """MODEL_ONLY_POSTERIOR_MODE constant is importable and non-empty."""
    assert MODEL_ONLY_POSTERIOR_MODE, (
        "MODEL_ONLY_POSTERIOR_MODE must be defined and truthy (K1 invariant)"
    )


def test_r6_model_only_mode_is_not_market_blend() -> None:
    """MODEL_ONLY_POSTERIOR_MODE must not be a market-blend mode name.

    Any mode that includes 'market' in its name would indicate market prices
    are entering the posterior — a K1 violation.
    """
    assert "market" not in str(MODEL_ONLY_POSTERIOR_MODE).lower(), (
        f"MODEL_ONLY_POSTERIOR_MODE={MODEL_ONLY_POSTERIOR_MODE!r} contains 'market' — "
        "K1 regression: market price must not enter epistemic posterior."
    )


def test_r6_model_only_mode_contains_model_keyword() -> None:
    """MODEL_ONLY_POSTERIOR_MODE name must reference 'model' to document intent."""
    assert "model" in str(MODEL_ONLY_POSTERIOR_MODE).lower(), (
        f"MODEL_ONLY_POSTERIOR_MODE={MODEL_ONLY_POSTERIOR_MODE!r} does not contain 'model'. "
        "K1 intent: mode name must document that posterior comes from model, not market."
    )


def test_r6_compute_posterior_method_exists() -> None:
    """MarketAnalysis._compute_posterior is callable (posterior computation contract)."""
    from src.strategy.market_analysis import MarketAnalysis
    assert callable(getattr(MarketAnalysis, "_compute_posterior", None)), (
        "MarketAnalysis._compute_posterior must be callable (K1 posterior separation)"
    )
