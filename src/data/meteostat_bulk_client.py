# Created: 2026-04-22
# Last reused/audited: 2026-04-22
# Authority basis: subagent research 2026-04-22 — bulk CSV for sparse-WU
#                  ICAO stations; no auth, no per-IP rate limit, parallel-safe.
"""Meteostat bulk-CSV client for filling sparse-WU ICAO stations.

Meteostat publishes static gzipped CSV archives at
``bulk.meteostat.net/v2/hourly/<WMO>.csv.gz``. One file per station covers
the full history (often back to the 1940s-80s) in a single download:

  date, hour, temp_C, dewpoint_C, rhum_pct, prcp_mm, snow_mm,
  wdir_deg, wspd_kmh, wpgt_kmh, pres_hPa, tsun_min, coco

No authentication. No documented rate limit. Parallel-safe (static CDN
files, not dynamic endpoints). Replaces ~12 hours of Ogimet serial work
with <60 seconds of parallel downloads for five stations.

Caveat: Meteostat bulk files lag real-time by weeks to months; verify the
last-date empirically before using on live dates. Populated values are
hourly-aggregated (one row per UTC hour), not sub-hourly, so extremum
semantics reduce to max == min == value for each row. This is acceptable
for historical fill of sparse stations where no sub-hourly data exists.

ICAO → WMO mapping was verified empirically 2026-04-22 against
``https://bulk.meteostat.net/v2/hourly/<WMO>.csv.gz`` for each station.
Adding a new station requires probing Meteostat's station lookup at
``https://meteostat.net/en/station/<WMO>`` and confirming the ICAO field.
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from src.data.wu_hourly_client import HourlyObservation

logger = logging.getLogger(__name__)


METEOSTAT_BULK_URL = "https://bulk.meteostat.net/v2/hourly/{wmo}.csv.gz"
METEOSTAT_HEADERS = {"User-Agent": "zeus-obs-v2/1.0 (research; contact via repo)"}


# ICAO → Meteostat station ID mapping for Tier 1 WU cities. Derived
# 2026-04-22 from Meteostat's full station catalog
# (https://bulk.meteostat.net/v2/stations/full.json.gz) for ICAO-matched
# entries, plus the five sparse-station IDs (ZGSZ/DNMM/WIHH/VILK/MPMG)
# empirically verified by the subagent research pass.
#
# Note: Meteostat's bulk endpoint accepts both pure-WMO numeric IDs
# (e.g. "72530") AND Meteostat-internal station IDs with letter prefixes
# (e.g. "KBKF0", "EGLC0") when the station has no canonical WMO. Both
# forms route to /v2/hourly/<id>.csv.gz and return the same format.
#
# Coverage empirically verified 2026-04-22: each ID returns HTTP 200
# with ≥16,000 rows in the 2024-01-01 → 2026-04-21 window. Cutoff
# dates (where the archive lags real-time) vary per station from
# 2025-07-27 (DNMM Lagos) to 2026-03-15 (FACT Cape Town).
#
# Stations NOT in catalog: Jakarta WIHH was missing from the ICAO-keyed
# catalog but WMO 96749 works directly on the bulk endpoint (subagent
# research). Shanghai ZSPD has no Meteostat archive — fall back to
# WU + Ogimet only.
ICAO_TO_WMO: dict[str, str] = {
    # US stations (WMO-prefixed block 72xxx) ------------------------
    "KATL": "72219",  # Atlanta
    "KAUS": "74745",  # Austin
    "KDAL": "72258",  # Dallas
    "KBKF": "KBKF0",  # Denver (Buckley SFB)
    "KHOU": "SC9N0",  # Houston (Hobby) — Meteostat internal ID
    "KLAX": "72295",  # Los Angeles
    "KMIA": "72202",  # Miami
    "KLGA": "72503",  # NYC (LaGuardia)
    "KSFO": "72494",  # San Francisco
    "KSEA": "72793",  # Seattle
    "KORD": "72530",  # Chicago
    # EU / MENA -----------------------------------------------------
    "EHAM": "06240",  # Amsterdam
    "LTAC": "17128",  # Ankara
    "FACT": "68816",  # Cape Town
    "EFHK": "02974",  # Helsinki
    "OEJN": "41024",  # Jeddah
    "EGLC": "EGLC0",  # London (City)
    "LEMD": "08221",  # Madrid
    "LIMC": "16066",  # Milan (Malpensa)
    "EDDM": "10866",  # Munich
    # Paris LFPB (Le Bourget) WMO id intentionally not seeded — Meteostat
    # bulk fallback for Paris is disabled until a verified LFPB WMO id is
    # added (Polymarket migrated Paris settlement LFPG -> LFPB on
    # 2026-05-01; see architecture/paris_station_resolution_2026-05-01.yaml
    # and config/cities.json _changelog 2026-05-01 entry). LFPG (07157) is
    # the wrong-station mapping and is removed; tier_resolver will skip
    # the meteostat_bulk fallback for Paris while the entry is absent,
    # which is the safe behavior until LFPB is verified upstream.
    "EPWA": "12375",  # Warsaw (Chopin)
    # Asia ----------------------------------------------------------
    "ZBAA": "54511",  # Beijing
    "RKPK": "47153",  # Busan
    "ZUUU": "56294",  # Chengdu
    "ZUCK": "57516",  # Chongqing
    "ZGGG": "59287",  # Guangzhou
    "WIHH": "96749",  # Jakarta (Halim) — not in ICAO-keyed catalog; WMO verified directly
    "OPKC": "41780",  # Karachi
    "WMKK": "48647",  # Kuala Lumpur
    "VILK": "42369",  # Lucknow
    "RPLL": "98429",  # Manila
    "RKSI": "47113",  # Seoul (Incheon)
    "ZGSZ": "59493",  # Shenzhen
    "WSSS": "48698",  # Singapore
    "RCSS": "46696",  # Taipei (Songshan)
    "RJTT": "47671",  # Tokyo (Haneda)
    "ZHHH": "57494",  # Wuhan
    # Oceania -------------------------------------------------------
    "NZAA": "93119",  # Auckland
    "NZWN": "93436",  # Wellington
    # Africa --------------------------------------------------------
    "DNMM": "65201",  # Lagos
    # Americas ------------------------------------------------------
    "SAEZ": "87576",  # Buenos Aires
    "MMMX": "76679",  # Mexico City
    "MPMG": "78384",  # Panama City (Albrook). Subagent-verified ID;
                      # keep stable to preserve idempotency of prior fill.
    "SBGR": "SBGR0",  # Sao Paulo (Guarulhos) — Meteostat internal ID
    "CYYZ": "71624",  # Toronto
    # Stations without Meteostat archives (fall back to WU + Ogimet):
    # Shanghai ZSPD — absent from catalog, bulk returns 404.
    #
    # To add a new city, verify:
    #   curl -I https://bulk.meteostat.net/v2/hourly/<id>.csv.gz
    # returns HTTP 200 AND the csv contains rows in the target date range.
}


@dataclass(frozen=True)
class MeteostatBulkFetchResult:
    """Structured result of one ``fetch_meteostat_bulk`` call."""

    observations: list[HourlyObservation] = field(default_factory=list)
    raw_row_count: int = 0
    failure_reason: Optional[str] = None
    retryable: bool = False
    error: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.failure_reason is not None


def meteostat_source_tag(icao: str) -> str:
    """Return the canonical source tag for Meteostat-sourced rows."""
    return f"meteostat_bulk_{icao.lower()}"


def fetch_meteostat_bulk(
    icao: str,
    *,
    start_date: date,
    end_date: date,
    city_name: str,
    timezone_name: str,
    unit: str = "C",
    timeout_seconds: float = 60.0,
) -> MeteostatBulkFetchResult:
    """Download Meteostat's full bulk CSV for *icao* and filter to the date range.

    Parameters
    ----------
    icao:
        4-letter ICAO code. Must be in ``ICAO_TO_WMO``.
    start_date, end_date:
        Inclusive local-date range.
    city_name:
        cities.json key; stamped on each ``HourlyObservation.city``.
    timezone_name:
        IANA zone for local-date bucketing.
    unit:
        'C' (default) or 'F'. Meteostat stores °C natively; F conversion
        applied after fetch.
    timeout_seconds:
        Per-request HTTP timeout. Default 60s because the gzip decompress
        + CSV parse for a large station can take a few seconds after the
        initial 1-3 MB download.

    Returns
    -------
    MeteostatBulkFetchResult
        ``observations`` is the list of per-hour rows whose local date
        is in ``[start_date, end_date]``. ``failure_reason`` is set on
        HTTP error or parse error.
    """
    if icao not in ICAO_TO_WMO:
        return MeteostatBulkFetchResult(
            failure_reason="UNSUPPORTED_ICAO",
            retryable=False,
            error=f"{icao!r} not in ICAO_TO_WMO; add WMO mapping explicitly.",
        )
    if unit not in ("F", "C"):
        raise ValueError(f"unit must be 'F' or 'C', got {unit!r}")

    wmo = ICAO_TO_WMO[icao]
    url = METEOSTAT_BULK_URL.format(wmo=wmo)

    try:
        resp = httpx.get(url, timeout=timeout_seconds, headers=METEOSTAT_HEADERS)
    except (httpx.HTTPError, httpx.RequestError) as exc:
        logger.warning("Meteostat bulk fetch raised %s for %s: %s", type(exc).__name__, icao, exc)
        return MeteostatBulkFetchResult(
            failure_reason="NETWORK_ERROR",
            retryable=True,
            error=f"{type(exc).__name__}: {exc}",
        )

    if resp.status_code == 404:
        return MeteostatBulkFetchResult(
            failure_reason="NOT_FOUND",
            retryable=False,
            error=f"WMO {wmo} has no bulk archive at {url}",
        )
    if resp.status_code != 200:
        return MeteostatBulkFetchResult(
            failure_reason="HTTP_ERROR",
            retryable=resp.status_code >= 500,
            error=f"HTTP {resp.status_code}",
        )

    try:
        raw = gzip.decompress(resp.content)
    except (OSError, gzip.BadGzipFile) as exc:
        return MeteostatBulkFetchResult(
            failure_reason="PARSE_ERROR",
            retryable=True,
            error=f"gunzip failed: {exc}",
        )

    tz = ZoneInfo(timezone_name)
    observations: list[HourlyObservation] = []
    raw_count = 0
    source_tag = meteostat_source_tag(icao)

    reader = csv.reader(io.StringIO(raw.decode("utf-8", errors="replace")))
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    for row in reader:
        raw_count += 1
        if len(row) < 3:
            continue
        date_s, hour_s, temp_s = row[0], row[1], row[2]
        if not date_s or not hour_s or not temp_s:
            continue
        # Early out: skip rows outside the requested UTC-date window. Note
        # the window filter is on UTC date; we re-check local_date after
        # conversion to include rows that straddle the local boundary.
        if date_s < start_str or date_s > end_str:
            # Tolerate one day either side so we capture timezone-shifted
            # local dates at the boundary.
            try:
                d = date.fromisoformat(date_s)
            except ValueError:
                continue
            delta = (d - start_date).days
            if delta < -1 or delta > (end_date - start_date).days + 1:
                continue
        try:
            hour = int(hour_s)
            temp_c = float(temp_s)
            utc_dt = datetime.fromisoformat(f"{date_s}T{hour:02d}:00:00+00:00")
        except (ValueError, TypeError):
            continue

        local_dt = utc_dt.astimezone(tz)
        local_date = local_dt.date()
        if local_date < start_date or local_date > end_date:
            continue

        utc_offset = local_dt.utcoffset()
        dst_offset = local_dt.dst()
        dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
        is_ambiguous = bool(getattr(local_dt, "fold", 0))

        if unit == "F":
            temp_out = temp_c * 9.0 / 5.0 + 32.0
        else:
            temp_out = temp_c

        observations.append(
            HourlyObservation(
                city=city_name,
                target_date=local_date.isoformat(),
                local_hour=float(local_dt.hour),
                local_timestamp=local_dt.isoformat(),
                utc_timestamp=utc_dt.isoformat(),
                utc_offset_minutes=(
                    int(utc_offset.total_seconds() / 60) if utc_offset else 0
                ),
                dst_active=1 if dst_active else 0,
                is_ambiguous_local_hour=1 if is_ambiguous else 0,
                is_missing_local_hour=0,  # Meteostat publishes hourly-aligned UTC; never inside a DST gap
                time_basis="utc_hour_bucket_extremum",
                hour_max_temp=temp_out,
                hour_min_temp=temp_out,  # single value per hour; max == min
                hour_max_raw_ts=utc_dt.isoformat(),
                hour_min_raw_ts=utc_dt.isoformat(),
                temp_unit=unit,
                station_id=icao,
                observation_count=1,
            )
        )

    return MeteostatBulkFetchResult(
        observations=observations,
        raw_row_count=raw_count,
    )
