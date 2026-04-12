"""Execution / microstructure diagnostics for Zeus.

This is not a full strategy module; it is a practical data-prep layer for learning:
- where spreads are persistently wide
- where quote quality is too poor for aggressive entry
- where edge persistence should influence order style
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MicrostructureProfile:
    city: str
    token_count: int
    target_days: int
    quote_rows: int
    nonnull_spread_rows: int
    avg_spread: float | None


def city_profiles(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            city,
            COUNT(*) AS quote_rows,
            COUNT(DISTINCT token_id) AS token_count,
            COUNT(DISTINCT target_date) AS target_days,
            SUM(CASE WHEN spread IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_spread_rows,
            AVG(CASE WHEN spread IS NOT NULL THEN spread END) AS avg_spread
        FROM token_price_log
        GROUP BY city
        ORDER BY quote_rows DESC
        """,
        conn,
    )


def candidate_wide_spread_cities(conn: sqlite3.Connection, min_quote_rows: int = 50000) -> pd.DataFrame:
    profiles = city_profiles(conn)
    return profiles[
        (profiles["quote_rows"] >= min_quote_rows)
        & (profiles["nonnull_spread_rows"] > 0)
    ].sort_values(["avg_spread", "quote_rows"], ascending=[False, False])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Audit Zeus microstructure coverage from token_price_log.")
    parser.add_argument("db_path")
    args = parser.parse_args()

    with sqlite3.connect(args.db_path) as conn:
        profiles = city_profiles(conn)

    print(profiles.to_string(index=False))
