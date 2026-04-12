from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(slots=True)
class CalibrationDecisionGroup:
    group_id: str
    city: str
    target_date: str
    forecast_available_at: str
    cluster: str
    season: str
    lead_days: float
    settlement_value: float | None
    winning_range_label: str | None
    bias_corrected: bool
    n_pair_rows: int
    n_positive_rows: int
    recorded_at: str



def make_group_id(city: str, target_date: str, forecast_available_at: str) -> str:
    return f"{city}|{target_date}|{forecast_available_at}"



def ensure_calibration_pair_group_column(conn: sqlite3.Connection) -> bool:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(calibration_pairs)")}
    added = False
    if "decision_group_id" not in columns:
        conn.execute("ALTER TABLE calibration_pairs ADD COLUMN decision_group_id TEXT")
        added = True
    if "bias_corrected" not in columns:
        conn.execute("ALTER TABLE calibration_pairs ADD COLUMN bias_corrected INTEGER DEFAULT 0")
        added = True
    return added



def backfill_calibration_decision_groups(
    conn: sqlite3.Connection,
    *,
    update_pair_rows: bool = True,
    only_since: str | None = None,
) -> dict[str, int]:
    if update_pair_rows:
        ensure_calibration_pair_group_column(conn)

    sql = """
        SELECT
            city,
            target_date,
            forecast_available_at,
            cluster,
            season,
            lead_days,
            settlement_value,
            COALESCE(MAX(bias_corrected), 0) AS bias_corrected,
            COUNT(*) AS n_pair_rows,
            SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS n_positive_rows,
            MAX(CASE WHEN outcome = 1 THEN range_label ELSE NULL END) AS winning_range_label
        FROM calibration_pairs
    """
    params: list[object] = []
    if only_since:
        sql += " WHERE target_date >= ?"
        params.append(only_since)
    sql += " GROUP BY city, target_date, forecast_available_at, cluster, season, lead_days, settlement_value"

    now = datetime.now(timezone.utc).isoformat()
    groups = 0
    anomalies = 0
    pair_updates = 0
    for row in conn.execute(sql, params):
        (
            city,
            target_date,
            forecast_available_at,
            cluster,
            season,
            lead_days,
            settlement_value,
            bias_corrected,
            n_pair_rows,
            n_positive_rows,
            winning_range_label,
        ) = row
        group_id = make_group_id(city, target_date, forecast_available_at)
        conn.execute(
            """
            INSERT OR REPLACE INTO calibration_decision_group (
                group_id,
                city,
                target_date,
                forecast_available_at,
                cluster,
                season,
                lead_days,
                settlement_value,
                winning_range_label,
                bias_corrected,
                n_pair_rows,
                n_positive_rows,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_id,
                city,
                target_date,
                forecast_available_at,
                cluster,
                season,
                float(lead_days),
                settlement_value,
                winning_range_label,
                int(bool(bias_corrected)),
                int(n_pair_rows),
                int(n_positive_rows),
                now,
            ),
        )
        if update_pair_rows:
            cur = conn.execute(
                """
                UPDATE calibration_pairs
                SET decision_group_id = ?, bias_corrected = ?
                WHERE city = ? AND target_date = ? AND forecast_available_at = ?
                """,
                (group_id, int(bool(bias_corrected)), city, target_date, forecast_available_at),
            )
            pair_updates += cur.rowcount
        groups += 1
        if int(n_pair_rows) != 11 or int(n_positive_rows) != 1:
            anomalies += 1
    return {
        "groups": groups,
        "pair_updates": pair_updates,
        "anomalous_groups": anomalies,
    }
