#!/usr/bin/env python3
# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: KARACHI_TRADE_DECISIONS_GAP_TRACE.md §6-8
#
# Migration: add BEFORE INSERT TRIGGER on position_current requiring a
# matching trade_decisions.runtime_trade_id row.
#
# Why: trade_decisions was authored as best-effort telemetry, but downstream
# consumers treat it as the authoritative bridge linking the UUID
# position_current row to the INTEGER position_lots ledger.  The silent-except
# in log_trade_entry (fixed in the same packet) allowed position_current to be
# committed without a bridge row, masking the defect on every subsequent
# lifecycle update.
#
# Trigger semantics:
#   - BEFORE INSERT only — does NOT affect ON CONFLICT DO UPDATE paths
#     (existing rows updated via upsert are unaffected).
#   - The 17 pre-existing orphan rows in position_current are NOT affected
#     by this trigger; it fires only on new inserts.
#   - Karachi safety: the synthesizer (src/state/trade_decisions_synthesizer.py)
#     ships in the same packet.  When update_trade_lifecycle encounters a
#     missing bridge on an existing orphan row, the synthesizer fires first
#     and populates the bridge programmatically — no operator action needed.
#     The trigger prevents new orphans; the synthesizer repairs historical ones.
#
# Idempotency: checks sqlite_master before creating trigger; safe to re-run.
#
# Runner-framework entry point: def up(conn) per F23 migration runner contract.
# Standalone-mode raw sqlite3.connect at line 114 is operator-mode (`python -m`
# direct invocation); operator stops daemon before running.  Runner-invoked
# def up(conn) path is lock-free by design (runner owns db_writer_lock).
# WRITER_LOCK_DEFER_REVIEW=2026-05-17
#
# Operator runbook:
#   python scripts/migrations/202605_position_current_bridge_required_trigger.py
#   python scripts/migrations/202605_position_current_bridge_required_trigger.py --dry-run

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

TRIGGER_NAME = "trg_position_current_requires_bridge"

TRIGGER_SQL = f"""
CREATE TRIGGER {TRIGGER_NAME}
BEFORE INSERT ON position_current
BEGIN
  SELECT RAISE(ABORT, 'position_current INSERT requires matching trade_decisions.runtime_trade_id')
  WHERE NOT EXISTS (
    SELECT 1 FROM trade_decisions WHERE runtime_trade_id = NEW.position_id
  );
END
"""


def _is_already_applied(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
        (TRIGGER_NAME,),
    ).fetchone()
    return row is not None


def _has_trade_decisions_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_decisions'"
    ).fetchone()
    return row is not None


def _has_position_current_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='position_current'"
    ).fetchone()
    return row is not None


def up(conn: sqlite3.Connection) -> None:
    """Runner-framework entry point (def up(conn) contract).

    Creates the BEFORE INSERT trigger on position_current that requires a
    matching trade_decisions.runtime_trade_id row.  Idempotent.
    """
    if _is_already_applied(conn):
        logger.debug("Trigger %s already exists; skipping.", TRIGGER_NAME)
        return
    if not _has_trade_decisions_table(conn):
        logger.warning(
            "trade_decisions table not found; skipping trigger creation."
        )
        return
    if not _has_position_current_table(conn):
        logger.warning(
            "position_current table not found; skipping trigger creation."
        )
        return
    conn.execute(TRIGGER_SQL)
    conn.commit()
    logger.info("Created trigger %s.", TRIGGER_NAME)


def _migrate_one_db(db_path: Path, *, dry_run: bool = False) -> dict:
    outcome: dict = {"db_path": str(db_path), "action": None, "details": ""}

    if not db_path.exists():
        outcome["action"] = "skip_missing"
        outcome["details"] = "DB file does not exist"
        return outcome

    conn = sqlite3.connect(str(db_path))
    try:
        if not _has_trade_decisions_table(conn) or not _has_position_current_table(conn):
            outcome["action"] = "skip_no_tables"
            outcome["details"] = "trade_decisions or position_current not present"
            return outcome

        if _is_already_applied(conn):
            outcome["action"] = "no_op_already_applied"
            outcome["details"] = f"Trigger {TRIGGER_NAME} already exists."
            return outcome

        if dry_run:
            outcome["action"] = "dry_run_would_create_trigger"
            outcome["details"] = (
                f"Would CREATE TRIGGER {TRIGGER_NAME} BEFORE INSERT ON position_current."
            )
            return outcome

        conn.execute(TRIGGER_SQL)
        conn.commit()
        outcome["action"] = "created_trigger"
        outcome["details"] = f"Trigger {TRIGGER_NAME} created."
        return outcome
    finally:
        conn.close()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add BEFORE INSERT trigger on position_current requiring trade_decisions bridge.",
    )
    parser.add_argument("--db", action="append", help="Target DB path.")
    parser.add_argument("--dry-run", action="store_true", help="Plan-only; no DB modification.")
    parser.add_argument("--repo-root", default=None, help="Override repo root.")
    args = parser.parse_args(argv)

    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        repo_root = Path(__file__).resolve().parent.parent.parent

    if args.db:
        targets = [Path(p).resolve() for p in args.db]
    else:
        targets = [
            repo_root / "state" / "zeus_trades.db",
            repo_root / "state" / "zeus-live.db",
        ]

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger.info("Migration: %s", TRIGGER_NAME)
    if args.dry_run:
        logger.info("DRY-RUN mode: no DB modification.")

    overall_exit = 0
    for db in targets:
        try:
            outcome = _migrate_one_db(db, dry_run=args.dry_run)
        except Exception as exc:
            outcome = {"db_path": str(db), "action": "error", "details": f"{type(exc).__name__}: {exc}"}
            overall_exit = 1
        logger.info("  %s -> %s (%s)", db.name, outcome["action"], outcome["details"])
        if outcome["action"] == "error":
            overall_exit = 1

    return overall_exit


if __name__ == "__main__":
    sys.exit(main())
