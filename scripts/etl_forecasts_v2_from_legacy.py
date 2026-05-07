"""ETL: legacy `forecasts` → `historical_forecasts_v2` (metric-parametrized).

Created: 2026-04-21
Last reused/audited: 2026-04-21
Authority basis: Gate F Step 2 — docs/operations/task_2026-04-21_gate_f_data_backfill/step1_schema_audit.md (Gap B) + step1b_source_validity.md (P1 v2-writer missing).

## What

Reads each row from legacy `forecasts` (columns: forecast_high, forecast_low
— two metrics per row) and writes two metric-partitioned rows into
`historical_forecasts_v2`:

- one with temperature_metric='high', forecast_value=forecast_high
- one with temperature_metric='low', forecast_value=forecast_low

Rows where a metric value is NULL are skipped for that metric only (the
other metric still lands). Authority is derived from the source tag per
the authority matrix below.

## Why a separate ETL (not double-write in the appender)

Per step1b audit recommendation: keep the ingestion path single-writer
(legacy-only) and make v2 a DERIVED table via ETL hop. Parallels the
Phase 7A/7B pattern for observations. This lets us rerun v2 population
idempotently without worrying about appender concurrency.

## Authority matrix

| Source tag | Authority |
|---|---|
| `openmeteo_previous_runs`, `gfs_previous_runs`, `ecmwf_previous_runs`, `icon_previous_runs`, `ukmo_previous_runs` | VERIFIED |
| (everything else) | UNVERIFIED |

Dead-source authority is handled at ingest time; this ETL inherits from
the legacy row's source tag without re-probe.

## data_version

``v1.legacy-forecasts-backfill`` — distinguishes these rows from rows
written by a future direct v2 writer (which would use ``v2.native`` or
similar). Lets consumers filter by provenance generation.

## Idempotence

UNIQUE(city, target_date, source, temperature_metric, lead_days) on the
v2 table. INSERT OR REPLACE means reruns over the same source data
produce the same v2 rows with refreshed `recorded_at`. Safe to rerun.

## Usage

    .venv/bin/python scripts/etl_forecasts_v2_from_legacy.py           # dry run
    .venv/bin/python scripts/etl_forecasts_v2_from_legacy.py --apply
    .venv/bin/python scripts/etl_forecasts_v2_from_legacy.py --apply --batch 5000
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection  # noqa: E402
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402
from src.state.schema.v2_schema import apply_v2_schema  # noqa: E402

logger = logging.getLogger(__name__)


VERIFIED_SOURCES: frozenset[str] = frozenset({
    "openmeteo_previous_runs",
    "gfs_previous_runs",
    "ecmwf_previous_runs",
    "icon_previous_runs",
    "ukmo_previous_runs",
})


DATA_VERSION = "v1.legacy-forecasts-backfill"


def _authority_for(source: str) -> str:
    """VERIFIED if source is in the approved NWP set; UNVERIFIED otherwise."""
    return "VERIFIED" if source in VERIFIED_SOURCES else "UNVERIFIED"


def _provenance_for(row: sqlite3.Row) -> str:
    """Compact JSON capturing the legacy row's lineage."""
    payload = {
        "legacy_forecasts_id": row["id"],
        "forecast_basis_date": row["forecast_basis_date"],
        "forecast_issue_time": row["forecast_issue_time"],
        "retrieved_at": row["retrieved_at"],
        "imported_at": row["imported_at"],
    }
    return json.dumps(payload, default=str, separators=(",", ":"))


def _count_legacy(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()
    return int(row[0])


def _count_v2(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM historical_forecasts_v2").fetchone()
    return int(row[0])


def run_etl(
    conn: sqlite3.Connection,
    *,
    apply: bool,
    batch: int = 5000,
) -> dict:
    """Emit metric-partitioned v2 rows from the legacy `forecasts` table.

    Returns a summary dict with before/after counts and classifications.
    Does not commit when apply=False (dry run via ROLLBACK).
    """
    apply_v2_schema(conn)  # ensure v2 columns exist; idempotent
    conn.row_factory = sqlite3.Row

    before = _count_legacy(conn)
    v2_before = _count_v2(conn)

    written_high = 0
    written_low = 0
    skipped_null_both = 0
    skipped_null_high = 0
    skipped_null_low = 0
    unknown_unit = 0

    cur = conn.execute(
        "SELECT id, city, target_date, source, forecast_basis_date, "
        "forecast_issue_time, lead_days, lead_time_hours, forecast_high, "
        "forecast_low, temp_unit, retrieved_at, imported_at "
        "FROM forecasts ORDER BY id"
    )

    pending: list[tuple] = []
    INSERT_SQL = (
        "INSERT OR REPLACE INTO historical_forecasts_v2 "
        "(city, target_date, source, temperature_metric, forecast_value, "
        " temp_unit, lead_days, available_at, authority, data_version, "
        " provenance_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    for row in cur:
        high = row["forecast_high"]
        low = row["forecast_low"]
        if high is None and low is None:
            skipped_null_both += 1
            continue

        authority = _authority_for(row["source"])
        provenance = _provenance_for(row)
        available_at = row["retrieved_at"] or row["imported_at"]
        temp_unit = row["temp_unit"]
        if temp_unit not in ("F", "C"):
            unknown_unit += 1

        common = (
            row["city"], row["target_date"], row["source"],
        )

        if high is not None:
            pending.append(common + (
                "high", float(high),
                temp_unit, row["lead_days"], available_at,
                authority, DATA_VERSION, provenance,
            ))
            written_high += 1
        else:
            skipped_null_high += 1

        if low is not None:
            pending.append(common + (
                "low", float(low),
                temp_unit, row["lead_days"], available_at,
                authority, DATA_VERSION, provenance,
            ))
            written_low += 1
        else:
            skipped_null_low += 1

        if len(pending) >= batch:
            conn.executemany(INSERT_SQL, pending)
            pending.clear()

    if pending:
        conn.executemany(INSERT_SQL, pending)
        pending.clear()

    if apply:
        conn.commit()
    else:
        conn.rollback()

    v2_after = _count_v2(conn) if apply else v2_before + written_high + written_low

    return {
        "apply": apply,
        "legacy_forecasts_rows": before,
        "v2_rows_before": v2_before,
        "v2_rows_after": v2_after,
        "written_high": written_high,
        "written_low": written_low,
        "skipped_null_both": skipped_null_both,
        "skipped_null_high_only": skipped_null_high,
        "skipped_null_low_only": skipped_null_low,
        "unknown_unit_rows": unknown_unit,
    }


def _print_summary(summary: dict) -> None:
    print("=== ETL forecasts → historical_forecasts_v2 summary ===")
    for key, val in summary.items():
        print(f"  {key}: {val}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--apply", action="store_true", help="Commit writes; default is dry run.")
    parser.add_argument("--batch", type=int, default=5000, help="Rows per executemany batch.")
    parser.add_argument("--db", default=None, help="Optional path to world DB.")
    args = parser.parse_args()

    from src.state.db import ZEUS_WORLD_DB_PATH  # noqa: PLC0415
    _lock_path = Path(args.db) if args.db else ZEUS_WORLD_DB_PATH
    with db_writer_lock(_lock_path, WriteClass.BULK):
        if args.db:
            conn = sqlite3.connect(args.db)
        else:
            conn = get_world_connection(write_class="bulk")
        try:
            summary = run_etl(conn, apply=args.apply, batch=args.batch)
        finally:
            conn.close()

    _print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
