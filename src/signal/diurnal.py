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

    Confidence = empirical P(daily max already set | city, month, hour).
    Resolution hierarchy:
      1. diurnal_peak_prob (monthly) — 30+ days per cell
      2. diurnal_curves.p_high_set (seasonal) — ~120 days per cell
      3. heuristic slope — cities without openmeteo_archive daily coverage
    """
    season = season_from_month(target_date.month)
    month = target_date.month

    try:
        from src.state.db import get_connection

        conn = get_connection()

        # Peak hour from seasonal avg_temp curve (unchanged)
        season_rows = conn.execute(
            "SELECT hour, avg_temp, std_temp, p_high_set FROM diurnal_curves "
            "WHERE city = ? AND season = ? ORDER BY hour",
            (city_name, season),
        ).fetchall()

        if not season_rows or len(season_rows) < 12:
            conn.close()
            return None, 0.0, "insufficient_diurnal_data_rows"

        peak_row = max(season_rows, key=lambda r: r["avg_temp"])
        peak_hour = int(peak_row["hour"])

        # 1. Monthly lookup
        monthly_row = conn.execute(
            "SELECT p_high_set FROM diurnal_peak_prob WHERE city = ? AND month = ? AND hour = ?",
            (city_name, month, current_local_hour),
        ).fetchone()
        conn.close()

        if monthly_row and monthly_row["p_high_set"] is not None:
            return peak_hour, float(monthly_row["p_high_set"]), "monthly_empirical"

        # 2. Seasonal fallback
        current_row = next((r for r in season_rows if r["hour"] == current_local_hour), None)
        if current_row and current_row["p_high_set"] is not None:
            return peak_hour, float(current_row["p_high_set"]), "seasonal_empirical"

        # 3. Heuristic slope (cities without openmeteo_archive coverage)
        peak_temp = peak_row["avg_temp"]
        if current_local_hour < peak_hour - 2:
            return peak_hour, 0.1, "well_before_peak"
        if current_local_hour < peak_hour:
            return peak_hour, 0.3, "approaching_peak"
        if current_local_hour == peak_hour:
            return peak_hour, 0.5, "at_peak_uncertain"
        if current_row is None:
            return peak_hour, 0.95, "late_night_wrap"
        hours_past_peak = current_local_hour - peak_hour
        temp_drop = peak_temp - current_row["avg_temp"]
        drop_zscore = temp_drop / peak_row["std_temp"] if peak_row["std_temp"] > 0 else 1.0
        time_confidence = min(0.95, 0.5 + hours_past_peak * 0.1)
        drop_confidence = min(0.95, 0.5 + drop_zscore * 0.15)
        return peak_hour, max(time_confidence, drop_confidence), "heuristic_slope"

    except Exception as e:
        logger.debug("Failed to fetch peak hour context for %s: %s", city_name, e)
        return None, 0.0, f"exception_or_no_data: {e}"


def post_peak_confidence(
    city_name: str,
    target_date: date,
    current_local_hour: int,
) -> float:
    """Empirical P(daily high already set | city, month, hour).

    Resolution hierarchy:
      1. diurnal_peak_prob (monthly) — most precise, 30+ days/cell
      2. diurnal_curves.p_high_set (seasonal) — ~120 days/cell
      3. heuristic slope — cities without openmeteo_archive coverage

    Returns:
        0.0 - 0.3: pre-peak (observation not yet dominant)
        0.3 - 0.7: near peak, uncertain
        0.7 - 1.0: post-peak, observation dominates ENS
    """
    season = season_from_month(target_date.month)
    month = target_date.month

    try:
        from src.state.db import get_connection

        conn = get_connection()

        # 1. Monthly lookup
        monthly_row = conn.execute(
            "SELECT p_high_set FROM diurnal_peak_prob WHERE city = ? AND month = ? AND hour = ?",
            (city_name, month, current_local_hour),
        ).fetchone()
        if monthly_row and monthly_row["p_high_set"] is not None:
            conn.close()
            return float(monthly_row["p_high_set"])

        # 2. Seasonal fallback
        season_rows = conn.execute(
            "SELECT hour, avg_temp, std_temp, p_high_set FROM diurnal_curves "
            "WHERE city = ? AND season = ? ORDER BY hour",
            (city_name, season),
        ).fetchall()
        conn.close()

        if not season_rows or len(season_rows) < 12:
            return 0.0

        current_row = next((r for r in season_rows if r["hour"] == current_local_hour), None)
        if current_row and current_row["p_high_set"] is not None:
            return float(current_row["p_high_set"])

        # 3. Heuristic slope
        peak_row = max(season_rows, key=lambda r: r["avg_temp"])
        peak_hour = peak_row["hour"]
        peak_temp = peak_row["avg_temp"]

        if current_local_hour < peak_hour - 2:
            return 0.1
        if current_local_hour < peak_hour:
            return 0.3
        if current_local_hour == peak_hour:
            return 0.5
        if current_row is None:
            return 0.95

        hours_past_peak = current_local_hour - peak_hour
        temp_drop = peak_temp - current_row["avg_temp"]
        drop_zscore = temp_drop / peak_row["std_temp"] if peak_row["std_temp"] > 0 else 1.0
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
