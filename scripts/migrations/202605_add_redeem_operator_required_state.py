#!/usr/bin/env python3
# Lifecycle: created=2026-05-16; last_reviewed=2026-05-16; last_reused=never
# Purpose: Add REDEEM_OPERATOR_REQUIRED to settlement_commands.state CHECK
#   (SQLite cannot ALTER CHECK in place; rebuild via CREATE new + INSERT copy +
#   DROP old + RENAME). Also bumps PRAGMA user_version to 4 on ALL canonical
#   DBs (zeus_trades + zeus-live + zeus-world + zeus-forecasts) to satisfy
#   src.main._startup_world_db_schema_ready_check() — PR #126 review-fix from
#   Codex P1 #2 (without world+forecasts bump, daemon retries 5 min then fatal).
# Reuse: Run as operator BEFORE deploying SCHEMA_VERSION=4 code. Daemon-stop
#   prerequisite (fcntl.flock check on zeus-live.db). --dry-run flag for
#   operator verification. Authority basis: SCAFFOLD_F14_F16.md §K.8 v5.
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


NEW_SCHEMA_VERSION = 4


def _is_already_applied(conn: sqlite3.Connection) -> bool:
    """Idempotency check: did this migration already run on this DB?"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='settlement_commands' "
        "AND sql LIKE ?",
        (f"%{NEW_STATE}%",),
    ).fetchone()
    return row is not None


def _has_settlement_commands_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='settlement_commands'"
    ).fetchone()
    return row is not None


def _bump_user_version(conn: sqlite3.Connection, target: int) -> str:
    """For DBs that don't carry settlement_commands (world.db, forecasts.db)
    we still need user_version to match the shared SCHEMA_VERSION sentinel,
    because src.main._startup_world_db_schema_ready_check() compares
    PRAGMA user_version on every canonical DB against SCHEMA_VERSION.

    PR #126 review-fix (Codex P1 #2): bumping SCHEMA_VERSION on a
    trade-ledger-only CHECK change would otherwise brick boot on world.db
    + forecasts.db (still at v3) until operator manually bumped them.
    """
    cur = conn.execute("PRAGMA user_version").fetchone()
    current = cur[0] if cur else 0
    if current == target:
        return f"no_op_user_version_already_{target}"
    if current > target:
        return f"no_op_user_version_higher_{current}"
    conn.execute(f"PRAGMA user_version = {target}")
    return f"user_version_bumped_{current}_to_{target}"


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
        # PR #126 review-fix (Codex P1 #2): DBs without settlement_commands
        # (world.db, forecasts.db) still need PRAGMA user_version bumped
        # because src.main._startup_world_db_schema_ready_check() compares
        # user_version against the shared SCHEMA_VERSION sentinel.
        if not _has_settlement_commands_table(conn):
            if dry_run:
                outcome["action"] = "dry_run_would_bump_user_version"
                outcome["details"] = (
                    f"No settlement_commands table; would bump user_version "
                    f"to {NEW_SCHEMA_VERSION} only."
                )
                return outcome
            result = _bump_user_version(conn, NEW_SCHEMA_VERSION)
            conn.commit()
            outcome["action"] = "user_version_only"
            outcome["details"] = result
            return outcome

        if _is_already_applied(conn):
            # CHECK already current; still verify user_version is in sync.
            if dry_run:
                outcome["action"] = "dry_run_no_op_or_user_version_bump"
                outcome["details"] = "CHECK already current; user_version may need bump only."
                return outcome
            result = _bump_user_version(conn, NEW_SCHEMA_VERSION)
            conn.commit()
            outcome["action"] = "no_op_already_applied"
            outcome["details"] = (
                f"CHECK constraint already includes {NEW_STATE}; "
                f"user_version: {result}"
            )
            return outcome

        if dry_run:
            outcome["action"] = "dry_run_would_migrate"
            outcome["details"] = (
                f"Would rebuild settlement_commands with new CHECK, "
                f"preserve all rows, recreate {len(INDEXES)} indexes, "
                f"run foreign_key_check pre-commit, bump user_version to "
                f"{NEW_SCHEMA_VERSION}."
            )
            return outcome

        # PRAGMA foreign_keys MUST be set OUTSIDE transaction (SQLite docs)
        conn.execute("PRAGMA foreign_keys = OFF")

        conn.execute("BEGIN IMMEDIATE TRANSACTION")
        try:
            # PR #126 review-fix (Copilot 3254021453): conn.executescript()
            # in Python sqlite3 issues an implicit COMMIT before each script,
            # breaking our outer BEGIN IMMEDIATE. Use conn.execute() for the
            # single CREATE TABLE statement instead — no transaction collision.
            conn.execute(NEW_CHECK.rstrip(";").rstrip())
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

        # Bump user_version after CHECK rebuild + commit (outside transaction
        # per SQLite PRAGMA semantics).
        _bump_user_version(conn, NEW_SCHEMA_VERSION)
        conn.commit()

        conn.execute("PRAGMA foreign_keys = ON")
        outcome["action"] = "migrated"
        outcome["details"] = (
            f"Table rebuilt; CHECK includes new state; FKs verified; "
            f"user_version bumped to {NEW_SCHEMA_VERSION}."
        )
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
        # PR #126 review-fix (Codex P1 #2): include world.db in default targets
        # so SCHEMA_VERSION sentinel stays in sync across DBs that share it.
        #
        # PR #126 G5c FA3 ship-blocker fix (R2): zeus-forecasts.db is NOT in
        # default targets. forecasts DB uses an INDEPENDENT sentinel
        # SCHEMA_FORECASTS_VERSION (src/state/db.py:2427, currently 3) that
        # this PR does NOT change. Bumping forecasts.db user_version to 4
        # would trigger assert_schema_current_forecasts(conn) (db.py:3099)
        # to raise SchemaOutOfDateError on next boot of com.zeus.forecast-live
        # — fatal. settlement_commands lives on trade DB only; forecasts
        # schema is untouched here.
        targets = [
            repo_root / "state" / "zeus_trades.db",
            repo_root / "state" / "zeus-live.db",
            repo_root / "state" / "zeus-world.db",
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


def up(conn: sqlite3.Connection) -> None:
    """Runner-framework entry point (def up(conn) contract).

    Delegates to the idempotent per-connection logic already encoded in this
    migration.  The runner passes an open connection; we apply the CHECK-
    constraint rebuild and user_version bump if not yet done.

    Note: the runner seeds this migration name into _migrations_applied at
    ledger-create time (_BOOTSTRAP_APPLIED) so up() is never called on a DB
    that already had the migration applied via the standalone main() path.
    This wrapper is a conformance shim for future runner invocations and for
    new-DB provisioning paths.
    """
    if _is_already_applied(conn):
        _bump_user_version(conn, NEW_SCHEMA_VERSION)
        conn.commit()
        return

    if not _has_settlement_commands_table(conn):
        _bump_user_version(conn, NEW_SCHEMA_VERSION)
        conn.commit()
        return

    # Full table-rebuild path (mirrors _migrate_one_db without the Path/lock
    # logic — the runner owns connection lifecycle and lock acquisition).
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE TRANSACTION")
    try:
        conn.execute(NEW_CHECK.rstrip(";").rstrip())
        conn.execute(
            "INSERT INTO settlement_commands_new SELECT * FROM settlement_commands"
        )
        conn.execute("DROP TABLE settlement_commands")
        conn.execute("ALTER TABLE settlement_commands_new RENAME TO settlement_commands")
        for idx_sql in INDEXES:
            conn.execute(idx_sql)
        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            conn.execute("ROLLBACK")
            raise RuntimeError(
                f"foreign_key_check returned {len(violations)} violations: "
                f"{violations[:5]!r}"
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    _bump_user_version(conn, NEW_SCHEMA_VERSION)
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


if __name__ == "__main__":
    sys.exit(main())
