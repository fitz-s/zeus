# Lifecycle: created=2026-05-15; last_reviewed=2026-05-15; last_reused=2026-05-15
# Purpose: Lock read-only live-order E2E proof classification and overclaim prevention.
# Reuse: Run after venue command schema, executor submit events, or live-order evidence rules change.
# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_live_order_e2e.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_live_order_e2e_under_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE venue_commands (
          command_id TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          decision_id TEXT,
          idempotency_key TEXT,
          snapshot_id TEXT,
          side TEXT,
          token_id TEXT,
          limit_price TEXT,
          size TEXT,
          created_at TEXT,
          updated_at TEXT
        );
        CREATE TABLE venue_command_events (
          event_id TEXT PRIMARY KEY,
          command_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          state_after TEXT,
          occurred_at TEXT,
          payload_json TEXT
        );
        CREATE TABLE venue_submission_envelopes (
          envelope_id TEXT PRIMARY KEY,
          command_id TEXT NOT NULL,
          captured_at TEXT,
          order_id TEXT,
          raw_request_hash TEXT,
          raw_response_json TEXT
        );
        CREATE TABLE venue_order_facts (
          fact_id TEXT PRIMARY KEY,
          command_id TEXT NOT NULL,
          venue_order_id TEXT,
          observed_at TEXT,
          state TEXT
        );
        CREATE TABLE venue_trade_facts (
          fact_id TEXT PRIMARY KEY,
          command_id TEXT NOT NULL,
          observed_at TEXT,
          state TEXT
        );
        """
    )


def _insert_command(conn: sqlite3.Connection, *, state: str = "ACKED", decision_id: str = "decision-1") -> None:
    conn.execute(
        """
        INSERT INTO venue_commands (
          command_id, state, decision_id, idempotency_key, snapshot_id, side,
          token_id, limit_price, size, created_at, updated_at
        )
        VALUES ('cmd-1', ?, ?, 'idem-1', 'snap-1', 'BUY', 'token-1', '0.42',
                '10.00', '2026-05-15T12:00:00Z', '2026-05-15T12:00:02Z')
        """,
        (state, decision_id),
    )


def _insert_submit_requested(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO venue_command_events (
          event_id, command_id, event_type, state_after, occurred_at, payload_json
        )
        VALUES ('evt-1', 'cmd-1', 'SUBMIT_REQUESTED', 'SUBMITTING',
                '2026-05-15T12:00:01Z', '{}')
        """
    )


def _insert_submit_acked(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO venue_command_events (
          event_id, command_id, event_type, state_after, occurred_at, payload_json
        )
        VALUES ('evt-2', 'cmd-1', 'SUBMIT_ACKED', 'ACKED',
                '2026-05-15T12:00:02Z', ?)
        """,
        (json.dumps({"venue_order_id": "order-1"}),),
    )


def _insert_pre_submit_envelope(conn: sqlite3.Connection, *, order_id: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO venue_submission_envelopes (
          envelope_id, command_id, captured_at, order_id, raw_request_hash,
          raw_response_json
        )
        VALUES ('pre-submit:cmd-1', 'cmd-1', '2026-05-15T12:00:01Z',
                ?, 'hash-1', NULL)
        """,
        (order_id,),
    )


def _insert_order_fact(conn: sqlite3.Connection, *, command_id: str = "cmd-1", order_id: str = "order-1") -> None:
    conn.execute(
        """
        INSERT INTO venue_order_facts (
          fact_id, command_id, venue_order_id, observed_at, state
        )
        VALUES ('fact-1', ?, ?, '2026-05-15T12:00:03Z', 'RESTING')
        """,
        (command_id, order_id),
    )


def _schema_current(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE venue_commands (
          command_id TEXT PRIMARY KEY,
          snapshot_id TEXT NOT NULL,
          envelope_id TEXT NOT NULL,
          position_id TEXT NOT NULL,
          decision_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL UNIQUE,
          intent_kind TEXT NOT NULL,
          market_id TEXT NOT NULL,
          token_id TEXT NOT NULL,
          side TEXT NOT NULL,
          size REAL NOT NULL,
          price REAL NOT NULL,
          venue_order_id TEXT,
          state TEXT NOT NULL,
          last_event_id TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          review_required_reason TEXT
        );
        CREATE TABLE venue_command_events (
          event_id TEXT PRIMARY KEY,
          command_id TEXT NOT NULL,
          sequence_no INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          payload_json TEXT,
          state_after TEXT NOT NULL
        );
        CREATE TABLE venue_submission_envelopes (
          envelope_id TEXT PRIMARY KEY,
          raw_request_hash TEXT NOT NULL,
          raw_response_json TEXT,
          order_id TEXT,
          captured_at TEXT NOT NULL
        );
        CREATE TABLE venue_order_facts (
          fact_id TEXT PRIMARY KEY,
          command_id TEXT NOT NULL,
          venue_order_id TEXT,
          observed_at TEXT,
          state TEXT
        );
        CREATE TABLE venue_trade_facts (
          fact_id TEXT PRIMARY KEY,
          command_id TEXT NOT NULL,
          observed_at TEXT,
          state TEXT
        );
        """
    )


def test_accepted_order_with_full_correlation_trace_passes(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn)

    with module._connect_readonly(db) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "PASS"
    assert result["completion_category"] == "LIVE_ORDER_SUBMITTED"
    assert result["venue_order_id"] == "order-1"


def test_current_schema_envelope_id_link_passes(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema_current(conn)
        conn.execute(
            """
            INSERT INTO venue_submission_envelopes (
              envelope_id, raw_request_hash, raw_response_json, order_id,
              captured_at
            )
            VALUES ('env-current-1', 'hash-current-1', NULL, NULL,
                    '2026-05-15T12:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO venue_commands (
              command_id, snapshot_id, envelope_id, position_id, decision_id,
              idempotency_key, intent_kind, market_id, token_id, side, size,
              price, venue_order_id, state, last_event_id, created_at,
              updated_at, review_required_reason
            )
            VALUES (
              'cmd-current-1', 'snap-1', 'env-current-1', 'pos-1',
              'decision-1', 'idem-current-1', 'ENTRY', 'market-1', 'token-1',
              'BUY', 10.0, 0.42, 'order-current-1', 'ACKED', 'evt-current-2',
              '2026-05-15T12:00:00Z', '2026-05-15T12:00:02Z', NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO venue_command_events (
              event_id, command_id, sequence_no, event_type, occurred_at,
              payload_json, state_after
            )
            VALUES ('evt-current-1', 'cmd-current-1', 1, 'SUBMIT_REQUESTED',
                    '2026-05-15T12:00:01Z', '{}', 'SUBMITTING')
            """
        )
        conn.execute(
            """
            INSERT INTO venue_command_events (
              event_id, command_id, sequence_no, event_type, occurred_at,
              payload_json, state_after
            )
            VALUES ('evt-current-2', 'cmd-current-1', 2, 'SUBMIT_ACKED',
                    '2026-05-15T12:00:02Z',
                    '{"venue_order_id":"order-current-1"}', 'ACKED')
            """
        )
        conn.execute(
            """
            INSERT INTO venue_order_facts (
              fact_id, command_id, venue_order_id, observed_at, state
            )
            VALUES ('fact-current-1', 'cmd-current-1', 'order-current-1',
                    '2026-05-15T12:00:03Z', 'RESTING')
            """
        )

    with module._connect_readonly(db) as conn:
        result = module.evaluate(conn, "cmd-current-1")

    assert result["status"] == "PASS"
    assert result["completion_category"] == "LIVE_ORDER_SUBMITTED"
    assert result["venue_order_id"] == "order-current-1"


def test_accepted_ack_without_order_fact_is_not_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)

    with module._connect_readonly(db) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_ACKED_MISSING_ORDER_FACT"
    assert any(
        check["name"] == "venue_order_fact_present" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_rejected_order_is_recorded_but_not_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="SUBMIT_REJECTED")
        _insert_submit_requested(conn)
        _insert_pre_submit_envelope(conn)
        conn.execute(
            """
            INSERT INTO venue_command_events (
              event_id, command_id, event_type, state_after, occurred_at,
              payload_json
            )
            VALUES ('evt-2', 'cmd-1', 'SUBMIT_REJECTED', 'SUBMIT_REJECTED',
                    '2026-05-15T12:00:02Z', '{"reason":"closed"}')
            """
        )

    with module._connect_readonly(db) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_REJECTED_OR_UNKNOWN_RECORDED"
    assert any(
        check["name"] == "command_not_rejected_or_unknown" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_accepted_state_without_decision_trace_fails(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, decision_id="")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn)

    with module._connect_readonly(db) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert any(
        check["name"] == "command_decision_id_present" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_missing_trade_db_is_structured_no_proof(tmp_path, capsys):
    module = _load_module()
    missing_db = tmp_path / "missing.db"

    exit_code = module.main(["--trade-db", str(missing_db), "--json", "--allow-no-proof"])

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert result["checks"][0]["name"] == "trade_db_present"
