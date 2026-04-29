#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round3_verdict.md §1 #2 + ULTIMATE_PLAN.md L305-308
# (silent attribution drift detector). ATTRIBUTION_DRIFT packet BATCH 3.
"""Weekly ATTRIBUTION_DRIFT runner.

CLI wrapper that wires src.state.attribution_drift BATCH 1 + BATCH 2:

  1. Compute per-position attribution-drift verdicts over a configurable
     window via detect_drifts_in_window (BATCH 1).
  2. Aggregate into per-strategy drift_rate via
     compute_drift_rate_per_strategy (BATCH 2).
  3. Emit a structured JSON report with per-strategy drift_rate + counts +
     per-position drift evidence (so operator can audit each detected drift).

K1 compliance:
  - Read-only DB access via state path discovery.
  - JSON output is derived context (operator/ops evidence), NOT authority.
  - No mutation of any canonical surface.
  - Per round3_verdict.md §1 #4 + boot §6 #4: manual run only — operator
    decides cron / launchd wiring later.

Mirrors scripts/edge_observation_weekly.py shape (same flag set, same
output-dir convention) so operators learn one runner pattern.

Usage:
    python3 scripts/attribution_drift_weekly.py
    python3 scripts/attribution_drift_weekly.py --end-date 2026-04-28
    python3 scripts/attribution_drift_weekly.py --window-days 14
    python3 scripts/attribution_drift_weekly.py --drift-rate-threshold 0.1
    python3 scripts/attribution_drift_weekly.py --report-out /tmp/x.json
    python3 scripts/attribution_drift_weekly.py --db-path state/zeus-shared.db

Default report path:
    docs/operations/attribution_drift/weekly_<YYYY-MM-DD>.json

Exit code: 0 if no strategy's drift_rate exceeds --drift-rate-threshold
(default 0.05); 1 if at least one strategy exceeds (cron-friendly).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
# LOW-OPERATIONAL-WP-3-1 fix: ensure `from src.state.X import ...` works when
# this script is invoked as `python3 scripts/attribution_drift_weekly.py` from
# any cwd, without requiring PYTHONPATH=. or `python -m scripts.X`.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "operations" / "attribution_drift"
DEFAULT_DB_PATH = REPO_ROOT / "state" / "zeus-shared.db"
DEFAULT_DRIFT_RATE_THRESHOLD = 0.05   # per boot §6 #3 + dispatch GO_BATCH_1 default 3


def _resolve_end_date(end_date_str: str | None):
    if end_date_str is None:
        return datetime.now(timezone.utc).date()
    return datetime.strptime(end_date_str, "%Y-%m-%d").date()


def _verdict_to_dict(v: Any) -> dict:
    """Serialize an AttributionVerdict (dataclass-of-dataclass) to plain dict
    suitable for JSON. Defensive against non-dataclass shapes."""
    if is_dataclass(v):
        return asdict(v)
    return dict(v)


def run_weekly(
    db_path: Path,
    end_date,
    window_days: int = 7,
) -> dict[str, Any]:
    """Compute the weekly attribution-drift report.

    Returns a JSON-serializable dict with per-strategy drift_rate + counts
    AND per-position drift_detected verdicts (for operator audit of each
    individual detected drift). insufficient_signal positions are surfaced
    via the per-strategy n_insufficient counts but their per-position
    detail is suppressed (would dominate the report by volume; operators
    can re-run the BATCH 1 detector directly if needed).
    """
    from src.state.attribution_drift import (
        compute_drift_rate_per_strategy,
        detect_drifts_in_window,
    )

    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        per_strategy = compute_drift_rate_per_strategy(
            conn, window_days=window_days, end_date=end_date.isoformat(),
        )
        verdicts = detect_drifts_in_window(
            conn, window_days=window_days, end_date=end_date.isoformat(),
        )
    finally:
        conn.close()

    drift_positions = [
        _verdict_to_dict(v) for v in verdicts if v.kind == "drift_detected"
    ]

    return {
        "report_kind": "attribution_drift_weekly",
        "report_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "end_date": end_date.isoformat(),
        "window_days": window_days,
        "db_path": str(db_path.relative_to(REPO_ROOT)) if str(db_path).startswith(str(REPO_ROOT)) else str(db_path),
        "per_strategy": per_strategy,
        "drift_positions": drift_positions,
    }


def _resolve_report_path(arg: str | None, end_date) -> Path:
    if arg:
        return Path(arg)
    DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_REPORT_DIR / f"weekly_{end_date.isoformat()}.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end-date", help="Inclusive end day of the current window (YYYY-MM-DD); default today UTC")
    ap.add_argument("--window-days", type=int, default=7, help="Window length in calendar days (default 7)")
    ap.add_argument("--drift-rate-threshold", type=float, default=DEFAULT_DRIFT_RATE_THRESHOLD,
                    help=f"Exit 1 when any strategy's drift_rate exceeds this value (default {DEFAULT_DRIFT_RATE_THRESHOLD})")
    ap.add_argument("--db-path", help=f"Path to Zeus state DB (default {DEFAULT_DB_PATH})")
    ap.add_argument("--report-out", help="Path to write JSON report (default docs/operations/attribution_drift/weekly_<date>.json)")
    ap.add_argument("--stdout", action="store_true", help="Also print the JSON to stdout")
    args = ap.parse_args(argv)

    end_date = _resolve_end_date(args.end_date)
    db_path = Path(args.db_path) if args.db_path else DEFAULT_DB_PATH
    report = run_weekly(db_path, end_date, window_days=args.window_days)
    out_path = _resolve_report_path(args.report_out, end_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n")

    print(f"wrote: {out_path}")
    if args.stdout:
        print(json.dumps(report, indent=2, default=str))

    # Per-strategy summary line for operator scanning (mirror EO style).
    any_exceeds = False
    for sk, rec in report["per_strategy"].items():
        rate = rec["drift_rate"]
        rate_str = f"{rate:.3f}" if rate is not None else "n/a"
        n_dec = rec["n_decidable"]
        n_drift = rec["n_drift"]
        n_ins = rec["n_insufficient"]
        sq = rec["sample_quality"]
        flag = ""
        if rate is not None and rate > args.drift_rate_threshold:
            flag = f"  EXCEEDS {args.drift_rate_threshold}"
            any_exceeds = True
        print(f"  {sk}: drift_rate={rate_str} drift={n_drift}/{n_dec} insufficient={n_ins} q={sq}{flag}")

    return 1 if any_exceeds else 0


if __name__ == "__main__":
    sys.exit(main())
