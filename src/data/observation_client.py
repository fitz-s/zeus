"""Real-time observation client for Day0 signal.

Spec §1.3 priority:
  Priority 1: WU API (if available)
  Priority 2: IEM ASOS real-time + calibrated offset
  Priority 3: Meteostat hourly (Europe)

ASOS→WU offset calibration data not migrated yet — using 0.0 offset with warning.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.config import City

logger = logging.getLogger(__name__)

# IEM ASOS API (free, no key)
IEM_BASE = "https://mesonet.agron.iastate.edu/json"

# Meteostat API (free tier with key, or use RapidAPI)
METEOSTAT_BASE = "https://meteostat.p.rapidapi.com"


def get_current_observation(city: City) -> Optional[dict]:
    """Get current temperature observation for Day0 signal.

    Returns: {"high_so_far": float, "current_temp": float, "source": str,
              "observation_time": str, "unit": str}
    Returns None if no observation available.
    """
    # IEM ASOS only for US cities (°F stations). Spec §1.3 Priority 2.
    if city.iem_station and city.settlement_unit == "F":
        result = _fetch_iem_asos(city)
        if result is not None:
            return result

    # All cities fallback: Open-Meteo hourly (free, no API key).
    result = _fetch_openmeteo_hourly(city)
    if result is not None:
        return result

    logger.warning("No observation source available for %s", city.name)
    return None


def _fetch_iem_asos(city: City) -> Optional[dict]:
    """Fetch latest ASOS observation from IEM. Spec §1.3: Priority 2."""
    station = city.iem_station
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

        # ASOS→WU offset: not calibrated yet, use 0.0
        # TODO(Phase B): Apply per-station offset from WU backfill data
        offset = 0.0
        if offset == 0.0:
            logger.warning(
                "ASOS→WU offset not calibrated for %s (%s). Using 0.0.",
                city.name, station,
            )

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
