#!/usr/bin/env python3
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Advisory structural audit of scheduled jobs (fast-executor DB writers, OpenData multi-owner).
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR3);
#   operator spec §"Top 10 efficiency blockers" + §4; src/data/source_job_registry.py.
"""Data-collection efficiency audit — PR3 (advisory, read-only).

Surfaces STRUCTURAL faults from the job registry (no runtime change):

  1. fast_executor_db_writer  — a DB-writing job on the file-only 'fast' executor
     (starves heartbeats behind the single-writer lock). Today: ingest_uma_resolution_listener.
  2. opendata_multi_owner     — OpenData live producers in BOTH daemons would double-produce;
     ownership must be a runtime singleton (PR4 enforces). Registry-level detection here.
  3. unregistered_scheduled   — delegated to data_collection_inventory --check.

    python3 scripts/data_collection_efficiency_audit.py            # report (advisory, exit 0)
    python3 scripts/data_collection_efficiency_audit.py --blocking # exit 1 if any fault found
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.source_job_registry import (  # noqa: E402
    fast_executor_db_writers,
    opendata_owners,
)


def run_audit() -> list[str]:
    faults: list[str] = []

    for j in fast_executor_db_writers():
        faults.append(
            f"[fast_executor_db_writer] {j.job_id} ({j.owner_daemon}): writes_db on the file-only "
            f"'fast' executor — {j.notes or 'move DB write off fast (PR8)'}"
        )

    owners = opendata_owners()
    owners_by_daemon = {j.owner_daemon for j in owners}
    if len(owners_by_daemon) > 1:
        faults.append(
            "[opendata_multi_owner] OpenData live producers declared in multiple daemons "
            f"({sorted(owners_by_daemon)}); runtime ownership must be a singleton (PR4 enforces). "
            "NOTE: registration is env-gated (ZEUS_FORECAST_LIVE_OWNER) so only one is active at "
            "runtime — this is a registry-visibility note, not a proven double-production."
        )

    return faults


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Data-collection efficiency audit (advisory).")
    p.add_argument("--blocking", action="store_true", help="exit 1 if any structural fault found")
    args = p.parse_args(argv)

    faults = run_audit()
    if not faults:
        print("data_collection_efficiency_audit: OK — no structural faults.")
        return 0

    print(f"data_collection_efficiency_audit: {len(faults)} structural fault(s):")
    for f in faults:
        print(f"  {f}")
    return 1 if args.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
