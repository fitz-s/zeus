#!/usr/bin/env python3
"""Backfill hourly observations from Open-Meteo Archive API.

Fetches historical hourly temperature_2m for cities missing hourly data
and writes directly to zeus-world.db observation_instants + hourly_observations.

Open-Meteo Archive: free, no API key, global coverage, hourly since 1940.
Rate limit: ~10,000 calls/day. Each call = 1 city × 90 days.

Usage:
    cd zeus && .venv/bin/python scripts/backfill_hourly_openmeteo.py
    cd zeus && .venv/bin/python scripts/backfill_hourly_openmeteo.py --cities Austin Denver
    cd zeus && .venv/bin/python scripts/backfill_hourly_openmeteo.py --days 365
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.config import cities as ALL_CITIES, City
from src.state.db import get_world_connection, init_schema

OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CHUNK_DAYS = 90  # Open-Meteo allows up to ~1 year per request, but 90 days is safer
SLEEP_BETWEEN_REQUESTS = 1.0  # Be polite


def _fetch_hourly_chunk(
    city: City,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Fetch hourly temperature from Open-Meteo Archive for a date range."""
    temp_unit = "fahrenheit" if city.settlement_unit == "F" else "celsius"

    resp = httpx.get(
        OPENMETEO_ARCHIVE_URL,
        params={
            "latitude": city.lat,
            "longitude": city.lon,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "hourly": "temperature_2m",
            "temperature_unit": temp_unit,
            "timezone": city.timezone,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])

    tz = ZoneInfo(city.timezone)
    results = []
    for raw_time, temp in zip(times, temps):
        if temp is None:
            continue
        try:
            local_dt = datetime.fromisoformat(raw_time).replace(tzinfo=tz)
            utc_dt = local_dt.astimezone(timezone.utc)
            target_date = local_dt.date().isoformat()

            results.append({
                "city": city.name,
                "target_date": target_date,
                "source": "openmeteo_archive_hourly",
                "timezone_name": city.timezone,
                "local_hour": local_dt.hour,
                "local_timestamp": local_dt.isoformat(),
                "utc_timestamp": utc_dt.isoformat(),
                "utc_offset_minutes": int(local_dt.utcoffset().total_seconds() / 60),
                "dst_active": bool(local_dt.dst()),
                "is_ambiguous_local_hour": 0,
                "is_missing_local_hour": 0,
                "temp_current": float(temp),
                "temp_unit": city.settlement_unit,
            })
        except (ValueError, AttributeError):
            continue

    return results


def backfill_city(
    city: City,
    days_back: int = 440,
    conn=None,
    *,
    chunk_days: int = CHUNK_DAYS,
    sleep_seconds: float = SLEEP_BETWEEN_REQUESTS,
) -> dict:
    """Backfill hourly observations for one city."""
    own_conn = conn is None
    if own_conn:
        conn = get_world_connection()
        init_schema(conn)

    # Check existing coverage
    existing = conn.execute(
        "SELECT MIN(target_date), MAX(target_date), COUNT(DISTINCT target_date) "
        "FROM observation_instants WHERE city = ? AND source = 'openmeteo_archive_hourly'",
        (city.name,),
    ).fetchone()

    existing_dates = existing[2] if existing[2] else 0
    print(f"  {city.name}: {existing_dates} existing dates in observation_instants")

    end_date = date.today() - timedelta(days=2)  # Archive has ~2 day delay
    start_date = end_date - timedelta(days=days_back - 1)

    total_inserted = 0
    total_skipped = 0

    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)

        try:
            rows = _fetch_hourly_chunk(city, current, chunk_end)
            inserted = 0

            for r in rows:
                try:
                    cur = conn.execute("""
                        INSERT OR IGNORE INTO observation_instants
                        (city, target_date, source, timezone_name, local_hour,
                         local_timestamp, utc_timestamp, utc_offset_minutes,
                         dst_active, is_ambiguous_local_hour, is_missing_local_hour,
                         time_basis, temp_current, temp_unit, imported_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        r["city"], r["target_date"], r["source"],
                        r["timezone_name"], r["local_hour"],
                        r["local_timestamp"], r["utc_timestamp"],
                        r["utc_offset_minutes"], r["dst_active"],
                        r["is_ambiguous_local_hour"], r["is_missing_local_hour"],
                        "archive_hourly",
                        r["temp_current"], r["temp_unit"],
                        datetime.now(timezone.utc).isoformat(),
                    ))
                    if cur.rowcount > 0:
                        inserted += 1
                except Exception:
                    pass

            conn.commit()
            total_inserted += inserted
            total_skipped += len(rows) - inserted
            print(f"    {current} → {chunk_end}: {inserted} new, {len(rows) - inserted} skip")

        except Exception as e:
            print(f"    {current} → {chunk_end}: ERROR {e}")

        current = chunk_end + timedelta(days=1)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    if own_conn:
        conn.close()

    return {"city": city.name, "inserted": total_inserted, "skipped": total_skipped}


MANIFEST_PATH = Path.home() / ".openclaw/workspace-venus/51 source data/docs/tigge_city_coordinate_manifest_full_20260330.json"


def _load_manifest_cities() -> list[City]:
    """Load all 38 cities from TIGGE manifest, using Zeus config where available."""
    import json
    zeus_map = {c.name: c for c in ALL_CITIES}

    manifest = json.loads(MANIFEST_PATH.read_text())
    result = []
    for entry in manifest["cities"]:
        name = entry["city"]
        if name in zeus_map:
            result.append(zeus_map[name])
        else:
            # Build lightweight City for expansion cities
            result.append(City(
                name=name,
                lat=float(entry["lat"]),
                lon=float(entry["lon"]),
                timezone=entry["timezone"],
                settlement_unit=entry["unit"],
                cluster="expansion",
                wu_station=entry.get("wu_station") or "",
            ))
    return result


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", nargs="+", default=None, help="Cities to backfill (default: all missing)")
    parser.add_argument("--days", type=int, default=440, help="Days to look back (default: 440)")
    parser.add_argument("--chunk-days", type=int, default=CHUNK_DAYS, help=f"Days per Open-Meteo request (default: {CHUNK_DAYS})")
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_REQUESTS, help=f"Seconds between requests (default: {SLEEP_BETWEEN_REQUESTS})")
    parser.add_argument("--all-zeus", action="store_true", help="Backfill all configured Zeus cities")
    parser.add_argument("--all-manifest", action="store_true", help="Backfill all 38 TIGGE manifest cities")
    args = parser.parse_args()

    # Build city pool
    if args.all_manifest:
        all_pool = _load_manifest_cities()
    else:
        all_pool = list(ALL_CITIES)

    pool_map = {c.name: c for c in all_pool}

    # Cities that already have hourly in zeus
    conn = get_world_connection()
    init_schema(conn)
    covered = {r[0] for r in conn.execute(
        "SELECT DISTINCT city FROM observation_instants WHERE source = 'openmeteo_archive_hourly'"
    ).fetchall()}

    if args.cities:
        targets = [pool_map[name] for name in args.cities if name in pool_map]
    elif args.all_zeus:
        targets = list(ALL_CITIES)
    elif args.all_manifest:
        targets = [c for c in all_pool if c.name not in covered]
    else:
        targets = [c for c in all_pool if c.name not in covered]

    print(f"=== Open-Meteo Hourly Backfill ===")
    print(f"Pool: {len(all_pool)} cities | Targets: {len(targets)} cities | {args.days} days back")
    print(f"Already covered: {sorted(covered)}")

    results = []
    for city in targets:
        print(f"\n[{city.name}]")
        r = backfill_city(
            city,
            days_back=args.days,
            conn=conn,
            chunk_days=args.chunk_days,
            sleep_seconds=args.sleep,
        )
        results.append(r)

    conn.close()

    # Now rebuild hourly_observations from observation_instants
    print("\n--- Rebuilding hourly_observations ---")
    from scripts.etl_hourly_observations import run_etl
    run_etl()

    print("\n=== Summary ===")
    total = sum(r["inserted"] for r in results)
    print(f"Total inserted: {total:,}")
    for r in results:
        print(f"  {r['city']:20s} +{r['inserted']:,}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
