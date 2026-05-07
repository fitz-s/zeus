# Shadow-only: outputs are additive facts, not live blockers
"""Decision-group calibration accounting.

`calibration_pairs` is a bin-row table. A single forecast event usually emits
many rows, so calibration maturity needs an independent group substrate before
any future behavior change can safely move away from pair-row counts.
"""

from __future__ import annotations

SHADOW_ONLY: bool = True  # ZDM-02: explicitly advisory-only; must never enter evaluator/control gate

import sqlite3
from dataclasses import dataclass

from src.architecture.decorators import capability, protects
from src.config import calibration_maturity_thresholds, cities_by_name


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


def build_decision_groups(
    conn: sqlite3.Connection,
    authority_filter: str = "VERIFIED",
) -> list[CalibrationDecisionGroup]:
    """Build one independent calibration sample per city/date/forecast time.

    K4.5 H5 fix: filters by authority='VERIFIED' by default.
    Pass authority_filter='any' to include all rows (diagnostics only).
    """
    clauses = ["decision_group_id IS NOT NULL", "decision_group_id != ''"]
    params: tuple = ()
    if authority_filter != "any":
        clauses.append("authority = ?")
        params = (authority_filter,)
    where_clause = "WHERE " + " AND ".join(clauses)

    rows = conn.execute(
        f"""
        SELECT
            decision_group_id,
            MIN(city) AS city,
            MIN(target_date) AS target_date,
            MIN(forecast_available_at) AS forecast_available_at,
            MIN(cluster) AS cluster,
            MIN(season) AS season,
            MIN(lead_days) AS lead_days,
            MAX(settlement_value) AS settlement_value,
            MAX(CASE WHEN outcome = 1 THEN range_label ELSE NULL END) AS winning_range_label,
            COALESCE(MAX(bias_corrected), 0) AS bias_corrected,
            COUNT(*) AS n_pair_rows,
            SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS n_positive_rows,
            COUNT(DISTINCT city) AS distinct_city,
            COUNT(DISTINCT target_date) AS distinct_target_date,
            COUNT(DISTINCT forecast_available_at) AS distinct_forecast_available_at,
            COUNT(DISTINCT lead_days) AS distinct_lead_days,
            COUNT(DISTINCT cluster) AS distinct_cluster,
            COUNT(DISTINCT season) AS distinct_season,
            COUNT(DISTINCT bin_source) AS distinct_bin_source,
            COUNT(DISTINCT range_label) AS distinct_range_label,
            MAX(bin_source) AS bin_source
        FROM calibration_pairs
        {where_clause}
        GROUP BY decision_group_id
        ORDER BY city, target_date, forecast_available_at, lead_days, decision_group_id
        """,
        params,
    ).fetchall()

    groups: list[CalibrationDecisionGroup] = []
    for row in rows:
        city = str(row["city"])
        target_date = str(row["target_date"])
        forecast_available_at = str(row["forecast_available_at"])
        lead_days = float(row["lead_days"] or 0.0)
        _raise_on_group_collision(row)
        groups.append(
            CalibrationDecisionGroup(
                group_id=str(row["decision_group_id"]),
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
    authority_filter: str = "VERIFIED",
) -> CalibrationDecisionGroup | None:
    """Build one decision group for a freshly written calibration sample.

    K4.5.1 fix: filters by authority='VERIFIED' by default (matches sibling
    build_decision_groups). Pass authority_filter='any' for diagnostics.
    """
    lead_filter = "" if lead_days is None else "AND lead_days = ?"
    auth_filter = "" if authority_filter == "any" else "AND authority = ?"
    params: tuple = (
        (city, target_date, forecast_available_at)
        if lead_days is None
        else (city, target_date, forecast_available_at, lead_days)
    )
    if authority_filter != "any":
        params = params + (authority_filter,)
    rows = conn.execute(
        f"""
        SELECT
            decision_group_id,
            MIN(city) AS city,
            MIN(target_date) AS target_date,
            MIN(forecast_available_at) AS forecast_available_at,
            MIN(cluster) AS cluster,
            MIN(season) AS season,
            MIN(lead_days) AS lead_days,
            MAX(settlement_value) AS settlement_value,
            MAX(CASE WHEN outcome = 1 THEN range_label ELSE NULL END) AS winning_range_label,
            COALESCE(MAX(bias_corrected), 0) AS bias_corrected,
            COUNT(*) AS n_pair_rows,
            SUM(CASE WHEN outcome = 1 THEN 1 ELSE 0 END) AS n_positive_rows,
            COUNT(DISTINCT city) AS distinct_city,
            COUNT(DISTINCT target_date) AS distinct_target_date,
            COUNT(DISTINCT forecast_available_at) AS distinct_forecast_available_at,
            COUNT(DISTINCT lead_days) AS distinct_lead_days,
            COUNT(DISTINCT cluster) AS distinct_cluster,
            COUNT(DISTINCT season) AS distinct_season,
            COUNT(DISTINCT bin_source) AS distinct_bin_source,
            COUNT(DISTINCT range_label) AS distinct_range_label,
            MAX(bin_source) AS bin_source
        FROM calibration_pairs
        WHERE city = ?
          AND target_date = ?
          AND forecast_available_at = ?
          {lead_filter}
          {auth_filter}
          AND decision_group_id IS NOT NULL
        GROUP BY decision_group_id
        """,
        params,
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise ValueError(
            "Multiple decision_group_id values matched "
            f"{city}/{target_date}/{forecast_available_at}; pass lead_days "
            "and source_model_version-specific context to disambiguate"
        )
    row = rows[0]
    _raise_on_group_collision(row)
    row_lead_days = float(row["lead_days"] or 0.0)
    return CalibrationDecisionGroup(
        group_id=str(row["decision_group_id"]),
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


def _raise_on_group_collision(row: sqlite3.Row) -> None:
    conflicted = [
        field
        for field in (
            "city",
            "target_date",
            "forecast_available_at",
            "lead_days",
            "cluster",
            "season",
            "bin_source",
        )
        if int(row[f"distinct_{field}"] or 0) > 1
    ]
    if conflicted:
        raise ValueError(
            "decision_group_id collision with conflicting metadata fields "
            f"{conflicted}: {row['decision_group_id']!r}"
        )
    if row["bin_source"] == "canonical_v1" and int(row["n_positive_rows"] or 0) != 1:
        raise ValueError(
            "canonical decision group must have exactly one positive row: "
            f"{row['decision_group_id']!r} has {row['n_positive_rows']}"
        )
    if row["bin_source"] == "canonical_v1":
        n_pair_rows = int(row["n_pair_rows"] or 0)
        city_name = str(row["city"]) if "city" in row.keys() else None
        city_obj = cities_by_name.get(city_name) if city_name else None
        if city_obj:
            from src.contracts.calibration_bins import F_CANONICAL_GRID, C_CANONICAL_GRID
            expected = F_CANONICAL_GRID.n_bins if city_obj.settlement_unit == "F" else C_CANONICAL_GRID.n_bins
            if n_pair_rows != expected:
                raise ValueError(
                    f"canonical decision group has wrong pair row count for unit {city_obj.settlement_unit!r}: "
                    f"{row['decision_group_id']!r} has {n_pair_rows} (expected {expected})"
                )
        elif n_pair_rows not in (92, 102):
            raise ValueError(
                "canonical decision group has truncated pair rows: "
                f"{row['decision_group_id']!r} has {n_pair_rows}"
            )
    if row["bin_source"] == "canonical_v1" and int(row["distinct_range_label"] or 0) != int(row["n_pair_rows"] or 0):
        raise ValueError(
            "canonical decision group has duplicate range labels: "
            f"{row['decision_group_id']!r}"
        )


def summarize_bucket_health(groups: list[CalibrationDecisionGroup]) -> list[dict]:
    """Summarize effective calibration sample size by bucket."""
    buckets: dict[tuple[str, str], dict] = {}
    for group in groups:
        key = (group.cluster, group.season)
        bucket = buckets.setdefault(
            key,
            {
                "shadow_only": True,
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


@capability("calibration_decision_group_write", lease=True)
@protects("INV-15", "INV-21")
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
            SET bias_corrected = ?
            WHERE decision_group_id = ?
            """,
            [
                (
                    group.bias_corrected,
                    group.group_id,
                )
                for group in groups
            ],
        )
    return len(rows)
