# Lifecycle: created=2026-05-15; last_reviewed=2026-05-16; last_reused=2026-05-16
# Purpose: Lock read-only live-order E2E proof classification and overclaim prevention.
# Reuse: Run after venue command schema, executor submit events, or live-order evidence rules change.
# Created: 2026-05-15
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md

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
          venue_order_id TEXT,
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
          sequence_no INTEGER,
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
          source TEXT,
          observed_at TEXT,
          state TEXT,
          remaining_size TEXT,
          matched_size TEXT,
          local_sequence INTEGER
        );
        CREATE TABLE venue_trade_facts (
          fact_id TEXT PRIMARY KEY,
          command_id TEXT NOT NULL,
          venue_order_id TEXT,
          trade_id TEXT,
          source TEXT,
          filled_size TEXT,
          fill_price TEXT,
          observed_at TEXT,
          state TEXT,
          local_sequence INTEGER
        );
        CREATE TABLE position_events (
          event_id TEXT PRIMARY KEY,
          position_id TEXT NOT NULL,
          sequence_no INTEGER,
          event_type TEXT NOT NULL,
          command_id TEXT,
          order_id TEXT,
          env TEXT
        );
        CREATE TABLE position_current (
          position_id TEXT PRIMARY KEY,
          phase TEXT NOT NULL,
          order_id TEXT,
          order_status TEXT,
          shares TEXT,
          cost_basis_usd TEXT
        );
        """
    )


def _connect_module_readonly(module, trade_db: Path, tmp_path: Path):
    world_db = tmp_path / "zeus-world.db"
    forecasts_db = tmp_path / "zeus-forecasts.db"
    for db_path in (world_db, forecasts_db):
        sqlite3.connect(db_path).close()
    return module._connect_readonly(trade_db, world_db, forecasts_db)


def _insert_command(
    conn: sqlite3.Connection,
    *,
    state: str = "ACKED",
    decision_id: str = "decision-1",
    venue_order_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO venue_commands (
          command_id, state, venue_order_id, decision_id, idempotency_key,
          snapshot_id, side, token_id, limit_price, size, created_at, updated_at
        )
        VALUES ('cmd-1', ?, ?, ?, 'idem-1', 'snap-1', 'BUY', 'token-1', '0.42',
                '10.00', '2026-05-15T12:00:00Z', '2026-05-15T12:00:02Z')
        """,
        (state, venue_order_id, decision_id),
    )


def _insert_submit_requested(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO venue_command_events (
          event_id, command_id, sequence_no, event_type, state_after, occurred_at, payload_json
        )
        VALUES ('evt-1', 'cmd-1', 1, 'SUBMIT_REQUESTED', 'SUBMITTING',
                '2026-05-15T12:00:01Z', '{}')
        """
    )


def _insert_submit_acked(conn: sqlite3.Connection, *, order_id: str = "order-1") -> None:
    conn.execute(
        """
        INSERT INTO venue_command_events (
          event_id, command_id, sequence_no, event_type, state_after, occurred_at, payload_json
        )
        VALUES ('evt-2', 'cmd-1', 2, 'SUBMIT_ACKED', 'ACKED',
                '2026-05-15T12:00:02Z', ?)
        """,
        (json.dumps({"venue_order_id": order_id}),),
    )


def _insert_later_submit_rejected(
    conn: sqlite3.Connection,
    *,
    occurred_at: str = "2026-05-15T12:00:03Z",
    sequence_no: int = 3,
) -> None:
    conn.execute(
        """
        INSERT INTO venue_command_events (
          event_id, command_id, sequence_no, event_type, state_after, occurred_at, payload_json
        )
        VALUES ('evt-3', 'cmd-1', ?, 'SUBMIT_REJECTED', 'SUBMIT_REJECTED',
                ?, '{"reason":"late_reject"}')
        """,
        (sequence_no, occurred_at),
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


def _insert_order_fact(
    conn: sqlite3.Connection,
    *,
    fact_id: str = "fact-1",
    command_id: str = "cmd-1",
    order_id: str = "order-1",
    source: str = "REST",
    state: str = "RESTING",
    remaining_size: str | None = "10.00",
    matched_size: str | None = "0",
    observed_at: str = "2026-05-15T12:00:03Z",
    local_sequence: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO venue_order_facts (
          fact_id, command_id, venue_order_id, source, observed_at, state,
          remaining_size, matched_size, local_sequence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact_id,
            command_id,
            order_id,
            source,
            observed_at,
            state,
            remaining_size,
            matched_size,
            local_sequence,
        ),
    )


def _insert_trade_fact(
    conn: sqlite3.Connection,
    *,
    fact_id: str = "trade-fact-1",
    command_id: str = "cmd-1",
    order_id: str = "order-1",
    trade_id: str = "trade-1",
    source: str = "REST",
    state: str = "MATCHED",
    observed_at: str = "2026-05-15T12:00:04Z",
    local_sequence: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
          fact_id, command_id, venue_order_id, trade_id, source, filled_size,
          fill_price, observed_at, state, local_sequence
        )
        VALUES (?, ?, ?, ?, ?, '10.00', '0.42', ?, ?, ?)
        """,
        (fact_id, command_id, order_id, trade_id, source, observed_at, state, local_sequence),
    )


def _insert_position_fill(
    conn: sqlite3.Connection,
    *,
    order_id: str = "order-1",
    order_status: str = "filled",
) -> None:
    conn.execute(
        """
        INSERT INTO position_events (
          event_id, position_id, sequence_no, event_type, command_id, order_id, env
        )
        VALUES ('pos-1:entry_order_filled', 'pos-1', 3, 'ENTRY_ORDER_FILLED',
                NULL, ?, 'live')
        """,
        (order_id,),
    )
    conn.execute(
        """
        INSERT INTO position_current (position_id, phase, order_id, order_status, shares, cost_basis_usd)
        VALUES ('pos-1', 'active', ?, ?, '10.00', '4.20')
        """,
        (order_id, order_status),
    )


def _insert_position_pending(conn: sqlite3.Connection, *, order_id: str = "order-1") -> None:
    conn.execute(
        """
        INSERT INTO position_events (
          event_id, position_id, sequence_no, event_type, command_id, order_id, env
        )
        VALUES ('pos-1:entry_order_posted', 'pos-1', 2, 'ENTRY_ORDER_POSTED',
                'cmd-1', ?, 'live')
        """,
        (order_id,),
    )
    conn.execute(
        """
        INSERT INTO position_current (position_id, phase, order_id, shares, cost_basis_usd)
        VALUES ('pos-1', 'pending_entry', ?, '0', '0')
        """,
        (order_id,),
    )


def _insert_position_voided(conn: sqlite3.Connection, *, order_id: str = "order-1") -> None:
    conn.execute(
        """
        INSERT INTO position_events (
          event_id, position_id, sequence_no, event_type, command_id, order_id, env
        )
        VALUES ('pos-1:entry_order_voided', 'pos-1', 3, 'ENTRY_ORDER_VOIDED',
                'cmd-1', ?, 'live')
        """,
        (order_id,),
    )
    conn.execute(
        """
        INSERT INTO position_current (position_id, phase, order_id, shares, cost_basis_usd)
        VALUES ('pos-1', 'voided', ?, '0', '0')
        """,
        (order_id,),
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
          source TEXT,
          observed_at TEXT,
          state TEXT,
          remaining_size TEXT,
          matched_size TEXT,
          local_sequence INTEGER
        );
        CREATE TABLE venue_trade_facts (
          fact_id TEXT PRIMARY KEY,
          command_id TEXT NOT NULL,
          venue_order_id TEXT,
          trade_id TEXT,
          source TEXT,
          filled_size TEXT,
          fill_price TEXT,
          observed_at TEXT,
          state TEXT,
          local_sequence INTEGER
        );
        CREATE TABLE position_events (
          event_id TEXT PRIMARY KEY,
          position_id TEXT NOT NULL,
          sequence_no INTEGER,
          event_type TEXT NOT NULL,
          command_id TEXT,
          order_id TEXT,
          env TEXT
        );
        CREATE TABLE position_current (
          position_id TEXT PRIMARY KEY,
          phase TEXT NOT NULL,
          order_id TEXT,
          shares TEXT,
          cost_basis_usd TEXT
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

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "PASS"
    assert result["completion_category"] == "LIVE_ORDER_SUBMITTED"
    assert result["venue_order_id"] == "order-1"


def test_resting_order_with_pending_zero_share_projection_passes(tmp_path):
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
        _insert_position_pending(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "PASS"
    assert result["completion_category"] == "LIVE_ORDER_SUBMITTED"


def test_terminal_no_fill_order_with_voided_projection_passes(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="EXPIRED")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        conn.execute(
            """
            INSERT INTO venue_command_events (
              event_id, command_id, sequence_no, event_type, state_after, occurred_at, payload_json
            )
            VALUES ('evt-3', 'cmd-1', 3, 'EXPIRED', 'EXPIRED',
                    '2026-05-15T12:00:04Z', '{"reason":"venue_terminal_no_fill"}')
            """
        )
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, state="CANCEL_CONFIRMED")
        _insert_position_voided(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "PASS"
    assert result["completion_category"] == "LIVE_ORDER_TERMINAL_NO_FILL"


def test_terminal_no_fill_order_requires_explicit_zero_matched_size(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="EXPIRED")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        conn.execute(
            """
            INSERT INTO venue_command_events (
              event_id, command_id, sequence_no, event_type, state_after, occurred_at, payload_json
            )
            VALUES ('evt-3', 'cmd-1', 3, 'EXPIRED', 'EXPIRED',
                    '2026-05-15T12:00:04Z', '{"reason":"venue_terminal_no_fill"}')
            """
        )
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, state="CANCEL_CONFIRMED", matched_size=None)
        _insert_position_voided(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert any(
        check["name"] == "latest_venue_order_fact_open"
        and check["status"] == "FAIL"
        and "latest_state=CANCEL_CONFIRMED" in check["detail"]
        for check in result["checks"]
    )


def test_pending_projection_requires_explicit_zero_shares_and_cost_basis(tmp_path):
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
        _insert_position_pending(conn)
        conn.execute(
            "UPDATE position_current SET shares = NULL WHERE position_id = 'pos-1'"
        )

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert any(
        check["name"] == "no_position_without_fill" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_voided_projection_requires_explicit_zero_shares_and_cost_basis(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="EXPIRED")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        conn.execute(
            """
            INSERT INTO venue_command_events (
              event_id, command_id, sequence_no, event_type, state_after, occurred_at, payload_json
            )
            VALUES ('evt-3', 'cmd-1', 3, 'EXPIRED', 'EXPIRED',
                    '2026-05-15T12:00:04Z', '{"reason":"venue_terminal_no_fill"}')
            """
        )
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="0")
        _insert_position_voided(conn)
        conn.execute(
            "UPDATE position_current SET cost_basis_usd = NULL WHERE position_id = 'pos-1'"
        )

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_TERMINAL_NO_FILL_MISSING_POSITION_PROOF"
    assert any(
        check["name"] == "position_current_voided_projection_present"
        and check["status"] == "FAIL"
        for check in result["checks"]
    )


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
              fact_id, command_id, venue_order_id, source, observed_at, state,
              local_sequence
            )
            VALUES ('fact-current-1', 'cmd-current-1', 'order-current-1', 'WS_USER',
                    '2026-05-15T12:00:03Z', 'RESTING', 1)
            """
        )

    with _connect_module_readonly(module, db, tmp_path) as conn:
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

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_ACKED_MISSING_ORDER_FACT"
    assert any(
        check["name"] == "latest_venue_order_fact_open" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_fake_venue_order_fact_is_not_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, source="FAKE_VENUE")

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_ACKED_MISSING_ORDER_FACT"
    assert any(
        check["name"] == "latest_venue_order_fact_open" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_order_fact_order_id_mismatch_is_not_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, order_id="other-order")

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert any(
        check["name"] == "venue_order_facts_identity_consistent" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_conflicting_order_facts_under_same_command_are_not_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, fact_id="fact-1", order_id="order-1", local_sequence=1)
        _insert_order_fact(conn, fact_id="fact-2", order_id="other-order", local_sequence=1)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert any(
        check["name"] == "venue_order_facts_identity_consistent" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_command_and_accepted_event_order_id_mismatch_is_not_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, venue_order_id="order-command")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn, order_id="order-event")
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, order_id="order-command")

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert any(
        check["name"] == "venue_order_identity_consistent" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_later_rejected_event_after_ack_is_not_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_later_submit_rejected(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert any(
        check["name"] == "latest_event_not_rejected_or_unknown" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_later_rejected_sequence_overrides_earlier_timestamp(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_later_submit_rejected(conn, occurred_at="2026-05-15T12:00:00Z", sequence_no=3)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert any(
        check["name"] == "latest_event_not_rejected_or_unknown" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_latest_terminal_order_fact_is_not_submitted_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(
            conn,
            fact_id="fact-1",
            state="RESTING",
            observed_at="2026-05-15T12:00:03Z",
            local_sequence=1,
        )
        _insert_order_fact(
            conn,
            fact_id="fact-2",
            state="CANCEL_CONFIRMED",
            observed_at="2026-05-15T12:00:03Z",
            local_sequence=2,
        )

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_ACKED_MISSING_ORDER_FACT"
    assert any(
        check["name"] == "latest_venue_order_fact_open"
        and check["status"] == "FAIL"
        and "latest_state=CANCEL_CONFIRMED" in check["detail"]
        for check in result["checks"]
    )


def test_order_fact_local_sequence_overrides_later_observed_at(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(
            conn,
            fact_id="fact-1",
            state="RESTING",
            observed_at="2026-05-15T12:00:10Z",
            local_sequence=1,
        )
        _insert_order_fact(
            conn,
            fact_id="fact-2",
            state="CANCEL_CONFIRMED",
            observed_at="2026-05-15T12:00:00Z",
            local_sequence=2,
        )

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_ACKED_MISSING_ORDER_FACT"
    assert any(
        check["name"] == "latest_venue_order_fact_open"
        and check["status"] == "FAIL"
        and "latest_state=CANCEL_CONFIRMED" in check["detail"]
        for check in result["checks"]
    )


def test_latest_fake_terminal_order_fact_supersedes_live_open_fact(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn)
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(
            conn,
            fact_id="fact-1",
            source="REST",
            state="RESTING",
            observed_at="2026-05-15T12:00:03Z",
            local_sequence=1,
        )
        _insert_order_fact(
            conn,
            fact_id="fact-2",
            source="FAKE_VENUE",
            state="CANCEL_CONFIRMED",
            observed_at="2026-05-15T12:00:03Z",
            local_sequence=2,
        )

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_ACKED_MISSING_ORDER_FACT"
    assert any(
        check["name"] == "latest_venue_order_fact_open"
        and check["status"] == "FAIL"
        and "latest_source=FAKE_VENUE" in check["detail"]
        and "latest_state=CANCEL_CONFIRMED" in check["detail"]
        for check in result["checks"]
    )


def test_fill_fact_without_position_projection_is_not_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="FILLED")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, state="MATCHED")
        _insert_trade_fact(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_FILL_MISSING_POSITION_PROOF"
    assert any(
        check["name"] == "position_fill_event_present" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_fill_completion_requires_trade_fact_and_position_projection(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="FILLED")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, state="MATCHED")
        _insert_trade_fact(conn)
        _insert_position_fill(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "PASS"
    assert result["completion_category"] == "LIVE_ORDER_FILLED"


def test_partial_fill_requires_partial_position_projection_status(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="PARTIAL")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, state="MATCHED")
        _insert_trade_fact(conn, state="CONFIRMED")
        _insert_position_fill(conn, order_status="filled")

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_FILL_MISSING_POSITION_PROOF"
    assert any(
        check["name"] == "position_current_order_status_consistent"
        and check["status"] == "FAIL"
        and "command_state=PARTIAL" in check["detail"]
        for check in result["checks"]
    )


def test_latest_terminal_order_fact_blocks_fill_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="FILLED")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(
            conn,
            fact_id="fact-1",
            state="MATCHED",
            observed_at="2026-05-15T12:00:03Z",
            local_sequence=1,
        )
        _insert_order_fact(
            conn,
            fact_id="fact-2",
            state="CANCEL_CONFIRMED",
            observed_at="2026-05-15T12:00:03Z",
            local_sequence=2,
        )
        _insert_trade_fact(conn)
        _insert_position_fill(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_FILL_MISSING_POSITION_PROOF"
    assert any(
        check["name"] == "latest_venue_order_fact_open"
        and check["status"] == "FAIL"
        and "latest_state=CANCEL_CONFIRMED" in check["detail"]
        for check in result["checks"]
    )


def test_fake_venue_trade_fact_is_not_fill_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="FILLED")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, state="MATCHED")
        _insert_trade_fact(conn, source="FAKE_VENUE")
        _insert_position_fill(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_FILL_MISSING_POSITION_PROOF"
    assert any(
        check["name"] == "venue_trade_fact_present" and check["status"] == "FAIL"
        for check in result["checks"]
    )


def test_latest_fake_trade_fact_blocks_fill_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="FILLED")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, state="MATCHED")
        _insert_trade_fact(
            conn,
            fact_id="trade-fact-1",
            source="REST",
            observed_at="2026-05-15T12:00:10Z",
            local_sequence=1,
        )
        _insert_trade_fact(
            conn,
            fact_id="trade-fact-2",
            trade_id="trade-2",
            source="FAKE_VENUE",
            observed_at="2026-05-15T12:00:00Z",
            local_sequence=2,
        )
        _insert_position_fill(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_FILL_MISSING_POSITION_PROOF"
    assert any(
        check["name"] == "venue_trade_fact_present"
        and check["status"] == "FAIL"
        and "latest_source=FAKE_VENUE" in check["detail"]
        for check in result["checks"]
    )


def test_conflicting_trade_facts_under_same_command_are_not_fill_completion(tmp_path):
    module = _load_module()
    db = tmp_path / "trades.db"
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        _schema(conn)
        _insert_command(conn, state="FILLED")
        _insert_submit_requested(conn)
        _insert_submit_acked(conn)
        _insert_pre_submit_envelope(conn)
        _insert_order_fact(conn, state="MATCHED")
        _insert_trade_fact(conn, fact_id="trade-fact-1", order_id="order-1", trade_id="trade-1")
        _insert_trade_fact(conn, fact_id="trade-fact-2", order_id="other-order", trade_id="trade-2")
        _insert_position_fill(conn)

    with _connect_module_readonly(module, db, tmp_path) as conn:
        result = module.evaluate(conn, "cmd-1")

    assert result["status"] == "FAIL"
    assert result["completion_category"] == "LIVE_ORDER_FILL_MISSING_POSITION_PROOF"
    assert any(
        check["name"] == "venue_trade_facts_identity_consistent" and check["status"] == "FAIL"
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
              event_id, command_id, sequence_no, event_type, state_after, occurred_at,
              payload_json
            )
            VALUES ('evt-2', 'cmd-1', 2, 'SUBMIT_REJECTED', 'SUBMIT_REJECTED',
                    '2026-05-15T12:00:02Z', '{"reason":"closed"}')
            """
        )

    with _connect_module_readonly(module, db, tmp_path) as conn:
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

    with _connect_module_readonly(module, db, tmp_path) as conn:
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


def test_allow_no_proof_does_not_soft_pass_failed_live_order_classification(tmp_path, capsys):
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
    world_db = tmp_path / "zeus-world.db"
    forecasts_db = tmp_path / "zeus-forecasts.db"
    for db_path in (world_db, forecasts_db):
        sqlite3.connect(db_path).close()

    exit_code = module.main(
        [
            "--trade-db",
            str(db),
            "--world-db",
            str(world_db),
            "--forecasts-db",
            str(forecasts_db),
            "--json",
            "--allow-no-proof",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert result["status"] == "FAIL"
    assert result["completion_category"] == "NO_LIVE_ORDER_PROOF"
    assert any(
        check["name"] == "command_decision_id_present" and check["status"] == "FAIL"
        for check in result["checks"]
    )
