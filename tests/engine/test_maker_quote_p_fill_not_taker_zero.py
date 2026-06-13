# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: no-order root (live, 2026-06-13). The maker-quote lane hardcoded
#   p_fill_lcb=0.0 (a TAKER-shaped value: a maker rest has no own ask to cross), which
#   zeroed trade_score = p_fill_lcb x edge for EVERY maker buy_no regardless of edge,
#   structurally strangling the favorite-longshot NO harvest in a system that is
#   fundamentally a maker. The maker quote must carry the MAKER resting-fill prior.
#   This is the recurring "taker-shaped checks strangle makers" class.
"""Antibody: the maker-quote lane must not return a taker-shaped p_fill_lcb=0.0.

A maker rest's fill probability is the maker resting-fill prior, never the taker
visible-depth LCB (which is 0 on a maker quote because there is no own ask). Pinning
this keeps trade_score from being structurally zeroed for every maker candidate.
"""
from __future__ import annotations

import inspect

from src.engine import event_reactor_adapter as era
from src.strategy.live_inference.mode_consistent_ev import MAKER_FILL_PROBABILITY_PRIOR


def test_maker_quote_does_not_hardcode_taker_zero_p_fill():
    src = inspect.getsource(era._maker_quote_execution_price_from_snapshot)
    # The taker-shaped zero must be gone from the return path.
    assert "p_fill_lcb = 0.0" not in src, (
        "maker quote re-introduced the taker-shaped p_fill_lcb=0.0 — it zeroes "
        "trade_score = p_fill x edge for every maker buy_no regardless of edge."
    )
    # The maker resting-fill prior is the lane-correct admission probability.
    assert "MAKER_FILL_PROBABILITY_PRIOR" in src


def test_maker_fill_prior_is_positive():
    """trade_score = p_fill_lcb x edge can only be positive for a strong-edge maker
    candidate if the maker fill prior is strictly positive."""
    assert float(MAKER_FILL_PROBABILITY_PRIOR) > 0.0


def test_trade_score_positive_for_strong_edge_maker_candidate():
    """Relationship: with the maker fill prior, a strong native-NO edge yields a
    POSITIVE trade_score (the old p_fill=0 made it 0 regardless of edge)."""
    from src.strategy.live_inference.trade_score import robust_trade_score
    from src.contracts.execution_price import ExecutionPrice

    # A favorite-longshot NO: model ~0.91 confident, maker quote cost ~0.30 -> edge ~0.6.
    q_lcb_no = 0.91
    maker_cost = 0.30
    ep = ExecutionPrice(value=maker_cost, price_type="bid", fee_deducted=True,
                        currency="probability_units")
    score_zero_pfill = robust_trade_score(
        trade_score_id="t", q_posterior=q_lcb_no, q_5pct=q_lcb_no,
        c_95pct=ep, c_stress=ep, p_fill_lcb=0.0,
    ).score
    score_maker_pfill = robust_trade_score(
        trade_score_id="t", q_posterior=q_lcb_no, q_5pct=q_lcb_no,
        c_95pct=ep, c_stress=ep, p_fill_lcb=float(MAKER_FILL_PROBABILITY_PRIOR),
    ).score
    assert score_zero_pfill == 0.0, "p_fill=0 must zero trade_score (the old killer)"
    assert score_maker_pfill > 0.0, (
        "maker fill prior must make a strong-edge maker candidate's trade_score positive"
    )
