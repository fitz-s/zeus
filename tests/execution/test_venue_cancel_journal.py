# Created: 2026-07-03
# Authority basis: W4.2 relocation of the persisted-cancel journal engine out of the retired
#   src/execution/maker_rest_escalation.py into src/execution/venue_cancel_journal.py (still used
#   by main._edli_boot_invalid_pending_entry_authority_cancel_once,
#   main._edli_continuous_redecision_screen_cycle, and main._c3_staleness_cancel_cycle's carried-over
#   invalid-entry-authority lane). Retains the generic journaling-contract coverage from the retired
#   tests/execution/test_maker_rest_escalation.py (TestFailSoft/TestPersistedRestCancel) — the
#   deadline/TTL-classification tests (find_expired_resting_entries, run_cancels_for_expired_rests,
#   run_maker_rest_escalation_cycle) retired WITH the deleted module; that classification now lives
#   in tests/test_order_state_predicates.py and tests/execution/test_staleness_cancel.py.
"""Relationship tests for the shared persisted-cancel command-journal executor."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.execution.venue_cancel_journal import run_persisted_cancels_for_expired_rests

UTC = timezone.utc
NOW = datetime(2026, 7, 3, 22, 0, 0, tzinfo=UTC)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY, intent_kind TEXT, market_id TEXT,
            token_id TEXT, side TEXT, size REAL, price REAL,
            venue_order_id TEXT, state TEXT, last_event_id TEXT,
            created_at TEXT, updated_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE venue_command_events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT,
            state_after TEXT NOT NULL,
            UNIQUE (command_id, sequence_no)
        )"""
    )
    conn.execute(
        """CREATE TABLE provenance_envelope_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            payload_json TEXT,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            venue_timestamp TEXT,
            local_sequence INTEGER NOT NULL,
            UNIQUE (subject_type, subject_id, local_sequence)
        )"""
    )
    # append_event's terminal dispatch (SCH-W1.1-CAS-LEDGER terminalization-centrality
    # invariant) unconditionally reads this table for any command reaching a CANCELLED-
    # class terminal state — must exist (empty is fine; these fixtures never reserve).
    conn.execute(
        """CREATE TABLE collateral_reservations (
            command_id TEXT PRIMARY KEY,
            reservation_type TEXT NOT NULL,
            token_id TEXT,
            amount INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            released_at TEXT,
            release_reason TEXT,
            converted_amount INTEGER NOT NULL DEFAULT 0
        )"""
    )
    return conn


def _add_order(
    conn,
    *,
    command_id: str,
    intent_kind: str = "ENTRY",
    command_state: str = "ACKED",
    venue_order_id: str | None = None,
    created_at: datetime = NOW - timedelta(minutes=180),
):
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            command_id, intent_kind, "m1", "t1", "BUY", 10.0, 0.5,
            venue_order_id, command_state, None,
            created_at.isoformat(), created_at.isoformat(),
        ),
    )
    conn.execute(
        """INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after
        ) VALUES (?, ?, 1, 'INTENT_CREATED', ?, NULL, 'INTENT_CREATED')""",
        (f"{command_id}-intent", command_id, created_at.isoformat()),
    )
    conn.execute(
        """INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after
        ) VALUES (?, ?, 2, 'SUBMIT_ACKED', ?, NULL, 'ACKED')""",
        (f"{command_id}-acked", command_id, created_at.isoformat()),
    )


def _entry(command_id: str, venue_order_id: str, **overrides) -> dict:
    base = {
        "command_id": command_id,
        "venue_order_id": venue_order_id,
        "created_at": (NOW - timedelta(minutes=30)).isoformat(),
        "fact_state": "LIVE",
        "matched_size": "0",
        "cancel_reason": "TEST_CANCEL",
        "cancel_action": "CANCEL_REPLACE",
        "cancel_detail": {"trigger": "test"},
    }
    base.update(overrides)
    return base


class _FakeClob:
    def __init__(self, fail_on: set[str] | None = None):
        self.cancelled: list[str] = []
        self._fail_on = fail_on or set()

    def cancel_order(self, order_id: str):
        if order_id in self._fail_on:
            raise RuntimeError("venue cancel error")
        self.cancelled.append(order_id)
        return {"canceled": [order_id]}


class TestPersistedRestCancel:
    def test_persisted_cancel_records_command_terminal_state_before_harvest(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        collected: list[dict] = []
        clob = _FakeClob()

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")],
            clob,
            conn_factory=lambda: conn,
            close_connections=False,
            collect_cancelled=collected,
        )

        assert stats == {
            "scanned": 1, "cancelled": 1, "cancel_failed": 0, "cancel_journal_failed": 0,
        }
        assert clob.cancelled == ["o1"]
        assert [entry["command_id"] for entry in collected] == ["c1"]
        assert conn.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'c1'"
        ).fetchone()[0] == "CANCELLED"
        events = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            ).fetchall()
        ]
        assert events[-2:] == ["CANCEL_REQUESTED", "CANCEL_ACKED"]

    def test_cancel_unknown_event_carries_recovery_semantics(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        clob = _FakeClob(fail_on={"o1"})

        run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")], clob, conn_factory=lambda: conn, close_connections=False,
        )

        event = conn.execute(
            """
            SELECT event_type, payload_json
              FROM venue_command_events
             WHERE command_id = 'c1'
             ORDER BY sequence_no DESC
             LIMIT 1
            """
        ).fetchone()
        assert event["event_type"] == "CANCEL_REPLACE_BLOCKED"
        payload = json.loads(event["payload_json"])
        assert payload["reason"] == "post_cancel_unknown_possible_side_effect"
        assert payload["semantic_cancel_status"] == "CANCEL_UNKNOWN"
        assert payload["requires_m5_reconcile"] is True

    def test_cancel_not_canceled_is_recoverable_unknown_not_cancel_failed(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")

        class NotCanceledClob:
            def cancel_order(self, _order_id: str):
                return {
                    "orderID": "o1",
                    "status": "NOT_CANCELED",
                    "errorMessage": "order still live after cancel request",
                }

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")], NotCanceledClob(),
            conn_factory=lambda: conn, close_connections=False,
        )

        events = [
            (row["event_type"], json.loads(row["payload_json"] or "{}"))
            for row in conn.execute(
                "SELECT event_type, payload_json FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            )
        ]
        assert stats == {
            "scanned": 1, "cancelled": 0, "cancel_failed": 1, "cancel_journal_failed": 0,
        }
        assert events[-1][0] == "CANCEL_REPLACE_BLOCKED"
        assert "CANCEL_FAILED" not in [event_type for event_type, _ in events]
        assert events[-1][1]["semantic_cancel_status"] == "CANCEL_UNKNOWN"
        assert events[-1][1]["requires_m5_reconcile"] is True

    def test_terminal_command_race_does_not_append_cancel_replace_blocked(self):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")

        class RaceClob:
            def cancel_order(self, _order_id: str):
                conn.execute(
                    "UPDATE venue_commands SET state = 'CANCELLED' WHERE command_id = 'c1'"
                )
                conn.commit()
                raise RuntimeError("matched orders can't be canceled")

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")], RaceClob(),
            conn_factory=lambda: conn, close_connections=False,
        )

        event_types = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            ).fetchall()
        ]
        assert stats == {
            "scanned": 1, "cancelled": 0, "cancel_failed": 0, "cancel_journal_failed": 0,
        }
        assert event_types[-1] == "CANCEL_REQUESTED"
        assert "CANCEL_REPLACE_BLOCKED" not in event_types

    def test_pre_cancel_journal_lock_retry_is_idempotent_after_request_committed(self, monkeypatch):
        conn = _db()
        _add_order(conn, command_id="c1", venue_order_id="o1")
        collected: list[dict] = []
        clob = _FakeClob()

        import src.state.venue_command_repo as command_repo

        real_append_event = command_repo.append_event
        calls = {"count": 0}

        def lock_after_cancel_requested_committed(conn, *, command_id, event_type, occurred_at, payload):
            calls["count"] += 1
            event_id = real_append_event(
                conn, command_id=command_id, event_type=event_type,
                occurred_at=occurred_at, payload=payload,
            )
            if event_type == "CANCEL_REQUESTED" and calls["count"] == 1:
                conn.commit()
                raise sqlite3.OperationalError("database is locked")
            return event_id

        monkeypatch.setattr(command_repo, "append_event", lock_after_cancel_requested_committed)

        stats = run_persisted_cancels_for_expired_rests(
            [_entry("c1", "o1")], clob,
            conn_factory=lambda: conn, close_connections=False, collect_cancelled=collected,
        )

        assert stats == {
            "scanned": 1, "cancelled": 1, "cancel_failed": 0, "cancel_journal_failed": 0,
        }
        assert clob.cancelled == ["o1"]
        assert [entry["command_id"] for entry in collected] == ["c1"]
        events = [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM venue_command_events "
                "WHERE command_id = 'c1' ORDER BY sequence_no"
            ).fetchall()
        ]
        assert events.count("CANCEL_REQUESTED") == 1
        assert events[-2:] == ["CANCEL_REQUESTED", "CANCEL_ACKED"]

    def test_persisted_cancel_immediately_voids_zero_fill_pending_entry_projection(self):
        from src.execution.command_recovery import reconcile_unresolved_commands
        from src.state.db import init_schema
        from tests.test_command_recovery import (
            _advance_to_acked,
            _append_order_fact,
            _insert,
            _insert_decision_log_trade_case_for_recovery,
        )

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        _insert(conn, size=13.45, price=0.68)
        _advance_to_acked(conn, venue_order_id="ord-live")
        _append_order_fact(
            conn, order_id="ord-live", state="LIVE",
            matched_size="0", remaining_size="13.45", source="REST",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        mock_client = MagicMock(
            spec_set=["get_order", "get_open_orders", "get_trades", "get_clob_market_info", "v2_preflight"]
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []
        live_summary = reconcile_unresolved_commands(conn, mock_client)
        assert live_summary["live_entry_projection_repair"]["advanced"] == 1
        assert conn.execute(
            "SELECT phase FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()[0] == "pending_entry"

        clob = _FakeClob()
        stats = run_persisted_cancels_for_expired_rests(
            [
                {
                    "command_id": "cmd-001",
                    "venue_order_id": "ord-live",
                    "token_id": "tok-001",
                    "market_id": "mkt-001",
                    "created_at": "2026-04-26T00:00:00Z",
                    "fact_state": "LIVE",
                    "matched_size": "0",
                    "cancel_reason": "CONFIRMED_VALUE_REFRESH",
                    "cancel_action": "CANCEL_REPLACE",
                }
            ],
            clob,
            conn_factory=lambda: conn,
            close_connections=False,
        )

        assert stats == {
            "scanned": 1, "cancelled": 1, "cancel_failed": 0, "cancel_journal_failed": 0,
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "voided", "shares": 0.0, "cost_basis_usd": 0.0, "order_status": "canceled",
        }
        events = conn.execute(
            """
            SELECT event_type
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert [row["event_type"] for row in events] == [
            "POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED", "ENTRY_ORDER_VOIDED",
        ]
