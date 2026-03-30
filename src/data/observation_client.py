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

    # European cities: Meteostat. Spec §1.3 Priority 3.
    if city.settlement_unit == "C":
        result = _fetch_meteostat(city)
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


def _fetch_meteostat(city: City) -> Optional[dict]:
    """Fetch latest observation from Meteostat. Spec §1.3: Priority 3 (Europe)."""
    try:
        # Use the free JSON endpoint for latest station data
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        resp = httpx.get(
            f"https://meteostat.p.rapidapi.com/stations/hourly",
            params={
                "station": "03772",  # London Heathrow as default
                "start": date_str,
                "end": date_str,
            },
            headers={
                "X-RapidAPI-Key": "placeholder",  # TODO: resolve from keychain
            },
            timeout=15.0,
        )

        if resp.status_code != 200:
            return None

        data = resp.json()
        hourly = data.get("data", [])
        if not hourly:
            return None

        # Get max temp so far today
        temps = [h["temp"] for h in hourly if h.get("temp") is not None]
        if not temps:
            return None

        return {
            "high_so_far": max(temps),
            "current_temp": temps[-1],
            "source": "meteostat",
            "observation_time": hourly[-1].get("time", ""),
            "unit": "C",
        }

    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("Meteostat fetch failed for %s: %s", city.name, e)
        return None
