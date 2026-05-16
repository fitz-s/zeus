# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: pf_quarantine_plan.md + critic-opus pre-review APPROVE (zero blocking findings)
#                  R-F1 full-population preservation sweep applied
#                  R-F2 explicit state machine (fresh / no-op / partial HALT)
#
# P-F quarantine runner: UPDATE 74 rows from authority='VERIFIED' to 'QUARANTINED',
# augmenting provenance_json via nested json_set to ADD (not replace) 3 new keys.
#
# Mapping source: evidence/pf_quarantine_mapping.json (74 entries, closed enumerable set)
#
# Reproducible via:
#   python3 docs/operations/.../evidence/scripts/pf_mark_quarantine.py

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
DB_PATH = REPO_ROOT / "state" / "zeus-world.db"
SNAPSHOT_PATH = REPO_ROOT / "state" / "zeus-world.db.pre-pf_2026-04-23"
SNAPSHOT_HASH_PATH = SNAPSHOT_PATH.parent / (SNAPSHOT_PATH.name + ".md5")
MAPPING_PATH = (
    REPO_ROOT
    / "docs"
    / "operations"
    / "task_2026-04-23_data_readiness_remediation"
    / "evidence"
    / "pf_quarantine_mapping.json"
)

EXPECTED_QUARANTINE_ROWS = 74
EXPECTED_TOTAL_ROWS = 1556
EXPECTED_VERIFIED_AFTER = EXPECTED_TOTAL_ROWS - EXPECTED_QUARANTINE_ROWS  # 1482

EXPECTED_REASON_COUNTS = {
    "pc_audit_source_role_collapse_no_source_correct_obs_available": 27,
    "pc_audit_shenzhen_drift_nonreproducible": 26,
    "pc_audit_dst_spring_forward_bin_mismatch": 7,
    "pc_audit_station_remap_needed_no_cwa_collector": 7,
    "pc_audit_seoul_station_drift_2026-03_through_2026-04": 5,
    "pc_audit_1unit_drift": 2,
}


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
    """Take pre-mutation snapshot; verify integrity.
    Reuses the P-B-era asymmetric check (first-run: main==snap; re-run: snap==recorded_hash).
    """
    log("WAL checkpoint (TRUNCATE)")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    if SNAPSHOT_PATH.exists():
        log(f"Snapshot exists; verifying integrity (idempotent re-run)")
        snap_hash = md5_of(SNAPSHOT_PATH)
        if SNAPSHOT_HASH_PATH.exists():
            recorded = SNAPSHOT_HASH_PATH.read_text().strip().split()[0]
            if recorded != snap_hash:
                log(f"FAIL: snap md5 {snap_hash} != recorded {recorded}")
                sys.exit(1)
            log(f"snap md5 {snap_hash} matches recorded; rollback artifact intact")
        else:
            log(f"WARN: no hash file; writing for {snap_hash}")
            SNAPSHOT_HASH_PATH.write_text(f"{snap_hash}  {SNAPSHOT_PATH.name}\n")
        return snap_hash

    log(f"cp {DB_PATH} → {SNAPSHOT_PATH}")
    shutil.copy2(DB_PATH, SNAPSHOT_PATH)
    main_hash = md5_of(DB_PATH)
    snap_hash = md5_of(SNAPSHOT_PATH)
    log(f"main md5={main_hash}")
    log(f"snap md5={snap_hash}")
    if main_hash != snap_hash:
        log("FAIL: first-run snapshot diverges from main")
        sys.exit(1)
    SNAPSHOT_HASH_PATH.write_text(f"{snap_hash}  {SNAPSHOT_PATH.name}\n")
    return main_hash


def load_mapping() -> list[dict]:
    with open(MAPPING_PATH) as f:
        mapping = json.load(f)
    log(f"loaded {len(mapping)} mapping entries from {MAPPING_PATH}")
    if len(mapping) != EXPECTED_QUARANTINE_ROWS:
        log(f"FAIL: mapping size {len(mapping)} != expected {EXPECTED_QUARANTINE_ROWS}")
        sys.exit(2)
    got_counts = Counter(e["reason_id"] for e in mapping)
    if dict(got_counts) != EXPECTED_REASON_COUNTS:
        log(f"FAIL: mapping reason distribution {dict(got_counts)} != expected {EXPECTED_REASON_COUNTS}")
        sys.exit(2)
    return mapping


def classify_state(conn: sqlite3.Connection, mapping: list[dict]) -> str:
    """Determine DB state vs mapping: fresh / noop / partial."""
    verified_keys: set[tuple[str, str]] = set()
    quarantined_keys: set[tuple[str, str]] = set()
    for e in mapping:
        row = conn.execute(
            "SELECT authority, json_extract(provenance_json, '$.quarantine_reason') "
            "FROM settlements WHERE city=? AND target_date=?",
            (e["city"], e["target_date"]),
        ).fetchone()
        if row is None:
            log(f"FAIL: mapping row {e} does not exist in DB — ABORT")
            sys.exit(2)
        authority, qr = row
        if authority == "VERIFIED":
            verified_keys.add((e["city"], e["target_date"]))
        elif authority == "QUARANTINED" and qr == e["reason_id"]:
            quarantined_keys.add((e["city"], e["target_date"]))
        else:
            log(
                f"FAIL: row {e['city']}/{e['target_date']} in unexpected state "
                f"authority={authority} quarantine_reason={qr}"
            )
            sys.exit(2)

    v, q = len(verified_keys), len(quarantined_keys)
    log(f"state classify: {v} VERIFIED + {q} QUARANTINED (expected {EXPECTED_QUARANTINE_ROWS} total)")
    if v == EXPECTED_QUARANTINE_ROWS and q == 0:
        return "fresh"
    if q == EXPECTED_QUARANTINE_ROWS and v == 0:
        return "noop"
    return "partial"


def apply_quarantine(conn: sqlite3.Connection, mapping: list[dict]) -> int:
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log(f"executing quarantine UPDATEs for {len(mapping)} rows in one transaction")

    conn.execute("BEGIN IMMEDIATE")
    try:
        total_changes = 0
        for e in mapping:
            cur = conn.execute(
                """
                UPDATE settlements
                SET authority = 'QUARANTINED',
                    provenance_json = json_set(
                        json_set(
                            json_set(provenance_json, '$.quarantine_reason', ?),
                            '$.quarantined_at', ?),
                        '$.quarantined_by_packet', ?)
                WHERE city = ? AND target_date = ? AND authority = 'VERIFIED'
                """,
                (e["reason_id"], now_iso, "P-F", e["city"], e["target_date"]),
            )
            if cur.rowcount != 1:
                log(
                    f"FAIL: UPDATE for {e['city']}/{e['target_date']} affected {cur.rowcount} rows "
                    f"(expected 1); ROLLBACK"
                )
                conn.rollback()
                sys.exit(3)
            total_changes += cur.rowcount
        log(f"all 74 UPDATEs applied (total changes={total_changes})")

        # Post-verify #1: counts
        q_count = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE authority='QUARANTINED'"
        ).fetchone()[0]
        v_count = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE authority='VERIFIED'"
        ).fetchone()[0]
        log(f"post-UPDATE counts: VERIFIED={v_count}, QUARANTINED={q_count}")
        if q_count != EXPECTED_QUARANTINE_ROWS or v_count != EXPECTED_VERIFIED_AFTER:
            log(f"FAIL: authority counts {v_count}/{q_count} != expected {EXPECTED_VERIFIED_AFTER}/{EXPECTED_QUARANTINE_ROWS}")
            conn.rollback()
            sys.exit(4)

        # Post-verify #2: per-reason partition
        rows = conn.execute(
            "SELECT json_extract(provenance_json, '$.quarantine_reason') AS r, COUNT(*) "
            "FROM settlements WHERE authority='QUARANTINED' "
            "GROUP BY 1"
        ).fetchall()
        got = {r[0]: r[1] for r in rows}
        if got != EXPECTED_REASON_COUNTS:
            log(f"FAIL: per-reason partition {got} != expected {EXPECTED_REASON_COUNTS}")
            conn.rollback()
            sys.exit(4)
        log(f"per-reason partition verified: {got}")

        # Post-verify #3 (R-F1): full-population P-A retrofit key preservation
        preserved = conn.execute(
            """
            SELECT COUNT(*) FROM settlements
            WHERE authority = 'QUARANTINED'
              AND json_extract(provenance_json, '$.writer') = 'unregistered_bulk_writer_2026-04-16'
              AND json_extract(provenance_json, '$.quarantine_reason') IS NOT NULL
            """
        ).fetchone()[0]
        log(f"R-F1 full-population preservation sweep: {preserved}/74 rows carry both P-A retrofit + quarantine keys")
        if preserved != EXPECTED_QUARANTINE_ROWS:
            log(f"FAIL: R-F1 sweep {preserved} != 74; some json_set call dropped a retrofit key")
            conn.rollback()
            sys.exit(4)

        conn.commit()
        log("COMMIT")
        return total_changes
    except sqlite3.Error as e:
        log(f"SQLite error: {e}; ROLLBACK")
        conn.rollback()
        sys.exit(5)


def main() -> int:
    log("P-F quarantine runner starting")
    log(f"REPO_ROOT={REPO_ROOT}")
    log(f"DB_PATH={DB_PATH}")

    pre_hash = take_snapshot()
    mapping = load_mapping()

    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        total_rows = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        log(f"total settlements row count: {total_rows}")
        if total_rows != EXPECTED_TOTAL_ROWS:
            log(f"FAIL: total {total_rows} != expected {EXPECTED_TOTAL_ROWS}")
            sys.exit(1)

        # R-F2 explicit state machine
        state = classify_state(conn, mapping)
        log(f"runner state: {state}")

        if state == "noop":
            log("idempotent re-run — all 74 rows already quarantined with correct reasons; exiting 0")
            print(json.dumps({
                "state": "noop",
                "pre_hash": pre_hash,
                "total_rows": total_rows,
                "quarantined": EXPECTED_QUARANTINE_ROWS,
                "verified": EXPECTED_VERIFIED_AFTER,
            }, indent=2))
            return 0

        if state == "partial":
            log("partial state detected — HALT (some rows quarantined, some not; investigate)")
            sys.exit(6)

        # state == "fresh"
        assert state == "fresh"
        changes = apply_quarantine(conn, mapping)

        summary = {
            "state": "fresh_applied",
            "pre_hash": pre_hash,
            "snapshot_path": str(SNAPSHOT_PATH),
            "total_rows": total_rows,
            "verified_count": EXPECTED_VERIFIED_AFTER,
            "quarantined_count": EXPECTED_QUARANTINE_ROWS,
            "reason_counts": EXPECTED_REASON_COUNTS,
            "update_changes": changes,
        }
        print()
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
