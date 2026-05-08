#!/usr/bin/env python3
# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: round3_verdict.md §1 #2 (FOURTH edge packet) + ULTIMATE_PLAN.md §4 #2.
# CALIBRATION_HARDENING packet BATCH 3 (FINAL).
"""Weekly CALIBRATION_HARDENING runner.

CLI wrapper that wires src.state.calibration_observation BATCH 1 + BATCH 2:

  1. Compute per-bucket-key Platt parameter snapshot for the current window
     via compute_platt_parameter_snapshot_per_bucket (BATCH 1; PATH A
     bucket-snapshot framing).
  2. Build per-bucket per-window history by re-running BATCH 1 across a
     trailing sequence of windows (mirror of ws_poll_reaction_weekly +
     edge_observation_weekly).
  3. Run detect_parameter_drift (BATCH 2 ratio test) per bucket with a
     PER-BUCKET threshold dict (HIGH temperature_metric buckets get a
     tighter multiplier 1.3 because alpha decay is fastest there per
     src/calibration/AGENTS.md L14-22 + dispatch GO_BATCH_3 §Per-bucket
     threshold defaults).
  4. Emit a structured JSON report with per-bucket snapshot + per-bucket
     ParameterDriftVerdict + bootstrap_usable_count surface for each bucket
     (LOW-NUANCE-CALIBRATION-1-2 carry-forward from critic 27th cycle —
     operator visibility on bootstrap-row vs aggregated-sample gap).

K1 compliance:
  - Read-only DB access.
  - JSON output is derived context (operator/ops evidence), NOT authority.
  - No mutation of any canonical surface.
  - Per round3_verdict.md §1 #4 + boot §6 #4: manual run only — operator
    decides cron / launchd wiring later.

Per-bucket threshold defaults (LOW-DESIGN-WP-2-2 pattern carry-forward):

    HIGH temperature_metric: 1.3  (alpha decays fastest — bot scanning per
                                   src/calibration/AGENTS.md; tight)
    LOW temperature_metric:  1.5  (standard — slower decay)
    legacy bucket_key:       1.5  (treated as HIGH-only by Phase 9C L3
                                   convention; standard threshold)
    insufficient (n<30):     SUPPRESS — drift detection skipped until maturity

Operator can override per-bucket via repeated --override-bucket KEY=VALUE
flags, e.g.:
    --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=1.1
    --override-bucket low:Tokyo:JJA:ecmwf_ens_v2:width_normalized_density=1.4

Mirrors scripts/ws_poll_reaction_weekly.py + scripts/attribution_drift_weekly.py
+ scripts/edge_observation_weekly.py shape (same flag style, same output-
dir convention) so operators learn one runner pattern.

Usage:
    python3 scripts/calibration_observation_weekly.py
    python3 scripts/calibration_observation_weekly.py --end-date 2026-04-28
    python3 scripts/calibration_observation_weekly.py --window-days 7 --n-windows 6
    python3 scripts/calibration_observation_weekly.py --critical-ratio-cutoff 2.5
    python3 scripts/calibration_observation_weekly.py --override-bucket high:NewYork:DJF:tigge_v3:width_normalized_density=1.1
    python3 scripts/calibration_observation_weekly.py --report-out /tmp/cal.json --stdout
    python3 scripts/calibration_observation_weekly.py --db-path state/zeus-shared.db

Default report path:
    docs/operations/calibration_observation/weekly_<YYYY-MM-DD>.json

Exit code: 0 if no bucket is drift_detected; 1 if at least one bucket has
drift_detected (cron-friendly).
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
# script is invoked as `python3 scripts/calibration_observation_weekly.py`
# from any cwd, without requiring PYTHONPATH=. or `python -m scripts.X`.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# db_writer_lock removed: this script is read-only (PR #86 Copilot fix)

DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "operations" / "calibration_observation"
DEFAULT_DB_PATH = REPO_ROOT / "state" / "zeus-shared.db"
DEFAULT_TRAILING_WINDOWS = 4   # 1 current + 3 trailing per detect_parameter_drift min_windows
DEFAULT_DRIFT_THRESHOLD_MULTIPLIER_HIGH = 1.3  # tighter — fast-decay HIGH metric
DEFAULT_DRIFT_THRESHOLD_MULTIPLIER_LOW = 1.5   # standard — slower-decay LOW metric
DEFAULT_DRIFT_THRESHOLD_MULTIPLIER_LEGACY = 1.5  # legacy = HIGH-only by convention
DEFAULT_CRITICAL_RATIO_CUTOFF = 2.0


def _resolve_end_date(end_date_str: str | None) -> date:
    if end_date_str is None:
        return datetime.now(timezone.utc).date()
    return datetime.strptime(end_date_str, "%Y-%m-%d").date()


def _verdict_to_dict(v: Any) -> dict:
    """Serialize a ParameterDriftVerdict (dataclass) to plain dict for JSON."""
    if is_dataclass(v):
        return asdict(v)
    return dict(v)


def _resolve_bucket_threshold(
    snapshot: dict[str, Any],
    *,
    overrides: dict[str, float],
) -> float:
    """Resolve per-bucket drift_threshold_multiplier.

    Order of precedence:
      1. Explicit operator override (matches bucket_key exactly)
      2. HIGH temperature_metric → 1.3
      3. LOW temperature_metric → 1.5
      4. Legacy (no temperature_metric set) → 1.5
    """
    bucket_key = snapshot.get("bucket_key", "")
    if bucket_key in overrides:
        return overrides[bucket_key]
    temp_metric = snapshot.get("temperature_metric")
    if temp_metric == "high":
        return DEFAULT_DRIFT_THRESHOLD_MULTIPLIER_HIGH
    if temp_metric == "low":
        return DEFAULT_DRIFT_THRESHOLD_MULTIPLIER_LOW
    return DEFAULT_DRIFT_THRESHOLD_MULTIPLIER_LEGACY


def _build_parameter_history(
    conn: sqlite3.Connection,
    bucket_key: str,
    end_date: date,
    window_days: int,
    n_windows: int,
) -> list[dict[str, Any]]:
    """Build chronological per-window parameter history for ONE bucket_key.

    Re-runs compute_platt_parameter_snapshot_per_bucket n_windows times,
    each with end_date shifted back by window_days. Returns oldest → newest
    list of that bucket's window records (the shape detect_parameter_drift
    expects).

    NOTE: only includes windows where the bucket_key is present in the
    snapshot. If a bucket was NOT present in some historical window
    (e.g., model not yet fitted at that time), that window is SKIPPED —
    detect_parameter_drift's min_windows guard handles the resulting
    short-history case via insufficient_data.
    """
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


def run_weekly(
    db_path: Path,
    end_date: date,
    window_days: int = 7,
    n_windows: int = DEFAULT_TRAILING_WINDOWS,
    overrides: dict[str, float] | None = None,
    critical_ratio_cutoff: float = DEFAULT_CRITICAL_RATIO_CUTOFF,
) -> dict[str, Any]:
    """Compute the weekly Platt-parameter-drift report.

    Returns a JSON-serializable dict with per-bucket current-window snapshot
    + per-bucket ParameterDriftVerdict + per-bucket threshold actually used.
    """
    from src.state.calibration_observation import (
        compute_platt_parameter_snapshot_per_bucket,
        detect_parameter_drift,
    )

    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    overrides = overrides or {}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Current-window snapshot.
        snapshot = compute_platt_parameter_snapshot_per_bucket(
            conn, window_days=window_days, end_date=end_date.isoformat(),
        )
        # Per-bucket drift detection over n_windows of parameter history.
        verdicts: dict[str, dict[str, Any]] = {}
        thresholds_used: dict[str, float] = {}
        for bucket_key, snap in snapshot.items():
            threshold = _resolve_bucket_threshold(snap, overrides=overrides)
            thresholds_used[bucket_key] = threshold
            # Suppress drift detection for insufficient-quality buckets.
            if snap.get("sample_quality") == "insufficient":
                verdicts[bucket_key] = {
                    "kind": "insufficient_data",
                    "bucket_key": bucket_key,
                    "severity": None,
                    "evidence": {
                        "reason": "current_window_sample_quality_insufficient",
                        "n_samples": snap.get("n_samples", 0),
                    },
                }
                continue
            history = _build_parameter_history(
                conn, bucket_key, end_date, window_days, n_windows,
            )
            v = detect_parameter_drift(
                history, bucket_key,
                drift_threshold_multiplier=threshold,
                critical_ratio_cutoff=critical_ratio_cutoff,
            )
            verdicts[bucket_key] = _verdict_to_dict(v)
    finally:
        conn.close()

    return {
        "report_kind": "calibration_observation_weekly",
        "report_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "end_date": end_date.isoformat(),
        "window_days": window_days,
        "n_windows_for_drift": n_windows,
        "per_bucket_thresholds": thresholds_used,
        "critical_ratio_cutoff": critical_ratio_cutoff,
        "db_path": str(db_path.relative_to(REPO_ROOT)) if str(db_path).startswith(str(REPO_ROOT)) else str(db_path),
        "current_window": snapshot,
        "drift_verdicts": verdicts,
    }


def _resolve_report_path(arg: str | None, end_date: date) -> Path:
    if arg:
        return Path(arg)
    DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_REPORT_DIR / f"weekly_{end_date.isoformat()}.json"


def _parse_override_bucket(raw_list: list[str]) -> dict[str, float]:
    """Parse repeated --override-bucket KEY=VALUE flags into a dict.

    Validates: KEY is a non-empty string (matched against snapshot bucket_keys
    at runtime — we don't pre-validate against an enum because v2 model_keys
    are dynamically composed); VALUE is a positive float. Raises
    argparse.ArgumentTypeError on malformed input.

    LOW-DESIGN-WP-2-2 pattern carry-forward (4-input validation):
      - missing equals → error
      - empty key → error
      - non-float value → error
      - non-positive value → error
    """
    out: dict[str, float] = {}
    if not raw_list:
        return out
    for raw in raw_list:
        if "=" not in raw:
            raise argparse.ArgumentTypeError(
                f"--override-bucket expects KEY=VALUE, got: {raw}"
            )
        key, _, val = raw.partition("=")
        key = key.strip()
        val = val.strip()
        if not key:
            raise argparse.ArgumentTypeError(
                f"--override-bucket KEY is empty: {raw}"
            )
        try:
            fv = float(val)
        except ValueError as e:
            raise argparse.ArgumentTypeError(
                f"--override-bucket value not a float: {raw}"
            ) from e
        if fv <= 0:
            raise argparse.ArgumentTypeError(
                f"--override-bucket multiplier must be positive: {raw}"
            )
        out[key] = fv
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--end-date", help="Inclusive end day of the current window (YYYY-MM-DD); default today UTC")
    ap.add_argument("--window-days", type=int, default=7, help="Window length in calendar days (default 7)")
    ap.add_argument("--n-windows", type=int, default=DEFAULT_TRAILING_WINDOWS,
                    help="Number of windows in the drift-detection history (default 4)")
    ap.add_argument("--critical-ratio-cutoff", type=float, default=DEFAULT_CRITICAL_RATIO_CUTOFF,
                    help=f"Severity bumps to critical at ratio >= this value (default {DEFAULT_CRITICAL_RATIO_CUTOFF})")
    ap.add_argument("--override-bucket", action="append", default=[],
                    metavar="KEY=VALUE",
                    help="Override per-bucket drift_threshold_multiplier; repeatable")
    ap.add_argument("--db-path", help=f"Path to Zeus state DB (default {DEFAULT_DB_PATH})")
    ap.add_argument("--report-out",
                    help="Path to write JSON report (default docs/operations/calibration_observation/weekly_<date>.json)")
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
        critical_ratio_cutoff=args.critical_ratio_cutoff,
    )
    out_path = _resolve_report_path(args.report_out, end_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n")

    print(f"wrote: {out_path}")
    if args.stdout:
        print(json.dumps(report, indent=2, default=str))

    # Per-bucket summary line for operator scanning (mirror EO/AD/WP style).
    any_drift = False
    for bucket_key, v in report["drift_verdicts"].items():
        snap = report["current_window"][bucket_key]
        n = snap.get("n_samples", 0)
        sq = snap.get("sample_quality", "n/a")
        thr = report["per_bucket_thresholds"].get(bucket_key, "?")
        sev = f" {v['severity']}" if v.get("severity") else ""
        flag = ""
        if v["kind"] == "drift_detected":
            flag = f"  EXCEEDS thr={thr}"
            any_drift = True
        bcount = snap.get("bootstrap_count", 0)
        bucount = snap.get("bootstrap_usable_count", 0)
        boot_marker = f" boot={bucount}/{bcount}" if bcount != bucount else ""
        print(f"  {bucket_key}: n={n} q={sq}{boot_marker} → {v['kind']}{sev}{flag}")

    return 1 if any_drift else 0


if __name__ == "__main__":
    sys.exit(main())
