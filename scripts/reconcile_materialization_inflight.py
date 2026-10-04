#!/usr/bin/env python3
# Lifecycle: created=2026-10-03; last_reviewed=2026-10-03; last_reused=2026-10-03
# Purpose: Quiescent inflight reconcile for the execution-lease protocol migration and rollback.
# Reuse: Run only with the forecast-live daemon stopped; see the migration doc.
"""Return inflight materialization requests to requests/ when no owner remains.

Dry-run by default; ``--apply`` restores. Exit 0 when the queue is quiescent
(nothing refused), 3 when a live or unknown owner was refused and left in place.
Procedure: docs/operations/current/plans/canonical_execution_lease_migration.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_live_materialization_queue import (  # noqa: E402
    reconcile_inflight_for_migration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--request-dir", type=Path,
        default=ROOT / "state" / "replacement_forecast_live" / "requests",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    report = reconcile_inflight_for_migration(request_path=args.request_dir, apply=args.apply)
    print(json.dumps({
        "applied": args.apply,
        "restored": list(report.restored),
        "refused": [list(item) for item in report.refused],
        "held_leases": list(report.held_leases),
        "live_staging": list(report.live_staging),
        "drained_staging": report.drained_staging,
        "unsettled_captures": [list(item) for item in report.unsettled_captures],
        "settled_captures": report.settled_captures,
        "quiescent": report.quiescent,
    }, indent=2))
    return 0 if report.quiescent else 3


if __name__ == "__main__":
    raise SystemExit(main())
