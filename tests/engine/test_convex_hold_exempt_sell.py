"""Convex positions are exempt from auction SELL candidacy (reversal_plan_tier0 item 8).

A convex entry (avg entry price < CONVEX_HOLD_PRICE_THRESHOLD) may exit only on
hard-fact zero-support direct sells, RED force exits, or operational emergency —
never on the capital-velocity GLOBAL_CAPITAL_OPTIMAL_SELL reallocation objective
(84% of historical convex early exits fired while the position's own belief law
said HOLD; pooled recovery 0.987 = liquidation breakeven, tail surrendered).
"""

from dataclasses import dataclass

from src.engine.global_batch_runtime import (
    CONVEX_HOLD_PRICE_THRESHOLD,
    _convex_hold_exempt_position_ids,
)


@dataclass
class _Exposure:
    avg_price: float


class _Position:
    def __init__(self, position_id: str, avg_price: float):
        self.position_id = position_id
        self._avg_price = avg_price

    def effective_exposure(self) -> _Exposure:
        return _Exposure(avg_price=self._avg_price)


class _State:
    def __init__(self, positions):
        self.positions = tuple(positions)


def test_convex_position_is_exempt():
    state = _State([_Position("pos-cheap", 0.15)])
    assert _convex_hold_exempt_position_ids(state) == frozenset({"pos-cheap"})


def test_rich_position_is_not_exempt():
    state = _State([_Position("pos-rich", 0.40)])
    assert _convex_hold_exempt_position_ids(state) == frozenset()


def test_threshold_boundary_is_not_exempt():
    state = _State([_Position("pos-at", float(CONVEX_HOLD_PRICE_THRESHOLD))])
    assert _convex_hold_exempt_position_ids(state) == frozenset()


def test_zero_or_missing_price_is_not_exempt():
    # No positive avg entry price → nothing to classify as convex; stays on
    # the ordinary capital-velocity objective (documented fail-open-to-normal).
    state = _State([_Position("pos-zero", 0.0), _Position("pos-neg", -0.1)])
    assert _convex_hold_exempt_position_ids(state) == frozenset()


def test_position_without_exposure_method_is_skipped():
    class _Bare:
        position_id = "pos-bare"

    state = _State([])
    state.positions = (_Bare(),)
    assert _convex_hold_exempt_position_ids(state) == frozenset()


def test_mixed_book_partitions_correctly():
    state = _State(
        [
            _Position("a", 0.05),
            _Position("b", 0.249),
            _Position("c", 0.25),
            _Position("d", 0.70),
        ]
    )
    assert _convex_hold_exempt_position_ids(state) == frozenset({"a", "b"})
