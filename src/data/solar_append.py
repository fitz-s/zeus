"""K2 live sunrise/sunset appender — dual-path: Open-Meteo archive + NOAA.

Keeps `solar_daily` fresh for all 46 cities. Unlike the other three append
modules this one is deterministic — sunrise/sunset are astronomical, not
observational, so there is no IngestionGuard in the write path and no
"did the peak happen yet" scheduling gate. Sunrise/sunset values for any
given (city, target_date) in the past, present, or future are fully
determined by lat/lon/timezone/date and never change after publication.

Consequences of this determinism:
- `daily_tick` writes [today, today+14]: today via Open-Meteo archive API
  (authoritative settlement source) and today+1..today+14 via stdlib NOAA
  solar equations (no HTTP). archive-api.open-meteo.com rejects
  end_date > today with HTTP 400; the NOAA path provides the forward window
  so day0_capture never starves. Verified 2026-05-19 (alpha-loss postmortem).
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
import math
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
# NOAA astronomical path (stdlib-only, used for target_date > today)
# ---------------------------------------------------------------------------


def _noaa_sunrise_sunset_utc(
    target: date, lat: float, lon: float
) -> tuple[datetime, datetime]:
    """Approximate sunrise/sunset UTC using NOAA solar equations (stdlib-only).

    Copied from scripts/onboard_cities.py — kept local to avoid src→scripts
    import coupling. The math is deterministic and requires no network call.
    """
    day_of_year = target.timetuple().tm_yday
    gamma = 2.0 * math.pi / 365.0 * (day_of_year - 1)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    lat_rad = math.radians(lat)
    zenith = math.radians(90.833)
    cos_hour_angle = (
        math.cos(zenith) / (math.cos(lat_rad) * math.cos(decl))
        - math.tan(lat_rad) * math.tan(decl)
    )
    cos_hour_angle = max(-1.0, min(1.0, cos_hour_angle))
    hour_angle = math.degrees(math.acos(cos_hour_angle))
    solar_noon_utc_minutes = 720.0 - 4.0 * lon - eqtime
    sunrise_minutes = solar_noon_utc_minutes - 4.0 * hour_angle
    sunset_minutes = solar_noon_utc_minutes + 4.0 * hour_angle
    midnight = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
    return (
        midnight + timedelta(minutes=sunrise_minutes),
        midnight + timedelta(minutes=sunset_minutes),
    )


def _build_noaa_row(city: City, target: date) -> dict:
    """Build a solar_daily row dict using NOAA computation (no network)."""
    sunrise_utc, sunset_utc = _noaa_sunrise_sunset_utc(target, city.lat, city.lon)
    tz = ZoneInfo(city.timezone)
    sunrise_local = sunrise_utc.astimezone(tz)
    sunset_local = sunset_utc.astimezone(tz)
    utc_offset = sunrise_local.utcoffset()
    dst_offset = sunrise_local.dst()
    dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
    return {
        "city": city.name,
        "target_date": target.isoformat(),
        "timezone": city.timezone,
        "lat": float(city.lat),
        "lon": float(city.lon),
        "sunrise_local": sunrise_local.isoformat(),
        "sunset_local": sunset_local.isoformat(),
        "sunrise_utc": sunrise_utc.isoformat(),
        "sunset_utc": sunset_utc.isoformat(),
        "utc_offset_minutes": int(utc_offset.total_seconds() / 60) if utc_offset else 0,
        "dst_active": 1 if dst_active else 0,
    }


def _append_solar_future_window(
    city: City,
    start_date: date,
    end_date: date,
    conn,
    *,
    rebuild_run_id: str,
) -> dict:
    """Write NOAA-computed rows for [start_date, end_date] (future dates, no HTTP).

    Used by daily_tick for target_date > today. Open-Meteo archive rejects
    future dates (HTTP 400); NOAA equations are deterministic for any date.
    """
    stats = {"inserted": 0, "errors": 0}
    if start_date > end_date:
        return stats
    d = start_date
    while d <= end_date:
        try:
            r = _build_noaa_row(city, d)
            _write_row_with_coverage(conn, r)
            stats["inserted"] += 1
        except Exception as e:
            stats["errors"] += 1
            logger.warning(
                "noaa solar insert failed %s %s: %s: %s",
                city.name, d, type(e).__name__, e,
            )
        d += timedelta(days=1)
    conn.commit()
    return stats


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
    """Fetch + upsert sunrise/sunset for [start, end] for one city via Open-Meteo archive.

    start/end must be <= today: archive-api.open-meteo.com rejects
    end_date > today with HTTP 400. For future dates use
    _append_solar_future_window (NOAA astronomical path, no HTTP).
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
    """Daemon once-per-day entrypoint: write [today, today+future_days] for each city.

    Dual-path per date:
    - today: Open-Meteo archive API (authoritative settlement source).
    - today+1..today+future_days: NOAA astronomical equations (no HTTP).

    archive-api.open-meteo.com rejects end_date > today (HTTP 400), so the
    forward window is computed locally. Because sunrise/sunset is
    deterministic, this call is idempotent and can run any time. Scheduled
    once per day (not per hour) in src/main.py.

    Returned stats keys:
    - fetched/inserted/fetch_errors: Open-Meteo archive path counters.
    - noaa_errors: NOAA local-compute or insert failures (no network).
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if rebuild_run_id is None:
        rebuild_run_id = f"solar_tick_{now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    if cities is None:
        cities = list(ALL_CITIES)

    totals = {
        "cities_processed": 0,
        "fetched": 0,
        "inserted": 0,
        "fetch_errors": 0,   # Open-Meteo network failures
        "noaa_errors": 0,    # NOAA local-compute / insert failures
    }
    today = now_utc.date()
    future_end = today + timedelta(days=future_days)
    for city in cities:
        # Present: Open-Meteo archive (authoritative settlement source).
        # archive-api.open-meteo.com rejects end_date > today (HTTP 400),
        # so only today's row is fetched here. Verified empirically 2026-05-10.
        archive_stats = append_solar_window(
            city, today, today, conn, rebuild_run_id=rebuild_run_id,
        )
        totals["fetched"] += archive_stats.get("fetched", 0)
        totals["inserted"] += archive_stats.get("inserted", 0)
        totals["fetch_errors"] += archive_stats.get("fetch_errors", 0)

        # Future: NOAA astronomical equations (no network, stdlib-only).
        # Writes today+1 through today+future_days so day0_capture never
        # starves waiting for tomorrow's solar context.
        future_stats = _append_solar_future_window(
            city,
            today + timedelta(days=1),
            future_end,
            conn,
            rebuild_run_id=rebuild_run_id,
        )
        totals["inserted"] += future_stats.get("inserted", 0)
        totals["noaa_errors"] += future_stats.get("errors", 0)

        totals["cities_processed"] += 1
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
