"""K2 live daily-observation appender (WU ICAO + HKO).

Replaces the broken `src/data/wu_daily_collector.py` for live ingestion of
daily high/low temperatures into the `observations` table. Handles the two
distinct daily-obs source lanes Zeus uses:

1. WU ICAO history for 45 configured cities. Uses the same
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

Source registry (CITY_STATIONS) and fetch helpers are intentionally
duplicated from `scripts/backfill_wu_daily_all.py` and
`scripts/backfill_hko_daily.py` (Path A). Phase C of the K2 packet will
extract the common client into `src/data/wu_icao_client.py` +
`src/data/hko_client.py` so backfill and live append share one
implementation.

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
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import httpx

from src.calibration.manager import season_from_date
from src.config import cities_by_name
from src.data.ingestion_guard import IngestionGuard, IngestionRejected
from src.signal.diurnal import _is_missing_local_hour
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

# Security S1 fix: fail-closed if the env var is not set. The previous
# `os.environ.get("WU_API_KEY", "<hex>")` default baked a WU API key
# literal into the source tree and therefore into git history. A deploy
# where WU_API_KEY is unset silently fell back to that committed key,
# hiding config drift behind HTTP 401/403 rather than a loud startup
# failure. Anyone who cloned the repo had the same key. The value has
# been removed from source; the operator must set WU_API_KEY in launchd/
# systemd/.env before starting the daemon. (The matching removal is in
# scripts/backfill_wu_daily_all.py. The legacy wu_daily_collector.py
# still has its own hardcoded fallback but that module is dead code —
# deprecated by K2 and unregistered in src/main.py; Phase C removes it.)
_WU_API_KEY_ENV = "WU_API_KEY"
WU_API_KEY = os.environ.get(_WU_API_KEY_ENV)
if not WU_API_KEY:
    # Lazy-fail: allow import without the env so unit tests that don't
    # hit WU can import the module. The actual fetch path will raise.
    WU_API_KEY = None
WU_ICAO_HISTORY_URL = (
    "https://api.weather.com/v1/location/{icao}:9:{cc}/observations/historical.json"
)
WU_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

#: city name → (ICAO code, country code, declared settlement unit)
CITY_STATIONS: dict[str, tuple[str, str, str]] = {
    # US cities (F-unit settlement)
    "NYC":           ("KLGA", "US", "F"),
    "Chicago":       ("KORD", "US", "F"),
    "Atlanta":       ("KATL", "US", "F"),
    "Austin":        ("KAUS", "US", "F"),
    "Dallas":        ("KDAL", "US", "F"),
    "Denver":        ("KBKF", "US", "F"),
    "Houston":       ("KHOU", "US", "F"),
    "Los Angeles":   ("KLAX", "US", "F"),
    "Miami":         ("KMIA", "US", "F"),
    "San Francisco": ("KSFO", "US", "F"),
    "Seattle":       ("KSEA", "US", "F"),
    # Americas
    "Buenos Aires":  ("SAEZ", "AR", "C"),
    "Mexico City":   ("MMMX", "MX", "C"),
    "Sao Paulo":     ("SBGR", "BR", "C"),
    "Toronto":       ("CYYZ", "CA", "C"),
    # Europe
    "London":        ("EGLC", "GB", "C"),
    "Paris":         ("LFPG", "FR", "C"),
    "Munich":        ("EDDM", "DE", "C"),
    "Madrid":        ("LEMD", "ES", "C"),
    "Milan":         ("LIMC", "IT", "C"),
    "Warsaw":        ("EPWA", "PL", "C"),
    "Amsterdam":     ("EHAM", "NL", "C"),
    "Helsinki":      ("EFHK", "FI", "C"),
    "Ankara":        ("LTAC", "TR", "C"),
    "Tel Aviv":      ("LLBG", "IL", "C"),
    # Asia
    "Beijing":       ("ZBAA", "CN", "C"),
    "Shanghai":      ("ZSPD", "CN", "C"),
    "Shenzhen":      ("ZGSZ", "CN", "C"),
    "Chengdu":       ("ZUUU", "CN", "C"),
    "Chongqing":     ("ZUCK", "CN", "C"),
    "Wuhan":         ("ZHHH", "CN", "C"),
    "Guangzhou":     ("ZGGG", "CN", "C"),
    # Hong Kong is intentionally NOT in CITY_STATIONS — HKO is the
    # authoritative Polymarket settlement source for HK.
    "Tokyo":         ("RJTT", "JP", "C"),
    "Seoul":         ("RKSI", "KR", "C"),
    "Taipei":        ("RCSS", "TW", "C"),
    "Singapore":     ("WSSS", "SG", "C"),
    "Lucknow":       ("VILK", "IN", "C"),
    "Karachi":       ("OPKC", "PK", "C"),
    "Manila":        ("RPLL", "PH", "C"),
    # Oceania
    "Wellington":    ("NZWN", "NZ", "C"),
    "Auckland":      ("NZAA", "NZ", "C"),
    # Africa
    "Lagos":         ("DNMM", "NG", "C"),
    "Cape Town":     ("FACT", "ZA", "C"),
    # Middle East
    "Jeddah":        ("OEJN", "SA", "C"),
    # Southeast Asia
    "Kuala Lumpur":  ("WMKK", "MY", "C"),
    "Jakarta":       ("WIHH", "ID", "C"),
    # Northeast Asia
    "Busan":         ("RKPK", "KR", "C"),
    # Latin America
    "Panama City":   ("MPMG", "PA", "C"),
}

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
    if not WU_API_KEY:
        raise RuntimeError(
            f"{_WU_API_KEY_ENV} environment variable is required but not set. "
            "Set it in the daemon launch environment (launchd plist, systemd "
            "service, or .env) before running K2 daily_obs_append."
        )
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
    except Exception as e:
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
HKO_STATION = "HKO"
HKO_CITY_NAME = "Hong Kong"
HKO_SOURCE = "hko_daily_api"
HKO_FETCH_RETRY_COUNT = 2
HKO_FETCH_RETRY_BACKOFF_SEC = 3.0


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
        except Exception as e:
            return {}, f"unexpected error: {type(e).__name__}: {e}"
    return {}, "exhausted retries"


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
        conn.execute(
            """
            INSERT INTO observations (
                city, target_date, source, high_temp, low_temp, unit, station_id, fetched_at,
                raw_value, raw_unit, target_unit, value_type,
                fetch_utc, local_time, collection_window_start_utc, collection_window_end_utc,
                timezone, utc_offset_minutes, dst_active,
                is_ambiguous_local_hour, is_missing_local_hour,
                hemisphere, season, month,
                rebuild_run_id, data_source_version,
                authority, provenance_metadata
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?
            )
            ON CONFLICT(city, target_date, source) DO UPDATE SET
                high_temp = excluded.high_temp,
                low_temp = excluded.low_temp,
                unit = excluded.unit,
                station_id = excluded.station_id,
                fetched_at = excluded.fetched_at,
                raw_value = excluded.raw_value,
                raw_unit = excluded.raw_unit,
                target_unit = excluded.target_unit,
                value_type = excluded.value_type,
                fetch_utc = excluded.fetch_utc,
                local_time = excluded.local_time,
                collection_window_start_utc = excluded.collection_window_start_utc,
                collection_window_end_utc = excluded.collection_window_end_utc,
                timezone = excluded.timezone,
                utc_offset_minutes = excluded.utc_offset_minutes,
                dst_active = excluded.dst_active,
                is_ambiguous_local_hour = excluded.is_ambiguous_local_hour,
                is_missing_local_hour = excluded.is_missing_local_hour,
                hemisphere = excluded.hemisphere,
                season = excluded.season,
                month = excluded.month,
                rebuild_run_id = excluded.rebuild_run_id,
                data_source_version = excluded.data_source_version,
                authority = excluded.authority,
                provenance_metadata = excluded.provenance_metadata
            """,
            (
                atom_high.city, atom_high.target_date.isoformat(), atom_high.source,
                atom_high.value, atom_low.value, atom_high.target_unit,
                atom_high.station_id, atom_high.fetch_utc.isoformat(),
                atom_high.raw_value, atom_high.raw_unit,
                atom_high.target_unit, atom_high.value_type,
                atom_high.fetch_utc.isoformat(), atom_high.local_time.isoformat(),
                atom_high.collection_window_start_utc.isoformat(),
                atom_high.collection_window_end_utc.isoformat(),
                atom_high.timezone, atom_high.utc_offset_minutes, int(atom_high.dst_active),
                int(atom_high.is_ambiguous_local_hour), int(atom_high.is_missing_local_hour),
                atom_high.hemisphere, atom_high.season, atom_high.month,
                atom_high.rebuild_run_id, atom_high.data_source_version,
                atom_high.authority, json.dumps(atom_high.provenance_metadata),
            ),
        )
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
    info = CITY_STATIONS.get(city_name)
    if not info:
        logger.warning("append_wu_city: %s not in CITY_STATIONS", city_name)
        return {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "missing_from_api": 0}

    icao, cc, unit = info
    city_cfg = cities_by_name.get(city_name)
    if city_cfg is None:
        logger.warning("append_wu_city: %s not in cities.json", city_name)
        return {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "missing_from_api": 0}

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
    for city_name in CITY_STATIONS:
        city_cfg = cities_by_name.get(city_name)
        if city_cfg is None:
            continue
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

    # HKO refresh: gate to once per day at UTC hour 2 (=10:00 HKT). Running
    # every hourly tick produced ~720 fetches/month with near-zero marginal
    # benefit since HKO publishes monthly with multi-day #→C flips.
    # Reviewer flagged the every-tick version as S2.
    hko_stats = None
    if now_utc.hour == 2:
        hko_now = now_utc.astimezone(ZoneInfo(cities_by_name[HKO_CITY_NAME].timezone))
        months = [(hko_now.year, hko_now.month)]
        prior_y, prior_m = _prior_month(hko_now.year, hko_now.month)
        months.append((prior_y, prior_m))
        hko_stats = append_hko_months(months, conn, rebuild_run_id=rebuild_run_id)

    return {"wu": wu_totals, "hko": hko_stats}


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
    for r in rows:
        target = date.fromisoformat(r["target_date"])
        if target < cutoff:
            continue
        if r["data_source"] == WU_SOURCE:
            wu_by_city.setdefault(r["city"], []).append(target)
        elif r["data_source"] == HKO_SOURCE:
            hko_months.add((target.year, target.month))

    totals = {"wu_cities_touched": 0, "wu_inserted": 0, "wu_guard_rejected": 0,
              "hko_months_touched": 0, "hko_inserted": 0, "hko_incomplete": 0}

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

    return totals
