# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ANTI_DRIFT_CHARTER §3 M1; IMPLEMENTATION_PLAN Phase 5.A deliverable A-1
"""ritual_signal_aggregate.py — M1 telemetry consumption endpoint.

Reads logs/ritual_signal/*.jsonl, computes per-gate counts (allow / refuse / warn),
per-capability hit distribution, and time-windowed summaries (last 24h, 7d, 30d).

Usage:
    python3 scripts/ritual_signal_aggregate.py [--out path/to/output.json] [--log-dir path]

Output: JSON to stdout (or --out file) with structure:
  {
    "generated_at": "<iso8601>",
    "windows": {
      "24h": { "total": N, "per_gate": {...}, "per_cap_id": {...}, "per_decision": {...} },
      "7d":  { ... },
      "30d": { ... }
    },
    "all_time": { "total": N, "per_gate": {...}, "per_cap_id": {...}, "per_decision": {...} }
  }
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = REPO_ROOT / "logs" / "ritual_signal"

WINDOWS_DAYS: dict[str, int] = {"24h": 1, "7d": 7, "30d": 30}


# ---------------------------------------------------------------------------
# Core aggregation
# ---------------------------------------------------------------------------

def _load_entries(log_dir: pathlib.Path) -> list[dict]:
    """Load all ritual_signal JSON lines from all *.jsonl files in log_dir."""
    entries: list[dict] = []
    if not log_dir.is_dir():
        return entries
    for log_file in sorted(log_dir.rglob("*.jsonl")):
        for raw_line in log_file.read_text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                obj = {"_malformed": True, "_raw": line}
            entries.append(obj)
    return entries


def _parse_ts(entry: dict) -> datetime | None:
    """Return UTC datetime from entry's invocation_ts, or None if missing/malformed."""
    ts_str = entry.get("invocation_ts", "")
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _decision_key(entry: dict) -> str:
    """Return a unified decision/outcome key (gates use different field names)."""
    if "decision" in entry:
        return entry["decision"]
    if "outcome" in entry:
        return entry["outcome"]
    return "_unknown"


def _bucket(entries: list[dict]) -> dict:
    """Compute per-gate, per-cap_id, per-decision counts for a list of entries."""
    per_gate: dict[str, int] = collections.Counter()
    per_cap_id: dict[str, int] = collections.Counter()
    per_decision: dict[str, int] = collections.Counter()

    for e in entries:
        if e.get("_malformed"):
            per_gate["_malformed"] = per_gate.get("_malformed", 0) + 1
            continue
        per_gate[e.get("helper", "_unknown")] += 1
        per_cap_id[e.get("cap_id", "(no_cap_id_field)")] += 1
        per_decision[_decision_key(e)] += 1

    return {
        "total": len(entries),
        "per_gate": dict(per_gate),
        "per_cap_id": dict(per_cap_id),
        "per_decision": dict(per_decision),
    }


def aggregate(log_dir: pathlib.Path = DEFAULT_LOG_DIR) -> dict:
    """Aggregate all ritual_signal logs. Returns structured dict."""
    all_entries = _load_entries(log_dir)
    now = datetime.now(timezone.utc)

    windows: dict[str, dict] = {}
    for label, days in WINDOWS_DAYS.items():
        cutoff = now - timedelta(days=days)
        windowed = [
            e for e in all_entries
            if (ts := _parse_ts(e)) is not None and ts >= cutoff
        ]
        windows[label] = _bucket(windowed)

    return {
        "generated_at": now.isoformat(),
        "log_dir": str(log_dir),
        "windows": windows,
        "all_time": _bucket(all_entries),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate ritual_signal telemetry logs.")
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Write JSON output to PATH (stdout if omitted).",
    )
    parser.add_argument(
        "--log-dir",
        metavar="DIR",
        default=str(DEFAULT_LOG_DIR),
        help=f"Directory containing *.jsonl files (default: {DEFAULT_LOG_DIR}).",
    )
    args = parser.parse_args(argv)

    log_dir = pathlib.Path(args.log_dir)
    result = aggregate(log_dir)
    payload = json.dumps(result, indent=2)

    if args.out:
        out_path = pathlib.Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n")
        print(f"ritual_signal_aggregate: wrote {out_path}", file=sys.stderr)
    else:
        print(payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
