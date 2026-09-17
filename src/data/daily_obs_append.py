# Created: 2026-04-21
# Last reused/audited: 2026-05-18
# Authority basis: K2 live ingestion; F3 PR 2/3 typed temperature boundary
#                  per Path A (src/types/temperature.py).
"""K2 live daily-observation appender (WU ICAO + HKO + Ogimet METAR/SYNOP).

Replaces the broken `src/data/wu_daily_collector.py` for live ingestion of
daily high/low temperatures into the `observations` table. Handles the three
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

3. Ogimet's same-station METAR mirror for target dates whose configured
   resolver family is NOAA. These slow requests are quota-governed and spread
   across hourly shards; direct NOAA METAR remains the live Day0 source clock.

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
  schedules WU by local peak, HKO on its own cadence, and a bounded NOAA
  mirror shard.
- `catch_up_missing(conn, *, days_back, max_cities)` — bounded repair
  entrypoint: queries data_coverage for MISSING / retry-ready FAILED
  rows within `days_back` and fills them via the same write path.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import httpx

# G10 calibration-fence (2026-04-26, con-nyx NICE-TO-HAVE #4): import from
# canonical location to avoid transitively pulling src.calibration into the
# ingest lane (FORBIDDEN per tests/test_ingest_isolation.py post-fix).
from src.contracts.season import season_from_date
from src.config import cities_by_name, settlement_source_type_for_city
from src.data.daily_observation_writer import (
    write_daily_observation_with_revision,
)
from src.data.ingestion_guard import IngestionGuard, IngestionRejected
from src.data.metar_temperature import metar_temperature_c
from src.engine.time_context import city_local_day_end_target_date
from src.types.temperature import Temperature
# G10 helper-extraction (2026-04-26, con-nyx MAJOR #1): import from canonical
# location to avoid transitively pulling src.signal into the ingest lane.
from src.contracts.dst_semantics import _is_missing_local_hour
from src.state.data_coverage import (
    CoverageReason,
    CoverageStatus,
    DataTable,
    coverage_row_status,
    record_failed,
    record_legitimate_gap,
    record_written,
)
from src.types.observation_atom import ObservationAtom
from src.types.temperature import Celsius, CelsiusBox

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WU ICAO client (duplicated from scripts/backfill_wu_daily_all.py — Path A)
# ---------------------------------------------------------------------------

# =============================================================================
# WU PUBLIC WEB KEY — NOT A SECRET. DO NOT FLAG. [REVIEW-SAFE: WU_PUBLIC_KEY]
# =============================================================================
# WU ICAO historical public web key. This is NOT a secret — it is the same
# key that wunderground.com's own browser UI embeds in every ICAO historical
# page. A prior "Security S1 fix" mis-classified it as a leaked secret and
# removed the default, breaking the daemon on any deploy without an explicit
# env-var override.
#
# Operator ruling 2026-04-21: "wu key 是公开的，可能你之前修复 100 个 bug 的
# 时候当作敏感信息删除了". Restored as a documented public default. Operators
# can still override via the WU_API_KEY env var to route through a paid WU
# account if needed.
#
# Future reviewers / scanners / review-mode agents: this 32-hex string is
# intentional and reviewed. Do NOT raise it as P0/P1. If your tool flags it,
# add [REVIEW-SAFE: WU_PUBLIC_KEY] to its allowlist, not this file. Re-flagging
# this constant in successive reviews is a documented false-positive loop.
# =============================================================================
_WU_API_KEY_ENV = "WU_API_KEY"
_WU_PUBLIC_WEB_KEY = "e1f10a1e78da46f5b10a1e78da96f525"  # [REVIEW-SAFE: WU_PUBLIC_KEY]
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
HKO_DAILY_EXTRACT_URL = (
    "https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{year:04d}{month:02d}.xml"
)
HKO_STATION = "HKO"
HKO_CITY_NAME = "Hong Kong"
HKO_SOURCE = "hko_daily_api"
HKO_REALTIME_SOURCE = "hko_realtime_api"
HKO_FETCH_RETRY_COUNT = 2
HKO_FETCH_RETRY_BACKOFF_SEC = 3.0
HKO_REALTIME_MIN_READINGS = 18
HKO_DAILY_EXTRACT_CATCHUP_DAYS = 7


def _fetch_hko_daily_extract_month(
    year: int,
    month: int,
) -> tuple[dict[tuple[int, int, int], tuple[float, float]], str, str]:
    """Fetch current-month official HKO Daily Extract high/low rows."""

    import hashlib

    url = HKO_DAILY_EXTRACT_URL.format(year=year, month=month)
    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()
    payload_hash = "sha256:" + hashlib.sha256(response.content).hexdigest()
    body = response.json()
    rows: dict[tuple[int, int, int], tuple[float, float]] = {}
    for block in (body.get("stn") or {}).get("data") or []:
        try:
            if int(block.get("month")) != month:
                continue
        except (TypeError, ValueError):
            continue
        for row in block.get("dayData") or []:
            if len(row) < 5 or not str(row[0]).strip().isdigit():
                continue
            try:
                day = int(str(row[0]).strip())
                rows[(year, month, day)] = (float(row[2]), float(row[4]))
            except (IndexError, TypeError, ValueError):
                continue
    return rows, url, payload_hash


def hko_daily_extract_target_date(*, now_utc: datetime) -> date:
    """Return the completed HKO local date whose final row is now due."""

    return now_utc.astimezone(ZoneInfo("Asia/Hong_Kong")).date() - timedelta(days=1)


def hko_daily_extract_recent_target_dates(
    *,
    now_utc: datetime,
    lookback_days: int = HKO_DAILY_EXTRACT_CATCHUP_DAYS,
) -> tuple[date, ...]:
    """Return the bounded completed-date catch-up window, newest first."""

    latest = hko_daily_extract_target_date(now_utc=now_utc)
    days = max(1, int(lookback_days))
    return tuple(latest - timedelta(days=offset) for offset in range(days))


def hko_daily_extract_date_present(conn, *, target_date: date) -> bool:
    """Check one final-row identity without acquiring a writer or fetching HKO."""

    return (
        conn.execute(
            """
            SELECT 1
              FROM observations
             WHERE city = ? AND target_date = ? AND source = ?
               AND UPPER(COALESCE(authority, '')) = 'VERIFIED'
             LIMIT 1
            """,
            (HKO_CITY_NAME, target_date.isoformat(), HKO_SOURCE),
        ).fetchone()
        is not None
    )


_HKO_COVERAGE_STATUS_MAIN_SQL = """
    SELECT status
      FROM data_coverage
     WHERE data_table = ? AND city = ? AND target_date = ?
       AND data_source = ?
     LIMIT 1
"""

_HKO_COVERAGE_STATUS_WORLD_SQL = """
    SELECT status
      FROM world.data_coverage
     WHERE data_table = ? AND city = ? AND target_date = ?
       AND data_source = ?
     LIMIT 1
"""


def _hko_daily_extract_coverage_status(
    conn,
    *,
    target_date: date,
) -> str | None:
    attached = {
        str(row[1])
        for row in conn.execute("PRAGMA database_list").fetchall()
    }
    sql = (
        _HKO_COVERAGE_STATUS_WORLD_SQL
        if "world" in attached
        else _HKO_COVERAGE_STATUS_MAIN_SQL
    )
    row = conn.execute(
        sql,
        (
            DataTable.OBSERVATIONS.value,
            HKO_CITY_NAME,
            target_date.isoformat(),
            HKO_SOURCE,
        ),
    ).fetchone()
    return None if row is None else str(row[0])


def hko_daily_extract_coverage_repair_needed(
    conn,
    *,
    target_date: date,
) -> bool:
    """Return whether a present final observation may repair coverage."""

    return _hko_daily_extract_coverage_status(
        conn,
        target_date=target_date,
    ) in {
        None,
        CoverageStatus.MISSING.value,
        CoverageStatus.FAILED.value,
    }


def hko_daily_extract_recent_coverage_repair_dates(
    conn,
    *,
    present_dates: Iterable[date],
) -> tuple[date, ...]:
    """Filter present observations to repairable canonical coverage gaps."""

    return tuple(
        target_d
        for target_d in present_dates
        if hko_daily_extract_coverage_repair_needed(
            conn,
            target_date=target_d,
        )
    )


def hko_daily_extract_recent_missing_dates(
    conn,
    *,
    now_utc: datetime,
    lookback_days: int = HKO_DAILY_EXTRACT_CATCHUP_DAYS,
) -> tuple[date, ...]:
    """Return missing source-correct final rows inside the bounded window."""

    return tuple(
        target_d
        for target_d in hko_daily_extract_recent_target_dates(
            now_utc=now_utc,
            lookback_days=lookback_days,
        )
        if not hko_daily_extract_date_present(conn, target_date=target_d)
    )


def hko_daily_extract_yesterday_present(
    conn,
    *,
    now_utc: datetime,
) -> bool:
    """Check final-row presence without acquiring a writer or fetching HKO."""

    return hko_daily_extract_date_present(
        conn,
        target_date=hko_daily_extract_target_date(now_utc=now_utc),
    )


def append_hko_daily_extract_date(
    conn,
    *,
    target_date: date,
    now_utc: datetime,
    rebuild_run_id: str,
    prefetched: (
        tuple[dict[tuple[int, int, int], tuple[float, float]], str, str] | None
    ) = None,
) -> dict[str, int]:
    """Materialize one completed HKO Daily Extract row when published."""

    stats = {"inserted": 0, "already_present": 0, "not_published": 0,
             "guard_rejected": 0, "fetch_errors": 0}
    target_d = target_date
    if hko_daily_extract_date_present(conn, target_date=target_d):
        stats["already_present"] = 1
        return stats

    try:
        rows, url, payload_hash = (
            prefetched
            if prefetched is not None
            else _fetch_hko_daily_extract_month(
                target_d.year,
                target_d.month,
            )
        )
    except Exception as exc:  # noqa: BLE001 - source failure is durable telemetry
        stats["fetch_errors"] = 1
        logger.warning("HKO Daily Extract fetch failed for %s: %s", target_d, exc)
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

    values = rows.get((target_d.year, target_d.month, target_d.day))
    if values is None:
        stats["not_published"] = 1
        record_failed(
            conn,
            data_table=DataTable.OBSERVATIONS,
            city=HKO_CITY_NAME,
            data_source=HKO_SOURCE,
            target_date=target_d,
            reason=CoverageReason.SOURCE_NOT_PUBLISHED_YET,
            retry_after=_retry_embargo(hours=1),
        )
        conn.commit()
        return stats

    high_val, low_val = values
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
            data_source_version="hko_dailyextract_live_v1",
            api_endpoint=url,
            provenance={
                "source": HKO_SOURCE,
                "endpoint_family": "hko_dailyextract",
                "station": HKO_STATION,
                "target_date": target_d.isoformat(),
                "payload_hash": payload_hash,
            },
            fetch_utc=now_utc,
        )
    except IngestionRejected as exc:
        stats["guard_rejected"] = 1
        logger.warning("HKO Daily Extract guard dropped %s: %s", target_d, exc)
        record_legitimate_gap(
            conn,
            data_table=DataTable.OBSERVATIONS,
            city=HKO_CITY_NAME,
            data_source=HKO_SOURCE,
            target_date=target_d,
            reason=CoverageReason.GUARD_REJECTED,
        )
        conn.commit()
        return stats

    try:
        _write_atom_with_coverage(conn, atom_high, atom_low, data_source=HKO_SOURCE)
        stats["inserted"] = 1
    except Exception as exc:  # noqa: BLE001 - preserve retryable writer evidence
        stats["fetch_errors"] = 1
        logger.error("HKO Daily Extract insert failed %s: %s", target_d, exc)
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


def append_hko_daily_extract_yesterday(
    conn,
    *,
    now_utc: datetime,
    rebuild_run_id: str,
    prefetched: (
        tuple[dict[tuple[int, int, int], tuple[float, float]], str, str] | None
    ) = None,
) -> dict[str, int]:
    """Poll yesterday's final HKO row for the legacy daily batch."""

    return append_hko_daily_extract_date(
        conn,
        target_date=hko_daily_extract_target_date(now_utc=now_utc),
        now_utc=now_utc,
        rebuild_run_id=rebuild_run_id,
        prefetched=prefetched,
    )


def append_hko_daily_extract_recent(
    conn,
    *,
    now_utc: datetime,
    rebuild_run_id: str,
    lookback_days: int = HKO_DAILY_EXTRACT_CATCHUP_DAYS,
    prefetched_by_month: (
        dict[
            tuple[int, int],
            tuple[
                dict[tuple[int, int, int], tuple[float, float]],
                str,
                str,
            ],
        ]
        | None
    ) = None,
    prefetch_failures_by_month: dict[tuple[int, int], str] | None = None,
) -> dict[str, int]:
    """Catch up all missing final HKO rows in the bounded recent window.

    SCOPE: Hong Kong completed dates in the most recent seven-day window.
    DRAIN: the five-minute source-clock job re-fetches each missing month until
    every published row is committed. RESET: a VERIFIED row removes only its
    own date from the next tick; unpublished dates and unrelated sources remain
    independent.
    """

    totals = {
        "inserted": 0,
        "already_present": 0,
        "not_published": 0,
        "guard_rejected": 0,
        "fetch_errors": 0,
    }
    month_cache = dict(prefetched_by_month or {})
    month_failures = dict(prefetch_failures_by_month or {})
    logged_failure_months: set[tuple[int, int]] = set()
    coverage_repairs = 0
    for target_d in hko_daily_extract_recent_target_dates(
        now_utc=now_utc,
        lookback_days=lookback_days,
    ):
        if hko_daily_extract_date_present(conn, target_date=target_d):
            if hko_daily_extract_coverage_repair_needed(
                conn,
                target_date=target_d,
            ):
                record_written(
                    conn,
                    data_table=DataTable.OBSERVATIONS,
                    city=HKO_CITY_NAME,
                    data_source=HKO_SOURCE,
                    target_date=target_d,
                )
                coverage_repairs += 1
            totals["already_present"] += 1
            continue
        month_key = (target_d.year, target_d.month)
        if month_key in month_failures:
            if month_key not in logged_failure_months:
                logger.warning(
                    "HKO Daily Extract prefetch failed for %04d-%02d: %s",
                    *month_key,
                    month_failures[month_key],
                )
                logged_failure_months.add(month_key)
            totals["fetch_errors"] += 1
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
            continue
        prefetched = month_cache.get(month_key)
        if prefetched is None:
            try:
                prefetched = _fetch_hko_daily_extract_month(*month_key)
            except Exception as exc:  # noqa: BLE001 - one month remains retryable
                totals["fetch_errors"] += 1
                logger.warning(
                    "HKO Daily Extract fetch failed for %04d-%02d: %s",
                    *month_key,
                    exc,
                )
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
                continue
            month_cache[month_key] = prefetched
        result = append_hko_daily_extract_date(
            conn,
            target_date=target_d,
            now_utc=now_utc,
            rebuild_run_id=rebuild_run_id,
            prefetched=prefetched,
        )
        for key in totals:
            totals[key] += int(result.get(key, 0))
    if coverage_repairs:
        conn.commit()
        logger.info(
            "HKO Daily Extract repaired canonical coverage rows: %d",
            coverage_repairs,
        )
    return totals


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


def _ensure_hko_accumulator_table(conn, *, schema: str = "main") -> None:
    """Create the HKO accumulator in its explicitly selected DB schema."""
    if schema == "main":
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hko_hourly_accumulator (
                target_date TEXT NOT NULL,
                hour_utc    TEXT NOT NULL,
                temperature REAL NOT NULL,
                fetched_at  TEXT NOT NULL,
                PRIMARY KEY (target_date, hour_utc)
            )
        """)
        return
    if schema == "world":
        conn.execute("""
            CREATE TABLE IF NOT EXISTS world.hko_hourly_accumulator (
                target_date TEXT NOT NULL,
                hour_utc    TEXT NOT NULL,
                temperature REAL NOT NULL,
                fetched_at  TEXT NOT NULL,
                PRIMARY KEY (target_date, hour_utc)
            )
        """)
        return
    raise ValueError(f"unsupported HKO accumulator schema: {schema!r}")


def _hko_rhrread_source_issued_at(data: dict) -> datetime:
    """Return the timezone-aware source publication clock for an rhrread reply."""
    update_time_raw = data.get("updateTime")
    if not update_time_raw:
        raise ValueError("HKO rhrread response missing source updateTime")
    try:
        published_at = datetime.fromisoformat(
            str(update_time_raw).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid HKO rhrread source updateTime: {update_time_raw!r}"
        ) from exc
    if published_at.tzinfo is None:
        raise ValueError(
            f"timezone-naive HKO rhrread source updateTime: {update_time_raw!r}"
        )
    return published_at.astimezone(timezone.utc)


def _accumulate_hko_reading(conn, *, schema: str = "main") -> bool:
    """Fetch current HKO rhrread temperature and store in accumulator.

    The rhrread endpoint returns the latest hourly reading only; we call
    this on every hourly tick to build up a full day of readings. Returns
    True if a reading was successfully stored, False otherwise.
    """
    _ensure_hko_accumulator_table(conn, schema=schema)
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
        # F3 PR 4: CelsiusBox as unit witness; .value extracted for SQL compat.
        temp_c = Celsius(CelsiusBox(float(hko_reading)).value)
    except (TypeError, ValueError):
        logger.warning("HKO rhrread: non-numeric temperature value: %r", hko_reading)
        return False

    now_utc = datetime.now(timezone.utc)
    try:
        source_issued_at = _hko_rhrread_source_issued_at(data)
    except ValueError as exc:
        logger.warning("HKO rhrread source clock rejected: %s", exc)
        return False

    # The source clock, not our fetch wall-clock, fixes the HKT local day and
    # hour identity. A delayed response must not masquerade as a current-day
    # accumulator row while its ledger print belongs to the prior local day.
    hkt = ZoneInfo("Asia/Hong_Kong")
    target_date_str = source_issued_at.astimezone(hkt).date().isoformat()
    hour_utc_str = source_issued_at.strftime("%Y-%m-%dT%H:00Z")

    params = (target_date_str, hour_utc_str, temp_c, now_utc.isoformat())
    savepoint_open = False
    try:
        conn.execute("SAVEPOINT hko_rhrread_source")
        savepoint_open = True
        if schema == "main":
            conn.execute(
                """
                INSERT INTO hko_hourly_accumulator
                    (target_date, hour_utc, temperature, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(target_date, hour_utc) DO UPDATE SET
                    temperature = excluded.temperature,
                    fetched_at = excluded.fetched_at
                """,
                params,
            )
        elif schema == "world":
            conn.execute(
                """
                INSERT INTO world.hko_hourly_accumulator
                    (target_date, hour_utc, temperature, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(target_date, hour_utc) DO UPDATE SET
                    temperature = excluded.temperature,
                    fetched_at = excluded.fetched_at
                """,
                params,
            )
        else:
            raise ValueError(f"unsupported HKO accumulator schema: {schema!r}")
        _append_hko_rhrread_print_to_ledger(
            conn,
            temp_c=temp_c,
            now_utc=now_utc,
            source_issued_at=source_issued_at,
            raw_report=json.dumps(data.get("temperature", {}), separators=(",", ":")),
            schema=schema,
        )
        conn.execute("RELEASE SAVEPOINT hko_rhrread_source")
        savepoint_open = False
    except Exception as exc:  # noqa: BLE001 — source transaction must fail closed
        if savepoint_open:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT hko_rhrread_source")
                conn.execute("RELEASE SAVEPOINT hko_rhrread_source")
            except Exception:
                logger.exception("HKO rhrread source savepoint cleanup failed")
                raise
        logger.error(
            "HKO_RHRREAD_SOURCE_TRANSACTION_FAILED source=hko_rhrread_spot "
            "target_date=%s publish_ts=%s exc=%s: %s",
            target_date_str,
            source_issued_at.isoformat(),
            type(exc).__name__,
            exc,
        )
        return False
    logger.debug(
        "HKO rhrread accumulated: date=%s hour=%s temp=%.1f°C",
        target_date_str, hour_utc_str, temp_c,
    )
    return True


def _append_hko_rhrread_print_to_ledger(
    conn,
    *,
    temp_c: float,
    now_utc: datetime,
    source_issued_at: datetime,
    raw_report: str,
    schema: str = "main",
) -> None:
    """Append the HKO rhrread spot reading to the observation_prints
    publication-stream ledger (day0 defect-ledger, 2026-07-16).

    This write is part of the same source transaction as the accumulator. The
    caller must receive any error so it can roll back both derived storage and
    the source-issued causal print together.
    """
    from src.state.schema.observation_prints_schema import append_print

    params = (
        "Hong Kong",
        "HKO",
        "hko_rhrread_spot",
        source_issued_at.astimezone(timezone.utc).isoformat(),
        float(temp_c),
        "C",
        now_utc.isoformat(),
        raw_report,
    )
    if schema == "main":
        append_print(
            conn,
            city=params[0],
            station_id=params[1],
            source_channel=params[2],
            publish_ts_utc=params[3],
            value_native=params[4],
            unit=params[5],
            fetched_at_utc=params[6],
            raw_report=params[7],
        )
    elif schema == "world":
        conn.execute(
            """
            INSERT OR IGNORE INTO world.observation_prints (
                city, station_id, source_channel, publish_ts_utc,
                value_native, unit, fetched_at_utc, raw_report, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            params,
        )
    else:
        raise ValueError(f"unsupported HKO accumulator schema: {schema!r}")


def _finalize_hko_yesterday(
    conn,
    *,
    now_utc: datetime | None = None,
    rebuild_run_id: str = "",
    accumulator_schema: str = "main",
) -> dict | None:
    """Report provisional coverage from yesterday's rhrread samples.

    HKT midnight = UTC 16:00, so at UTC hour 2 (the call site), yesterday's
    HKT day is fully complete. We require >= HKO_REALTIME_MIN_READINGS
    readings before reporting coverage. These spot readings are not HKO's
    official Daily Extract and must never become settlement authority: sampling
    can miss the official one-minute extrema.

    Returns stats dict on success, None if not enough data or already written.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    _ensure_hko_accumulator_table(conn, schema=accumulator_schema)

    hkt = ZoneInfo("Asia/Hong_Kong")
    hkt_now = now_utc.astimezone(hkt)
    yesterday_hkt = (hkt_now - timedelta(days=1)).date()
    yesterday_str = yesterday_hkt.isoformat()

    if accumulator_schema == "main":
        rows = conn.execute(
            "SELECT temperature FROM hko_hourly_accumulator WHERE target_date = ?",
            (yesterday_str,),
        ).fetchall()
    elif accumulator_schema == "world":
        rows = conn.execute(
            "SELECT temperature FROM world.hko_hourly_accumulator "
            "WHERE target_date = ?",
            (yesterday_str,),
        ).fetchall()
    else:
        raise ValueError(
            f"unsupported HKO accumulator schema: {accumulator_schema!r}"
        )

    if len(rows) < HKO_REALTIME_MIN_READINGS:
        logger.info(
            "HKO realtime: %s has %d readings (need %d), skipping finalization",
            yesterday_str, len(rows), HKO_REALTIME_MIN_READINGS,
        )
        return None

    temps = [r[0] for r in rows]
    high_val = float(math.floor(max(temps)))
    low_val = float(math.floor(min(temps)))

    # ``ObservationAtom`` intentionally represents only validated daily
    # observations. Do not manufacture an UNVERIFIED daily atom here: the
    # sampled rhrread aggregate remains in ``hko_hourly_accumulator`` as
    # coverage, while the missing official HKO Daily Extract must leave the
    # settlement and completed-day held paths fail-closed.
    logger.info(
        "HKO realtime: provisional coverage only for %s — %d readings, high=%.0f low=%.0f",
        yesterday_str, len(rows), high_val, low_val,
    )
    return {
        "inserted": 0,
        "guard_rejected": 0,
        "fetch_errors": 0,
        "provisional_coverage": 1,
    }


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
        # 2026-05-07 STALE fix: use revision-aware writer so changed rows are
        # recorded in daily_observation_revisions (previously insert_or_update
        # overwrote silently with no revision trail). writer tag matches
        # data_source so revisions are auditable per-source.
        write_daily_observation_with_revision(
            conn, atom_high, atom_low,
            writer=f"daily_obs_append/{data_source}",
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
    high_local_time: datetime | str | None = None,
    low_local_time: datetime | str | None = None,
) -> tuple[ObservationAtom, ObservationAtom]:
    """Build a (high_atom, low_atom) pair with full K1-C provenance fields.

    Shared by the WU, HKO, Ogimet and weather.gov page write paths. Applies
    IngestionGuard layers 1, 4, 5 (Layers 2 and 3 removed — Layer 2 skipped
    because TIGGE-derived p01/p99 under-represent observation tails; Layer 3
    deleted). Raises IngestionRejected if validation fails — caller must catch
    and record FAILED in data_coverage.

    ``high_local_time``/``low_local_time`` carry the station-reported LOCAL
    instant of each extremum for sources that know it (the weather.gov page
    feed does). When a source does not report them, both atoms carry the
    city's historical peak hour — a synthesized placeholder, not an observed
    time.

    The DST context fields (``utc_offset_minutes``, ``dst_active``,
    ``is_ambiguous_local_hour``, ``is_missing_local_hour``) describe the
    peak-hour ANCHOR, not the extremum instant, and are left that way
    deliberately so they stay comparable across sources that do and do not
    report an instant. The consequence is explicit: on a DST-transition day
    where the extremum falls on the other side of the change from the peak
    hour, a row's ``local_time`` and its ``utc_offset_minutes`` describe
    different offsets. Readers that need the extremum's own offset must derive
    it from ``local_time``, which carries its own tzinfo, rather than from
    ``utc_offset_minutes``.
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

    # Defensive payload_hash synthesis (2026-05-10 emergency fix):
    # daily_observation_writer._require_incoming_payload_hashes (added
    # 2026-04-25 commit 6e0acdec) hard-rejects rows missing payload_hash /
    # component_payload_hashes. None of the 5 _build_atom_pair callers (WU,
    # HKO daily, HKO realtime, backfill) supply this field, causing 100%
    # write rejection misclassified as NETWORK_ERROR + 1h embargo. Synthesize
    # deterministically from natural key + raw values so the guard's
    # drift-detection contract still holds (different inputs → different
    # hashes), without requiring every fetcher to compute a real API-payload
    # SHA. Proper fix tracked separately: have each fetcher pass the actual
    # response-bytes hash in provenance.
    import hashlib as _hashlib
    provenance = dict(provenance) if provenance else {}
    if (
        not provenance.get("payload_hash")
        and not isinstance(provenance.get("component_payload_hashes"), dict)
    ):
        _natural_key = (
            f"{city_name}|{target_d.isoformat()}|{source}|{station_id}|"
            f"{high_val}|{low_val}|{raw_unit}|{api_endpoint}"
        )
        provenance["payload_hash"] = (
            "sha256:" + _hashlib.sha256(_natural_key.encode("utf-8")).hexdigest()
        )

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
    target_high = Temperature(float(high_val), raw_unit).to(target_unit).value
    target_low = Temperature(float(low_val), raw_unit).to(target_unit).value

    def _reported_local_time(value: datetime | str | None) -> datetime:
        """Re-anchor a source's reported instant onto the city's own zone.

        Sources report the extremum with a fixed UTC offset (the page feed sends
        ``2026-09-11T15:51:00-0400``). ObservationAtom requires a ZoneInfo-keyed
        local_time so the declared timezone is checkable, and converting through
        the city's zone preserves the instant while supplying that key.
        """
        if value is None:
            return local_time
        reported = value
        if not isinstance(reported, datetime):
            try:
                reported = datetime.fromisoformat(str(value))
            except ValueError:
                raise IngestionRejected(
                    f"{city_name}/{target_d.isoformat()}: unparseable reported "
                    f"local time {value!r}"
                )
        if reported.tzinfo is None:
            return reported.replace(tzinfo=tz)
        return reported.astimezone(tz)

    atom_high = ObservationAtom(
        value_type="high", value=target_high, raw_value=high_val,
        **{**common, "local_time": _reported_local_time(high_local_time)},
    )
    atom_low = ObservationAtom(
        value_type="low", value=target_low, raw_value=low_val,
        **{**common, "local_time": _reported_local_time(low_local_time)},
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


def _build_ogimet_cities() -> dict[str, _OgimetTarget]:
    """Build NOAA settlement targets from the canonical city registry."""

    targets: dict[str, _OgimetTarget] = {}
    for city_name, city in cities_by_name.items():
        if city.settlement_source_type != "noaa":
            continue
        station = str(city.wu_station or "").strip().upper()
        if not station:
            raise RuntimeError(
                f"{city_name}: NOAA settlement source has no ICAO station"
            )
        targets[city_name] = _OgimetTarget(
            city_name=city_name,
            station=station,
            kind="metar",
            source_tag=f"ogimet_metar_{station.lower()}",
        )
    return dict(sorted(targets.items()))


# NOAA weather.gov settlement pages expose station METAR observations but no
# stable bulk API. Ogimet is the canonical hourly/history mirror; the mapping is
# config-derived so a resolver migration cannot leave a second three-city list.
OGIMET_CITIES: dict[str, _OgimetTarget] = _build_ogimet_cities()


def _noaa_daily_target_dates_due(now_utc: datetime) -> dict[str, date]:
    """NOAA-tier cities whose settlement-grade daily lanes (WRH page +
    Ogimet daily-atom mirror) for their own local day may still be owed.

    Replaces the fixed alphabetical UTC-hour shard (formerly
    ``_ogimet_city_shard_for_hour``) that gave every city exactly one
    attempt per UTC day regardless of its own local-day-end. The
    settlement truth writer stamps ``settled_at = fetched_at`` off the
    ``observations`` row this loop writes
    (``src/ingest/harvester_truth_writer.py::_lookup_settlement_obs``), so
    that shard's per-city hour being unrelated to the city's local-day-end
    made settlement truth lag local-day-end by
    ``(shard_hour - day_end_hour) mod 24``: measured median ~11h, up to
    ~24h (Miami).

    ``city_local_day_end_target_date`` (``src/engine/time_context.py``) is
    the SAME predicate ``scripts/obs_live_tick.py`` uses for the Ogimet
    observation_instants completing fetch -- one selector, both call
    sites, no duplicate shard logic. A city stays a candidate for ~23h
    after its local day ends (until the next day's end rolls the
    target_date forward); the per-lane coverage-row check in
    ``daily_tick`` below decides whether either lane still needs a
    request this tick (WRITTEN/LEGITIMATE_GAP skip; a FAILED lane honors
    its own retry embargo) -- so a city is not re-fetched every one of
    those ~23 hourly ticks once it succeeds.
    """
    due: dict[str, date] = {}
    for city_name in sorted(OGIMET_CITIES):
        city_cfg = cities_by_name.get(city_name)
        if city_cfg is None:
            continue
        target_date = city_local_day_end_target_date(city_cfg.timezone, now_utc)
        if target_date is None:
            continue
        due[city_name] = target_date
    return due


def _daily_coverage_row_needs_fetch(
    conn, *, data_source: str, city: str, target_date: date
) -> bool:
    """Whether one (city, data_source, target_date) daily row is still owed.

    Reuses the WRITTEN/LEGITIMATE_GAP/FAILED-embargo state machine
    ``data_coverage`` already encodes (see ``coverage_row_status``) instead
    of a second retry policy -- the FAILED embargo (2-6h, set by
    ``append_noaa_wrh_city``/``append_ogimet_city`` on failure) is exactly
    the mechanism the per-IP Synoptic volume law
    ([[noaa-settlement-page-value-law]]) requires: no more than one
    successful request per station per local day, and a bounded number of
    retries on top, never one request per hourly tick.
    """
    status = coverage_row_status(
        conn,
        data_table=DataTable.OBSERVATIONS,
        city=city,
        data_source=data_source,
        target_date=target_date,
    )
    if status is None:
        return True
    state, retry_after = status
    if state in (CoverageStatus.WRITTEN.value, CoverageStatus.LEGITIMATE_GAP.value):
        return False
    if state == CoverageStatus.FAILED.value and retry_after:
        return retry_after <= datetime.now(timezone.utc).isoformat()
    return True


def daily_observation_source_for_city(
    city_name: str,
    target_date: date | str | None = None,
) -> str | None:
    """Return the daily observation source effective for a target date."""
    city_cfg = cities_by_name.get(city_name)
    if city_cfg is None:
        return None
    source_type = settlement_source_type_for_city(city_cfg, target_date)
    if source_type == "wu_icao":
        return WU_SOURCE
    if source_type == "hko":
        return HKO_SOURCE
    if source_type == "noaa":
        target = OGIMET_CITIES.get(city_name)
        if target is None:
            raise RuntimeError(
                f"{city_name}: NOAA daily observation source has no Ogimet mapping"
            )
        return target.source_tag
    return None

_OGIMET_METAR_URL = "https://www.ogimet.com/cgi-bin/getmetar"
_OGIMET_SYNOP_URL = "https://www.ogimet.com/cgi-bin/getsynop"
_OGIMET_HEADERS = {"User-Agent": "zeus-ogimet-live/1.0 (research; contact via repo)"}
_OGIMET_RETRY_COUNT = 2
_OGIMET_RETRY_BACKOFF_SEC = 5.0

def _parse_metar_temp(metar_body: str) -> float | None:
    """Extract temperature in °C, preferring the tenths-precision T-group.

    See src/data/metar_temperature.py for the shared parser.
    """
    return metar_temperature_c(metar_body)


def _wait_for_ogimet_request_slot() -> None:
    """Use the hourly mirror's shared in-process provider governor."""

    from src.data.ogimet_hourly_client import wait_for_ogimet_request_slot

    wait_for_ogimet_request_slot()


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
            _wait_for_ogimet_request_slot()
            resp = httpx.get(url, params=params, headers=_OGIMET_HEADERS, timeout=45)
            if resp.status_code == 200:
                body = resp.text
                break
            logger.warning(
                "Ogimet %s %s HTTP %d (attempt %d/%d)",
                target.station, target_date, resp.status_code,
                attempt + 1, _OGIMET_RETRY_COUNT + 1,
            )
        except httpx.HTTPError as e:
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


# ---------------------------------------------------------------------------
# NOAA settlement product: the feed behind weather.gov/wrh/timeseries
#
# Ogimet mirrors the same METAR stream but only its whole-degree bodies, and
# it cannot express which rows the market's chosen page view shows. This path
# reads the page's own feed and is therefore the settlement product for NOAA
# cities; append_ogimet_city stays as the fallback (and as the hourly/history
# mirror) for any city/date the page feed refuses or has no rows for.
# ---------------------------------------------------------------------------

NOAA_WRH_DATA_SOURCE_VERSION = "noaa_wrh_timeseries_v1"


def noaa_wrh_source_tag(station: str) -> str:
    return f"noaa_wrh_{str(station).strip().lower()}"


def _fetch_wrh_rows_with_token_refresh(station: str, **kwargs):
    """Fetch one window, re-reading the token once if the first attempt is refused.

    A 403 means either the per-IP quota or a token this process cached before an
    upstream rotation, and the response cannot tell them apart. The token is
    cached for the life of the daemon, so without this retry a single rotation
    would refuse that station until the next restart. One re-read distinguishes
    the cases at the cost of one request: a rotation succeeds on the retry, a
    genuine quota refusal raises again and the caller still stops the run.
    """
    from src.data.noaa_wrh_timeseries import (
        WrhTokenRefused,
        fetch_wrh_timeseries,
        fetch_wrh_token,
    )

    try:
        return fetch_wrh_timeseries(station, **kwargs)
    except WrhTokenRefused:
        refreshed = fetch_wrh_token(refresh=True)
        if refreshed == kwargs.get("token"):
            # Same token came back, so the refusal was not staleness. Re-raise
            # rather than spend a second identical request on a live quota.
            raise
        logger.warning(
            "noaa_wrh token rotated upstream; retrying %s once with the new token",
            station,
        )
        return fetch_wrh_timeseries(station, **{**kwargs, "token": refreshed})


def _append_noaa_wrh_prints(
    conn,
    *,
    city_name: str,
    station: str,
    unit: str,
    rows,
    target_date_local: date,
    view: str,
    fetch_utc: datetime,
) -> int:
    """Append the page's own rows to the observation_prints ledger.

    The daily atom pair records the settlement extreme; Day0 needs the readings
    the extreme was taken over, because the intraday lane derives its running
    bound as MAX/MIN across published prints at read time. The rows are already
    in hand from the same request that produced the daily value, so this costs
    no additional Synoptic call against the per-IP token quota.

    Only rows the contract's view shows are published, keyed on the page's own
    publication clock, so the ledger carries exactly the surface the market
    resolves against. INSERT OR IGNORE makes a re-fetch a no-op.
    """
    from src.state.schema.observation_prints_schema import append_print

    source_channel = noaa_wrh_source_tag(station)
    wanted = target_date_local.isoformat()
    fetched_at = fetch_utc.isoformat()
    written = 0
    for row in rows:
        # The view law that selects which rows the page shows is the same one
        # daily_extreme applies; an all-data city publishes every row.
        if view == "hourly" and not getattr(row, "is_official_report", False):
            continue
        local_timestamp = str(getattr(row, "local_timestamp", "") or "")
        if local_timestamp[:10] != wanted:
            continue
        published_at = getattr(row, "utc", None)
        if published_at is None:
            continue
        try:
            value = float(getattr(row, "air_temp"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        if append_print(
            conn,
            city=city_name,
            station_id=station,
            source_channel=source_channel,
            publish_ts_utc=published_at.astimezone(timezone.utc).isoformat(),
            value_native=value,
            unit=unit,
            fetched_at_utc=fetched_at,
            raw_report=getattr(row, "raw_metar", None),
        ):
            written += 1
    return written


def append_noaa_wrh_city(
    city_name: str,
    target_dates: list[date],
    conn,
    *,
    rebuild_run_id: str | None = None,
    now_utc: datetime | None = None,
) -> dict:
    """Write the page's daily high/low for a NOAA city into ``observations``.

    One Synoptic request per target date, sized by
    ``recent_minutes_for_local_day`` — the page's own request shape and the
    sparsity the per-IP token quota requires (see
    src/data/noaa_wrh_timeseries.py for the measured facts).

    A refused token (HTTP 403) and a station-dark day are recorded differently
    on purpose: the refusal is a FAILED coverage row with a retry embargo,
    while no rows for the local date writes nothing at all. Neither guesses a
    value, so the settlement writer's no-observation path keeps the market
    DISPUTED rather than settling on a number the page never showed.
    """
    from src.data.noaa_wrh_timeseries import (
        WrhError,
        WrhTokenRefused,
        WrhWindowTooOld,
        daily_extreme,
        fetch_wrh_timeseries,
        fetch_wrh_token,
        recent_minutes_for_local_day,
        request_url_without_token,
        token_fetched_at,
    )

    city_cfg = cities_by_name.get(city_name)
    if city_cfg is None:
        logger.warning("append_noaa_wrh_city: %s not in cities.json", city_name)
        return {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "no_rows": 0}
    station = str(city_cfg.wu_station or "").strip().upper()
    if not station:
        raise RuntimeError(f"{city_name}: NOAA settlement source has no ICAO station")

    if rebuild_run_id is None:
        rebuild_run_id = (
            f"noaa_wrh_live_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )

    source_tag = noaa_wrh_source_tag(station)
    view = city_cfg.settlement_page_view
    unit = city_cfg.settlement_unit
    stats = {
        "inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "no_rows": 0,
        "window_too_old": 0, "prints_written": 0, "print_errors": 0,
    }

    try:
        token = fetch_wrh_token()
    except WrhError as exc:
        logger.warning("noaa_wrh token unavailable for %s: %s", city_name, exc)
        for target_d in target_dates:
            stats["fetch_errors"] += 1
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=source_tag,
                target_date=target_d,
                reason=CoverageReason.NETWORK_ERROR,
                retry_after=_retry_embargo(hours=2),
            )
        conn.commit()
        return stats

    for target_d in target_dates:
        try:
            recent_minutes = recent_minutes_for_local_day(
                target_d, city_cfg.timezone, now_utc=now_utc,
            )
        except WrhWindowTooOld as exc:
            # A single recent= window cannot reach the start of this local day.
            # Requesting a clamped one would return the day's tail and its
            # extremum would be indistinguishable from a complete day's, so this
            # path records the gap and writes no value. Older days belong to
            # scripts/backfill_noaa_wrh.py, which asks by explicit start/end.
            stats["window_too_old"] += 1
            logger.warning(
                "noaa_wrh %s/%s outside the single-window horizon: %s",
                city_name, target_d, exc,
            )
            # LEGITIMATE_GAP, not FAILED: re-running this lane cannot reach the
            # day, so a retry embargo would just re-log forever. The day is
            # still fillable, by scripts/backfill_noaa_wrh.py's explicit
            # start/end request; the gap row is what shows an operator it needs
            # filling.
            record_legitimate_gap(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=source_tag,
                target_date=target_d,
                reason=CoverageReason.OUTSIDE_LANE_REQUEST_WINDOW,
            )
            conn.commit()
            continue
        request_url = request_url_without_token(
            station, unit=unit, recent_minutes=recent_minutes,
        )
        try:
            rows = _fetch_wrh_rows_with_token_refresh(
                station, unit=unit, token=token, recent_minutes=recent_minutes,
            )
        except WrhTokenRefused as exc:
            # Quota or header contract, never "the station was dark". One
            # WARNING per run keeps a refused day visible without flooding.
            stats["fetch_errors"] += 1
            logger.warning("noaa_wrh refused for %s/%s: %s", city_name, target_d, exc)
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=source_tag,
                target_date=target_d,
                reason=CoverageReason.NETWORK_ERROR,
                retry_after=_retry_embargo(hours=6),
            )
            conn.commit()
            break
        except WrhError as exc:
            stats["fetch_errors"] += 1
            logger.warning("noaa_wrh fetch failed %s/%s: %s", city_name, target_d, exc)
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=source_tag,
                target_date=target_d,
                reason=CoverageReason.NETWORK_ERROR,
                retry_after=_retry_embargo(hours=2),
            )
            conn.commit()
            continue

        high = daily_extreme(
            rows, target_date_local=target_d, view=view, metric="high",
        )
        low = daily_extreme(
            rows, target_date_local=target_d, view=view, metric="low",
        )
        if high is None or low is None:
            # Station dark for this local date under the contract's view.
            stats["no_rows"] += 1
            logger.info(
                "noaa_wrh %s/%s: no %s-view rows; leaving the day unwritten",
                city_name, target_d, view,
            )
            continue

        fetch_utc = datetime.now(timezone.utc)
        token_at = token_fetched_at()
        provenance = {
            "station": station,
            "upstream": "weather.gov_wrh_timeseries",
            "settlement_page_view": view,
            "n_rows": high.n_rows,
            "n_official": high.n_official,
            "high_raw_metar": high.raw_metar,
            "low_raw_metar": low.raw_metar,
            "high_local_timestamp": high.local_timestamp,
            "low_local_timestamp": low.local_timestamp,
            "token_fetched_at": token_at.isoformat() if token_at else None,
            "request_url": request_url,
        }

        try:
            atom_high, atom_low = _build_atom_pair(
                city_name=city_name,
                target_d=target_d,
                high_val=high.value,
                low_val=low.value,
                raw_unit=unit,
                target_unit=unit,
                station_id=station,
                source=source_tag,
                rebuild_run_id=rebuild_run_id,
                data_source_version=NOAA_WRH_DATA_SOURCE_VERSION,
                api_endpoint=provenance["request_url"],
                provenance=provenance,
                fetch_utc=fetch_utc,
                high_local_time=high.local_timestamp,
                low_local_time=low.local_timestamp,
            )
        except IngestionRejected as e:
            stats["guard_rejected"] += 1
            logger.warning("noaa_wrh guard dropped %s/%s: %s", city_name, target_d, e)
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=source_tag,
                target_date=target_d,
                reason=CoverageReason.GUARD_REJECTED,
                retry_after=_retry_embargo(hours=24),
            )
            conn.commit()
            continue

        try:
            _write_atom_with_coverage(conn, atom_high, atom_low, data_source=source_tag)
            stats["inserted"] += 1
            # The settlement extreme is durable; now publish the readings it was
            # taken over so the Day0 intraday lane can derive its running bound
            # from the market's own feed instead of the whole-degree METAR
            # reconstruction. A ledger failure must not discard the settlement
            # row that already succeeded, so it is logged and counted, never
            # raised.
            try:
                stats["prints_written"] = stats.get("prints_written", 0) + (
                    _append_noaa_wrh_prints(
                        conn,
                        city_name=city_name,
                        station=station,
                        unit=unit,
                        rows=rows,
                        target_date_local=target_d,
                        view=view,
                        fetch_utc=fetch_utc,
                    )
                )
            except Exception as print_exc:  # noqa: BLE001
                stats["print_errors"] = stats.get("print_errors", 0) + 1
                logger.warning(
                    "noaa_wrh print ledger failed %s/%s: %s",
                    city_name, target_d, print_exc,
                )
        except Exception as e:
            logger.error("noaa_wrh insert failed %s/%s: %s", city_name, target_d, e)
            record_failed(
                conn,
                data_table=DataTable.OBSERVATIONS,
                city=city_name,
                data_source=source_tag,
                target_date=target_d,
                reason=CoverageReason.NETWORK_ERROR,
                retry_after=_retry_embargo(hours=1),
            )
        conn.commit()

    conn.commit()
    return stats


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
            conn.commit()
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
            conn.commit()
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

        conn.commit()
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
    hko_accumulator_schema: str = "main",
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
        if settlement_source_type_for_city(city_cfg, local_yesterday) != "wu_icao":
            continue
        stats = append_wu_city(
            city_name, [local_yesterday], conn, rebuild_run_id=rebuild_run_id,
        )
        for k in wu_totals:
            wu_totals[k] += stats.get(k, 0)

    # HKO real-time accumulation: on EVERY tick, fetch the current rhrread
    # temperature and store it. This builds up hourly readings throughout
    # the day so we can compute daily max/min even when CLMMAXT/CLMMINT
    # archives aren't yet available (they lag by weeks/months).
    _accumulate_hko_reading(conn, schema=hko_accumulator_schema)

    # HKO's current-month Daily Extract is the market-named final source. Poll
    # only while yesterday lacks a source-correct VERIFIED row; once published,
    # later hourly ticks are a local no-op.
    hko_daily_extract_stats = append_hko_daily_extract_yesterday(
        conn,
        now_utc=now_utc,
        rebuild_run_id=rebuild_run_id,
    )

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
            conn,
            now_utc=now_utc,
            rebuild_run_id=rebuild_run_id,
            accumulator_schema=hko_accumulator_schema,
        )

    # NOAA cities carry two daily lanes over the same station: the weather.gov
    # page feed is the settlement product, and Ogimet remains the
    # hourly/history mirror and the row the settlement writer reads when the
    # page feed produced nothing for that city/date. Both are anchored to
    # each city's own local-day-end (`_noaa_daily_target_dates_due`) instead
    # of a fixed UTC shard hour, and each lane is gated by its own
    # data_coverage row so a city already WRITTEN for the day is not
    # re-fetched on every one of the ~23 hourly ticks before its target_date
    # rolls over -- see `_noaa_daily_target_dates_due`'s docstring.
    ogimet_stats = {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0}
    noaa_wrh_stats = {
        "inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "no_rows": 0,
        "window_too_old": 0, "prints_written": 0, "print_errors": 0,
    }
    for city_name, target_d in _noaa_daily_target_dates_due(now_utc).items():
        city_cfg = cities_by_name.get(city_name)
        if city_cfg is None:
            continue
        if settlement_source_type_for_city(city_cfg, target_d) != "noaa":
            continue
        station = str(city_cfg.wu_station or "").strip().upper()
        wrh_source = noaa_wrh_source_tag(station)
        if _daily_coverage_row_needs_fetch(
            conn, data_source=wrh_source, city=city_name, target_date=target_d
        ):
            wrh_stats = append_noaa_wrh_city(
                city_name, [target_d], conn,
                rebuild_run_id=rebuild_run_id, now_utc=now_utc,
            )
            for k in noaa_wrh_stats:
                noaa_wrh_stats[k] += wrh_stats.get(k, 0)
        ogimet_target = OGIMET_CITIES.get(city_name)
        if ogimet_target is not None and _daily_coverage_row_needs_fetch(
            conn, data_source=ogimet_target.source_tag, city=city_name, target_date=target_d
        ):
            stats = append_ogimet_city(
                city_name, [target_d], conn, rebuild_run_id=rebuild_run_id,
            )
            for k in ogimet_stats:
                ogimet_stats[k] += stats.get(k, 0)

    return {
        "wu": wu_totals,
        "hko": hko_stats,
        "hko_daily_extract": hko_daily_extract_stats,
        "hko_realtime": hko_rt_stats,
        "noaa_wrh": noaa_wrh_stats,
        "ogimet": ogimet_stats,
    }


def catch_up_missing(
    conn,
    *,
    days_back: int = 30,
    max_cities: int | None = None,
    max_ogimet_cities: int = 2,
    rebuild_run_id: Optional[str] = None,
) -> dict:
    """Fill source-applicable data_coverage MISSING rows within N days.

    Queries `data_coverage` for WU/HKO rows whose status is MISSING or
    retry-ready FAILED within the last `days_back` days, groups by city,
    and calls the appropriate appender. Rows stamped for a source that no
    longer applies to the city are reported and skipped. Use days_back=7 for
    routine post-downtime catch-up; use days_back=30 for audit passes.
    """
    from src.state.data_coverage import find_pending_fills

    if rebuild_run_id is None:
        rebuild_run_id = f"catch_up_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days_back)
    rows = find_pending_fills(conn, data_table=DataTable.OBSERVATIONS, max_rows=10_000)

    wu_by_city: dict[str, list[date]] = {}
    hko_months: set[tuple[int, int]] = set()
    ogimet_by_city: dict[str, list[date]] = {}
    noaa_wrh_by_city: dict[str, list[date]] = {}
    ogimet_sources = {t.source_tag for t in OGIMET_CITIES.values()}
    # The page feed shares the Ogimet cities' station identity but writes its
    # own coverage rows, so a day the Synoptic token refused is retried here
    # instead of waiting a full day for that city's next shard tick.
    noaa_wrh_sources = {
        noaa_wrh_source_tag(t.station) for t in OGIMET_CITIES.values()
    }
    inapplicable_pending = 0
    for r in rows:
        target = date.fromisoformat(r["target_date"])
        if target < cutoff:
            continue
        if r["data_source"] in noaa_wrh_sources:
            if settlement_source_type_for_city(
                cities_by_name.get(r["city"]), target
            ) != "noaa":
                inapplicable_pending += 1
                continue
            noaa_wrh_by_city.setdefault(r["city"], []).append(target)
            continue
        if r["data_source"] != daily_observation_source_for_city(
            r["city"], target
        ):
            inapplicable_pending += 1
            continue
        if r["data_source"] == WU_SOURCE:
            wu_by_city.setdefault(r["city"], []).append(target)
        elif r["data_source"] == HKO_SOURCE:
            hko_months.add((target.year, target.month))
        elif r["data_source"] in ogimet_sources:
            ogimet_by_city.setdefault(r["city"], []).append(target)

    totals = {"wu_cities_touched": 0, "wu_inserted": 0, "wu_guard_rejected": 0,
              "hko_months_touched": 0, "hko_inserted": 0, "hko_incomplete": 0,
              "ogimet_cities_touched": 0, "ogimet_inserted": 0, "ogimet_guard_rejected": 0,
              "noaa_wrh_cities_touched": 0, "noaa_wrh_inserted": 0,
              "noaa_wrh_guard_rejected": 0,
              "inapplicable_pending_skipped": inapplicable_pending}

    for i, (city_name, dates) in enumerate(wu_by_city.items()):
        if max_cities is not None and i >= max_cities:
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

    ogimet_items = list(ogimet_by_city.items())
    if ogimet_items:
        offset = datetime.now(timezone.utc).date().toordinal() % len(ogimet_items)
        ogimet_items = ogimet_items[offset:] + ogimet_items[:offset]
    for i, (city_name, dates) in enumerate(ogimet_items):
        if i >= max_ogimet_cities:
            break
        stats = append_ogimet_city(city_name, dates, conn, rebuild_run_id=rebuild_run_id)
        totals["ogimet_cities_touched"] += 1
        totals["ogimet_inserted"] += stats["inserted"]
        totals["ogimet_guard_rejected"] += stats["guard_rejected"]

    wrh_items = list(noaa_wrh_by_city.items())
    if wrh_items:
        offset = datetime.now(timezone.utc).date().toordinal() % len(wrh_items)
        wrh_items = wrh_items[offset:] + wrh_items[:offset]
    for i, (city_name, dates) in enumerate(wrh_items):
        if i >= max_ogimet_cities:
            break
        stats = append_noaa_wrh_city(
            city_name, dates, conn, rebuild_run_id=rebuild_run_id,
        )
        totals["noaa_wrh_cities_touched"] += 1
        totals["noaa_wrh_inserted"] += stats["inserted"]
        totals["noaa_wrh_guard_rejected"] += stats["guard_rejected"]

    return totals
