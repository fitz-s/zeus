#!/usr/bin/env python3
# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: round3_verdict.md §1 #2 (FIFTH and FINAL edge packet) +
# ULTIMATE_PLAN.md §4 #4 (LEARNING_LOOP_PACKET).
# LEARNING_LOOP packet BATCH 3 (FINAL of FINAL).
"""Weekly LEARNING_LOOP runner.

CLI wrapper that wires src.state.learning_loop_observation BATCH 1 + BATCH 2
+ orchestrates cross-packet integration with src.state.calibration_observation:

  1. Compute per-bucket-key learning-loop pipeline state for the current
     window via compute_learning_loop_state_per_bucket (BATCH 1).
  2. Build per-bucket per-window state history by re-running BATCH 1 across
     a trailing sequence of windows (mirror of sibling weekly runners).
  3. CROSS-PACKET ORCHESTRATION: for each bucket, also build a per-bucket
     parameter-snapshot history via compute_platt_parameter_snapshot_per_bucket
     (CALIBRATION packet BATCH 1) and run detect_parameter_drift (CALIBRATION
     packet BATCH 2) to get drift_detected per bucket.
  4. Run detect_learning_loop_stall (BATCH 2 3-kind composable detector)
     per bucket with PER-BUCKET threshold dict (HIGH temperature_metric
     buckets get tighter thresholds across all 3 stall_kinds) AND with
     drift_detected fed in from step 3.
  5. Emit a structured JSON report with per-bucket snapshot + per-bucket
     ParameterStallVerdict + cross-packet drift_detected map per bucket.

K1 compliance:
  - Read-only DB access throughout.
  - JSON output is derived context (operator/ops evidence), NOT authority.
  - No mutation of any canonical surface.
  - Per round3_verdict.md §1 #4: manual run only — operator decides
    cron / launchd wiring later.

CROSS-MODULE COMPOSITION ARCHITECTURE: this runner is the ONLY place where
the LEARNING_LOOP detector's drift_no_refit kind gets its drift_detected
input. Per LEARNING_LOOP boot §6.3 + GO_BATCH_2 §3 ACCEPT-DEFAULT, the
detector module itself stays pure-Python with caller-provided
drift_detected — keeps detector unit-testable in isolation and avoids
cross-module DB-read coupling.

Per-bucket threshold defaults (LOW-DESIGN-WP-2-2 carry-forward pattern):

    HIGH temperature_metric: pair_growth=1.3 / pairs_ready=20 / drift=10
                             (alpha decays fastest — bot scanning per
                              src/calibration/AGENTS.md L14-22; tightest
                              discipline)
    LOW temperature_metric:  pair_growth=1.5 / pairs_ready=30 / drift=14
                             (standard; sibling-coherent with WP/CALIBRATION
                              defaults)
    legacy:                  same as LOW (HIGH-only by Phase 9C L3 convention)
    insufficient (sample):   SUPPRESS — stall detection skipped until maturity

Operator can override per-bucket via repeated --override-bucket KEY=FIELD=VALUE
flags, e.g.:
    --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=pair_growth=1.1
    --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=pairs_ready=15

Mirrors scripts/{ws_poll_reaction,calibration_observation,attribution_drift,
edge_observation}_weekly.py shape (same flag style, same output-dir
convention) so operators learn one runner pattern.

Usage:
    python3 scripts/learning_loop_observation_weekly.py
    python3 scripts/learning_loop_observation_weekly.py --end-date 2026-04-28
    python3 scripts/learning_loop_observation_weekly.py --window-days 7 --n-windows 6
    python3 scripts/learning_loop_observation_weekly.py --pair-growth-threshold 1.3
    python3 scripts/learning_loop_observation_weekly.py --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=pair_growth=1.1
    python3 scripts/learning_loop_observation_weekly.py --report-out /tmp/ll.json --stdout
    python3 scripts/learning_loop_observation_weekly.py --db-path state/zeus-shared.db

Default report path:
    docs/operations/learning_loop_observation/weekly_<YYYY-MM-DD>.json

Exit code: 0 if no bucket is stall_detected; 1 if at least one bucket has
stall_detected (cron-friendly).
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
# LOW-OPERATIONAL-WP-3-1 fix (critic 25th cycle carry-forward applied
# pre-emptively): ensure `from src.state.X import ...` works when this
# script is invoked from any cwd, without requiring PYTHONPATH=. or
# `python -m scripts.X`.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# db_writer_lock removed: this script is read-only (PR #86 Copilot fix)

DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "operations" / "learning_loop_observation"
DEFAULT_DB_PATH = REPO_ROOT / "state" / "zeus-shared.db"
DEFAULT_TRAILING_WINDOWS = 4

# Per-bucket threshold defaults (per dispatch §Per-bucket threshold defaults
# + GO_BATCH_3 §Per-bucket threshold defaults dict).
DEFAULT_HIGH_PAIR_GROWTH = 1.3
DEFAULT_HIGH_PAIRS_READY = 20
DEFAULT_HIGH_DRIFT = 10
DEFAULT_LOW_PAIR_GROWTH = 1.5
DEFAULT_LOW_PAIRS_READY = 30
DEFAULT_LOW_DRIFT = 14


def _resolve_end_date(end_date_str: str | None) -> date:
    if end_date_str is None:
        return datetime.now(timezone.utc).date()
    return datetime.strptime(end_date_str, "%Y-%m-%d").date()


def _verdict_to_dict(v: Any) -> dict:
    """Serialize a ParameterStallVerdict (dataclass) to plain dict for JSON."""
    if is_dataclass(v):
        return asdict(v)
    return dict(v)


def _resolve_bucket_thresholds(
    snapshot: dict[str, Any],
    *,
    overrides: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Resolve per-bucket thresholds (3-tuple).

    Returns a dict with keys: pair_growth, pairs_ready, drift.

    Order of precedence:
      1. Explicit operator override (matches bucket_key + field exactly)
      2. HIGH temperature_metric → tighter defaults (1.3 / 20 / 10)
      3. LOW or legacy → standard defaults (1.5 / 30 / 14)
    """
    bucket_key = snapshot.get("bucket_key", "")
    temp_metric = snapshot.get("temperature_metric")
    if temp_metric == "high":
        thresholds = {
            "pair_growth": DEFAULT_HIGH_PAIR_GROWTH,
            "pairs_ready": DEFAULT_HIGH_PAIRS_READY,
            "drift": DEFAULT_HIGH_DRIFT,
        }
    else:
        thresholds = {
            "pair_growth": DEFAULT_LOW_PAIR_GROWTH,
            "pairs_ready": DEFAULT_LOW_PAIRS_READY,
            "drift": DEFAULT_LOW_DRIFT,
        }
    if bucket_key in overrides:
        thresholds.update(overrides[bucket_key])
    return thresholds


def _build_state_history(
    conn: sqlite3.Connection,
    bucket_key: str,
    end_date: date,
    window_days: int,
    n_windows: int,
) -> list[dict[str, Any]]:
    """Build chronological per-window state history for ONE bucket_key."""
    from src.state.learning_loop_observation import compute_learning_loop_state_per_bucket
    history: list[dict[str, Any]] = []
    for offset in range(n_windows - 1, -1, -1):
        wend = end_date - timedelta(days=offset * window_days)
        per_bucket = compute_learning_loop_state_per_bucket(
            conn, window_days=window_days, end_date=wend.isoformat(),
        )
        if bucket_key in per_bucket:
            history.append(per_bucket[bucket_key])
    return history


def _build_parameter_history(
    conn: sqlite3.Connection,
    bucket_key: str,
    end_date: date,
    window_days: int,
    n_windows: int,
) -> list[dict[str, Any]]:
    """Build per-bucket parameter-snapshot history (CALIBRATION BATCH 1
    output) for cross-module drift detection."""
    from src.state.calibration_observation import compute_platt_parameter_snapshot_per_bucket
    history: list[dict[str, Any]] = []
    for offset in range(n_windows - 1, -1, -1):
        wend = end_date - timedelta(days=offset * window_days)
        per_bucket = compute_platt_parameter_snapshot_per_bucket(
            conn, window_days=window_days, end_date=wend.isoformat(),
        )
        if bucket_key in per_bucket:
            history.append(per_bucket[bucket_key])
    return history


def _resolve_drift_detected_for_bucket(
    conn: sqlite3.Connection,
    bucket_key: str,
    end_date: date,
    window_days: int,
    n_windows: int,
) -> bool | None:
    """CROSS-PACKET ORCHESTRATION: feed CALIBRATION BATCH 1 history into
    CALIBRATION BATCH 2 detector to get drift_detected per bucket.

    Returns True/False (drift verdict) OR None when CALIBRATION's detector
    returns insufficient_data (LEARNING's detect_learning_loop_stall then
    treats drift_no_refit as insufficient too).
    """
    from src.state.calibration_observation import detect_parameter_drift
    history = _build_parameter_history(conn, bucket_key, end_date, window_days, n_windows)
    if not history:
        return None
    verdict = detect_parameter_drift(history, bucket_key)
    if verdict.kind == "drift_detected":
        return True
    if verdict.kind == "within_normal":
        return False
    # insufficient_data → return None (caller-provided seam)
    return None


def run_weekly(
    db_path: Path,
    end_date: date,
    window_days: int = 7,
    n_windows: int = DEFAULT_TRAILING_WINDOWS,
    overrides: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Compute the weekly LEARNING_LOOP report.

    Returns a JSON-serializable dict with per-bucket current snapshot +
    per-bucket ParameterStallVerdict + per-bucket drift_detected map.
    """
    from src.state.learning_loop_observation import (
        compute_learning_loop_state_per_bucket,
        detect_learning_loop_stall,
    )

    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    overrides = overrides or {}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Current-window snapshot.
        snapshot = compute_learning_loop_state_per_bucket(
            conn, window_days=window_days, end_date=end_date.isoformat(),
        )
        # Per-bucket stall detection over n_windows of state history.
        verdicts: dict[str, dict[str, Any]] = {}
        thresholds_used: dict[str, dict[str, float]] = {}
        drift_detected_map: dict[str, bool | None] = {}
        for bucket_key, snap in snapshot.items():
            thresholds = _resolve_bucket_thresholds(snap, overrides=overrides)
            thresholds_used[bucket_key] = thresholds
            # Suppress stall detection for insufficient-quality buckets.
            if snap.get("sample_quality") == "insufficient":
                verdicts[bucket_key] = {
                    "kind": "insufficient_data",
                    "bucket_key": bucket_key,
                    "stall_kinds": [],
                    "severity": None,
                    "evidence": {
                        "reason": "current_window_sample_quality_insufficient",
                        "n_pairs_canonical": snap.get("n_pairs_canonical", 0),
                    },
                }
                drift_detected_map[bucket_key] = None
                continue
            # CROSS-PACKET ORCHESTRATION: get drift_detected from CALIBRATION.
            drift_detected = _resolve_drift_detected_for_bucket(
                conn, bucket_key, end_date, window_days, n_windows,
            )
            drift_detected_map[bucket_key] = drift_detected
            # Build state history + run stall detector.
            state_history = _build_state_history(
                conn, bucket_key, end_date, window_days, n_windows,
            )
            v = detect_learning_loop_stall(
                state_history, bucket_key,
                pair_growth_threshold_multiplier=thresholds["pair_growth"],
                days_pairs_ready_no_retrain=thresholds["pairs_ready"],
                days_drift_no_refit=thresholds["drift"],
                drift_detected=drift_detected,
            )
            verdicts[bucket_key] = _verdict_to_dict(v)
    finally:
        conn.close()

    return {
        "report_kind": "learning_loop_observation_weekly",
        "report_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "end_date": end_date.isoformat(),
        "window_days": window_days,
        "n_windows_for_stall": n_windows,
        "per_bucket_thresholds": thresholds_used,
        "drift_detected_map": drift_detected_map,
        "db_path": str(db_path.relative_to(REPO_ROOT)) if str(db_path).startswith(str(REPO_ROOT)) else str(db_path),
        "current_window": snapshot,
        "stall_verdicts": verdicts,
    }


def _resolve_report_path(arg: str | None, end_date: date) -> Path:
    if arg:
        return Path(arg)
    DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_REPORT_DIR / f"weekly_{end_date.isoformat()}.json"


def _parse_override_bucket(raw_list: list[str]) -> dict[str, dict[str, float]]:
    """Parse repeated --override-bucket KEY=FIELD=VALUE flags.

    Format: <bucket_key>=<field_name>=<float_value>
    Where field_name ∈ {pair_growth, pairs_ready, drift}.

    Validates: KEY is non-empty; FIELD ∈ valid set; VALUE is positive float.
    Raises argparse.ArgumentTypeError on malformed input.

    LOW-DESIGN-WP-2-2 pattern carry-forward (4-input validation).
    """
    valid_fields = {"pair_growth", "pairs_ready", "drift"}
    out: dict[str, dict[str, float]] = {}
    if not raw_list:
        return out
    for raw in raw_list:
        # Format: bucket_key=field=value (2 equal signs)
        parts = raw.rsplit("=", 2)
        if len(parts) != 3:
            raise argparse.ArgumentTypeError(
                f"--override-bucket expects KEY=FIELD=VALUE (3 parts split on =), got: {raw}"
            )
        bucket_key, field, val = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not bucket_key:
            raise argparse.ArgumentTypeError(
                f"--override-bucket KEY is empty: {raw}"
            )
        if field not in valid_fields:
            raise argparse.ArgumentTypeError(
                f"--override-bucket FIELD must be one of {sorted(valid_fields)}; got {field!r}"
            )
        try:
            fv = float(val)
        except ValueError as e:
            raise argparse.ArgumentTypeError(
                f"--override-bucket value not a float: {raw}"
            ) from e
        if fv <= 0:
            raise argparse.ArgumentTypeError(
                f"--override-bucket value must be positive: {raw}"
            )
        out.setdefault(bucket_key, {})[field] = fv
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--end-date", help="Inclusive end day of the current window (YYYY-MM-DD); default today UTC")
    ap.add_argument("--window-days", type=int, default=7, help="Window length in calendar days (default 7)")
    ap.add_argument("--n-windows", type=int, default=DEFAULT_TRAILING_WINDOWS,
                    help="Number of windows in the stall-detection history (default 4)")
    ap.add_argument("--override-bucket", action="append", default=[],
                    metavar="KEY=FIELD=VALUE",
                    help="Override per-bucket threshold; FIELD ∈ {pair_growth, pairs_ready, drift}; repeatable")
    ap.add_argument("--db-path", help=f"Path to Zeus state DB (default {DEFAULT_DB_PATH})")
    ap.add_argument("--report-out",
                    help="Path to write JSON report (default docs/operations/learning_loop_observation/weekly_<date>.json)")
    ap.add_argument("--stdout", action="store_true", help="Also print the JSON to stdout")
    args = ap.parse_args(argv)

    end_date = _resolve_end_date(args.end_date)
    db_path = Path(args.db_path) if args.db_path else DEFAULT_DB_PATH
    overrides = _parse_override_bucket(args.override_bucket)

    report = run_weekly(
        db_path, end_date,
        window_days=args.window_days,
        n_windows=args.n_windows,
        overrides=overrides,
    )
    out_path = _resolve_report_path(args.report_out, end_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n")

    print(f"wrote: {out_path}")
    if args.stdout:
        print(json.dumps(report, indent=2, default=str))

    # Per-bucket summary line for operator scanning.
    any_stall = False
    for bucket_key, v in report["stall_verdicts"].items():
        snap = report["current_window"][bucket_key]
        n_pairs = snap.get("n_pairs_canonical", 0)
        sq = snap.get("sample_quality", "n/a")
        kinds_str = ",".join(v.get("stall_kinds", [])) or "—"
        sev = f" {v['severity']}" if v.get("severity") else ""
        flag = ""
        if v["kind"] == "stall_detected":
            flag = f"  STALL kinds={kinds_str}"
            any_stall = True
        drift = report["drift_detected_map"].get(bucket_key)
        drift_marker = "" if drift is None else (" drift=true" if drift else "")
        print(f"  {bucket_key}: n_pairs={n_pairs} q={sq}{drift_marker} → {v['kind']}{sev}{flag}")

    return 1 if any_stall else 0


if __name__ == "__main__":
    sys.exit(main())
