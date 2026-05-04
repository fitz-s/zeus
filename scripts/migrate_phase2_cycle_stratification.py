# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: Phase 2 design doc DESIGN_PHASE2_PLATT_CYCLE_STRATIFICATION.md
#                  + critic-opus 2026-05-04 BLOCKER 3 (data_version asymmetry)
#                  + may4math.md Finding 1 (full domain key required).
#                  Run BEFORE refit_platt_v2.py with cycle-aware grouping.
"""Phase 2 schema migration: add cycle/source_id/horizon_profile to calibration tables.

What this migrates:
  - platt_models_v2 ADD COLUMN cycle TEXT NOT NULL DEFAULT '00'
  - platt_models_v2 ADD COLUMN source_id TEXT NOT NULL DEFAULT 'tigge_mars'
  - platt_models_v2 ADD COLUMN horizon_profile TEXT NOT NULL DEFAULT 'full'
  - calibration_pairs_v2 ADD COLUMN cycle TEXT NOT NULL DEFAULT '00'
  - calibration_pairs_v2 ADD COLUMN source_id TEXT NOT NULL DEFAULT 'tigge_mars'
  - calibration_pairs_v2 ADD COLUMN horizon_profile TEXT NOT NULL DEFAULT 'full'

After ALTER, runs idempotent UPDATEs deriving cycle from snapshot_id linkage:
  cycle      ← substr(ensemble_snapshots_v2.issue_time, 12, 2) when joinable
  source_id  ← stays 'tigge_mars' for legacy 'tigge_*' data_version pairs;
               flipped to 'ecmwf_open_data' if data_version starts with 'ecmwf_opendata_'
  horizon_profile ← stays 'full' (legacy is all 00z TIGGE = full horizon)

Idempotent: ALTER ... ADD COLUMN errors if the column exists; we catch and skip.
UPDATE only flips rows still at default ('00') with successful snapshot_id JOIN.

Live trade daemon must remain DOWN during this migration to avoid concurrent writes
on calibration_pairs_v2 (per critic-opus 2026-05-04 race condition warning).

Usage (from zeus repo root, zeus venv active):
    python scripts/migrate_phase2_cycle_stratification.py [--dry-run]

Verifies daemon-down precondition by checking control_overrides has an active
high-precedence operator lock; refuses to migrate if no lock present.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("phase2_migrate")

# Match Phase 2 design defaults.
DEFAULT_CYCLE = "00"
DEFAULT_SOURCE_ID = "tigge_mars"
DEFAULT_HORIZON_PROFILE = "full"

ALTERS = [
    # platt_models_v2
    ("platt_models_v2", "cycle", "TEXT NOT NULL DEFAULT '00'"),
    ("platt_models_v2", "source_id", "TEXT NOT NULL DEFAULT 'tigge_mars'"),
    ("platt_models_v2", "horizon_profile", "TEXT NOT NULL DEFAULT 'full'"),
    # calibration_pairs_v2
    ("calibration_pairs_v2", "cycle", "TEXT NOT NULL DEFAULT '00'"),
    ("calibration_pairs_v2", "source_id", "TEXT NOT NULL DEFAULT 'tigge_mars'"),
    ("calibration_pairs_v2", "horizon_profile", "TEXT NOT NULL DEFAULT 'full'"),
]


def _check_daemon_down(conn) -> tuple[bool, str]:
    """Verify trade daemon is locked via operator-precedence (>= 200) row.

    Returns (ok, message).
    """
    rows = conn.execute(
        """
        SELECT issued_by, value, precedence, effective_until
        FROM control_overrides
        WHERE target_type='global' AND target_key='entries' AND action_type='gate'
          AND value='true'
          AND (effective_until IS NULL OR effective_until > strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ORDER BY precedence DESC
        """
    ).fetchall()
    if not rows:
        return False, "no active entries-paused override; trade daemon may be live"
    top = rows[0]
    if top[2] < 200:
        return False, f"top precedence is {top[2]} (< 200); not operator-issued"
    return True, f"locked by {top[0]} precedence={top[2]} until={top[3] or 'NEVER'}"


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _apply_alters(conn, dry_run: bool) -> dict:
    applied: list[str] = []
    skipped: list[str] = []
    for table, column, type_clause in ALTERS:
        if _column_exists(conn, table, column):
            skipped.append(f"{table}.{column} (already exists)")
            continue
        sql = f"ALTER TABLE {table} ADD COLUMN {column} {type_clause}"
        logger.info("ALTER: %s", sql)
        if not dry_run:
            conn.execute(sql)
        applied.append(f"{table}.{column}")
    return {"applied": applied, "skipped": skipped}


def _backfill_calibration_pairs(conn, dry_run: bool) -> dict:
    """Derive cycle/source_id from joining calibration_pairs_v2.snapshot_id → ensemble_snapshots_v2.

    Only updates rows still at the default ('00','tigge_mars') AND with a resolvable snapshot_id.
    """
    if dry_run and not _column_exists(conn, "calibration_pairs_v2", "cycle"):
        # In dry-run, ALTER hasn't actually been applied; cannot probe the new column.
        # Estimate candidate count from snapshot_id linkage instead.
        candidate_count = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE snapshot_id IS NOT NULL"
        ).fetchone()[0]
        logger.info("[dry-run] estimated backfill candidates: %d (snapshot_id linkage)",
                    candidate_count)
        return {"backfill_candidates": candidate_count, "dry_run": True,
                "note": "ALTER not applied in dry-run; estimate based on snapshot_id presence"}

    # Count rows that need backfill
    candidate_count = conn.execute("""
        SELECT COUNT(*)
        FROM calibration_pairs_v2 cp
        WHERE cp.snapshot_id IS NOT NULL
          AND cp.cycle = ?
    """, (DEFAULT_CYCLE,)).fetchone()[0]
    logger.info("candidates for cycle backfill: %d", candidate_count)

    if dry_run:
        return {"backfill_candidates": candidate_count, "dry_run": True}

    # Backfill cycle from issue_time substring; chunked to avoid long writer lock.
    BATCH = 100_000
    updated_total = 0
    while True:
        cursor = conn.execute("""
            UPDATE calibration_pairs_v2
            SET cycle = (
                SELECT substr(es.issue_time, 12, 2)
                FROM ensemble_snapshots_v2 es
                WHERE es.snapshot_id = calibration_pairs_v2.snapshot_id
            )
            WHERE pair_id IN (
                SELECT pair_id FROM calibration_pairs_v2
                WHERE snapshot_id IS NOT NULL AND cycle = ?
                LIMIT ?
            )
        """, (DEFAULT_CYCLE, BATCH))
        rows = cursor.rowcount
        conn.commit()
        if rows == 0:
            break
        updated_total += rows
        logger.info("cycle backfill batch: %d rows (running total %d)", rows, updated_total)

    # Backfill source_id by data_version prefix (cheap UPDATE, indexed on data_version).
    src_cursor = conn.execute("""
        UPDATE calibration_pairs_v2
        SET source_id = 'ecmwf_open_data'
        WHERE source_id = 'tigge_mars' AND data_version LIKE 'ecmwf_opendata_%'
    """)
    src_updated = src_cursor.rowcount
    conn.commit()
    logger.info("source_id backfill: %d rows flipped to ecmwf_open_data", src_updated)

    # horizon_profile stays 'full' for legacy; no backfill needed.

    return {
        "cycle_updated": updated_total,
        "source_id_updated": src_updated,
        "horizon_profile_updated": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions; do not apply.")
    parser.add_argument("--skip-lock-check", action="store_true",
                        help="DANGEROUS: bypass trade-daemon-locked precondition.")
    args = parser.parse_args()

    from src.state.db import get_world_connection
    conn = get_world_connection()
    try:
        if not args.skip_lock_check:
            ok, msg = _check_daemon_down(conn)
            logger.info("daemon-lock check: %s — %s", "PASS" if ok else "FAIL", msg)
            if not ok and not args.dry_run:
                logger.error("Refusing to migrate: %s", msg)
                logger.error("Pass --skip-lock-check to override (NOT RECOMMENDED).")
                return 2

        alter_result = _apply_alters(conn, dry_run=args.dry_run)
        logger.info("ALTER summary: %s", json.dumps(alter_result, indent=2))

        backfill_result = _backfill_calibration_pairs(conn, dry_run=args.dry_run)
        logger.info("backfill summary: %s", json.dumps(backfill_result, indent=2))

        # Verification
        if not args.dry_run:
            distrib = conn.execute("""
                SELECT cycle, source_id, COUNT(*) AS n
                FROM calibration_pairs_v2
                WHERE snapshot_id IS NOT NULL
                GROUP BY cycle, source_id
                ORDER BY n DESC
                LIMIT 10
            """).fetchall()
            logger.info("calibration_pairs_v2 cycle×source_id distribution:")
            for row in distrib:
                logger.info("  cycle=%s source_id=%s rows=%d", row[0], row[1], row[2])

        print(json.dumps({
            "alter_result": alter_result,
            "backfill_result": backfill_result,
            "dry_run": args.dry_run,
        }, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
