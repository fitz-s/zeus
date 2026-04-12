#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PACKET_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKET_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backfill_probability_traces_from_opportunities import run_backfill


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", help="Accepted for compatibility; canonical script uses the configured shared DB.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_backfill(dry_run=args.dry_run)
    print(result)


if __name__ == "__main__":
    main()
