# Created: 2026-04-21
# Last reused/audited: 2026-04-21
# Authority basis: plan v3 Phase 0 file #4 (.omc/plans/observation-instants-
#                  migration-iter3.md L86-93); step2_phase0_pilot_plan.md.
"""WU ICAO hourly-observation client for observation_instants_v2 backfill.

Wraps the Weather Underground private v1 endpoint
``api.weather.com/v1/location/{ICAO}:9:{CC}/observations/historical.json``
which returns sub-hourly (METAR cadence, ~30 min) observations. This
client snaps each requested UTC hour to the nearest METAR observation
within ±30 minutes, producing exactly one ``HourlyObservation`` per
UTC top-of-hour per day of the request window.

Design notes
------------
- Shares ``WU_API_KEY`` / ``_WU_PUBLIC_WEB_KEY`` with
  ``src.data.daily_obs_append`` so the 2026-04-21 Day-0 ghost-trade root
  fix (public-key fallback) automatically covers this client.
- The snap window is ±30 min around each UTC hour. If multiple METAR
  reports land in that window, the one closest to HH:00:00 wins. If no
  report lands in the window, that hour is skipped (not emitted, not
  NaN-filled) — gaps are legitimate and the backfill driver records
  them via row-count audits rather than synthetic fills.
- Local-time fields are computed via ``ZoneInfo``, preserving DST folds
  and missing-hour detection. Spring-forward hours where the local wall
  clock doesn't exist are flagged via ``is_missing_local_hour=1``; the
  hour is still emitted if UTC-aligned data is present.

Public API
----------
- ``fetch_wu_hourly(icao, cc, start_date, end_date, unit, timezone_name)
  -> WuHourlyFetchResult`` — one-call fetch over a date range. Handles
  HTTP 401/403/429/5xx with typed failure reasons; raises only on code
  bugs. Never silently swallows errors.
- ``HourlyObservation`` — structured per-hour row with all fields needed
  to construct an ``ObsV2Row`` via ``build_obs_v2_row_kwargs``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

# Reuse the exact WU_API_KEY resolution path used by the daily client so
# the public-key fallback stays in one place. Do not re-read the env var.
from src.data.daily_obs_append import (
    WU_API_KEY,
    WU_HEADERS,
    WU_ICAO_HISTORY_URL,
)

logger = logging.getLogger(__name__)


#: Canonical source tag for WU hourly rows. Must match
#: ``tier_resolver.EXPECTED_SOURCE_BY_CITY`` for WU cities.
WU_HOURLY_SOURCE = "wu_icao_history"


@dataclass(frozen=True)
class HourlyObservation:
    """One top-of-hour observation, ready for ``ObsV2Row`` construction.

    Fields map 1:1 to ``observation_instants_v2`` columns. The backfill
    driver is responsible for attaching ``authority``, ``data_version``,
    and ``provenance_json`` before handing the row to the writer.
    """

    city: str  # settlement-station city name (cities.json key)
    target_date: str  # 'YYYY-MM-DD' local date
    local_hour: float
    local_timestamp: str  # ISO with offset
    utc_timestamp: str  # ISO with +00:00
    utc_offset_minutes: int
    dst_active: int
    is_ambiguous_local_hour: int
    is_missing_local_hour: int
    time_basis: str  # 'utc_hour_aligned' for snap-to-hour
    temp_current: float
    temp_unit: str  # 'F' or 'C'
    station_id: str  # ICAO (e.g. 'KORD')
    observation_count: int  # raw METAR reports that contributed (1 after snap)
    raw_obs_ts: str  # the actual METAR valid_time ISO, for audit


@dataclass(frozen=True)
class WuHourlyFetchResult:
    """Structured result of one ``fetch_wu_hourly`` call."""

    observations: list[HourlyObservation] = field(default_factory=list)
    raw_observation_count: int = 0
    failure_reason: Optional[str] = None
    retryable: bool = False
    auth_failed: bool = False
    error: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.failure_reason is not None


# ----------------------------------------------------------------------
# Fetch
# ----------------------------------------------------------------------

# Snap window: ±30 minutes around each UTC top-of-hour.
_SNAP_WINDOW_SECONDS = 30 * 60


def fetch_wu_hourly(
    icao: str,
    cc: str,
    start_date: date,
    end_date: date,
    unit: str,
    timezone_name: str,
    *,
    city_name: str,
    timeout_seconds: float = 30.0,
) -> WuHourlyFetchResult:
    """Fetch hourly WU observations for *icao* from *start_date* to *end_date*.

    Parameters
    ----------
    icao:
        4-letter airport code (e.g. 'KORD', 'EGLC').
    cc:
        2-letter country code (e.g. 'US', 'GB').
    start_date, end_date:
        Inclusive local-date range.
    unit:
        'F' or 'C'. Maps to WU's 'e' / 'm' units parameter.
    timezone_name:
        IANA zone (e.g. 'America/Chicago'). Used for local-date bucketing
        and DST-aware local fields.
    city_name:
        cities.json key; stamped on each ``HourlyObservation.city``.
    timeout_seconds:
        Per-request HTTP timeout.

    Returns
    -------
    WuHourlyFetchResult
        ``observations`` is the list of snap-to-hour rows. ``failure_reason``
        is ``None`` on success (even if observations is empty — a legitimate
        upstream gap is not a failure).
    """
    if unit not in ("F", "C"):
        raise ValueError(f"unit must be 'F' or 'C', got {unit!r}")
    assert WU_API_KEY, (
        "WU_API_KEY resolved empty; _WU_PUBLIC_WEB_KEY fallback broken? "
        "Phase 0 requires the public-key fallback in daily_obs_append.py."
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
            timeout=timeout_seconds,
            headers=WU_HEADERS,
        )
    except (httpx.HTTPError, httpx.RequestError) as exc:
        logger.warning(
            "WU hourly fetch raised %s for %s:%s %s..%s: %s",
            type(exc).__name__,
            icao,
            cc,
            start_date,
            end_date,
            exc,
        )
        return WuHourlyFetchResult(
            failure_reason="NETWORK_ERROR",
            retryable=True,
            error=f"{type(exc).__name__}: {exc}",
        )

    if resp.status_code in (401, 403):
        return WuHourlyFetchResult(
            failure_reason="AUTH_ERROR",
            retryable=False,
            auth_failed=True,
            error=f"HTTP {resp.status_code}",
        )
    if resp.status_code == 429:
        return WuHourlyFetchResult(
            failure_reason="HTTP_429",
            retryable=True,
            error="HTTP 429 (rate-limited)",
        )
    if 500 <= resp.status_code <= 599:
        return WuHourlyFetchResult(
            failure_reason="HTTP_5XX",
            retryable=True,
            error=f"HTTP {resp.status_code}",
        )
    if resp.status_code != 200:
        return WuHourlyFetchResult(
            failure_reason="NETWORK_ERROR",
            retryable=True,
            error=f"HTTP {resp.status_code}",
        )

    try:
        body = resp.json()
    except ValueError as exc:
        return WuHourlyFetchResult(
            failure_reason="PARSE_ERROR",
            retryable=True,
            error=f"json parse failed: {exc}",
        )
    raw_observations = body.get("observations", []) or []
    snapped = _snap_to_hourly(
        raw_observations,
        icao=icao,
        unit=unit,
        timezone_name=timezone_name,
        city_name=city_name,
        start_date=start_date,
        end_date=end_date,
    )
    return WuHourlyFetchResult(
        observations=snapped,
        raw_observation_count=len(raw_observations),
    )


# ----------------------------------------------------------------------
# Snap-to-hour
# ----------------------------------------------------------------------


def _snap_to_hourly(
    raw_observations: list[dict],
    *,
    icao: str,
    unit: str,
    timezone_name: str,
    city_name: str,
    start_date: date,
    end_date: date,
) -> list[HourlyObservation]:
    """Snap METAR-cadence observations to one ``HourlyObservation`` per UTC hour.

    Algorithm:
    1. Parse each raw obs into (utc_dt, temp).
    2. Bucket by UTC hour (truncate to :00).
    3. Within each bucket, pick the obs closest to the bucket's HH:00.
    4. Emit one ``HourlyObservation`` per bucket for hours within the
       local-date range [start_date, end_date].
    """
    tz = ZoneInfo(timezone_name)

    # (utc_hour_floor, seconds_from_top_of_hour, obs_dict)
    buckets: dict[datetime, tuple[int, dict]] = {}
    for obs in raw_observations:
        temp = obs.get("temp")
        epoch = obs.get("valid_time_gmt")
        if temp is None or epoch is None:
            continue
        try:
            utc_dt = datetime.fromtimestamp(int(epoch), timezone.utc)
        except (ValueError, OSError, OverflowError):
            continue
        hour_floor = utc_dt.replace(minute=0, second=0, microsecond=0)
        delta_seconds = abs((utc_dt - hour_floor).total_seconds())
        # Handle the >30min case: snap to the next hour if closer
        if delta_seconds > 30 * 60:
            hour_floor = hour_floor + timedelta(hours=1)
            delta_seconds = abs((utc_dt - hour_floor).total_seconds())
        if delta_seconds > _SNAP_WINDOW_SECONDS:
            continue  # too far from any hour (shouldn't happen if math is right)
        existing = buckets.get(hour_floor)
        if existing is None or delta_seconds < existing[0]:
            buckets[hour_floor] = (int(delta_seconds), obs)

    out: list[HourlyObservation] = []
    for hour_floor in sorted(buckets):
        _, obs = buckets[hour_floor]
        local_dt = hour_floor.astimezone(tz)
        local_date = local_dt.date()
        if local_date < start_date or local_date > end_date:
            continue
        utc_offset = local_dt.utcoffset()
        dst_offset = local_dt.dst()
        dst_active = bool(dst_offset and dst_offset.total_seconds() > 0)
        # is_missing_local_hour: the wall clock at this moment doesn't
        # exist in the local tz (spring-forward). ZoneInfo handles this
        # via fold; a normalized compare is the cleanest detection.
        is_missing = _detect_missing_local_hour(hour_floor, tz)
        is_ambiguous = bool(getattr(local_dt, "fold", 0))

        temp = obs.get("temp")
        raw_ts = datetime.fromtimestamp(
            int(obs["valid_time_gmt"]), timezone.utc
        ).isoformat()
        out.append(
            HourlyObservation(
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
                time_basis="utc_hour_aligned",
                temp_current=float(temp),
                temp_unit=unit,
                station_id=icao,
                observation_count=1,
                raw_obs_ts=raw_ts,
            )
        )
    return out


def _detect_missing_local_hour(utc_dt: datetime, tz: ZoneInfo) -> bool:
    """True if the local wall clock for *utc_dt* lands inside a DST gap.

    Implementation: convert UTC → local, then convert local (with fold=0)
    back to UTC. If the round-trip is off by the DST savings amount, the
    local time isn't real.
    """
    local_dt = utc_dt.astimezone(tz)
    roundtrip_utc = local_dt.replace(tzinfo=tz).astimezone(timezone.utc)
    return abs((roundtrip_utc - utc_dt).total_seconds()) >= 3600
