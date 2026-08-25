# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: coverage for scripts/migrations/202608_decision_log_retention.py
#   (2026-08-24/25 storage_capacity DATA_DEGRADED incident retention slice).
"""Antibodies for the decision_log retention migration: dry-run counting, the
tier0-anchor protection exception, idempotent re-apply, and the consumer-window
--keep-days floor guard."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "migrations"
    / "202608_decision_log_retention.py"
)

DECISION_LOG_DDL = """
CREATE TABLE decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    artifact_json TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    env TEXT NOT NULL DEFAULT 'live'
)
"""
DECISION_LOG_TS_INDEX = "CREATE INDEX idx_decision_log_ts ON decision_log(timestamp)"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_202608_decision_log_retention", MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso(days_ago: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%dT%H:%M:%S")


def _make_fixture_db(db_path: Path, *, with_tier0_table: bool = True) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(DECISION_LOG_DDL)
    conn.execute(DECISION_LOG_TS_INDEX)
    if with_tier0_table:
        from src.state.schema.tier0_candidate_set_provenance_schema import (
            ensure_table,
        )

        ensure_table(conn)

    old_ts = _iso(40)
    recent_ts = _iso(5)

    # Row 1: old, ordinary settlement mode -- deletable.
    conn.execute(
        "INSERT INTO decision_log (id, mode, started_at, completed_at, artifact_json, timestamp) "
        "VALUES (1, 'settlement', ?, ?, ?, ?)",
        (old_ts, old_ts, json.dumps({"summary": {}}), old_ts),
    )
    # Row 2: old, global-auction mode, ANCHORED to a tier0 candidate-set row -- protected.
    conn.execute(
        "INSERT INTO decision_log (id, mode, started_at, completed_at, artifact_json, timestamp) "
        "VALUES (2, 'global_single_order_auction', ?, ?, ?, ?)",
        (
            old_ts,
            old_ts,
            json.dumps({"summary": {"selection_epoch_identity": "epoch-protected"}}),
            old_ts,
        ),
    )
    # Row 3: old, global-auction mode, NOT anchored to any tier0 row -- deletable.
    conn.execute(
        "INSERT INTO decision_log (id, mode, started_at, completed_at, artifact_json, timestamp) "
        "VALUES (3, 'global_single_order_auction', ?, ?, ?, ?)",
        (
            old_ts,
            old_ts,
            json.dumps({"summary": {"selection_epoch_identity": "epoch-unprotected"}}),
            old_ts,
        ),
    )
    # Row 4: recent, ordinary mode -- survives on age alone (not older than cutoff).
    conn.execute(
        "INSERT INTO decision_log (id, mode, started_at, completed_at, artifact_json, timestamp) "
        "VALUES (4, 'settlement', ?, ?, ?, ?)",
        (recent_ts, recent_ts, json.dumps({"summary": {}}), recent_ts),
    )
    if with_tier0_table:
        conn.execute(
            """
            INSERT INTO tier0_candidate_set_provenance (
                selection_epoch_identity, decision_at_utc, city_date_group_id, city,
                target_date, candidate_id, family_key, bin_id, side, token_id, action,
                eligible, selected, market_key, created_at
            ) VALUES (
                'epoch-protected', ?, 'grp-1', 'Denver', '2026-07-01', 'cand-1',
                'family-1', 'bin-1', 'YES', 'token-1', 'BUY', 1, 1, 'market-1', ?
            )
            """,
            (old_ts, old_ts),
        )
    conn.commit()
    conn.close()


def _ids(db_path: Path) -> set[int]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[0] for row in conn.execute("SELECT id FROM decision_log")}
    finally:
        conn.close()


def test_dry_run_reports_correct_counts_without_writing(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    stats = mig.run(db_path, keep_days=30, dry_run=True)

    assert stats["total_rows_older_than_cutoff"] == 3  # rows 1, 2, 3
    assert stats["protected_by_tier0_anchor"] == 1      # row 2
    assert stats["deletable"] == 2                       # rows 1, 3
    # Dry-run must never write.
    assert _ids(db_path) == {1, 2, 3, 4}


def test_apply_deletes_only_unprotected_old_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    stats = mig.run(db_path, keep_days=30, dry_run=False)

    assert stats["rows_deleted"] == 2
    # Row 2 (tier0-anchored) and row 4 (recent) survive; rows 1 and 3 are gone.
    assert _ids(db_path) == {2, 4}


def test_apply_is_idempotent_on_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    first = mig.run(db_path, keep_days=30, dry_run=False)
    second = mig.run(db_path, keep_days=30, dry_run=False)

    assert first["rows_deleted"] == 2
    assert second["rows_deleted"] == 0
    assert _ids(db_path) == {2, 4}


def test_keep_days_below_minimum_refuses(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    with pytest.raises(ValueError, match="KEEP_DAYS_BELOW_MINIMUM"):
        mig.run(db_path, keep_days=mig.MIN_KEEP_DAYS - 1, dry_run=False)

    # Refusal must not touch the DB at all.
    assert _ids(db_path) == {1, 2, 3, 4}


def test_missing_tier0_table_protects_nothing_extra(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path, with_tier0_table=False)
    mig = _load_migration()

    stats = mig.run(db_path, keep_days=30, dry_run=True)

    assert stats["tier0_table_present"] == 0
    # Without the anchor table, all 3 old rows (including the would-be-protected
    # global-auction row) are deletable -- matches the literal spec: the
    # exception protects nothing when its anchor table is absent.
    assert stats["protected_by_tier0_anchor"] == 0
    assert stats["deletable"] == 3
