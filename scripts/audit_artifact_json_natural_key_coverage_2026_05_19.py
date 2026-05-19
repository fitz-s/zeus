# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.3 (Backfill PRECONDITION, critic round-2 SEV-2); operator directive 2026-05-19 "paths按main写入"
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

IMPORTANT: This script reads PRIMARY production state (not worktree-local).
Uses src/state/db_paths.primary_world_db_path() per operator directive.

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
from src.state.db_paths import primary_world_db_path


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
    """Run the audit and return a result dict.

    Reads from PRIMARY production world DB (not worktree-local).
    Exits with code 2 if DB or table is absent — vacuous pass is not acceptable.
    """
    db_path = primary_world_db_path()
    if not db_path.exists():
        print(
            f"FATAL: PRIMARY world DB not found at {db_path}. "
            "Audit cannot run against absent production DB — vacuous pass rejected.",
            file=sys.stderr,
        )
        sys.exit(2)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")

    # Hard-fail if decision_log table is missing — this must be a production DB
    has_table = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='decision_log'"
    ).fetchone()[0]
    if not has_table:
        conn.close()
        print(
            f"FATAL: decision_log table missing in PRIMARY world DB at {db_path}. "
            "The production DB must have decision_log — vacuous pass rejected. "
            "Verify PR-T1-A migration applied to primary state/zeus-world.db.",
            file=sys.stderr,
        )
        sys.exit(2)

    total_runs = conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]
    if total_runs == 0:
        conn.close()
        print(
            f"FATAL: decision_log has 0 rows in PRIMARY world DB at {db_path}. "
            "No data to audit — vacuous pass rejected. "
            "Confirm live daemon has written decision_log entries before running audit.",
            file=sys.stderr,
        )
        sys.exit(2)

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
        print(
            f"FATAL: Sampled {sampled_runs} decision_log runs from PRIMARY DB at {db_path}; "
            "none contained trade_cases — vacuous pass rejected. "
            "Confirm live daemon decision_log rows include artifact_json with trade_cases.",
            file=sys.stderr,
        )
        sys.exit(2)
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
        "db_path": str(db_path),
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
