"""Real-time observation client for Day0 signal.

Spec §1.3 priority:
  Priority 1: WU API (settlement authority)
  Priority 2: IEM ASOS real-time (US cities)
  Priority 3: Open-Meteo hourly (all cities, free fallback)

Contract:
  high_so_far MUST mean the target city's local target-date maximum observed so far,
  not a rolling 24-hour maximum.
"""

import logging
from datetime import date, datetime, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import httpx

from src.config import City
from src.data.openmeteo_quota import quota_tracker

logger = logging.getLogger(__name__)

WU_API_KEY = "6532d6454b8aa370768e63d6ba5a832e"
WU_OBS_URL = "https://api.weather.com/v1/geocode/{lat}/{lon}/observations/timeseries.json"
IEM_BASE = "https://mesonet.agron.iastate.edu/json"


def _coerce_reference_time(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _coerce_target_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _resolve_observation_context(
    city: City,
    target_date: date | str | None = None,
    reference_time: datetime | str | None = None,
) -> tuple[date, datetime, datetime, ZoneInfo]:
    reference_utc = _coerce_reference_time(reference_time)
    tz = ZoneInfo(city.timezone)
    reference_local = reference_utc.astimezone(tz)
    target_day = _coerce_target_date(target_date) if target_date is not None else reference_local.date()
    return target_day, reference_utc, reference_local, tz


def _select_local_day_samples(
    samples: Iterable[tuple[float, datetime, object]],
    target_day: date,
    reference_local: datetime,
) -> list[tuple[float, datetime, object]]:
    selected = [
        (float(temp), dt_local, raw_time)
        for temp, dt_local, raw_time in samples
        if dt_local.date() == target_day and dt_local <= reference_local
    ]
    selected.sort(key=lambda row: row[1])
    return selected


def _parse_wu_valid_time(raw_value, tz: ZoneInfo) -> datetime | None:
    try:
        if isinstance(raw_value, (int, float)):
            return datetime.fromtimestamp(float(raw_value), tz=timezone.utc).astimezone(tz)
        raw = str(raw_value).strip()
        if raw.isdigit():
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).astimezone(tz)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(tz)
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _parse_local_timestamp(raw_value, tz: ZoneInfo) -> datetime | None:
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, datetime):
        return raw_value.astimezone(tz) if raw_value.tzinfo is not None else raw_value.replace(tzinfo=tz)

    raw = str(raw_value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.astimezone(tz) if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def get_current_observation(
    city: City,
    target_date: date | str | None = None,
    reference_time: datetime | str | None = None,
) -> Optional[dict]:
    """Get the current target-date observation for Day0 signal."""

    target_day, _, reference_local, tz = _resolve_observation_context(
        city, target_date=target_date, reference_time=reference_time
    )

    result = _fetch_wu_observation(city, target_day=target_day, reference_local=reference_local, tz=tz)
    if result is not None:
        return result

    if city.wu_station and city.settlement_unit == "F":
        result = _fetch_iem_asos(city, target_day=target_day, reference_local=reference_local, tz=tz)
        if result is not None:
            return result

    result = _fetch_openmeteo_hourly(city, target_day=target_day, reference_local=reference_local, tz=tz)
    if result is not None:
        return result

    from src.contracts.exceptions import ObservationUnavailableError

    logger.error(
        "No observation source available for %s on local target_date=%s up to %s",
        city.name,
        target_day,
        reference_local.isoformat(),
    )
    raise ObservationUnavailableError(f"All observation providers failed for {city.name}/{target_day.isoformat()}")


def _fetch_wu_observation(
    city: City,
    *,
    target_day: date,
    reference_local: datetime,
    tz: ZoneInfo,
) -> Optional[dict]:
    try:
        url = WU_OBS_URL.format(lat=city.lat, lon=city.lon)
        unit = "e" if city.settlement_unit == "F" else "m"

        resp = httpx.get(
            url,
            params={
                "apiKey": WU_API_KEY,
                "units": unit,
                "hours": 23,  # WU timeseries max is 23
            },
            timeout=15.0,
        )

        if resp.status_code != 200:
            return None

        data = resp.json()
        observations = data["observations"]
        if not observations:
            return None

        samples: list[tuple[float, datetime, object]] = []
        for obs in observations:
            temp = obs.get("temp")
            raw_time = obs.get("valid_time_gmt")
            if temp is None or raw_time is None:
                continue
            dt_local = _parse_wu_valid_time(raw_time, tz)
            if dt_local is None:
                continue
            samples.append((float(temp), dt_local, raw_time))

        selected = _select_local_day_samples(samples, target_day, reference_local)
        if not selected:
            return None

        current_temp, _, raw_time = selected[-1]
        high_so_far = max(temp for temp, _, _ in selected)
        return {
            "high_so_far": float(high_so_far),
            "current_temp": float(current_temp),
            "source": "wu_api",
            "observation_time": raw_time,
            "unit": city.settlement_unit,
        }

    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.debug("WU observation fetch failed for %s: %s", city.name, e)
        return None


def _fetch_iem_asos(
    city: City,
    *,
    target_day: date,
    reference_local: datetime,
    tz: ZoneInfo,
) -> Optional[dict]:
    station = city.wu_station
    if not station:
        return None

    try:
        url = f"{IEM_BASE}/current.py"
        resp = httpx.get(url, params={"station": station, "network": "ASOS"}, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()

        if not data or "last_ob" not in data:
            return None

        ob = data["last_ob"]
        temp_f = ob["tmpf"]
        if temp_f is None:
            return None

        local_valid = ob.get("local_valid")
        observed_local = _parse_local_timestamp(local_valid, tz)
        if observed_local is None:
            return None
        if observed_local.date() != target_day or observed_local > reference_local:
            return None
        if target_day != reference_local.date():
            logger.debug(
                "Skipping IEM ASOS for %s target_day=%s because current endpoint only supports the current local day",
                city.name,
                target_day,
            )
            return None

        offset = _get_asos_wu_offset(city, target_date=target_day)

        current_temp = float(temp_f) + offset
        high_so_far = float(ob["max_tmpf"]) + offset if ob.get("max_tmpf") is not None else current_temp

        return {
            "high_so_far": high_so_far,
            "current_temp": current_temp,
            "source": "iem_asos",
            "observation_time": local_valid,
            "unit": "F",
        }

    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("IEM ASOS fetch failed for %s: %s", city.name, e)
        return None


def _fetch_openmeteo_hourly(
    city: City,
    *,
    target_day: date,
    reference_local: datetime,
    tz: ZoneInfo,
) -> Optional[dict]:
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
                "past_hours": 36,
                "forecast_hours": 0,
                "timezone": city.timezone,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        quota_tracker.record_call("observation")
        data = resp.json()

        hourly = data["hourly"]
        temps = hourly["temperature_2m"]
        times = hourly["time"]

        if not temps:
            return None

        samples: list[tuple[float, datetime, object]] = []
        for temp, raw_time in zip(temps, times):
            if temp is None:
                continue
            dt_local = _parse_local_timestamp(raw_time, tz)
            if dt_local is None:
                continue
            samples.append((float(temp), dt_local, raw_time))

        selected = _select_local_day_samples(samples, target_day, reference_local)
        if not selected:
            return None

        current_temp, _, raw_time = selected[-1]
        high_so_far = max(temp for temp, _, _ in selected)
        return {
            "high_so_far": float(high_so_far),
            "current_temp": float(current_temp),
            "source": "openmeteo_hourly",
            "observation_time": raw_time,
            "unit": city.settlement_unit,
        }

    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("Open-Meteo hourly fetch failed for %s: %s", city.name, e)
        return None


def _get_asos_wu_offset(city: City, target_date: date | str | None = None) -> float:
    try:
        from src.state.db import get_shared_connection as get_connection

        if target_date is None:
            raise ValueError("target_date must be explicit for ASOS→WU offset lookup")
        target_day = _coerce_target_date(target_date)
        month = target_day.month
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
                city.name,
                season,
                offset_val,
                row["std"],
                row["n_samples"],
            )
            return float(offset_val)

        from src.contracts.exceptions import MissingCalibrationError

        logger.warning(
            "No calibrated ASOS→WU offset for %s/%s (n=%s). Missing required calibration.",
            city.name,
            season,
            row["n_samples"] if row else 0,
        )
        raise MissingCalibrationError(f"No calibrated ASOS→WU offset found for {city.name}/{season}")

    except Exception as e:
        from src.contracts.exceptions import MissingCalibrationError

        if isinstance(e, MissingCalibrationError):
            raise
        logger.warning("Failed to load ASOS→WU offset for %s: %s", city.name, e)
        raise MissingCalibrationError(f"Offset load failed for {city.name}: {e}") from e
