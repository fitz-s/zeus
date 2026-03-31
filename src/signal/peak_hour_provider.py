"""Peak Hour Provider (Single Source of Truth)

Replaces all hardcoded `historical_peak_hour=15.0` instances with 
strict DB-backed diurnal lookups, alongside post-peak confidence modeling.
"""

import logging
from datetime import date, datetime
from typing import Tuple, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def _season_from_month(month: int) -> str:
    if month in (12, 1, 2):
        return "DJF"
    elif month in (3, 4, 5):
        return "MAM"
    elif month in (6, 7, 8):
        return "JJA"
    return "SON"


def get_current_local_hour(timezone: str) -> int:
    """Get the current hour in a city's local timezone."""
    tz = ZoneInfo(timezone)
    return datetime.now(tz).hour


def get_peak_hour_context(
    city_name: str,
    target_date: date,
    current_local_hour: int,
) -> Tuple[Optional[int], float, Optional[str]]:
    """
    Single-source of truth for determining the peak hour of a city.
    
    Returns:
        peak_hour: The hour (0-23) the temperature peaks, or None if unknown.
        confidence: 0.0 to 1.0 confidence that the peak has *already passed*.
        fallback_reason: String explaining why confidence might be low (e.g., "no_data").
    """
    season = _season_from_month(target_date.month)

    try:
        from src.state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT hour, avg_temp, std_temp FROM diurnal_curves "
            "WHERE city = ? AND season = ? "
            "ORDER BY hour",
            (city_name, season),
        ).fetchall()
        conn.close()

        if not rows or len(rows) < 12:
            return None, 0.0, "insufficient_diurnal_data_rows"

        # Find peak hour and temp
        peak_row = max(rows, key=lambda r: r["avg_temp"])
        peak_hour = int(peak_row["hour"])
        peak_temp = peak_row["avg_temp"]

        # Confidence modeling based on current hour vs peak hour
        if current_local_hour < peak_hour - 2:
            return peak_hour, 0.1, "well_before_peak"

        if current_local_hour < peak_hour:
            return peak_hour, 0.3, "approaching_peak"

        if current_local_hour == peak_hour:
            return peak_hour, 0.5, "at_peak_uncertain"

        # Post-peak: we analyze the drop rate to gain confidence
        hours_past_peak = current_local_hour - peak_hour
        current_row = next(
            (r for r in rows if r["hour"] == current_local_hour), None
        )

        if current_row is None:
            return peak_hour, 0.95, "late_night_wrap"

        temp_drop = peak_temp - current_row["avg_temp"]
        if peak_row["std_temp"] > 0:
            drop_zscore = temp_drop / peak_row["std_temp"]
        else:
            drop_zscore = 1.0

        time_confidence = min(0.95, 0.5 + hours_past_peak * 0.1)
        drop_confidence = min(0.95, 0.5 + drop_zscore * 0.15)
        
        final_conf = max(time_confidence, drop_confidence)

        return peak_hour, final_conf, "data_derived"

    except Exception as e:
        logger.error("Peak hour provider failed for %s: %s", city_name, e)
        return None, 0.0, f"db_exception: {e}"
