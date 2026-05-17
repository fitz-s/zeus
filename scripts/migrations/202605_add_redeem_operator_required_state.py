#!/usr/bin/env python3
# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: SCAFFOLD_F14_F16.md §K.8 v5 (post G2 round-4 PASS)
#
# Migration: add REDEEM_OPERATOR_REQUIRED to settlement_commands.state
# CHECK constraint via SQLite table-rebuild pattern.
#
# Why: SQLite cannot ALTER CHECK in place. Adding the new state requires
# CREATE new + INSERT copy + DROP old + RENAME. Per SCAFFOLD §K.8 v5 the
# spec includes:
#   - Daemon-stop prerequisite (fcntl.flock check on zeus-live.db)
#   - PRAGMA foreign_keys OFF (set OUTSIDE transaction per SQLite docs)
#   - Per-DB atomic mode (each DB its own transaction)
#   - Step 8.5 PRAGMA foreign_key_check BEFORE COMMIT → abort on violations
#   - Idempotency via sqlite_master.sql LIKE check
#   - --dry-run mode for operator verification
#
# Production state at migration time per scout 2026-05-16:
#   state/zeus_trades.db.settlement_commands: 0 rows
#   state/zeus-live.db.settlement_commands: 0 rows
# Migration is data-cost-zero; included as durable upgrade pattern for
# future state additions (REDEEM_ABANDONED, etc) when tables are non-empty.

from __future__ import annotations

import argparse
import fcntl
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

NEW_STATE = "REDEEM_OPERATOR_REQUIRED"

NEW_CHECK = (
    "CREATE TABLE settlement_commands_new (\n"
    "  command_id TEXT PRIMARY KEY,\n"
    "  state TEXT NOT NULL CHECK (state IN (\n"
    "    'REDEEM_INTENT_CREATED','REDEEM_SUBMITTED','REDEEM_TX_HASHED',\n"
    "    'REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_RETRYING','REDEEM_REVIEW_REQUIRED',\n"
    "    'REDEEM_OPERATOR_REQUIRED'\n"
    "  )),\n"
    "  condition_id TEXT NOT NULL,\n"
    "  market_id TEXT NOT NULL,\n"
    "  payout_asset TEXT NOT NULL CHECK (payout_asset IN ('pUSD','USDC','USDC_E')),\n"
    "  pusd_amount_micro INTEGER,\n"
    "  token_amounts_json TEXT,\n"
    "  tx_hash TEXT,\n"
    "  block_number INTEGER,\n"
    "  confirmation_count INTEGER DEFAULT 0,\n"
    "  requested_at TEXT NOT NULL,\n"
    "  submitted_at TEXT,\n"
    "  terminal_at TEXT,\n"
    "  error_payload TEXT\n"
    ");"
)

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_settlement_commands_state "
    "ON settlement_commands (state, requested_at);",
    "CREATE INDEX IF NOT EXISTS idx_settlement_commands_condition "
    "ON settlement_commands (condition_id, market_id);",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_settlement_commands_active_condition_asset "
    "ON settlement_commands (condition_id, market_id, payout_asset) "
    "WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED');",
]


def _is_already_applied(conn: sqlite3.Connection) -> bool:
    """Idempotency check: did this migration already run on this DB?"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='settlement_commands' "
        "AND sql LIKE ?",
        (f"%{NEW_STATE}%",),
    ).fetchone()
    return row is not None


def _check_db_lock_free(db_path: Path) -> None:
    """Daemon-stop prerequisite: ensure DB file is not held by daemon.

    Per SCAFFOLD §K.8 v5 NEW-P12-v4a fix: refuse to run if state/zeus-live.db
    is file-locked by another process (running daemon).
    """
    if not db_path.exists():
        # nothing to migrate; downstream will skip
        return
    try:
        with db_path.open("rb") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            # release immediately; we only wanted to test acquisition
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except BlockingIOError:
        raise RuntimeError(
            f"DB {db_path} is locked by another process — stop the daemon "
            f"(e.g. launchctl stop com.zeus.live) before running migration."
        )


def _migrate_one_db(db_path: Path, *, dry_run: bool = False) -> dict:
    """Migrate a single DB. Returns outcome dict."""
    outcome: dict = {"db_path": str(db_path), "action": None, "details": ""}

    if not db_path.exists():
        outcome["action"] = "skip_missing"
        outcome["details"] = "DB file does not exist"
        return outcome

    _check_db_lock_free(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        if _is_already_applied(conn):
            outcome["action"] = "no_op_already_applied"
            outcome["details"] = f"CHECK constraint already includes {NEW_STATE}"
            return outcome

        if dry_run:
            outcome["action"] = "dry_run_would_migrate"
            outcome["details"] = (
                f"Would rebuild settlement_commands with new CHECK, "
                f"preserve all rows, recreate {len(INDEXES)} indexes, "
                f"run foreign_key_check pre-commit."
            )
            return outcome

        # PRAGMA foreign_keys MUST be set OUTSIDE transaction (SQLite docs)
        conn.execute("PRAGMA foreign_keys = OFF")

        conn.execute("BEGIN IMMEDIATE TRANSACTION")
        try:
            conn.executescript(NEW_CHECK)
            conn.execute(
                "INSERT INTO settlement_commands_new SELECT * FROM settlement_commands"
            )
            conn.execute("DROP TABLE settlement_commands")
            conn.execute("ALTER TABLE settlement_commands_new RENAME TO settlement_commands")
            for idx_sql in INDEXES:
                conn.execute(idx_sql)
            # Step 8.5: foreign_key_check BEFORE COMMIT per SCAFFOLD v5
            violations = list(conn.execute("PRAGMA foreign_key_check"))
            if violations:
                conn.execute("ROLLBACK")
                outcome["action"] = "abort_fk_violations"
                outcome["details"] = (
                    f"foreign_key_check returned {len(violations)} violations; "
                    f"rolled back. First few: {violations[:5]!r}"
                )
                return outcome
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        conn.execute("PRAGMA foreign_keys = ON")
        outcome["action"] = "migrated"
        outcome["details"] = "Table rebuilt; CHECK includes new state; FKs verified."
        return outcome
    finally:
        conn.close()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add REDEEM_OPERATOR_REQUIRED to settlement_commands CHECK.",
    )
    parser.add_argument(
        "--db",
        action="append",
        help="Target DB path. If omitted, runs against both zeus_trades.db "
        "and zeus-live.db under state/.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan-only; no DB modification.",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Override repo root (default: auto-detect from this script).",
    )
    args = parser.parse_args(argv)

    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        # scripts/migrations/<this_file>.py → repo root is grandparent.parent
        repo_root = Path(__file__).resolve().parent.parent.parent

    if args.db:
        targets = [Path(p).resolve() for p in args.db]
    else:
        targets = [
            repo_root / "state" / "zeus_trades.db",
            repo_root / "state" / "zeus-live.db",
        ]

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger.info("Migration: add %s to settlement_commands.state CHECK", NEW_STATE)
    logger.info("Targets: %s", [str(t) for t in targets])
    if args.dry_run:
        logger.info("DRY-RUN mode: no DB modification will occur.")

    all_outcomes = []
    overall_exit = 0
    for db in targets:
        try:
            outcome = _migrate_one_db(db, dry_run=args.dry_run)
        except Exception as exc:
            outcome = {
                "db_path": str(db),
                "action": "error",
                "details": f"{type(exc).__name__}: {exc}",
            }
            overall_exit = 1
        all_outcomes.append(outcome)
        logger.info("  %s -> %s (%s)", db.name, outcome["action"], outcome["details"])
        if outcome["action"] in {"abort_fk_violations", "error"}:
            overall_exit = 1

    logger.info("Migration complete. Per-DB outcomes:")
    for o in all_outcomes:
        logger.info("  %s", o)

    return overall_exit


if __name__ == "__main__":
    sys.exit(main())
