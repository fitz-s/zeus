#!/usr/bin/env python3
"""Repair HKO observation_instants rows to the live runtime-monitoring role.

HKO observations are live monitor/settlement evidence for Hong Kong, but are
not calibration-training rows.  Older writer semantics persisted them as
coverage_fill_evidence / REQUIRES_SOURCE_REAUDIT, which makes Day0 and monitor
readers ignore the canonical HK source.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import STATE_DIR


DEFAULT_DB = STATE_DIR / "zeus-world.db"


def repair_hko_runtime_monitoring_observations(
    conn: sqlite3.Connection,
    *,
    apply: bool = False,
) -> dict[str, object]:
    predicate = """
        city = 'Hong Kong'
        AND source = 'hko_hourly_accumulator'
        AND UPPER(COALESCE(authority, '')) = 'ICAO_STATION_NATIVE'
        AND (
            COALESCE(source_role, '') != 'runtime_monitoring'
            OR COALESCE(training_allowed, 1) != 0
            OR COALESCE(causality_status, '') != 'OK'
        )
    """
    sample_rows = conn.execute(
        f"""
        SELECT id, target_date, utc_timestamp, source_role, training_allowed, causality_status
          FROM observation_instants
         WHERE {predicate}
         ORDER BY target_date DESC, utc_timestamp DESC
         LIMIT 10
        """
    ).fetchall()
    count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM observation_instants WHERE {predicate}"
        ).fetchone()[0]
        or 0
    )
    result: dict[str, object] = {
        "dry_run": not apply,
        "candidates_found": count,
        "sample": [
            {
                "id": row[0],
                "target_date": row[1],
                "utc_timestamp": row[2],
                "source_role": row[3],
                "training_allowed": row[4],
                "causality_status": row[5],
            }
            for row in sample_rows
        ],
    }
    if not apply or count == 0:
        result["rows_updated"] = 0
        return result
    cur = conn.execute(
        f"""
        UPDATE observation_instants
           SET source_role = 'runtime_monitoring',
               training_allowed = 0,
               causality_status = 'OK'
         WHERE {predicate}
        """
    )
    conn.commit()
    result["rows_updated"] = int(cur.rowcount if cur.rowcount is not None else 0)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        with db_writer_lock(args.db, WriteClass.BULK):
            conn = sqlite3.connect(str(args.db), timeout=30.0)
            conn.execute("PRAGMA busy_timeout = 30000")
            try:
                result = repair_hko_runtime_monitoring_observations(conn, apply=True)
            finally:
                conn.close()
    else:
        conn = sqlite3.connect(str(args.db), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            result = repair_hko_runtime_monitoring_observations(conn, apply=False)
        finally:
            conn.close()
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
