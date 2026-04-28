#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
#                  + 2026-04-28 audit:
#                    - Gate 5 in verify_truth_surfaces.py:680-750 fires when
#                      obs_v2 rows training_allowed=1 lack payload_hash OR
#                      parser_version in provenance_json.
#                    - obs_v2 row schema: temp_current is NULL but running_max
#                      and running_min carry the UTC-hour-bucket extrema
#                      (aggregation='utc_hour_bucket_extremum'). Verification
#                      against WU API must reconstruct the same hour-bucket
#                      max/min, not compare temp_current.
#                    - 932,777 wu_icao_history rows + 60,623 ogimet_metar_*
#                      rows are training_allowed=1 with the gap. This script
#                      handles the WU subset (932k); ogimet is a follow-up.
"""Gate 5 patch — fill payload_hash + parser_version on existing obs_v2 rows.

PROBLEM
-------
932,777 rows in `observation_instants_v2`:
  - source='wu_icao_history', authority='VERIFIED', training_allowed=1
  - running_max + running_min populated (the row IS valid evidence)
  - provenance_json missing 'payload_hash' AND 'parser_version' keys

Gate 5 (`payload_identity_missing` in
`scripts/verify_truth_surfaces.py:680-750`) fail-closes calibration rebuild.
Like Gates 3+4, the row VALUES are intact and runtime-usable; only the
audit-trail fields are missing.

SEMANTICS — same UPDATE-only pattern as fill_observations_provenance_existing.py
-------------------------------------------------------------------------------
1. Live WU API call for the relevant (city, target_date) range
2. Reconstruct UTC-hour-bucket max/min from the hourly observations
3. Verify each row's running_max / running_min match within --tolerance
4. ON match: UPDATE provenance_json USING json_set to ADD payload_hash +
   parser_version (other fields preserved — `aggregation`, `hour_max_raw_ts`,
   `station_id`, `tier`, etc. stay intact)
5. ON mismatch: quarantine, do NOT update

NOT synthesis: payload_hash is sha256 of the live WU API response bytes,
parser_version is a stable named constant. No fabrication; if API is
unavailable, the row is skipped (not "filled with placeholder").

USAGE
-----
Dry-run (default):

    python -m docs.operations.task_2026-04-28_obs_provenance_preflight.scripts.fill_obs_v2_payload_identity_existing

Single city + small batch:

    ... --cities Chicago --limit 100

Live apply:

    ... --start-date 2024-01-01 --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone as _tz
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backfill_wu_daily_all import (  # noqa: E402
    CITY_STATIONS,
    WU_API_KEY,
    WU_ICAO_HISTORY_URL,
    HEADERS,
    _build_wu_source_url,
    _sha256_payload,
)
from src.config import cities_by_name  # noqa: E402

import requests  # noqa: E402


PARSER_VERSION = "wu_icao_hourly_legacy_audit_v1"
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


def _resolve_zeus_db_path() -> Path:
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
SNAPSHOT_PATH = DB_PATH.parent / f"{DB_PATH.name}.pre-gate5-fill-2026-04-28"


def _fetch_wu_hourly(
    icao: str,
    cc: str,
    start_date: date,
    end_date: date,
    unit: str,
) -> tuple[list[tuple[int, float]], dict] | None:
    """Fetch hourly observations from WU ICAO history API.

    Returns ([(epoch_seconds, temp), ...], provenance_dict) or None on failure.
    epoch_seconds is `valid_time_gmt` from each obs.
    """
    url = WU_ICAO_HISTORY_URL.format(icao=icao, cc=cc)
    unit_code = "m" if unit == "C" else "e"
    source_url = _build_wu_source_url(
        icao=icao, cc=cc, unit_code=unit_code,
        start_date=start_date, end_date=end_date,
    )
    try:
        resp = requests.get(
            url,
            params={
                "apiKey": WU_API_KEY, "units": unit_code,
                "startDate": start_date.strftime("%Y%m%d"),
                "endDate": end_date.strftime("%Y%m%d"),
            },
            timeout=30, headers=HEADERS,
        )
        if resp.status_code != 200:
            return None
        payload_hash = _sha256_payload(resp.content)
        observations = resp.json().get("observations", [])
        if not observations:
            return None
        hourly: list[tuple[int, float]] = []
        for obs in observations:
            temp = obs.get("temp")
            epoch = obs.get("valid_time_gmt")
            if temp is None or epoch is None:
                continue
            hourly.append((int(epoch), float(temp)))
        provenance = {
            "source": "wu_icao_history",
            "station_id": icao,
            "country_code": cc,
            "unit": unit,
            "unit_code": unit_code,
            "payload_hash": payload_hash,
            "payload_scope": "chunk",
            "source_url": source_url,
            "api_key_redacted": True,
            "parser_version": PARSER_VERSION,
            "request_start_date": start_date.isoformat(),
            "request_end_date": end_date.isoformat(),
            "verified_for_obs_v2_payload_identity_at": datetime.now(_tz.utc).isoformat(),
        }
        return hourly, provenance
    except Exception:
        return None


def _bucket_hour(epoch_sec: int) -> int:
    """Return the UTC-hour bucket start epoch (seconds) for a given epoch."""
    return (epoch_sec // 3600) * 3600


def _hour_extrema(
    hourly: list[tuple[int, float]],
) -> dict[int, tuple[float, float, int]]:
    """Bucket hourly observations by UTC hour, return {bucket_epoch: (max, min, count)}."""
    out: dict[int, list[float]] = {}
    for epoch, temp in hourly:
        b = _bucket_hour(epoch)
        out.setdefault(b, []).append(temp)
    return {b: (max(v), min(v), len(v)) for b, v in out.items()}


def _utc_iso_to_epoch(iso: str) -> int:
    """Parse 'YYYY-MM-DDTHH:MM:SS+00:00' to epoch seconds (UTC)."""
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    return int(datetime.fromisoformat(iso).timestamp())


def _find_gap_rows(
    conn: sqlite3.Connection,
    city: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    source: str = "wu_icao_history",
) -> list[sqlite3.Row]:
    where = (
        "training_allowed=1 "
        "AND source = ? "
        "AND (json_extract(provenance_json,'$.payload_hash') IS NULL "
        "     OR json_extract(provenance_json,'$.parser_version') IS NULL)"
    )
    sql = (
        "SELECT id, city, source, target_date, utc_timestamp, "
        "       running_max, running_min, temp_unit, station_id "
        "FROM observation_instants_v2 "
        f"WHERE {where}"
    )
    params: list = [source]
    if city is not None:
        sql += " AND city = ?"
        params.append(city)
    if start_date is not None:
        sql += " AND target_date >= ?"
        params.append(start_date)
    if end_date is not None:
        sql += " AND target_date <= ?"
        params.append(end_date)
    sql += " ORDER BY city, target_date, utc_timestamp"
    return conn.execute(sql, tuple(params)).fetchall()


def _group_by_chunks(rows, chunk_days: int) -> list[tuple[str, list]]:
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
    p = argparse.ArgumentParser(description="Fill payload_hash + parser_version on existing obs_v2 rows (Gate 5 fix, WU subset).")
    p.add_argument("--cities", nargs="+", default=None)
    p.add_argument("--start-date", default=None, help="ISO YYYY-MM-DD; only fill rows with target_date >= start-date")
    p.add_argument("--end-date", default=None, help="ISO YYYY-MM-DD; only fill rows with target_date <= end-date")
    p.add_argument("--apply", action="store_true", help="execute UPDATEs (default: dry-run, no writes)")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC)
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
    n_no_bucket = 0
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
                })
                print(f"  SKIP-UNKNOWN-CITY {city_name}: {len(bucket)} rows")
                continue
            icao, cc, unit = info

            chunk_dates = [date.fromisoformat(r["target_date"]) for r in bucket]
            chunk_start = min(chunk_dates)
            chunk_end = max(chunk_dates)

            result = _fetch_wu_hourly(icao, cc, chunk_start, chunk_end, unit)
            if result is None:
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
            hourly, api_provenance = result
            extrema = _hour_extrema(hourly)

            for r in bucket:
                row_epoch = _utc_iso_to_epoch(r["utc_timestamp"])
                bucket_key = _bucket_hour(row_epoch)
                got = extrema.get(bucket_key)
                if got is None:
                    n_no_bucket += 1
                    quarantine.append({
                        "kind": "no_bucket_for_utc_hour",
                        "row_id": r["id"],
                        "city": city_name,
                        "utc_timestamp": r["utc_timestamp"],
                    })
                    continue
                api_max, api_min, _api_n = got
                row_max = float(r["running_max"]) if r["running_max"] is not None else None
                row_min = float(r["running_min"]) if r["running_min"] is not None else None
                if row_max is None or row_min is None:
                    n_value_mismatch += 1
                    quarantine.append({
                        "kind": "row_missing_extrema",
                        "row_id": r["id"],
                        "city": city_name,
                        "utc_timestamp": r["utc_timestamp"],
                    })
                    continue
                if abs(api_max - row_max) > args.tolerance or abs(api_min - row_min) > args.tolerance:
                    n_value_mismatch += 1
                    quarantine.append({
                        "kind": "value_mismatch",
                        "row_id": r["id"],
                        "city": city_name,
                        "utc_timestamp": r["utc_timestamp"],
                        "row_max": row_max, "api_max": api_max,
                        "row_min": row_min, "api_min": api_min,
                        "tolerance": args.tolerance,
                    })
                    continue
                # match — UPDATE provenance_json adding payload_hash + parser_version
                if args.apply:
                    # json_set adds the keys; existing keys preserved
                    conn.execute(
                        """
                        UPDATE observation_instants_v2
                        SET provenance_json = json_set(
                            json_set(
                                json_set(
                                    json_set(
                                        json_set(provenance_json, '$.payload_hash', ?),
                                        '$.parser_version', ?),
                                    '$.source_url', ?),
                                '$.country_code', ?),
                            '$.verified_for_obs_v2_payload_identity_at', ?)
                        WHERE id = ?
                        """,
                        (
                            api_provenance["payload_hash"],
                            api_provenance["parser_version"],
                            api_provenance["source_url"],
                            api_provenance["country_code"],
                            api_provenance["verified_for_obs_v2_payload_identity_at"],
                            r["id"],
                        ),
                    )
                n_filled += 1

            if args.apply:
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

    if quarantine:
        QUARANTINE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = QUARANTINE_LOG_DIR / (
            f"gate5_fill_quarantine_{'apply' if args.apply else 'dryrun'}_2026-04-28.json"
        )
        log_path.write_text(json.dumps({
            "args": vars(args),
            "snapshot_path": str(SNAPSHOT_PATH) if args.apply else None,
            "quarantine_count": len(quarantine),
            "quarantine": quarantine[:5000],  # cap to avoid huge logs
            "quarantine_truncated": len(quarantine) > 5000,
        }, indent=2, default=str))
        print(f"\nquarantine log: {log_path} ({len(quarantine)} entries)")

    print("\n=== summary ===")
    print(f"  filled (provenance updated):      {n_filled}")
    print(f"  value_mismatch (skipped):         {n_value_mismatch}")
    print(f"  api_fail (skipped):               {n_api_fail}")
    print(f"  no_bucket_for_utc_hour (skipped): {n_no_bucket}")
    print(f"  unknown_city (skipped):           {n_skipped_unknown_city}")
    print(f"  total target rows:                {len(rows)}")
    print(f"  mode:                             {'APPLY' if args.apply else 'DRY-RUN (no DB writes)'}")
    if args.apply:
        print(f"  snapshot:                         {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
