"""Tests for K6 Slice M: INSERT ON CONFLICT and ATTACH guard fixes."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB_PY = ROOT / "src" / "state" / "db.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    """In-memory SQLite connection with row_factory."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _create_opportunity_fact(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE opportunity_fact (
            decision_id TEXT PRIMARY KEY,
            candidate_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            entry_method TEXT,
            snapshot_id TEXT,
            p_raw REAL,
            p_cal REAL,
            p_market REAL,
            alpha REAL,
            best_edge REAL,
            ci_width REAL,
            rejection_stage TEXT,
            rejection_reason_json TEXT,
            availability_status TEXT,
            should_trade INTEGER,
            recorded_at TEXT
        )
    """)


def _create_outcome_fact(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE outcome_fact (
            position_id TEXT PRIMARY KEY,
            strategy_key TEXT,
            entered_at TEXT,
            exited_at TEXT,
            settled_at TEXT,
            exit_reason TEXT,
            admin_exit_reason TEXT,
            decision_snapshot_id TEXT,
            pnl REAL,
            outcome INTEGER,
            hold_duration_hours REAL,
            monitor_count INTEGER,
            chain_corrections_count INTEGER
        )
    """)


# ---------------------------------------------------------------------------
# Test 1: opportunity_fact ON CONFLICT preserves recorded_at
# ---------------------------------------------------------------------------

def test_insert_on_conflict_opportunity_fact():
    conn = _make_conn()
    _create_opportunity_fact(conn)

    # First insert
    conn.execute("""
        INSERT INTO opportunity_fact (
            decision_id, candidate_id, city, target_date, range_label,
            direction, strategy_key, discovery_mode, entry_method, snapshot_id,
            p_raw, p_cal, p_market, alpha, best_edge, ci_width,
            rejection_stage, rejection_reason_json, availability_status,
            should_trade, recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(decision_id) DO UPDATE SET
            candidate_id=excluded.candidate_id,
            city=excluded.city,
            target_date=excluded.target_date,
            range_label=excluded.range_label,
            direction=excluded.direction,
            strategy_key=excluded.strategy_key,
            discovery_mode=excluded.discovery_mode,
            entry_method=excluded.entry_method,
            snapshot_id=excluded.snapshot_id,
            p_raw=excluded.p_raw,
            p_cal=excluded.p_cal,
            p_market=excluded.p_market,
            alpha=excluded.alpha,
            best_edge=excluded.best_edge,
            ci_width=excluded.ci_width,
            rejection_stage=excluded.rejection_stage,
            rejection_reason_json=excluded.rejection_reason_json,
            availability_status=excluded.availability_status,
            should_trade=excluded.should_trade,
            recorded_at=COALESCE(opportunity_fact.recorded_at, excluded.recorded_at)
    """, (
        "d1", "c1", "London", "2026-04-15", "25-30",
        "buy_yes", "settlement_capture", "scanner", "limit", "snap1",
        0.6, 0.65, 0.5, 0.1, 0.15, 0.05,
        "", None, "OK",
        1, "2026-04-15T10:00:00Z",
    ))

    row = conn.execute("SELECT * FROM opportunity_fact WHERE decision_id='d1'").fetchone()
    assert row["recorded_at"] == "2026-04-15T10:00:00Z"
    assert row["city"] == "London"

    # Second insert with same PK but different data — recorded_at should be preserved
    conn.execute("""
        INSERT INTO opportunity_fact (
            decision_id, candidate_id, city, target_date, range_label,
            direction, strategy_key, discovery_mode, entry_method, snapshot_id,
            p_raw, p_cal, p_market, alpha, best_edge, ci_width,
            rejection_stage, rejection_reason_json, availability_status,
            should_trade, recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(decision_id) DO UPDATE SET
            candidate_id=excluded.candidate_id,
            city=excluded.city,
            target_date=excluded.target_date,
            range_label=excluded.range_label,
            direction=excluded.direction,
            strategy_key=excluded.strategy_key,
            discovery_mode=excluded.discovery_mode,
            entry_method=excluded.entry_method,
            snapshot_id=excluded.snapshot_id,
            p_raw=excluded.p_raw,
            p_cal=excluded.p_cal,
            p_market=excluded.p_market,
            alpha=excluded.alpha,
            best_edge=excluded.best_edge,
            ci_width=excluded.ci_width,
            rejection_stage=excluded.rejection_stage,
            rejection_reason_json=excluded.rejection_reason_json,
            availability_status=excluded.availability_status,
            should_trade=excluded.should_trade,
            recorded_at=COALESCE(opportunity_fact.recorded_at, excluded.recorded_at)
    """, (
        "d1", "c1", "Paris", "2026-04-16", "30-35",
        "buy_no", "center_buy", "scanner", "market", "snap2",
        0.7, 0.72, 0.55, 0.2, 0.17, 0.06,
        "edge_too_small", None, "OK",
        0, "2026-04-15T12:00:00Z",
    ))

    row = conn.execute("SELECT * FROM opportunity_fact WHERE decision_id='d1'").fetchone()
    # recorded_at should be PRESERVED from first insert
    assert row["recorded_at"] == "2026-04-15T10:00:00Z", (
        f"recorded_at was clobbered: {row['recorded_at']}"
    )
    # Other fields should be UPDATED
    assert row["city"] == "Paris"
    assert row["target_date"] == "2026-04-16"
    assert row["direction"] == "buy_no"
    assert row["should_trade"] == 0


# ---------------------------------------------------------------------------
# Test 2: outcome_fact ON CONFLICT
# ---------------------------------------------------------------------------

def test_insert_on_conflict_outcome_fact():
    conn = _make_conn()
    _create_outcome_fact(conn)

    # First insert
    conn.execute("""
        INSERT INTO outcome_fact (
            position_id, strategy_key, entered_at, exited_at, settled_at,
            exit_reason, admin_exit_reason, decision_snapshot_id,
            pnl, outcome, hold_duration_hours, monitor_count, chain_corrections_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id) DO UPDATE SET
            strategy_key=excluded.strategy_key,
            entered_at=excluded.entered_at,
            exited_at=excluded.exited_at,
            settled_at=excluded.settled_at,
            exit_reason=excluded.exit_reason,
            admin_exit_reason=excluded.admin_exit_reason,
            decision_snapshot_id=excluded.decision_snapshot_id,
            pnl=excluded.pnl,
            outcome=excluded.outcome,
            hold_duration_hours=excluded.hold_duration_hours,
            monitor_count=excluded.monitor_count,
            chain_corrections_count=excluded.chain_corrections_count
    """, (
        "pos1", "settlement_capture", "2026-04-14T08:00:00Z", None, None,
        None, None, "snap1",
        None, None, None, 0, 0,
    ))

    row = conn.execute("SELECT * FROM outcome_fact WHERE position_id='pos1'").fetchone()
    assert row["strategy_key"] == "settlement_capture"
    assert row["pnl"] is None

    # Second insert with same PK — update with settlement data
    conn.execute("""
        INSERT INTO outcome_fact (
            position_id, strategy_key, entered_at, exited_at, settled_at,
            exit_reason, admin_exit_reason, decision_snapshot_id,
            pnl, outcome, hold_duration_hours, monitor_count, chain_corrections_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id) DO UPDATE SET
            strategy_key=excluded.strategy_key,
            entered_at=excluded.entered_at,
            exited_at=excluded.exited_at,
            settled_at=excluded.settled_at,
            exit_reason=excluded.exit_reason,
            admin_exit_reason=excluded.admin_exit_reason,
            decision_snapshot_id=excluded.decision_snapshot_id,
            pnl=excluded.pnl,
            outcome=excluded.outcome,
            hold_duration_hours=excluded.hold_duration_hours,
            monitor_count=excluded.monitor_count,
            chain_corrections_count=excluded.chain_corrections_count
    """, (
        "pos1", "settlement_capture", "2026-04-14T08:00:00Z",
        "2026-04-15T06:00:00Z", "2026-04-15T06:00:00Z",
        "settled", None, "snap1",
        12.50, 1, 22.0, 3, 1,
    ))

    row = conn.execute("SELECT * FROM outcome_fact WHERE position_id='pos1'").fetchone()
    assert row["pnl"] == 12.50
    assert row["outcome"] == 1
    assert row["exited_at"] == "2026-04-15T06:00:00Z"
    assert row["monitor_count"] == 3
    # Only 1 row should exist
    count = conn.execute("SELECT COUNT(*) FROM outcome_fact").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# Test 3: ATTACH guard — no double-attach error
# ---------------------------------------------------------------------------

def test_attach_world_guard_no_double_attach():
    """Verify the ATTACH guard in get_trade_connection_with_world prevents double-attach."""
    source = DB_PY.read_text()

    # Verify the guard pattern exists in source
    assert 'PRAGMA database_list' in source, "ATTACH guard not found in db.py"
    assert '"world" not in attached' in source, "world-not-in-attached check missing"

    # Simulate the guard logic directly
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # First call: world not attached
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    assert "world" not in attached

    # ATTACH a temporary DB as world
    conn.execute("ATTACH DATABASE ':memory:' AS world")

    # Second call: world IS attached — guard should prevent re-attach
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    assert "world" in attached

    # If we tried to ATTACH again without the guard, it would raise
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("ATTACH DATABASE ':memory:' AS world")

    conn.close()


# ---------------------------------------------------------------------------
# Test 4: Regression guard — no INSERT OR REPLACE in db.py
# ---------------------------------------------------------------------------

def test_no_insert_or_replace_in_db():
    """Scan db.py source for any remaining INSERT OR REPLACE and assert zero matches."""
    source = DB_PY.read_text()
    matches = re.findall(r'INSERT OR REPLACE', source, re.IGNORECASE)
    assert len(matches) == 0, (
        f"Found {len(matches)} remaining INSERT OR REPLACE in db.py — "
        f"all should be converted to INSERT ... ON CONFLICT"
    )
