"""Open-Meteo Ensemble API client.

Fetches ECMWF IFS 51-member and GFS 31-member ensemble forecasts.
Returns raw hourly temperature arrays for signal generation.

API: https://ensemble-api.open-meteo.com/v1/ensemble
Free tier: 10,000 calls/day, no API key required.
"""

from datetime import datetime, timezone
from typing import Optional

import httpx
import numpy as np

from src.config import City, ensemble_member_count
from src.data.openmeteo_quota import quota_tracker


API_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Retry config per CLAUDE.md: retry 3× with 10s backoff
MAX_RETRIES = 3
RETRY_BACKOFF_S = 10.0
CACHE_TTL_SECONDS = 15 * 60
_ENSEMBLE_CACHE: dict[tuple[str, float, float, str, str], dict] = {}


def _cache_key(city: City, model: str) -> tuple[str, float, float, str, str]:
    return (
        city.name,
        float(city.lat),
        float(city.lon),
        city.settlement_unit,
        model,
    )


def _clone_result(result: dict) -> dict:
    cloned = dict(result)
    if "members_hourly" in cloned:
        cloned["members_hourly"] = np.array(cloned["members_hourly"], copy=True)
    if "times" in cloned:
        cloned["times"] = list(cloned["times"])
    return cloned


def fetch_ensemble(
    city: City,
    forecast_days: int = 8,
    model: str = "ecmwf_ifs025",
) -> Optional[dict]:  # Spec §2.1
    """Fetch ensemble forecast from Open-Meteo.

    Returns dict with:
        members_hourly: np.ndarray shape (n_members, hours) in city's settlement unit
        issue_time: datetime (UTC)
        fetch_time: datetime (UTC)
        model: str
        n_members: int

    Returns None if all retries fail.
    """
    temp_unit = "fahrenheit" if city.settlement_unit == "F" else "celsius"

    params = {
        "latitude": city.lat,
        "longitude": city.lon,
        "hourly": "temperature_2m",
        "models": model,
        "forecast_days": forecast_days,
        "temperature_unit": temp_unit,
    }

    fetch_time = datetime.now(timezone.utc)
    last_error = None
    cache_key = _cache_key(city, model)
    cached = _ENSEMBLE_CACHE.get(cache_key)
    if cached is not None:
        age_seconds = (fetch_time - cached["fetch_time"]).total_seconds()
        cached_days = int(cached.get("forecast_days", 0))
        if age_seconds <= CACHE_TTL_SECONDS and cached_days >= int(forecast_days):
            return _clone_result(cached)

    for attempt in range(MAX_RETRIES):
        try:
            if not quota_tracker.can_call():
                print("  WARN Open-Meteo quota blocked ensemble request")
                return None
            resp = httpx.get(API_URL, params=params, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            quota_tracker.record_call("ensemble")
            parsed = _parse_response(data, model, fetch_time)
            parsed["forecast_days"] = int(forecast_days)
            _ENSEMBLE_CACHE[cache_key] = parsed
            return _clone_result(parsed)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            last_error = e
            if isinstance(e, httpx.HTTPStatusError) and e.response is not None and e.response.status_code == 429:
                retry_after = e.response.headers.get("Retry-After")
                try:
                    retry_after_seconds = int(retry_after) if retry_after else None
                except ValueError:
                    retry_after_seconds = None
                quota_tracker.note_rate_limited(retry_after_seconds)
            if attempt < MAX_RETRIES - 1:
                import time
                time.sleep(RETRY_BACKOFF_S)

    # All retries exhausted — return None (caller decides: skip market this cycle)
    print(f"  WARN ensemble fetch failed after {MAX_RETRIES} retries: {last_error}")
    return None


def _parse_response(data: dict, model: str, fetch_time: datetime) -> dict:
    """Parse Open-Meteo ensemble response into structured dict.

    Open-Meteo returns ensemble members as separate keys:
    temperature_2m_member0, temperature_2m_member1, ..., temperature_2m_member50
    """
    hourly = data["hourly"]
    times = hourly["time"]

    # Collect all member arrays.
    # Open-Meteo format: temperature_2m (control run), temperature_2m_member01, ..., member50
    # The control run (temperature_2m without suffix) is member 0.
    members = []

    # Member 0 = control run (key: temperature_2m, no suffix)
    if "temperature_2m" in hourly:
        members.append(hourly["temperature_2m"])

    # Members 01-50 (zero-padded two digits)
    for i in range(1, 100):
        key = f"temperature_2m_member{i:02d}"
        if key not in hourly:
            break
        members.append(hourly[key])

    if not members:
        raise ValueError(f"No ensemble members found in response for model {model}")

    members_hourly = np.array(members, dtype=np.float64)  # (n_members, hours)
    n_members = members_hourly.shape[0]

    # Parse issue time from first timestamp
    issue_time = datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc)

    return {
        "members_hourly": members_hourly,
        "times": times,
        "issue_time": issue_time,
        "fetch_time": fetch_time,
        "model": model,
        "n_members": n_members,
    }


def validate_ensemble(result: dict, expected_members: int | None = None) -> bool:
    """Validate ensemble response. Per CLAUDE.md: reject if < expected members."""
    if expected_members is None:
        expected_members = ensemble_member_count()
    if result is None:
        return False
    n = result["n_members"]
    if n < expected_members:
        print(f"  WARN ensemble has {n} members, expected {expected_members}. REJECTED.")
        return False
    return True


def _clear_cache() -> None:
    _ENSEMBLE_CACHE.clear()
