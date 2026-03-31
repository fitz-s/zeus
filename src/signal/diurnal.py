"""Diurnal analysis utilities.

Provides data-driven peak hour and post-peak confidence from diurnal_curves table.
Replaces hardcoded `historical_peak_hour = 15.0` with per-city×season values.
"""

import logging
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def season_from_month(month: int) -> str:
    if month in (12, 1, 2):
        return "DJF"
    elif month in (3, 4, 5):
        return "MAM"
    elif month in (6, 7, 8):
        return "JJA"
    return "SON"


def get_peak_hour_context(
    city_name: str, target_date: date, current_local_hour: int
) -> tuple[Optional[int], float, str]:
    """Single source of truth for peak hour.
    Returns: (peak_hour, confidence, fallback_reason)
    """
    season = season_from_month(target_date.month)

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

        peak_row = max(rows, key=lambda r: r["avg_temp"])
        peak_hour = int(peak_row["hour"])
        peak_temp = peak_row["avg_temp"]

        if current_local_hour < peak_hour - 2:
            return peak_hour, 0.1, "well_before_peak"

        if current_local_hour < peak_hour:
            return peak_hour, 0.3, "approaching_peak"

        if current_local_hour == peak_hour:
            return peak_hour, 0.5, "at_peak_uncertain"

        # Post-peak cooling rate
        hours_past_peak = current_local_hour - peak_hour
        current_row = next((r for r in rows if r["hour"] == current_local_hour), None)

        if current_row is None:
            return peak_hour, 0.95, "late_night_wrap"

        temp_drop = peak_temp - current_row["avg_temp"]
        if peak_row["std_temp"] > 0:
            drop_zscore = temp_drop / peak_row["std_temp"]
        else:
            drop_zscore = 1.0

        time_confidence = min(0.95, 0.5 + hours_past_peak * 0.1)
        drop_confidence = min(0.95, 0.5 + drop_zscore * 0.15)

        return peak_hour, max(time_confidence, drop_confidence), "data_derived"

    except Exception as e:
        logger.debug("Failed to fetch peak hour context for %s: %s", city_name, e)
        return None, 0.0, f"exception_or_no_data: {e}"


def post_peak_confidence(
    city_name: str,
    target_date: date,
    current_local_hour: int,
) -> float:
    """Compute confidence (0.0-1.0) that the daily high has already been reached.

    Uses diurnal_curves to check:
    1. Has the peak hour passed? (main signal)
    2. How steep is the post-peak cooling curve? (sharper cooling = higher confidence)

    Returns:
        0.0 - 0.3: pre-peak or unknown
        0.3 - 0.7: near peak, uncertain
        0.7 - 1.0: clearly post-peak, high confidence observation dominates
    """
    season = season_from_month(target_date.month)

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
            return 0.0  # Not enough data

        # Find peak hour and temperature
        peak_row = max(rows, key=lambda r: r["avg_temp"])
        peak_hour = peak_row["hour"]
        peak_temp = peak_row["avg_temp"]

        if current_local_hour < peak_hour - 2:
            return 0.1  # Well before peak

        if current_local_hour < peak_hour:
            return 0.3  # Approaching peak

        if current_local_hour == peak_hour:
            return 0.5  # At peak — could still be rising

        # Post-peak: compute cooling rate
        hours_past_peak = current_local_hour - peak_hour
        current_row = next(
            (r for r in rows if r["hour"] == current_local_hour), None
        )

        if current_row is None:
            # Past 23:00 wrapping, definitely post-peak
            return 0.95

        temp_drop = peak_temp - current_row["avg_temp"]
        # Normalize: if temp has dropped > 2 std from peak, very confident
        if peak_row["std_temp"] > 0:
            drop_zscore = temp_drop / peak_row["std_temp"]
        else:
            drop_zscore = 1.0

        # Confidence ramps from 0.5 to 0.95 based on hours past peak
        # and how much temperature has dropped
        time_confidence = min(0.95, 0.5 + hours_past_peak * 0.1)
        drop_confidence = min(0.95, 0.5 + drop_zscore * 0.15)

        return max(time_confidence, drop_confidence)

    except Exception as e:
        logger.debug("Post-peak confidence failed for %s: %s", city_name, e)
        return 0.0


def get_current_local_hour(timezone: str) -> int:
    """Get the current hour in a city's local timezone."""
    tz = ZoneInfo(timezone)
    return datetime.now(tz).hour
