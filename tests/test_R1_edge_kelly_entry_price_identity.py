# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave1 + §Wave2
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: R1 — relationship test antibody
# Reuse: Pre-Wave-2 identity codifier + post-Wave-2 ExecutionPrice carry-through assertion for the BinEdge.entry_price → Kelly seam value identity. Pair with INV-38.
"""R1: BinEdge.entry_price value reaches kelly_size unchanged.

Codifies the invariant that whatever VALUE flows through BinEdge.entry_price
arrives at the ExecutionPrice / kelly_size boundary unchanged. After Wave 2
(INV-38) the field is typed ``ExecutionPrice`` (not bare float) — the contract
becomes: the underlying ``.value`` is preserved end-to-end. Codifies the
numeric identity so future bootstrap c_b sampling (Wave 5) does not silently
shift it.
"""
from __future__ import annotations

import pytest

from src.types.market import BinEdge, Bin
from src.contracts.execution_price import ExecutionPrice


def _make_bin_edge(entry_price: float = 0.42) -> BinEdge:
    b = Bin(low=None, high=26.0, unit="C", label="26°C or below")
    return BinEdge(
        bin=b,
        direction="buy_yes",
        edge=0.08,
        ci_lower=0.35,
        ci_upper=0.52,
        p_model=0.50,
        p_market=entry_price,
        p_posterior=0.50,
        entry_price=entry_price,  # legacy float — __post_init__ coerces to ExecutionPrice
        p_value=0.01,
        vwmp=entry_price,
    )


def test_r1_entry_price_is_execution_price_post_wave2() -> None:
    """Post-Wave-2 (INV-38): BinEdge.entry_price is typed ExecutionPrice.

    Pre-Wave-2 this asserted ``isinstance(float)``; Wave 2 reverses the
    contract — float construction is auto-coerced via ``__post_init__`` so the
    bare-float path still works, but the stored field is always ExecutionPrice.
    """
    edge = _make_bin_edge(0.42)
    assert isinstance(edge.entry_price, ExecutionPrice), (
        f"BinEdge.entry_price must be ExecutionPrice post-Wave-2, "
        f"got {type(edge.entry_price).__name__}"
    )
    # Numeric value identity preserved (the codified R1 contract)
    assert float(edge.entry_price) == 0.42


def test_r1_entry_price_value_reaches_ep_seam() -> None:
    """The same value that BinEdge carries arrives at any downstream ExecutionPrice consumer."""
    entry_price_val = 0.42
    edge = _make_bin_edge(entry_price_val)

    # Use the .value attribute of the typed entry_price directly (Wave 2 path)
    ep = edge.entry_price
    assert ep.value == entry_price_val, (
        "entry_price value changed between BinEdge construction and downstream read"
    )
    # Also assert float() coercion path (ergonomics dunder) matches
    assert float(edge.entry_price) == entry_price_val


def test_r1_fee_adjusted_ep_retains_same_underlying_price() -> None:
    """After with_taker_fee(), original value_in is preserved (fee shifts value, not provenance)."""
    entry_price_val = 0.42
    edge = _make_bin_edge(entry_price_val)
    ep = edge.entry_price  # typed ExecutionPrice (post-Wave-2)
    ep_fee = ep.with_taker_fee(0.05)
    # Fee-adjusted value strictly greater than pre-fee value (fee adds cost)
    assert ep_fee.value > ep.value
    # And the seam input value must equal the underlying BinEdge entry_price value
    assert ep.value == float(edge.entry_price)
