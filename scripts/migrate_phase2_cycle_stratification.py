# Created: 2026-05-04
# Last reused/audited: 2026-05-05
# Authority basis: Phase 2 design doc DESIGN_PHASE2_PLATT_CYCLE_STRATIFICATION.md
#                  + critic-opus 2026-05-04 BLOCKER 3 (data_version asymmetry)
#                  + may4math.md Finding 1 (full domain key required).
#                  + critic-opus 2026-05-05 blockers 1/2/3 (UNIQUE rebuild,
#                    cycle well-formed assertion, platt source_id backfill).
#                  + loop convergence + busy_timeout fixes (post-crash 00:33Z).
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
    """Single-pass cycle + source_id backfill (no loop).

    2026-05-05: replaced previous chunked loop that did not converge —
    UPDATE re-touched 00z rows whose substring extract == default,
    incrementing rowcount but never advancing past the first batch.
    Single-pass flip of 12z rows is correct and terminates immediately.
    """
    if dry_run and not _column_exists(conn, "calibration_pairs_v2", "cycle"):
        candidate_count = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE snapshot_id IS NOT NULL"
        ).fetchone()[0]
        logger.info("[dry-run] estimated backfill candidates: %d", candidate_count)
        return {"backfill_candidates": candidate_count, "dry_run": True,
                "note": "ALTER not applied in dry-run; estimate based on snapshot_id presence"}

    # Estimate 12z flip candidates
    cycle_12_count = conn.execute("""
        SELECT COUNT(*) FROM calibration_pairs_v2
        WHERE snapshot_id IN (
            SELECT snapshot_id FROM ensemble_snapshots_v2
            WHERE substr(issue_time, 12, 2) = '12'
        )
    """).fetchone()[0]
    logger.info("12z cycle-flip candidates: %d", cycle_12_count)

    if dry_run:
        return {"cycle_12_candidates": cycle_12_count, "dry_run": True}

    # Single-pass: flip only 12z rows; 00z stays at DEFAULT_CYCLE='00'
    cycle_cursor = conn.execute("""
        UPDATE calibration_pairs_v2
        SET cycle = '12'
        WHERE snapshot_id IN (
            SELECT snapshot_id FROM ensemble_snapshots_v2
            WHERE substr(issue_time, 12, 2) = '12'
        )
    """)
    cycle_updated = cycle_cursor.rowcount
    conn.commit()
    logger.info("cycle backfill: %d rows flipped to '12'", cycle_updated)

    # source_id flip (single-pass — was always correct)
    src_cursor = conn.execute("""
        UPDATE calibration_pairs_v2
        SET source_id = 'ecmwf_open_data'
        WHERE source_id = 'tigge_mars' AND data_version LIKE 'ecmwf_opendata_%'
    """)
    src_updated = src_cursor.rowcount
    conn.commit()
    logger.info("source_id backfill: %d rows flipped to ecmwf_open_data", src_updated)

    return {
        "cycle_updated": cycle_updated,
        "source_id_updated": src_updated,
        "horizon_profile_updated": 0,
    }


def _backfill_platt_models_source_id(conn, dry_run: bool) -> dict:
    """Blocker 3 (2026-05-05 critic-opus): symmetric source_id flip on platt_models_v2.

    Mirrors the calibration_pairs_v2 prefix-match logic (line 167-174). Pre-existing
    OpenData-trained Platt rows stay mislabeled 'tigge_mars' otherwise.
    """
    if dry_run and not _column_exists(conn, "platt_models_v2", "source_id"):
        # ALTER not applied in dry-run; estimate via data_version prefix only.
        candidate = conn.execute(
            "SELECT COUNT(*) FROM platt_models_v2 "
            "WHERE data_version LIKE 'ecmwf_opendata_%'"
        ).fetchone()[0]
        return {"candidates": candidate, "dry_run": True,
                "note": "ALTER not applied in dry-run; estimate based on data_version prefix"}

    candidate = conn.execute(
        "SELECT COUNT(*) FROM platt_models_v2 "
        "WHERE source_id = 'tigge_mars' AND data_version LIKE 'ecmwf_opendata_%'"
    ).fetchone()[0]
    if dry_run:
        return {"candidates": candidate, "dry_run": True}
    if candidate == 0:
        return {"updated": 0, "note": "no mislabeled OpenData Platt rows found"}
    cursor = conn.execute(
        "UPDATE platt_models_v2 "
        "SET source_id = 'ecmwf_open_data' "
        "WHERE source_id = 'tigge_mars' AND data_version LIKE 'ecmwf_opendata_%'"
    )
    conn.commit()
    return {"updated": cursor.rowcount}


def _assert_cycle_well_formed(conn) -> dict:
    """Blocker 2 (2026-05-05 critic-opus): fail-closed on malformed cycle.

    Only audits rows that participated in the JOIN backfill (snapshot_id NOT NULL).
    Orphan-snapshot rows legitimately keep DEFAULT_CYCLE='00' and pass.
    """
    bad = conn.execute(
        """
        SELECT COUNT(*) FROM calibration_pairs_v2
        WHERE snapshot_id IS NOT NULL
          AND (cycle IS NULL OR cycle = '' OR cycle NOT IN ('00', '12'))
        """
    ).fetchone()[0]
    if bad == 0:
        return {"status": "ok", "bad_rows": 0}
    sample = conn.execute(
        """
        SELECT pair_id, snapshot_id, cycle FROM calibration_pairs_v2
        WHERE snapshot_id IS NOT NULL
          AND (cycle IS NULL OR cycle = '' OR cycle NOT IN ('00', '12'))
        LIMIT 5
        """
    ).fetchall()
    return {"status": "fail", "bad_rows": bad, "sample": sample}


def _rebuild_platt_models_v2_extended_unique(conn, dry_run: bool) -> dict:
    """Blocker 1 (2026-05-05 critic-opus): rebuild platt_models_v2 with extended UNIQUE.

    Legacy prod DB has inline UNIQUE(temperature_metric, cluster, season, data_version,
    input_space, is_active) — 6 columns. SQLite cannot ALTER DROP CONSTRAINT, so we use
    the 12-step recipe: create tmp with new schema, INSERT-by-name, DROP, RENAME.
    """
    # Step 1 — probe current state via autoindex SQL
    autoindex_rows = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND tbl_name='platt_models_v2' "
        "AND name LIKE 'sqlite_autoindex_%'"
    ).fetchall()
    extended = any(
        (idx_sql or '').lower().count('cycle') > 0
        and (idx_sql or '').lower().count('source_id') > 0
        for _, idx_sql in autoindex_rows
    )
    if extended:
        return {"rebuild": "skipped", "reason": "extended UNIQUE already in place"}

    # Step 2 — row count
    n = conn.execute("SELECT COUNT(*) FROM platt_models_v2").fetchone()[0]

    # Step 3 — dry-run: report without applying
    if dry_run:
        return {
            "rebuild": "dry_run",
            "rows_to_copy": n,
            "current_unique_dim": 6,
            "target_unique_dim": 9,
        }

    # Step 4 — real run inside explicit transaction
    COLS = (
        "model_key, temperature_metric, cluster, season, data_version, input_space, "
        "param_A, param_B, param_C, bootstrap_params_json, n_samples, brier_insample, "
        "fitted_at, is_active, authority, bucket_key, "
        "cycle, source_id, horizon_profile, recorded_at"
    )
    try:
        conn.execute("BEGIN")

        conn.execute(f"""
            CREATE TABLE platt_models_v2_tmp (
                model_key TEXT PRIMARY KEY,
                temperature_metric TEXT NOT NULL
                    CHECK (temperature_metric IN ('high', 'low')),
                cluster TEXT NOT NULL,
                season TEXT NOT NULL,
                data_version TEXT NOT NULL,
                input_space TEXT NOT NULL DEFAULT 'raw_probability',
                param_A REAL NOT NULL,
                param_B REAL NOT NULL,
                param_C REAL NOT NULL DEFAULT 0.0,
                bootstrap_params_json TEXT NOT NULL,
                n_samples INTEGER NOT NULL,
                brier_insample REAL,
                fitted_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
                    CHECK (is_active IN (0, 1)),
                authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
                bucket_key TEXT,
                cycle TEXT NOT NULL DEFAULT '00',
                source_id TEXT NOT NULL DEFAULT 'tigge_mars',
                horizon_profile TEXT NOT NULL DEFAULT 'full',
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(temperature_metric, cluster, season, data_version,
                       input_space, is_active, cycle, source_id, horizon_profile)
            )
        """)

        conn.execute(f"INSERT INTO platt_models_v2_tmp ({COLS}) SELECT {COLS} FROM platt_models_v2")

        copied = conn.execute("SELECT COUNT(*) FROM platt_models_v2_tmp").fetchone()[0]
        assert copied == n, f"row count mismatch after INSERT: expected {n}, got {copied}"

        conn.execute("DROP TABLE platt_models_v2")
        conn.execute("ALTER TABLE platt_models_v2_tmp RENAME TO platt_models_v2")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_platt_models_v2_lookup
                ON platt_models_v2(temperature_metric, cluster, season,
                                   data_version, input_space, is_active)
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"rebuild": "complete", "rows_copied": copied, "expected": n}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions; do not apply.")
    parser.add_argument("--skip-lock-check", action="store_true",
                        help="DANGEROUS: bypass trade-daemon-locked precondition.")
    args = parser.parse_args()

    from src.state.db import get_world_connection
    conn = get_world_connection()
    conn.execute("PRAGMA busy_timeout = 30000")  # 30s wait on lock contention
    logger.info("busy_timeout=30000ms set on connection")
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

        # Blocker 3 — symmetric source_id flip on platt_models_v2
        platt_src_result = _backfill_platt_models_source_id(conn, dry_run=args.dry_run)
        logger.info("platt source_id backfill: %s", json.dumps(platt_src_result, indent=2))

        # Blocker 2 — fail-closed cycle assertion (real run only; columns absent in dry-run)
        if not args.dry_run:
            cycle_check = _assert_cycle_well_formed(conn)
            logger.info("cycle well-formed check: %s", json.dumps(cycle_check, indent=2))
            if cycle_check["status"] != "ok":
                logger.error(
                    "ABORT: %d rows with malformed cycle after backfill. sample=%s",
                    cycle_check["bad_rows"], cycle_check.get("sample"),
                )
                return 3
        else:
            cycle_check = {"status": "skipped_dry_run"}
            logger.info("cycle well-formed check: skipped (dry-run)")

        # Blocker 1 — rebuild platt_models_v2 with extended UNIQUE (runs last so
        #             it copies backfilled source_id values)
        rebuild_result = _rebuild_platt_models_v2_extended_unique(conn, dry_run=args.dry_run)
        logger.info("platt UNIQUE rebuild: %s", json.dumps(rebuild_result, indent=2))

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
            "platt_src_result": platt_src_result,
            "cycle_check": cycle_check,
            "rebuild_result": rebuild_result,
            "dry_run": args.dry_run,
        }, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
