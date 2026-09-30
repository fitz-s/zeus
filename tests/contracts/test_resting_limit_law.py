# Created: 2026-09-30
# Last audited: 2026-09-30
# Authority basis: funnel-leak investigation 2026-09-30 (82 on 09-29 / 53 on
#   09-30 GLOBAL_BUY_JIT_MAKER_WITNESS_SUPERSEDED:current_limit_or_cashflow_changed
#   on a flickering top bid; cut 7615 posted limit 0.25 under a 0.26 JIT bid);
#   price band [0.05, 0.95] inclusive (venue_submission_envelope).
"""One law decides whether a selected resting limit is valid on the current book.

The JIT preflight, the final submit gate and the executor recapture all call
``resting_limit_violation``; a bid retreat below the limit keeps it, a bid at or
above it, an ask at or below it, or a price outside the band rejects it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.contracts.executable_cost_curve import BidBookLevel, BookLevel, ExecutableCostCurve, FeeModel
from src.contracts.venue_submission_envelope import resting_limit_violation
from src.solve.solver import passive_buy_proposal_at_limit, passive_buy_proposal_curve


@pytest.mark.parametrize(
    ("side", "limit", "bid", "ask", "expected"),
    (
        ("BUY", "0.27", "0.24", "0.30", None),  # HK low: bid retreated, limit kept
        ("BUY", "0.27", "0.26", "0.30", None),
        ("BUY", "0.27", "0.27", "0.30", "at_or_below_best_bid"),
        ("BUY", "0.25", "0.26", "0.30", "at_or_below_best_bid"),  # cut 7615
        ("BUY", "0.27", "0.24", "0.27", "at_or_above_best_ask"),
        ("BUY", "0.27", "0.24", "0.26", "at_or_above_best_ask"),
        ("BUY", "0.27", None, "0.30", None),
        ("BUY", "0.27", "0.24", None, None),
        # SELL crosses only at or below the bid; at/behind the ask it rests.
        ("SELL", "0.61", "0.60", "0.61", None),  # 1-tick spread default bid+tick
        ("SELL", "0.61", "0.58", "0.60", None),
        ("SELL", "0.61", "0.61", "0.70", "at_or_below_best_bid"),
        ("SELL", "0.61", "0.63", "0.70", "at_or_below_best_bid"),
        ("SELL", "0.61", None, None, None),
        # Band edges: inclusive [0.05, 0.95], both sides.
        ("BUY", "0.05", "0.04", "0.06", None),
        ("BUY", "0.049", "0.04", "0.06", "outside_live_band"),
        ("BUY", "0.95", "0.94", "0.96", None),
        ("BUY", "0.951", "0.94", "0.96", "outside_live_band"),
        ("SELL", "0.05", "0.04", "0.06", None),
        ("SELL", "0.049", "0.04", "0.06", "outside_live_band"),
        ("SELL", "0.95", "0.94", "0.95", None),
        ("SELL", "0.951", "0.94", "0.96", "outside_live_band"),
        ("BUY", "nan", "0.24", "0.30", "price_invalid"),
    ),
)
def test_resting_limit_violation(side, limit, bid, ask, expected):
    assert (
        resting_limit_violation(side, limit, best_bid=bid, best_ask=ask) == expected
    )


def test_resting_limit_violation_requires_a_side():
    with pytest.raises(ValueError):
        resting_limit_violation("HOLD", "0.27", best_bid="0.24", best_ask="0.30")


def test_one_tick_spread_sell_default_is_a_valid_maker_rest():
    from src.solve.solver import ExecutableSellCurve, passive_sell_proposal_curve

    curve = ExecutableSellCurve(
        token_id="tok",
        side="YES",
        snapshot_id="snap",
        book_hash="hash",
        levels=(BidBookLevel(price=Decimal("0.60"), size=Decimal("10")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"),
        quote_ttl=timedelta(seconds=30),
    )
    proposal = passive_sell_proposal_curve(curve, capacity=Decimal("10"))
    assert proposal is not None
    assert proposal.levels[0].price == Decimal("0.61")


def _curve(ask: str) -> ExecutableCostCurve:
    return ExecutableCostCurve(
        token_id="tok",
        side="NO",
        snapshot_id="snap",
        book_hash="hash",
        levels=(BookLevel(price=Decimal(ask), size=Decimal("100")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"),
        quote_ttl=timedelta(seconds=30),
    )


def _bids(price: str) -> tuple[BidBookLevel, ...]:
    return (BidBookLevel(price=Decimal(price), size=Decimal("50")),)


def test_proposal_at_limit_keeps_limit_after_bid_retreat():
    proposal = passive_buy_proposal_at_limit(
        _curve("0.30"), native_bid_levels=_bids("0.24"), limit=Decimal("0.27")
    )
    assert proposal is not None
    assert proposal.levels[0].price == Decimal("0.27")
    assert proposal.levels[0].size == Decimal("50")


@pytest.mark.parametrize(("bid", "ask"), (("0.27", "0.30"), ("0.28", "0.30"), ("0.24", "0.27")))
def test_proposal_at_limit_rejects_outside_spread(bid, ask):
    assert (
        passive_buy_proposal_at_limit(
            _curve(ask), native_bid_levels=_bids(bid), limit=Decimal("0.27")
        )
        is None
    )


def test_bid_plus_tick_proposal_is_the_same_law_at_its_own_limit():
    proposal = passive_buy_proposal_curve(_curve("0.30"), native_bid_levels=_bids("0.26"))
    assert proposal is not None and proposal.levels[0].price == Decimal("0.27")
    # One-tick spread: bid+tick would cross.
    assert passive_buy_proposal_curve(_curve("0.27"), native_bid_levels=_bids("0.26")) is None
    # Above the band.
    assert passive_buy_proposal_curve(_curve("0.97"), native_bid_levels=_bids("0.95")) is None


def _witness(bid, ask):
    return SimpleNamespace(current_best_bid=bid, current_best_ask=ask)


def test_final_submit_gate_refuses_limit_at_or_below_bid():
    from src.engine.event_reactor_adapter import (
        _assert_final_resting_limit_valid,
        _submit_price_moved_abort_reason,
    )

    maker = {"post_only": True, "side": "BUY", "limit_price": 0.25}
    with pytest.raises(ValueError, match="MAKER_LIMIT_SUPERSEDED:at_or_below_best_bid") as exc:
        _assert_final_resting_limit_valid(maker, _witness(0.26, 0.30))
    assert _submit_price_moved_abort_reason(exc.value).startswith(
        "SUBMIT_ABORTED_PRICE_MOVED:MAKER_LIMIT_SUPERSEDED:at_or_below_best_bid"
    )
    with pytest.raises(ValueError, match="at_or_below_best_bid"):
        _assert_final_resting_limit_valid(maker, _witness(0.25, 0.30))
    with pytest.raises(ValueError, match="at_or_above_best_ask"):
        _assert_final_resting_limit_valid(maker, _witness(0.20, 0.25))
    _assert_final_resting_limit_valid(maker, _witness(0.24, 0.30))
    # A taker crosses by design; the resting law does not apply.
    _assert_final_resting_limit_valid(
        {"post_only": False, "side": "BUY", "limit_price": 0.30}, _witness(0.29, 0.30)
    )


def test_final_submit_gate_is_wired_before_witness_adoption():
    import inspect

    from src.engine import event_reactor_adapter as era

    source = inspect.getsource(era._build_live_execution_command_certificates)
    gate = source.index("_assert_final_resting_limit_valid(")
    assert source.index("_assert_final_jit_witness_revalidates_intent(") < gate
    assert gate < source.index("persist_presubmit_jit_snapshot(")


def _sell_authority(bid: str, ask: str | None, limit: str = "0.61"):
    from src.solve.solver import ExecutableSellCurve

    curve = ExecutableSellCurve(
        token_id="tok",
        side="YES",
        snapshot_id="snap",
        book_hash="hash",
        levels=(BidBookLevel(price=Decimal(bid), size=Decimal("10")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"),
        quote_ttl=timedelta(seconds=30),
    )
    proposal = SimpleNamespace(levels=(SimpleNamespace(price=Decimal(limit)),))
    candidate = SimpleNamespace(
        execution_mode="MAKER_REST",
        executable_sell_curve=curve,
        economic_sell_curve=proposal,
        native_ask_levels=(
            () if ask is None else (BookLevel(price=Decimal(ask), size=Decimal("10")),)
        ),
    )
    return SimpleNamespace(jit_candidate=candidate, actuation=None)


def test_final_sell_maker_limit_uses_the_same_law():
    from src.execution.exit_lifecycle import GlobalSellExecutionAuthority

    limit_of = GlobalSellExecutionAuthority.limit_price
    # Bid retreated below the selected limit: no longer bid+tick, still valid.
    assert limit_of(_sell_authority("0.58", "0.70")) == Decimal("0.61")
    assert limit_of(_sell_authority("0.60", None)) == Decimal("0.61")
    # 1-tick spread: limit == ask rests in the ask queue.
    assert limit_of(_sell_authority("0.60", "0.61")) == Decimal("0.61")
    with pytest.raises(ValueError, match="at_or_below_best_bid"):
        limit_of(_sell_authority("0.61", "0.70"))
