"""B070 migration: convert control_overrides from mutable table to VIEW over append-only history.

BEFORE: control_overrides is a mutable SQLite table with PRIMARY KEY (override_id).
        Writes use INSERT OR REPLACE (overwrite) and UPDATE (expire). History is lost.

AFTER:  control_overrides_history is an append-only log. control_overrides is a VIEW
        that projects the latest recorded_at per override_id. Every upsert/expire
        is a new history row. Audit trail preserved.

WORKTREE MODE (fresh DB): init_schema already creates both shapes via
apply_architecture_kernel_schema. This migration is a no-op on fresh DBs.

PRODUCTION MODE (existing DB with legacy control_overrides table): the operator
runs this script ONCE during a quiet window (no in-flight control_plane commands):
  1. Create control_overrides_history (if not exists)
  2. Copy every row from legacy control_overrides into history with
     operation='migrated' and recorded_at=issued_at
  3. For rows where effective_until is non-null and in the past, also insert
     a second history row with operation='expire' and recorded_at=effective_until
  4. DROP TABLE control_overrides (legacy)
  5. CREATE VIEW control_overrides on top of history

The script checks for ZEUS_DESTRUCTIVE_CONFIRMED=1 before the DROP step.
Without that env var, it runs in dry-run mode and only reports what would happen.

Usage:
    python scripts/migrate_b070_control_overrides_to_history.py           # dry run
    ZEUS_DESTRUCTIVE_CONFIRMED=1 \\
        python scripts/migrate_b070_control_overrides_to_history.py --apply
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection  # noqa: E402


def _object_exists(conn: sqlite3.Connection, name: str, kind: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?",
        (kind, name),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])


HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS control_overrides_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    override_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('strategy', 'global', 'position')),
    target_key TEXT NOT NULL,
    action_type TEXT NOT NULL,
    value TEXT NOT NULL,
    issued_by TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    effective_until TEXT,
    reason TEXT NOT NULL,
    precedence INTEGER NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('upsert', 'expire', 'migrated', 'revoke')),
    recorded_at TEXT NOT NULL
)
"""

HISTORY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_control_overrides_history_id_time
    ON control_overrides_history(override_id, history_id DESC)
"""

NO_UPDATE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS control_overrides_history_no_update
BEFORE UPDATE ON control_overrides_history
BEGIN
    SELECT RAISE(ABORT, 'control_overrides_history is append-only');
END
"""

NO_DELETE_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS control_overrides_history_no_delete
BEFORE DELETE ON control_overrides_history
BEGIN
    SELECT RAISE(ABORT, 'control_overrides_history is append-only');
END
"""

VIEW_DDL = """
CREATE VIEW IF NOT EXISTS control_overrides AS
SELECT override_id, target_type, target_key, action_type, value,
       issued_by, issued_at, effective_until, reason, precedence
FROM control_overrides_history h1
WHERE history_id = (
    SELECT MAX(history_id)
    FROM control_overrides_history h2
    WHERE h2.override_id = h1.override_id
)
"""


def run_migration(
    conn: sqlite3.Connection,
    *,
    apply: bool,
    destructive_confirmed: bool,
) -> dict:
    summary: dict = {
        "steps": [],
        "legacy_table_present": False,
        "legacy_row_count": 0,
        "history_table_present": False,
        "history_row_count_before": 0,
        "view_present_before": False,
        "migrated_upsert_rows": 0,
        "migrated_expire_rows": 0,
        "dropped_legacy": False,
        "created_view": False,
        "apply": apply,
        "destructive_confirmed": destructive_confirmed,
    }

    # Take a write lock for the duration of the migration so concurrent
    # riskguard/control_plane writers cannot interleave. BEGIN IMMEDIATE is
    # a no-op if autocommit is already off, but is safe to attempt.
    if apply:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass  # already in a transaction

    legacy_is_table = _object_exists(conn, "control_overrides", "table")
    view_present = _object_exists(conn, "control_overrides", "view")
    history_present = _object_exists(conn, "control_overrides_history", "table")

    summary["legacy_table_present"] = legacy_is_table
    summary["view_present_before"] = view_present
    summary["history_table_present"] = history_present
    if history_present:
        summary["history_row_count_before"] = _count(conn, "control_overrides_history")
    if legacy_is_table:
        summary["legacy_row_count"] = _count(conn, "control_overrides")

    if view_present and not legacy_is_table:
        summary["steps"].append("noop: VIEW already in place, no legacy table")
        return summary

    # Step 1: create history table + triggers + index (idempotent)
    summary["steps"].append("ensure control_overrides_history + triggers + index")
    if apply:
        conn.execute(HISTORY_DDL)
        conn.execute(HISTORY_INDEX)
        conn.execute(NO_UPDATE_TRIGGER)
        conn.execute(NO_DELETE_TRIGGER)

    if not legacy_is_table:
        summary["steps"].append("no legacy control_overrides table; skip copy + drop")
        if apply and not view_present:
            summary["steps"].append("create control_overrides VIEW")
            conn.execute(VIEW_DDL)
            summary["created_view"] = True
        if apply:
            conn.commit()
        return summary

    # Step 2: copy legacy rows into history with operation='migrated'
    legacy_rows = conn.execute(
        """
        SELECT override_id, target_type, target_key, action_type, value,
               issued_by, issued_at, effective_until, reason, precedence
        FROM control_overrides
        """
    ).fetchall()
    now_iso = datetime.now(timezone.utc).isoformat()
    summary["migrated_upsert_rows"] = len(legacy_rows)
    summary["steps"].append(
        f"copy {len(legacy_rows)} legacy row(s) into control_overrides_history "
        f"with operation='migrated', recorded_at=issued_at"
    )

    expire_inserts: list[tuple] = []
    if apply:
        for row in legacy_rows:
            conn.execute(
                """
                INSERT INTO control_overrides_history (
                    override_id, target_type, target_key, action_type, value,
                    issued_by, issued_at, effective_until, reason, precedence,
                    operation, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'migrated', ?)
                """,
                (
                    row[0], row[1], row[2], row[3], row[4],
                    row[5], row[6], row[7], row[8], row[9],
                    row[6],  # recorded_at = issued_at
                ),
            )

    # Step 3: for each legacy row with effective_until in the past, add a second
    # 'expire' history row so the VIEW correctly projects the row as expired
    for row in legacy_rows:
        effective_until = row[7]
        if effective_until and effective_until <= now_iso:
            expire_inserts.append(
                (
                    row[0], row[1], row[2], row[3], row[4],
                    row[5], row[6], effective_until, row[8], row[9],
                    effective_until,  # recorded_at = effective_until (already in past)
                )
            )
    summary["migrated_expire_rows"] = len(expire_inserts)
    if expire_inserts:
        summary["steps"].append(
            f"add {len(expire_inserts)} 'expire' history row(s) for already-expired legacy rows"
        )
        if apply:
            for args in expire_inserts:
                conn.execute(
                    """
                    INSERT INTO control_overrides_history (
                        override_id, target_type, target_key, action_type, value,
                        issued_by, issued_at, effective_until, reason, precedence,
                        operation, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'expire', ?)
                    """,
                    args,
                )

    # Step 4: DROP legacy table (destructive; requires ZEUS_DESTRUCTIVE_CONFIRMED=1)
    if not destructive_confirmed:
        summary["steps"].append(
            "SKIP DROP TABLE control_overrides (ZEUS_DESTRUCTIVE_CONFIRMED not set)"
        )
        if apply:
            conn.rollback()
            summary["steps"].append("rolled back history inserts (destructive gate not confirmed)")
        return summary

    summary["steps"].append("DROP TABLE control_overrides (legacy)")
    if apply:
        conn.execute("DROP TABLE control_overrides")
        summary["dropped_legacy"] = True

    # Step 5: create VIEW under the legacy name
    summary["steps"].append("CREATE VIEW control_overrides")
    if apply:
        conn.execute(VIEW_DDL)
        summary["created_view"] = True
        conn.commit()

    return summary


def _print_summary(summary: dict) -> None:
    print("=== B070 control_overrides migration summary ===")
    for key, value in summary.items():
        if key == "steps":
            continue
        print(f"  {key}: {value}")
    print("  steps:")
    for step in summary["steps"]:
        print(f"    - {step}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute changes (default: dry run, describe only)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to world DB (default: project world DB from get_world_connection)",
    )
    args = parser.parse_args(argv)

    destructive_confirmed = os.environ.get("ZEUS_DESTRUCTIVE_CONFIRMED") == "1"
    apply = bool(args.apply)
    if args.db:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
    else:
        conn = get_world_connection(write_class="bulk")

    try:
        summary = run_migration(
            conn,
            apply=apply,
            destructive_confirmed=destructive_confirmed,
        )
    finally:
        conn.close()

    _print_summary(summary)
    if apply and not destructive_confirmed and summary["legacy_table_present"]:
        print(
            "\nNOTE: legacy control_overrides table still present. To complete "
            "migration, rerun with ZEUS_DESTRUCTIVE_CONFIRMED=1 environment variable."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
