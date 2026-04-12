"""Probability trace completeness audit for Zeus.

This module is intentionally small and practical:
it helps close the gap between shadow-only probability vectors and canonical fact surfaces.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


def load_probability_surface_summary(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            'opportunity_fact' AS table_name,
            COUNT(*) AS rows,
            SUM(CASE WHEN p_raw IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_raw,
            SUM(CASE WHEN p_cal IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_cal,
            SUM(CASE WHEN p_market IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_market,
            SUM(CASE WHEN alpha IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_alpha
        FROM opportunity_fact
        UNION ALL
        SELECT
            'trade_decisions' AS table_name,
            COUNT(*) AS rows,
            SUM(CASE WHEN p_raw IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_raw,
            SUM(CASE WHEN p_calibrated IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_cal,
            SUM(CASE WHEN p_posterior IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_market,
            NULL AS nonnull_alpha
        FROM trade_decisions
        UNION ALL
        SELECT
            'shadow_signals' AS table_name,
            COUNT(*) AS rows,
            SUM(CASE WHEN p_raw_json IS NOT NULL AND trim(p_raw_json) <> '' THEN 1 ELSE 0 END) AS nonnull_p_raw,
            SUM(CASE WHEN p_cal_json IS NOT NULL AND trim(p_cal_json) <> '' THEN 1 ELSE 0 END) AS nonnull_p_cal,
            SUM(CASE WHEN edges_json IS NOT NULL AND trim(edges_json) <> '' THEN 1 ELSE 0 END) AS nonnull_p_market,
            NULL AS nonnull_alpha
        FROM shadow_signals
        """,
        conn,
    )


def recommend_actions(summary: pd.DataFrame) -> list[str]:
    actions: list[str] = []
    lookup = {row["table_name"]: row for _, row in summary.iterrows()}

    opp = lookup.get("opportunity_fact")
    if opp is not None and opp["rows"] and opp["nonnull_p_raw"] == 0:
        actions.append("Backfill p_raw vectors into canonical probability trace at decision time.")

    td = lookup.get("trade_decisions")
    if td is not None and td["rows"] and td["nonnull_p_cal"] == 0:
        actions.append("Persist calibrated probabilities on every trade decision row or linked probability_trace_fact.")

    shadow = lookup.get("shadow_signals")
    if shadow is not None and shadow["nonnull_p_raw"] > 0:
        actions.append("Promote shadow_signals probability vectors into a first-class durable fact surface.")

    return actions


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Audit Zeus probability truth surfaces.")
    parser.add_argument("db_path")
    args = parser.parse_args()

    with sqlite3.connect(args.db_path) as conn:
        summary = load_probability_surface_summary(conn)

    print(summary.to_string(index=False))
    print()
    for item in recommend_actions(summary):
        print("-", item)
