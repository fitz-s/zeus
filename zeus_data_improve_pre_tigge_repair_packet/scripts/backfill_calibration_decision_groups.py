#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.execution.calibration_group_writer import backfill_calibration_decision_groups


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--since")
    parser.add_argument("--no-update-pairs", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        result = backfill_calibration_decision_groups(
            conn,
            update_pair_rows=not args.no_update_pairs,
            only_since=args.since,
        )
        conn.commit()
    finally:
        conn.close()
    print(result)


if __name__ == "__main__":
    main()
