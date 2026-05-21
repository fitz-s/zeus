# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Relationship antibody for monotonic venue order truth reduction.
# Reuse: Run when changing venue order fact precedence, command recovery,
#        exchange reconciliation, or terminal/no-fill projection semantics.
# Authority basis: user live endpoint asymmetry analysis 2026-05-21; monotonic venue order truth reducer

from __future__ import annotations

from decimal import Decimal

from src.execution.order_truth_reducer import (
    PARTIAL_WITH_REMAINDER,
    TERMINAL_FILLED,
    TERMINAL_NO_FILL,
    TERMINAL_PARTIAL,
    VenueOrderTruthReducer,
)
from src.state.db import get_connection, init_schema
from src.state.venue_command_repo import append_order_fact


def test_terminal_zero_remainder_no_fill_does_not_regress_to_live() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {"state": "EXPIRED", "remaining_size": "0", "matched_size": "0"},
            {"state": "LIVE", "remaining_size": "5", "matched_size": "0"},
        ],
        trade_filled_size="0",
        command_size="5",
        open_order_present=True,
    )

    assert reduced.state == "EXPIRED"
    assert reduced.proof_class == TERMINAL_NO_FILL
    assert reduced.remaining_size == Decimal("0")
    assert reduced.matched_size == Decimal("0")


def test_positive_trade_fact_cannot_reduce_to_terminal_no_fill() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {"state": "EXPIRED", "remaining_size": "0", "matched_size": "0"},
        ],
        trade_filled_size="2",
        command_size="5",
    )

    assert reduced.state == "PARTIALLY_MATCHED"
    assert reduced.proof_class == PARTIAL_WITH_REMAINDER
    assert reduced.remaining_size == Decimal("3")
    assert reduced.matched_size == Decimal("2")


def test_terminal_zero_remainder_partial_does_not_regress_to_open_remainder() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {"state": "EXPIRED", "remaining_size": "0", "matched_size": "2.11"},
            {"state": "PARTIALLY_MATCHED", "remaining_size": "2.26", "matched_size": "4.95"},
        ],
        trade_filled_size="4.95",
        command_size="7.21",
        open_order_present=True,
    )

    assert reduced.state == "EXPIRED"
    assert reduced.proof_class == TERMINAL_PARTIAL
    assert reduced.remaining_size == Decimal("0")
    assert reduced.matched_size == Decimal("4.95")


def test_matched_zero_remainder_order_fact_outranks_command_size_residue() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {"state": "MATCHED", "remaining_size": "0", "matched_size": "4.99"},
            {"state": "RESTING", "remaining_size": "0.01", "matched_size": "4.99"},
        ],
        trade_filled_size="4.99",
        command_size="5",
        open_order_present=False,
    )

    assert reduced.state == "MATCHED"
    assert reduced.proof_class == TERMINAL_FILLED
    assert reduced.remaining_size == Decimal("0")
    assert reduced.matched_size == Decimal("4.99")


def test_terminal_positive_zero_remainder_does_not_regress_to_later_partial() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {"state": "EXPIRED", "remaining_size": "0", "matched_size": "100"},
            {"state": "PARTIALLY_MATCHED", "remaining_size": "81.16", "matched_size": "100"},
        ],
        trade_filled_size="100",
        command_size="181.16",
    )

    assert reduced.state == "EXPIRED"
    assert reduced.proof_class == TERMINAL_PARTIAL
    assert reduced.remaining_size == Decimal("0")
    assert reduced.matched_size == Decimal("100")


def test_absence_from_open_orders_alone_is_unknown_not_no_exposure() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[],
        trade_filled_size="0",
        command_size="5",
        open_order_present=False,
    )

    assert reduced.state == "UNKNOWN"
    assert reduced.proof_class == "UNKNOWN_SIDE_EFFECT"


def test_append_order_fact_uses_reducer_to_preserve_terminal_no_fill(tmp_path) -> None:
    conn = get_connection(tmp_path / "order-truth-reducer.db")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (
            'cmd-1', 'snap-1', 'env-1', 'pos-1', 'dec-1',
            'idem-1', 'entry', 'm-1', 'tok-1', 'BUY', 5, 0.20,
            'order-1', 'ACKED', NULL, '2026-05-21T09:59:00+00:00',
            '2026-05-21T09:59:00+00:00', NULL
        )
        """
    )
    conn.commit()

    terminal_fact_id = append_order_fact(
        conn,
        venue_order_id="order-1",
        command_id="cmd-1",
        state="EXPIRED",
        remaining_size="0",
        matched_size="0",
        source="REST",
        observed_at="2026-05-21T10:00:00+00:00",
        raw_payload_hash="a" * 64,
    )
    live_fact_id = append_order_fact(
        conn,
        venue_order_id="order-1",
        command_id="cmd-1",
        state="LIVE",
        remaining_size="5",
        matched_size="0",
        source="REST",
        observed_at="2026-05-21T10:01:00+00:00",
        raw_payload_hash="b" * 64,
    )

    assert live_fact_id == terminal_fact_id
    count = conn.execute("SELECT COUNT(*) FROM venue_order_facts").fetchone()[0]
    assert count == 1
