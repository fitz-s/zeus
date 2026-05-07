#!/usr/bin/env python3
"""Backfill hourly observations from Open-Meteo Archive API (K1-C guarded).

Fetches historical hourly temperature_2m for cities and writes to
`observation_instants`. Every hourly reading passes through IngestionGuard
Layers 1 and 5 before insertion. DST semantics fields
(`is_missing_local_hour`, `is_ambiguous_local_hour`, `dst_active`) are
computed from the actual `local_dt` via ZoneInfo rather than hardcoded.

Guard layer selection for hourly readings:
- Layer 1 `check_unit_consistency` — Earth records + city.settlement_unit
- Layer 3 DELETED 2026-04-13 (hemisphere-uniform envelope was a category
  error — see src/data/ingestion_guard.py module docstring).
- Layer 5 `check_dst_boundary` — reject readings in DST spring-forward gaps
- Layer 2 (physical_bounds) SKIPPED: tuned for daily max; hourly readings
  can legitimately fall below `p01 - 2σ` at night.
- Layer 4 (collection_timing) SKIPPED: historical backfill is always
  "after the target date" by construction.

K1-C port: 2026-04-13. Replaces the pre-K1 version that hardcoded DST
fields to 0 and silently swallowed every INSERT exception. The derived
table `hourly_observations` is auto-rebuilt from `observation_instants`
via `scripts/etl_hourly_observations.py` at the end of the run.

Usage:
    cd zeus && python scripts/backfill_hourly_openmeteo.py --all-zeus --days 832
    cd zeus && python scripts/backfill_hourly_openmeteo.py --cities "Buenos Aires" --days 7 --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.config import cities as ALL_CITIES, cities_by_name, City
from src.data.ingestion_guard import (
    DstBoundaryViolation,
    IngestionGuard,
    IngestionRejected,
    UnitConsistencyViolation,
)
from src.signal.diurnal import _is_missing_local_hour
from src.state.db import get_world_connection, init_schema

logger = logging.getLogger(__name__)

OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CHUNK_DAYS = 90
SLEEP_BETWEEN_REQUESTS = 1.0
FETCH_RETRY_COUNT = 2
FETCH_RETRY_BACKOFF_SEC = 2.0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _hemisphere_for_lat(lat: float) -> str:
    return "N" if lat >= 0 else "S"


def _to_fahrenheit(value: float, unit: str) -> float:
    if unit == "C":
        return value * 9 / 5 + 32
    if unit == "K":
        return (value - 273.15) * 9 / 5 + 32
    return value


# ---------------------------------------------------------------------------
# Fetch layer
# ---------------------------------------------------------------------------


def _fetch_hourly_chunk(
    city: City,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Fetch one Open-Meteo chunk. Returns per-hour dicts.

    The `temperature_unit` parameter is pinned to the city's declared
    settlement unit, and the `timezone` parameter pins the response's
    `time` strings to local ISO. `local_dt` is then constructed with
    `ZoneInfo` so Python's DST resolution applies correctly.
    """
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
    out: list[dict] = []
    for raw_time, temp in zip(times, temps):
        if temp is None:
            continue
        try:
            naive = datetime.fromisoformat(raw_time)
            local_dt = naive.replace(tzinfo=tz)

            # Compute REAL DST semantics — never hardcode.
            # Per ObservationAtom docstring §2.6: "MUST be computed from
            # SolarDay/ZoneInfo via _is_missing_local_hour. Never hardcode
            # these to False."
            is_missing = _is_missing_local_hour(local_dt, tz)
            is_ambiguous = bool(getattr(local_dt, "fold", 0))
            utc_offset = local_dt.utcoffset()
            dst_offset = local_dt.dst()
            dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
            utc_dt = local_dt.astimezone(timezone.utc)

            out.append({
                "city": city.name,
                "target_date": local_dt.date().isoformat(),
                "source": "openmeteo_archive_hourly",
                "timezone_name": city.timezone,
                "local_hour": float(local_dt.hour),
                "local_timestamp": local_dt.isoformat(),
                "utc_timestamp": utc_dt.isoformat(),
                "utc_offset_minutes": int(utc_offset.total_seconds() / 60) if utc_offset else 0,
                "dst_active": 1 if dst_active else 0,
                "is_ambiguous_local_hour": 1 if is_ambiguous else 0,
                "is_missing_local_hour": 1 if is_missing else 0,
                "temp_current": float(temp),
                "temp_unit": city.settlement_unit,
                "local_dt": local_dt,  # for guard Layer 5; not written to DB
            })
        except (ValueError, AttributeError) as e:
            logger.warning("Parse failed %s %s: %s", city.name, raw_time, e)
            continue

    return out


def _fetch_with_retry(
    city: City,
    start_date: date,
    end_date: date,
) -> tuple[list[dict], str | None]:
    """Fetch one chunk with N retries on transient HTTP errors.

    Returns (rows, None) on success or ([], error_message) on permanent
    failure after all retries exhausted.
    """
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            return _fetch_hourly_chunk(city, start_date, end_date), None
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
# Guard layer (Layers 1, 3, 5 — the ones that apply to hourly)
# ---------------------------------------------------------------------------


_GUARD = IngestionGuard()


def _validate_hourly_reading(city_name: str, r: dict) -> str | None:
    """Run K1-C guard Layers 1 + 3 + 5 on one hourly reading.

    Returns None on pass, or a rejection category string:
      "unit" | "seasonal" | "dst" | "other"
    """
    city_obj = cities_by_name.get(city_name)
    declared_unit = city_obj.settlement_unit if city_obj else r["temp_unit"]
    month = int(r["target_date"].split("-")[1])
    hemisphere = _hemisphere_for_lat(city_obj.lat) if city_obj else "N"
    value_f = _to_fahrenheit(r["temp_current"], r["temp_unit"])

    try:
        _GUARD.check_unit_consistency(
            city=city_name,
            raw_value=r["temp_current"],
            raw_unit=r["temp_unit"],
            declared_unit=declared_unit,
            target_date=date.fromisoformat(r["target_date"]),
        )
        # Layer 3 (check_seasonal_plausibility) deleted 2026-04-13.
        # See src/data/ingestion_guard.py module docstring for rationale.
        _GUARD.check_dst_boundary(
            city=city_name,
            local_time=r["local_dt"],
        )
    except UnitConsistencyViolation:
        return "unit"
    except DstBoundaryViolation:
        return "dst"
    except IngestionRejected:
        return "other"
    return None


# ---------------------------------------------------------------------------
# Write layer
# ---------------------------------------------------------------------------


_INSERT_SQL = """
    INSERT OR IGNORE INTO observation_instants
    (city, target_date, source, timezone_name, local_hour,
     local_timestamp, utc_timestamp, utc_offset_minutes,
     dst_active, is_ambiguous_local_hour, is_missing_local_hour,
     time_basis, temp_current, temp_unit, imported_at, raw_response)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _write_reading(conn, r: dict, rebuild_run_id: str) -> int:
    """Insert one hourly reading. Returns cursor.rowcount (0 if duplicate)."""
    imported_at = datetime.now(timezone.utc).isoformat()
    raw_response = json.dumps({"rebuild_run_id": rebuild_run_id})
    cur = conn.execute(_INSERT_SQL, (
        r["city"], r["target_date"], r["source"],
        r["timezone_name"], r["local_hour"],
        r["local_timestamp"], r["utc_timestamp"],
        r["utc_offset_minutes"], r["dst_active"],
        r["is_ambiguous_local_hour"], r["is_missing_local_hour"],
        "archive_hourly",
        r["temp_current"], r["temp_unit"],
        imported_at, raw_response,
    ))
    return cur.rowcount


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


_STAT_KEYS = (
    "fetched", "inserted", "skipped_duplicate",
    "fetch_errors", "guard_rejected_unit", "guard_rejected_seasonal",
    "guard_rejected_dst", "guard_rejected_other", "insert_errors",
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
    rebuild_run_id: str,
    dry_run: bool = False,
) -> dict:
    """Backfill one city via the K1-C guarded path."""
    end_date = date.today() - timedelta(days=2)  # Open-Meteo archive has ~2 day lag
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
                rejection = _validate_hourly_reading(city.name, r)
                if rejection:
                    chunk[f"guard_rejected_{rejection}"] += 1
                    continue
                if dry_run:
                    chunk["inserted"] += 1
                    continue
                try:
                    rowcount = _write_reading(conn, r, rebuild_run_id)
                    if rowcount > 0:
                        chunk["inserted"] += 1
                    else:
                        chunk["skipped_duplicate"] += 1
                except Exception as e:
                    chunk["insert_errors"] += 1
                    logger.error(
                        "Insert failed %s %s hr=%s: %s",
                        city.name, r["target_date"], r["local_hour"], e,
                    )
            if not dry_run:
                conn.commit()

        for k in _STAT_KEYS:
            city_totals[k] += chunk[k]

        print(
            f"    {current} → {chunk_end}: "
            f"fetched={chunk['fetched']} "
            f"inserted={chunk['inserted']} "
            f"dup={chunk['skipped_duplicate']} "
            f"guard=({chunk['guard_rejected_unit']}u/"
            f"{chunk['guard_rejected_seasonal']}s/"
            f"{chunk['guard_rejected_dst']}d/"
            f"{chunk['guard_rejected_other']}o) "
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
    parser.add_argument("--chunk-days", type=int, default=CHUNK_DAYS,
                        help=f"Open-Meteo request chunk size (default: {CHUNK_DAYS})")
    parser.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_REQUESTS,
                        help=f"Seconds between requests (default: {SLEEP_BETWEEN_REQUESTS})")
    parser.add_argument("--all-zeus", action="store_true",
                        help="Backfill all configured Zeus cities")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + validate but no DB writes and no ETL follow-up")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    rebuild_run_id = (
        f"backfill_hourly_openmeteo_"
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )

    if args.dry_run:
        print("[DRY RUN] No rows will be written and no ETL follow-up.")

    pool_map = {c.name: c for c in ALL_CITIES}

    conn = get_world_connection(write_class="bulk")
    init_schema(conn)

    if args.cities:
        targets = [pool_map[n] for n in args.cities if n in pool_map]
        for n in args.cities:
            if n not in pool_map:
                print(f"  SKIP: {n} not in ALL_CITIES")
    elif args.all_zeus:
        targets = list(ALL_CITIES)
    else:
        covered = {r[0] for r in conn.execute(
            "SELECT DISTINCT city FROM observation_instants "
            "WHERE source='openmeteo_archive_hourly'"
        ).fetchall()}
        targets = [c for c in ALL_CITIES if c.name not in covered]

    print(f"=== Open-Meteo Hourly Backfill (K1-C guarded) ===")
    print(f"Run ID:   {rebuild_run_id}")
    print(f"Targets:  {len(targets)} cities | {args.days} days back")
    print(f"Guard:    Layer 1 (unit) + Layer 3 (seasonal) + Layer 5 (DST boundary)")

    all_stats: list[dict] = []
    for city in targets:
        print(f"\n[{city.name}] unit={city.settlement_unit}")
        stats = backfill_city(
            city,
            days_back=args.days,
            conn=conn,
            chunk_days=args.chunk_days,
            sleep_seconds=args.sleep,
            rebuild_run_id=rebuild_run_id,
            dry_run=args.dry_run,
        )
        all_stats.append(stats)

    conn.close()

    # Summary
    print(f"\n=== Summary ===")
    if all_stats:
        totals = {k: sum(s[k] for s in all_stats) for k in _STAT_KEYS}
        for k in _STAT_KEYS:
            print(f"  {k:30s} {totals[k]}")

        print(f"\nPer-city (non-zero inserted only):")
        for s in all_stats:
            if s["inserted"] > 0:
                rej_total = (
                    s["guard_rejected_unit"]
                    + s["guard_rejected_seasonal"]
                    + s["guard_rejected_dst"]
                    + s["guard_rejected_other"]
                )
                print(f"  {s['city']:20s} inserted={s['inserted']:>6} "
                      f"guard_rej={rej_total}")

    # ETL follow-up — only if not dry-run and something was actually written
    if not args.dry_run:
        total_inserted = sum(s["inserted"] for s in all_stats)
        if total_inserted > 0:
            print(f"\n--- Rebuilding hourly_observations from observation_instants ---")
            try:
                from scripts.etl_hourly_observations import run_etl
                run_etl()
            except Exception as e:
                logger.error("etl_hourly_observations follow-up failed: %s", e)
                return 1
        else:
            print("\nNo rows inserted; skipping ETL follow-up.")
    else:
        print("\n[DRY RUN] Skipping etl_hourly_observations follow-up.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
