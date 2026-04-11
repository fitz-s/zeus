"""Forecast error distribution substrate for future uncertainty correction."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ForecastErrorProfile:
    profile_id: str
    city: str
    season: str
    source: str
    lead_days: int
    n_samples: int
    bias: float
    mae: float
    mse: float
    error_variance: float
    error_stddev: float


def _profile_id(city: str, season: str, source: str, lead_days: int) -> str:
    return f"{city}|{season}|{source}|{lead_days}"


def build_forecast_error_profiles(conn: sqlite3.Connection) -> list[ForecastErrorProfile]:
    """Aggregate forecast_skill rows into source/lead/city error profiles."""
    rows = conn.execute(
        """
        SELECT
            city,
            season,
            source,
            lead_days,
            COUNT(*) AS n_samples,
            AVG(error) AS bias,
            AVG(ABS(error)) AS mae,
            AVG(error * error) AS mse
        FROM forecast_skill
        GROUP BY city, season, source, lead_days
        ORDER BY city, season, source, lead_days
        """
    ).fetchall()

    profiles: list[ForecastErrorProfile] = []
    for row in rows:
        city = str(row["city"])
        season = str(row["season"])
        source = str(row["source"])
        lead_days = int(row["lead_days"])
        bias = float(row["bias"])
        mse = float(row["mse"])
        variance = max(0.0, mse - bias * bias)
        profiles.append(
            ForecastErrorProfile(
                profile_id=_profile_id(city, season, source, lead_days),
                city=city,
                season=season,
                source=source,
                lead_days=lead_days,
                n_samples=int(row["n_samples"] or 0),
                bias=bias,
                mae=float(row["mae"]),
                mse=mse,
                error_variance=variance,
                error_stddev=math.sqrt(variance),
            )
        )
    return profiles


def write_forecast_error_profiles(
    conn: sqlite3.Connection,
    profiles: list[ForecastErrorProfile],
    *,
    recorded_at: str,
) -> int:
    """Materialize error profiles into the additive profile table."""
    if not profiles:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO forecast_error_profile (
            profile_id,
            city,
            season,
            source,
            lead_days,
            n_samples,
            bias,
            mae,
            mse,
            error_variance,
            error_stddev,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                profile.profile_id,
                profile.city,
                profile.season,
                profile.source,
                profile.lead_days,
                profile.n_samples,
                profile.bias,
                profile.mae,
                profile.mse,
                profile.error_variance,
                profile.error_stddev,
                recorded_at,
            )
            for profile in profiles
        ],
    )
    return len(profiles)
