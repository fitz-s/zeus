"""Data-driven city correlation estimation for Zeus.

Two useful domains:
1) settlement anomaly correlation
2) forecast error correlation

This module estimates a correlation matrix and applies a simple shrinkage step so that
the result is stable enough for portfolio/risk usage.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


def load_settlement_anomalies(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return date x city anomaly matrix based on monthly city means."""
    df = pd.read_sql_query(
        """
        WITH base AS (
          SELECT
              city,
              target_date,
              CAST(substr(target_date, 6, 2) AS INTEGER) AS month_num,
              settlement_value
          FROM settlements
          WHERE settlement_value IS NOT NULL
        ),
        monthly_mean AS (
          SELECT city, month_num, AVG(settlement_value) AS month_mean
          FROM base
          GROUP BY city, month_num
        )
        SELECT
            b.city,
            b.target_date,
            b.settlement_value - m.month_mean AS anomaly
        FROM base b
        JOIN monthly_mean m
          ON m.city = b.city
         AND m.month_num = b.month_num
        """,
        conn,
    )
    return df.pivot(index="target_date", columns="city", values="anomaly")


def load_forecast_error_matrix(
    conn: sqlite3.Connection,
    *,
    source: str,
    lead_days: int,
) -> pd.DataFrame:
    """Return target_date x city matrix of forecast errors."""
    df = pd.read_sql_query(
        """
        SELECT city, target_date, error
        FROM forecast_skill
        WHERE source = ?
          AND lead_days = ?
        """,
        conn,
        params=(source, int(lead_days)),
    )
    return df.pivot(index="target_date", columns="city", values="error")


def shrink_correlation(
    matrix: pd.DataFrame,
    *,
    min_periods: int = 60,
    shrinkage_lambda: float = 0.20,
) -> pd.DataFrame:
    """Shrink empirical correlations toward identity."""
    corr = matrix.corr(min_periods=min_periods)
    values = corr.to_numpy(dtype=float)
    shrunk = (1.0 - shrinkage_lambda) * values + shrinkage_lambda * np.eye(values.shape[0])
    return pd.DataFrame(shrunk, index=corr.index, columns=corr.columns)


def top_pairs(corr: pd.DataFrame, top_n: int = 25) -> pd.DataFrame:
    rows = []
    cols = list(corr.columns)
    for i, c1 in enumerate(cols):
        for j in range(i + 1, len(cols)):
            c2 = cols[j]
            value = corr.iloc[i, j]
            if pd.notna(value):
                rows.append((c1, c2, float(value)))
    out = pd.DataFrame(rows, columns=["city_a", "city_b", "corr"])
    return out.sort_values("corr", ascending=False).head(top_n)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Estimate Zeus city correlation matrices.")
    parser.add_argument("db_path")
    parser.add_argument("--domain", choices=["settlement_anomaly", "forecast_error"], default="settlement_anomaly")
    parser.add_argument("--source", default="ecmwf")
    parser.add_argument("--lead-days", type=int, default=3)
    parser.add_argument("--shrinkage-lambda", type=float, default=0.20)
    args = parser.parse_args()

    with sqlite3.connect(args.db_path) as conn:
        if args.domain == "settlement_anomaly":
            matrix = load_settlement_anomalies(conn)
        else:
            matrix = load_forecast_error_matrix(conn, source=args.source, lead_days=args.lead_days)

    corr = shrink_correlation(matrix, shrinkage_lambda=args.shrinkage_lambda)
    print(top_pairs(corr).to_string(index=False))
