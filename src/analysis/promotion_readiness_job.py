# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: task brief (Track R-1a §5) + PHASE_6_PLAN.md §T4
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/
#                    PROMO_VALIDATOR_CRITIC.md
"""Promotion Readiness Job — offline ranked readiness report.

Offline (no daemon coupling). Reads from a WORLD DB conn, runs:
    EvidenceReport → pure tribunal verdict (no DB write) → PromotionReadinessValidator

and writes a ranked readiness report to .omc/research/promotion_readiness_{timestamp}.json.

OPERATOR_REF GUARD: promotion to any live tier (>= LIVE_PILOT_TINY) requires
a non-empty operator_ref; the validator raises ValueError if missing.
This guard is preserved at the job level: pass operator_ref only when a human
operator has explicitly authorised the promotion action.

PURE-COMPUTE mode: the tribunal verdict is computed via promotion_predicate() +
the demotion condition directly — adjudicate() is NOT called (it requires a live
conn and raises RuntimeError on PROMOTE/DEMOTE when conn=None).
The job never writes a tier. It emits a recommendation only.

Usage:
    python -m src.analysis.promotion_readiness_job \\
        --world-db /path/to/world.db \\
        --strategies shoulder_sell \\
        [--operator-ref "PR#NNN" ]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.analysis.evidence_report import build_evidence_report
from src.analysis.live_readiness_tribunal import (
    TribunalVerdict,
    VerdictKind,
    promotion_predicate,
)
from src.analysis.promotion_readiness import PromotionReadinessValidator
from src.contracts.evidence_tier import EvidenceTier


# ---------------------------------------------------------------------------
# Strategy defaults
# ---------------------------------------------------------------------------

_STRATEGY_DEFAULTS: dict[str, dict] = {
    "shoulder_sell": {
        "tier_observed": EvidenceTier.REPLAY_PASS,
        "tier_required_for_live": EvidenceTier.LIVE_LIMITED_HAIRCUT,
        "breakeven_win_rate": 0.52,
        "cost_of_capital": 0.02,
    },
}

_DEFAULT_TIER_REQUIRED = EvidenceTier.LIVE_LIMITED_HAIRCUT
_DEFAULT_BREAKEVEN = 0.5
_DEFAULT_COST_OF_CAPITAL = 0.0


# ---------------------------------------------------------------------------
# Core job function
# ---------------------------------------------------------------------------

def run_promotion_readiness_job(
    strategy_ids: list[str],
    *,
    conn: sqlite3.Connection,
    operator_ref: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> dict:
    """Run the promotion readiness assessment for a list of strategies.

    Parameters
    ----------
    strategy_ids:
        List of strategy IDs to assess.
    conn:
        World DB connection (caller-supplied, INV-37). Read-only sufficient;
        adjudicate() is called in pure-compute mode (conn=None for writes).
    operator_ref:
        Operator reference string (PR number, JIRA ticket, etc.).
        Required for any strategy where tier_target >= LIVE_PILOT_TINY.
        Must be explicitly supplied by a human operator; never auto-filled.
    output_dir:
        Directory to write the JSON report. Defaults to .omc/research/ in the
        repo root.

    Returns
    -------
    dict
        Structured readiness report suitable for JSON serialisation.
    """
    report_rows = []

    for strategy_id in strategy_ids:
        defaults = _STRATEGY_DEFAULTS.get(strategy_id, {})
        tier_observed = defaults.get("tier_observed", EvidenceTier.REPLAY_PASS)
        tier_required = defaults.get("tier_required_for_live", _DEFAULT_TIER_REQUIRED)
        breakeven = defaults.get("breakeven_win_rate", _DEFAULT_BREAKEVEN)
        cost_of_capital = defaults.get("cost_of_capital", _DEFAULT_COST_OF_CAPITAL)

        evidence = build_evidence_report(
            strategy_id,
            tier_observed,
            conn=conn,
            breakeven_win_rate=breakeven,
        )

        # Pure-compute tribunal verdict via promotion_predicate + demotion check.
        # adjudicate() is NOT used here: it calls _write_verdict_row() on PROMOTE/DEMOTE
        # and raises RuntimeError when conn=None. This job is read-only.
        tier_current = evidence.tier_observed
        ci_lower = evidence.ci_lower
        breakeven = evidence.breakeven_win_rate

        if promotion_predicate(tier_current, tier_required, ci_lower, breakeven, cost_of_capital):
            _tier_target = EvidenceTier(min(7, tier_current.value + 1))
            _verdict_kind = VerdictKind.PROMOTE
            _verdict_reason = (
                f"PROMOTE: ci_lower={ci_lower:.4f} > breakeven={breakeven:.4f} + "
                f"cost_of_capital={cost_of_capital:.4f}; "
                f"{tier_current.name} → {_tier_target.name} [advisory; operator apply required]"
            )
        elif ci_lower is not None and ci_lower < breakeven - cost_of_capital:
            _tier_target = EvidenceTier(max(0, tier_current.value - 1))
            _verdict_kind = VerdictKind.DEMOTE
            _verdict_reason = (
                f"DEMOTE: ci_lower={ci_lower:.4f} < breakeven={breakeven:.4f} - "
                f"cost_of_capital={cost_of_capital:.4f}; "
                f"{tier_current.name} → {_tier_target.name} [advisory; operator apply required]"
            )
        else:
            _tier_target = tier_current
            _verdict_kind = VerdictKind.HOLD
            _verdict_reason = (
                f"HOLD: ci_lower={ci_lower!r}, breakeven={breakeven:.4f}; "
                f"insufficient evidence to promote or demote"
            )

        tribunal_verdict = TribunalVerdict(
            strategy_id=strategy_id,
            verdict=_verdict_kind,
            tier_current=tier_current,
            tier_target=_tier_target,
            verdict_reason=_verdict_reason,
            ci_lower=ci_lower,
        )

        # PromotionReadinessValidator: operator_ref guard preserved
        validator = PromotionReadinessValidator(
            tier_required_for_live=tier_required,
            cost_of_capital=cost_of_capital,
        )
        # operator_ref passed through; validator raises if tier_target >= LIVE_PILOT_TINY
        # and operator_ref is missing/whitespace.
        assessment = validator.assess(evidence, operator_ref=operator_ref)

        report_rows.append({
            "strategy_id": strategy_id,
            "tier_observed": tier_observed.name,
            "tier_required_for_live": tier_required.name,
            "n_decisions": evidence.n_decisions,
            "n_settled": evidence.n_settled,
            "n_wins": evidence.n_wins,
            "ci_lower": evidence.ci_lower,
            "ci_upper": evidence.ci_upper,
            "tribunal_verdict": tribunal_verdict.verdict.value,
            "tribunal_rationale": tribunal_verdict.verdict_reason,
            "readiness_verdict": assessment.verdict.value,
            "readiness_summary": assessment.summary,
            "operator_ref_required": assessment.operator_ref_required,
            "signals": [
                {
                    "signal_name": s.signal_name,
                    "passed": s.passed,
                    "rationale": s.rationale,
                }
                for s in assessment.signals
            ],
        })

    # Sort by readiness (READY first, then by n_settled desc)
    def _sort_key(row: dict) -> tuple:
        return (
            0 if row["readiness_verdict"] == "READY" else 1,
            -(row.get("n_settled") or 0),
        )
    report_rows.sort(key=_sort_key)

    report = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "operator_ref": operator_ref or "",
        "strategies": report_rows,
    }

    # Write to .omc/research/
    if output_dir is None:
        # Find repo root by walking up from this file
        repo_root = Path(__file__).resolve().parent.parent.parent
        output_dir = repo_root / ".omc" / "research"

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = output_dir / f"promotion_readiness_{ts}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"[promotion_readiness_job] Report written: {out_path}")

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promotion Readiness Job — offline ranked strategy readiness report."
    )
    parser.add_argument(
        "--world-db",
        dest="world_db",
        required=True,
        help="Path to world DB (read-only access sufficient).",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["shoulder_sell"],
        help="Strategy IDs to assess.",
    )
    parser.add_argument(
        "--operator-ref",
        dest="operator_ref",
        default=None,
        help=(
            "Operator reference (PR number, ticket ID). "
            "REQUIRED for any strategy targeting a live tier."
        ),
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help="Output directory for JSON report (default: .omc/research/).",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{Path(args.world_db).as_posix()}?mode=ro", uri=True)
    try:
        report = run_promotion_readiness_job(
            strategy_ids=args.strategies,
            conn=conn,
            operator_ref=args.operator_ref,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
    finally:
        conn.close()

    # Print ranked summary to stdout
    print(f"\n[promotion_readiness_job] Assessed {len(report['strategies'])} strategies")
    for row in report["strategies"]:
        print(
            f"  {row['strategy_id']:30s} "
            f"verdict={row['readiness_verdict']:10s} "
            f"n_settled={row['n_settled']:5d} "
            f"ci_lower={row['ci_lower']!r:10} "
            f"tribunal={row['tribunal_verdict']}"
        )


if __name__ == "__main__":
    main()
