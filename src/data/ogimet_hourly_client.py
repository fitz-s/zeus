# Created: 2026-04-21
# Last reused/audited: 2026-04-21
# Authority basis: plan v3 Phase 0 file #5 (.omc/plans/observation-instants-
#                  migration-iter3.md L86-93); step2_phase0_pilot_plan.md.
"""Ogimet METAR hourly-observation client for observation_instants_v2 backfill.

Wraps ``https://www.ogimet.com/cgi-bin/getmetar`` which mirrors NOAA METAR
bulletins (including off-hour SPECI at extremes) for any ICAO station.
Phase 0 uses this only for the three cities whose
``settlement_source_type == 'noaa'``: Istanbul (LTFM), Moscow (UUWW),
Tel Aviv (LLBG).

**Extremum-preserving semantics (critical)**. Same aggregation contract
as ``wu_hourly_client``: each UTC hour bucket emits one
``HourlyObservation`` carrying ``hour_max_temp`` / ``hour_min_temp`` /
raw timestamps across ALL raw reports in the bucket, never a single
"closest to HH:00" snapshot. The rationale lives in wu_hourly_client's
docstring — picking a snap-point erases intra-hour SPECI peaks and
poisons both Platt calibration and Day-0 stop-loss monitoring.

METAR's native unit is °C; all three current consumers
(cities.json settlement_unit=='C' for Istanbul/Moscow/Tel Aviv) match
natively with no conversion. An F conversion path exists for future
use.

Public API
----------
- ``fetch_ogimet_hourly(station, start_date, end_date, *, city_name,
  timezone_name, source_tag, unit='C') -> OgimetHourlyFetchResult``.
  Returns per-hour bucket extrema. Accepts the tier_resolver expected
  ``source_tag`` so the backfill driver doesn't recompute it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from src.data.wu_hourly_client import HourlyObservation


# Force IPv4-only HTTP transport. Ogimet's IPv6 route intermittently
# stalls in SYN_SENT from some home-ISP IPv6 transports, causing
# minute-scale hangs even though the IPv4 path is fast and reliable.
# Phase 0 pilot 2026-04-22 hit a sustained IPv6 hang mid-Moscow backfill;
# binding outgoing sockets to the IPv4 wildcard forces httpx to pick A
# records over AAAA during DNS resolution.
_OGIMET_TRANSPORT = httpx.HTTPTransport(local_address="0.0.0.0")

logger = logging.getLogger(__name__)


OGIMET_METAR_URL = "https://www.ogimet.com/cgi-bin/getmetar"
OGIMET_HEADERS = {
    "User-Agent": "zeus-obs-v2-backfill/1.0 (research; contact via repo)",
}

#: Max window per HTTP request. Matches the existing daily backfill
#: script's behavior; Ogimet throttles if windows exceed ~30 days.
OGIMET_CHUNK_DAYS = 30

#: Ogimet documents a rate limit of "one query per remote IP every 20s"
#: (HTTP 501 response: "your quota limit for slow queries rate").
#: We sleep 21s between requests to stay safely under the threshold.
OGIMET_MIN_INTERVAL_SECONDS = 21.0

# Module-level last-request timestamp so the interval is enforced across
# multiple fetch_ogimet_hourly calls within one driver run (e.g. one
# city's multiple 30-day chunks, or multiple Ogimet cities in sequence).
_last_ogimet_request_at: float = 0.0

# METAR temp/dewpoint group regex. Copied from
# scripts/backfill_ogimet_metar.py::_METAR_TEMP_RE so a single-file change
# to one parser doesn't silently diverge the other; the A7 antibody test
# pins source_tag consistency separately.
_METAR_TEMP_RE = re.compile(r"\s(M?\d{1,2})/(M?\d{1,2})\s")


@dataclass(frozen=True)
class OgimetHourlyFetchResult:
    """Structured result of one ``fetch_ogimet_hourly`` call."""

    observations: list[HourlyObservation] = field(default_factory=list)
    raw_metar_count: int = 0
    failure_reason: Optional[str] = None
    retryable: bool = False
    error: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.failure_reason is not None


# ----------------------------------------------------------------------
# Parse helpers
# ----------------------------------------------------------------------


def _parse_metar_temp_c(metar_body: str) -> Optional[float]:
    """Extract temperature in °C from a raw METAR body, or None if absent."""
    match = _METAR_TEMP_RE.search(" " + metar_body + " ")
    if not match:
        return None
    raw = match.group(1)
    negative = raw.startswith("M")
    try:
        value = int(raw[1:] if negative else raw)
    except ValueError:
        return None
    return float(-value if negative else value)


def _parse_metar_csv_line(line: str) -> Optional[tuple[datetime, float]]:
    """Parse one Ogimet CSV line into ``(utc_dt, temp_c)`` or ``None``.

    Format: ``ICAO,YYYY,MM,DD,HH,MI,<METAR body>`` where the METAR body
    may contain commas (hence the ``split(",", 6)`` splitlimit).
    """
    parts = line.split(",", 6)
    if len(parts) < 7:
        return None
    try:
        year, month, day, hour, minute = map(int, parts[1:6])
        obs_utc = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None
    temp = _parse_metar_temp_c(parts[6])
    if temp is None:
        return None
    return obs_utc, temp


# ----------------------------------------------------------------------
# Fetch
# ----------------------------------------------------------------------


def fetch_ogimet_hourly(
    station: str,
    start_date: date,
    end_date: date,
    *,
    city_name: str,
    timezone_name: str,
    source_tag: str,
    unit: str = "C",
    timeout_seconds: float = 45.0,
) -> OgimetHourlyFetchResult:
    """Fetch hourly METAR observations for *station* over a date range.

    Parameters
    ----------
    station:
        ICAO code (e.g. 'UUWW').
    start_date, end_date:
        Inclusive local-date range.
    city_name:
        cities.json key; stamped on each ``HourlyObservation.city``.
    timezone_name:
        IANA zone used for local-date bucketing and DST fields.
    source_tag:
        The ``source`` column value to stamp, typically obtained from
        ``tier_resolver.expected_source_for_city``. Passed in rather
        than computed here so the client stays city-agnostic.
    unit:
        'C' (default; matches METAR native) or 'F' (conversion applied).
        For Phase 0 all Ogimet cities settle in 'C', so the default path
        is lossless.
    timeout_seconds:
        Per-request HTTP timeout. Ogimet can be slow during EU peak hours.

    Returns
    -------
    OgimetHourlyFetchResult
        ``observations`` is the list of snap-to-hour rows across the
        entire range (multiple chunks stitched).

    Notes
    -----
    The date range is internally chunked into ``OGIMET_CHUNK_DAYS``
    windows. On a chunk-level failure (HTTP 5xx, timeout), the failure
    is returned with ``observations`` containing whatever partial rows
    had already been parsed. Caller decides whether to retry the
    missing chunk.
    """
    if unit not in ("F", "C"):
        raise ValueError(f"unit must be 'F' or 'C', got {unit!r}")

    all_rows: list[tuple[datetime, float]] = []
    raw_count = 0
    current = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_utc = datetime.combine(end_date, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc)

    while current <= end_utc:
        chunk_end = min(current + timedelta(days=OGIMET_CHUNK_DAYS), end_utc)
        result = _fetch_one_chunk(
            station=station,
            begin=current,
            end=chunk_end,
            timeout_seconds=timeout_seconds,
        )
        if result.failed:
            return OgimetHourlyFetchResult(
                observations=list(
                    _aggregate(
                        all_rows,
                        station=station,
                        unit_out=unit,
                        timezone_name=timezone_name,
                        city_name=city_name,
                        source_tag=source_tag,
                        start_date=start_date,
                        end_date=end_date,
                    )
                ),
                raw_metar_count=raw_count,
                failure_reason=result.failure_reason,
                retryable=result.retryable,
                error=result.error,
            )
        all_rows.extend(result.observations)  # list of (utc_dt, temp_c)
        raw_count += result.raw_metar_count
        current = chunk_end + timedelta(seconds=1)

    observations = list(
        _aggregate(
            all_rows,
            station=station,
            unit_out=unit,
            timezone_name=timezone_name,
            city_name=city_name,
            source_tag=source_tag,
            start_date=start_date,
            end_date=end_date,
        )
    )
    return OgimetHourlyFetchResult(
        observations=observations,
        raw_metar_count=raw_count,
    )


@dataclass(frozen=True)
class _ChunkResult:
    observations: list[tuple[datetime, float]] = field(default_factory=list)
    raw_metar_count: int = 0
    failure_reason: Optional[str] = None
    retryable: bool = False
    error: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.failure_reason is not None


def _fetch_one_chunk(
    station: str, begin: datetime, end: datetime, timeout_seconds: float
) -> _ChunkResult:
    global _last_ogimet_request_at
    # Enforce Ogimet's documented 20s rate limit per remote IP.
    import time as _time

    elapsed = _time.monotonic() - _last_ogimet_request_at
    if elapsed < OGIMET_MIN_INTERVAL_SECONDS:
        _time.sleep(OGIMET_MIN_INTERVAL_SECONDS - elapsed)
    params = {
        "icao": station,
        "begin": begin.strftime("%Y%m%d%H%M"),
        "end": end.strftime("%Y%m%d%H%M"),
    }
    try:
        # Use a short-lived Client bound to IPv4 so flaky home-ISP IPv6
        # routes can't stall us in SYN_SENT.
        with httpx.Client(transport=_OGIMET_TRANSPORT, timeout=timeout_seconds) as client:
            resp = client.get(
                OGIMET_METAR_URL,
                params=params,
                headers=OGIMET_HEADERS,
            )
        _last_ogimet_request_at = _time.monotonic()
    except (httpx.HTTPError, httpx.RequestError) as exc:
        logger.warning(
            "Ogimet fetch raised %s for %s %s..%s: %s",
            type(exc).__name__,
            station,
            begin,
            end,
            exc,
        )
        return _ChunkResult(
            failure_reason="NETWORK_ERROR",
            retryable=True,
            error=f"{type(exc).__name__}: {exc}",
        )

    if resp.status_code == 429:
        return _ChunkResult(
            failure_reason="HTTP_429", retryable=True, error="HTTP 429"
        )
    if 500 <= resp.status_code <= 599:
        return _ChunkResult(
            failure_reason="HTTP_5XX",
            retryable=True,
            error=f"HTTP {resp.status_code}",
        )
    if resp.status_code != 200:
        return _ChunkResult(
            failure_reason="NETWORK_ERROR",
            retryable=True,
            error=f"HTTP {resp.status_code}",
        )

    parsed: list[tuple[datetime, float]] = []
    raw = 0
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        raw += 1
        row = _parse_metar_csv_line(line)
        if row is not None:
            parsed.append(row)
    return _ChunkResult(observations=parsed, raw_metar_count=raw)


# ----------------------------------------------------------------------
# Extremum-preserving hourly aggregation (shared semantics with WU client)
# ----------------------------------------------------------------------


def _aggregate(
    rows: list[tuple[datetime, float]],
    *,
    station: str,
    unit_out: str,
    timezone_name: str,
    city_name: str,
    source_tag: str,
    start_date: date,
    end_date: date,
):
    """Generator: yield one ``HourlyObservation`` per UTC hour bucket.

    Each yielded row carries the bucket's maximum and minimum
    temperature (with their raw METAR timestamps), preserving intra-hour
    SPECI extremes that the old snap-to-HH:00 logic would have erased.
    See wu_hourly_client module docstring for why this matters.

    Temperature-unit conversion (C -> F) applies AFTER aggregation, so
    rounding behavior is identical regardless of unit.
    """
    tz = ZoneInfo(timezone_name)

    # Bucket: hour_floor -> list of (temp_c, raw_utc_dt)
    buckets: dict[datetime, list[tuple[float, datetime]]] = {}
    for utc_dt, temp_c in rows:
        hour_floor = utc_dt.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour_floor, []).append((temp_c, utc_dt))

    def _convert(temp_c: float) -> float:
        if unit_out == "F":
            return temp_c * 9.0 / 5.0 + 32.0
        return temp_c

    for hour_floor in sorted(buckets):
        obs_list = buckets[hour_floor]
        local_dt = hour_floor.astimezone(tz)
        local_date = local_dt.date()
        if local_date < start_date or local_date > end_date:
            continue

        max_temp, max_dt = obs_list[0]
        min_temp, min_dt = obs_list[0]
        for temp_v, dt_v in obs_list[1:]:
            if temp_v > max_temp or (temp_v == max_temp and dt_v < max_dt):
                max_temp, max_dt = temp_v, dt_v
            if temp_v < min_temp or (temp_v == min_temp and dt_v < min_dt):
                min_temp, min_dt = temp_v, dt_v

        utc_offset = local_dt.utcoffset()
        dst_offset = local_dt.dst()
        dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
        is_missing = _detect_missing_local_hour(hour_floor, tz)
        is_ambiguous = bool(getattr(local_dt, "fold", 0))

        yield HourlyObservation(
            city=city_name,
            target_date=local_date.isoformat(),
            local_hour=float(local_dt.hour),
            local_timestamp=local_dt.isoformat(),
            utc_timestamp=hour_floor.isoformat(),
            utc_offset_minutes=int(utc_offset.total_seconds() / 60)
            if utc_offset
            else 0,
            dst_active=1 if dst_active else 0,
            is_ambiguous_local_hour=1 if is_ambiguous else 0,
            is_missing_local_hour=1 if is_missing else 0,
            time_basis="utc_hour_bucket_extremum",
            hour_max_temp=_convert(max_temp),
            hour_min_temp=_convert(min_temp),
            hour_max_raw_ts=max_dt.isoformat(),
            hour_min_raw_ts=min_dt.isoformat(),
            temp_unit=unit_out,
            station_id=station,
            observation_count=len(obs_list),
        )


def _detect_missing_local_hour(utc_dt: datetime, tz: ZoneInfo) -> bool:
    """Round-trip test: local at this UTC then back to UTC — gap if mismatched."""
    local_dt = utc_dt.astimezone(tz)
    roundtrip_utc = local_dt.replace(tzinfo=tz).astimezone(timezone.utc)
    return abs((roundtrip_utc - utc_dt).total_seconds()) >= 3600
