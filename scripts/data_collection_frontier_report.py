#!/usr/bin/env python3
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Operator read-only data-collection temporal frontier report.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §10;
#   docs/operations/current/plans/data_temporal_kernel/PLAN.md; src/data/collection_frontier.py.
"""Operator-facing data-collection frontier report — PR2 of the Data Temporal Kernel program.

READ-ONLY. Renders the in-memory frontier (src/data/collection_frontier.compute_frontier)
as a table or JSON, or explains a single source's live blocker. Writes nothing.

    python3 scripts/data_collection_frontier_report.py --role live --table
    python3 scripts/data_collection_frontier_report.py --role live --json
    python3 scripts/data_collection_frontier_report.py --source ecmwf_open_data --explain
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.collection_frontier import FrontierRow, compute_frontier  # noqa: E402


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _row_to_dict(r: FrontierRow) -> dict[str, Any]:
    return {
        "source_id": r.source_id,
        "track": r.track,
        "calendar_id": r.calendar_id,
        "role": r.role,
        "target_local_date": r.target_local_date,
        "source_issue_time": _iso(r.source_issue_time),
        "source_release_time": _iso(r.source_release_time),
        "safe_fetch_not_before": _iso(r.safe_fetch_not_before),
        "latest_attempt_at": _iso(r.latest_attempt_at),
        "latest_success_at": _iso(r.latest_success_at),
        "captured_at": _iso(r.captured_at),
        "imported_at": _iso(r.imported_at),
        "completeness_status": r.completeness_status,
        "readiness_status": r.readiness_status,
        "readiness_expires_at": _iso(r.readiness_expires_at),
        "freshness_state": r.freshness_state,
        "freshness_age_seconds": r.freshness_age_seconds,
        "live_blocker": r.live_blocker,
        "operator_action": r.operator_action,
        "health_consecutive_failures": r.health_consecutive_failures,
        "health_last_success_at": _iso(r.health_last_success_at),
        "health_degraded_since": _iso(r.health_degraded_since),
    }


def _fmt_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    h = seconds / 3600.0
    return f"{h:.1f}h"


def _render_table(rows: list[FrontierRow]) -> str:
    header = f"{'SOURCE':28} {'TRACK':14} {'ROLE':9} {'FRESH':9} {'AGE':>7} {'BLOCKER':20} ACTION"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.source_id:28.28} {r.track:14.14} {r.role:9} {r.freshness_state:9} "
            f"{_fmt_age(r.freshness_age_seconds):>7} {r.live_blocker:20} {r.operator_action}"
        )
    return "\n".join(lines)


def _render_explain(rows: list[FrontierRow], source: str) -> str:
    matches = [r for r in rows if r.source_id == source or r.calendar_id == source]
    if not matches:
        return f"no frontier row for source/calendar_id {source!r}"
    out = []
    for r in matches:
        d = _row_to_dict(r)
        out.append(f"source: {r.source_id}  track: {r.track}  ({r.calendar_id})")
        for k in (
            "role", "target_local_date", "source_issue_time", "source_release_time",
            "safe_fetch_not_before", "latest_success_at", "captured_at", "imported_at",
            "completeness_status", "readiness_status", "readiness_expires_at",
            "freshness_state", "freshness_age_seconds", "health_consecutive_failures",
        ):
            out.append(f"  {k}: {d[k]}")
        out.append(f"  >>> live_blocker: {r.live_blocker}")
        out.append(f"  >>> operator_action: {r.operator_action}")
        out.append("")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Data-collection frontier report (read-only).")
    parser.add_argument("--role", choices=["live", "backfill", "shadow"], default=None)
    parser.add_argument("--source", default=None, help="filter/explain a single source_id or calendar_id")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--table", action="store_true", help="emit a table (default)")
    parser.add_argument("--explain", action="store_true", help="explain a single source's blocker")
    args = parser.parse_args(argv)

    rows = compute_frontier(role_filter=args.role)
    if args.source and not args.explain:
        rows = [r for r in rows if r.source_id == args.source or r.calendar_id == args.source]

    if args.explain:
        if not args.source:
            parser.error("--explain requires --source")
        print(_render_explain(rows, args.source))
        return 0

    if args.json:
        print(json.dumps([_row_to_dict(r) for r in rows], indent=2))
        return 0

    print(_render_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
