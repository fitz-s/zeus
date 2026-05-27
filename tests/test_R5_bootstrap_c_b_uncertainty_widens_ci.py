# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave1 + D6
"""R5: sigma_market > 0 must widen edge_ci_lower vs legacy fixed c_b.

RED today — MarketAnalysis._bootstrap_bin uses fixed p_market[i] across all
bootstrap iterations. When called with sigma_market=0.03, ci_lower must be
lower than with sigma_market=0 (wider CI). Today: no sigma_market param exists,
so the call with sigma_market>0 either raises or produces identical CI to
sigma_market=0 — both are RED.

Wave 5 adds c_b ~ N(p_market, sigma_market) per bootstrap iteration so the
CI widens. Flip GREEN by adding sigma_market parameter to _bootstrap_bin.

Antibody for INV-40 market-cost uncertainty arm.
"""
from __future__ import annotations

import pytest
import inspect


@pytest.mark.xfail(
    reason="Wave 5 — MarketAnalysis._bootstrap_bin has no sigma_market parameter. "
           "Flip GREEN when c_b ~ N(p_market, sigma_market) per iteration is added.",
    strict=True,
)
def test_r5_bootstrap_bin_accepts_sigma_market_parameter() -> None:
    """_bootstrap_bin must accept a sigma_market parameter (Wave 5 interface contract).

    RED today: no sigma_market parameter exists.
    """
    from src.strategy.market_analysis import MarketAnalysis
    sig = inspect.signature(MarketAnalysis._bootstrap_bin)
    assert "sigma_market" in sig.parameters, (
        f"_bootstrap_bin signature {list(sig.parameters.keys())} lacks 'sigma_market'. "
        "D6 defect: market-cost uncertainty absent from bootstrap CI computation."
    )


@pytest.mark.xfail(
    reason="Wave 5 — _bootstrap_bin uses fixed c_b=p_market[i]; sigma_market not sampled. "
           "Flip GREEN when sigma_market>0 produces strictly wider CI than sigma_market=0.",
    strict=True,
)
def test_r5_sigma_market_gt0_widens_edge_ci_lower() -> None:
    """ci_lower with sigma_market=0.03 must be < ci_lower with sigma_market=0.

    Calls production _bootstrap_bin with sigma_market=0 and sigma_market=0.03.
    Today: AttributeError (no param) or identical CI (fixed c_b). Both = RED.
    Post-Wave-5: ci_lower_with_sigma < ci_lower_legacy.
    """
    import numpy as np
    from src.strategy.market_analysis import MarketAnalysis

    rng = np.random.default_rng(42)
    p_raw = np.array([0.60, 0.25, 0.15])
    p_cal = np.array([0.60, 0.25, 0.15])
    p_market = np.array([0.45, 0.30, 0.25])
    bin_idx = 0
    n_bootstrap = 500

    # This call must raise AttributeError or produce same result as sigma_market=0
    # to stay RED. Post-Wave-5, it must produce wider CI.
    ci_legacy = MarketAnalysis._bootstrap_bin(
        p_raw=p_raw, p_cal=p_cal, p_market=p_market,
        bin_idx=bin_idx, n_bootstrap=n_bootstrap, rng=rng,
        sigma_market=0.0,
    )
    rng2 = np.random.default_rng(42)
    ci_with_sigma = MarketAnalysis._bootstrap_bin(
        p_raw=p_raw, p_cal=p_cal, p_market=p_market,
        bin_idx=bin_idx, n_bootstrap=n_bootstrap, rng=rng2,
        sigma_market=0.03,
    )

    lower_legacy = ci_legacy[0] if hasattr(ci_legacy, "__getitem__") else ci_legacy
    lower_sigma = ci_with_sigma[0] if hasattr(ci_with_sigma, "__getitem__") else ci_with_sigma

    assert lower_sigma < lower_legacy, (
        f"sigma_market=0.03 did not widen CI: lower_sigma={lower_sigma:.4f} >= "
        f"lower_legacy={lower_legacy:.4f}. D6: market-cost uncertainty absent from bootstrap."
    )
