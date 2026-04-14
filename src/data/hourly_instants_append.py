"""K2 live hourly-instants appender (Open-Meteo Archive API).

Keeps `observation_instants` fresh for all 46 cities after the initial
historical backfill. `observation_instants` is a per-hour physical table
(one row per city × utc_timestamp), but coverage tracking is intentionally
coarsened to daily grain (one `data_coverage` row per city × local_date)
because:

1. Per-hour coverage rows would create ~20k rows per city per year
   (46 × 365 × 24 = 403k / year). Daily grain is 46 × 365 = 16.8k / year.
2. The hole scanner's interesting question is "does this city have any
   hourly data for date X?", not "is hour 13:00 specifically missing?".
3. Dropped individual hours (DST spring-forward) would false-positive as
   holes at per-hour grain.

Coverage contract at daily grain: a (city, local_date) row flips to
WRITTEN as soon as the live fetch for that date returns at least 20 hours
(less than 24 allows for DST spring-forward's 23-hour day).

This module intentionally duplicates the fetch + guard logic from
`scripts/backfill_hourly_openmeteo.py` (Path A). Phase C will extract a
shared `src/data/openmeteo_archive_client.py`.

Public API:
- `append_hourly_window(city, start_date, end_date, conn, *, rebuild_run_id)`
  — fetch and upsert hourly rows for one city over a date range
- `hourly_tick(conn, *, now_utc)` — daemon per-hour entrypoint that
  sweeps all 46 cities with a per-city dynamic end_date (respects each
  city's local day boundary)
- `catch_up_missing(conn, *, days_back)` — daemon boot entrypoint
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import httpx

from src.config import City, cities as ALL_CITIES
from src.data.ingestion_guard import (
    DstBoundaryViolation,
    IngestionGuard,
    IngestionRejected,
    UnitConsistencyViolation,
)
from src.signal.diurnal import _is_missing_local_hour
from src.state.data_coverage import (
    CoverageReason,
    DataTable,
    record_failed,
    record_legitimate_gap,
    record_written,
)

logger = logging.getLogger(__name__)

OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE = "openmeteo_archive_hourly"
CHUNK_DAYS = 90
SLEEP_BETWEEN_REQUESTS = 1.0
FETCH_RETRY_COUNT = 2
FETCH_RETRY_BACKOFF_SEC = 2.0

#: Minimum hours a local_date must have to be marked WRITTEN. DST
#: spring-forward days have 23 hours; the scanner accepts any day with
#: ≥20 hours as covered to allow for the occasional Open-Meteo null.
_MIN_HOURS_PER_DAY_FOR_WRITTEN = 20

_GUARD = IngestionGuard()


def _hemisphere_for_lat(lat: float) -> str:
    return "N" if lat >= 0 else "S"


def _to_fahrenheit(value: float, unit: str) -> float:
    if unit == "C":
        return value * 9 / 5 + 32
    if unit == "K":
        return (value - 273.15) * 9 / 5 + 32
    return value


def _retry_embargo(hours: int = 1) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# Fetch layer (duplicated from scripts/backfill_hourly_openmeteo.py)
# ---------------------------------------------------------------------------


def _fetch_hourly_chunk(
    city: City,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Fetch one Open-Meteo archive chunk. Returns per-hour row dicts.

    Each dict has all fields needed for `observation_instants` INSERT plus
    a `local_dt` datetime used by Layer 5 DST gap detection. The `local_dt`
    is constructed via `ZoneInfo` from the Open-Meteo-returned local ISO
    string so Python's DST resolution applies correctly — hardcoding the
    DST flags (as the pre-K1-C version did) silently produces 1-hour
    offsets for every DST day.
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
            is_missing = _is_missing_local_hour(local_dt, tz)
            is_ambiguous = bool(getattr(local_dt, "fold", 0))
            utc_offset = local_dt.utcoffset()
            dst_offset = local_dt.dst()
            dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
            utc_dt = local_dt.astimezone(timezone.utc)

            out.append({
                "city": city.name,
                "target_date": local_dt.date().isoformat(),
                "source": SOURCE,
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
                "local_dt": local_dt,
            })
        except (ValueError, AttributeError) as e:
            logger.debug("parse failed %s %s: %s", city.name, raw_time, e)
            continue
    return out


def _fetch_with_retry(
    city: City, start_date: date, end_date: date,
) -> tuple[list[dict], str | None]:
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            return _fetch_hourly_chunk(city, start_date, end_date), None
        except httpx.HTTPError as e:
            if attempt < FETCH_RETRY_COUNT:
                time.sleep(FETCH_RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            return [], f"http error after {FETCH_RETRY_COUNT + 1} tries: {e}"
        except Exception as e:
            return [], f"unexpected error: {type(e).__name__}: {e}"
    return [], "exhausted retries"


# ---------------------------------------------------------------------------
# Guard layer (Layers 1 + 5 only — same as backfill after Layer 3 removal)
# ---------------------------------------------------------------------------


def _validate_hourly_reading(city_name: str, r: dict) -> str | None:
    """Return rejection category string or None if the row passes.

    Layer 1 (unit + earth records) applied because the Open-Meteo response
    can occasionally contain sentinel values (99999, -9999) that must be
    caught. Layer 5 (DST boundary) applied because spring-forward hours
    must be explicitly rejected — they represent a local time that does
    not exist, so any value there is semantically wrong.

    Layer 2 (physical_bounds) skipped: hourly readings can legitimately
    fall below p01 at night (4am minimum is below daily max p01 by design).
    Layer 3 (seasonal_plausibility) deleted 2026-04-13. Layer 4
    (collection_timing) skipped: archive backfill is always "after the
    target date" by construction, so timing check is trivially true.
    """
    from src.config import cities_by_name
    city_obj = cities_by_name.get(city_name)
    declared_unit = city_obj.settlement_unit if city_obj else r["temp_unit"]

    try:
        _GUARD.check_unit_consistency(
            city=city_name,
            raw_value=r["temp_current"],
            raw_unit=r["temp_unit"],
            declared_unit=declared_unit,
        )
        _GUARD.check_dst_boundary(city=city_name, local_time=r["local_dt"])
    except UnitConsistencyViolation:
        return "unit"
    except DstBoundaryViolation:
        return "dst"
    except IngestionRejected:
        return "other"
    return None


# ---------------------------------------------------------------------------
# Write layer — physical table + per-date data_coverage rollup
# ---------------------------------------------------------------------------


_INSERT_SQL = """
    INSERT OR REPLACE INTO observation_instants
    (city, target_date, source, timezone_name, local_hour,
     local_timestamp, utc_timestamp, utc_offset_minutes,
     dst_active, is_ambiguous_local_hour, is_missing_local_hour,
     time_basis, temp_current, temp_unit, imported_at, raw_response)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _write_row(conn, r: dict, rebuild_run_id: str) -> None:
    imported_at = datetime.now(timezone.utc).isoformat()
    raw_response = json.dumps({"rebuild_run_id": rebuild_run_id})
    conn.execute(_INSERT_SQL, (
        r["city"], r["target_date"], r["source"],
        r["timezone_name"], r["local_hour"],
        r["local_timestamp"], r["utc_timestamp"],
        r["utc_offset_minutes"], r["dst_active"],
        r["is_ambiguous_local_hour"], r["is_missing_local_hour"],
        "archive_hourly",
        r["temp_current"], r["temp_unit"],
        imported_at, raw_response,
    ))


def _rollup_dates_written(rows: list[dict]) -> dict[str, int]:
    """Count hours written per local_date in this batch.

    Used to decide whether each (city, local_date) should be marked
    WRITTEN in data_coverage. Only dates with ≥ _MIN_HOURS_PER_DAY_FOR_WRITTEN
    flip to WRITTEN; partial-day rows remain un-flipped and the scanner
    will pick them up on the next pass.
    """
    counts: dict[str, int] = {}
    for r in rows:
        td = r["target_date"]
        counts[td] = counts.get(td, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Public: per-city window append
# ---------------------------------------------------------------------------


def append_hourly_window(
    city: City,
    start_date: date,
    end_date: date,
    conn,
    *,
    rebuild_run_id: str,
    chunk_days: int = CHUNK_DAYS,
    sleep_seconds: float = SLEEP_BETWEEN_REQUESTS,
) -> dict:
    """Fetch and write all hourly rows for one city over [start, end].

    Chunks the range into `chunk_days`-sized API calls (Open-Meteo has a
    soft limit around 90 days per request). Per-row IngestionGuard
    rejections are recorded but do not halt the chunk; per-chunk fetch
    failures halt the chunk and record FAILED for each target_date in it.

    After writing, rolls up per-local_date hour counts and flips each
    (city, local_date) with ≥ _MIN_HOURS_PER_DAY_FOR_WRITTEN rows to
    WRITTEN in data_coverage. Dates with fewer rows remain uncovered so
    the next tick / scanner pass retries them.
    """
    stats = {
        "fetched": 0, "inserted": 0, "guard_rejected": 0,
        "fetch_errors": 0, "dates_marked_written": 0,
    }
    if start_date > end_date:
        return stats

    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)
        rows, err = _fetch_with_retry(city, current, chunk_end)
        if err:
            stats["fetch_errors"] += 1
            logger.error("hourly chunk failed %s %s..%s: %s",
                         city.name, current, chunk_end, err)
            # Mark every date in this chunk FAILED so scanner retries.
            d = current
            while d <= chunk_end:
                record_failed(
                    conn,
                    data_table=DataTable.OBSERVATION_INSTANTS,
                    city=city.name,
                    data_source=SOURCE,
                    target_date=d,
                    reason=CoverageReason.NETWORK_ERROR,
                    retry_after=_retry_embargo(hours=1),
                )
                d += timedelta(days=1)
            current = chunk_end + timedelta(days=1)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            continue

        stats["fetched"] += len(rows)
        kept: list[dict] = []
        for r in rows:
            rejection = _validate_hourly_reading(city.name, r)
            if rejection:
                stats["guard_rejected"] += 1
                continue
            kept.append(r)

        # Track only rows whose INSERT actually succeeded, so the
        # WRITTEN rollup below is computed from reality, not intent.
        # The earlier version counted from `kept` (all post-validate
        # rows) which would mark a date WRITTEN even if several of its
        # hours had INSERT failures. Same-spirit fix as the S1-2
        # savepoint pattern in daily_obs_append.py.
        written_rows: list[dict] = []
        for r in kept:
            try:
                _write_row(conn, r, rebuild_run_id)
                stats["inserted"] += 1
                written_rows.append(r)
            except Exception as e:
                logger.warning(
                    "hourly insert failed %s %s hr=%s: %s: %s",
                    city.name, r["target_date"], r["local_hour"],
                    type(e).__name__, e,
                )

        # Roll up per-date from written_rows (not kept) and flip to
        # WRITTEN where the success count hits threshold.
        counts = _rollup_dates_written(written_rows)
        for target_date_str, n_hours in counts.items():
            if n_hours >= _MIN_HOURS_PER_DAY_FOR_WRITTEN:
                record_written(
                    conn,
                    data_table=DataTable.OBSERVATION_INSTANTS,
                    city=city.name,
                    data_source=SOURCE,
                    target_date=target_date_str,
                )
                stats["dates_marked_written"] += 1

        conn.commit()
        current = chunk_end + timedelta(days=1)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return stats


# ---------------------------------------------------------------------------
# Public: daemon entrypoints
# ---------------------------------------------------------------------------


def _city_yesterday_local(city: City, now_utc: datetime) -> date:
    """Return the most recent *fully completed* local date for this city.

    This is the per-city dynamic end_date strategy: rather than using a
    global `today - N` which under-counts eastward timezones, compute the
    upper bound per city from `now_utc.astimezone(tz) - 1 day`. At 12:00
    UTC, Auckland (UTC+13) sees today-1 = yesterday_NZDT (local), while
    Honolulu (UTC-10) also sees today-1 = yesterday_HST (local), even
    though their UTC "yesterdays" are different.
    """
    tz = ZoneInfo(city.timezone)
    return (now_utc.astimezone(tz) - timedelta(days=1)).date()


def hourly_tick(
    conn,
    *,
    now_utc: Optional[datetime] = None,
    cities: Optional[Iterable[City]] = None,
    rebuild_run_id: Optional[str] = None,
    days_window: int = 3,
) -> dict:
    """Daemon per-hour entrypoint: rolling [today-N, today-1] window append.

    For each city:
      - start = max(local today - days_window, global_floor)
      - end   = local yesterday (per-city)
      - append_hourly_window fetches and upserts
      - data_coverage flipped per local_date

    Default `days_window=3` means every hour we re-pull the last 3 local
    days per city. This is idempotent (INSERT OR REPLACE) and catches
    any late Open-Meteo data promotions, 1-hour network blips, etc.

    Returns aggregate stats across all 46 cities.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if rebuild_run_id is None:
        rebuild_run_id = f"hourly_tick_{now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    if cities is None:
        cities = list(ALL_CITIES)

    totals = {
        "cities_processed": 0, "fetched": 0, "inserted": 0,
        "guard_rejected": 0, "fetch_errors": 0, "dates_marked_written": 0,
    }
    for city in cities:
        end_d = _city_yesterday_local(city, now_utc)
        start_d = end_d - timedelta(days=days_window - 1)
        stats = append_hourly_window(
            city, start_d, end_d, conn, rebuild_run_id=rebuild_run_id,
        )
        totals["cities_processed"] += 1
        for k in ("fetched", "inserted", "guard_rejected",
                  "fetch_errors", "dates_marked_written"):
            totals[k] += stats.get(k, 0)

    return totals


def catch_up_missing(
    conn,
    *,
    days_back: int = 30,
    max_cities: int = 46,
    rebuild_run_id: Optional[str] = None,
) -> dict:
    """Daemon boot entrypoint: fill MISSING / retry-ready FAILED rows.

    Groups pending fills from `data_coverage` by city, computes the
    contiguous min/max target_date per city, and calls
    `append_hourly_window` once per city. Caps the work at `max_cities`
    to bound one scan pass to a reasonable API budget.
    """
    from src.config import cities_by_name
    from src.state.data_coverage import find_pending_fills

    if rebuild_run_id is None:
        rebuild_run_id = f"catch_up_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"

    cutoff = date.today() - timedelta(days=days_back)
    rows = find_pending_fills(
        conn, data_table=DataTable.OBSERVATION_INSTANTS, max_rows=100_000,
    )

    by_city: dict[str, list[date]] = {}
    for r in rows:
        target = date.fromisoformat(r["target_date"])
        if target < cutoff:
            continue
        by_city.setdefault(r["city"], []).append(target)

    totals = {
        "cities_touched": 0, "fetched": 0, "inserted": 0,
        "guard_rejected": 0, "fetch_errors": 0, "dates_marked_written": 0,
    }
    for i, (city_name, dates) in enumerate(by_city.items()):
        if i >= max_cities:
            break
        city = cities_by_name.get(city_name)
        if city is None:
            continue
        # Contiguous window from min..max fills both the specific holes
        # and any intermediate dates whose MISSING was below the `days_back`
        # cutoff. INSERT OR REPLACE makes that safe.
        stats = append_hourly_window(
            city, min(dates), max(dates), conn, rebuild_run_id=rebuild_run_id,
        )
        totals["cities_touched"] += 1
        for k in ("fetched", "inserted", "guard_rejected",
                  "fetch_errors", "dates_marked_written"):
            totals[k] += stats.get(k, 0)

    return totals
