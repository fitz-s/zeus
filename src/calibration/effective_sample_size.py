"""Decision-group calibration accounting.

`calibration_pairs` is a bin-row table. A single forecast event usually emits
many rows, so calibration maturity needs an independent group substrate before
any future behavior change can safely move away from pair-row counts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
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
    bias_corrected: int
    n_pair_rows: int
    n_positive_rows: int


def _group_id(city: str, target_date: str, forecast_available_at: str) -> str:
    return f"{city}|{target_date}|{forecast_available_at}"


def build_decision_groups(conn: sqlite3.Connection) -> list[CalibrationDecisionGroup]:
    """Build one independent calibration sample per city/date/forecast time."""
    rows = conn.execute(
        """
        SELECT
            city,
            target_date,
            forecast_available_at,
            MIN(cluster) AS cluster,
            MIN(season) AS season,
            AVG(lead_days) AS lead_days,
            MAX(settlement_value) AS settlement_value,
            MAX(CASE WHEN outcome = 1 THEN range_label ELSE NULL END) AS winning_range_label,
            COUNT(*) AS n_pair_rows,
            SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS n_positive_rows
        FROM calibration_pairs
        GROUP BY city, target_date, forecast_available_at
        ORDER BY city, target_date, forecast_available_at
        """
    ).fetchall()

    groups: list[CalibrationDecisionGroup] = []
    for row in rows:
        city = str(row["city"])
        target_date = str(row["target_date"])
        forecast_available_at = str(row["forecast_available_at"])
        groups.append(
            CalibrationDecisionGroup(
                group_id=_group_id(city, target_date, forecast_available_at),
                city=city,
                target_date=target_date,
                forecast_available_at=forecast_available_at,
                cluster=str(row["cluster"]),
                season=str(row["season"]),
                lead_days=float(row["lead_days"] or 0.0),
                settlement_value=(
                    None if row["settlement_value"] is None else float(row["settlement_value"])
                ),
                winning_range_label=(
                    None if row["winning_range_label"] is None else str(row["winning_range_label"])
                ),
                bias_corrected=0,
                n_pair_rows=int(row["n_pair_rows"] or 0),
                n_positive_rows=int(row["n_positive_rows"] or 0),
            )
        )
    return groups


def summarize_bucket_health(groups: list[CalibrationDecisionGroup]) -> list[dict]:
    """Summarize effective calibration sample size by bucket."""
    buckets: dict[tuple[str, str], dict] = {}
    for group in groups:
        key = (group.cluster, group.season)
        bucket = buckets.setdefault(
            key,
            {
                "bucket_key": f"{group.cluster}_{group.season}",
                "cluster": group.cluster,
                "season": group.season,
                "decision_groups": 0,
                "pair_rows": 0,
                "positive_rows": 0,
                "min_lead_days": group.lead_days,
                "max_lead_days": group.lead_days,
            },
        )
        bucket["decision_groups"] += 1
        bucket["pair_rows"] += group.n_pair_rows
        bucket["positive_rows"] += group.n_positive_rows
        bucket["min_lead_days"] = min(bucket["min_lead_days"], group.lead_days)
        bucket["max_lead_days"] = max(bucket["max_lead_days"], group.lead_days)

    out = []
    for bucket in buckets.values():
        decision_groups = int(bucket["decision_groups"])
        bucket["rows_per_group"] = (
            round(float(bucket["pair_rows"]) / decision_groups, 2)
            if decision_groups
            else 0.0
        )
        out.append(bucket)
    return sorted(out, key=lambda row: (-row["decision_groups"], row["bucket_key"]))


def write_decision_groups(
    conn: sqlite3.Connection,
    groups: list[CalibrationDecisionGroup],
    *,
    recorded_at: str,
) -> int:
    """Materialize groups into the additive `calibration_decision_group` table."""
    if not groups:
        return 0
    rows = [
        (
            group.group_id,
            group.city,
            group.target_date,
            group.forecast_available_at,
            group.cluster,
            group.season,
            group.lead_days,
            group.settlement_value,
            group.winning_range_label,
            group.bias_corrected,
            group.n_pair_rows,
            group.n_positive_rows,
            recorded_at,
        )
        for group in groups
    ]
    conn.executemany(
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
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)
