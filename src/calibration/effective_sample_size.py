"""Decision-group calibration accounting.

`calibration_pairs` is a bin-row table. A single forecast event usually emits
many rows, so calibration maturity needs an independent group substrate before
any future behavior change can safely move away from pair-row counts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from src.config import calibration_maturity_thresholds


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


def _lead_key(lead_days: float) -> str:
    return f"{float(lead_days):g}"


def _group_id(city: str, target_date: str, forecast_available_at: str, lead_days: float) -> str:
    return f"{city}|{target_date}|{forecast_available_at}|lead={_lead_key(lead_days)}"


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
            lead_days,
            MAX(settlement_value) AS settlement_value,
            MAX(CASE WHEN outcome = 1 THEN range_label ELSE NULL END) AS winning_range_label,
            COALESCE(MAX(bias_corrected), 0) AS bias_corrected,
            COUNT(*) AS n_pair_rows,
            SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS n_positive_rows
        FROM calibration_pairs
        GROUP BY city, target_date, forecast_available_at, lead_days
        ORDER BY city, target_date, forecast_available_at, lead_days
        """
    ).fetchall()

    groups: list[CalibrationDecisionGroup] = []
    for row in rows:
        city = str(row["city"])
        target_date = str(row["target_date"])
        forecast_available_at = str(row["forecast_available_at"])
        lead_days = float(row["lead_days"] or 0.0)
        groups.append(
            CalibrationDecisionGroup(
                group_id=_group_id(city, target_date, forecast_available_at, lead_days),
                city=city,
                target_date=target_date,
                forecast_available_at=forecast_available_at,
                cluster=str(row["cluster"]),
                season=str(row["season"]),
                lead_days=lead_days,
                settlement_value=(
                    None if row["settlement_value"] is None else float(row["settlement_value"])
                ),
                winning_range_label=(
                    None if row["winning_range_label"] is None else str(row["winning_range_label"])
                ),
                bias_corrected=int(row["bias_corrected"] or 0),
                n_pair_rows=int(row["n_pair_rows"] or 0),
                n_positive_rows=int(row["n_positive_rows"] or 0),
            )
        )
    return groups


def build_decision_group_for_key(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    forecast_available_at: str,
    lead_days: float | None = None,
) -> CalibrationDecisionGroup | None:
    """Build one decision group for a freshly written calibration sample."""
    lead_filter = "" if lead_days is None else "AND lead_days = ?"
    params: tuple = (
        (city, target_date, forecast_available_at)
        if lead_days is None
        else (city, target_date, forecast_available_at, lead_days)
    )
    row = conn.execute(
        f"""
        SELECT
            city,
            target_date,
            forecast_available_at,
            MIN(cluster) AS cluster,
            MIN(season) AS season,
            lead_days,
            MAX(settlement_value) AS settlement_value,
            MAX(CASE WHEN outcome = 1 THEN range_label ELSE NULL END) AS winning_range_label,
            COALESCE(MAX(bias_corrected), 0) AS bias_corrected,
            COUNT(*) AS n_pair_rows,
            SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS n_positive_rows
        FROM calibration_pairs
        WHERE city = ?
          AND target_date = ?
          AND forecast_available_at = ?
          {lead_filter}
        GROUP BY city, target_date, forecast_available_at, lead_days
        """,
        params,
    ).fetchone()
    if row is None:
        return None
    row_lead_days = float(row["lead_days"] or 0.0)
    return CalibrationDecisionGroup(
        group_id=_group_id(
            str(row["city"]),
            str(row["target_date"]),
            str(row["forecast_available_at"]),
            row_lead_days,
        ),
        city=str(row["city"]),
        target_date=str(row["target_date"]),
        forecast_available_at=str(row["forecast_available_at"]),
        cluster=str(row["cluster"]),
        season=str(row["season"]),
        lead_days=row_lead_days,
        settlement_value=None if row["settlement_value"] is None else float(row["settlement_value"]),
        winning_range_label=None if row["winning_range_label"] is None else str(row["winning_range_label"]),
        bias_corrected=int(row["bias_corrected"] or 0),
        n_pair_rows=int(row["n_pair_rows"] or 0),
        n_positive_rows=int(row["n_positive_rows"] or 0),
    )


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


def _maturity_level_from_count(n_samples: int) -> int:
    level1, level2, level3 = calibration_maturity_thresholds()
    if n_samples >= level1:
        return 1
    if n_samples >= level2:
        return 2
    if n_samples >= level3:
        return 3
    return 4


def summarize_maturity_shadow(groups: list[CalibrationDecisionGroup]) -> list[dict]:
    """Compare current pair-row maturity with decision-group effective maturity.

    This is behavior-neutral: active routing can continue using existing pair
    counts while this report exposes buckets whose apparent maturity is inflated
    by many bin rows per independent forecast event.
    """
    health_rows = summarize_bucket_health(groups)
    out = []
    for row in health_rows:
        pair_rows = int(row["pair_rows"])
        decision_groups = int(row["decision_groups"])
        pair_level = _maturity_level_from_count(pair_rows)
        group_level = _maturity_level_from_count(decision_groups)
        out.append(
            {
                **row,
                "pair_row_maturity_level": pair_level,
                "decision_group_maturity_level": group_level,
                "maturity_inflated": group_level > pair_level,
            }
        )
    return out


def write_decision_groups(
    conn: sqlite3.Connection,
    groups: list[CalibrationDecisionGroup],
    *,
    recorded_at: str,
    update_pair_rows: bool = True,
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
    if update_pair_rows:
        conn.executemany(
            """
            UPDATE calibration_pairs
            SET decision_group_id = ?, bias_corrected = ?
            WHERE city = ?
              AND target_date = ?
              AND forecast_available_at = ?
              AND lead_days = ?
            """,
            [
                (
                    group.group_id,
                    group.bias_corrected,
                    group.city,
                    group.target_date,
                    group.forecast_available_at,
                    group.lead_days,
                )
                for group in groups
            ],
        )
    return len(rows)
