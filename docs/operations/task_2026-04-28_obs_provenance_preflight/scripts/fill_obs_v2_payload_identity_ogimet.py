#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
#                  + companion to fill_obs_v2_payload_identity_existing.py (WU
#                    subset). Same UPDATE-only pattern, applied to the
#                    ogimet_metar_{uuww, ltfm, llbg} subset (Moscow/Istanbul/
#                    Tel Aviv) of obs_v2 — 60,623 rows training_allowed=1
#                    missing payload_hash + parser_version.
"""Gate 5 patch — ogimet subset of obs_v2 (Moscow/Istanbul/Tel Aviv).

Same semantics as the WU patch: fetch real ogimet METAR data via
`src.data.ogimet_hourly_client.fetch_ogimet_hourly`, verify each obs_v2 row's
running_max/running_min match the reconstructed hour-bucket extrema within
--tolerance, then UPDATE provenance_json adding payload_hash + parser_version.

Differences from the WU patch:
  - Uses ogimet METAR scraper (no API key needed)
  - payload_hash is sha256 of canonicalized hourly observation list
    (ogimet client doesn't expose raw response bytes)
  - source URL pattern: https://www.ogimet.com/cgi-bin/getmetar?icao=<ICAO>
"""
from __future__ import annotations

import argparse
import os
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone as _tz
from pathlib import Path

OPERATOR_APPLY_APPROVAL_ENV = "ZEUS_OPERATOR_APPROVED_DB_MUTATION"


def _require_operator_apply_approval() -> None:
    if os.environ.get(OPERATOR_APPLY_APPROVAL_ENV) != "YES":
        raise SystemExit(
            "REFUSING --apply: this packet script can mutate zeus DB state or call "
            "external data sources. Set ZEUS_OPERATOR_APPROVED_DB_MUTATION=YES "
            "only after the active packet/current_state authorizes the mutation."
        )

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from src.data.ogimet_hourly_client import fetch_ogimet_hourly  # noqa: E402
from src.config import cities_by_name  # noqa: E402

PARSER_VERSION = "ogimet_metar_legacy_audit_v1"
DEFAULT_TOLERANCE = 0.5
DEFAULT_SLEEP_SEC = 1.0
DEFAULT_CHUNK_DAYS = 31

# (city → (icao, source_tag)) — 3 cities tier_resolver routes to ogimet
OGIMET_STATIONS: dict[str, tuple[str, str]] = {
    "Moscow":   ("UUWW", "ogimet_metar_uuww"),
    "Istanbul": ("LTFM", "ogimet_metar_ltfm"),
    "Tel Aviv": ("LLBG", "ogimet_metar_llbg"),
}

QUARANTINE_LOG_DIR = (
    PROJECT_ROOT / "docs" / "operations"
    / "task_2026-04-28_obs_provenance_preflight" / "evidence"
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
SNAPSHOT_PATH = DB_PATH.parent / f"{DB_PATH.name}.pre-gate5-ogimet-fill-2026-04-28"


def _bucket_hour_epoch(utc_iso: str) -> int:
    if utc_iso.endswith("Z"):
        utc_iso = utc_iso[:-1] + "+00:00"
    return (int(datetime.fromisoformat(utc_iso).timestamp()) // 3600) * 3600


def _canonical_observation_hash(obs_list) -> str:
    """sha256 of canonicalized (epoch, max, min) tuples — deterministic per fetch."""
    payload = sorted(
        (
            int(o.utc_timestamp and datetime.fromisoformat(
                o.utc_timestamp.replace("Z", "+00:00")).timestamp() or 0),
            float(o.hour_max_temp) if o.hour_max_temp is not None else None,
            float(o.hour_min_temp) if o.hour_min_temp is not None else None,
        )
        for o in obs_list
    )
    return "sha256:" + hashlib.sha256(json.dumps(payload, default=str).encode()).hexdigest()


def _build_ogimet_provenance(
    *,
    icao: str,
    chunk_start: date,
    chunk_end: date,
    payload_hash: str,
    source_tag: str,
) -> dict:
    return {
        "source": source_tag,
        "station_id": icao,
        "payload_hash": payload_hash,
        "payload_scope": "chunk",
        "source_url": (
            f"https://www.ogimet.com/cgi-bin/getmetar"
            f"?icao={icao}&start={chunk_start.isoformat()}&end={chunk_end.isoformat()}"
        ),
        "parser_version": PARSER_VERSION,
        "request_start_date": chunk_start.isoformat(),
        "request_end_date": chunk_end.isoformat(),
        "verified_for_obs_v2_payload_identity_at": datetime.now(_tz.utc).isoformat(),
    }


def _find_gap_rows(
    conn,
    city: str | None,
    source: str,
    start_date: str | None,
    end_date: str | None,
):
    sql = (
        "SELECT id, city, source, target_date, utc_timestamp, "
        "       running_max, running_min, temp_unit, station_id "
        "FROM observation_instants_v2 "
        "WHERE training_allowed=1 AND source = ? "
        "  AND (json_extract(provenance_json,'$.payload_hash') IS NULL "
        "       OR json_extract(provenance_json,'$.parser_version') IS NULL)"
    )
    params: list = [source]
    if city:
        sql += " AND city = ?"
        params.append(city)
    if start_date:
        sql += " AND target_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND target_date <= ?"
        params.append(end_date)
    sql += " ORDER BY target_date, utc_timestamp"
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
    p = argparse.ArgumentParser()
    p.add_argument("--cities", nargs="+", default=None)
    p.add_argument("--start-date", default=None)
    p.add_argument("--end-date", default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SEC)
    args = p.parse_args()
    if args.apply:
        _require_operator_apply_approval()

    if not DB_PATH.exists():
        sys.stderr.write(f"FATAL: db not found: {DB_PATH}\n")
        return 2

    if args.apply:
        if SNAPSHOT_PATH.exists():
            print(f"[apply] snapshot already exists: {SNAPSHOT_PATH}")
        else:
            print(f"[apply] snapshotting → {SNAPSHOT_PATH}")
            shutil.copy2(DB_PATH, SNAPSHOT_PATH)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # gather rows for each ogimet source
    target_cities = args.cities or list(OGIMET_STATIONS.keys())
    rows: list = []
    for city in target_cities:
        info = OGIMET_STATIONS.get(city)
        if info is None:
            continue
        icao, source_tag = info
        rows.extend(_find_gap_rows(conn, city, source_tag, args.start_date, args.end_date))
    if args.limit:
        rows = rows[: args.limit]

    print(f"target rows: {len(rows)}")
    if not rows:
        return 0

    groups = _group_by_chunks(rows, args.chunk_days)
    print(f"ogimet API call groups (city × {args.chunk_days}-day): {len(groups)}")

    n_filled = 0
    n_value_mismatch = 0
    n_api_fail = 0
    n_no_bucket = 0
    quarantine: list[dict] = []

    if args.apply:
        conn.execute("BEGIN")

    try:
        for city_name, bucket in groups:
            info = OGIMET_STATIONS[city_name]
            icao, source_tag = info
            city_cfg = cities_by_name.get(city_name)
            timezone_name = city_cfg.timezone if city_cfg else "UTC"

            chunk_dates = [date.fromisoformat(r["target_date"]) for r in bucket]
            chunk_start = min(chunk_dates)
            chunk_end = max(chunk_dates)

            try:
                result = fetch_ogimet_hourly(
                    station=icao,
                    start_date=chunk_start,
                    end_date=chunk_end,
                    city_name=city_name,
                    timezone_name=timezone_name,
                    source_tag=source_tag,
                    unit="C",
                )
            except Exception as e:
                n_api_fail += len(bucket)
                quarantine.append({
                    "kind": "api_exception",
                    "city": city_name,
                    "chunk_start": chunk_start.isoformat(),
                    "chunk_end": chunk_end.isoformat(),
                    "error": str(e)[:200],
                })
                print(f"  EXC {city_name} {chunk_start}..{chunk_end}: {e}")
                time.sleep(args.sleep)
                continue
            if not result.observations:
                n_api_fail += len(bucket)
                quarantine.append({
                    "kind": "api_empty",
                    "city": city_name,
                    "chunk_start": chunk_start.isoformat(),
                    "chunk_end": chunk_end.isoformat(),
                    "row_count": len(bucket),
                })
                time.sleep(args.sleep)
                continue

            # Build (epoch_bucket -> (max, min)) map from HourlyObservation
            api_extrema: dict[int, tuple[float | None, float | None]] = {}
            for o in result.observations:
                iso = o.utc_timestamp
                if not iso:
                    continue
                epoch = _bucket_hour_epoch(iso)
                api_extrema[epoch] = (o.hour_max_temp, o.hour_min_temp)

            payload_hash = _canonical_observation_hash(result.observations)
            api_provenance = _build_ogimet_provenance(
                icao=icao,
                chunk_start=chunk_start, chunk_end=chunk_end,
                payload_hash=payload_hash,
                source_tag=source_tag,
            )

            for r in bucket:
                bucket_key = _bucket_hour_epoch(r["utc_timestamp"])
                got = api_extrema.get(bucket_key)
                if got is None:
                    n_no_bucket += 1
                    quarantine.append({
                        "kind": "no_bucket_for_utc_hour",
                        "row_id": r["id"], "city": city_name,
                        "utc_timestamp": r["utc_timestamp"],
                    })
                    continue
                api_max, api_min = got
                row_max = float(r["running_max"]) if r["running_max"] is not None else None
                row_min = float(r["running_min"]) if r["running_min"] is not None else None
                if (row_max is None or row_min is None or
                    api_max is None or api_min is None):
                    n_value_mismatch += 1
                    quarantine.append({
                        "kind": "missing_extrema",
                        "row_id": r["id"], "city": city_name,
                        "utc_timestamp": r["utc_timestamp"],
                        "row": [row_max, row_min], "api": [api_max, api_min],
                    })
                    continue
                if (abs(api_max - row_max) > args.tolerance or
                    abs(api_min - row_min) > args.tolerance):
                    n_value_mismatch += 1
                    quarantine.append({
                        "kind": "value_mismatch",
                        "row_id": r["id"], "city": city_name,
                        "utc_timestamp": r["utc_timestamp"],
                        "row_max": row_max, "api_max": api_max,
                        "row_min": row_min, "api_min": api_min,
                        "tolerance": args.tolerance,
                    })
                    continue
                if args.apply:
                    conn.execute(
                        """
                        UPDATE observation_instants_v2
                        SET provenance_json = json_set(
                            json_set(
                                json_set(
                                    json_set(provenance_json, '$.payload_hash', ?),
                                    '$.parser_version', ?),
                                '$.source_url', ?),
                            '$.verified_for_obs_v2_payload_identity_at', ?)
                        WHERE id = ?
                        """,
                        (
                            api_provenance["payload_hash"],
                            api_provenance["parser_version"],
                            api_provenance["source_url"],
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
            f"gate5_ogimet_quarantine_{'apply' if args.apply else 'dryrun'}_2026-04-28.json"
        )
        log_path.write_text(json.dumps({
            "args": vars(args),
            "snapshot_path": str(SNAPSHOT_PATH) if args.apply else None,
            "quarantine_count": len(quarantine),
            "quarantine": quarantine[:5000],
        }, indent=2, default=str))
        print(f"\nquarantine log: {log_path} ({len(quarantine)} entries)")

    print("\n=== summary ===")
    print(f"  filled (provenance updated):      {n_filled}")
    print(f"  value_mismatch (skipped):         {n_value_mismatch}")
    print(f"  api_fail (skipped):               {n_api_fail}")
    print(f"  no_bucket_for_utc_hour (skipped): {n_no_bucket}")
    print(f"  total target rows:                {len(rows)}")
    print(f"  mode:                             {'APPLY' if args.apply else 'DRY-RUN'}")
    if args.apply:
        print(f"  snapshot:                         {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
