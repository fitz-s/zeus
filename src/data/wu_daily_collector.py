"""WU Daily High Temperature Collector — Zeus-native.

Fetches the previous day's daily high temperature from Weather Underground
for all active cities and writes to zeus-shared.db.

Critical: WU timeseries API only keeps ~36 hours of data.
If we don't collect TODAY, yesterday's data is lost forever.

Schedule: daily at UTC 12:00 (all US cities have finalized daily high by then).
"""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from src.config import cities as CITIES, City
from src.state.db import get_shared_connection

logger = logging.getLogger(__name__)

WU_API_KEY = "6532d6454b8aa370768e63d6ba5a832e"
WU_HISTORY_URL = "https://api.weather.com/v1/geocode/{lat}/{lon}/observations/historical.json"
WU_TIMESERIES_URL = "https://api.weather.com/v1/geocode/{lat}/{lon}/observations/timeseries.json"


def _fetch_wu_daily_high(
    city: City,
    target_date: date,
) -> Optional[float]:
    """Fetch daily high temperature from WU for a specific date.

    Uses the timeseries endpoint with enough hours to cover the full day.
    Returns temperature in the city's settlement unit (F or C).
    """
    unit_code = "e" if city.settlement_unit == "F" else "m"

    try:
        # WU timeseries hours param: max 23
        # For daily collection run at UTC 12:00, yesterday's data should be within range
        resp = httpx.get(
            WU_TIMESERIES_URL.format(lat=city.lat, lon=city.lon),
            params={
                "apiKey": WU_API_KEY,
                "units": unit_code,
                "hours": 23,
            },
            timeout=15.0,
        )

        if resp.status_code != 200:
            logger.warning("WU API returned %d for %s", resp.status_code, city.name)
            return None

        data = resp.json()
        observations = data.get("observations", [])
        if not observations:
            return None

        # Filter to target_date observations only
        tz = ZoneInfo(city.timezone)
        day_temps = []
        for obs in observations:
            obs_time = obs.get("valid_time_gmt")
            temp = obs.get("temp")
            if obs_time is None or temp is None:
                continue

            # Convert epoch to local date
            obs_dt = datetime.fromtimestamp(float(obs_time), tz=timezone.utc).astimezone(tz)
            if obs_dt.date() == target_date:
                day_temps.append(float(temp))

        if not day_temps:
            return None

        return max(day_temps)

    except Exception as e:
        logger.warning("WU fetch failed for %s %s: %s", city.name, target_date, e)
        return None


def collect_daily_highs(
    target_date: date | None = None,
    cities: list[City] | None = None,
) -> dict:
    """Collect WU daily high temperatures for all active cities.

    Args:
        target_date: Date to collect for (default: yesterday)
        cities: List of cities (default: all configured cities)

    Returns:
        Summary dict with counts and results.
    """
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    if cities is None:
        cities = CITIES

    conn = get_shared_connection()
    results = {"collected": 0, "skipped": 0, "errors": 0, "details": []}

    for city in cities:
        # Check if we already have WU data for this date
        existing = conn.execute(
            "SELECT high_temp FROM observations WHERE city = ? AND target_date = ? AND source = 'wu_daily_zeus'",
            (city.name, target_date.isoformat()),
        ).fetchone()

        if existing is not None:
            results["skipped"] += 1
            continue

        high_temp = _fetch_wu_daily_high(city, target_date)

        if high_temp is not None:
            conn.execute("""
                INSERT OR IGNORE INTO observations
                (city, target_date, source, high_temp, unit, fetched_at)
                VALUES (?, ?, 'wu_daily_zeus', ?, ?, ?)
            """, (
                city.name,
                target_date.isoformat(),
                high_temp,
                city.settlement_unit,
                datetime.now(timezone.utc).isoformat(),
            ))

            # Also update settlement_value if we have a settlement without one
            conn.execute("""
                UPDATE settlements
                SET settlement_value = ?, settlement_source = 'wu_daily_zeus'
                WHERE city = ? AND target_date = ?
                AND (settlement_value IS NULL OR settlement_value = '')
            """, (high_temp, city.name, target_date.isoformat()))

            results["collected"] += 1
            results["details"].append({
                "city": city.name, "date": target_date.isoformat(),
                "high_temp": high_temp, "unit": city.settlement_unit,
            })
            logger.info("WU daily high: %s %s = %.1f°%s",
                        city.name, target_date, high_temp, city.settlement_unit)
        else:
            results["errors"] += 1
            logger.warning("WU daily high unavailable: %s %s", city.name, target_date)

    conn.commit()
    conn.close()

    logger.info("WU daily collection for %s: collected=%d, skipped=%d, errors=%d",
                target_date, results["collected"], results["skipped"], results["errors"])
    return results


def backfill_wu_daily(
    date_from: date,
    date_to: date,
    cities: list[City] | None = None,
) -> dict:
    """Backfill WU daily highs for a date range.

    Note: WU API only keeps ~36-72 hours of timeseries data,
    so this only works for very recent dates.
    """
    current = date_from
    total = {"collected": 0, "skipped": 0, "errors": 0}

    while current <= date_to:
        result = collect_daily_highs(target_date=current, cities=cities)
        for key in ("collected", "skipped", "errors"):
            total[key] += result[key]
        current += timedelta(days=1)

    return total


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    if "--backfill" in sys.argv:
        # Backfill last 2 days (WU API limit)
        today = date.today()
        result = backfill_wu_daily(today - timedelta(days=2), today - timedelta(days=1))
    else:
        result = collect_daily_highs()

    print(f"Collected: {result['collected']}, Skipped: {result['skipped']}, Errors: {result['errors']}")
