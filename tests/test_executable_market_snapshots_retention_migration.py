# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: coverage for scripts/migrations/202608_executable_market_snapshots_retention.py
#   (2026-08-24/25 storage_capacity DATA_DEGRADED incident retention slice 2).
"""Antibodies for the executable_market_snapshots retention migration: dry-run
counting, the venue_commands/position_events anchor-protection exception,
idempotent re-apply, the --keep-days floor guard, missing-anchor-table
degrade, and -- the one unique to this table -- that the append-only
no_delete trigger is intact both before AND after a migration run (never left
dropped by a failed or successful chunk)."""
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
    / "202608_executable_market_snapshots_retention.py"
)

SNAPSHOTS_DDL = """
CREATE TABLE executable_market_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    condition_id TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE TRIGGER no_update_executable_market_snapshots
BEFORE UPDATE ON executable_market_snapshots
BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
CREATE TRIGGER no_delete_executable_market_snapshots
BEFORE DELETE ON executable_market_snapshots
BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
"""

VENUE_COMMANDS_DDL = """
CREATE TABLE venue_commands (
    command_id TEXT PRIMARY KEY,
    snapshot_id TEXT
)
"""

POSITION_EVENTS_DDL = """
CREATE TABLE position_events (
    event_id TEXT PRIMARY KEY,
    snapshot_id TEXT
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_202608_executable_market_snapshots_retention", MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def _make_fixture_db(db_path: Path, *, with_anchors: bool = True) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SNAPSHOTS_DDL)
    if with_anchors:
        conn.execute(VENUE_COMMANDS_DDL)
        conn.execute(POSITION_EVENTS_DDL)

    old_ts = _iso(40)
    recent_ts = _iso(5)

    # snap-old-1: old, NOT referenced by any command/event -- deletable.
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap-old-1', 'cond-1', ?)",
        (old_ts,),
    )
    # snap-old-anchored: old, referenced by venue_commands.snapshot_id -- protected.
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap-old-anchored', 'cond-2', ?)",
        (old_ts,),
    )
    # snap-old-anchored-2: old, referenced by position_events.snapshot_id -- protected.
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap-old-anchored-2', 'cond-3', ?)",
        (old_ts,),
    )
    # snap-recent-1: recent, unreferenced -- survives on age alone.
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap-recent-1', 'cond-4', ?)",
        (recent_ts,),
    )
    if with_anchors:
        conn.execute("INSERT INTO venue_commands VALUES ('cmd-1', 'snap-old-anchored')")
        conn.execute("INSERT INTO position_events VALUES ('pe-1', 'snap-old-anchored-2')")
    conn.commit()
    conn.close()


def _ids(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[0] for row in conn.execute("SELECT snapshot_id FROM executable_market_snapshots")}
    finally:
        conn.close()


def _trigger_blocks_delete(db_path: Path) -> bool:
    """True iff a raw DELETE against the table is still rejected by the trigger."""
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            conn.execute("DELETE FROM executable_market_snapshots WHERE snapshot_id = 'snap-recent-1'")
            conn.rollback()
            return False
        except sqlite3.IntegrityError:
            return True
        except sqlite3.OperationalError as exc:
            # RAISE(ABORT, ...) surfaces as OperationalError with the custom message.
            return "APPEND-ONLY" in str(exc)
    finally:
        conn.close()


def test_dry_run_reports_correct_counts_without_writing(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    stats = mig.run(db_path, keep_days=30, dry_run=True)

    assert stats["total_rows_older_than_cutoff"] == 3  # the 3 old rows
    assert stats["protected_by_anchor"] == 2            # the 2 anchored rows
    assert stats["deletable"] == 1                       # snap-old-1 only
    assert _ids(db_path) == {"snap-old-1", "snap-old-anchored", "snap-old-anchored-2", "snap-recent-1"}


def test_apply_deletes_only_unprotected_old_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    stats = mig.run(db_path, keep_days=30, dry_run=False)

    assert stats["rows_deleted"] == 1
    assert _ids(db_path) == {"snap-old-anchored", "snap-old-anchored-2", "snap-recent-1"}


def test_append_only_trigger_intact_after_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    assert _trigger_blocks_delete(db_path)  # before: trigger present
    mig.run(db_path, keep_days=30, dry_run=False)
    assert _trigger_blocks_delete(db_path)  # after: trigger still present, still enforcing


def test_apply_is_idempotent_on_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    first = mig.run(db_path, keep_days=30, dry_run=False)
    second = mig.run(db_path, keep_days=30, dry_run=False)

    assert first["rows_deleted"] == 1
    assert second["rows_deleted"] == 0
    assert _ids(db_path) == {"snap-old-anchored", "snap-old-anchored-2", "snap-recent-1"}


def test_keep_days_below_minimum_refuses(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path)
    mig = _load_migration()

    with pytest.raises(ValueError, match="KEEP_DAYS_BELOW_MINIMUM"):
        mig.run(db_path, keep_days=mig.MIN_KEEP_DAYS - 1, dry_run=False)

    assert _ids(db_path) == {"snap-old-1", "snap-old-anchored", "snap-old-anchored-2", "snap-recent-1"}


def test_missing_anchor_tables_protects_nothing_extra(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _make_fixture_db(db_path, with_anchors=False)
    mig = _load_migration()

    stats = mig.run(db_path, keep_days=30, dry_run=True)

    assert stats["anchor_tables_present"] == 0
    assert stats["protected_by_anchor"] == 0
    assert stats["deletable"] == 3  # all 3 old rows, including the would-be-anchored ones
