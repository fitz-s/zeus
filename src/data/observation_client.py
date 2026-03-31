"""Real-time observation client for Day0 signal.

Spec §1.3 priority:
  Priority 1: WU API (settlement authority)
  Priority 2: IEM ASOS real-time (US cities)
  Priority 3: Open-Meteo hourly (all cities, free fallback)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.config import City
from src.data.openmeteo_quota import quota_tracker

logger = logging.getLogger(__name__)

# WU API (settlement authority — spec §1.3 Priority 1)
# Public weather.com v3 API key (not a secret)
WU_API_KEY = "6532d6454b8aa370768e63d6ba5a832e"
WU_OBS_URL = "https://api.weather.com/v1/geocode/{lat}/{lon}/observations/timeseries.json"

# IEM ASOS API (free, no key)
IEM_BASE = "https://mesonet.agron.iastate.edu/json"


def get_current_observation(city: City) -> Optional[dict]:
    """Get current temperature observation for Day0 signal.

    Returns: {"high_so_far": float, "current_temp": float, "source": str,
              "observation_time": str, "unit": str}
    Returns None if no observation available.
    """
    # Priority 1: WU API (settlement authority)
    result = _fetch_wu_observation(city)
    if result is not None:
        return result

    # Priority 2: IEM ASOS for US cities
    if city.wu_station and city.settlement_unit == "F":
        result = _fetch_iem_asos(city)
        if result is not None:
            return result

    # Priority 3: Open-Meteo hourly (free, all cities)
    result = _fetch_openmeteo_hourly(city)
    if result is not None:
        return result

    logger.warning("No observation source available for %s", city.name)
    return None


def _fetch_wu_observation(city: City) -> Optional[dict]:
    """Fetch current observation from Weather Underground. Spec §1.3: Priority 1.

    WU is the settlement authority — this is the temperature Polymarket uses.
    """
    try:
        url = WU_OBS_URL.format(lat=city.lat, lon=city.lon)
        unit = "e" if city.settlement_unit == "F" else "m"  # 'e' = imperial, 'm' = metric

        resp = httpx.get(url, params={
            "apiKey": WU_API_KEY,
            "units": unit,
            "hours": 24,
        }, timeout=15.0)

        if resp.status_code != 200:
            return None

        data = resp.json()
        observations = data.get("observations", [])
        if not observations:
            return None

        # Get current and max temperature
        temps = [o.get("temp") for o in observations if o.get("temp") is not None]
        if not temps:
            return None

        return {
            "high_so_far": float(max(temps)),
            "current_temp": float(temps[-1]),
            "source": "wu_api",
            "observation_time": observations[-1].get("valid_time_gmt", ""),
            "unit": city.settlement_unit,
        }

    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.debug("WU observation fetch failed for %s: %s", city.name, e)
        return None


def _fetch_iem_asos(city: City) -> Optional[dict]:
    """Fetch latest ASOS observation from IEM. Spec §1.3: Priority 2.

    Uses wu_station (ICAO code like KLGA, KORD) which IEM also accepts.
    """
    station = city.wu_station
    if not station:
        return None

    try:
        url = f"{IEM_BASE}/current.py"
        resp = httpx.get(url, params={"station": station, "network": "ASOS"},
                         timeout=15.0)
        resp.raise_for_status()
        data = resp.json()

        if not data or "last_ob" not in data:
            return None

        ob = data["last_ob"]
        temp_f = ob.get("tmpf")
        if temp_f is None:
            return None

        # Apply calibrated ASOS→WU offset (per city×season)
        offset = _get_asos_wu_offset(city)

        current_temp = float(temp_f) + offset
        high_so_far = float(ob.get("max_tmpf", temp_f)) + offset

        return {
            "high_so_far": high_so_far,
            "current_temp": current_temp,
            "source": "iem_asos",
            "observation_time": ob.get("local_valid", ""),
            "unit": "F",
        }

    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("IEM ASOS fetch failed for %s: %s", city.name, e)
        return None


def _fetch_openmeteo_hourly(city: City) -> Optional[dict]:
    """Fetch today's hourly observations from Open-Meteo. Free, no API key.

    Uses Open-Meteo's forecast API with past_hours to get recent observations.
    Works for all cities (US and Europe). Returns °C for European, °F for US.
    """
    try:
        if not quota_tracker.can_call():
            logger.warning("Open-Meteo quota blocked observation fallback for %s", city.name)
            return None
        temp_unit = "fahrenheit" if city.settlement_unit == "F" else "celsius"
        resp = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": city.lat,
                "longitude": city.lon,
                "hourly": "temperature_2m",
                "temperature_unit": temp_unit,
                "past_hours": 24,
                "forecast_hours": 0,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        quota_tracker.record_call("observation")
        data = resp.json()

        hourly = data.get("hourly", {})
        temps = hourly.get("temperature_2m", [])
        times = hourly.get("time", [])

        if not temps:
            return None

        # Filter out None values
        valid = [(t, tm) for t, tm in zip(temps, times) if t is not None]
        if not valid:
            return None

        temp_values = [v[0] for v in valid]
        last_time = valid[-1][1]

        return {
            "high_so_far": max(temp_values),
            "current_temp": temp_values[-1],
            "source": "openmeteo_hourly",
            "observation_time": last_time,
            "unit": city.settlement_unit,
        }

    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("Open-Meteo hourly fetch failed for %s: %s", city.name, e)
        return None


def _get_asos_wu_offset(city: City) -> float:
    """Get calibrated ASOS→WU offset for this city×current season.

    The asos_wu_offsets table stores per-city×season offsets computed from
    historical paired ASOS/WU observations. The offset = mean(WU - ASOS).
    Positive offset means ASOS reads lower than WU → add to ASOS readings.

    Returns offset in °F (all ASOS stations report Fahrenheit).
    Falls back to 0.0 if no calibrated offset exists.
    """
    from datetime import date

    try:
        from src.state.db import get_connection

        today = date.today().isoformat()
        month = int(today.split("-")[1])
        if month in (12, 1, 2):
            season = "DJF"
        elif month in (3, 4, 5):
            season = "MAM"
        elif month in (6, 7, 8):
            season = "JJA"
        else:
            season = "SON"

        conn = get_connection()
        row = conn.execute(
            "SELECT offset, std, n_samples FROM asos_wu_offsets "
            "WHERE city = ? AND season = ?",
            (city.name, season),
        ).fetchone()
        conn.close()

        if row and row["n_samples"] >= 10:
            offset_val = row["offset"]
            logger.info(
                "ASOS→WU offset for %s/%s: %+.2f°F (σ=%.2f, n=%d)",
                city.name, season, offset_val, row["std"], row["n_samples"],
            )
            return float(offset_val)

        logger.warning(
            "No calibrated ASOS→WU offset for %s/%s (n=%s). Using 0.0.",
            city.name, season, row["n_samples"] if row else 0,
        )
        return 0.0

    except Exception as e:
        logger.warning("Failed to load ASOS→WU offset for %s: %s", city.name, e)
        return 0.0
