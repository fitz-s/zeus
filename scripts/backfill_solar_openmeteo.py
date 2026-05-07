#!/usr/bin/env python3
"""Backfill sunrise/sunset daily context from Open-Meteo Archive API.

Writes to `solar_daily` table which is consumed by `src/signal/diurnal.py:49`
for day0 signal generation. Uses the same Open-Meteo archive endpoint as
`backfill_hourly_openmeteo.py` (same data provider, same timezone/lat/lon
semantics), just requesting `daily=sunrise,sunset` instead of hourly temps.

Why this script exists alongside `etl_solar_times.py`:
The previous JSONL-import path (`etl_solar_times.py`) relied on a
pre-generated file at
  /Users/leofitz/.openclaw/workspace-venus/51 source data/raw/solar/
   city_solar_times_20240101_20260331.jsonl
which only covered 38 of the 46 configured cities (missing: Auckland,
Busan, Cape Town, Jakarta, Jeddah, Kuala Lumpur, Lagos, Panama City) and
ended on 2026-03-31. Per the "every city must have the same data volume"
requirement, this script fetches directly from Open-Meteo for all 46
cities across the full 832-day window. INSERT OR REPLACE is used so it
can run on top of the earlier JSONL import and overwrite per (city,
target_date) key without conflict.

Validation:
- lat ∈ [-90, 90], lon ∈ [-180, 180] from canonical cities.json (already
  verified by K0 authoritative cities config commit)
- timezone parses via ZoneInfo (raised at script load via city.timezone
  already being consumed by other Zeus code)
- Every response row has non-null sunrise and sunset, parseable ISO
- Network errors retried N times with exponential backoff

Usage:
    cd zeus && python scripts/backfill_solar_openmeteo.py --all-zeus --days 832
    cd zeus && python scripts/backfill_solar_openmeteo.py --cities "Buenos Aires" --days 7 --dry-run
"""
from __future__ import annotations

import argparse
import logging
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

logger = logging.getLogger(__name__)

OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CHUNK_DAYS = 90
SLEEP_BETWEEN_REQUESTS = 1.0
FETCH_RETRY_COUNT = 2
FETCH_RETRY_BACKOFF_SEC = 2.0


# ---------------------------------------------------------------------------
# Fetch layer
# ---------------------------------------------------------------------------


def _fetch_solar_chunk(
    city: City,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Fetch one Open-Meteo archive chunk for sunrise/sunset daily data.

    Returns per-day dicts with all 11 columns needed for solar_daily INSERT.
    """
    resp = httpx.get(
        OPENMETEO_ARCHIVE_URL,
        params={
            "latitude": city.lat,
            "longitude": city.lon,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": "sunrise,sunset",
            "timezone": city.timezone,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    times = daily.get("time") or []
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []

    if not (len(times) == len(sunrises) == len(sunsets)):
        logger.warning(
            "Open-Meteo response length mismatch for %s %s..%s: "
            "times=%d sunrises=%d sunsets=%d",
            city.name, start_date, end_date,
            len(times), len(sunrises), len(sunsets),
        )
        return []

    tz = ZoneInfo(city.timezone)
    out: list[dict] = []
    for target_date_str, sunrise_raw, sunset_raw in zip(times, sunrises, sunsets):
        if sunrise_raw is None or sunset_raw is None:
            logger.warning(
                "Null sunrise/sunset for %s %s", city.name, target_date_str,
            )
            continue
        try:
            # Open-Meteo returns local ISO strings (because of `timezone` param).
            # Attach tz to get DST-aware local datetime. Sunrise/sunset are
            # never in DST gap hours for 46-city latitudes (all below 55°
            # absolute lat), so we don't need Layer 5-style gap detection.
            sunrise_local_dt = datetime.fromisoformat(sunrise_raw).replace(tzinfo=tz)
            sunset_local_dt = datetime.fromisoformat(sunset_raw).replace(tzinfo=tz)

            sunrise_utc_dt = sunrise_local_dt.astimezone(timezone.utc)
            sunset_utc_dt = sunset_local_dt.astimezone(timezone.utc)

            # DST context — computed from sunrise's tz (sunset may be in a
            # different DST state only on the transition day; we use sunrise
            # as canonical anchor).
            utc_offset = sunrise_local_dt.utcoffset()
            dst_offset = sunrise_local_dt.dst()
            dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)

            out.append({
                "city": city.name,
                "target_date": target_date_str,
                "timezone": city.timezone,
                "lat": float(city.lat),
                "lon": float(city.lon),
                "sunrise_local": sunrise_local_dt.isoformat(),
                "sunset_local": sunset_local_dt.isoformat(),
                "sunrise_utc": sunrise_utc_dt.isoformat(),
                "sunset_utc": sunset_utc_dt.isoformat(),
                "utc_offset_minutes": int(utc_offset.total_seconds() / 60) if utc_offset else 0,
                "dst_active": 1 if dst_active else 0,
            })
        except (ValueError, AttributeError) as e:
            logger.warning(
                "Parse failed %s %s sunrise=%r sunset=%r: %s",
                city.name, target_date_str, sunrise_raw, sunset_raw, e,
            )
            continue

    return out


def _fetch_with_retry(
    city: City,
    start_date: date,
    end_date: date,
) -> tuple[list[dict], str | None]:
    """Fetch one chunk with N retries on transient HTTP errors."""
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            return _fetch_solar_chunk(city, start_date, end_date), None
        except httpx.HTTPError as e:
            if attempt < FETCH_RETRY_COUNT:
                wait = FETCH_RETRY_BACKOFF_SEC * (attempt + 1)
                logger.warning(
                    "Fetch retry %d/%d for %s %s..%s after %.1fs: %s",
                    attempt + 1, FETCH_RETRY_COUNT, city.name,
                    start_date, end_date, wait, e,
                )
                time.sleep(wait)
                continue
            return [], f"http error after {FETCH_RETRY_COUNT + 1} tries: {e}"
        except Exception as e:
            return [], f"unexpected error: {type(e).__name__}: {e}"
    return [], "exhausted retries"


# ---------------------------------------------------------------------------
# Write layer
# ---------------------------------------------------------------------------


_INSERT_SQL = """
    INSERT OR REPLACE INTO solar_daily
    (city, target_date, timezone, lat, lon, sunrise_local, sunset_local,
     sunrise_utc, sunset_utc, utc_offset_minutes, dst_active)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _write_solar_row(conn, r: dict) -> int:
    """Insert one solar row. Returns cursor rowcount."""
    cur = conn.execute(_INSERT_SQL, (
        r["city"], r["target_date"], r["timezone"],
        r["lat"], r["lon"],
        r["sunrise_local"], r["sunset_local"],
        r["sunrise_utc"], r["sunset_utc"],
        r["utc_offset_minutes"], r["dst_active"],
    ))
    return cur.rowcount


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


_STAT_KEYS = (
    "fetched", "inserted", "fetch_errors", "insert_errors",
)


def _new_stats() -> dict[str, int]:
    return {k: 0 for k in _STAT_KEYS}


def backfill_city(
    city: City,
    days_back: int,
    conn,
    *,
    chunk_days: int = CHUNK_DAYS,
    sleep_seconds: float = SLEEP_BETWEEN_REQUESTS,
    dry_run: bool = False,
) -> dict:
    end_date = date.today() - timedelta(days=2)
    start_date = end_date - timedelta(days=days_back - 1)
    city_totals: dict = {"city": city.name, **_new_stats()}

    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)
        chunk = _new_stats()

        rows, err = _fetch_with_retry(city, current, chunk_end)
        if err:
            chunk["fetch_errors"] = 1
            logger.error(
                "Chunk failed for %s %s..%s: %s",
                city.name, current, chunk_end, err,
            )
        else:
            chunk["fetched"] = len(rows)
            for r in rows:
                if dry_run:
                    chunk["inserted"] += 1
                    continue
                try:
                    rc = _write_solar_row(conn, r)
                    if rc > 0:
                        chunk["inserted"] += 1
                except Exception as e:
                    chunk["insert_errors"] += 1
                    logger.error(
                        "Insert failed %s %s: %s",
                        city.name, r["target_date"], e,
                    )
            if not dry_run:
                conn.commit()

        for k in _STAT_KEYS:
            city_totals[k] += chunk[k]

        print(
            f"    {current} → {chunk_end}: "
            f"fetched={chunk['fetched']} "
            f"inserted={chunk['inserted']} "
            f"err={chunk['fetch_errors'] + chunk['insert_errors']}"
        )

        current = chunk_end + timedelta(days=1)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return city_totals


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--cities", nargs="+", default=None,
                        help="Cities to backfill (default: all not-yet-covered)")
    parser.add_argument("--days", type=int, default=832,
                        help="Days to look back (default: 832 = 2024-01-01 → today-2)")
    parser.add_argument("--chunk-days", type=int, default=CHUNK_DAYS)
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_REQUESTS)
    parser.add_argument("--all-zeus", action="store_true",
                        help="Backfill all 46 configured Zeus cities")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch but do not write to DB")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.dry_run:
        print("[DRY RUN] No rows will be written.")

    pool_map = {c.name: c for c in ALL_CITIES}

    conn = get_world_connection(write_class="bulk")
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)

    if args.cities:
        targets = [pool_map[n] for n in args.cities if n in pool_map]
        for n in args.cities:
            if n not in pool_map:
                print(f"  SKIP: {n} not in ALL_CITIES")
    elif args.all_zeus:
        targets = list(ALL_CITIES)
    else:
        # Default: only cities without existing solar_daily rows
        covered = {r[0] for r in conn.execute(
            "SELECT DISTINCT city FROM solar_daily"
        ).fetchall()}
        targets = [c for c in ALL_CITIES if c.name not in covered]

    print(f"=== Open-Meteo Solar Backfill ===")
    print(f"Targets:  {len(targets)} cities | {args.days} days back")

    all_stats: list[dict] = []
    for city in targets:
        print(f"\n[{city.name}] {city.timezone}")
        stats = backfill_city(
            city,
            days_back=args.days,
            conn=conn,
            chunk_days=args.chunk_days,
            sleep_seconds=args.sleep,
            dry_run=args.dry_run,
        )
        all_stats.append(stats)

    conn.close()

    # Summary
    print(f"\n=== Summary ===")
    if all_stats:
        totals = {k: sum(s[k] for s in all_stats) for k in _STAT_KEYS}
        for k in _STAT_KEYS:
            print(f"  {k:25s} {totals[k]}")

        print(f"\nPer-city:")
        for s in all_stats:
            print(f"  {s['city']:20s} inserted={s['inserted']:>6} "
                  f"fetch_errs={s['fetch_errors']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
