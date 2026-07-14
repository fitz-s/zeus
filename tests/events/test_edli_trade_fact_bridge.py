# Created: 2026-06-06
# Last reused/audited: 2026-07-11
# Purpose: Lock EDLI fill-audit bridge from authenticated WS trade facts.
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from src.events.edli_trade_fact_bridge import append_confirmed_trade_facts_to_edli
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.state.db import init_schema
from src.state.venue_command_repo import append_trade_fact


NOW = datetime(2026, 6, 6, 21, 40, tzinfo=timezone.utc)


def test_confirmed_ws_trade_fact_appends_edli_user_trade_observed():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_edli_chain(ledger)
    _insert_command(conn)
    append_trade_fact(
        conn,
        trade_id="trade-1",
        venue_order_id="venue-1",
        command_id="cmd-1",
        state="CONFIRMED",
        filled_size="7",
        fill_price="0.72",
        source="WS_USER",
        observed_at=NOW,
        venue_timestamp=NOW,
        raw_payload_hash="a" * 64,
        raw_payload_json=json.dumps({"id": "trade-1", "status": "CONFIRMED"}),
        tx_hash="0xtx",
    )

    appended = append_confirmed_trade_facts_to_edli(conn, now=NOW)

    assert appended == 1
    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "USER_TRADE_OBSERVED"
    row = conn.execute(
        "SELECT payload_json FROM edli_live_order_events WHERE event_type='UserTradeObserved'"
    ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["fill_authority_state"] == "FILL_CONFIRMED"
    assert payload["trade_id"] == "trade-1"
    assert payload["filled_size"] == "7"
    assert payload["fill_price"] == "0.72"
    assert payload["source_trade_fact_authority"] == "venue_trade_facts:WS_USER:CONFIRMED"

    assert append_confirmed_trade_facts_to_edli(conn, now=NOW) == 0


def test_bridge_does_not_promote_non_confirmed_or_non_user_channel_facts():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_edli_chain(ledger)
    _insert_command(conn)
    append_trade_fact(
        conn,
        trade_id="trade-rest",
        venue_order_id="venue-1",
        command_id="cmd-1",
        state="CONFIRMED",
        filled_size="7",
        fill_price="0.72",
        source="REST",
        observed_at=NOW,
        venue_timestamp=NOW,
        raw_payload_hash="b" * 64,
        raw_payload_json="{}",
    )
    append_trade_fact(
        conn,
        trade_id="trade-mined",
        venue_order_id="venue-1",
        command_id="cmd-1",
        state="MINED",
        filled_size="7",
        fill_price="0.72",
        source="WS_USER",
        observed_at=NOW,
        venue_timestamp=NOW,
        raw_payload_hash="c" * 64,
        raw_payload_json="{}",
    )

    assert append_confirmed_trade_facts_to_edli(conn, now=NOW) == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM edli_live_order_events WHERE event_type='UserTradeObserved'"
    ).fetchone()[0] == 0


def test_bridge_prefers_attached_trades_over_ghost_world_tables(tmp_path):
    world_conn = _conn()
    ledger = LiveOrderAggregateLedger(world_conn)
    _seed_edli_chain(ledger)
    # Simulate the live split-brain hazard: world/main has legacy ghost tables,
    # while the authoritative execution facts live in zeus_trades.db.
    world_conn.execute("DELETE FROM venue_commands")
    world_conn.execute("DELETE FROM venue_trade_facts")

    trade_path = tmp_path / "zeus_trades.db"
    trade_conn = sqlite3.connect(trade_path)
    trade_conn.row_factory = sqlite3.Row
    init_schema(trade_conn)
    _insert_command(trade_conn)
    append_trade_fact(
        trade_conn,
        trade_id="trade-attached",
        venue_order_id="venue-1",
        command_id="cmd-1",
        state="CONFIRMED",
        filled_size="7",
        fill_price="0.72",
        source="WS_USER",
        observed_at=NOW,
        venue_timestamp=NOW,
        raw_payload_hash="d" * 64,
        raw_payload_json="{}",
    )
    trade_conn.commit()
    trade_conn.close()

    assert append_confirmed_trade_facts_to_edli(world_conn, now=NOW, trade_db_path=trade_path) == 1
    assert world_conn.execute(
        "SELECT COUNT(*) FROM edli_live_order_events WHERE event_type='UserTradeObserved'"
    ).fetchone()[0] == 1


def test_bridge_deduplicates_redecision_commands_and_uses_latest_command_time():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_edli_chain(ledger)
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        )
        SELECT 'duplicate-execution-command', aggregate_id, 50, event_type,
               parent_event_hash, 'duplicate-execution-command-hash', payload_json,
               payload_hash, source_authority, ?, ?, schema_version
          FROM edli_live_order_events
         WHERE aggregate_id = 'event-1:intent-1'
           AND event_type = 'ExecutionCommandCreated'
         LIMIT 1
        """,
        (
            (NOW + timedelta(minutes=30)).isoformat(),
            (NOW + timedelta(minutes=30)).isoformat(),
        ),
    )
    _insert_command(conn)
    append_trade_fact(
        conn,
        trade_id="trade-redecision",
        venue_order_id="venue-1",
        command_id="cmd-1",
        state="CONFIRMED",
        filled_size="7",
        fill_price="0.72",
        source="WS_USER",
        observed_at=NOW,
        venue_timestamp=NOW,
        raw_payload_hash="e" * 64,
        raw_payload_json="{}",
    )
    append_trade_fact(
        conn,
        trade_id="trade-redecision",
        venue_order_id="venue-1",
        command_id="cmd-1",
        state="CONFIRMED",
        filled_size="7",
        fill_price="0.72",
        source="WS_USER",
        observed_at=NOW + timedelta(minutes=1),
        venue_timestamp=NOW + timedelta(minutes=1),
        raw_payload_hash="f" * 64,
        raw_payload_json="{}",
    )

    assert append_confirmed_trade_facts_to_edli(conn, now=NOW) == 1
    row = conn.execute(
        """
        SELECT occurred_at, payload_json
          FROM edli_live_order_events
         WHERE event_type='UserTradeObserved'
        """
    ).fetchone()
    payload = json.loads(row["payload_json"])
    assert row["occurred_at"] == (NOW + timedelta(minutes=30)).isoformat()
    assert payload["source_trade_observed_at"] == (
        NOW + timedelta(minutes=1)
    ).isoformat()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _insert_command(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at
        ) VALUES (
            'cmd-1', 'snap-1', 'env-1', 'pos-1', 'command-1',
            'idem-1', 'ENTRY', 'market-1', 'token-1', 'BUY', 7,
            0.72, 'venue-1', 'FILLED',
            '2026-06-06T21:39:00+00:00',
            '2026-06-06T21:40:00+00:00'
        )
        """
    )


def _seed_edli_chain(ledger: LiveOrderAggregateLedger) -> None:
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitPlanBuilt",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    pre_submit = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
            "usage_id": "usage-1",
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "command-1"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "venue_order_id": "venue-1",
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )


def _pre_submit_payload() -> dict:
    return {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "token-1",
        "side": "BUY",
        "direction": "buy_no",
        "order_type": "FOK_LIMIT",
        "time_in_force": "FOK",
        "post_only": False,
        "checked_at": NOW.isoformat(),
        "quote_seen_at": NOW.isoformat(),
        "quote_age_ms": 10,
        "book_hash": "book-hash",
        "current_best_bid": 0.71,
        "current_best_ask": 0.72,
        "limit_price": 0.72,
        "size": 7.0,
        "q_live": 0.95,
        "q_lcb_5pct": 0.90,
        "expected_edge": 0.18,
        "selection_authority_applied": "qkernel_spine",
        "min_entry_price": 0.10,
        "min_expected_profit_usd": 1.0,
        "min_submit_edge_density": 0.05,
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "route_type": "portfolio",
        },
        "would_cross_book": True,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 5,
        "size_ok": True,
        "neg_risk": True,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "book_authority_id": "clob_jit_book",
        "book_captured_at": NOW.isoformat(),
        "heartbeat_authority_id": "heartbeat",
        "heartbeat_checked_at": NOW.isoformat(),
        "user_ws_authority_id": "ws_user",
        "user_ws_checked_at": NOW.isoformat(),
        "venue_connectivity_authority_id": "clob",
        "venue_connectivity_checked_at": NOW.isoformat(),
        "balance_allowance_authority_id": "wallet",
        "balance_allowance_checked_at": NOW.isoformat(),
        "expected_edge_source_certificate_hash": "actionable-hash-1",
        "cost_basis_source_certificate_hash": "cost-hash-1",
    }


# ---------------------------------------------------------------------------
# Fill-orphan recovery lane (HK 30C 2026-06-12 incident antibodies).
# A venue fill whose WS_USER CONFIRMED message was lost to a user-channel
# dropout exists only as a REST trade fact under a terminal FILLED command.
# The WS bridge can never promote it; the recovery lane must — with explicit
# RECONCILE_SOURCE provenance — and must NEVER fire inside the grace window
# or when the WS truth already exists.
# ---------------------------------------------------------------------------
from src.events.edli_trade_fact_bridge import append_rest_filled_orphan_trade_facts_to_edli


def _insert_rest_only_fill(
    conn, *, observed_at, trade_id="trade-orphan", state="MATCHED"
):
    append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id="venue-1",
        command_id="cmd-1",
        state=state,
        filled_size="5",
        fill_price="0.72",
        source="REST",
        observed_at=observed_at,
        venue_timestamp=observed_at,
        raw_payload_hash="e" * 64,
        raw_payload_json="{}",
    )


def test_rest_orphan_past_grace_is_recovered_with_reconcile_provenance():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_edli_chain(ledger)
    _insert_command(conn)
    _insert_rest_only_fill(conn, observed_at=NOW + timedelta(minutes=1))

    assert append_rest_filled_orphan_trade_facts_to_edli(conn, now=NOW + timedelta(hours=3)) == 1
    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "USER_TRADE_OBSERVED"
    row = conn.execute(
        "SELECT payload_json FROM edli_live_order_events WHERE event_type='UserTradeObserved'"
    ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["fill_authority_state"] == "FILL_CONFIRMED"
    assert payload["source_authority"] == "venue_reconcile"
    assert payload["source_trade_fact_authority"] == "venue_trade_facts:REST:MATCHED"
    assert payload["venue_command_state"] == "FILLED"
    assert "ws_user_confirmed_missing_after_grace" in payload["recovery_basis"]

    # Idempotent: the UserTradeObserved row now exists for this trade_id.
    assert append_rest_filled_orphan_trade_facts_to_edli(conn, now=NOW + timedelta(hours=3)) == 0


def test_rest_orphan_inside_grace_window_is_left_for_the_user_channel():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_edli_chain(ledger)
    _insert_command(conn)
    _insert_rest_only_fill(conn, observed_at=NOW + timedelta(minutes=1))

    assert append_rest_filled_orphan_trade_facts_to_edli(conn, now=NOW + timedelta(minutes=6)) == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM edli_live_order_events WHERE event_type='UserTradeObserved'"
    ).fetchone()[0] == 0


def test_rest_orphan_with_ws_confirmed_sibling_never_double_recovers():
    """When the user channel DID deliver for this trade, the WS bridge owns it
    — the recovery lane must not produce a second authority."""
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_edli_chain(ledger)
    _insert_command(conn)
    _insert_rest_only_fill(conn, observed_at=NOW + timedelta(minutes=1), trade_id="trade-1")
    append_trade_fact(
        conn,
        trade_id="trade-1",
        venue_order_id="venue-1",
        command_id="cmd-1",
        state="CONFIRMED",
        filled_size="5",
        fill_price="0.72",
        source="WS_USER",
        observed_at=NOW + timedelta(minutes=1),
        venue_timestamp=NOW + timedelta(minutes=1),
        raw_payload_hash="f" * 64,
        raw_payload_json="{}",
    )

    assert append_rest_filled_orphan_trade_facts_to_edli(conn, now=NOW + timedelta(hours=3)) == 0


def test_rest_orphan_under_non_terminal_command_is_not_recovered():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_edli_chain(ledger)
    _insert_command(conn)
    conn.execute("UPDATE venue_commands SET state='ACKED' WHERE command_id='cmd-1'")
    _insert_rest_only_fill(conn, observed_at=NOW + timedelta(minutes=1))

    assert append_rest_filled_orphan_trade_facts_to_edli(conn, now=NOW + timedelta(hours=3)) == 0


def test_rest_orphan_nonfill_state_is_not_recovered():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_edli_chain(ledger)
    _insert_command(conn)
    _insert_rest_only_fill(
        conn,
        observed_at=NOW + timedelta(minutes=1),
        state="FAILED",
    )

    assert append_rest_filled_orphan_trade_facts_to_edli(
        conn, now=NOW + timedelta(hours=3)
    ) == 0


def test_rest_orphan_uses_latest_command_time_and_one_logical_trade_fact():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_edli_chain(ledger)
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        )
        SELECT 'redecision-execution-command', aggregate_id, 50, event_type,
               parent_event_hash, 'redecision-execution-command-hash', payload_json,
               payload_hash, source_authority, ?, ?, schema_version
          FROM edli_live_order_events
         WHERE aggregate_id = 'event-1:intent-1'
           AND event_type = 'ExecutionCommandCreated'
         LIMIT 1
        """,
        (
            (NOW + timedelta(minutes=30)).isoformat(),
            (NOW + timedelta(minutes=30)).isoformat(),
        ),
    )
    _insert_command(conn)
    _insert_rest_only_fill(
        conn,
        observed_at=NOW + timedelta(minutes=1),
        trade_id="trade-multistatus",
        state="MATCHED",
    )
    _insert_rest_only_fill(
        conn,
        observed_at=NOW + timedelta(minutes=7),
        trade_id="trade-multistatus",
        state="CONFIRMED",
    )

    assert append_rest_filled_orphan_trade_facts_to_edli(
        conn, now=NOW + timedelta(hours=3)
    ) == 1
    row = conn.execute(
        """
        SELECT occurred_at, payload_json
          FROM edli_live_order_events
         WHERE event_type = 'UserTradeObserved'
        """
    ).fetchone()
    payload = json.loads(row["payload_json"])
    assert row["occurred_at"] == (NOW + timedelta(minutes=30)).isoformat()
    assert payload["source_trade_observed_at"] == (
        NOW + timedelta(minutes=7)
    ).isoformat()
    assert payload["source_trade_fact_authority"] == (
        "venue_trade_facts:REST:CONFIRMED"
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM edli_live_order_events WHERE event_type='UserTradeObserved'"
    ).fetchone()[0] == 1
