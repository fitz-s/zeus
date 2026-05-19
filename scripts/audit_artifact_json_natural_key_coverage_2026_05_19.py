# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.3 (Backfill PRECONDITION, critic round-2 SEV-2)
"""Audit artifact_json natural-key coverage in decision_log.

Reads N random decision_log rows from the world DB, parses artifact_json,
iterates trade_cases within each row, calls from_artifact_json() on each
trade_case, and reports the natural-key recovery rate.

Recovery rate = (trade_cases where from_artifact_json returns non-None)
                / (total trade_cases examined)

Gate: >= 0.80 → backfill viable (PR-T1-B can proceed).
      <  0.80 → operator decision required (Path D with reduced coverage vs Path C).

Note: from_artifact_json returns a PARTIAL key (city placeholder for market_slug).
Recovery rate measures whether temperature_metric derivation and required fields
(target_date, observation_time via timestamp) are present — not whether market_slug
is resolved. The backfill resolves city→market_slug via market_events_v2.

Output: JSON to .claude/jobs/audit_artifact_json_natural_key/<UTC-timestamp>/result.json
        + stdout summary line.

Usage:
    python scripts/audit_artifact_json_natural_key_coverage_2026_05_19.py [--sample N]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone

# Allow running from repo root
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.contracts.decision_natural_key import from_artifact_json
from src.state.db import ZEUS_WORLD_DB_PATH


def _iter_trade_cases(artifact_json_str: str):
    """Yield individual trade_case dicts from a decision_log artifact_json string."""
    try:
        j = json.loads(artifact_json_str)
    except (json.JSONDecodeError, TypeError):
        return
    trade_cases = j.get("trade_cases")
    if not isinstance(trade_cases, list):
        return
    yield from trade_cases


def run_audit(sample: int = 1000) -> dict:
    """Run the audit and return a result dict."""
    conn = sqlite3.connect(str(ZEUS_WORLD_DB_PATH))
    conn.row_factory = sqlite3.Row

    # Check table existence before querying (world DB may be empty on first run)
    has_table = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='decision_log'"
    ).fetchone()[0]
    if not has_table:
        conn.close()
        return {
            "total_runs": 0,
            "sampled_runs": 0,
            "total_trade_cases": 0,
            "recovered": 0,
            "failed": 0,
            "recovery_rate": None,
            "status": "NO_TABLE",
            "message": "decision_log table does not exist in world DB — no data to audit; gate passes vacuously",
            "db_path": str(ZEUS_WORLD_DB_PATH),
            "audit_ts": datetime.now(timezone.utc).isoformat(),
        }

    total_runs = conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]
    if total_runs == 0:
        conn.close()
        return {
            "total_runs": 0,
            "sampled_runs": 0,
            "total_trade_cases": 0,
            "recovered": 0,
            "failed": 0,
            "recovery_rate": None,
            "status": "NO_DATA",
            "message": "decision_log has 0 rows — no data to audit; gate passes vacuously",
            "db_path": str(ZEUS_WORLD_DB_PATH),
            "audit_ts": datetime.now(timezone.utc).isoformat(),
        }

    rows = conn.execute(
        """
        SELECT artifact_json FROM decision_log
        ORDER BY RANDOM()
        LIMIT ?
        """,
        (sample,),
    ).fetchall()
    conn.close()

    sampled_runs = len(rows)
    total_trade_cases = 0
    recovered = 0
    failed = 0
    failure_examples: list[dict] = []

    for row in rows:
        for tc in _iter_trade_cases(row["artifact_json"]):
            total_trade_cases += 1
            nk = from_artifact_json(tc)
            if nk is not None:
                recovered += 1
            else:
                failed += 1
                if len(failure_examples) < 3:
                    # Capture first few failures for diagnosis
                    failure_examples.append({
                        "city": tc.get("city"),
                        "target_date": tc.get("target_date"),
                        "range_label": tc.get("range_label"),
                        "timestamp": tc.get("timestamp"),
                    })

    if total_trade_cases == 0:
        recovery_rate = None
        status = "NO_TRADE_CASES"
        message = (
            f"Sampled {sampled_runs} decision_log runs; "
            "none contained trade_cases — gate passes vacuously"
        )
    else:
        recovery_rate = recovered / total_trade_cases
        if recovery_rate >= 0.80:
            status = "PASS"
            message = (
                f"recovery_rate={recovery_rate:.4f} "
                f"({recovered}/{total_trade_cases}) — gate PASS (>= 0.80)"
            )
        else:
            status = "FAIL"
            message = (
                f"recovery_rate={recovery_rate:.4f} "
                f"({recovered}/{total_trade_cases}) — gate FAIL (< 0.80); "
                "operator decision required (Path D with reduced coverage vs Path C forward-only)"
            )

    return {
        "total_runs": total_runs,
        "sampled_runs": sampled_runs,
        "total_trade_cases": total_trade_cases,
        "recovered": recovered,
        "failed": failed,
        "recovery_rate": recovery_rate,
        "status": status,
        "message": message,
        "failure_examples": failure_examples,
        "db_path": str(ZEUS_WORLD_DB_PATH),
        "audit_ts": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit artifact_json natural-key coverage in decision_log."
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=1000,
        help="Number of decision_log rows to sample (default: 1000)",
    )
    args = parser.parse_args()

    result = run_audit(sample=args.sample)

    # Write JSON output
    ts_slug = result["audit_ts"].replace(":", "-").replace("+", "Z").split(".")[0]
    out_dir = (
        pathlib.Path(__file__).parent.parent
        / ".claude"
        / "jobs"
        / "audit_artifact_json_natural_key"
        / ts_slug
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "result.json"
    out_path.write_text(json.dumps(result, indent=2))

    # Stdout summary
    rate = result["recovery_rate"]
    rate_str = f"{rate:.4f}" if rate is not None else "N/A"
    print(
        f"recovery_rate={rate_str} "
        f"({result['recovered']}/{result['total_trade_cases']} trade_cases "
        f"from {result['sampled_runs']} sampled runs)"
    )
    print(f"status: {result['status']}")
    print(f"result written to: {out_path}")

    if result["status"] == "FAIL":
        sys.exit(1)


if __name__ == "__main__":
    main()
