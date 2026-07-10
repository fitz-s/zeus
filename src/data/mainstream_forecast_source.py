# Created: 2026-06-03
# Last reused/audited: 2026-06-04 (metric-crossing fix: metric-matched daily field)
# Authority basis: Task #135 mainstream-forecast direction-agreement gate;
#   open-meteo.com/v1/forecast (standard, non-ECMWF endpoint — independent
#   from our raw ECMWF ensemble). City coords from config/cities.json (airport
#   station lat/lon, same as settlement station). Freshness: max_age_hours=6
#   (forecast updates ~every 6h). Fail-closed on missing/stale.
"""Mainstream forecast point for the direction-agreement gate (#135).

Fetches `temperature_2m_max` for a future date from Open-Meteo's standard
/v1/forecast endpoint (the GFS/ECMWF blend best-match consensus — independent
from our raw ECMWF open-data ensemble ingested via `forecasts_append`).

Authority design:
  source = "open_meteo_standard_forecast"
  authority_tier = "mainstream"   (arm-gate reference, not trading signal)

Provenance fields on every returned snapshot:
  fetched_at_utc  — ISO timestamp of the HTTP call
  source          — "open_meteo_standard_forecast"
  authority_tier  — "mainstream"
  latitude        — used for the API call
  longitude       — used for the API call

Fail-closed contract:
  fetch_mainstream_point() returns None (not raises) when:
    - city is unknown in config/cities.json
    - the target_date is not in the API response window
    - the HTTP call fails after retries
    - the returned value is missing or non-numeric
    - the result is older than max_age_hours (caller supplies a cache dict)

This module has NO side effects — it does not write to any DB. The caller
(event_reactor_adapter.py) may cache snapshots in a dict keyed by
(city, target_date) and pass a fetched_at to the freshness check.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_MAX_AGE_HOURS_DEFAULT = 6.0

# ----------------------------------------------------------------------------- #
# Process-global warm cache (STEP 7 / E2 of the consolidated timeliness fix).
#
# The mainstream point is an Open-Meteo HTTP fetch whose client applies
# Retry-After ``time.sleep`` on 429s. The reactor's proof path runs UNDER the
# world_write_mutex, so a synchronous fetch there serialized every world write
# behind a slow/blocked network call. We split the concern:
#   - ``warm_mainstream_point`` performs the fetch and stores into this global
#     cache; it is driven by a dedicated scheduler job OFF the mutex path.
#   - ``read_mainstream_point_cached`` reads this cache ONLY and returns None on
#     miss (fail-closed-to-None) — it NEVER touches the network. The proof path
#     calls this, so the mutex-held decision path can never block on a fetch.
# Thread-safe: the warm job and the reactor run on different scheduler threads.
# ----------------------------------------------------------------------------- #
_WARM_CACHE_LOCK = threading.Lock()
_WARM_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}

def _city_entry(city: str) -> dict[str, Any] | None:
    """Resolve city coordinates through the runtime-reloadable config map."""

    try:
        from src.config import cities_by_alias, runtime_cities_by_name  # noqa: PLC0415

        city_key = str(city).lower()
        city_obj = runtime_cities_by_name().get(city)
        if city_obj is None:
            city_obj = cities_by_alias.get(city_key)
        if city_obj is None:
            return None
        return {
            "lat": city_obj.lat,
            "lon": city_obj.lon,
            "unit": city_obj.settlement_unit,
            "timezone": city_obj.timezone,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("mainstream_forecast_source: failed to load city config: %s", exc)
        return None


def fetch_mainstream_point(
    city: str,
    target_date: str,
    *,
    metric: str,
    max_age_hours: float = _MAX_AGE_HOURS_DEFAULT,
    _cache: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the mainstream daily-extremum temperature point for (city, target_date, metric).

    METRIC IS REQUIRED AND LOAD-BEARING. HIGH and LOW are physically different
    quantities — the daily MAX and the daily MIN of the temperature series — and a
    market settles against exactly one of them. This function fetches the matching
    Open-Meteo daily field:
        metric == "high" -> temperature_2m_max
        metric == "low"  -> temperature_2m_min
    A HIGH-only default is FORBIDDEN: a LOW market graded against the daily max
    compares two different physical quantities (the original #135 metric-crossing
    defect — Paris LOW 13°C bin scored against the 20°C daily high). `metric` has no
    default so omission raises TypeError; an unknown value raises ValueError.

    Returns a dict with keys:
        point        — float, daily extremum (max for high / min for low) in native unit
        unit         — "C" or "F"
        metric       — "high" | "low" (echoed for provenance)
        source       — "open_meteo_standard_forecast"
        authority_tier — "mainstream"
        fetched_at_utc — ISO8601 UTC timestamp of the HTTP response
        latitude     — float
        longitude    — float
        target_date  — str, YYYY-MM-DD

    Returns None (fail-closed) on any error or staleness.

    _cache is an optional dict shared by the caller across candidates for the
    same city/date/metric cycle — avoids duplicate HTTP calls. Keys are
    (city, target_date, metric); metric is in the key so high/low never collide.
    """
    metric_norm = str(metric).lower()
    if metric_norm not in ("high", "low"):
        raise ValueError(
            f"fetch_mainstream_point: metric must be 'high' or 'low', got {metric!r}"
        )
    daily_field = "temperature_2m_max" if metric_norm == "high" else "temperature_2m_min"

    cache_key = (city.lower(), target_date, metric_norm)

    # Serve from cache if fresh.
    if _cache is not None and cache_key in _cache:
        cached = _cache[cache_key]
        if _is_fresh(cached.get("fetched_at_utc"), max_age_hours):
            return cached
        # Stale — remove and re-fetch.
        del _cache[cache_key]

    entry = _city_entry(city)
    if entry is None:
        logger.debug("mainstream_forecast_source: unknown city %r — fail-closed", city)
        return None

    lat = entry.get("lat")
    lon = entry.get("lon")
    unit_raw = (entry.get("unit") or "C").upper()

    if lat is None or lon is None:
        logger.debug(
            "mainstream_forecast_source: city %r missing lat/lon — fail-closed", city
        )
        return None

    # Open-Meteo temperature_unit: "celsius" or "fahrenheit".
    temp_unit_param = "fahrenheit" if unit_raw == "F" else "celsius"

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": daily_field,  # metric-matched: max for high, min for low
        "temperature_unit": temp_unit_param,
        "forecast_days": 16,
        "timezone": entry.get("timezone", "UTC"),
    }

    try:
        from src.data.openmeteo_client import fetch as _om_fetch  # noqa: PLC0415

        fetched_at = datetime.now(tz=timezone.utc).isoformat()
        resp = _om_fetch(
            _FORECAST_URL,
            params,
            endpoint_label=f"mainstream/{city}/{target_date}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mainstream_forecast_source: HTTP error for %r %s: %s",
            city,
            target_date,
            exc,
        )
        return None

    point = _extract_daily_value(resp, target_date, daily_field)
    if point is None:
        logger.debug(
            "mainstream_forecast_source: %r %s not in response window — fail-closed",
            city,
            target_date,
        )
        return None

    result: dict[str, Any] = {
        "point": float(point),
        "unit": unit_raw,
        "metric": metric_norm,
        "source": "open_meteo_standard_forecast",
        "authority_tier": "mainstream",
        "fetched_at_utc": fetched_at,
        "latitude": lat,
        "longitude": lon,
        "target_date": target_date,
    }

    if _cache is not None:
        _cache[cache_key] = result

    return result


def read_mainstream_point_cached(
    city: str,
    target_date: str,
    *,
    metric: str,
    max_age_hours: float = _MAX_AGE_HOURS_DEFAULT,
) -> dict[str, Any] | None:
    """Cache-ONLY read of the warm mainstream point (STEP 7 / E2).

    NEVER performs a network fetch. Returns the warm-cached snapshot for
    (city, target_date, metric) iff present AND fresh (< ``max_age_hours``);
    otherwise returns None (fail-closed-to-None). The reactor proof path calls
    this so the mutex-held decision path can never block on an Open-Meteo fetch.
    A miss leaves the existing ``mainstream_point=None → FAIL_CLOSED`` behavior
    intact — exactly as a stale/absent fetch would today.
    """
    metric_norm = str(metric).lower()
    if metric_norm not in ("high", "low"):
        raise ValueError(
            f"read_mainstream_point_cached: metric must be 'high' or 'low', got {metric!r}"
        )
    cache_key = (city.lower(), target_date, metric_norm)
    with _WARM_CACHE_LOCK:
        cached = _WARM_CACHE.get(cache_key)
        if cached is None:
            return None
        if not _is_fresh(cached.get("fetched_at_utc"), max_age_hours):
            # Stale: drop it and fail-closed. The warm job re-populates next tick.
            _WARM_CACHE.pop(cache_key, None)
            return None
        return dict(cached)


def warm_mainstream_point(
    city: str,
    target_date: str,
    *,
    metric: str,
) -> dict[str, Any] | None:
    """Fetch the mainstream point and store it in the process-global warm cache
    (STEP 7 / E2). Driven by the dedicated ``_edli_mainstream_warm_cycle``
    scheduler job, OFF the world_write_mutex decision path. Returns the fetched
    snapshot (or None on fail-closed); the side effect is the cache write that
    ``read_mainstream_point_cached`` later serves to the reactor.
    """
    cached = read_mainstream_point_cached(city, target_date, metric=metric)
    if cached is not None:
        return cached

    result = fetch_mainstream_point(city, target_date, metric=metric)
    if result is not None:
        cache_key = (city.lower(), target_date, str(metric).lower())
        with _WARM_CACHE_LOCK:
            _WARM_CACHE[cache_key] = result
    return result


def _extract_daily_value(
    resp: dict[str, Any], target_date: str, field: str
) -> float | None:
    """Pull the daily `field` value for target_date from an Open-Meteo /v1/forecast response.

    `field` is the metric-matched Open-Meteo variable name
    (temperature_2m_max for HIGH, temperature_2m_min for LOW).
    """
    try:
        daily = resp.get("daily", {})
        dates = daily.get("time", [])
        values = daily.get(field, [])
        for d, v in zip(dates, values):
            if str(d) == target_date and v is not None:
                return float(v)
    except Exception as exc:  # noqa: BLE001
        logger.debug("mainstream_forecast_source: _extract_daily_value error: %s", exc)
    return None


def _is_fresh(fetched_at_utc: str | None, max_age_hours: float) -> bool:
    """True if fetched_at_utc is within max_age_hours of now."""
    if not fetched_at_utc:
        return False
    try:
        fetched = datetime.fromisoformat(fetched_at_utc)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(tz=timezone.utc) - fetched).total_seconds() / 3600.0
        return age_h <= max_age_hours
    except Exception:  # noqa: BLE001
        return False
