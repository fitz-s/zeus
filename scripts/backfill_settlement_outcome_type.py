# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase7_settlement_type_gate/PHASE_7_PLAN.md §T4
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/08_PHASE_7_SETTLEMENT_TYPE_GATE.md
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: One-shot backfill of settlements_v2.outcome_type from existing authority+winning_bin columns.
# Reuse: Safe to re-run (idempotent). Use --dry-run first. Only touches rows with outcome_type IS NULL.
"""Backfill settlements_v2.outcome_type from existing authority column.

Mapping (per plan §T4):
  authority='VERIFIED'   + winning_bin NOT NULL → VENUE_RESOLVED_WIN (3)
  authority='UNVERIFIED'                        → UNRESOLVED (0)
  authority='QUARANTINED'                       → DISPUTED (100)

Rows with outcome_type already set are skipped (idempotent).
Chunked 500 rows per SAVEPOINT for safe partial-progress on large DBs.
Use --dry-run to preview without writing.

Usage:
    python scripts/backfill_settlement_outcome_type.py [--dry-run] [--chunk-size N]
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

# Ensure project root is on the path when run directly
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.contracts.settlement_outcome import SettlementOutcome

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authority → SettlementOutcome mapping
# ---------------------------------------------------------------------------

def _authority_to_outcome(authority: str, winning_bin: object) -> int:
    """Map an authority + winning_bin pair to a SettlementOutcome integer.

    VERIFIED + winning_bin non-null → VENUE_RESOLVED_WIN (settlements_v2 stores
    winning side only; LOSE is not derivable from existing columns alone).
    UNVERIFIED → UNRESOLVED.
    QUARANTINED → DISPUTED.
    Any other / unknown → UNRESOLVED (fail-closed).
    """
    if authority == "VERIFIED" and winning_bin is not None and str(winning_bin).strip():
        return int(SettlementOutcome.VENUE_RESOLVED_WIN)
    if authority == "QUARANTINED":
        return int(SettlementOutcome.DISPUTED)
    # UNVERIFIED or any unknown authority
    return int(SettlementOutcome.UNRESOLVED)


# ---------------------------------------------------------------------------
# Core backfill logic
# ---------------------------------------------------------------------------

def run_backfill(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    chunk_size: int = 500,
) -> dict[str, int]:
    """Backfill outcome_type on rows where it is NULL.

    Args:
        conn: Open connection to zeus-forecasts.db (caller owns lifecycle).
        dry_run: When True, compute assignments but do not write.
        chunk_size: Rows per SAVEPOINT chunk. Default 500.

    Returns:
        Dict with 'total_processed', 'total_updated', 'total_skipped_already_set'.
        total_skipped_already_set: rows with outcome_type IS NOT NULL skipped by the WHERE clause.
    """
    stats: dict[str, int] = {
        "total_processed": 0,
        "total_skipped_already_set": 0,
        "total_updated": 0,
    }

    # Stream rows via cursor + fetchmany to avoid loading entire result set into memory.
    cursor = conn.execute(
        "SELECT settlement_id, authority, winning_bin FROM settlements_v2 WHERE outcome_type IS NULL"
    )

    chunk_index = 0
    while True:
        chunk = cursor.fetchmany(chunk_size)
        if not chunk:
            break
        stats["total_processed"] += len(chunk)

        updates: list[tuple[int, int]] = []
        for row in chunk:
            sid, authority, winning_bin = row[0], row[1], row[2]
            outcome_int = _authority_to_outcome(str(authority or "UNVERIFIED"), winning_bin)
            updates.append((outcome_int, sid))

        if dry_run:
            for outcome_int, sid in updates:
                logger.debug("[dry-run] settlement_id=%s → outcome_type=%s (%s)",
                             sid, outcome_int, SettlementOutcome(outcome_int).name)
            stats["total_updated"] += len(updates)
        else:
            # INV-37: use SAVEPOINT per chunk; no raw conn.commit() at top level
            sp_name = f"backfill_chunk_{chunk_index}"
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                conn.executemany(
                    "UPDATE settlements_v2 SET outcome_type = ? WHERE settlement_id = ?",
                    updates,
                )
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                stats["total_updated"] += len(updates)
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                raise

        chunk_index += 1

    logger.info("Processed %d rows total", stats["total_processed"])
    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview assignments without writing")
    parser.add_argument("--chunk-size", type=int, default=500, help="Rows per SAVEPOINT chunk (default 500)")
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to zeus-forecasts.db. Defaults to ZEUS_FORECASTS_DB_PATH env or project default.",
    )
    args = parser.parse_args()

    # Resolve DB path
    if args.db_path:
        db_path = Path(args.db_path)
    else:
        env_path = os.environ.get("ZEUS_FORECASTS_DB_PATH")
        if env_path:
            db_path = Path(env_path)
        else:
            from src.state.db import ZEUS_FORECASTS_DB_PATH
            db_path = ZEUS_FORECASTS_DB_PATH

    if not db_path.exists():
        logger.error("DB not found at %s", db_path)
        sys.exit(1)

    logger.info("Connecting to %s (dry_run=%s, chunk_size=%d)", db_path, args.dry_run, args.chunk_size)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        stats = run_backfill(conn, dry_run=args.dry_run, chunk_size=args.chunk_size)
    finally:
        conn.close()

    action = "Would update" if args.dry_run else "Updated"
    logger.info(
        "%s %d rows (of %d NULL, %d already set)",
        action,
        stats["total_updated"],
        stats["total_processed"],
        stats["total_skipped_already_set"],
    )
    if args.dry_run:
        logger.info("[dry-run] No rows written.")


if __name__ == "__main__":
    main()
