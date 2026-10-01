# Lifecycle: created=2026-05-21; last_reviewed=2026-10-01; last_reused=2026-10-01
# Purpose: Relationship antibody for monotonic venue order truth reduction.
# Reuse: Run when changing venue order fact precedence, command recovery,
#        exchange reconciliation, or terminal/no-fill projection semantics.
# Authority basis: user live endpoint asymmetry analysis 2026-05-21; monotonic venue order truth reducer

from __future__ import annotations

from decimal import Decimal

import pytest

from src.contracts.canonical_lifecycle import OrderProofClass
from src.execution.order_truth_reducer import (
    PARTIAL_WITH_REMAINDER,
    TERMINAL_FILLED,
    TERMINAL_NO_FILL,
    TERMINAL_PARTIAL,
    VenueOrderTruthReducer,
)


def test_reduce_returns_typed_order_proof_class() -> None:
    # The reducer's proof_class is the typed canonical OrderProofClass (still
    # str-comparable for legacy consumers, but now type-enforced).
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[{"state": "MATCHED", "remaining_size": "0", "matched_size": "5"}],
        trade_filled_size="5",
        command_size="5",
    )
    assert isinstance(reduced.proof_class, OrderProofClass)
    assert reduced.proof_class is OrderProofClass.TERMINAL_FILLED
    # Backward-compat: still equals the legacy string and module constant.
    assert reduced.proof_class == "TERMINAL_FILLED"
    assert reduced.proof_class == TERMINAL_FILLED
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

    # A later fill changes exposure, not the venue's already-terminal remainder.
    assert reduced.state == "EXPIRED"
    assert reduced.proof_class == TERMINAL_PARTIAL
    assert reduced.remaining_size == Decimal("0")
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


def test_matched_zero_remainder_short_fill_is_terminal_partial() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {"state": "MATCHED", "remaining_size": "0", "matched_size": "7"},
        ],
        trade_filled_size="7",
        command_size="17.5",
    )

    assert reduced.state == "MATCHED"
    assert reduced.proof_class == TERMINAL_PARTIAL
    assert reduced.remaining_size == Decimal("0")
    assert reduced.matched_size == Decimal("7")


def test_terminal_partial_fact_prevents_short_matched_string_regression() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {"state": "MATCHED", "remaining_size": "0", "matched_size": "11"},
            {
                "state": "PARTIALLY_MATCHED",
                "remaining_size": "0",
                "matched_size": "11",
            },
        ],
        trade_filled_size="11",
        command_size="15",
    )

    assert reduced.state == "PARTIALLY_MATCHED"
    assert reduced.proof_class == TERMINAL_PARTIAL
    assert reduced.remaining_size == Decimal("0")
    assert reduced.matched_size == Decimal("11")


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


def test_cancel_confirmed_partial_remainder_is_terminal_no_resting_truth() -> None:
    reduced = VenueOrderTruthReducer.reduce(
        order_facts=[
            {
                "state": "CANCEL_CONFIRMED",
                "remaining_size": "15.07",
                "matched_size": "10",
            },
            {
                "state": "PARTIALLY_MATCHED",
                "remaining_size": "15.07",
                "matched_size": "10",
            },
        ],
        trade_filled_size="10",
        command_size="25.07",
        open_order_present=True,
    )

    assert reduced.state == "CANCEL_CONFIRMED"
    assert reduced.proof_class == TERMINAL_PARTIAL
    assert reduced.remaining_size == Decimal("0")
    assert reduced.matched_size == Decimal("10")


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


def _fact_writer_conn(tmp_path, side):
    conn = get_connection(tmp_path / f"order-facts-{side}.db")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, created_at, updated_at
        ) VALUES ('cmd-1', 'snap-1', 'env-1', 'pos-1', 'dec-1', 'idem-1', ?,
                  'm-1', 'tok-1', ?, 5, 0.20, 'order-1', 'ACKED',
                  '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')
        """,
        ("entry" if side == "BUY" else "exit", side),
    )
    conn.commit()
    return conn


def _write_fact(conn, state, remaining, matched, tag):
    return append_order_fact(
        conn, venue_order_id="order-1", command_id="cmd-1", state=state,
        remaining_size=remaining, matched_size=matched, source="REST",
        observed_at="2026-10-01T00:00:00+00:00", raw_payload_hash=tag * 64,
    )


def _stored_truth(conn):
    facts = [dict(row) for row in conn.execute(
        "SELECT state, remaining_size, matched_size FROM venue_order_facts "
        "ORDER BY local_sequence")]
    return facts, VenueOrderTruthReducer.reduce(order_facts=facts, command_size="5")


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("fill_state,remaining,matched", [
    ("PARTIALLY_MATCHED", "3", "2"),
    ("PARTIALLY_MATCHED", "0", "5"),
])
@pytest.mark.parametrize("terminal_first", [True, False])
def test_writer_persists_positive_fill_on_either_side_of_terminal_fact(
    tmp_path, side, fill_state, remaining, matched, terminal_first,
) -> None:
    conn = _fact_writer_conn(tmp_path, side)
    try:
        writes = [("EXPIRED", "0", "0", "a"), (fill_state, remaining, matched, "b")]
        for write in (writes if terminal_first else writes[::-1]):
            _write_fact(conn, *write)
        # Repeated delivery of the same fill evidence is economically empty.
        _write_fact(conn, fill_state, remaining, matched, "b")
        facts, truth = _stored_truth(conn)
        assert len(facts) == 2
        assert max(Decimal(f["matched_size"]) for f in facts) == Decimal(matched)
        assert truth.matched_size == Decimal(matched)
        assert truth.remaining_size == Decimal("0")
    finally:
        conn.close()


def test_writer_suppresses_stale_fill_not_above_stored_evidence(tmp_path) -> None:
    conn = _fact_writer_conn(tmp_path, "BUY")
    try:
        _write_fact(conn, "PARTIALLY_MATCHED", "2", "3", "a")
        terminal_id = _write_fact(conn, "EXPIRED", "0", "3", "b")
        assert _write_fact(conn, "PARTIALLY_MATCHED", "3", "2", "c") == terminal_id
        assert _write_fact(conn, "LIVE", "5", "0", "d") == terminal_id
        facts, truth = _stored_truth(conn)
        assert len(facts) == 2
        assert (truth.matched_size, truth.remaining_size) == (Decimal("3"), Decimal("0"))
    finally:
        conn.close()
