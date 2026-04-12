#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PACKET_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKET_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.blocked_oos import evaluate_blocked_oos_calibration


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--model-name", default="extended_platt_v_current")
    parser.add_argument("--train-start", required=True)
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--test-start", required=True)
    parser.add_argument("--test-end", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        result = evaluate_blocked_oos_calibration(
            conn,
            train_start=args.train_start,
            train_end=args.train_end,
            test_start=args.test_start,
            test_end=args.test_end,
            model_name=args.model_name,
            write=args.write,
        )
        if args.write:
            conn.commit()
    finally:
        conn.close()
    print(result)


if __name__ == "__main__":
    main()
