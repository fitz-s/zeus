# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: P-G pg_corrections_plan.md §2; critic-opus pre-review APPROVE 2026-04-23
#                  + F1 (explicit ROLLBACK on mismatch) + snapshot-hash verification hazard
#
# P-G mutation runner: DELETE 6 rows with assertion-guarded transactions.
#   TXN 1: Denver 2026-04-15 (synthetic orphan per P-D §9.1)
#   TXN 2: London/NYC/Seoul/Tokyo/Shanghai 2026-04-15 (LOW-market metric contamination)
#
# Each transaction:
#   1. SELECT pre: verify expected rows exist
#   2. DELETE: execute
#   3. SELECT changes(): assert row count matches expected
#   4. SELECT post: assert 0 rows remain for deleted keys
#   5. COMMIT on success, ROLLBACK on any assertion failure
#
# Snapshot safety:
#   - PRAGMA wal_checkpoint(TRUNCATE) flushes WAL into main DB
#   - cp zeus-world.db → zeus-world.db.pre-pg_2026-04-23
#   - md5 hash verification before execution
#
# Reproducible from a clean state via:
#   python3 docs/operations/.../evidence/scripts/pg_delete_wrongmetric_and_synthetic.py
#
# Exit codes: 0=success; 1=snapshot/hash failure; 2=pre-verify mismatch;
#             3=DELETE changes() mismatch; 4=post-verify mismatch; 5=SQLite error

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
DB_PATH = REPO_ROOT / "state" / "zeus-world.db"
SNAPSHOT_PATH = REPO_ROOT / "state" / "zeus-world.db.pre-pg_2026-04-23"
# Explicit concatenation — `Path.with_suffix()` replaces everything after the
# last dot, which would yield `zeus-world.db.db.pre-pg_2026-04-23.md5` because
# `.pre-pg_2026-04-23` reads as the existing suffix (F1-POST critic finding).
SNAPSHOT_HASH_PATH = SNAPSHOT_PATH.parent / (SNAPSHOT_PATH.name + ".md5")


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
    """Flush WAL, cp DB, compute + record hash. Returns the main DB hash."""
    log(f"Connecting to {DB_PATH} for WAL checkpoint")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    if SNAPSHOT_PATH.exists():
        log(f"Snapshot already exists at {SNAPSHOT_PATH} — keeping existing snapshot for idempotency")
    else:
        log(f"Copying {DB_PATH} → {SNAPSHOT_PATH}")
        shutil.copy2(DB_PATH, SNAPSHOT_PATH)

    main_hash = md5_of(DB_PATH)
    snap_hash = md5_of(SNAPSHOT_PATH)
    log(f"main md5={main_hash}")
    log(f"snap md5={snap_hash}")
    if main_hash != snap_hash:
        log("FAIL: main and snapshot hashes differ before mutation")
        sys.exit(1)
    # Record hash file for future audit
    SNAPSHOT_HASH_PATH.write_text(f"{snap_hash}  {SNAPSHOT_PATH.name}\n")
    return main_hash


def run_txn(
    conn: sqlite3.Connection,
    *,
    txn_name: str,
    pre_select: tuple[str, tuple],
    expected_rows: int,
    delete_sql: tuple[str, tuple],
    post_select: tuple[str, tuple],
) -> dict:
    """Execute one atomic DELETE transaction with full pre/post verification.

    The transaction begins BEFORE the pre-verify SELECT so that any concurrent
    writer is serialized against IMMEDIATE-lock semantics for the duration of
    the DELETE decision. ROLLBACK is used on any mismatch so no partial state
    lands in the DB.
    """
    log(f"=== {txn_name} ===")
    # Open IMMEDIATE transaction first (acquires RESERVED lock) so pre-verify
    # sees a stable snapshot and no other writer can interleave.
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Pre-verify INSIDE the transaction
        cur = conn.execute(pre_select[0], pre_select[1])
        pre_rows = cur.fetchall()
        log(f"pre-verify: {len(pre_rows)} rows found (expected {expected_rows})")
        for r in pre_rows:
            log(f"  pre-row: {dict(r)}")
        if len(pre_rows) != expected_rows:
            log(f"FAIL: pre-verify count mismatch ({len(pre_rows)} vs {expected_rows}); ROLLBACK")
            conn.rollback()
            sys.exit(2)

        # Execute DELETE
        cur = conn.execute(delete_sql[0], delete_sql[1])
        changes = cur.rowcount
        log(f"DELETE changes()={changes} (expected {expected_rows})")
        if changes != expected_rows:
            log(f"FAIL: DELETE row count mismatch; ROLLBACK")
            conn.rollback()
            sys.exit(3)

        # Post-verify still inside the transaction
        cur = conn.execute(post_select[0], post_select[1])
        remaining = cur.fetchone()[0]
        log(f"post-verify (inside txn): {remaining} rows remain (expected 0)")
        if remaining != 0:
            log(f"FAIL: post-verify non-zero remaining; ROLLBACK")
            conn.rollback()
            sys.exit(4)

        conn.commit()
        log(f"COMMIT {txn_name}")
        return {"txn": txn_name, "changes": changes, "pre_rows": [dict(r) for r in pre_rows]}
    except sqlite3.Error as e:
        log(f"SQLite error inside {txn_name}: {e}; ROLLBACK")
        conn.rollback()
        sys.exit(5)


def main() -> int:
    log(f"P-G delete runner starting")
    log(f"REPO_ROOT={REPO_ROOT}")
    log(f"DB_PATH={DB_PATH}")

    pre_main_hash = take_snapshot()

    # Pre-mutation total count for sanity. isolation_level=None puts sqlite3
    # in manual-BEGIN mode so our BEGIN IMMEDIATE is honored.
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    pre_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    log(f"pre-mutation settlements row count: {pre_count}")
    if pre_count != 1562:
        log(f"FAIL: unexpected pre-count {pre_count} (expected 1562); HALT — another agent may have mutated the table")
        conn.close()
        sys.exit(1)

    try:
        results = []
        # TXN 1: Denver
        results.append(
            run_txn(
                conn,
                txn_name="TXN1_denver_synthetic",
                pre_select=(
                    "SELECT id, city, target_date, pm_bin_lo, pm_bin_hi, unit, settlement_source_type "
                    "FROM settlements WHERE city = ? AND target_date = ?",
                    ("Denver", "2026-04-15"),
                ),
                expected_rows=1,
                delete_sql=(
                    "DELETE FROM settlements WHERE city = ? AND target_date = ?",
                    ("Denver", "2026-04-15"),
                ),
                post_select=(
                    "SELECT COUNT(*) FROM settlements WHERE city = ? AND target_date = ?",
                    ("Denver", "2026-04-15"),
                ),
            )
        )

        # TXN 2: 5 LOW-metric-contaminated rows
        city_list = ("London", "NYC", "Seoul", "Tokyo", "Shanghai")
        placeholders = ",".join("?" * len(city_list))
        results.append(
            run_txn(
                conn,
                txn_name="TXN2_low_market_contamination",
                pre_select=(
                    f"SELECT id, city, target_date, pm_bin_lo, pm_bin_hi, unit, settlement_source_type "
                    f"FROM settlements WHERE target_date = ? AND city IN ({placeholders}) "
                    f"ORDER BY city",
                    ("2026-04-15", *city_list),
                ),
                expected_rows=5,
                delete_sql=(
                    f"DELETE FROM settlements WHERE target_date = ? AND city IN ({placeholders})",
                    ("2026-04-15", *city_list),
                ),
                post_select=(
                    f"SELECT COUNT(*) FROM settlements WHERE target_date = ? AND city IN ({placeholders})",
                    ("2026-04-15", *city_list),
                ),
            )
        )

        # Post-mutation row count
        post_count = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        log(f"post-mutation settlements row count: {post_count} (expected 1556)")
        if post_count != 1556:
            log(f"FAIL: unexpected post-count {post_count} (expected 1556)")
            sys.exit(4)

        log(f"P-G DELETE phase complete. Rows removed: {pre_count - post_count}")
        summary = {
            "pre_count": pre_count,
            "post_count": post_count,
            "pre_main_hash": pre_main_hash,
            "snapshot_path": str(SNAPSHOT_PATH),
            "transactions": results,
        }
        print()
        print(json.dumps(summary, indent=2, default=str))
        return 0

    except sqlite3.Error as e:
        log(f"SQLite error: {e}")
        conn.rollback()
        sys.exit(5)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
