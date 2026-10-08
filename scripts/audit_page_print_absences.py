#!/usr/bin/env python3
# Created: 2026-10-08
# Last audited: 2026-10-08
# Authority basis: G10 (task_2026-10-06_fast_obs_closure): page-row deletion was
#   unobservable because the ledger stores rows, not fetch membership. The intraday
#   noaa_wrh tick now tags a held page clock that a covering fetch omitted.
"""Read-only: page-print absences per station and unit, from world fact_revocations."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.state.fact_revocation import OBSERVATION_PRINTS_TABLE, REASON_PAGE_PRINT_ABSENT  # noqa: E402

QUERY = """
SELECT json_extract(r.meta_json, '$.station_id') AS station, p.unit AS unit,
       COUNT(*) AS absences, MIN(json_extract(r.meta_json, '$.publish_ts_utc')) AS first_clock,
       MAX(json_extract(r.meta_json, '$.publish_ts_utc')) AS last_clock
  FROM fact_revocations r
  JOIN observation_prints p ON p.id = CAST(r.row_id AS INTEGER)
 WHERE r.reason_code = ? AND r.table_name = ? AND r.recorded_at >= ?
 GROUP BY station, unit
 ORDER BY absences DESC, station
 LIMIT 500
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-db", default=str(REPO / "state" / "zeus-world.db"))
    parser.add_argument("--since", default="1970-01-01", help="recorded_at lower bound (UTC ISO)")
    args = parser.parse_args(argv)
    conn = sqlite3.connect(f"file:{Path(args.world_db).resolve()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(QUERY, (REASON_PAGE_PRINT_ABSENT, OBSERVATION_PRINTS_TABLE, args.since)).fetchall()
    finally:
        conn.close()
    print("station\tunit\tabsences\tfirst_clock\tlast_clock")
    for row in rows:
        print("\t".join("" if value is None else str(value) for value in row))
    print(f"total\t\t{sum(row[2] for row in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
