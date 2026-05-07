#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round3_verdict.md §1 #2 (FIRST edge packet) + ULTIMATE_PLAN.md
# L297-301 (weekly drift assertion runner). EDGE_OBSERVATION packet BATCH 3.
"""Weekly EDGE_OBSERVATION drift-assertion runner.

CLI wrapper that wires src.state.edge_observation BATCH 1 + BATCH 2:

  1. Compute realized edge per strategy_key over a configurable window
     (default 7 days = weekly).
  2. Run alpha-decay detection on each strategy by re-running the edge
     computation over a sliding sequence of trailing windows.
  3. Emit a structured JSON report with per-strategy verdict + evidence.

K1 compliance:
  - Read-only DB access via state path discovery.
  - JSON output is derived context (operator/ops evidence), NOT authority.
  - No mutation of any canonical surface.
  - Per round3_verdict.md §1 #4 + boot §6 #4: manual run only — operator
    decides cron / launchd wiring later (this packet does not modify any
    automation surfaces).

Usage:
    python3 scripts/edge_observation_weekly.py
    python3 scripts/edge_observation_weekly.py --end-date 2026-04-28
    python3 scripts/edge_observation_weekly.py --window-days 14
    python3 scripts/edge_observation_weekly.py --report-out /tmp/x.json
    python3 scripts/edge_observation_weekly.py --db-path state/zeus-shared.db

Default report path:
    docs/operations/edge_observation/weekly_<YYYY-MM-DD>.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
# LOW-OPERATIONAL-WP-3-1 fix: ensure `from src.state.X import ...` works when
# this script is invoked as `python3 scripts/edge_observation_weekly.py` from
# any cwd, without requiring PYTHONPATH=. or `python -m scripts.X`.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "operations" / "edge_observation"
DEFAULT_DB_PATH = REPO_ROOT / "state" / "zeus-shared.db"
DEFAULT_TRAILING_WINDOWS = 4   # 1 current + 3 trailing per detect_alpha_decay min_windows


def _resolve_end_date(end_date_str: str | None) -> date:
    if end_date_str is None:
        return datetime.now(timezone.utc).date()
    return datetime.strptime(end_date_str, "%Y-%m-%d").date()


def _build_edge_history(
    conn: sqlite3.Connection,
    strategy_key: str,
    end_date: date,
    window_days: int,
    n_windows: int,
) -> list[dict[str, Any]]:
    """Build chronological per-window edge history for ONE strategy_key.

    Re-runs compute_realized_edge_per_strategy n_windows times, each with
    end_date shifted back by window_days. Returns oldest → newest list of
    that strategy's window records (the shape detect_alpha_decay expects).
    """
    from src.state.edge_observation import compute_realized_edge_per_strategy
    history: list[dict[str, Any]] = []
    for offset in range(n_windows - 1, -1, -1):
        wend = end_date - timedelta(days=offset * window_days)
        per_strategy = compute_realized_edge_per_strategy(
            conn, window_days=window_days, end_date=wend.isoformat(),
        )
        history.append(per_strategy[strategy_key])
    return history


def run_weekly(
    db_path: Path,
    end_date: date,
    window_days: int = 7,
    n_windows: int = DEFAULT_TRAILING_WINDOWS,
) -> dict[str, Any]:
    """Compute the weekly drift-assertion report.

    Returns a JSON-serializable dict with per-strategy current-window
    snapshot + per-strategy DriftVerdict.
    """
    from src.state.edge_observation import (
        STRATEGY_KEYS,
        compute_realized_edge_per_strategy,
        detect_alpha_decay,
    )

    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    with db_writer_lock(db_path, WriteClass.BULK):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Current window snapshot.
            snapshot = compute_realized_edge_per_strategy(
                conn, window_days=window_days, end_date=end_date.isoformat(),
            )
            # Per-strategy decay detection over n_windows of edge history.
            verdicts: dict[str, dict[str, Any]] = {}
            for sk in STRATEGY_KEYS:
                history = _build_edge_history(conn, sk, end_date, window_days, n_windows)
                v = detect_alpha_decay(history, sk)
                verdicts[sk] = {
                    "kind": v.kind,
                    "severity": v.severity,
                    "evidence": v.evidence,
                }
        finally:
            conn.close()

    return {
        "report_kind": "edge_observation_weekly",
        "report_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "end_date": end_date.isoformat(),
        "window_days": window_days,
        "n_windows_for_decay": n_windows,
        "db_path": str(db_path.relative_to(REPO_ROOT)) if str(db_path).startswith(str(REPO_ROOT)) else str(db_path),
        "current_window": snapshot,
        "decay_verdicts": verdicts,
    }


def _resolve_report_path(arg: str | None, end_date: date) -> Path:
    if arg:
        return Path(arg)
    DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_REPORT_DIR / f"weekly_{end_date.isoformat()}.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end-date", help="Inclusive end day of the current window (YYYY-MM-DD); default today UTC")
    ap.add_argument("--window-days", type=int, default=7, help="Window length in calendar days (default 7)")
    ap.add_argument("--n-windows", type=int, default=DEFAULT_TRAILING_WINDOWS,
                    help="Number of windows in the decay-detection history (default 4)")
    ap.add_argument("--db-path", help=f"Path to Zeus state DB (default {DEFAULT_DB_PATH})")
    ap.add_argument("--report-out", help="Path to write JSON report (default docs/operations/edge_observation/weekly_<date>.json)")
    ap.add_argument("--stdout", action="store_true", help="Also print the JSON to stdout")
    args = ap.parse_args(argv)

    end_date = _resolve_end_date(args.end_date)
    db_path = Path(args.db_path) if args.db_path else DEFAULT_DB_PATH
    report = run_weekly(db_path, end_date, window_days=args.window_days, n_windows=args.n_windows)
    out_path = _resolve_report_path(args.report_out, end_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n")

    print(f"wrote: {out_path}")
    if args.stdout:
        print(json.dumps(report, indent=2, default=str))

    # Per-strategy summary line for operator scanning.
    for sk, v in report["decay_verdicts"].items():
        snap = report["current_window"][sk]
        edge_str = f"{snap['edge_realized']:.4f}" if snap.get("edge_realized") is not None else "n/a"
        sev = f" {v['severity']}" if v.get("severity") else ""
        print(f"  {sk}: edge={edge_str} n={snap['n_trades']} q={snap['sample_quality']} → {v['kind']}{sev}")

    # Exit non-zero if ANY strategy has alpha_decay_detected — useful for cron.
    any_decay = any(v["kind"] == "alpha_decay_detected" for v in report["decay_verdicts"].values())
    return 1 if any_decay else 0


if __name__ == "__main__":
    sys.exit(main())
