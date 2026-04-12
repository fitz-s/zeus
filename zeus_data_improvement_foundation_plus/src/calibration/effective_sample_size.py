"""Decision-group aware calibration accounting for Zeus.

This module turns bin-level `calibration_pairs` rows into independent forecast-event groups.
It is intentionally SQLite/pandas based so it can run directly against zeus-shared.db.

Key idea:
    calibration maturity should be based on independent decision groups, not raw pair rows.

A decision group is defined as:
    (city, target_date, forecast_available_at)

You can extend the key later with source / model_version if needed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


GROUP_KEY_SQL = """
SELECT
    city,
    target_date,
    forecast_available_at,
    MIN(cluster) AS cluster,
    MIN(season) AS season,
    AVG(lead_days) AS lead_days,
    MAX(settlement_value) AS settlement_value,
    MAX(bias_corrected) AS bias_corrected,
    COUNT(*) AS n_pair_rows,
    SUM(outcome) AS n_positive_rows
FROM calibration_pairs
GROUP BY city, target_date, forecast_available_at
"""


@dataclass(frozen=True)
class MaturityThresholds:
    """Bucket maturity thresholds in independent decision groups."""

    standard: int = 150
    regularized: int = 50
    fallback: int = 15


def build_decision_groups(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return one row per independent calibration decision group."""
    df = pd.read_sql_query(GROUP_KEY_SQL, conn)
    if df.empty:
        return df

    df["bucket_key"] = df["cluster"] + "_" + df["season"]
    df["group_weight"] = 1.0 / df["n_pair_rows"].clip(lower=1)
    return df


def summarize_bucket_health(
    decision_groups: pd.DataFrame,
    thresholds: MaturityThresholds = MaturityThresholds(),
) -> pd.DataFrame:
    """Summarize effective calibration sample size by bucket."""
    if decision_groups.empty:
        return pd.DataFrame(
            columns=[
                "bucket_key",
                "cluster",
                "season",
                "decision_groups",
                "pair_rows",
                "avg_rows_per_group",
                "positive_rows",
                "maturity_level",
            ]
        )

    summary = (
        decision_groups.groupby(["bucket_key", "cluster", "season"], dropna=False)
        .agg(
            decision_groups=("target_date", "size"),
            pair_rows=("n_pair_rows", "sum"),
            positive_rows=("n_positive_rows", "sum"),
            avg_rows_per_group=("n_pair_rows", "mean"),
            min_lead_days=("lead_days", "min"),
            max_lead_days=("lead_days", "max"),
        )
        .reset_index()
    )

    def maturity_level(n_groups: int) -> str:
        if n_groups >= thresholds.standard:
            return "standard"
        if n_groups >= thresholds.regularized:
            return "regularized"
        if n_groups >= thresholds.fallback:
            return "fallback"
        return "insufficient"

    summary["maturity_level"] = summary["decision_groups"].map(maturity_level)
    return summary.sort_values(["decision_groups", "bucket_key"], ascending=[False, True])


def write_decision_groups_back(
    conn: sqlite3.Connection,
    decision_groups: pd.DataFrame,
    recorded_at: str,
) -> int:
    """Materialize decision-group accounting into `calibration_decision_group`.

    This assumes the migration in this package has already been applied.
    """
    if decision_groups.empty:
        return 0

    rows = []
    for row in decision_groups.itertuples(index=False):
        group_id = f"{row.city}|{row.target_date}|{row.forecast_available_at}"
        rows.append(
            (
                group_id,
                row.city,
                row.target_date,
                row.forecast_available_at,
                row.cluster,
                row.season,
                float(row.lead_days),
                None if pd.isna(row.settlement_value) else float(row.settlement_value),
                None,  # winning_range_label can be backfilled from settlements if needed
                int(row.bias_corrected),
                int(row.n_pair_rows),
                int(row.n_positive_rows),
                recorded_at,
            )
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO calibration_decision_group (
            group_id, city, target_date, forecast_available_at, cluster, season, lead_days,
            settlement_value, winning_range_label, bias_corrected, n_pair_rows, n_positive_rows,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def load_and_summarize(db_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convenience helper for scripts/notebooks."""
    with sqlite3.connect(str(db_path)) as conn:
        groups = build_decision_groups(conn)
    health = summarize_bucket_health(groups)
    return groups, health


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build decision-group aware calibration summaries.")
    parser.add_argument("db_path", help="Path to zeus-shared.db")
    parser.add_argument("--write-back", action="store_true", help="Write rows into calibration_decision_group")
    parser.add_argument("--recorded-at", default="manual", help="recorded_at value for write-back")
    args = parser.parse_args()

    with sqlite3.connect(args.db_path) as conn:
        groups = build_decision_groups(conn)
        health = summarize_bucket_health(groups)

        print("Decision groups:", len(groups))
        print(health.to_string(index=False))

        if args.write_back:
            n = write_decision_groups_back(conn, groups, recorded_at=args.recorded_at)
            conn.commit()
            print(f"Wrote {n} rows into calibration_decision_group.")
