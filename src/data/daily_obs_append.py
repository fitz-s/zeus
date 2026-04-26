"""K2 live daily-observation appender (WU ICAO + HKO + Ogimet METAR/SYNOP).

Replaces the broken `src/data/wu_daily_collector.py` for live ingestion of
daily high/low temperatures into the `observations` table. Handles the two
distinct daily-obs source lanes Zeus uses:

1. WU ICAO history for cities whose settlement_source_type == "wu_icao" in
   cities.json. Uses the same
   `v1/location/{ICAO}:9:{CC}/observations/historical.json` endpoint as
   `scripts/backfill_wu_daily_all.py` — NOT the older `timeseries.json`
   endpoint that `wu_daily_collector.py` used — so a live write and a
   backfill write for the same (city, date) produce bit-identical rows.

2. HKO Open Data API for Hong Kong only. HKO is the authoritative
   Polymarket settlement source for HK; VHHH airport (which WU would hit
   under the ICAO key) differs from HKO HQ by 1-3°C due to urban heat
   island, so HK must never go through the WU lane.

Contract:
- Every successful insert writes TWO rows: the observations INSERT and a
  data_coverage WRITTEN upsert, in the same SQLite transaction, so the K2
  coverage ledger never diverges from the physical table.
- Every failed fetch writes a data_coverage FAILED row with a retry_after
  embargo so the scanner doesn't hammer a rate-limited upstream.
- HKO incomplete-flag days ("#"/"***") are pinned as LEGITIMATE_GAP, not
  retried.
- All rows flow through `ObservationAtom` + `IngestionGuard.validate()`,
  producing `authority='VERIFIED'` — unlike the legacy collector which
  silently landed rows as `UNVERIFIED` and made them dead data for
  calibration.

Station config (ICAO code, country code, settlement unit) is read from
cities.json via src.config.cities_by_name — that is the single source of
truth. The local CITY_STATIONS parallel map has been removed (Phase 3 R-G).
Phase C of the K2 packet will extract the WU/HKO fetch helpers into shared
clients (wu_icao_client.py, hko_client.py) so backfill and live append share
one implementation.

Public API:
- `append_wu_city(city_name, target_dates, conn, *, rebuild_run_id)` —
  fetch a specific date set for one WU city and write atoms + coverage.
- `append_hko_months(year_months, conn, *, rebuild_run_id)` — fetch one
  or more HKO months (each is a CLMMAXT + CLMMINT pair) and write.
- `daily_tick(conn, *, now_utc)` — daemon-facing per-hour entrypoint:
  uses `WuDailyScheduler` to find WU cities whose local peak+4h window
  is active, fetches them for today, plus HKO current+prior month on
  every call (idempotent).
- `catch_up_missing(conn, *, days_back, max_cities)` — daemon boot
  entrypoint: queries data_coverage for MISSING / retry-ready FAILED
  rows within `days_back` and fills them via the same write path.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import httpx
import requests

from src.calibration.manager import season_from_date
from src.config import cities_by_name
from src.data.daily_observation_writer import insert_or_update_current_observation
from src.data.ingestion_guard import IngestionGuard, IngestionRejected
# G10 helper-extraction (2026-04-26, con-nyx MAJOR #1): import from canonical
# location to avoid transitively pulling src.signal into the ingest lane.
from src.contracts.dst_semantics import _is_missing_local_hour
from src.state.data_coverage import (
    CoverageReason,
    DataTable,
    record_failed,
    record_legitimate_gap,
    record_written,
)
from src.types.observation_atom import ObservationAtom

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WU ICAO client (duplicated from scripts/backfill_wu_daily_all.py — Path A)
# ---------------------------------------------------------------------------

# WU ICAO historical public web key. This is NOT a secret — it is the
# same key that wunderground.com's own browser UI embeds in every
# ICAO historical page. A prior "Security S1 fix" mis-classified it as
# a leaked secret and removed the default, breaking the daemon on any
# deploy without an explicit env-var override (operator 2026-04-21
# correction: "wu key 是公开的，可能你之前修复 100 个 bug 的时候当作敏感
# 信息删除了"). Restored as a documented public default. Operators can
# still override via the WU_API_KEY env var to route through a paid
# WU account if needed.
_WU_API_KEY_ENV = "WU_API_KEY"
_WU_PUBLIC_WEB_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
WU_API_KEY = os.environ.get(_WU_API_KEY_ENV) or _WU_PUBLIC_WEB_KEY
WU_ICAO_HISTORY_URL = (
    "https://api.weather.com/v1/location/{icao}:9:{cc}/observations/historical.json"
)
WU_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

#: WU data_source string as it appears in both `observations.source` and
#: `data_coverage.data_source`. Must match the backfill script exactly.
WU_SOURCE = "wu_icao_history"


@dataclass(frozen=True)
class WuDailyFetchResult:
    """Structured WU fetch result.

    `payload` is populated only when the HTTP response was usable.  A
    `failure_reason` means the upstream request or response was not trustworthy
    enough to interpret as business-empty data.
    """

    payload: dict[str, tuple[float, float]]
    failure_reason: str | None = None
    retryable: bool = False
    auth_failed: bool = False
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.failure_reason is not None


def _fetch_wu_icao_daily_highs_lows(
    icao: str,
    cc: str,
    start_date: date,
    end_date: date,
    unit: str,
    timezone_name: str,
) -> WuDailyFetchResult:
    """Fetch local-date (high, low) from the WU ICAO history endpoint.

    Returns a structured result.  On success, `payload` is
    {ISO_date_str: (high, low)} with both values in the requested unit.
    Local-date bucketing converts the UTC epoch into the city's timezone
    before grouping, so a fetch crossing a UTC midnight still attributes each
    observation to the right local day.
    """
    # WU_API_KEY always set (public fallback or env-var override), so no
    # runtime guard needed. Kept as an assertion for defensive confidence.
    assert WU_API_KEY, "WU_API_KEY resolved empty; _WU_PUBLIC_WEB_KEY fallback broken?"
    url = WU_ICAO_HISTORY_URL.format(icao=icao, cc=cc)
    unit_code = "m" if unit == "C" else "e"

    try:
        resp = httpx.get(
            url,
            params={
                "apiKey": WU_API_KEY,
                "units": unit_code,
                "startDate": start_date.strftime("%Y%m%d"),
                "endDate": end_date.strftime("%Y%m%d"),
            },
            timeout=30.0,
            headers=WU_HEADERS,
        )
        if resp.status_code in (401, 403):
            return WuDailyFetchResult(
                payload={},
                failure_reason=CoverageReason.AUTH_ERROR,
                retryable=False,
                auth_failed=True,
                error=f"HTTP {resp.status_code}",
            )
        if resp.status_code == 429:
            return WuDailyFetchResult(
                payload={},
                failure_reason=CoverageReason.HTTP_429,
                retryable=True,
                error="HTTP 429",
            )
        if 500 <= resp.status_code <= 599:
            return WuDailyFetchResult(
                payload={},
                failure_reason=CoverageReason.HTTP_5XX,
                retryable=True,
                error=f"HTTP {resp.status_code}",
            )
        if resp.status_code != 200:
            return WuDailyFetchResult(
                payload={},
                failure_reason=CoverageReason.NETWORK_ERROR,
                retryable=True,
                error=f"HTTP {resp.status_code}",
            )

        try:
            body = resp.json()
        except ValueError as e:
            return WuDailyFetchResult(
                payload={},
                failure_reason=CoverageReason.PARSE_ERROR,
                retryable=True,
                error=f"json parse failed: {e}",
            )
        observations = body.get("observations", [])
        if not observations:
            return WuDailyFetchResult(payload={})

        tz = ZoneInfo(timezone_name)
        highs: dict[str, float] = {}
        lows: dict[str, float] = {}
        for obs in observations:
            temp = obs.get("temp")
            epoch = obs.get("valid_time_gmt")
            if temp is None or epoch is None:
                continue
            local_date = datetime.fromtimestamp(int(epoch), timezone.utc).astimezone(tz).date()
            if local_date < start_date or local_date > end_date:
                continue
            key = local_date.isoformat()
            t = float(temp)
            highs[key] = max(highs.get(key, float("-inf")), t)
            lows[key] = min(lows.get(key, float("inf")), t)

        payload = {
            key: (high, lows[key])
            for key, high in highs.items()
            if high != float("-inf") and lows[key] != float("inf")
        }
        if not payload:
            return WuDailyFetchResult(
                payload={},
                failure_reason=CoverageReason.PARSE_ERROR,
                retryable=True,
                error="WU response had observations but no usable temp/valid_time_gmt pairs",
            )
        return WuDailyFetchResult(payload=payload)
    except (httpx.HTTPError, httpx.RequestError) as e:
        # S3 fix: warning not debug — programmer errors (KeyError on
        # response shape, attribute errors) should surface in production
        # logs, not disappear silently. The caller downgrades to FAILED
        # with a retry embargo either way, but a pattern of warnings in
        # the daemon log tells operators something is structurally wrong
        # with the fetch code rather than with the upstream API.
        logger.warning(
            "WU ICAO fetch raised %s for %s:%s %s..%s: %s",
            type(e).__name__, icao, cc, start_date, end_date, e,
        )
        return WuDailyFetchResult(
            payload={},
            failure_reason=CoverageReason.NETWORK_ERROR,
            retryable=True,
            error=f"{type(e).__name__}: {e}",
        )


# ---------------------------------------------------------------------------
# HKO client (duplicated from scripts/backfill_hko_daily.py — Path A)
# ---------------------------------------------------------------------------

HKO_API_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
HKO_REALTIME_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
HKO_STATION = "HKO"
HKO_CITY_NAME = "Hong Kong"
HKO_SOURCE = "hko_daily_api"
HKO_REALTIME_SOURCE = "hko_realtime_api"
HKO_FETCH_RETRY_COUNT = 2
HKO_FETCH_RETRY_BACKOFF_SEC = 3.0
HKO_REALTIME_MIN_READINGS = 18


def _fetch_hko_month(
    year: int,
    month: int,
    data_type: str,
) -> dict[tuple[int, int, int], tuple[float, str]]:
    """Fetch one HKO month's climate data (CLMMAXT / CLMMINT / CLMTEMP).

    Returns {(y, m, d): (value_celsius, completeness_flag)} where
    completeness_flag is "C" (complete), "#" (incomplete), or "***"
    (unavailable). Non-"C" rows have value=NaN and must be written as
    LEGITIMATE_GAP in data_coverage.
    """
    params = {
        "dataType": data_type,
        "year": str(year),
        "month": f"{month:02d}",
        "rformat": "json",
        "lang": "en",
        "station": HKO_STATION,
    }
    resp = httpx.get(HKO_API_URL, params=params, timeout=30.0)
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    out: dict[tuple[int, int, int], tuple[float, str]] = {}
    for row in rows:
        if len(row) < 5:
            continue
        try:
            y = int(row[0])
            m = int(row[1])
            d = int(row[2])
            val_str = str(row[3])
            completeness = str(row[4])
            if completeness == "C":
                out[(y, m, d)] = (float(val_str), completeness)
            else:
                out[(y, m, d)] = (float("nan"), completeness)
        except (ValueError, TypeError):
            continue
    return out


def _fetch_hko_month_with_retry(
    year: int,
    month: int,
    data_type: str,
) -> tuple[dict[tuple[int, int, int], tuple[float, str]], str | None]:
    for attempt in range(HKO_FETCH_RETRY_COUNT + 1):
        try:
            return _fetch_hko_month(year, month, data_type), None
        except httpx.HTTPError as e:
            if attempt < HKO_FETCH_RETRY_COUNT:
                time.sleep(HKO_FETCH_RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            return {}, f"http error after {HKO_FETCH_RETRY_COUNT + 1} tries: {e}"
    return {}, "exhausted retries"


# ---------------------------------------------------------------------------
# HKO real-time hourly accumulator (supplements CLMMAXT/CLMMINT)
# ---------------------------------------------------------------------------


def _ensure_hko_accumulator_table(conn) -> None:
    """Create hko_hourly_accumulator if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hko_hourly_accumulator (
            target_date TEXT NOT NULL,
            hour_utc    TEXT NOT NULL,
            temperature REAL NOT NULL,
            fetched_at  TEXT NOT NULL,
            PRIMARY KEY (target_date, hour_utc)
        )
    """)


def _accumulate_hko_reading(conn) -> bool:
    """Fetch current HKO rhrread temperature and store in accumulator.

    The rhrread endpoint returns the latest hourly reading only; we call
    this on every hourly tick to build up a full day of readings. Returns
    True if a reading was successfully stored, False otherwise.
    """
    _ensure_hko_accumulator_table(conn)
    try:
        resp = httpx.get(
            HKO_REALTIME_URL,
            params={"dataType": "rhrread", "lang": "en"},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.warning("HKO rhrread fetch failed: %s", e)
        return False

    temp_data = data.get("temperature", {}).get("data", [])
    hko_reading = None
    for entry in temp_data:
        if entry.get("place") == "Hong Kong Observatory":
            hko_reading = entry.get("value")
            break

    if hko_reading is None:
        logger.warning("HKO rhrread: no 'Hong Kong Observatory' station in response")
        return False

    try:
        temp_c = float(hko_reading)
    except (TypeError, ValueError):
        logger.warning("HKO rhrread: non-numeric temperature value: %r", hko_reading)
        return False

    now_utc = datetime.now(timezone.utc)
    # Target date in HKT (UTC+8)
    hkt = ZoneInfo("Asia/Hong_Kong")
    hkt_now = now_utc.astimezone(hkt)
    target_date_str = hkt_now.date().isoformat()
    hour_utc_str = now_utc.strftime("%Y-%m-%dT%H:00Z")

    conn.execute(
        """
        INSERT INTO hko_hourly_accumulator (target_date, hour_utc, temperature, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(target_date, hour_utc) DO UPDATE SET
            temperature = excluded.temperature,
            fetched_at = excluded.fetched_at
        """,
        (target_date_str, hour_utc_str, temp_c, now_utc.isoformat()),
    )
    conn.commit()
    logger.debug(
        "HKO rhrread accumulated: date=%s hour=%s temp=%.1f°C",
        target_date_str, hour_utc_str, temp_c,
    )
    return True


def _finalize_hko_yesterday(
    conn,
    *,
    now_utc: datetime | None = None,
    rebuild_run_id: str = "",
) -> dict | None:
    """Check if yesterday (HKT) has enough accumulated readings to produce an observation.

    HKT midnight = UTC 16:00, so at UTC hour 2 (the call site), yesterday's
    HKT day is fully complete. We require >= HKO_REALTIME_MIN_READINGS
    readings to trust the daily max/min.

    Returns stats dict on success, None if not enough data or already written.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    _ensure_hko_accumulator_table(conn)

    hkt = ZoneInfo("Asia/Hong_Kong")
    hkt_now = now_utc.astimezone(hkt)
    yesterday_hkt = (hkt_now - timedelta(days=1)).date()
    yesterday_str = yesterday_hkt.isoformat()

    rows = conn.execute(
        "SELECT temperature FROM hko_hourly_accumulator WHERE target_date = ?",
        (yesterday_str,),
    ).fetchall()

    if len(rows) < HKO_REALTIME_MIN_READINGS:
        logger.info(
            "HKO realtime: %s has %d readings (need %d), skipping finalization",
            yesterday_str, len(rows), HKO_REALTIME_MIN_READINGS,
        )
        return None

    temps = [r[0] for r in rows]
    high_val = float(math.floor(max(temps)))
    low_val = float(math.floor(min(temps)))

    logger.info(
        "HKO realtime: finalizing %s — %d readings, high=%.0f low=%.0f",
        yesterday_str, len(rows), high_val, low_val,
    )

    stats = {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0}

    try:
        atom_high, atom_low = _build_atom_pair(
            city_name=HKO_CITY_NAME,
            target_d=yesterday_hkt,
            high_val=high_val,
            low_val=low_val,
            raw_unit="C",
            target_unit="C",
            station_id=HKO_STATION,
            source=HKO_REALTIME_SOURCE,
            rebuild_run_id=rebuild_run_id,
            data_source_version="hko_rhrread_accumulated_v1",
            api_endpoint=f"{HKO_REALTIME_URL}?dataType=rhrread&lang=en",
            provenance={
                "station": "Hong Kong Observatory",
                "method": "hourly_rhrread_accumulation",
                "readings_count": len(rows),
                "raw_max": max(temps),
                "raw_min": min(temps),
            },
            fetch_utc=now_utc,
        )
    except IngestionRejected as e:
        stats["guard_rejected"] += 1
        logger.warning("HKO realtime guard dropped %s: %s", yesterday_str, e)
        record_legitimate_gap(
            conn,
            data_table=DataTable.OBSERVATIONS,
            city=HKO_CITY_NAME,
            data_source=HKO_REALTIME_SOURCE,
            target_date=yesterday_hkt,
            reason=CoverageReason.GUARD_REJECTED,
        )
        conn.commit()
        return stats

    try:
        _write_atom_with_coverage(conn, atom_high, atom_low, data_source=HKO_REALTIME_SOURCE)
        stats["inserted"] += 1
        logger.info(
            "HKO realtime: wrote observation for %s (high=%.0f low=%.0f)",
            yesterday_str, high_val, low_val,
        )
    except Exception as e:
        stats["fetch_errors"] += 1
        logger.error("HKO realtime insert failed %s: %s", yesterday_str, e)
        record_failed(
            conn,
            data_table=DataTable.OBSERVATIONS,
            city=HKO_CITY_NAME,
            data_source=HKO_REALTIME_SOURCE,
            target_date=yesterday_hkt,
            reason=CoverageReason.NETWORK_ERROR,
            retry_after=_retry_embargo(hours=1),
        )

    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Shared guard (Layer 3 deleted — see ingestion_guard.py module docstring)
# ---------------------------------------------------------------------------

_GUARD = IngestionGuard()


def _hemisphere_for_lat(lat: float) -> str:
    return "N" if lat >= 0 else "S"


# ---------------------------------------------------------------------------
# Atom + data_coverage write path (the K1-C + K2 contract)
# ---------------------------------------------------------------------------


def _write_atom_with_coverage(
    conn,
    atom_high: ObservationAtom,
    atom_low: ObservationAtom,
    *,
    data_source: str,
) -> None:
    """Write one (high, low) pair to observations AND data_coverage atomically.

    Uses a SAVEPOINT so a mid-write exception rolls back JUST this row's
    observation INSERT + coverage upsert, not previous successful rows in
    the same batch. The reviewer flagged the earlier version as S1: a
    failure between INSERT and record_written could leave observations
    with a row but data_coverage FAILED → scanner retry → duplicate. The
    savepoint guarantees that either both land or neither does, per row.
    Caller still commits at end of batch.
    """
    assert atom_high.value_type == "high"
    assert atom_low.value_type == "low"
    assert atom_high.city == atom_low.city
    assert atom_high.target_date == atom_low.target_date

    sp = f"sp_write_{id(atom_high)}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        insert_or_update_current_observation(conn, atom_high, atom_low)
        record_written(
            conn,
            data_table=DataTable.OBSERVATIONS,
            city=atom_high.city,
            data_source=data_source,
            target_date=atom_high.target_date,
        )
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise
    conn.execute(f"RELEASE SAVEPOINT {sp}")


def _build_atom_pair(
    *,
    city_name: str,
    target_d: date,
    high_val: float,
    low_val: float,
    raw_unit: str,
    target_unit: str,
    station_id: str,
    source: str,
    rebuild_run_id: str,
    data_source_version: str,
    api_endpoint: str,
    provenance: dict,
    fetch_utc: datetime | None = None,
) -> tuple[ObservationAtom, ObservationAtom]:
    """Build a (high_atom, low_atom) pair with full K1-C provenance fields.

    Shared by WU and HKO write paths. Applies IngestionGuard layers 1, 4,
    5 (Layers 2 and 3 removed — Layer 2 skipped because TIGGE-derived
    p01/p99 under-represent observation tails; Layer 3 deleted). Raises
    IngestionRejected if validation fails — caller must catch and record
    FAILED in data_coverage.
    """
    city_cfg = cities_by_name.get(city_name)
    if city_cfg is None:
        raise IngestionRejected(f"Unknown city {city_name!r}")
    tz = ZoneInfo(city_cfg.timezone)
    hemisphere = _hemisphere_for_lat(city_cfg.lat)

    peak_hour_raw = city_cfg.historical_peak_hour
    peak_h = int(peak_hour_raw)
    peak_m = int((peak_hour_raw - peak_h) * 60)
    local_time = datetime(
        target_d.year, target_d.month, target_d.day, peak_h, peak_m, tzinfo=tz,
    )
    is_missing_local = _is_missing_local_hour(local_time, tz)
    is_ambiguous = bool(getattr(local_time, "fold", 0))
    dst_offset = local_time.dst()
    dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
    utc_offset = local_time.utcoffset()
    utc_offset_min = int(utc_offset.total_seconds() // 60) if utc_offset is not None else 0

    window_start_local = datetime(target_d.year, target_d.month, target_d.day, 0, 0, tzinfo=tz)
    window_end_local = datetime(target_d.year, target_d.month, target_d.day, 23, 59, 59, tzinfo=tz)
    window_start_utc = window_start_local.astimezone(timezone.utc)
    window_end_utc = window_end_local.astimezone(timezone.utc)

    # Bug #39: prefer caller-provided timestamp (response completion time)
    if fetch_utc is None:
        fetch_utc = datetime.now(timezone.utc)
    season = season_from_date(target_d.isoformat(), lat=city_cfg.lat)

    # Internal sanity first — cheap, catches inverted rows immediately.
    if low_val > high_val:
        raise IngestionRejected(
            f"{city_name}/{target_d.isoformat()}: low={low_val} > high={high_val} — "
            f"dataset internally inconsistent"
        )

    # Layer 1 — unit consistency + Earth records on BOTH values.
    _GUARD.check_unit_consistency(
        city=city_name, raw_value=high_val, raw_unit=raw_unit,
        declared_unit=city_cfg.settlement_unit, target_date=target_d,
    )
    _GUARD.check_unit_consistency(
        city=city_name, raw_value=low_val, raw_unit=raw_unit,
        declared_unit=city_cfg.settlement_unit, target_date=target_d,
    )
    # Layer 2 (physical_bounds) is skipped here for the same reason as in
    # the WU backfill: TIGGE-derived p01/p99 systematically under-represent
    # observation tails (Sept NYC 84°F false-positive). Layer 4 and 5
    # preserved.
    _GUARD.check_collection_timing(
        city=city_name, fetch_utc=fetch_utc, target_date=target_d,
        peak_hour=peak_hour_raw,
    )
    _GUARD.check_dst_boundary(city=city_name, local_time=local_time)

    common = dict(
        city=city_name,
        target_date=target_d,
        target_unit=target_unit,
        raw_unit=raw_unit,
        source=source,
        station_id=station_id,
        api_endpoint=api_endpoint,
        fetch_utc=fetch_utc,
        local_time=local_time,
        collection_window_start_utc=window_start_utc,
        collection_window_end_utc=window_end_utc,
        timezone=city_cfg.timezone,
        utc_offset_minutes=utc_offset_min,
        dst_active=dst_active,
        is_ambiguous_local_hour=is_ambiguous,
        is_missing_local_hour=is_missing_local,
        hemisphere=hemisphere,
        season=season,
        month=target_d.month,
        rebuild_run_id=rebuild_run_id,
        data_source_version=data_source_version,
        authority="VERIFIED",
        validation_pass=True,
        provenance_metadata=provenance,
    )
    atom_high = ObservationAtom(
        value_type="high", value=high_val, raw_value=high_val, **common,
    )
    atom_low = ObservationAtom(
        value_type="low", value=low_val, raw_value=low_val, **common,
    )
    return atom_high, atom_low


# ---------------------------------------------------------------------------
# Public: WU city appender
# ---------------------------------------------------------------------------


def _retry_embargo(hours: int = 1) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def append_wu_city(
    city_name: str,
    target_dates: Iterable[date],
    conn,
    *,
    rebuild_run_id: str,
) -> dict:
    """Fetch and write a specific date set for one WU ICAO city.

    This is the live-side analogue of `scripts/backfill_wu_daily_all.py`'s
    per-city loop, with these differences:
      - Callers pass an explicit date set (from the scheduler or scanner),
        rather than a [today-N, today] range
      - Each success writes to `data_coverage` as WRITTEN
      - Each transient failure writes FAILED with a 1h retry embargo
      - Each guard rejection writes FAILED with GUARD_REJECTED (no embargo
        — scanner should not retry a deterministic rejection)

    Returns {'inserted', 'guard_rejected', 'fetch_errors', 'missing_from_api'}.
    """
    city_cfg = cities_by_name.get(city_name)
    if city_cfg is None:
        logger.warning("append_wu_city: %s not in cities.json", city_name)
        return {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "missing_from_api": 0}

    icao = city_cfg.wu_station
    cc = city_cfg.country_code
    unit = city_cfg.settlement_unit

    dates = sorted(set(target_dates))
    if not dates:
        return {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "missing_from_api": 0}

    stats = {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "missing_from_api": 0}

    # WU historical supports date ranges natively. Fetch the bounding
    # window [min..max] in one call, then filter to requested dates.
    start_d, end_d = dates[0], dates[-1]
    fetch_result = _fetch_wu_icao_daily_highs_lows(
        icao, cc, start_d, end_d, unit, city_cfg.timezone,
    )
    if fetch_result.failed:
        reason = fetch_result.failure_reason or CoverageReason.NETWORK_ERROR
        stats["fetch_errors"] = len(dates)
        embargo_hours = 24 if fetch_result.auth_failed else 1
        logger.warning(
            "WU fetch failed for %s %s..%s reason=%s retryable=%s error=%s",
            city_name, start_d, end_d, reason, fetch_result.retryable,
            fetch_result.error,
        )
        for target_d in dates:
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=WU_SOURCE,
                target_date=target_d,
                reason=reason,
                retry_after=_retry_embargo(hours=embargo_hours),
            )
        conn.commit()
        return stats

    highs_lows = fetch_result.payload
    if not highs_lows:
        # API returned a usable 200 response but no published observations.
        # Keep this separate from transport/auth/parse failures so the
        # coverage ledger preserves upstream meaning.
        stats["missing_from_api"] = len(dates)
        for target_d in dates:
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=WU_SOURCE,
                target_date=target_d,
                reason=CoverageReason.SOURCE_NOT_PUBLISHED_YET,
                retry_after=_retry_embargo(hours=6),
            )
        conn.commit()
        return stats

    for target_d in dates:
        target_str = target_d.isoformat()
        pair = highs_lows.get(target_str)
        if pair is None:
            # WU had nothing for this date — could be a legitimate gap
            # (station downtime, weekend blackout) or a real miss. Mark
            # FAILED with short embargo so scanner retries once before
            # operator review.
            stats["missing_from_api"] += 1
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=WU_SOURCE,
                target_date=target_d,
                reason=CoverageReason.SOURCE_NOT_PUBLISHED_YET,
                retry_after=_retry_embargo(hours=6),
            )
            continue

        high_val, low_val = pair
        try:
            atom_high, atom_low = _build_atom_pair(
                city_name=city_name,
                target_d=target_d,
                high_val=high_val,
                low_val=low_val,
                raw_unit=unit,
                target_unit=city_cfg.settlement_unit,
                station_id=f"{icao}:{cc}",
                source=WU_SOURCE,
                rebuild_run_id=rebuild_run_id,
                # Aligned to scripts/backfill_wu_daily_all.py so live and
                # backfill rows group into the same calibration bucket.
                data_source_version="wu_icao_v1_2026",
                api_endpoint=WU_ICAO_HISTORY_URL.format(icao=icao, cc=cc),
                provenance={
                    "icao": icao,
                    "cc": cc,
                    "fetched_range": f"{start_d.isoformat()}..{end_d.isoformat()}",
                },
            )
        except IngestionRejected as e:
            stats["guard_rejected"] += 1
            logger.warning("WU guard dropped %s/%s: %s", city_name, target_str, e)
            # Guard rejection is a permanent terminal state, so pin as
            # LEGITIMATE_GAP rather than FAILED (S2 fix). The scanner will
            # never retry this row; a future guard-logic change requires
            # an explicit re-ingest pass, not a retry-embargo cycle.
            record_legitimate_gap(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=WU_SOURCE,
                target_date=target_d,
                reason=CoverageReason.GUARD_REJECTED,
            )
            continue

        try:
            _write_atom_with_coverage(conn, atom_high, atom_low, data_source=WU_SOURCE)
            stats["inserted"] += 1
        except Exception as e:
            logger.error("WU insert failed %s/%s: %s", city_name, target_str, e)
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=WU_SOURCE,
                target_date=target_d,
                reason=CoverageReason.NETWORK_ERROR,
                retry_after=_retry_embargo(hours=1),
            )

    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Public: HKO appender
# ---------------------------------------------------------------------------


def append_hko_months(
    year_months: Iterable[tuple[int, int]],
    conn,
    *,
    rebuild_run_id: str,
) -> dict:
    """Fetch and write HKO daily high/low for one or more (year, month) pairs.

    HKO publishes monthly, so the grain of a live refresh is a month. The
    daemon's daily tick passes [(current_year, current_month), (prior_year,
    prior_month)] to catch both current-month "#"→"C" flips and early-month
    rollover.

    For each day:
    - Flag "C": write observation + data_coverage WRITTEN
    - Flag "#": data_coverage LEGITIMATE_GAP (HKO_INCOMPLETE_FLAG)
    - Flag "***": data_coverage LEGITIMATE_GAP (HKO_UNAVAILABLE_FLAG)
    """
    city_cfg = cities_by_name.get(HKO_CITY_NAME)
    if city_cfg is None:
        raise RuntimeError(f"{HKO_CITY_NAME} not in cities.json")

    stats = {"inserted": 0, "incomplete": 0, "unavailable": 0,
             "guard_rejected": 0, "fetch_errors": 0}

    for year, month in year_months:
        max_map, err_max = _fetch_hko_month_with_retry(year, month, "CLMMAXT")
        if err_max:
            stats["fetch_errors"] += 1
            logger.error("HKO CLMMAXT %d/%d failed: %s", year, month, err_max)
            continue
        time.sleep(0.5)  # courtesy between the two endpoint hits
        min_map, err_min = _fetch_hko_month_with_retry(year, month, "CLMMINT")
        if err_min:
            stats["fetch_errors"] += 1
            logger.error("HKO CLMMINT %d/%d failed: %s", year, month, err_min)
            continue

        common_days = set(max_map.keys()) & set(min_map.keys())
        for ymd in sorted(common_days):
            high_val, high_flag = max_map[ymd]
            low_val, low_flag = min_map[ymd]
            y, m, d = ymd
            target_d = date(y, m, d)

            if high_flag != "C" or low_flag != "C":
                reason = (
                    CoverageReason.HKO_UNAVAILABLE_FLAG
                    if "***" in (high_flag, low_flag)
                    else CoverageReason.HKO_INCOMPLETE_FLAG
                )
                if reason == CoverageReason.HKO_UNAVAILABLE_FLAG:
                    stats["unavailable"] += 1
                else:
                    stats["incomplete"] += 1
                record_legitimate_gap(
                    conn,
                    data_table=DataTable.OBSERVATIONS,
                    city=HKO_CITY_NAME,
                    data_source=HKO_SOURCE,
                    target_date=target_d,
                    reason=reason,
                )
                continue

            try:
                # HKO reports 0.1°C precision (e.g. 27.8°C) but PM's UMA
                # Oracle floors to integer °C for bin placement (27°C).
                # Keep raw_value at original precision for audit; floor the
                # value that enters _build_atom_pair so observations match
                # PM settlement semantics.  See oracle_error_rate analysis.
                import math as _math
                high_val = float(_math.floor(high_val))
                low_val = float(_math.floor(low_val))

                atom_high, atom_low = _build_atom_pair(
                    city_name=HKO_CITY_NAME,
                    target_d=target_d,
                    high_val=high_val,
                    low_val=low_val,
                    raw_unit="C",
                    target_unit="C",
                    station_id=HKO_STATION,
                    source=HKO_SOURCE,
                    rebuild_run_id=rebuild_run_id,
                    # Aligned to scripts/backfill_hko_daily.py (S2 fix).
                    data_source_version="hko_opendata_v1_2026",
                    api_endpoint=(
                        f"{HKO_API_URL}?dataType=CLMMAXT|CLMMINT"
                        f"&year={year}&month={month:02d}&station={HKO_STATION}"
                    ),
                    provenance={
                        "station": HKO_STATION,
                        "dataType": ["CLMMAXT", "CLMMINT"],
                    },
                )
            except IngestionRejected as e:
                stats["guard_rejected"] += 1
                logger.warning("HKO guard dropped %s: %s", target_d.isoformat(), e)
                # Permanent terminal state — see S2 fix note in append_wu_city.
                record_legitimate_gap(
                    conn,
                    data_table=DataTable.OBSERVATIONS,
                    city=HKO_CITY_NAME,
                    data_source=HKO_SOURCE,
                    target_date=target_d,
                    reason=CoverageReason.GUARD_REJECTED,
                )
                continue

            try:
                _write_atom_with_coverage(conn, atom_high, atom_low, data_source=HKO_SOURCE)
                stats["inserted"] += 1
            except Exception as e:
                logger.error("HKO insert failed %s: %s", target_d.isoformat(), e)
                record_failed(
                    conn,
                    data_table=DataTable.OBSERVATIONS,
                    city=HKO_CITY_NAME,
                    data_source=HKO_SOURCE,
                    target_date=target_d,
                    reason=CoverageReason.NETWORK_ERROR,
                    retry_after=_retry_embargo(hours=1),
                )

        conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Ogimet METAR client (Istanbul, Moscow — cities where WU API rejects)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _OgimetTarget:
    city_name: str
    station: str              # ICAO (METAR) or WMO block (SYNOP)
    kind: str                 # "metar" | "synop"
    source_tag: str           # value written to observations.source column


#: Cities whose Polymarket settlement source is NOAA weather.gov but
#: have no programmatic NOAA API — we fetch the same underlying METAR
#: stream via Ogimet (free, unauthenticated).
OGIMET_CITIES: dict[str, _OgimetTarget] = {
    "Istanbul": _OgimetTarget(
        city_name="Istanbul", station="LTFM", kind="metar",
        source_tag="ogimet_metar_ltfm",
    ),
    "Moscow": _OgimetTarget(
        city_name="Moscow", station="UUWW", kind="metar",
        source_tag="ogimet_metar_uuww",
    ),
    "Tel Aviv": _OgimetTarget(
        city_name="Tel Aviv", station="LLBG", kind="metar",
        source_tag="ogimet_metar_llbg",
    ),
}

_OGIMET_METAR_URL = "https://www.ogimet.com/cgi-bin/getmetar"
_OGIMET_SYNOP_URL = "https://www.ogimet.com/cgi-bin/getsynop"
_OGIMET_HEADERS = {"User-Agent": "zeus-ogimet-live/1.0 (research; contact via repo)"}
_OGIMET_RETRY_COUNT = 2
_OGIMET_RETRY_BACKOFF_SEC = 5.0

# METAR temp/dewpoint group: "10/08", "M05/M08"
_METAR_TEMP_RE = re.compile(r"\s(M?\d{1,2})/(M?\d{1,2})\s")


def _parse_metar_temp(metar_body: str) -> float | None:
    m = _METAR_TEMP_RE.search(" " + metar_body + " ")
    if not m:
        return None
    raw = m.group(1)
    negative = raw.startswith("M")
    try:
        v = int(raw[1:] if negative else raw)
    except ValueError:
        return None
    return float(-v if negative else v)


def _fetch_ogimet_day(
    target: _OgimetTarget,
    target_date: date,
    tz: ZoneInfo,
) -> tuple[float, float, int, datetime, datetime] | None:
    """Fetch one local day of METAR reports and return (high, low, count, first_utc, last_utc).

    Returns None on fetch failure or if no usable reports are found.
    """
    # Expand to full UTC window covering the local day
    local_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, tzinfo=tz)
    local_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, tzinfo=tz)
    begin_utc = local_start.astimezone(timezone.utc)
    end_utc = local_end.astimezone(timezone.utc)

    url = _OGIMET_METAR_URL if target.kind == "metar" else _OGIMET_SYNOP_URL
    params = {
        "icao" if target.kind == "metar" else "block": target.station,
        "begin": begin_utc.strftime("%Y%m%d%H%M"),
        "end": end_utc.strftime("%Y%m%d%H%M"),
    }

    body = ""
    for attempt in range(_OGIMET_RETRY_COUNT + 1):
        try:
            resp = requests.get(url, params=params, headers=_OGIMET_HEADERS, timeout=45)
            if resp.status_code == 200:
                body = resp.text
                break
            logger.warning(
                "Ogimet %s %s HTTP %d (attempt %d/%d)",
                target.station, target_date, resp.status_code,
                attempt + 1, _OGIMET_RETRY_COUNT + 1,
            )
        except requests.RequestException as e:
            logger.warning(
                "Ogimet %s %s %s (attempt %d/%d)",
                target.station, target_date, e,
                attempt + 1, _OGIMET_RETRY_COUNT + 1,
            )
        if attempt < _OGIMET_RETRY_COUNT:
            time.sleep(_OGIMET_RETRY_BACKOFF_SEC * (attempt + 1))

    if not body:
        return None

    # Parse METAR CSV lines: ICAO,YYYY,MM,DD,HH,MI,<body>
    temps: list[float] = []
    first_utc: datetime | None = None
    last_utc: datetime | None = None
    for line in body.splitlines():
        parts = line.split(",", 6)
        if len(parts) < 7:
            continue
        try:
            year, month, day, hour, minute = map(int, parts[1:6])
            obs_utc = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            continue
        temp = _parse_metar_temp(parts[6])
        if temp is None:
            continue
        # Only keep reports that fall in the target local day
        obs_local = obs_utc.astimezone(tz).date()
        if obs_local != target_date:
            continue
        temps.append(temp)
        if first_utc is None or obs_utc < first_utc:
            first_utc = obs_utc
        if last_utc is None or obs_utc > last_utc:
            last_utc = obs_utc

    if not temps or first_utc is None or last_utc is None:
        return None

    return max(temps), min(temps), len(temps), first_utc, last_utc


def append_ogimet_city(
    city_name: str,
    target_dates: list[date],
    conn,
    *,
    rebuild_run_id: str | None = None,
) -> dict:
    """Fetch and write Ogimet METAR observations for a non-WU city."""
    target = OGIMET_CITIES.get(city_name)
    if target is None:
        logger.warning("append_ogimet_city: %s not in OGIMET_CITIES", city_name)
        return {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0}

    city_cfg = cities_by_name.get(city_name)
    if city_cfg is None:
        logger.warning("append_ogimet_city: %s not in cities.json", city_name)
        return {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0}

    if rebuild_run_id is None:
        rebuild_run_id = f"ogimet_live_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"

    tz = ZoneInfo(city_cfg.timezone)
    stats = {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0}

    for target_d in target_dates:
        result = _fetch_ogimet_day(target, target_d, tz)
        if result is None:
            stats["fetch_errors"] += 1
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=target.source_tag,
                target_date=target_d,
                reason=CoverageReason.NETWORK_ERROR,
                retry_after=_retry_embargo(hours=2),
            )
            continue

        high_val, low_val, report_count, first_utc, last_utc = result
        provenance = {
            "station": target.station,
            "kind": target.kind,
            "upstream": "ogimet",
            "report_count": report_count,
            "window_first_utc": first_utc.isoformat(),
            "window_last_utc": last_utc.isoformat(),
        }

        try:
            atom_high, atom_low = _build_atom_pair(
                city_name=city_name,
                target_d=target_d,
                high_val=high_val,
                low_val=low_val,
                raw_unit="C",
                target_unit=city_cfg.settlement_unit,
                station_id=target.station,
                source=target.source_tag,
                rebuild_run_id=rebuild_run_id,
                data_source_version="ogimet_live_v1",
                api_endpoint=f"{_OGIMET_METAR_URL}?icao={target.station}",
                provenance=provenance,
                fetch_utc=datetime.now(timezone.utc),
            )
        except IngestionRejected as e:
            stats["guard_rejected"] += 1
            logger.warning("Ogimet guard dropped %s/%s: %s", city_name, target_d, e)
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=target.source_tag,
                target_date=target_d,
                reason=CoverageReason.GUARD_REJECTED,
                retry_after=_retry_embargo(hours=24),
            )
            continue

        try:
            _write_atom_with_coverage(conn, atom_high, atom_low, data_source=target.source_tag)
            stats["inserted"] += 1
        except Exception as e:
            logger.error("Ogimet insert failed %s/%s: %s", city_name, target_d, e)
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=target.source_tag,
                target_date=target_d,
                reason=CoverageReason.NETWORK_ERROR,
                retry_after=_retry_embargo(hours=1),
            )

        # Be polite to ogimet — 1 second between per-day requests
        time.sleep(1.0)

    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Public: daemon entrypoints (tick + catch-up)
# ---------------------------------------------------------------------------


def _prior_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def daily_tick(
    conn,
    *,
    now_utc: Optional[datetime] = None,
    rebuild_run_id: Optional[str] = None,
) -> dict:
    """Daemon per-hour entrypoint.

    For WU cities, uses `WuDailyScheduler.should_collect_now` to find
    cities whose local peak+4h window overlaps the current UTC hour, then
    fetches *today's* target_date (the day whose daily max has just
    finished being observed).

    For HKO, unconditionally refreshes [current_month, prior_month]. This
    is idempotent via data_coverage upsert and catches `#`→`C` flips
    without per-tick scheduling logic. HKO refresh runs once per hour
    (not once per day) so that a `#` flip in the upstream gets picked up
    within an hour of publication.

    Returns a nested dict {wu: {...}, hko: {...}} with per-call stats.
    """
    from src.data.wu_scheduler import WuDailyScheduler  # lazy — avoid circular

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if rebuild_run_id is None:
        rebuild_run_id = f"daily_tick_{now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    scheduler = WuDailyScheduler()
    wu_totals = {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "missing_from_api": 0}
    for city_cfg in cities_by_name.values():
        if city_cfg.settlement_source_type != "wu_icao":
            continue
        city_name = city_cfg.name
        if not scheduler.should_collect_now(city_cfg, now_utc):
            continue
        # Fetch the LAST COMPLETED local calendar day, not the in-progress
        # local today. At peak+4h the observed daily max has occurred, but
        # the calendar day is still 4+ hours from ending — late-evening
        # events could still theoretically shift the canonical daily high,
        # and WU historical.json has a ~24h publication lag so today's
        # data may not be there yet. The reviewer flagged this as S1
        # (silent under-reporting risk). wu_daily_collector.py (the legacy
        # collector this replaces) used the same `date.today() - 1` default.
        local_today = now_utc.astimezone(ZoneInfo(city_cfg.timezone)).date()
        local_yesterday = local_today - timedelta(days=1)
        stats = append_wu_city(
            city_name, [local_yesterday], conn, rebuild_run_id=rebuild_run_id,
        )
        for k in wu_totals:
            wu_totals[k] += stats.get(k, 0)

    # HKO real-time accumulation: on EVERY tick, fetch the current rhrread
    # temperature and store it. This builds up hourly readings throughout
    # the day so we can compute daily max/min even when CLMMAXT/CLMMINT
    # archives aren't yet available (they lag by weeks/months).
    _accumulate_hko_reading(conn)

    # HKO refresh: gate to once per day at UTC hour 2 (=10:00 HKT). Running
    # every hourly tick produced ~720 fetches/month with near-zero marginal
    # benefit since HKO publishes monthly with multi-day #→C flips.
    # Reviewer flagged the every-tick version as S2.
    hko_stats = None
    hko_rt_stats = None
    if now_utc.hour == 2:
        hko_now = now_utc.astimezone(ZoneInfo(cities_by_name[HKO_CITY_NAME].timezone))
        months = [(hko_now.year, hko_now.month)]
        prior_y, prior_m = _prior_month(hko_now.year, hko_now.month)
        months.append((prior_y, prior_m))
        hko_stats = append_hko_months(months, conn, rebuild_run_id=rebuild_run_id)

        # Also try to finalize yesterday's real-time accumulated observation.
        # This produces an hko_realtime_api row that supplements the monthly
        # CLMMAXT/CLMMINT archive (which returns empty for the current month).
        hko_rt_stats = _finalize_hko_yesterday(
            conn, now_utc=now_utc, rebuild_run_id=rebuild_run_id,
        )

    # Ogimet refresh: once per day at UTC hour 6. Istanbul (UTC+3) and
    # Moscow (UTC+3) have their previous local day completed by then.
    ogimet_stats = None
    if now_utc.hour == 6:
        ogimet_stats = {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0}
        for city_name, target in OGIMET_CITIES.items():
            city_cfg = cities_by_name.get(city_name)
            if city_cfg is None:
                continue
            local_today = now_utc.astimezone(ZoneInfo(city_cfg.timezone)).date()
            local_yesterday = local_today - timedelta(days=1)
            stats = append_ogimet_city(
                city_name, [local_yesterday], conn, rebuild_run_id=rebuild_run_id,
            )
            for k in ogimet_stats:
                ogimet_stats[k] += stats.get(k, 0)

    return {"wu": wu_totals, "hko": hko_stats, "hko_realtime": hko_rt_stats, "ogimet": ogimet_stats}


def catch_up_missing(
    conn,
    *,
    days_back: int = 30,
    max_cities: int = 46,
    rebuild_run_id: Optional[str] = None,
) -> dict:
    """Daemon boot entrypoint: fill data_coverage MISSING rows within N days.

    Queries `data_coverage` for WU/HKO rows whose status is MISSING or
    retry-ready FAILED within the last `days_back` days, groups by city,
    and calls the appropriate appender. Use days_back=7 for routine
    post-downtime catch-up; use days_back=30 for daemon-wide audit passes.
    """
    from src.state.data_coverage import find_pending_fills

    if rebuild_run_id is None:
        rebuild_run_id = f"catch_up_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"

    cutoff = date.today() - timedelta(days=days_back)
    rows = find_pending_fills(conn, data_table=DataTable.OBSERVATIONS, max_rows=10_000)

    wu_by_city: dict[str, list[date]] = {}
    hko_months: set[tuple[int, int]] = set()
    ogimet_by_city: dict[str, list[date]] = {}
    ogimet_sources = {t.source_tag for t in OGIMET_CITIES.values()}
    for r in rows:
        target = date.fromisoformat(r["target_date"])
        if target < cutoff:
            continue
        if r["data_source"] == WU_SOURCE:
            wu_by_city.setdefault(r["city"], []).append(target)
        elif r["data_source"] == HKO_SOURCE:
            hko_months.add((target.year, target.month))
        elif r["data_source"] in ogimet_sources:
            ogimet_by_city.setdefault(r["city"], []).append(target)

    totals = {"wu_cities_touched": 0, "wu_inserted": 0, "wu_guard_rejected": 0,
              "hko_months_touched": 0, "hko_inserted": 0, "hko_incomplete": 0,
              "ogimet_cities_touched": 0, "ogimet_inserted": 0, "ogimet_guard_rejected": 0}

    for i, (city_name, dates) in enumerate(wu_by_city.items()):
        if i >= max_cities:
            break
        stats = append_wu_city(city_name, dates, conn, rebuild_run_id=rebuild_run_id)
        totals["wu_cities_touched"] += 1
        totals["wu_inserted"] += stats["inserted"]
        totals["wu_guard_rejected"] += stats["guard_rejected"]

    if hko_months:
        stats = append_hko_months(sorted(hko_months), conn, rebuild_run_id=rebuild_run_id)
        totals["hko_months_touched"] = len(hko_months)
        totals["hko_inserted"] = stats["inserted"]
        totals["hko_incomplete"] = stats["incomplete"]

    for city_name, dates in ogimet_by_city.items():
        stats = append_ogimet_city(city_name, dates, conn, rebuild_run_id=rebuild_run_id)
        totals["ogimet_cities_touched"] += 1
        totals["ogimet_inserted"] += stats["inserted"]
        totals["ogimet_guard_rejected"] += stats["guard_rejected"]

    return totals
