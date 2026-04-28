#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
#                  + 2026-04-28 runtime-flow audit:
#                    - rebuild_calibration_pairs_v2.py:167-193 SELECTs
#                      city, target_date, high_temp/low_temp, unit, authority,
#                      source FROM observations WHERE authority='VERIFIED'.
#                      provenance_metadata is NOT in the SELECT, but Gates 3+4
#                      block the rebuild because audit-trail completeness is a
#                      separate contract.
#                    - daily_observation_writer.write_daily_observation_with_revision
#                      preserves existing rows by design (audit-trail protection),
#                      so it CANNOT fill empty provenance in-place. This script is
#                      the targeted UPDATE-only counterpart for the audit-trail
#                      gap on legitimately-VERIFIED rows.
"""Fill empty provenance_metadata on existing observations rows (Gate 3+4 fix).

PROBLEM
-------
39,431 rows in `observations` with:
  - source = 'wu_icao_history' (canonical WU)
  - authority = 'VERIFIED'
  - high_temp + low_temp populated (training-input fields complete)
  - provenance_metadata = NULL or '' or '{}'  (audit gap → Gate 3+4 BLOCKER)

The values are correct (training pipeline reads them and uses them); only the
audit trail is missing. Gate 3+4 in
`scripts/verify_truth_surfaces.py::build_calibration_pair_rebuild_preflight_report`
blocks live calibration rebuild because the provenance contract is part of zeus
data governance (Constraint #4 — provenance > correctness).

SEMANTICS — why this is NOT the demolished synthetic-provenance pattern
-----------------------------------------------------------------------
The earlier session's `enrich_observation_instants_v2_provenance.py.BROKEN-DO-
NOT-RUN` synthesized provenance from row contents (sha256 of canonical row digest,
fabricated `source_url`, etc) — operator banned (Constraint #4 violation).
This script is fundamentally different:

  1. REQUIRES live WU API fetch (real source data)
  2. VERIFIES that existing high_temp/low_temp match WU API response within
     `--tolerance` degrees (default 0.5)
  3. ON match: UPDATEs ONLY `provenance_metadata` field; high_temp, low_temp,
     authority, source are UNTOUCHED
  4. ON mismatch: writes row to quarantine_log JSON, does NOT update provenance
  5. NO fallback heuristic; NO synthesis; if WU API unavailable, skip

This satisfies the contract: provenance points to a real, re-fetchable WU API
call whose payload-hash and source_url are computed from the actual response
bytes — same shape that `backfill_wu_daily_all.py::_build_wu_daily_provenance`
produces for new rows.

WHY a separate script (vs reusing backfill_wu_daily_all.py)
-----------------------------------------------------------
`backfill_wu_daily_all.py` calls `daily_observation_writer.
write_daily_observation_with_revision`, which for existing rows with different
payload hash inserts a `daily_observation_revisions` audit record but does NOT
update the main `observations` row. That preservation is correct for normal
re-ingest (don't overwrite settled data), but it means provenance gaps cannot
be filled via that path. This script targets the empty-provenance subset
explicitly with a row-id UPDATE.

USAGE
-----
Dry-run all (default; no DB writes):

    python -m docs.operations.task_2026-04-28_obs_provenance_preflight.scripts.fill_observations_provenance_existing

Single city + small batch (validation):

    ... --cities Chicago --limit 30

Live apply (requires explicit flag):

    ... --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# Reuse vetted helpers from backfill_wu_daily_all.py — provenance shape and
# WU API call signature must stay byte-identical to the new-row path.
from backfill_wu_daily_all import (  # noqa: E402
    CITY_STATIONS,
    _build_wu_daily_provenance,
    _fetch_wu_icao_daily_highs_lows,
)
from src.config import cities_by_name  # noqa: E402


def _resolve_zeus_db_path() -> Path:
    """Resolve zeus-world.db. In a worktree the local state/ is a 0-byte
    placeholder; the real DB lives in the parent zeus dir."""
    candidate = PROJECT_ROOT / "state" / "zeus-world.db"
    if candidate.exists() and candidate.stat().st_size > 1_000_000:
        return candidate
    parts = PROJECT_ROOT.parts
    if ".claude" in parts:
        idx = parts.index(".claude")
        zeus_root = Path(*parts[:idx])
        real = zeus_root / "state" / "zeus-world.db"
        if real.exists() and real.stat().st_size > 1_000_000:
            return real
    return candidate


DB_PATH = _resolve_zeus_db_path()
SNAPSHOT_PATH = DB_PATH.parent / f"{DB_PATH.name}.pre-gate34-fill-2026-04-28"
DEFAULT_TOLERANCE = 0.5
DEFAULT_SLEEP_SEC = 0.5
DEFAULT_CHUNK_DAYS = 31
QUARANTINE_LOG_DIR = (
    PROJECT_ROOT
    / "docs"
    / "operations"
    / "task_2026-04-28_obs_provenance_preflight"
    / "evidence"
)


def _find_gap_rows(
    conn: sqlite3.Connection,
    city: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[sqlite3.Row]:
    """Return observations rows missing provenance_metadata for WU VERIFIED.

    Optional `start_date` / `end_date` (ISO 'YYYY-MM-DD') filter gap rows by
    target_date inclusive on each end.
    """
    where = (
        "source='wu_icao_history' AND authority='VERIFIED' "
        "AND high_temp IS NOT NULL AND low_temp IS NOT NULL "
        "AND (provenance_metadata IS NULL "
        "     OR TRIM(provenance_metadata)='' "
        "     OR TRIM(provenance_metadata)='{}')"
    )
    sql = f"SELECT id, city, target_date, high_temp, low_temp, unit FROM observations WHERE {where}"
    params: list = []
    if city is not None:
        sql += " AND city = ?"
        params.append(city)
    if start_date is not None:
        sql += " AND target_date >= ?"
        params.append(start_date)
    if end_date is not None:
        sql += " AND target_date <= ?"
        params.append(end_date)
    sql += " ORDER BY city, target_date"
    return conn.execute(sql, tuple(params)).fetchall()


def _group_by_chunks(rows, chunk_days: int) -> list[tuple[str, list]]:
    """Group rows by city, then by contiguous chunk_days windows (for batched API calls)."""
    by_city: dict[str, list] = {}
    for r in rows:
        by_city.setdefault(r["city"], []).append(r)
    groups: list[tuple[str, list]] = []
    for city_name, city_rows in by_city.items():
        city_rows.sort(key=lambda r: r["target_date"])
        if not city_rows:
            continue
        chunk_start = date.fromisoformat(city_rows[0]["target_date"])
        chunk_end = chunk_start + timedelta(days=chunk_days - 1)
        bucket: list = []
        for r in city_rows:
            d = date.fromisoformat(r["target_date"])
            if d > chunk_end:
                if bucket:
                    groups.append((city_name, bucket))
                chunk_start = d
                chunk_end = chunk_start + timedelta(days=chunk_days - 1)
                bucket = []
            bucket.append(r)
        if bucket:
            groups.append((city_name, bucket))
    return groups


def main() -> int:
    p = argparse.ArgumentParser(description="Fill empty provenance_metadata on existing observations rows (Gate 3+4 fix).")
    p.add_argument("--cities", nargs="+", default=None, help="restrict to named cities")
    p.add_argument("--start-date", default=None, help="ISO YYYY-MM-DD; only fill gap rows with target_date >= start-date")
    p.add_argument("--end-date", default=None, help="ISO YYYY-MM-DD; only fill gap rows with target_date <= end-date")
    p.add_argument("--apply", action="store_true", help="execute UPDATEs (default: dry-run, no writes)")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE, help="degrees tolerance for high/low match (default 0.5)")
    p.add_argument("--limit", type=int, default=None, help="cap row count for testing")
    p.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS, help="API call window size in days (default 31)")
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC, help="seconds between WU API calls (default 0.5)")
    args = p.parse_args()

    if not DB_PATH.exists():
        sys.stderr.write(f"FATAL: db not found: {DB_PATH}\n")
        return 2

    if args.apply:
        if SNAPSHOT_PATH.exists():
            print(f"[apply] snapshot already exists: {SNAPSHOT_PATH}")
        else:
            print(f"[apply] snapshotting {DB_PATH} → {SNAPSHOT_PATH}")
            shutil.copy2(DB_PATH, SNAPSHOT_PATH)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows: list = []
    if args.cities:
        for c in args.cities:
            rows.extend(_find_gap_rows(conn, c, args.start_date, args.end_date))
    else:
        rows = list(_find_gap_rows(conn, None, args.start_date, args.end_date))
    if args.limit:
        rows = rows[: args.limit]

    print(f"target rows: {len(rows)}")
    if not rows:
        return 0

    groups = _group_by_chunks(rows, args.chunk_days)
    print(f"API call groups (city × {args.chunk_days}-day window): {len(groups)}")

    n_filled = 0
    n_value_mismatch = 0
    n_api_fail = 0
    n_no_data_for_date = 0
    n_skipped_unknown_city = 0
    quarantine: list[dict] = []

    if args.apply:
        conn.execute("BEGIN")

    try:
        for city_name, bucket in groups:
            info = CITY_STATIONS.get(city_name)
            if info is None:
                n_skipped_unknown_city += len(bucket)
                quarantine.append({
                    "kind": "unknown_city",
                    "city": city_name,
                    "row_count": len(bucket),
                    "row_ids_sample": [r["id"] for r in bucket[:5]],
                })
                print(f"  SKIP-UNKNOWN-CITY {city_name}: {len(bucket)} rows (not in CITY_STATIONS)")
                continue
            icao, cc, unit = info
            city_cfg = cities_by_name.get(city_name)
            timezone_name = city_cfg.timezone if city_cfg is not None else "UTC"

            chunk_dates = [date.fromisoformat(r["target_date"]) for r in bucket]
            chunk_start = min(chunk_dates)
            chunk_end = max(chunk_dates)

            highs_lows = _fetch_wu_icao_daily_highs_lows(
                icao, cc, chunk_start, chunk_end, unit, timezone_name,
            )
            if not highs_lows:
                n_api_fail += len(bucket)
                quarantine.append({
                    "kind": "api_fail",
                    "city": city_name,
                    "chunk_start": chunk_start.isoformat(),
                    "chunk_end": chunk_end.isoformat(),
                    "row_count": len(bucket),
                })
                print(f"  API-FAIL {city_name} {chunk_start}..{chunk_end}: {len(bucket)} rows skipped")
                time.sleep(args.sleep)
                continue

            for r in bucket:
                target_str = r["target_date"]
                pair = highs_lows.get(target_str)
                if pair is None:
                    n_no_data_for_date += 1
                    quarantine.append({
                        "kind": "no_data_for_date",
                        "city": city_name,
                        "target_date": target_str,
                        "row_id": r["id"],
                    })
                    continue
                api_high, api_low, api_provenance = pair
                db_high = float(r["high_temp"])
                db_low = float(r["low_temp"])
                if abs(api_high - db_high) > args.tolerance or abs(api_low - db_low) > args.tolerance:
                    n_value_mismatch += 1
                    quarantine.append({
                        "kind": "value_mismatch",
                        "city": city_name,
                        "target_date": target_str,
                        "row_id": r["id"],
                        "db_high": db_high,
                        "api_high": api_high,
                        "db_low": db_low,
                        "api_low": api_low,
                        "tolerance": args.tolerance,
                    })
                    print(f"  MISMATCH {city_name}/{target_str}: "
                          f"db_high={db_high} api_high={api_high} "
                          f"db_low={db_low} api_low={api_low}")
                    continue
                # match — UPDATE provenance_metadata only
                if args.apply:
                    conn.execute(
                        "UPDATE observations SET provenance_metadata = ? WHERE id = ?",
                        (json.dumps(api_provenance, separators=(",", ":")), r["id"]),
                    )
                n_filled += 1

            if args.apply:
                # commit per-chunk to bound the transaction size
                conn.commit()
                conn.execute("BEGIN")
            time.sleep(args.sleep)

        if args.apply:
            conn.commit()
    except Exception:
        if args.apply:
            conn.rollback()
        raise
    finally:
        conn.close()

    # Always emit quarantine record
    if quarantine:
        QUARANTINE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = QUARANTINE_LOG_DIR / (
            f"gate34_fill_quarantine_{'apply' if args.apply else 'dryrun'}_2026-04-28.json"
        )
        log_path.write_text(json.dumps({
            "args": vars(args),
            "snapshot_path": str(SNAPSHOT_PATH) if args.apply else None,
            "quarantine_count": len(quarantine),
            "quarantine": quarantine,
        }, indent=2, default=str))
        print(f"\nquarantine log: {log_path}")

    print("\n=== summary ===")
    print(f"  filled (provenance written):      {n_filled}")
    print(f"  value_mismatch (skipped):         {n_value_mismatch}")
    print(f"  api_fail (skipped):               {n_api_fail}")
    print(f"  no_data_for_date (skipped):       {n_no_data_for_date}")
    print(f"  unknown_city (skipped):           {n_skipped_unknown_city}")
    print(f"  total target rows:                {len(rows)}")
    print(f"  mode:                             {'APPLY' if args.apply else 'DRY-RUN (no DB writes)'}")
    if args.apply:
        print(f"  snapshot:                         {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
