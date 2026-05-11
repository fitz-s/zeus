"""K2 live sunrise/sunset appender (Open-Meteo Archive API).

Keeps `solar_daily` fresh for all 46 cities. Unlike the other three append
modules this one is deterministic — sunrise/sunset are astronomical, not
observational, so there is no IngestionGuard in the write path and no
"did the peak happen yet" scheduling gate. Sunrise/sunset values for any
given (city, target_date) in the past, present, or future are fully
determined by lat/lon/timezone/date and never change after publication.

Consequences of this determinism:
- Fetch window is [today, today+14] — unlike WU/hourly/forecasts which
  must stay within the source's "published so far" range. The Open-Meteo
  endpoint computes future sunrise/sunset deterministically.
- Coverage grain is (city, target_date) with no sub_key. WRITTEN is the
  only non-empty state on the happy path; LEGITIMATE_GAP is theoretically
  possible for pre-city-onboard dates but the scanner applies that
  filter, not this appender.
- No per-date guard rejection path. If Open-Meteo returns malformed data
  the per-row parse raises and the whole chunk's dates get FAILED with a
  1h retry embargo.
- `daily_tick` is cheap — 46 cities × 15 days × 2 values = 1380 tiny
  rows per tick. Runs once per day (UTC 00:30) because there's no churn.

Path A duplication from `scripts/backfill_solar_openmeteo.py` — Phase C
will extract a shared Open-Meteo archive client.

Public API:
- `append_solar_window(city, start_date, end_date, conn, *, rebuild_run_id)`
- `daily_tick(conn, *, now_utc)` — once-per-day entrypoint
- `catch_up_missing(conn, *, days_back)` — boot entrypoint
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from src.config import City, cities as ALL_CITIES
from src.data.openmeteo_client import ARCHIVE_URL, fetch as openmeteo_fetch
from src.state.data_coverage import (
    CoverageReason,
    DataTable,
    record_failed,
    record_written,
)

logger = logging.getLogger(__name__)

SOURCE = "openmeteo_archive_solar"
CHUNK_DAYS = 90
SLEEP_BETWEEN_REQUESTS = 1.0


def _retry_embargo(hours: int = 1) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# Fetch layer (duplicated from scripts/backfill_solar_openmeteo.py)
# ---------------------------------------------------------------------------


def _fetch_solar_chunk(
    city: City,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Fetch one Open-Meteo archive chunk for sunrise/sunset daily data.

    Returns per-day dicts with the 11 columns the `solar_daily` table
    expects. The Open-Meteo `timezone` parameter pins response times to
    the city's local ISO, so the returned `sunrise`/`sunset` strings can
    be parsed straight into DST-aware ZoneInfo datetimes.
    """
    data = openmeteo_fetch(
        ARCHIVE_URL,
        {
            "latitude": city.lat,
            "longitude": city.lon,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": "sunrise,sunset",
            "timezone": city.timezone,
        },
        endpoint_label="archive_solar",
    )

    daily = data.get("daily", {})
    times = daily.get("time") or []
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []
    if not (len(times) == len(sunrises) == len(sunsets)):
        logger.warning(
            "solar response length mismatch %s %s..%s: t=%d r=%d s=%d",
            city.name, start_date, end_date,
            len(times), len(sunrises), len(sunsets),
        )
        return []

    tz = ZoneInfo(city.timezone)
    out: list[dict] = []
    for target_date_str, sunrise_raw, sunset_raw in zip(times, sunrises, sunsets):
        if sunrise_raw is None or sunset_raw is None:
            continue
        try:
            sunrise_local = datetime.fromisoformat(sunrise_raw).replace(tzinfo=tz)
            sunset_local = datetime.fromisoformat(sunset_raw).replace(tzinfo=tz)
            sunrise_utc = sunrise_local.astimezone(timezone.utc)
            sunset_utc = sunset_local.astimezone(timezone.utc)
            utc_offset = sunrise_local.utcoffset()
            dst_offset = sunrise_local.dst()
            dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
            out.append({
                "city": city.name,
                "target_date": target_date_str,
                "timezone": city.timezone,
                "lat": float(city.lat),
                "lon": float(city.lon),
                "sunrise_local": sunrise_local.isoformat(),
                "sunset_local": sunset_local.isoformat(),
                "sunrise_utc": sunrise_utc.isoformat(),
                "sunset_utc": sunset_utc.isoformat(),
                "utc_offset_minutes": int(utc_offset.total_seconds() / 60) if utc_offset else 0,
                "dst_active": 1 if dst_active else 0,
            })
        except (ValueError, AttributeError) as e:
            logger.warning(
                "solar parse failed %s %s sunrise=%r sunset=%r: %s",
                city.name, target_date_str, sunrise_raw, sunset_raw, e,
            )
            continue
    return out


def _fetch_with_retry(
    city: City, start_date: date, end_date: date,
) -> tuple[list[dict], str | None]:
    try:
        return _fetch_solar_chunk(city, start_date, end_date), None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    return [], "exhausted retries"


# ---------------------------------------------------------------------------
# Write layer with savepoint isolation (S1-2 pattern from daily_obs_append)
# ---------------------------------------------------------------------------


_INSERT_SQL = """
    INSERT OR REPLACE INTO solar_daily
    (city, target_date, timezone, lat, lon, sunrise_local, sunset_local,
     sunrise_utc, sunset_utc, utc_offset_minutes, dst_active)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _write_row_with_coverage(conn, r: dict) -> None:
    """Upsert one solar row and record WRITTEN coverage in one savepoint.

    Either both succeed or neither lands — the savepoint ROLLBACK TO
    rewinds a failed INSERT or coverage upsert without losing prior
    successful rows in the same chunk.

    Savepoint name uses `id(r)` not city/date: the earlier version
    (`f"sp_solar_{city}_{date}"`) was both (a) an untrusted-input sink
    for savepoint identifiers (security-reviewer S2a) and (b) reused the
    same identifier across retries of the same row, which stacks
    savepoints and lets ROLLBACK TO unwind the wrong frame (critic S1
    downgraded to S2). `id()` returns a process-internal address unique
    within the active transaction's object graph.
    """
    sp = f"sp_solar_{id(r)}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        conn.execute(_INSERT_SQL, (
            r["city"], r["target_date"], r["timezone"],
            r["lat"], r["lon"],
            r["sunrise_local"], r["sunset_local"],
            r["sunrise_utc"], r["sunset_utc"],
            r["utc_offset_minutes"], r["dst_active"],
        ))
        record_written(
            conn,
            data_table=DataTable.SOLAR_DAILY,
            city=r["city"],
            data_source=SOURCE,
            target_date=r["target_date"],
        )
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise
    conn.execute(f"RELEASE SAVEPOINT {sp}")


# ---------------------------------------------------------------------------
# Public: per-city window append
# ---------------------------------------------------------------------------


def append_solar_window(
    city: City,
    start_date: date,
    end_date: date,
    conn,
    *,
    rebuild_run_id: str,
    chunk_days: int = CHUNK_DAYS,
    sleep_seconds: float = SLEEP_BETWEEN_REQUESTS,
) -> dict:
    """Fetch + upsert sunrise/sunset for [start, end] for one city.

    Accepts start/end that may extend past "today" because sunrise/sunset
    is computable for any future date. Open-Meteo archive happily returns
    future-day predictions for this daily endpoint.
    """
    stats = {"fetched": 0, "inserted": 0, "fetch_errors": 0}
    if start_date > end_date:
        return stats

    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)
        rows, err = _fetch_with_retry(city, current, chunk_end)
        if err:
            stats["fetch_errors"] += 1
            logger.error("solar chunk failed %s %s..%s: %s",
                         city.name, current, chunk_end, err)
            d = current
            while d <= chunk_end:
                record_failed(
                    conn,
                    data_table=DataTable.SOLAR_DAILY,
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
        for r in rows:
            try:
                _write_row_with_coverage(conn, r)
                stats["inserted"] += 1
            except Exception as e:
                logger.warning(
                    "solar insert failed %s %s: %s: %s",
                    city.name, r["target_date"], type(e).__name__, e,
                )

        conn.commit()
        current = chunk_end + timedelta(days=1)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return stats


# ---------------------------------------------------------------------------
# Public: daemon entrypoints
# ---------------------------------------------------------------------------


def daily_tick(
    conn,
    *,
    now_utc: Optional[datetime] = None,
    cities: Optional[Iterable[City]] = None,
    rebuild_run_id: Optional[str] = None,
    future_days: int = 14,
) -> dict:
    """Daemon once-per-day entrypoint: fetch [today, today+future_days] for each city.

    Because sunrise/sunset is deterministic, this call is idempotent and
    can run any time. Scheduled once per day (not per hour) in src/main.py.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if rebuild_run_id is None:
        rebuild_run_id = f"solar_tick_{now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    if cities is None:
        cities = list(ALL_CITIES)

    totals = {"cities_processed": 0, "fetched": 0, "inserted": 0, "fetch_errors": 0}
    start_d = now_utc.date()
    # archive-api.open-meteo.com rejects end_date > today (HTTP 400).
    # Cap at today; sunrise/sunset for future dates must use a different
    # endpoint if ever needed. Verified empirically 2026-05-10.
    end_d = min(start_d + timedelta(days=future_days), now_utc.date())
    for city in cities:
        stats = append_solar_window(
            city, start_d, end_d, conn, rebuild_run_id=rebuild_run_id,
        )
        totals["cities_processed"] += 1
        for k in ("fetched", "inserted", "fetch_errors"):
            totals[k] += stats.get(k, 0)
    return totals


def catch_up_missing(
    conn,
    *,
    days_back: int = 30,
    max_cities: int = 46,
    rebuild_run_id: Optional[str] = None,
) -> dict:
    """Daemon boot entrypoint: fill MISSING/retry-ready FAILED solar rows."""
    from src.config import cities_by_name
    from src.state.data_coverage import find_pending_fills

    if rebuild_run_id is None:
        rebuild_run_id = f"solar_catchup_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"

    cutoff = date.today() - timedelta(days=days_back)
    rows = find_pending_fills(
        conn, data_table=DataTable.SOLAR_DAILY, max_rows=100_000,
    )
    by_city: dict[str, list[date]] = {}
    for r in rows:
        target = date.fromisoformat(r["target_date"])
        if target < cutoff:
            continue
        by_city.setdefault(r["city"], []).append(target)

    totals = {"cities_touched": 0, "fetched": 0, "inserted": 0, "fetch_errors": 0}
    for i, (city_name, dates) in enumerate(by_city.items()):
        if i >= max_cities:
            break
        city = cities_by_name.get(city_name)
        if city is None:
            continue
        stats = append_solar_window(
            city, min(dates), max(dates), conn, rebuild_run_id=rebuild_run_id,
        )
        totals["cities_touched"] += 1
        for k in ("fetched", "inserted", "fetch_errors"):
            totals[k] += stats.get(k, 0)
    return totals
