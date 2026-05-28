# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave2 + INV-38
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: R2 — relationship test antibody for INV-38
# Reuse: Production find_edges must construct typed ExecutionPrice(price_type='vwmp') for both buy_yes and buy_no; bare-float legacy fixtures get auto-coerced via __post_init__ to implied_probability with explicit logger WARNING.
"""R2: BinEdge.entry_price MUST be ExecutionPrice with executable provenance.

Antibody for INV-38 (bin_edge_entry_price_typed) and INV-39
(kelly_boundary_no_fabrication). Post-Wave-2 production path constructs
ExecutionPrice(price_type="vwmp") at the edge-scan seam in
MarketAnalysis.find_edges; legacy fixtures coerce floats via
BinEdge.__post_init__ with price_type="implied_probability" tagged
explicitly so the legacy path is visible, not silent.

The forbidden behaviour (D5 defect) was that evaluator.py
_size_at_execution_price_boundary fabricated
ExecutionPrice(price_type="implied_probability") over a bare float and
laundered it via .with_taker_fee() into "fee_adjusted" — bypassing the D3
contract. R2 asserts the production-path price_type is NEVER fabricated
implied_probability AND that whatever scan-side construction the find_edges
helpers produce carries one of the allowed provenance tags.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.types.market import BinEdge, Bin
from src.contracts.execution_price import ExecutionPrice

# Allowed price_type values at the BinEdge.entry_price level
# (post-Wave-2; Wave 3 may add "depth_walked").
_ALLOWED_PRICE_TYPES_POST_WAVE2 = {"vwmp", "ask", "fee_adjusted"}
# Legacy float coercion path is tagged implied_probability so the legacy
# semantic is visible — it is still allowed as a coercion result, NOT as a
# production-edge-scan output.
_ALLOWED_PRICE_TYPES_INCLUDING_LEGACY = _ALLOWED_PRICE_TYPES_POST_WAVE2 | {"implied_probability"}


def test_r2_bin_edge_entry_price_is_execution_price() -> None:
    """BinEdge.entry_price must be ExecutionPrice, not float (INV-38)."""
    b = Bin(low=None, high=26.0, unit="C", label="26°C or below")
    edge = BinEdge(
        bin=b,
        direction="buy_yes",
        edge=0.08,
        ci_lower=0.35,
        ci_upper=0.52,
        p_model=0.50,
        p_market=0.42,
        p_posterior=0.50,
        entry_price=0.42,  # bare float — __post_init__ coerces
        p_value=0.01,
        vwmp=0.42,
    )
    assert isinstance(edge.entry_price, ExecutionPrice), (
        f"BinEdge.entry_price must be ExecutionPrice post-INV-38, "
        f"got {type(edge.entry_price).__name__}"
    )


def test_r2_typed_construction_preserves_vwmp_provenance() -> None:
    """When the caller constructs ExecutionPrice(price_type='vwmp'), it survives BinEdge."""
    b = Bin(low=None, high=26.0, unit="C", label="26°C or below")
    vwmp_price = ExecutionPrice(
        value=0.42,
        price_type="vwmp",
        fee_deducted=False,
        currency="probability_units",
    )
    edge = BinEdge(
        bin=b,
        direction="buy_yes",
        edge=0.08,
        ci_lower=0.35,
        ci_upper=0.52,
        p_model=0.50,
        p_market=0.42,
        p_posterior=0.50,
        entry_price=vwmp_price,
        p_value=0.01,
        vwmp=0.42,
    )
    # Provenance preserved end-to-end (the production-path invariant)
    assert edge.entry_price.price_type == "vwmp", (
        f"VWMP provenance lost between construction and BinEdge: "
        f"{edge.entry_price.price_type}"
    )
    assert edge.entry_price in (vwmp_price,) or edge.entry_price is vwmp_price


def test_r2_production_find_edges_stamps_vwmp_provenance() -> None:
    """MarketAnalysis.find_edges (the production scan site) stamps price_type='vwmp'.

    This asserts that the post-Wave-2 production path constructs an
    ExecutionPrice with real VWMP provenance from `_buy_entry_price_from_clob`
    rather than letting a bare float reach BinEdge.__post_init__ (which would
    tag it implied_probability via the legacy coercion path).
    """
    from src.strategy.market_analysis import MarketAnalysis

    # Synthetic 2-bin partition where buy_yes edge is positive.
    bins = [
        Bin(low=None, high=26.0, unit="C", label="26°C or below"),
        Bin(low=26.0, high=None, unit="C", label="27°C or above"),
    ]
    p_raw = np.array([0.40, 0.60])
    p_cal = np.array([0.55, 0.45])  # calibrated favours bin 0
    p_market = np.array([0.30, 0.40])  # market underprices bin 0
    member_maxes = np.array([25.5, 25.5, 26.0, 26.5, 26.8])
    executable_mask = np.array([True, True])

    analysis = MarketAnalysis(
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_market,
        alpha=0.0,  # MODEL_ONLY effectively
        bins=bins,
        member_maxes=member_maxes,
        executable_mask=executable_mask,
        rng_seed=42,
    )
    edges = analysis.find_edges(n_bootstrap=20)
    assert len(edges) >= 1, "expected at least one positive-edge bin"

    for edge in edges:
        assert isinstance(edge.entry_price, ExecutionPrice), (
            f"production find_edges must construct typed ExecutionPrice, "
            f"got {type(edge.entry_price).__name__}"
        )
        assert edge.entry_price.price_type in _ALLOWED_PRICE_TYPES_POST_WAVE2, (
            f"production find_edges stamped price_type={edge.entry_price.price_type!r}; "
            f"must be in {_ALLOWED_PRICE_TYPES_POST_WAVE2} (NEVER 'implied_probability' — D5 defect)"
        )
        assert edge.entry_price.price_type != "implied_probability", (
            "production path must not produce implied_probability provenance (INV-38)"
        )
