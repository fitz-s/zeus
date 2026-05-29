# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL replay redesign §7 / PR E
"""Guard that the 5 new replay/backtest tables are created correctly by
init_backtest_schema, that the operation is idempotent, and that these
tables are NOT in the WORLD/TRADE fingerprint registry (live-restart-safe).
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.state.db import get_connection, init_backtest_schema
from src.state.table_registry import DBIdentity, tables_for


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NEW_TABLES = {
    "replay_runs",
    "replay_subjects",
    "forecast_probability_vectors",
    "settlement_resolution_truth",
    "replay_skill_results",
}


def _fresh_backtest_conn(tmp_path: Path) -> sqlite3.Connection:
    """Return a plain sqlite3 connection to a fresh temp backtest DB."""
    db_path = tmp_path / "zeus_backtest.db"
    return sqlite3.connect(str(db_path))


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_new_tables_created(tmp_path):
    """init_backtest_schema creates all 5 new replay tables on a fresh DB."""
    conn = _fresh_backtest_conn(tmp_path)
    init_backtest_schema(conn)
    existing = _table_names(conn)
    missing = NEW_TABLES - existing
    assert not missing, f"Tables not created: {missing}"
    conn.close()


def test_idempotent(tmp_path):
    """Calling init_backtest_schema twice does not raise."""
    conn = _fresh_backtest_conn(tmp_path)
    init_backtest_schema(conn)
    init_backtest_schema(conn)  # must not error
    conn.close()


def test_replay_subjects_promotion_authority_default(tmp_path):
    """INSERT into replay_subjects with promotion_authority omitted defaults to 0."""
    conn = _fresh_backtest_conn(tmp_path)
    init_backtest_schema(conn)

    conn.execute(
        """
        INSERT INTO replay_subjects (replay_subject_id, run_id, city, target_local_date)
        VALUES ('subj-001', 'run-001', 'NYC', '2026-05-29')
        """
    )
    conn.commit()

    row = conn.execute(
        "SELECT promotion_authority FROM replay_subjects WHERE replay_subject_id='subj-001'"
    ).fetchone()
    assert row is not None
    assert row[0] == 0, f"Expected promotion_authority=0, got {row[0]}"
    conn.close()


def test_replay_skill_results_round_trip_promotion_authority(tmp_path):
    """INSERT + SELECT round-trip on replay_skill_results; promotion_authority==0."""
    conn = _fresh_backtest_conn(tmp_path)
    init_backtest_schema(conn)

    conn.execute(
        """
        INSERT INTO replay_skill_results (
            skill_result_id, run_id, replay_subject_id,
            categorical_log_loss, ranked_probability_score
        ) VALUES ('skill-001', 'run-001', 'subj-001', 0.693, 0.25)
        """
    )
    conn.commit()

    row = conn.execute(
        """
        SELECT skill_result_id, categorical_log_loss, ranked_probability_score,
               promotion_authority
        FROM replay_skill_results
        WHERE skill_result_id='skill-001'
        """
    ).fetchone()
    assert row is not None
    assert row[0] == "skill-001"
    assert abs(row[1] - 0.693) < 1e-9
    assert abs(row[2] - 0.25) < 1e-9
    assert row[3] == 0, f"Expected promotion_authority=0, got {row[3]}"
    conn.close()


def test_new_tables_not_in_world_or_trade_registry():
    """Replay tables must NOT appear in WORLD or TRADE registry sets.

    This confirms live-restart-safety: assert_db_matches_registry(WORLD)
    and assert_db_matches_registry(TRADE) will not check for or fail on
    any of the new replay tables.
    """
    world_tables = tables_for(DBIdentity.WORLD)
    trade_tables = tables_for(DBIdentity.TRADE)
    live_tables = world_tables | trade_tables

    overlap = NEW_TABLES & live_tables
    assert not overlap, (
        f"Replay tables found in WORLD/TRADE registry (live-restart UNSAFE): {overlap}"
    )
