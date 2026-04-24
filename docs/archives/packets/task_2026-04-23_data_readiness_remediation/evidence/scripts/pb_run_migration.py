# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: pb_schema_plan.md §2 + §4 runbook + critic-opus APPROVE_WITH_CONDITIONS
#                  (C1 LIKE→json_extract applied; C2 reactivation contract documented)
#
# P-B schema migration runner: idempotent ALTER TABLE + CREATE TRIGGER + UPDATE backfill
# for settlements. Additive, NULL-preserving, no data destruction.
#
# Steps:
#   1. Pre-flight + snapshot + md5 verify
#   2. Apply 5 ALTER TABLE ADD COLUMN (each wrapped try/except OperationalError — idempotent)
#   3. CREATE TRIGGER IF NOT EXISTS settlements_authority_monotonic
#   4. Backfill provenance_json on all 1556 bulk-batch rows with the P-A retrofit JSON
#   5. Validate trigger behavior via 4 scratch-row UPDATE cases
#   6. Return exit 0 on all success, non-zero on any assertion failure
#
# Reproducible via:
#   python3 docs/operations/.../evidence/scripts/pb_run_migration.py

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
DB_PATH = REPO_ROOT / "state" / "zeus-world.db"
SNAPSHOT_PATH = REPO_ROOT / "state" / "zeus-world.db.pre-pb_2026-04-23"
SNAPSHOT_HASH_PATH = SNAPSHOT_PATH.parent / (SNAPSHOT_PATH.name + ".md5")

EXPECTED_ROW_COUNT = 1556

PROVENANCE_JSON = json.dumps(
    {
        "writer": "unregistered_bulk_writer_2026-04-16",
        "rca_doc": "docs/operations/task_2026-04-23_data_readiness_remediation/evidence/bulk_writer_rca_final.md",
        "reason": "provenance_hostile_bulk_batch",
        "inferred_source_json": "data/pm_settlement_truth.json",
        "inferred_source_json_confidence": 0.9,
        "writer_logic_inferred": (
            "settlement_value = pm_bin_lo if pm_bin_lo == pm_bin_hi else NULL; "
            "JSON sentinels -999/999 mapped to SQL NULL"
        ),
        "audit_packets": ["P-A", "P-C", "P-G"],
        "settled_at_all_rows": "2026-04-16T12:39:58.026729+00:00",
        "retrofit_date": "2026-04-23",
    },
    sort_keys=True,
)

ALTER_DDLS = [
    (
        "ALTER TABLE settlements ADD COLUMN temperature_metric TEXT "
        "CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low'))"
    ),
    "ALTER TABLE settlements ADD COLUMN physical_quantity TEXT",
    (
        "ALTER TABLE settlements ADD COLUMN observation_field TEXT "
        "CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp'))"
    ),
    "ALTER TABLE settlements ADD COLUMN data_version TEXT",
    "ALTER TABLE settlements ADD COLUMN provenance_json TEXT",
]

TRIGGER_DDL = """
CREATE TRIGGER IF NOT EXISTS settlements_authority_monotonic
BEFORE UPDATE OF authority ON settlements
WHEN (OLD.authority = 'VERIFIED' AND NEW.authority = 'UNVERIFIED')
  OR (OLD.authority = 'QUARANTINED' AND NEW.authority = 'VERIFIED'
      AND (NEW.provenance_json IS NULL
           OR json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'settlements.authority transition forbidden: VERIFIED->UNVERIFIED blocked, or QUARANTINED->VERIFIED missing provenance_json.reactivated_by');
END;
"""


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{ts}] {msg}", flush=True)


def take_snapshot() -> str:
    """Ensure a pre-migration snapshot + its md5 file exist. Idempotent.

    Case A (first run): cp DB → snapshot, record md5, verify main == snap.
    Case B (re-run after partial migration): snapshot already exists as the
        pre-mutation baseline. Verify the snapshot's own md5 still matches its
        recorded hash file (i.e., the rollback artifact is intact). DO NOT
        require main == snap, because main has been partially mutated by the
        first run and that is the expected state for re-run recovery.
    """
    log("WAL checkpoint (TRUNCATE)")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    if SNAPSHOT_PATH.exists():
        log(f"Snapshot already exists at {SNAPSHOT_PATH} — verifying integrity only (idempotent re-run)")
        snap_hash = md5_of(SNAPSHOT_PATH)
        if SNAPSHOT_HASH_PATH.exists():
            recorded = SNAPSHOT_HASH_PATH.read_text().strip().split()[0]
            if recorded != snap_hash:
                log(f"FAIL: snapshot md5 {snap_hash} != recorded {recorded}; snapshot corrupted")
                sys.exit(1)
            log(f"snapshot md5 {snap_hash} matches recorded hash; rollback artifact intact")
        else:
            log(f"WARN: no recorded hash file; writing fresh one for {snap_hash}")
            SNAPSHOT_HASH_PATH.write_text(f"{snap_hash}  {SNAPSHOT_PATH.name}\n")
        return snap_hash

    # First run: take the snapshot + record hash
    log(f"cp {DB_PATH} → {SNAPSHOT_PATH}")
    shutil.copy2(DB_PATH, SNAPSHOT_PATH)
    main_hash = md5_of(DB_PATH)
    snap_hash = md5_of(SNAPSHOT_PATH)
    log(f"main md5={main_hash}")
    log(f"snap md5={snap_hash}")
    if main_hash != snap_hash:
        log("FAIL: snapshot copy diverges from main (first-run integrity check)")
        sys.exit(1)
    SNAPSHOT_HASH_PATH.write_text(f"{snap_hash}  {SNAPSHOT_PATH.name}\n")
    return main_hash


def apply_ddls(conn: sqlite3.Connection) -> dict:
    """Apply the 5 ALTER TABLE DDL + 1 TRIGGER. Idempotent via try/except."""
    results: dict[str, str] = {}
    for ddl in ALTER_DDLS:
        short = ddl.split("ADD COLUMN ", 1)[1].split(" ", 1)[0]
        try:
            conn.execute(ddl)
            results[short] = "added"
            log(f"ALTER: added column {short}")
        except sqlite3.OperationalError as e:
            msg = str(e)
            if "duplicate column" in msg.lower():
                results[short] = "already_exists"
                log(f"ALTER: {short} already present (idempotent)")
            else:
                log(f"ALTER FAIL {short}: {msg}")
                raise
    try:
        conn.execute(TRIGGER_DDL)
        results["settlements_authority_monotonic"] = "created_or_present"
        log("TRIGGER: settlements_authority_monotonic created (or already existed)")
    except sqlite3.OperationalError as e:
        log(f"TRIGGER FAIL: {e}")
        raise
    return results


def backfill_provenance(conn: sqlite3.Connection) -> int:
    """UPDATE all rows with provenance_json IS NULL. Assertion-guarded."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        pre_null = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE provenance_json IS NULL"
        ).fetchone()[0]
        log(f"backfill pre-verify: {pre_null} rows with provenance_json IS NULL")
        total = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        if total != EXPECTED_ROW_COUNT:
            log(f"FAIL: total row count {total} != expected {EXPECTED_ROW_COUNT}; ROLLBACK")
            conn.rollback()
            sys.exit(2)
        if pre_null != EXPECTED_ROW_COUNT:
            # Partial re-run — only update the NULL ones, but warn.
            log(
                f"WARN: pre_null ({pre_null}) != total ({total}); "
                f"proceeding to fill remaining NULLs only"
            )

        cur = conn.execute(
            "UPDATE settlements SET provenance_json = ? WHERE provenance_json IS NULL",
            (PROVENANCE_JSON,),
        )
        changes = cur.rowcount
        log(f"UPDATE changes()={changes}")

        post_null = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE provenance_json IS NULL"
        ).fetchone()[0]
        log(f"backfill post-verify: {post_null} rows with provenance_json IS NULL (expected 0)")
        if post_null != 0:
            log("FAIL: post-backfill still has NULL rows; ROLLBACK")
            conn.rollback()
            sys.exit(3)

        conn.commit()
        log("COMMIT backfill")
        return changes
    except sqlite3.Error as e:
        log(f"SQLite error in backfill: {e}; ROLLBACK")
        conn.rollback()
        sys.exit(5)


def validate_trigger(conn: sqlite3.Connection) -> list[dict]:
    """Run 4 trigger test cases on a scratch row. Scratch row created + deleted in one transaction."""
    log("=== TRIGGER VALIDATION ===")
    conn.execute("BEGIN IMMEDIATE")
    cases = []
    try:
        # Create scratch row with authority='VERIFIED'
        conn.execute(
            "INSERT INTO settlements (city, target_date, authority) VALUES (?, ?, ?)",
            ("_pb_trigger_scratch", "2026-04-23T00:00:00", "VERIFIED"),
        )
        scratch_id = conn.execute(
            "SELECT id FROM settlements WHERE city = '_pb_trigger_scratch'"
        ).fetchone()[0]
        log(f"scratch row inserted id={scratch_id}")

        def attempt(label: str, sql: str, params: tuple, should_abort: bool) -> dict:
            try:
                conn.execute(sql, params)
                if should_abort:
                    log(f"  {label}: UNEXPECTED SUCCESS (should have ABORTed) — FAIL")
                    return {"case": label, "expected": "ABORT", "actual": "SUCCESS", "pass": False}
                log(f"  {label}: SUCCESS (as expected)")
                return {"case": label, "expected": "SUCCESS", "actual": "SUCCESS", "pass": True}
            except sqlite3.IntegrityError as e:
                if should_abort:
                    log(f"  {label}: ABORT (as expected) — {e}")
                    return {"case": label, "expected": "ABORT", "actual": "ABORT", "pass": True,
                            "error_msg": str(e)}
                log(f"  {label}: UNEXPECTED ABORT (should have succeeded) — {e}")
                return {"case": label, "expected": "SUCCESS", "actual": "ABORT", "pass": False,
                        "error_msg": str(e)}

        # Case 1: VERIFIED → UNVERIFIED (expect ABORT)
        cases.append(attempt(
            "V→U should ABORT",
            "UPDATE settlements SET authority='UNVERIFIED' WHERE id=?",
            (scratch_id,), should_abort=True,
        ))

        # Case 2: VERIFIED → QUARANTINED (expect SUCCESS; downgrade allowed)
        cases.append(attempt(
            "V→Q should SUCCEED",
            "UPDATE settlements SET authority='QUARANTINED' WHERE id=?",
            (scratch_id,), should_abort=False,
        ))

        # Case 3: QUARANTINED → VERIFIED without marker (expect ABORT)
        cases.append(attempt(
            "Q→V without marker should ABORT",
            "UPDATE settlements SET authority='VERIFIED', provenance_json='{}' WHERE id=?",
            (scratch_id,), should_abort=True,
        ))

        # Case 4: QUARANTINED → VERIFIED with marker (expect SUCCESS)
        cases.append(attempt(
            "Q→V with reactivated_by marker should SUCCEED",
            ("UPDATE settlements SET authority='VERIFIED', "
             "provenance_json='{\"reactivated_by\":\"trigger_validation_scratch\"}' WHERE id=?"),
            (scratch_id,), should_abort=False,
        ))

        # Delete scratch row (always)
        conn.execute("DELETE FROM settlements WHERE id=?", (scratch_id,))
        conn.commit()
        log("scratch row cleaned up; COMMIT validation txn")

        all_pass = all(c["pass"] for c in cases)
        if not all_pass:
            log("FAIL: one or more trigger cases failed")
            sys.exit(4)
        return cases
    except Exception as e:
        log(f"Unexpected error in trigger validation: {e}; ROLLBACK")
        conn.rollback()
        sys.exit(5)


def main() -> int:
    log("P-B migration runner starting")
    log(f"REPO_ROOT={REPO_ROOT}")
    log(f"DB_PATH={DB_PATH}")

    pre_main_hash = take_snapshot()

    # Use isolation_level=None for manual BEGIN/COMMIT control.
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row

    try:
        pre_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        log(f"pre-mutation settlements row count: {pre_count}")
        if pre_count != EXPECTED_ROW_COUNT:
            log(f"FAIL: pre-count {pre_count} != expected {EXPECTED_ROW_COUNT}; HALT")
            sys.exit(1)

        ddl_results = apply_ddls(conn)
        changes = backfill_provenance(conn)
        trigger_cases = validate_trigger(conn)

        post_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        post_null = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE provenance_json IS NULL"
        ).fetchone()[0]
        log(f"post-migration row count: {post_count}")
        log(f"post-migration rows with provenance_json IS NULL: {post_null}")
        if post_count != EXPECTED_ROW_COUNT:
            log(f"FAIL: post-count {post_count} != expected {EXPECTED_ROW_COUNT}")
            sys.exit(4)
        if post_null != 0:
            log(f"FAIL: {post_null} rows still NULL after backfill")
            sys.exit(4)

        summary = {
            "pre_main_hash": pre_main_hash,
            "pre_count": pre_count,
            "post_count": post_count,
            "post_null_count": post_null,
            "backfill_changes": changes,
            "ddl_results": ddl_results,
            "trigger_validation": trigger_cases,
            "snapshot_path": str(SNAPSHOT_PATH),
            "provenance_json_used": json.loads(PROVENANCE_JSON),
        }
        print()
        print(json.dumps(summary, indent=2, default=str))
        return 0
    except sqlite3.Error as e:
        log(f"SQLite error: {e}")
        sys.exit(5)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
