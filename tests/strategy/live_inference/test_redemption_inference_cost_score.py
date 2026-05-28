# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md R4/R5 proof.

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.strategy.live_inference.executable_cost import (
    QuoteBook,
    native_executable_cost,
    reject_forbidden_cost_source,
)
from src.strategy.live_inference.inference_engine import InferenceInputs, evaluate_live_bins
from src.strategy.live_inference.trade_score import robust_trade_score


def _book() -> QuoteBook:
    return QuoteBook(
        yes_asks=((0.42, 10.0),),
        no_asks=((0.35, 10.0),),
        yes_bids=((0.40, 10.0),),
        no_bids=((0.33, 10.0),),
        fee_rate=0.05,
        tick_size=0.01,
        min_order_size=1.0,
        neg_risk=False,
    )


def test_orderbook_event_does_not_change_q_live():
    base = evaluate_live_bins(InferenceInputs(prior=(0.4, 0.6)))
    with_orderbook = evaluate_live_bins(InferenceInputs(prior=(0.4, 0.6), orderbook_event=True))

    assert with_orderbook == base


def test_partial_forecast_no_live_trade():
    with pytest.raises(ValueError, match="partial forecast"):
        evaluate_live_bins(InferenceInputs(prior=(0.5, 0.5), forecast_complete=False))


def test_day0_mask_zero_mass_blocks():
    with pytest.raises(ValueError, match="zero probability mass"):
        evaluate_live_bins(InferenceInputs(prior=(0.5, 0.5), day0_mask=(0.0, 0.0)))


def test_buy_no_native_no_ask_only():
    cost = native_executable_cost(_book(), direction="buy_no", shares=1.0)

    assert cost.value > 0.35
    assert cost.value < 0.37


def test_midpoint_display_last_trade_and_no_complement_forbidden():
    for source in ("midpoint", "display_probability", "last_trade_price", "no_complement"):
        with pytest.raises(ValueError, match="forbidden"):
            reject_forbidden_cost_source(source)


def test_min_order_change_blocks():
    with pytest.raises(ValueError, match="min_order_size"):
        native_executable_cost(_book(), direction="buy_yes", shares=0.5)


def test_robust_trade_score_uses_typed_execution_price():
    cost = ExecutionPrice(0.40, "ask", fee_deducted=False, currency="probability_units").with_taker_fee()
    result = robust_trade_score(
        trade_score_id="score-1",
        q_posterior=0.6,
        q_5pct=0.55,
        c_95pct=cost,
        c_stress=cost,
        p_fill_lcb=0.8,
    )

    assert result.score > 0
