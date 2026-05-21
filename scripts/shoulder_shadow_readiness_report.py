# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T3 + AUTHORITY_GPT_ROUND_1_DOSSIER.md §12
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: Shadow-readiness gate for shoulder strategy. SELECT-only; NEVER mutates live_status.
# Reuse: Run by operator before promoting shoulder_sell from shadow to live.

"""shoulder_shadow_readiness_report — shadow-readiness gate for shoulder strategy promotion.

Per dossier §12: "Promotion gate: shoulder-specific shadow report and stress pass."

Aggregates:
  - decision_events: shoulder shadow decisions (strategy_key IN shoulder_*)
  - no_trade_events: shoulder rejections (reason LIKE 'SHOULDER_%')
  - tail_stress_scenarios: stress test rows
  - shoulder_exposure_ledger: total exposure

Emits ReadinessReport (dataclass) + optional markdown/JSON output.

CRITICAL INVARIANT (plan §3.1):
  This script NEVER mutates live_status in strategy_profile_registry.yaml
  or any DB column. It is READ-ONLY. Promotion is operator-gated.

ReadinessStatus enum (4 members):
  INSUFFICIENT_SHADOW         — fewer than MIN_SHADOW_DECISIONS shadow decisions
  INSUFFICIENT_STRESS_COVERAGE — fewer than MIN_STRESS_SCENARIOS stress rows
  INSUFFICIENT_REGIME_COVERAGE — fewer than MIN_REGIME_VARIETY distinct regimes
  READY_FOR_OPERATOR_REVIEW    — all thresholds met; operator may review for live

Usage
-----
    python scripts/shoulder_shadow_readiness_report.py [--db PATH] [--json] [--markdown]

--db PATH:      Override world DB path (default: from src.config STATE_DIR).
--json:         Print JSON output to stdout.
--markdown:     Print markdown report to stdout.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Thresholds (operator-tunable post-T3)
# ---------------------------------------------------------------------------

MIN_SHADOW_DECISIONS: int = 100
MIN_STRESS_SCENARIOS: int = 10
MIN_REGIME_VARIETY: int = 2  # at least 2 distinct regime tags in stress table

# ---------------------------------------------------------------------------
# ReadinessStatus enum
# ---------------------------------------------------------------------------

class ReadinessStatus(str, Enum):
    """4-member enum for shoulder shadow readiness gate (plan §2 T3)."""
    INSUFFICIENT_SHADOW = "INSUFFICIENT_SHADOW"
    INSUFFICIENT_STRESS_COVERAGE = "INSUFFICIENT_STRESS_COVERAGE"
    INSUFFICIENT_REGIME_COVERAGE = "INSUFFICIENT_REGIME_COVERAGE"
    READY_FOR_OPERATOR_REVIEW = "READY_FOR_OPERATOR_REVIEW"


# ---------------------------------------------------------------------------
# ReadinessReport dataclass
# ---------------------------------------------------------------------------

@dataclass
class ReadinessReport:
    """Structured output from evaluate_readiness.

    Fields
    ------
    status:
        One of the 4 ReadinessStatus values.
    shadow_decision_count:
        Number of shoulder shadow decisions in decision_events.
    no_trade_count:
        Number of shoulder no-trade events in no_trade_events.
    stress_coverage_count:
        Number of tail_stress_scenarios rows for shoulder candidates.
    regime_coverage_count:
        Number of distinct regimes covered in stress scenarios.
    exposure_total_usd:
        Sum of notional_usd from shoulder_exposure_ledger.
    blockers:
        List of human-readable blocker strings (empty when READY).
    """
    status: ReadinessStatus
    shadow_decision_count: int
    no_trade_count: int
    stress_coverage_count: int
    regime_coverage_count: int
    exposure_total_usd: float
    blockers: list[str]


# ---------------------------------------------------------------------------
# Core evaluation function (NEVER mutates live_status)
# ---------------------------------------------------------------------------

def evaluate_readiness(
    *,
    conn: sqlite3.Connection,
) -> ReadinessReport:
    """Evaluate shoulder shadow readiness against promotion thresholds.

    NEVER mutates live_status, strategy_profile_registry.yaml, or any DB row.
    Read-only query against conn (world DB).

    Parameters
    ----------
    conn:
        World-DB connection (INV-37 compatible). Caller provides.

    Returns
    -------
    ReadinessReport
        Populated report with status + all diagnostic counts.
    """
    blockers: list[str] = []

    # --- 1. Shadow decision count -------------------------------------------
    # Shoulder strategies use strategy_key values containing 'shoulder'
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM decision_events
            WHERE lower(strategy_key) LIKE '%shoulder%'
              AND outcome = 'TRADE'
            """
        ).fetchone()
        shadow_decision_count = int(row[0]) if row else 0
    except sqlite3.OperationalError:
        # decision_events table may not exist in minimal test contexts
        shadow_decision_count = 0

    # --- 2. Shoulder no-trade event count -----------------------------------
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM no_trade_events
            WHERE reason LIKE 'shoulder_%' OR reason LIKE 'SHOULDER_%'
            """
        ).fetchone()
        no_trade_count = int(row[0]) if row else 0
    except sqlite3.OperationalError:
        no_trade_count = 0

    # --- 3. Stress coverage count -------------------------------------------
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM tail_stress_scenarios"
        ).fetchone()
        stress_coverage_count = int(row[0]) if row else 0
    except sqlite3.OperationalError:
        stress_coverage_count = 0

    # --- 4. Regime variety (from stress scenarios temperature_metric) ---------
    # Regime variety = count of distinct temperature_metric values present
    # in tail_stress_scenarios (e.g. 'high' and 'low' = 2 regimes). This is
    # the correct proxy for shoulder regime coverage: both heat-dome (HIGH) and
    # cold-front (LOW) shoulder regimes should be represented before promotion.
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT temperature_metric) FROM tail_stress_scenarios"
        ).fetchone()
        regime_coverage_count = int(row[0]) if row else 0
    except sqlite3.OperationalError:
        regime_coverage_count = 0

    # --- 5. Total exposure from ledger ------------------------------------
    try:
        from src.state.shoulder_exposure_ledger import read_total_exposure_usd
        exposure_total_usd = read_total_exposure_usd(conn=conn)
    except (sqlite3.OperationalError, ImportError):
        exposure_total_usd = 0.0

    # --- Determine status (priority order) ----------------------------------
    if shadow_decision_count < MIN_SHADOW_DECISIONS:
        blockers.append(
            f"Insufficient shadow decisions: {shadow_decision_count} < {MIN_SHADOW_DECISIONS} required"
        )
        status = ReadinessStatus.INSUFFICIENT_SHADOW
    elif stress_coverage_count < MIN_STRESS_SCENARIOS:
        blockers.append(
            f"Insufficient stress coverage: {stress_coverage_count} < {MIN_STRESS_SCENARIOS} required"
        )
        status = ReadinessStatus.INSUFFICIENT_STRESS_COVERAGE
    elif regime_coverage_count < MIN_REGIME_VARIETY:
        blockers.append(
            f"Insufficient regime coverage: {regime_coverage_count} distinct regimes < {MIN_REGIME_VARIETY} required"
        )
        status = ReadinessStatus.INSUFFICIENT_REGIME_COVERAGE
    else:
        status = ReadinessStatus.READY_FOR_OPERATOR_REVIEW

    return ReadinessReport(
        status=status,
        shadow_decision_count=shadow_decision_count,
        no_trade_count=no_trade_count,
        stress_coverage_count=stress_coverage_count,
        regime_coverage_count=regime_coverage_count,
        exposure_total_usd=exposure_total_usd,
        blockers=blockers,
    )


def _report_to_markdown(report: ReadinessReport) -> str:
    """Format a ReadinessReport as a markdown string."""
    lines = [
        "# Shoulder Shadow Readiness Report",
        "",
        f"**Status**: `{report.status.value}`",
        "",
        "## Metrics",
        "",
        f"| Metric | Value | Threshold |",
        f"|--------|-------|-----------|",
        f"| Shadow decisions | {report.shadow_decision_count} | ≥{MIN_SHADOW_DECISIONS} |",
        f"| Stress scenarios | {report.stress_coverage_count} | ≥{MIN_STRESS_SCENARIOS} |",
        f"| Regime variety | {report.regime_coverage_count} | ≥{MIN_REGIME_VARIETY} |",
        f"| No-trade events | {report.no_trade_count} | (informational) |",
        f"| Total exposure USD | ${report.exposure_total_usd:.2f} | (informational) |",
        "",
    ]
    if report.blockers:
        lines += ["## Blockers", ""]
        for b in report.blockers:
            lines.append(f"- {b}")
        lines.append("")
    else:
        lines += [
            "## Result",
            "",
            "All thresholds met. Ready for operator review per dossier §12 promotion gate.",
            "",
            "> **IMPORTANT**: Operator must manually authorize live_status promotion.",
            "> This report never mutates live_status (plan §3.1 INV).",
            "",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Override world DB path (default: STATE_DIR/zeus-world.db)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="emit_json",
        help="Print JSON report to stdout",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        dest="emit_markdown",
        help="Print markdown report to stdout",
    )
    args = parser.parse_args()

    if args.db is None:
        from src.config import STATE_DIR
        db_path = STATE_DIR / "zeus-world.db"
    else:
        db_path = args.db

    conn = sqlite3.connect(str(db_path))
    try:
        report = evaluate_readiness(conn=conn)
    finally:
        conn.close()

    if args.emit_json:
        d = asdict(report)
        d["status"] = report.status.value
        print(json.dumps(d, indent=2))
    elif args.emit_markdown:
        print(_report_to_markdown(report))
    else:
        # Default: compact one-liner
        print(
            f"readiness_status={report.status.value} "
            f"shadow_decisions={report.shadow_decision_count} "
            f"stress_scenarios={report.stress_coverage_count} "
            f"regime_variety={report.regime_coverage_count} "
            f"exposure_usd={report.exposure_total_usd:.2f}"
        )
        if report.blockers:
            for b in report.blockers:
                print(f"  BLOCKER: {b}")


if __name__ == "__main__":
    main()
