from __future__ import annotations

import json
import sqlite3

import pytest

from scripts import resolve_edli_unknown_by_authenticated_absence as absence
from scripts import resolve_unresolved_edli_submit as prevenue
from src.state.schema import edli_live_order_events_schema


AGG = "event-1:intent-1"
TOKEN = "token-1"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    edli_live_order_events_schema.ensure_tables(conn)
    return conn


def _event(conn: sqlite3.Connection, seq: int, event_type: str, payload: dict) -> None:
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            f"agg-event-{seq}",
            AGG,
            seq,
            event_type,
            f"hash-{seq}",
            json.dumps(payload),
            f"payload-hash-{seq}",
            "existing_executor" if event_type in {"VenueSubmitAttempted", "SubmitUnknown"} else "engine_adapter",
            "2026-06-06T18:00:00+00:00",
            "2026-06-06T18:00:00+00:00",
        ),
    )


def _post_submit_unknown(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO edli_live_order_projection (
            aggregate_id, event_id, final_intent_id, current_state,
            last_sequence, last_event_type, last_event_hash,
            pending_reconcile, venue_order_id, updated_at, schema_version
        ) VALUES (?, 'event-1', 'intent-1', 'PENDING_RECONCILE',
                  3, 'SubmitUnknown', 'hash-3', 1, NULL,
                  '2026-06-06T18:00:00+00:00', 1)
        """,
        (AGG,),
    )
    _event(
        conn,
        1,
        "SubmitPlanBuilt",
        {
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "condition_id": "condition-1",
            "direction": "buy_no",
            "limit_price": 0.72,
            "size": 23.76,
            "token_id": TOKEN,
        },
    )
    _event(
        conn,
        2,
        "VenueSubmitAttempted",
        {
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "idempotency_key": "idem-1",
        },
    )
    _event(
        conn,
        3,
        "SubmitUnknown",
        {
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "execution_receipt_hash": "receipt-hash",
            "venue_call_started": True,
            "side_effect_known": False,
            "reconciliation_followup_required": True,
        },
    )


def test_prevenue_resolver_skips_post_submit_unknown_even_without_order_id() -> None:
    conn = _conn()
    _post_submit_unknown(conn)

    assert prevenue._stuck_aggregates(conn) == []


def test_authenticated_absence_proof_rejects_matching_trade_exposure() -> None:
    conn = _conn()
    _post_submit_unknown(conn)

    with pytest.raises(RuntimeError, match="matching exposure"):
        absence.build_absence_proof(
            conn,
            AGG,
            open_orders=[],
            trades=[{"asset_id": TOKEN, "side": "BUY", "matched_amount": "1"}],
        )


def test_authenticated_absence_proof_records_zero_matching_exposure() -> None:
    conn = _conn()
    _post_submit_unknown(conn)

    proof = absence.build_absence_proof(
        conn,
        AGG,
        open_orders=[{"asset_id": "other-token"}],
        trades=[{"asset_id": "other-token"}],
    )

    assert proof["matching_open_order_count"] == 0
    assert proof["matching_trade_count"] == 0
    assert proof["open_orders_query_complete"] is True
    assert proof["trades_query_complete"] is True
    assert len(proof["proof_hash"]) == 64
