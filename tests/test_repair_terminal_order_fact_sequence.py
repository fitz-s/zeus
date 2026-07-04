# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: Regression tests for terminal order-fact sequence repair.
# Reuse: Run when terminal ENTRY command/order fact repair or latest-fact gates change.
# Authority basis: AGENTS.md position/execution proof gates; scripts/AGENTS.md repair contract.

from __future__ import annotations

import sqlite3

from scripts import repair_terminal_order_fact_sequence as repair


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            venue_order_id TEXT,
            intent_kind TEXT,
            state TEXT,
            updated_at TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_order_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL,
            remaining_size TEXT,
            matched_size TEXT,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            venue_timestamp TEXT,
            ingested_at TEXT,
            local_sequence INTEGER NOT NULL,
            raw_payload_hash TEXT NOT NULL,
            raw_payload_json TEXT,
            UNIQUE (venue_order_id, local_sequence)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE provenance_envelope_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            payload_json TEXT,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            venue_timestamp TEXT,
            local_sequence INTEGER NOT NULL
        )
        """
    )
    return conn


def _insert_command(
    conn: sqlite3.Connection,
    *,
    command_id: str = "cmd-1",
    venue_order_id: str = "order-1",
    state: str = "CANCELLED",
) -> None:
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, venue_order_id, intent_kind, state, updated_at, created_at
        ) VALUES (?, ?, 'ENTRY', ?, '2026-07-02T13:47:23+00:00', '2026-07-02T13:41:20+00:00')
        """,
        (command_id, venue_order_id, state),
    )


def _insert_fact(
    conn: sqlite3.Connection,
    *,
    state: str,
    seq: int,
    remaining_size: str | None,
    matched_size: str | None,
    command_id: str = "cmd-1",
    venue_order_id: str = "order-1",
) -> None:
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, venue_timestamp, ingested_at, local_sequence,
            raw_payload_hash, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, 'WS_USER', ?, ?, ?, ?, ?, '{}')
        """,
        (
            venue_order_id,
            command_id,
            state,
            remaining_size,
            matched_size,
            f"2026-07-02T13:4{seq}:00+00:00",
            f"2026-07-02T13:4{seq}:00+00:00",
            f"2026-07-02T13:4{seq}:01+00:00",
            seq,
            str(seq) * 64,
        ),
    )


def test_find_candidates_detects_stale_latest_partial_after_terminal_fact() -> None:
    conn = _conn()
    _insert_command(conn)
    _insert_fact(conn, state="CANCEL_CONFIRMED", seq=4, remaining_size="15.07", matched_size="10")
    _insert_fact(conn, state="PARTIALLY_MATCHED", seq=5, remaining_size="15.07", matched_size="10")

    candidates = repair.find_candidates(conn)

    assert len(candidates) == 1
    assert candidates[0].command_id == "cmd-1"
    assert candidates[0].latest_state == "PARTIALLY_MATCHED"
    assert candidates[0].terminal_state == "CANCEL_CONFIRMED"
    assert candidates[0].terminal_remaining_size == "15.07"
    assert candidates[0].terminal_matched_size == "10"


def test_apply_candidate_appends_terminal_fact_and_is_idempotent() -> None:
    conn = _conn()
    _insert_command(conn)
    _insert_fact(conn, state="CANCEL_CONFIRMED", seq=4, remaining_size="15.07", matched_size="10")
    _insert_fact(conn, state="PARTIALLY_MATCHED", seq=5, remaining_size="15.07", matched_size="10")
    candidate = repair.find_candidates(conn)[0]

    appended_id = repair.apply_candidate(
        conn,
        candidate,
        observed_at="2026-07-04T04:40:00+00:00",
    )
    after = repair.find_candidates(conn)
    latest = conn.execute(
        """
        SELECT fact_id, state, remaining_size, matched_size, source, raw_payload_json
          FROM venue_order_facts
         WHERE venue_order_id = 'order-1'
         ORDER BY local_sequence DESC
         LIMIT 1
        """
    ).fetchone()

    assert after == []
    assert latest["fact_id"] == appended_id
    assert latest["state"] == "CANCEL_CONFIRMED"
    assert latest["remaining_size"] == "15.07"
    assert latest["matched_size"] == "10"
    assert latest["source"] == "OPERATOR"
    assert "terminal_order_fact_sequence_repair" in latest["raw_payload_json"]


def test_find_candidates_ignores_terminal_command_without_terminal_proof() -> None:
    conn = _conn()
    _insert_command(conn)
    _insert_fact(conn, state="PARTIALLY_MATCHED", seq=5, remaining_size="15.07", matched_size="10")

    assert repair.find_candidates(conn) == []


def test_find_candidates_ignores_nonterminal_command() -> None:
    conn = _conn()
    _insert_command(conn, state="PARTIAL")
    _insert_fact(conn, state="CANCEL_CONFIRMED", seq=4, remaining_size="15.07", matched_size="10")
    _insert_fact(conn, state="PARTIALLY_MATCHED", seq=5, remaining_size="15.07", matched_size="10")

    assert repair.find_candidates(conn) == []
