# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: architecture/money_path_ci.yaml MP-ORD-001/MP-ORD-004; src/execution/order_truth_reducer.py
"""Money-path model tests for monotonic venue order truth."""

from __future__ import annotations

from decimal import Decimal

from src.execution.order_truth_reducer import (
    PARTIAL_WITH_REMAINDER,
    TERMINAL_FILLED,
    TERMINAL_NO_FILL,
    UNKNOWN_SIDE_EFFECT,
    VenueOrderTruthReducer,
)


def test_terminal_fill_cannot_be_demoted_by_later_resting_fact() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {"state": "MATCHED", "remaining_size": "0", "matched_size": "5"},
            {"state": "RESTING", "remaining_size": "5", "matched_size": "0"},
        ],
        trade_filled_size="5",
        command_size="5",
        open_order_present=True,
    )

    assert reduced.state == "MATCHED"
    assert reduced.proof_class == TERMINAL_FILLED
    assert reduced.remaining_size == Decimal("0")


def test_terminal_no_fill_cannot_create_exposure() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {"state": "EXPIRED", "remaining_size": "0", "matched_size": "0"},
            {"state": "LIVE", "remaining_size": "5", "matched_size": "0"},
        ],
        trade_filled_size="0",
        command_size="5",
        open_order_present=True,
    )

    assert reduced.proof_class == TERMINAL_NO_FILL
    assert reduced.matched_size == Decimal("0")
    assert reduced.remaining_size == Decimal("0")


def test_positive_trade_fact_creates_partial_exposure_once() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[{"state": "EXPIRED", "remaining_size": "0", "matched_size": "0"}],
        trade_filled_size="2",
        command_size="5",
    )

    assert reduced.state == "PARTIALLY_MATCHED"
    assert reduced.proof_class == PARTIAL_WITH_REMAINDER
    assert reduced.matched_size == Decimal("2")
    assert reduced.remaining_size == Decimal("3")


def test_absent_open_order_without_fact_is_unknown_side_effect() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[],
        trade_filled_size="0",
        command_size="5",
        open_order_present=False,
    )

    assert reduced.state == "UNKNOWN"
    assert reduced.proof_class == UNKNOWN_SIDE_EFFECT
