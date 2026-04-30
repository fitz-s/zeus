# Created: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Rebuild date/metric-scoped settlement rows from VERIFIED daily observations.
# Reuse: Inspect SettlementSemantics, metric_identity, and the active source-conversion packet before applying writes.
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/full_suite_blocker_plan_2026-04-27.md
"""Rebuild high-temperature settlement rows from VERIFIED daily observations.

This repair helper is intentionally narrow: it writes only high-track settlement
rows derived from observations that are already authority='VERIFIED'. It does
not fetch external data, infer provider validity, or authorize live deployment.
Callers own transaction boundaries; dry-run is the default for CLI use.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import cities_by_name
from src.contracts.exceptions import SettlementPrecisionError
from src.contracts.settlement_semantics import SettlementSemantics
from src.data.rebuild_validators import (
    ImpossibleTemperatureError,
    UnknownUnitError,
    validate_observation_for_settlement,
)
from src.state.db import get_world_connection
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity

METRIC_IDENTITIES = {
    "high": HIGH_LOCALDAY_MAX,
    "low": LOW_LOCALDAY_MIN,
}
SETTLEMENT_DATA_VERSION_BY_SOURCE_TYPE = {
    "wu_icao": "wu_icao_history_v1",
    "hko": "hko_daily_api_v1",
    "noaa": "ogimet_metar_v1",
    "cwa_station": "cwa_no_collector_v0",
}


class SettlementRebuildSkip(ValueError):
    """Expected row-level skip for rebuild_settlements."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _city_for_observation(row: sqlite3.Row):
    city_name = str(row["city"])
    city = cities_by_name.get(city_name)
    if city is None:
        raise SettlementRebuildSkip("unknown_city")
    return city


def _validate_source_family(row: sqlite3.Row, city) -> None:
    source = str(row["source"] or "")
    source_type = city.settlement_source_type

    if source_type == "wu_icao":
        # Legacy fixture alias `wu_icao` is WU-family only. It must never
        # leak into HKO/Hong Kong or other source families.
        if source in {"wu_icao_history", "wu_icao"}:
            return
        raise SettlementRebuildSkip("source_family_mismatch")

    if source_type == "hko":
        if source == "hko_daily_api":
            return
        raise SettlementRebuildSkip("source_family_mismatch")

    if source_type == "noaa":
        if source.startswith("ogimet_metar_"):
            return
        raise SettlementRebuildSkip("source_family_mismatch")

    if source_type == "cwa_station":
        raise SettlementRebuildSkip("unsupported_source_family")

    raise SettlementRebuildSkip("unsupported_source_family")


def _round_metric_value(
    row: sqlite3.Row,
    conn: sqlite3.Connection,
    *,
    metric_identity: MetricIdentity,
) -> tuple[float, Any]:
    city = _city_for_observation(row)
    _validate_source_family(row, city)
    validator_row = dict(row)
    # validate_observation_for_settlement is the legacy high_temp validator.
    # Feed the selected metric value through that field while preserving the
    # original row for provenance and SQL writes.
    validator_row["high_temp"] = row[metric_identity.observation_field]
    converted_value = validate_observation_for_settlement(validator_row, city, conn)
    sem = SettlementSemantics.for_city(city)
    settlement_value = sem.assert_settlement_value(
        converted_value,
        context=f"rebuild_settlements/{city.name}/{row['target_date']}",
    )
    return settlement_value, city


def rebuild_settlements(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    city_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    temperature_metric: str = "high",
) -> dict[str, Any]:
    """Rebuild settlement rows from VERIFIED observation highs.

    Args:
        conn: Open world DB connection. The caller owns commit/rollback.
        dry_run: When true, compute counts without writing.
        city_filter: Optional exact city name filter.

    Returns a small summary dictionary. ``rows_skipped`` counts VERIFIED rows
    that could not be converted; UNVERIFIED rows are ignored and reported under
    ``unverified_ignored`` so authority filtering is not treated as an error.
    """

    metric_identity = METRIC_IDENTITIES[temperature_metric]
    conn.row_factory = sqlite3.Row
    obs_field = metric_identity.observation_field
    where = f"authority = 'VERIFIED' AND {obs_field} IS NOT NULL"
    params: list[Any] = []
    if city_filter:
        where += " AND city = ?"
        params.append(city_filter)
    if start_date:
        where += " AND target_date >= ?"
        params.append(start_date)
    if end_date:
        where += " AND target_date <= ?"
        params.append(end_date)

    rows = conn.execute(
        f"""
        SELECT city, target_date, source, high_temp, low_temp, unit, authority
        FROM observations
        WHERE {where}
        ORDER BY city, target_date
        """,
        params,
    ).fetchall()

    unverified_where = "authority != 'VERIFIED'"
    unverified_params: list[Any] = []
    if city_filter:
        unverified_where += " AND city = ?"
        unverified_params.append(city_filter)
    if start_date:
        unverified_where += " AND target_date >= ?"
        unverified_params.append(start_date)
    if end_date:
        unverified_where += " AND target_date <= ?"
        unverified_params.append(end_date)
    unverified_ignored = int(
        conn.execute(
            f"SELECT COUNT(*) FROM observations WHERE {unverified_where}",
            unverified_params,
        ).fetchone()[0]
    )

    rows_written = 0
    rows_skipped = 0
    rows_skipped_by_reason: Counter[str] = Counter()
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        try:
            settlement_value, city = _round_metric_value(
                row,
                conn,
                metric_identity=metric_identity,
            )
        except SettlementRebuildSkip as exc:
            rows_skipped += 1
            rows_skipped_by_reason[exc.reason] += 1
            continue
        except (ImpossibleTemperatureError, UnknownUnitError, SettlementPrecisionError):
            rows_skipped += 1
            rows_skipped_by_reason["invalid_observation"] += 1
            continue

        if dry_run:
            rows_written += 1
            continue

        data_version = SETTLEMENT_DATA_VERSION_BY_SOURCE_TYPE.get(
            city.settlement_source_type,
            "unknown_v0",
        )
        provenance_json = json.dumps(
            {
                "source": "scripts/rebuild_settlements.py",
                "authority": "VERIFIED",
                "obs_source": row["source"],
                "settlement_source_type": city.settlement_source_type,
                "data_version": data_version,
            },
            sort_keys=True,
        )

        conn.execute(
            """
            INSERT INTO settlements
            (city, target_date, winning_bin, settlement_value, settlement_source, settled_at,
             authority, temperature_metric, physical_quantity, observation_field,
             data_version, provenance_json, unit, settlement_source_type)
            VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(city, target_date, temperature_metric) DO UPDATE SET
                winning_bin = excluded.winning_bin,
                settlement_value = excluded.settlement_value,
                settlement_source = excluded.settlement_source,
                settled_at = excluded.settled_at,
                authority = excluded.authority,
                physical_quantity = excluded.physical_quantity,
                observation_field = excluded.observation_field,
                data_version = excluded.data_version,
                provenance_json = excluded.provenance_json,
                unit = excluded.unit,
                settlement_source_type = excluded.settlement_source_type
            """,
            (
                row["city"],
                row["target_date"],
                f"{int(settlement_value)}°{city.settlement_unit}",
                settlement_value,
                row["source"] or "verified_observation_rebuild",
                now,
                metric_identity.temperature_metric,
                metric_identity.physical_quantity,
                metric_identity.observation_field,
                data_version,
                provenance_json,
                city.settlement_unit,
                city.settlement_source_type,
            ),
        )
        rows_written += 1

    return {
        "dry_run": dry_run,
        "city_filter": city_filter,
        "start_date": start_date,
        "end_date": end_date,
        "temperature_metric": temperature_metric,
        "rows_seen": len(rows),
        "rows_written": rows_written,
        "rows_skipped": rows_skipped,
        "rows_skipped_by_reason": dict(rows_skipped_by_reason),
        "unverified_ignored": unverified_ignored,
    }


def rebuild_settlements_scoped(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    city_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    temperature_metric: str = "high",
) -> dict[str, Any]:
    metrics = ["high", "low"] if temperature_metric == "all" else [temperature_metric]
    return {
        metric: rebuild_settlements(
            conn,
            dry_run=dry_run,
            city_filter=city_filter,
            start_date=start_date,
            end_date=end_date,
            temperature_metric=metric,
        )
        for metric in metrics
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="World DB path; defaults to configured world DB")
    parser.add_argument("--city", dest="city_filter", default=None)
    parser.add_argument("--start-date", dest="start_date", default=None)
    parser.add_argument("--end-date", dest="end_date", default=None)
    parser.add_argument(
        "--temperature-metric",
        choices=("high", "low", "all"),
        default="high",
        help="Settlement metric to rebuild. Default preserves legacy high-only behavior.",
    )
    parser.add_argument("--apply", action="store_true", help="Write rows. Default is dry-run.")
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db)) if args.db else get_world_connection()
    try:
        summary = rebuild_settlements_scoped(
            conn,
            dry_run=not args.apply,
            city_filter=args.city_filter,
            start_date=args.start_date,
            end_date=args.end_date,
            temperature_metric=args.temperature_metric,
        )
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
        print(summary)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
