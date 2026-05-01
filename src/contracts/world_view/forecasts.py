# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.2
"""World-view accessor: forecasts.

Provides get_latest_forecast(world_conn, city, target_date, lead_days) -> ForecastView | None.

Uses world_conn opened by the caller — no ATTACH, no module-level singleton.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class ForecastView:
    """Read-only view of a forecast record from world.forecasts.

    Callers must not write back through this object.
    """
    city: str
    target_date: str
    source: str
    forecast_basis_date: Optional[str]
    forecast_issue_time: Optional[str]
    lead_days: Optional[int]
    forecast_high: Optional[float]
    forecast_low: Optional[float]
    temp_unit: str
    authority_tier: Optional[str]
    data_source_version: Optional[str]


def get_latest_forecast(
    world_conn: sqlite3.Connection,
    city: str,
    target_date: date | str,
    lead_days: Optional[int] = None,
) -> Optional[ForecastView]:
    """Return the latest forecast for (city, target_date[, lead_days]) from world DB.

    If lead_days is provided, only returns forecasts with matching lead_days.
    Returns the most recently fetched row (ORDER BY retrieved_at DESC).

    world_conn must already be open — caller manages lifecycle.
    Returns None if no forecast row exists.
    """
    target_date_str = str(target_date)

    try:
        if lead_days is not None:
            row = world_conn.execute(
                """
                SELECT city, target_date, source, forecast_basis_date,
                       forecast_issue_time, lead_days, forecast_high, forecast_low,
                       temp_unit, authority_tier, data_source_version
                  FROM forecasts
                 WHERE city = ? AND target_date = ? AND lead_days = ?
                 ORDER BY retrieved_at DESC
                 LIMIT 1
                """,
                (city, target_date_str, lead_days),
            ).fetchone()
        else:
            row = world_conn.execute(
                """
                SELECT city, target_date, source, forecast_basis_date,
                       forecast_issue_time, lead_days, forecast_high, forecast_low,
                       temp_unit, authority_tier, data_source_version
                  FROM forecasts
                 WHERE city = ? AND target_date = ?
                 ORDER BY retrieved_at DESC
                 LIMIT 1
                """,
                (city, target_date_str),
            ).fetchone()
    except sqlite3.OperationalError:
        return None

    if row is None:
        return None

    def _col(name, idx):
        try:
            return row[name]
        except (IndexError, KeyError):
            try:
                return row[idx]
            except (IndexError, TypeError):
                return None

    return ForecastView(
        city=_col("city", 0),
        target_date=_col("target_date", 1),
        source=_col("source", 2),
        forecast_basis_date=_col("forecast_basis_date", 3),
        forecast_issue_time=_col("forecast_issue_time", 4),
        lead_days=_col("lead_days", 5),
        forecast_high=_col("forecast_high", 6),
        forecast_low=_col("forecast_low", 7),
        temp_unit=_col("temp_unit", 8) or "F",
        authority_tier=_col("authority_tier", 9),
        data_source_version=_col("data_source_version", 10),
    )
