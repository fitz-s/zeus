# Created: 2026-04-27
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

HIGH_PHYSICAL_QUANTITY = "mx2t6_local_calendar_day_max"
HIGH_OBSERVATION_FIELD = "high_temp"
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


def _round_high_value(row: sqlite3.Row, conn: sqlite3.Connection) -> tuple[float, Any]:
    city = _city_for_observation(row)
    _validate_source_family(row, city)
    converted_value = validate_observation_for_settlement(dict(row), city, conn)
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

    conn.row_factory = sqlite3.Row
    where = "authority = 'VERIFIED' AND high_temp IS NOT NULL"
    params: list[Any] = []
    if city_filter:
        where += " AND city = ?"
        params.append(city_filter)

    rows = conn.execute(
        f"""
        SELECT city, target_date, source, high_temp, unit, authority
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
            settlement_value, city = _round_high_value(row, conn)
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
            VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', 'high', ?, ?, ?, ?, ?, ?)
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
                HIGH_PHYSICAL_QUANTITY,
                HIGH_OBSERVATION_FIELD,
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
        "rows_seen": len(rows),
        "rows_written": rows_written,
        "rows_skipped": rows_skipped,
        "rows_skipped_by_reason": dict(rows_skipped_by_reason),
        "unverified_ignored": unverified_ignored,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="World DB path; defaults to configured world DB")
    parser.add_argument("--city", dest="city_filter", default=None)
    parser.add_argument("--apply", action="store_true", help="Write rows. Default is dry-run.")
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db)) if args.db else get_world_connection()
    try:
        summary = rebuild_settlements(
            conn,
            dry_run=not args.apply,
            city_filter=args.city_filter,
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
