# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/full_suite_blocker_plan_2026-04-27.md
"""Rebuild high-temperature settlement rows from VERIFIED daily observations.

This repair helper is intentionally narrow: it writes only high-track settlement
rows derived from observations that are already authority='VERIFIED'. It does
not fetch external data, infer provider validity, or authorize live deployment.
Callers own transaction boundaries; dry-run is the default for CLI use.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.contracts.settlement_semantics import SettlementSemantics
from src.state.db import get_world_connection

HIGH_PHYSICAL_QUANTITY = "mx2t6_local_calendar_day_max"
HIGH_OBSERVATION_FIELD = "high_temp"
HIGH_DATA_VERSION = "wu_icao_history_v1"


def _round_high_value(raw_value: float, unit: str, city: str) -> float:
    sem = (
        SettlementSemantics.default_wu_celsius(city)
        if str(unit).upper() == "C"
        else SettlementSemantics.default_wu_fahrenheit(city)
    )
    return sem.assert_settlement_value(raw_value, context="rebuild_settlements")


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
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        try:
            settlement_value = _round_high_value(
                float(row["high_temp"]), str(row["unit"]), str(row["city"])
            )
        except Exception:
            rows_skipped += 1
            continue

        if dry_run:
            rows_written += 1
            continue

        conn.execute(
            """
            INSERT INTO settlements
            (city, target_date, winning_bin, settlement_value, settlement_source, settled_at,
             authority, temperature_metric, physical_quantity, observation_field,
             data_version, provenance_json)
            VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', 'high', ?, ?, ?, ?)
            ON CONFLICT(city, target_date, temperature_metric) DO UPDATE SET
                winning_bin = excluded.winning_bin,
                settlement_value = excluded.settlement_value,
                settlement_source = excluded.settlement_source,
                settled_at = excluded.settled_at,
                authority = excluded.authority,
                physical_quantity = excluded.physical_quantity,
                observation_field = excluded.observation_field,
                data_version = excluded.data_version,
                provenance_json = excluded.provenance_json
            """,
            (
                row["city"],
                row["target_date"],
                f"{int(settlement_value)}°{str(row['unit']).upper()}",
                settlement_value,
                row["source"] or "verified_observation_rebuild",
                now,
                HIGH_PHYSICAL_QUANTITY,
                HIGH_OBSERVATION_FIELD,
                HIGH_DATA_VERSION,
                '{"source":"scripts/rebuild_settlements.py","authority":"VERIFIED"}',
            ),
        )
        rows_written += 1

    return {
        "dry_run": dry_run,
        "city_filter": city_filter,
        "rows_seen": len(rows),
        "rows_written": rows_written,
        "rows_skipped": rows_skipped,
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
