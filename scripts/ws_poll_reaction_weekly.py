#!/usr/bin/env python3
# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: round3_verdict.md §1 #2 (R3 §3 weeks 5-12 third leg) +
# ULTIMATE_PLAN.md L312-314 (reactive WS during opening-inertia + shoulder-bin).
# WS_OR_POLL_TIGHTENING packet BATCH 3 (FINAL).
"""Weekly WS_OR_POLL_TIGHTENING runner.

CLI wrapper that wires src.state.ws_poll_reaction BATCH 1 + BATCH 2:

  1. Compute per-strategy reaction-latency snapshot for the current window
     via compute_reaction_latency_per_strategy (BATCH 1, PATH A latency-only).
  2. Build per-strategy per-window history by re-running BATCH 1 across a
     trailing sequence of windows (mirror of edge_observation_weekly).
  3. Run detect_reaction_gap (BATCH 2 ratio test) per strategy with a
     PER-STRATEGY threshold dict (opening_inertia gets a tighter multiplier
     because alpha decay is fastest there per AGENTS.md L114-126).
  4. Emit a structured JSON report with per-strategy snapshot + per-strategy
     gap verdict + a lightweight negative_latency_count surface (cycle-22
     LOW caveat carry-forward — operator gets visibility on upstream-clipping
     events without the detector ever silently swallowing them).

K1 compliance:
  - Read-only DB access.
  - JSON output is derived context (operator/ops evidence), NOT authority.
  - No mutation of any canonical surface.
  - Per round3_verdict.md §1 #4 + boot §6 #4: manual run only — operator
    decides cron / launchd wiring later.

Per-strategy threshold defaults (LOW-DESIGN-WP-2-2, critic 24th cycle):

    opening_inertia:    1.2  (alpha decay fastest — bot scanning; tight)
    shoulder_sell:      1.4  (moderate — competition narrows)
    center_buy:         1.5  (default ratio multiplier)
    settlement_capture: 1.5  (default — settlement timing is structurally
                              outcome-determined, not WS-reaction-bound)

Operator can override per-strategy threshold via repeated
--override-strategy KEY=VALUE flags, e.g.:
    --override-strategy opening_inertia=1.1
    --override-strategy shoulder_sell=1.3

Mirrors scripts/edge_observation_weekly.py + scripts/attribution_drift_weekly.py
shape (same flag style, same output-dir convention) so operators learn one
runner pattern.

Usage:
    python3 scripts/ws_poll_reaction_weekly.py
    python3 scripts/ws_poll_reaction_weekly.py --end-date 2026-04-28
    python3 scripts/ws_poll_reaction_weekly.py --window-days 7 --n-windows 6
    python3 scripts/ws_poll_reaction_weekly.py --critical-ratio-cutoff 2.5
    python3 scripts/ws_poll_reaction_weekly.py --override-strategy opening_inertia=1.1
    python3 scripts/ws_poll_reaction_weekly.py --report-out /tmp/wp.json --stdout
    python3 scripts/ws_poll_reaction_weekly.py --db-path state/zeus-shared.db

Default report path:
    docs/operations/ws_poll_reaction/weekly_<YYYY-MM-DD>.json

Exit code: 0 if no strategy is gap_detected; 1 if at least one strategy
has gap_detected (cron-friendly).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
# LOW-OPERATIONAL-WP-3-1 fix: ensure `from src.state.X import ...` works when
# this script is invoked as `python3 scripts/ws_poll_reaction_weekly.py` from
# any cwd, without requiring PYTHONPATH=. or `python -m scripts.X`.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "operations" / "ws_poll_reaction"
DEFAULT_DB_PATH = REPO_ROOT / "state" / "zeus-shared.db"
DEFAULT_TRAILING_WINDOWS = 4   # 1 current + 3 trailing per detect_reaction_gap min_windows

# Per-strategy threshold defaults (LOW-DESIGN-WP-2-2 fix).
# Tighter for opening_inertia per AGENTS.md L114-126 + dispatch
# GO_BATCH_3 hint: opening_inertia "alpha decay fastest (bot scanning)".
DEFAULT_PER_STRATEGY_THRESHOLDS: dict[str, float] = {
    "opening_inertia":    1.2,
    "shoulder_sell":      1.4,
    "center_buy":         1.5,
    "settlement_capture": 1.5,
}


def _resolve_end_date(end_date_str: str | None) -> date:
    if end_date_str is None:
        return datetime.now(timezone.utc).date()
    return datetime.strptime(end_date_str, "%Y-%m-%d").date()


def _verdict_to_dict(v: Any) -> dict:
    """Serialize a ReactionGapVerdict (dataclass) to plain dict suitable for JSON."""
    if is_dataclass(v):
        return asdict(v)
    return dict(v)


def _build_latency_history(
    conn: sqlite3.Connection,
    strategy_key: str,
    end_date: date,
    window_days: int,
    n_windows: int,
) -> list[dict[str, Any]]:
    """Build chronological per-window latency history for ONE strategy_key.

    Re-runs compute_reaction_latency_per_strategy n_windows times, each with
    end_date shifted back by window_days. Returns oldest → newest list of
    that strategy's window records (the shape detect_reaction_gap expects).
    """
    from src.state.ws_poll_reaction import compute_reaction_latency_per_strategy
    history: list[dict[str, Any]] = []
    for offset in range(n_windows - 1, -1, -1):
        wend = end_date - timedelta(days=offset * window_days)
        per_strategy = compute_reaction_latency_per_strategy(
            conn, window_days=window_days, end_date=wend.isoformat(),
        )
        history.append(per_strategy[strategy_key])
    return history


def _compute_negative_latency_count(
    conn: sqlite3.Connection,
    end_date: date,
    window_days: int,
) -> int:
    """Count price-tick rows in the current window whose Zeus persist
    timestamp is BEFORE the venue source timestamp (negative latency).

    Surfaced in the report so operator gets visibility on upstream
    clock-skew events even though compute_reaction_latency_per_strategy
    silently clips them to 0 at measurement time. Cycle-22 LOW caveat
    carry-forward: don't hide the count, surface it.

    Read-only K1; same date-window logic as compute_reaction_latency_per_strategy
    but counts the negative-delta rows directly. Returns 0 on parse-failure
    rows (they're excluded from the count, mirroring compute's exclusion).
    """
    from src.state.ws_poll_reaction import _parse_iso_to_ms, _resolve_window
    _ws, _we, window_start_dt, window_end_dt = _resolve_window(window_days, end_date.isoformat())
    window_start_ms = int(window_start_dt.timestamp() * 1000)
    window_end_ms = int(window_end_dt.timestamp() * 1000)

    cur = conn.execute("""
        SELECT DISTINCT
            tpl.token_id,
            tpl.source_timestamp,
            tpl.timestamp AS zeus_timestamp,
            pc.strategy_key
        FROM token_price_log tpl
        JOIN position_current pc ON pc.token_id = tpl.token_id
        WHERE tpl.timestamp IS NOT NULL
          AND pc.strategy_key IS NOT NULL
    """)
    n_negative = 0
    for row in cur.fetchall():
        source_ts = row[1] if not hasattr(row, "keys") else row["source_timestamp"]
        zeus_ts = row[2] if not hasattr(row, "keys") else row["zeus_timestamp"]
        zeus_ms = _parse_iso_to_ms(zeus_ts)
        source_ms = _parse_iso_to_ms(source_ts)
        if zeus_ms is None or source_ms is None:
            continue
        if zeus_ms < window_start_ms or zeus_ms > window_end_ms:
            continue
        if zeus_ms < source_ms:
            n_negative += 1
    return n_negative


def run_weekly(
    db_path: Path,
    end_date: date,
    window_days: int = 7,
    n_windows: int = DEFAULT_TRAILING_WINDOWS,
    per_strategy_thresholds: dict[str, float] | None = None,
    critical_ratio_cutoff: float = 2.0,
) -> dict[str, Any]:
    """Compute the weekly reaction-gap report.

    Returns a JSON-serializable dict with per-strategy current-window
    snapshot + per-strategy ReactionGapVerdict + negative_latency_count.
    """
    from src.state.ws_poll_reaction import (
        STRATEGY_KEYS,
        compute_reaction_latency_per_strategy,
        detect_reaction_gap,
    )

    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    thresholds = dict(DEFAULT_PER_STRATEGY_THRESHOLDS)
    if per_strategy_thresholds:
        thresholds.update(per_strategy_thresholds)

    with db_writer_lock(db_path, WriteClass.BULK):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Current-window snapshot.
            snapshot = compute_reaction_latency_per_strategy(
                conn, window_days=window_days, end_date=end_date.isoformat(),
            )
            # Per-strategy gap detection over n_windows of latency history.
            verdicts: dict[str, dict[str, Any]] = {}
            for sk in STRATEGY_KEYS:
                history = _build_latency_history(conn, sk, end_date, window_days, n_windows)
                v = detect_reaction_gap(
                    history, sk,
                    gap_threshold_multiplier=thresholds.get(sk, 1.5),
                    critical_ratio_cutoff=critical_ratio_cutoff,
                )
                verdicts[sk] = _verdict_to_dict(v)
            # Negative-latency surfacing for current window.
            negative_latency_count = _compute_negative_latency_count(
                conn, end_date, window_days,
            )
        finally:
            conn.close()

    return {
        "report_kind": "ws_poll_reaction_weekly",
        "report_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "end_date": end_date.isoformat(),
        "window_days": window_days,
        "n_windows_for_gap": n_windows,
        "per_strategy_thresholds": thresholds,
        "critical_ratio_cutoff": critical_ratio_cutoff,
        "negative_latency_count": negative_latency_count,
        "db_path": str(db_path.relative_to(REPO_ROOT)) if str(db_path).startswith(str(REPO_ROOT)) else str(db_path),
        "current_window": snapshot,
        "gap_verdicts": verdicts,
    }


def _resolve_report_path(arg: str | None, end_date: date) -> Path:
    if arg:
        return Path(arg)
    DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_REPORT_DIR / f"weekly_{end_date.isoformat()}.json"


def _parse_override_strategy(raw_list: list[str]) -> dict[str, float]:
    """Parse repeated --override-strategy KEY=VALUE flags into a dict.

    Validates that KEY is one of the 4 STRATEGY_KEYS and VALUE is a positive
    float. Raises argparse.ArgumentTypeError on malformed input.
    """
    from src.state.ws_poll_reaction import STRATEGY_KEYS
    out: dict[str, float] = {}
    if not raw_list:
        return out
    for raw in raw_list:
        if "=" not in raw:
            raise argparse.ArgumentTypeError(f"--override-strategy expects KEY=VALUE, got: {raw}")
        key, _, val = raw.partition("=")
        key = key.strip()
        val = val.strip()
        if key not in STRATEGY_KEYS:
            raise argparse.ArgumentTypeError(
                f"--override-strategy unknown strategy_key {key!r}; expected one of {sorted(STRATEGY_KEYS)}"
            )
        try:
            fv = float(val)
        except ValueError as e:
            raise argparse.ArgumentTypeError(f"--override-strategy value not a float: {raw}") from e
        if fv <= 0:
            raise argparse.ArgumentTypeError(f"--override-strategy multiplier must be positive: {raw}")
        out[key] = fv
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end-date", help="Inclusive end day of the current window (YYYY-MM-DD); default today UTC")
    ap.add_argument("--window-days", type=int, default=7, help="Window length in calendar days (default 7)")
    ap.add_argument("--n-windows", type=int, default=DEFAULT_TRAILING_WINDOWS,
                    help="Number of windows in the gap-detection history (default 4)")
    ap.add_argument("--critical-ratio-cutoff", type=float, default=2.0,
                    help="Severity bumps to critical at ratio >= this value (default 2.0)")
    ap.add_argument("--override-strategy", action="append", default=[],
                    metavar="KEY=VALUE",
                    help="Override per-strategy gap_threshold_multiplier; repeatable")
    ap.add_argument("--db-path", help=f"Path to Zeus state DB (default {DEFAULT_DB_PATH})")
    ap.add_argument("--report-out", help="Path to write JSON report (default docs/operations/ws_poll_reaction/weekly_<date>.json)")
    ap.add_argument("--stdout", action="store_true", help="Also print the JSON to stdout")
    args = ap.parse_args(argv)

    end_date = _resolve_end_date(args.end_date)
    db_path = Path(args.db_path) if args.db_path else DEFAULT_DB_PATH
    overrides = _parse_override_strategy(args.override_strategy)

    report = run_weekly(
        db_path, end_date,
        window_days=args.window_days,
        n_windows=args.n_windows,
        per_strategy_thresholds=overrides,
        critical_ratio_cutoff=args.critical_ratio_cutoff,
    )
    out_path = _resolve_report_path(args.report_out, end_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n")

    print(f"wrote: {out_path}")
    if args.stdout:
        print(json.dumps(report, indent=2, default=str))

    # Per-strategy summary line for operator scanning (mirror EO/AD style).
    any_gap = False
    for sk, v in report["gap_verdicts"].items():
        snap = report["current_window"][sk]
        p95 = snap.get("latency_p95_ms")
        p95_str = f"{p95:.1f}ms" if p95 is not None else "n/a"
        n_sig = snap.get("n_signals", 0)
        sq = snap.get("sample_quality", "n/a")
        thr = report["per_strategy_thresholds"].get(sk, 1.5)
        sev = f" {v['severity']}" if v.get("severity") else ""
        flag = ""
        if v["kind"] == "gap_detected":
            flag = f"  EXCEEDS thr={thr}"
            any_gap = True
        print(f"  {sk}: p95={p95_str} n={n_sig} q={sq} → {v['kind']}{sev}{flag}")
    if report["negative_latency_count"] > 0:
        print(f"  WARN negative_latency_count={report['negative_latency_count']} "
              "(upstream clock-skew; clipped to 0 in latency stats)")

    return 1 if any_gap else 0


if __name__ == "__main__":
    sys.exit(main())
