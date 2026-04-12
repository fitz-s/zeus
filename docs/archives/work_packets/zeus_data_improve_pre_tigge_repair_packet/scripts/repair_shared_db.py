#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PACKET_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKET_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.effective_sample_size import build_decision_groups, write_decision_groups
from src.state.db import init_schema


def apply_migration(conn: sqlite3.Connection, migration_path: Path) -> None:
    init_schema(conn)
    conn.executescript(migration_path.read_text())
    columns = {row[1] for row in conn.execute("PRAGMA table_info(calibration_pairs)")}
    if "decision_group_id" not in columns:
        conn.execute("ALTER TABLE calibration_pairs ADD COLUMN decision_group_id TEXT")
    if "bias_corrected" not in columns:
        conn.execute("ALTER TABLE calibration_pairs ADD COLUMN bias_corrected INTEGER DEFAULT 0")



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--with-day0", action="store_true")
    parser.add_argument("--start-date")
    parser.add_argument("--with-probability", action="store_true")
    parser.add_argument("--with-forecast-error-profile", action="store_true")
    args = parser.parse_args()

    migration_path = PACKET_ROOT / "migrations" / "2026_04_11_pre_tigge_cutover.sql"
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        apply_migration(conn, migration_path)
        summary = {}
        groups = build_decision_groups(conn)
        summary["calibration_groups"] = {
            "groups_built": len(groups),
            "groups_written": write_decision_groups(
                conn,
                groups,
                recorded_at=datetime.now(timezone.utc).isoformat(),
                update_pair_rows=True,
            ),
        }
        if args.with_probability:
            summary["probability_traces"] = {
                "status": "skipped",
                "reason": "Use repo script `scripts/backfill_probability_traces_from_opportunities.py` against the canonical shared DB.",
            }
        conn.commit()
    finally:
        conn.close()

    if args.with_day0:
        from src.signal.day0_residual import build_day0_residual_facts, write_day0_residual_facts

        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        try:
            facts = build_day0_residual_facts(conn)
            write_day0_residual_facts(
                conn,
                facts,
                recorded_at=datetime.now(timezone.utc).isoformat(),
            )
            conn.commit()
        finally:
            conn.close()

    if args.with_forecast_error_profile:
        summary["forecast_error_profile"] = {
            "status": "skipped",
            "reason": "Use repo-native `src.signal.forecast_error_distribution` materialization, not packet-local script.",
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("repair_shared_db complete")


if __name__ == "__main__":
    main()
