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

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_MAX_AGE_HOURS_DEFAULT = 6.0

# City config is loaded once at module import (read-only).
_CITY_CONFIG: dict[str, dict[str, Any]] = {}


def _load_city_config() -> None:
    """Populate _CITY_CONFIG from config/cities.json (repo root relative)."""
    global _CITY_CONFIG
    if _CITY_CONFIG:
        return
    # Walk up from this file to the repo root (src/data/../../)
    repo_root = pathlib.Path(__file__).parent.parent.parent
    path = repo_root / "config" / "cities.json"
    try:
        raw = json.loads(path.read_text())
        cities = raw.get("cities", [])
        for c in cities:
            name = c.get("name", "")
            if name:
                _CITY_CONFIG[name.lower()] = c
                # Also index by known aliases
                for alias in c.get("aliases", []):
                    _CITY_CONFIG[alias.lower()] = c
    except Exception as exc:  # noqa: BLE001
        logger.warning("mainstream_forecast_source: failed to load city config: %s", exc)


def _city_entry(city: str) -> dict[str, Any] | None:
    _load_city_config()
    return _CITY_CONFIG.get(city.lower())


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
