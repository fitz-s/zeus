# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §12 native executable cost contract.
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta, timezone

import pytest

from src.strategy.live_inference.executable_cost import (
    ExecutableCostError,
    NativeQuoteBook,
    QuoteLevel,
    assert_neg_risk_matches,
    assert_not_last_trade_cost,
    assert_not_midpoint_cost,
    assert_not_no_complement_cost,
    executable_cost,
    quote_book_from_executable_snapshot,
)
from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2


def _book(**overrides) -> NativeQuoteBook:
    base = {
        "yes_asks": (QuoteLevel(Decimal("0.44"), Decimal("10")),),
        "no_asks": (QuoteLevel(Decimal("0.61"), Decimal("10")),),
        "yes_bids": (QuoteLevel(Decimal("0.42"), Decimal("10")),),
        "no_bids": (QuoteLevel(Decimal("0.59"), Decimal("10")),),
        "min_tick_size": Decimal("0.01"),
        "min_order_size": Decimal("5"),
        "fee_rate": 0.05,
        "neg_risk": False,
    }
    base.update(overrides)
    return NativeQuoteBook(**base)


def test_buy_no_uses_native_no_ask_not_complement():
    price = executable_cost(_book(), direction="buy_no", shares=Decimal("5"))
    assert price.value > 0.61
    assert price.price_type == "fee_adjusted"
    price.assert_kelly_safe()


def test_buy_yes_uses_native_yes_ask():
    price = executable_cost(_book(), direction="buy_yes", shares=Decimal("5"))
    assert price.value > 0.44


def test_sell_uses_held_token_bid():
    price = executable_cost(_book(), direction="sell_yes", shares=Decimal("5"))
    assert price.value < 0.42
    assert price.price_type == "fee_adjusted"


def test_midpoint_forbidden():
    with pytest.raises(ExecutableCostError, match="midpoint"):
        assert_not_midpoint_cost(used_midpoint=True)


def test_last_trade_forbidden_as_cost():
    with pytest.raises(ExecutableCostError, match="last_trade"):
        assert_not_last_trade_cost(used_last_trade=True)


def test_no_complement_forbidden_as_cost():
    with pytest.raises(ExecutableCostError, match="1 - yes_price"):
        assert_not_no_complement_cost(used_yes_complement=True)


def test_fee_erases_edge():
    price = executable_cost(_book(yes_asks=(QuoteLevel(Decimal("0.99"), Decimal("10")),)), direction="buy_yes", shares=Decimal("5"))
    assert price.value > 0.99


def test_tick_size_mismatch_blocks():
    with pytest.raises(ExecutableCostError, match="tick"):
        executable_cost(
            _book(yes_asks=(QuoteLevel(Decimal("0.445"), Decimal("10")),)),
            direction="buy_yes",
            shares=Decimal("5"),
        )


def test_multilevel_vwap_need_not_be_tick_aligned_when_levels_are_valid():
    price = executable_cost(
        _book(yes_asks=(QuoteLevel(Decimal("0.44"), Decimal("1")), QuoteLevel(Decimal("0.45"), Decimal("4")))),
        direction="buy_yes",
        shares=Decimal("5"),
    )
    assert price.value > 0.448


def test_neg_risk_mismatch_blocks():
    with pytest.raises(ExecutableCostError, match="negRisk"):
        assert_neg_risk_matches(_book(neg_risk=False), expected_neg_risk=True)


def test_min_order_blocks():
    with pytest.raises(ExecutableCostError, match="min order"):
        executable_cost(_book(), direction="buy_yes", shares=Decimal("4"))


def test_no_depth_blocks():
    with pytest.raises(ExecutableCostError, match="NO_DEPTH"):
        executable_cost(_book(no_asks=()), direction="buy_no", shares=Decimal("5"))


def test_quote_book_from_executable_snapshot_uses_snapshot_fee_tick_min_order_negrisk():
    now = datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc)
    snapshot = ExecutableMarketSnapshotV2(
        snapshot_id="snap-1",
        gamma_market_id="gamma-1",
        event_id="event-1",
        event_slug="chicago-weather",
        condition_id="0xcondition",
        question_id="question-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
        selected_outcome_token_id=None,
        outcome_label=None,
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=None,
        market_end_at=None,
        market_close_at=None,
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        fee_details={"fee_rate_fraction": 0.05},
        token_map_raw={},
        rfqe=None,
        neg_risk=True,
        orderbook_top_bid=Decimal("0.42"),
        orderbook_top_ask=Decimal("0.44"),
        orderbook_depth_jsonb='{"yes-token":{"asks":[{"price":"0.44","size":"10"}],"bids":[{"price":"0.42","size":"10"}]},"no-token":{"asks":[{"price":"0.61","size":"10"}],"bids":[{"price":"0.59","size":"10"}]}}',
        raw_gamma_payload_hash="a" * 64,
        raw_clob_market_info_hash="b" * 64,
        raw_orderbook_hash="c" * 64,
        authority_tier="CLOB",
        captured_at=now,
        freshness_deadline=now + timedelta(seconds=30),
    )

    book = quote_book_from_executable_snapshot(snapshot)

    assert book.min_tick_size == Decimal("0.01")
    assert book.min_order_size == Decimal("5")
    assert book.fee_rate == 0.05
    assert book.neg_risk is True
    assert executable_cost(book, direction="buy_no", shares=Decimal("5")).value > 0.61
