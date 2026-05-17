# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: F7 audit / FIX_SEV1_BUNDLE.md §F7
"""Antibody tests: execution_fact.command_id linkage invariants."""

import sqlite3
import pytest


@pytest.fixture
def trade_conn(tmp_path):
    """In-memory DB with execution_fact + venue_commands schema."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS execution_fact (
            intent_id TEXT PRIMARY KEY,
            position_id TEXT,
            decision_id TEXT,
            order_role TEXT NOT NULL,
            strategy_key TEXT,
            posted_at TEXT,
            filled_at TEXT,
            voided_at TEXT,
            submitted_price REAL,
            fill_price REAL,
            shares REAL,
            fill_quality REAL,
            latency_seconds REAL,
            venue_status TEXT,
            terminal_exec_status TEXT,
            command_id TEXT
        );
        CREATE TABLE IF NOT EXISTS venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            decision_id TEXT,
            idempotency_key TEXT,
            intent_kind TEXT,
            market_id TEXT,
            token_id TEXT,
            side TEXT,
            size REAL,
            price REAL,
            state TEXT,
            created_at TEXT,
            snapshot_id TEXT,
            envelope_id TEXT,
            snapshot_checked_at TEXT,
            expected_min_tick_size TEXT,
            expected_min_order_size TEXT,
            expected_neg_risk INTEGER
        );
        """
    )
    yield conn
    conn.close()


def test_execution_fact_command_id_linkable(trade_conn):
    """For rows where command_id is not NULL, it must point to a real venue_commands row."""
    # Insert a venue_commands row
    trade_conn.execute(
        "INSERT INTO venue_commands (command_id, position_id, state, created_at)"
        " VALUES ('cmd-abc123', 'pos-1', 'ACKED', '2026-05-17T00:00:00Z')"
    )
    # Insert execution_fact row with valid FK
    trade_conn.execute(
        "INSERT INTO execution_fact (intent_id, position_id, order_role, command_id)"
        " VALUES ('pos-1:entry', 'pos-1', 'entry', 'cmd-abc123')"
    )
    # Insert execution_fact row with NULL command_id (pending-fill-authority path)
    trade_conn.execute(
        "INSERT INTO execution_fact (intent_id, position_id, order_role, command_id)"
        " VALUES ('pos-2:exit', 'pos-2', 'exit', NULL)"
    )
    trade_conn.commit()

    orphans = trade_conn.execute(
        """
        SELECT COUNT(*) FROM execution_fact ef
        WHERE command_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM venue_commands vc WHERE vc.command_id = ef.command_id
        )
        """
    ).fetchone()[0]
    assert orphans == 0


def test_execution_fact_orphan_command_id_detected(trade_conn):
    """Verify the antibody catches a dangling command_id (no matching venue_commands row)."""
    # Insert execution_fact row with a command_id that does NOT exist in venue_commands
    trade_conn.execute(
        "INSERT INTO execution_fact (intent_id, position_id, order_role, command_id)"
        " VALUES ('pos-3:entry', 'pos-3', 'entry', 'cmd-nonexistent')"
    )
    trade_conn.commit()

    orphans = trade_conn.execute(
        """
        SELECT COUNT(*) FROM execution_fact ef
        WHERE command_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM venue_commands vc WHERE vc.command_id = ef.command_id
        )
        """
    ).fetchone()[0]
    assert orphans == 1, "Antibody must catch orphaned command_id references"


def test_log_execution_fact_command_id_roundtrip(trade_conn):
    """log_execution_fact stores and retrieves command_id correctly."""
    from src.state.db import log_execution_fact

    # Apply migration to in-memory schema (column already present via fixture)
    result = log_execution_fact(
        trade_conn,
        intent_id="pos-10:entry",
        position_id="pos-10",
        order_role="entry",
        command_id="cmd-roundtrip-001",
    )
    assert result["status"] == "written"
    trade_conn.commit()

    row = trade_conn.execute(
        "SELECT command_id FROM execution_fact WHERE intent_id = 'pos-10:entry'"
    ).fetchone()
    assert row is not None
    assert row["command_id"] == "cmd-roundtrip-001"


def test_log_execution_fact_command_id_none_roundtrip(trade_conn):
    """log_execution_fact with command_id=None stores NULL (pending-fill-authority path)."""
    from src.state.db import log_execution_fact

    log_execution_fact(
        trade_conn,
        intent_id="pos-11:exit",
        position_id="pos-11",
        order_role="exit",
        command_id=None,
    )
    trade_conn.commit()

    row = trade_conn.execute(
        "SELECT command_id FROM execution_fact WHERE intent_id = 'pos-11:exit'"
    ).fetchone()
    assert row is not None
    assert row["command_id"] is None


def test_log_execution_fact_command_id_preserved_on_update(trade_conn):
    """COALESCE rule: once set, command_id is not overwritten by NULL update."""
    from src.state.db import log_execution_fact

    # First write — sets command_id
    log_execution_fact(
        trade_conn,
        intent_id="pos-12:entry",
        position_id="pos-12",
        order_role="entry",
        command_id="cmd-preserve-me",
    )
    trade_conn.commit()

    # Second write — command_id=None should NOT overwrite existing value
    log_execution_fact(
        trade_conn,
        intent_id="pos-12:entry",
        position_id="pos-12",
        order_role="entry",
        command_id=None,
        terminal_exec_status="filled",
    )
    trade_conn.commit()

    row = trade_conn.execute(
        "SELECT command_id FROM execution_fact WHERE intent_id = 'pos-12:entry'"
    ).fetchone()
    assert row["command_id"] == "cmd-preserve-me", (
        "COALESCE rule: command_id must not be overwritten by NULL update"
    )
