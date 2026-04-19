"""B071 migration: convert token_suppression from mutable table to VIEW over append-only history.

BEFORE: token_suppression is a mutable SQLite table with PRIMARY KEY (token_id).
        Writes use INSERT ... ON CONFLICT DO UPDATE (overwrite). History is lost.

AFTER:  token_suppression_history is an append-only log. token_suppression_current
        is a VIEW that projects the latest history_id per token_id. Every write
        is a new history row. Audit trail preserved for 3-state sequences like
        (auto-suppress → manual-override → auto-suppress).

WORKTREE MODE (fresh DB): init_schema already creates both shapes via
apply_architecture_kernel_schema. This migration is a no-op on fresh DBs.

PRODUCTION MODE (existing DB with legacy token_suppression table as mutable):
  1. Create token_suppression_history (if not exists)
  2. Copy every row from legacy token_suppression into history with
     operation='migrated' and recorded_at=created_at
  3. DROP TABLE token_suppression (legacy) — guarded by ZEUS_DESTRUCTIVE_CONFIRMED=1
  4. CREATE VIEW token_suppression as SELECT from token_suppression_current
     (backward-compat alias)

The script checks for ZEUS_DESTRUCTIVE_CONFIRMED=1 before the DROP step.
Without that env var, it runs in dry-run mode and only reports what would happen.

Usage:
    python scripts/migrate_b071_token_suppression_to_history.py           # dry run
    ZEUS_DESTRUCTIVE_CONFIRMED=1 \\
        python scripts/migrate_b071_token_suppression_to_history.py --apply
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
CREATE TABLE IF NOT EXISTS token_suppression_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT NOT NULL,
    condition_id TEXT,
    suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
        'operator_quarantine_clear',
        'chain_only_quarantined',
        'settled_position'
    )),
    source_module TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    operation TEXT NOT NULL DEFAULT 'record' CHECK (operation IN ('record', 'migrated')),
    recorded_at TEXT NOT NULL
)
"""

HISTORY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_token_suppression_history_id_time
    ON token_suppression_history(token_id, history_id DESC)
"""

HISTORY_TRIGGER_NO_UPDATE = """
CREATE TRIGGER IF NOT EXISTS token_suppression_history_no_update
BEFORE UPDATE ON token_suppression_history
BEGIN
    SELECT RAISE(ABORT, 'token_suppression_history is append-only');
END
"""

HISTORY_TRIGGER_NO_DELETE = """
CREATE TRIGGER IF NOT EXISTS token_suppression_history_no_delete
BEFORE DELETE ON token_suppression_history
BEGIN
    SELECT RAISE(ABORT, 'token_suppression_history is append-only');
END
"""

CURRENT_VIEW_DDL = """
CREATE VIEW IF NOT EXISTS token_suppression_current AS
SELECT token_id, condition_id, suppression_reason, source_module,
       created_at, updated_at, evidence_json
FROM token_suppression_history h1
WHERE history_id = (
    SELECT MAX(history_id)
    FROM token_suppression_history h2
    WHERE h2.token_id = h1.token_id
)
"""

LEGACY_ALIAS_VIEW_DDL = """
CREATE VIEW IF NOT EXISTS token_suppression AS
SELECT token_id, condition_id, suppression_reason, source_module,
       created_at, updated_at, evidence_json
FROM token_suppression_current
"""


def migrate(conn: sqlite3.Connection, apply: bool, drop_legacy: bool) -> dict:
    """Run the B071 migration. Returns a summary dict."""
    legacy_is_table = _object_exists(conn, "token_suppression", "table")
    legacy_is_view = _object_exists(conn, "token_suppression", "view")
    history_exists = _object_exists(conn, "token_suppression_history", "table")
    current_view_exists = _object_exists(conn, "token_suppression_current", "view")

    summary: dict = {
        "legacy_is_table": legacy_is_table,
        "legacy_is_view": legacy_is_view,
        "history_exists": history_exists,
        "current_view_exists": current_view_exists,
        "rows_migrated": 0,
        "drop_performed": False,
        "alias_view_created": False,
        "dry_run": not apply,
    }

    if not legacy_is_table and not history_exists:
        print("[B071] Neither token_suppression table nor history exists — nothing to migrate.")
        return summary

    if not legacy_is_table and history_exists:
        print("[B071] token_suppression is already migrated (history table present, legacy table absent). No-op.")
        return summary

    legacy_count = _count(conn, "token_suppression") if legacy_is_table else 0
    history_count = _count(conn, "token_suppression_history") if history_exists else 0
    print(f"[B071] Legacy token_suppression rows: {legacy_count}")
    print(f"[B071] Existing token_suppression_history rows: {history_count}")

    if not apply:
        print(
            f"[B071] DRY RUN: would create token_suppression_history, copy {legacy_count} rows "
            f"with operation='migrated'."
        )
        if drop_legacy:
            if os.environ.get("ZEUS_DESTRUCTIVE_CONFIRMED") != "1":
                print("[B071] DRY RUN: --drop-legacy requested but ZEUS_DESTRUCTIVE_CONFIRMED=1 not set.")
            else:
                print("[B071] DRY RUN: would DROP TABLE token_suppression and CREATE VIEW token_suppression.")
        return summary

    # Apply mode
    now = datetime.now(timezone.utc).isoformat()

    # Step 1: create history table if not exists
    with conn:
        conn.execute(HISTORY_DDL)
        conn.execute(HISTORY_INDEX)
        conn.execute(HISTORY_TRIGGER_NO_UPDATE)
        conn.execute(HISTORY_TRIGGER_NO_DELETE)
        conn.execute(CURRENT_VIEW_DDL)

    # Step 2: copy legacy rows into history
    if legacy_is_table and legacy_count > 0:
        rows = conn.execute(
            """
            SELECT token_id, condition_id, suppression_reason, source_module,
                   created_at, updated_at, evidence_json
            FROM token_suppression
            ORDER BY created_at ASC, token_id ASC
            """
        ).fetchall()
        already_migrated = set()
        if history_exists and history_count > 0:
            already_migrated = {
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT token_id FROM token_suppression_history WHERE operation = 'migrated'"
                ).fetchall()
            }
        to_insert = [r for r in rows if str(r[0]) not in already_migrated]
        if to_insert:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO token_suppression_history (
                        token_id, condition_id, suppression_reason, source_module,
                        created_at, updated_at, evidence_json, operation, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'migrated', ?)
                    """,
                    [
                        (
                            str(r["token_id"] or ""),
                            r["condition_id"],
                            str(r["suppression_reason"] or ""),
                            str(r["source_module"] or ""),
                            str(r["created_at"] or now),
                            str(r["updated_at"] or now),
                            str(r["evidence_json"] or "{}"),
                            now,
                        )
                        for r in to_insert
                    ],
                )
            summary["rows_migrated"] = len(to_insert)
            print(f"[B071] Migrated {len(to_insert)} rows into token_suppression_history.")
        else:
            print("[B071] All legacy rows already present in history — skipping INSERT.")

    # Step 3: drop legacy table and create alias VIEW (destructive — requires env var)
    if drop_legacy:
        if os.environ.get("ZEUS_DESTRUCTIVE_CONFIRMED") != "1":
            print(
                "[B071] --drop-legacy requested but ZEUS_DESTRUCTIVE_CONFIRMED=1 not set. "
                "Set the env var to enable the DROP + VIEW creation."
            )
        else:
            with conn:
                conn.execute("DROP TABLE token_suppression")
                conn.execute(LEGACY_ALIAS_VIEW_DDL)
            summary["drop_performed"] = True
            summary["alias_view_created"] = True
            print("[B071] Dropped legacy token_suppression TABLE, created token_suppression VIEW alias.")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="B071 migration: token_suppression → history+view")
    parser.add_argument("--apply", action="store_true", help="Apply migration (default: dry run)")
    parser.add_argument(
        "--drop-legacy",
        action="store_true",
        help="After migration, DROP legacy table and CREATE VIEW alias (requires ZEUS_DESTRUCTIVE_CONFIRMED=1)",
    )
    args = parser.parse_args()

    conn = get_world_connection()
    try:
        result = migrate(conn, apply=args.apply, drop_legacy=args.drop_legacy)
        print(f"[B071] Result: {result}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
