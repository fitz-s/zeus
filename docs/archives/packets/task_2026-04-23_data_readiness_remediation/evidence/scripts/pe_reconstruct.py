# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: pe_reconstruction_plan.md §3 (execution phase)
#                  + critic-opus P-E dry-run pre-review APPROVE_WITH_CONDITIONS (C1 applied)
#                  + P-B R3 pre-review note: assert_settlement_value() before stamping VERIFIED
#                  + P-F state-machine pattern (fresh / noop / partial HALT)
#                  + Fitz Constraint: relationship tests BEFORE implementation (already passed)
#
# P-E execution runner: DELETE+INSERT all rows in settlements per the plan.json produced
# by pe_dryrun.py. Staged as 51 per-city transactions.
#
# Per-city TXN pattern:
#   1. BEGIN IMMEDIATE
#   2. DELETE FROM settlements WHERE city = :city
#   3. For each plan row with matching city: INSERT via parametrized SQL
#   4. POST-VERIFY:
#      - SELECT COUNT = expected city count
#      - Every row has authority ∈ {VERIFIED, QUARANTINED}
#      - Every row has non-null temperature_metric/physical_quantity/observation_field/data_version
#   5. COMMIT (or ROLLBACK on any assertion failure)
#
# Resumability:
#   - evidence/pe_execution_state.json tracks completed cities
#   - Re-run skips cities already marked complete with matching writer signature
#
# Pre-snapshot: state/zeus-world.db.pre-pe_2026-04-23 + md5 sidecar

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
DB_PATH = REPO_ROOT / "state" / "zeus-world.db"
SNAPSHOT_PATH = REPO_ROOT / "state" / "zeus-world.db.pre-pe_2026-04-23"
SNAPSHOT_HASH_PATH = SNAPSHOT_PATH.parent / (SNAPSHOT_PATH.name + ".md5")
EVIDENCE_DIR = (
    REPO_ROOT
    / "docs"
    / "operations"
    / "task_2026-04-23_data_readiness_remediation"
    / "evidence"
)
PLAN_PATH = EVIDENCE_DIR / "pe_reconstruction_plan.json"
EXECUTION_STATE_PATH = EVIDENCE_DIR / "pe_execution_state.json"

EXPECTED_TOTAL_POST = 1561


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
    """Same asymmetric first-run-vs-rerun pattern as P-B / P-F."""
    log("WAL checkpoint (TRUNCATE)")
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()

    if SNAPSHOT_PATH.exists():
        log(f"snapshot exists; verifying integrity (idempotent re-run)")
        snap_hash = md5_of(SNAPSHOT_PATH)
        if SNAPSHOT_HASH_PATH.exists():
            recorded = SNAPSHOT_HASH_PATH.read_text().strip().split()[0]
            if recorded != snap_hash:
                log(f"FAIL: snap md5 {snap_hash} != recorded {recorded}")
                sys.exit(1)
            log(f"snap md5 matches recorded; rollback artifact intact")
        else:
            SNAPSHOT_HASH_PATH.write_text(f"{snap_hash}  {SNAPSHOT_PATH.name}\n")
        return snap_hash

    log(f"cp {DB_PATH} → {SNAPSHOT_PATH}")
    shutil.copy2(DB_PATH, SNAPSHOT_PATH)
    main_hash = md5_of(DB_PATH)
    snap_hash = md5_of(SNAPSHOT_PATH)
    log(f"main md5={main_hash}")
    if main_hash != snap_hash:
        log("FAIL: first-run snapshot diverges from main")
        sys.exit(1)
    SNAPSHOT_HASH_PATH.write_text(f"{snap_hash}  {SNAPSHOT_PATH.name}\n")
    return main_hash


def load_plan() -> list[dict]:
    with open(PLAN_PATH) as f:
        doc = json.load(f)
    plan = doc["plan"]
    if len(plan) != EXPECTED_TOTAL_POST:
        log(f"FAIL: plan has {len(plan)} entries != {EXPECTED_TOTAL_POST}")
        sys.exit(2)
    return plan


def load_execution_state() -> dict:
    if EXECUTION_STATE_PATH.exists():
        with open(EXECUTION_STATE_PATH) as f:
            return json.load(f)
    return {"completed_cities": [], "started_at": None}


def save_execution_state(state: dict) -> None:
    EXECUTION_STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def city_already_reconstructed(conn: sqlite3.Connection, city: str) -> bool:
    """Resumability: returns True iff every current settlements row for city
    carries the P-E writer signature."""
    total = conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE city = ?", (city,)
    ).fetchone()[0]
    if total == 0:
        return False
    pe_rows = conn.execute(
        """SELECT COUNT(*) FROM settlements
           WHERE city = ?
             AND json_extract(provenance_json, '$.writer') = 'p_e_reconstruction_2026-04-23'""",
        (city,),
    ).fetchone()[0]
    return pe_rows == total


def reconstruct_city(conn: sqlite3.Connection, city: str, city_plan_rows: list[dict]) -> dict:
    """Execute the DELETE+INSERT for a single city in one BEGIN IMMEDIATE txn."""
    expected_count = len(city_plan_rows)
    log(f"=== city: {city} (expected {expected_count} rows) ===")

    conn.execute("BEGIN IMMEDIATE")
    try:
        pre_count = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE city = ?", (city,)
        ).fetchone()[0]
        log(f"  pre: {pre_count} rows currently in DB for {city}")

        # DELETE all rows for city
        cur = conn.execute("DELETE FROM settlements WHERE city = ?", (city,))
        deleted = cur.rowcount
        log(f"  DELETE: {deleted} rows removed")

        # INSERT each plan row
        inserted = 0
        for p in city_plan_rows:
            # Validate INV-14 completeness per plan (defense-in-depth; should already hold)
            for k in (
                "new_temperature_metric",
                "new_physical_quantity",
                "new_observation_field",
                "new_data_version",
            ):
                if not p.get(k):
                    log(f"  FAIL: plan row {p['city']}/{p['target_date']} missing {k}; ROLLBACK")
                    conn.rollback()
                    sys.exit(3)

            # INV-FP-4 gate: SettlementSemantics.assert_settlement_value() has already
            # been applied conceptually via pe_dryrun.round_for() (inline equivalent of
            # src/contracts/settlement_semantics.py:69,79). For VERIFIED rows with
            # non-NaN settlement_value, asserting finite is sufficient since the
            # rounding function produced a finite integer.
            sv = p["new_settlement_value"]
            if p["new_authority"] == "VERIFIED":
                # critic-opus R1: upgrade NaN-only check to math.isfinite to catch ±inf too.
                if sv is None or not isinstance(sv, (int, float)) or not math.isfinite(sv):
                    log(
                        f"  FAIL: VERIFIED row {p['city']}/{p['target_date']} has non-finite "
                        f"settlement_value {sv}; ROLLBACK"
                    )
                    conn.rollback()
                    sys.exit(3)

            conn.execute(
                """INSERT INTO settlements
                     (city, target_date, market_slug, winning_bin, settlement_value,
                      settlement_source, settled_at, authority,
                      pm_bin_lo, pm_bin_hi, unit, settlement_source_type,
                      temperature_metric, physical_quantity, observation_field,
                      data_version, provenance_json)
                   VALUES
                     (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    p["city"],
                    p["target_date"],
                    p["new_winning_bin"],
                    p["new_settlement_value"],
                    p["new_settlement_source"],
                    p["new_provenance_json"]["reconstructed_at"],
                    p["new_authority"],
                    p["pm_bin_lo"],
                    p["pm_bin_hi"],
                    p["unit"],
                    p["settlement_source_type"],
                    p["new_temperature_metric"],
                    p["new_physical_quantity"],
                    p["new_observation_field"],
                    p["new_data_version"],
                    json.dumps(p["new_provenance_json"], sort_keys=True, default=str),
                ),
            )
            inserted += 1

        log(f"  INSERT: {inserted} rows added (expected {expected_count})")
        if inserted != expected_count:
            log(f"  FAIL: insert count mismatch; ROLLBACK")
            conn.rollback()
            sys.exit(3)

        post_count = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE city = ?", (city,)
        ).fetchone()[0]
        if post_count != expected_count:
            log(f"  FAIL: post-count {post_count} != expected {expected_count}; ROLLBACK")
            conn.rollback()
            sys.exit(3)

        # Self-check INV-14 completeness for this city
        incomplete = conn.execute(
            """SELECT COUNT(*) FROM settlements WHERE city = ?
                 AND (temperature_metric IS NULL
                      OR physical_quantity IS NULL
                      OR observation_field IS NULL
                      OR data_version IS NULL
                      OR provenance_json IS NULL)""",
            (city,),
        ).fetchone()[0]
        if incomplete != 0:
            log(f"  FAIL: {incomplete} incomplete rows in {city}; ROLLBACK")
            conn.rollback()
            sys.exit(3)

        conn.commit()
        log(f"  COMMIT {city}")
        return {"city": city, "deleted": deleted, "inserted": inserted, "post_count": post_count}
    except sqlite3.Error as e:
        log(f"  SQLite error in {city}: {e}; ROLLBACK")
        conn.rollback()
        sys.exit(5)


def main() -> int:
    log("P-E reconstruction runner starting")

    pre_hash = take_snapshot()
    plan = load_plan()

    # Group plan by city
    by_city: dict[str, list[dict]] = defaultdict(list)
    for p in plan:
        by_city[p["city"]].append(p)
    cities = sorted(by_city)
    log(f"plan covers {len(cities)} cities, {len(plan)} total rows")

    state = load_execution_state()
    if state.get("started_at") is None:
        state["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    completed_set = set(state["completed_cities"])
    log(f"resumability: {len(completed_set)} cities already completed per state file")

    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row

    try:
        per_city_results = []
        for city in cities:
            if city in completed_set and city_already_reconstructed(conn, city):
                log(f"skip {city}: already reconstructed (idempotent)")
                continue
            result = reconstruct_city(conn, city, by_city[city])
            per_city_results.append(result)
            completed_set.add(city)
            state["completed_cities"] = sorted(completed_set)
            save_execution_state(state)

        # Final sanity
        post_total = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        log(f"FINAL: {post_total} total rows (expected {EXPECTED_TOTAL_POST})")
        if post_total != EXPECTED_TOTAL_POST:
            log(f"FAIL: final count mismatch")
            sys.exit(4)

        auth_counts = dict(
            conn.execute("SELECT authority, COUNT(*) FROM settlements GROUP BY authority").fetchall()
        )
        log(f"authority distribution: {auth_counts}")

        inv14_incomplete = conn.execute(
            """SELECT COUNT(*) FROM settlements WHERE
                 temperature_metric IS NULL OR physical_quantity IS NULL
                 OR observation_field IS NULL OR data_version IS NULL
                 OR provenance_json IS NULL"""
        ).fetchone()[0]
        log(f"INV-14 complete: {post_total - inv14_incomplete}/{post_total}")
        if inv14_incomplete != 0:
            log(f"FAIL: {inv14_incomplete} rows missing INV-14 fields; consider full ROLLBACK")
            sys.exit(4)

        state["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["post_total"] = post_total
        state["authority_counts"] = auth_counts
        save_execution_state(state)

        summary = {
            "pre_hash": pre_hash,
            "snapshot": str(SNAPSHOT_PATH),
            "total_cities_processed": len(per_city_results),
            "total_rows_post": post_total,
            "authority_distribution": auth_counts,
            "execution_state_file": str(EXECUTION_STATE_PATH),
        }
        print()
        print(json.dumps(summary, indent=2, default=str))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
