# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: INV37_VIOLATION_LOCATE.md; PR-S4b §3; architecture/db_table_ownership.yaml
"""Antibody tests for INV-37 writer schema qualification (PR-S4b §3).

INV-37: cross-DB writes must land in zeus-world.db, not zeus_trades.db.

Pre-fix: log_shadow_signal, log_probability_trace_fact, and log_availability_fact
accepted a caller-supplied conn (which in cycle_runtime was zeus_trades.db MAIN
with world ATTACHed) and wrote to the MAIN DB — i.e., zeus_trades.db.

Fix: each writer now opens its own get_world_connection() and ignores the
passed conn. These tests verify:
  1. Rows land in zeus-world.db (COUNT > 0 after write).
  2. Rows do NOT land in the caller-supplied trade conn (COUNT == 0).
  3. Sed-break contract: if get_world_connection is NOT used internally, the
     row lands in the wrong DB and the assertion fails.
"""

from __future__ import annotations

import sqlite3
import tempfile
import os
import pathlib
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures: ephemeral world + trade DBs
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_world_db(tmp_path):
    """Create a minimal zeus-world.db with the 3 INV-37 tables."""
    db_path = tmp_path / "zeus-world.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shadow_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            decision_snapshot_id TEXT,
            p_raw_json TEXT,
            p_cal_json TEXT,
            edges_json TEXT,
            lead_hours REAL
        );
        CREATE TABLE IF NOT EXISTS probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT,
            decision_snapshot_id TEXT,
            candidate_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT,
            mode TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            entry_method TEXT,
            selected_method TEXT,
            trace_status TEXT,
            missing_reason_json TEXT,
            bin_labels_json TEXT,
            p_raw_json TEXT,
            p_cal_json TEXT,
            p_market_json TEXT,
            p_posterior_json TEXT,
            p_posterior REAL,
            alpha REAL,
            agreement TEXT,
            n_edges_found INTEGER,
            n_edges_after_fdr INTEGER,
            rejection_stage TEXT,
            availability_status TEXT,
            market_phase TEXT,
            recorded_at TEXT
        );
        CREATE TABLE IF NOT EXISTS availability_fact (
            availability_id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            failure_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            impact TEXT NOT NULL,
            details_json TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def tmp_trade_db(tmp_path):
    """Create a minimal zeus_trades.db (no INV-37 tables)."""
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS trade_decisions (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Helper: open trade conn (simulates cycle_runner.get_connection())
# ---------------------------------------------------------------------------

def _open_trade_conn(trade_db_path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(trade_db_path))
    return conn


# ---------------------------------------------------------------------------
# Test: log_shadow_signal routes to world DB
# ---------------------------------------------------------------------------

def test_shadow_signal_lands_in_world_db(tmp_world_db, tmp_trade_db):
    """Row must appear in zeus-world.db, NOT in the caller-supplied trade conn."""
    from src.state.db import log_shadow_signal

    trade_conn = _open_trade_conn(tmp_trade_db)

    with patch("src.state.db.get_world_connection") as mock_world:
        world_conn = sqlite3.connect(str(tmp_world_db))
        mock_world.return_value = world_conn

        log_shadow_signal(
            trade_conn,
            city="London",
            target_date="2026-05-18",
            timestamp="2026-05-18T12:00:00+00:00",
            decision_snapshot_id="snap-001",
            p_raw_json="[0.5, 0.5]",
            p_cal_json="[0.5, 0.5]",
            edges_json="[]",
            lead_hours=12.0,
        )
        world_conn.close()

    # Verify row landed in world DB
    world_check = sqlite3.connect(str(tmp_world_db))
    count_world = world_check.execute("SELECT COUNT(*) FROM shadow_signals").fetchone()[0]
    world_check.close()
    assert count_world == 1, f"Expected 1 row in zeus-world.db shadow_signals, got {count_world}"

    # Verify row did NOT land in trade DB
    # (trade_db has no shadow_signals table — a write to it would have raised)
    trade_check = sqlite3.connect(str(tmp_trade_db))
    tables = {r[0] for r in trade_check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    trade_check.close()
    assert "shadow_signals" not in tables, "shadow_signals must NOT exist on zeus_trades.db"

    trade_conn.close()


def test_shadow_signal_sed_break(tmp_world_db, tmp_trade_db):
    """Sed-break: if get_world_connection is bypassed, row lands in caller conn → test fails."""
    from src.state.db import log_shadow_signal
    import inspect

    src = inspect.getsource(log_shadow_signal)
    assert "get_world_connection" in src, (
        "sed-break: log_shadow_signal must call get_world_connection() internally. "
        "If this assertion fails, the INV-37 fix was reverted."
    )


# ---------------------------------------------------------------------------
# Test: log_probability_trace_fact routes to world DB
# ---------------------------------------------------------------------------

class _FakeCandidate:
    city = "Tokyo"
    target_date = "2026-05-18"
    discovery_mode = "live"
    event_id = "evt-001"
    market_phase = None


class _FakeDecision:
    decision_id = "dec-test-001"
    decision_snapshot_id = "snap-001"
    strategy_key = "settlement_capture"
    availability_status = "available"
    p_raw = None
    p_cal = None
    alpha = None
    agreement = None
    n_edges_found = 0
    n_edges_after_fdr = 0
    selected_method = None
    entry_method = None
    market_phase = None


def test_probability_trace_lands_in_world_db(tmp_world_db, tmp_trade_db):
    """Row must appear in zeus-world.db, NOT in the caller-supplied trade conn."""
    from src.state.db import log_probability_trace_fact

    trade_conn = _open_trade_conn(tmp_trade_db)

    with patch("src.state.db.get_world_connection") as mock_world:
        world_conn = sqlite3.connect(str(tmp_world_db))
        mock_world.return_value = world_conn

        result = log_probability_trace_fact(
            trade_conn,
            candidate=_FakeCandidate(),
            decision=_FakeDecision(),
            recorded_at="2026-05-18T12:00:00+00:00",
            mode="live",
        )
        world_conn.close()

    assert result.get("status") == "written", f"Expected written, got {result}"

    world_check = sqlite3.connect(str(tmp_world_db))
    count_world = world_check.execute(
        "SELECT COUNT(*) FROM probability_trace_fact"
    ).fetchone()[0]
    world_check.close()
    assert count_world == 1, f"Expected 1 row in zeus-world.db probability_trace_fact, got {count_world}"

    # Confirm no probability_trace_fact on trade DB
    trade_check = sqlite3.connect(str(tmp_trade_db))
    tables = {r[0] for r in trade_check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    trade_check.close()
    assert "probability_trace_fact" not in tables, (
        "probability_trace_fact must NOT exist on zeus_trades.db"
    )
    trade_conn.close()


def test_probability_trace_sed_break(tmp_world_db, tmp_trade_db):
    """Sed-break: log_probability_trace_fact must call get_world_connection internally."""
    from src.state.db import log_probability_trace_fact
    import inspect

    src = inspect.getsource(log_probability_trace_fact)
    assert "get_world_connection" in src, (
        "sed-break: log_probability_trace_fact must call get_world_connection() internally. "
        "If this assertion fails, the INV-37 fix was reverted."
    )


# ---------------------------------------------------------------------------
# Test: log_availability_fact routes to world DB
# ---------------------------------------------------------------------------

def test_availability_fact_lands_in_world_db(tmp_world_db, tmp_trade_db):
    """Row must appear in zeus-world.db, NOT in the caller-supplied trade conn."""
    from src.state.db import log_availability_fact

    trade_conn = _open_trade_conn(tmp_trade_db)

    with patch("src.state.db.get_world_connection") as mock_world:
        world_conn = sqlite3.connect(str(tmp_world_db))
        mock_world.return_value = world_conn

        result = log_availability_fact(
            trade_conn,
            availability_id="avail-test-001",
            scope_type="candidate",
            scope_key="London/2026-05-18",
            failure_type="TestFailure",
            started_at="2026-05-18T12:00:00+00:00",
            ended_at="2026-05-18T12:00:00+00:00",
            impact="skip",
            details={"reason": "test"},
        )
        world_conn.close()

    assert result.get("status") == "written", f"Expected written, got {result}"

    world_check = sqlite3.connect(str(tmp_world_db))
    count_world = world_check.execute(
        "SELECT COUNT(*) FROM availability_fact"
    ).fetchone()[0]
    world_check.close()
    assert count_world == 1, f"Expected 1 row in zeus-world.db availability_fact, got {count_world}"

    trade_check = sqlite3.connect(str(tmp_trade_db))
    tables = {r[0] for r in trade_check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    trade_check.close()
    assert "availability_fact" not in tables, (
        "availability_fact must NOT exist on zeus_trades.db"
    )
    trade_conn.close()


def test_availability_fact_sed_break(tmp_world_db, tmp_trade_db):
    """Sed-break: log_availability_fact must call get_world_connection internally."""
    from src.state.db import log_availability_fact
    import inspect

    src = inspect.getsource(log_availability_fact)
    assert "get_world_connection" in src, (
        "sed-break: log_availability_fact must call get_world_connection() internally. "
        "If this assertion fails, the INV-37 fix was reverted."
    )
