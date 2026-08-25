# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: coverage for scripts/migrations/202608_execution_feasibility_evidence_retention.py
#   (2026-08-24/25 storage_capacity DATA_DEGRADED incident retention slice 2).
"""Antibodies for the execution_feasibility_evidence retention migration: dry-run
counting, apply-deletes-only-old-rows, idempotent re-apply, and the
--keep-days floor guard."""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "migrations"
    / "202608_execution_feasibility_evidence_retention.py"
)

TABLE_DDL = """
CREATE TABLE execution_feasibility_evidence (
    evidence_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_label TEXT NOT NULL,
    direction TEXT NOT NULL,
    quote_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_202608_execution_feasibility_evidence_retention", MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def _make_fixture_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(TABLE_DDL)
    old_ts = _iso(40)
    recent_ts = _iso(5)
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES "
        "('ev-old-1', 'evt-1', 'cond-1', 'tok-1', 'YES', 'buy_yes', ?, ?, 1)",
        (old_ts, old_ts),
    )
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES "
        "('ev-old-2', 'evt-2', 'cond-2', 'tok-2', 'NO', 'buy_no', ?, ?, 1)",
        (old_ts, old_ts),
    )
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES "
        "('ev-recent-1', 'evt-3', 'cond-3', 'tok-3', 'YES', 'sell_yes', ?, ?, 1)",
        (recent_ts, recent_ts),
    )
    conn.commit()
    conn.close()


def _ids(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[0] for row in conn.execute("SELECT evidence_id FROM execution_feasibility_evidence")}
    finally:
        conn.close()


def test_dry_run_reports_correct_counts_without_writing(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    stats = mig.run(db_path, keep_days=30, dry_run=True)

    assert stats["deletable"] == 2  # the two old rows
    assert _ids(db_path) == {"ev-old-1", "ev-old-2", "ev-recent-1"}


def test_apply_deletes_only_old_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    stats = mig.run(db_path, keep_days=30, dry_run=False)

    assert stats["rows_deleted"] == 2
    assert _ids(db_path) == {"ev-recent-1"}


def test_apply_is_idempotent_on_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    first = mig.run(db_path, keep_days=30, dry_run=False)
    second = mig.run(db_path, keep_days=30, dry_run=False)

    assert first["rows_deleted"] == 2
    assert second["rows_deleted"] == 0
    assert _ids(db_path) == {"ev-recent-1"}


def test_keep_days_below_minimum_refuses(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    with pytest.raises(ValueError, match="KEEP_DAYS_BELOW_MINIMUM"):
        mig.run(db_path, keep_days=mig.MIN_KEEP_DAYS - 1, dry_run=False)

    assert _ids(db_path) == {"ev-old-1", "ev-old-2", "ev-recent-1"}


def test_apply_creates_cutoff_index(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    mig.run(db_path, keep_days=30, dry_run=False)

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (mig._CUTOFF_INDEX_NAME,),
        ).fetchone()
        assert row is not None
    finally:
        conn.close()
