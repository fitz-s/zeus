# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T4
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/07_PHASE_6_EVIDENCE_LADDER.md §LiveReadinessTribunal
"""LiveReadinessTribunal — evidence-driven tier promotion/demotion adjudicator.

Adjudicates tier transitions based on EvidenceReport inputs. Outputs
TribunalVerdict (PROMOTE / HOLD / DEMOTE) with rationale and tier_target.

Design constraints
------------------
- NO auto-promotion: mechanism only; operator-gated. Tribunal proposes; operator decides.
- NO LIVE_NORMAL downgrade without operator approval (DEMOTE targets at most LIVE_LIMITED_HAIRCUT
  for LIVE_NORMAL strategies unless caller explicitly overrides).
- Tribunal emits tier_target ONLY; Kelly multiplier resolved downstream by caller.
- DB write on PROMOTE and DEMOTE (writes row to evidence_tier_assignments with verdict_reason).
- HOLD: no DB write; verdict_reason populated for logging.

INV-37: DB writes accept caller-supplied conn; never auto-open.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from src.contracts.evidence_tier import EvidenceTier
from src.analysis.evidence_report import EvidenceReport


# ---------------------------------------------------------------------------
# Verdict enum
# ---------------------------------------------------------------------------

class VerdictKind(str, Enum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    DEMOTE = "DEMOTE"


# ---------------------------------------------------------------------------
# Domain object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TribunalVerdict:
    """Output of a single tribunal adjudication.

    Fields
    ------
    strategy_id:
        Strategy key adjudicated.
    verdict:
        PROMOTE / HOLD / DEMOTE.
    tier_current:
        EvidenceTier at time of adjudication.
    tier_target:
        Proposed tier after verdict. Same as tier_current for HOLD.
    verdict_reason:
        Human-readable rationale string.
    ci_lower:
        Credible interval lower bound used in decision (None if n_settled=0).
    """
    strategy_id: str
    verdict: VerdictKind
    tier_current: EvidenceTier
    tier_target: EvidenceTier
    verdict_reason: str
    ci_lower: Optional[float] = None


# ---------------------------------------------------------------------------
# Adjudication logic
# ---------------------------------------------------------------------------

def adjudicate(
    report: EvidenceReport,
    tier_required_for_live: EvidenceTier,
    *,
    cost_of_capital: float = 0.0,
    conn: Optional[sqlite3.Connection] = None,
    operator_ref: Optional[str] = None,
    allow_live_normal_demote: bool = False,
) -> TribunalVerdict:
    """Adjudicate a single strategy's evidence report.

    Promotion rule: tier_observed < tier_required_for_live AND
                    ci_lower > breakeven_win_rate + cost_of_capital.

    Demotion rule:  ci_lower is not None AND
                    ci_lower < breakeven_win_rate - cost_of_capital
                    (evidence of underperformance).
                    LIVE_NORMAL is NOT demoted unless allow_live_normal_demote=True.

    DB write on PROMOTE or DEMOTE: inserts row into evidence_tier_assignments
    with verdict_reason populated. INV-37: conn must be caller-supplied.

    Parameters
    ----------
    report:
        EvidenceReport for this strategy.
    tier_required_for_live:
        The minimum EvidenceTier required for runtime liveness (from strategy profile).
    cost_of_capital:
        Additional margin above breakeven required to promote.
    conn:
        World DB connection for writing verdict row (required for PROMOTE/DEMOTE).
    operator_ref:
        Optional operator reference string for the DB row.
    allow_live_normal_demote:
        If False (default), LIVE_NORMAL strategies are never demoted by tribunal.

    Returns
    -------
    TribunalVerdict
    """
    tier_current = report.tier_observed
    breakeven = report.breakeven_win_rate
    ci_lower = report.ci_lower

    # --- HOLD: tier insufficient but no evidence to promote ---
    # Check demotion first (failing evidence overrides "just hold")
    if ci_lower is not None and ci_lower < breakeven - cost_of_capital:
        # Evidence of underperformance → DEMOTE
        if tier_current == EvidenceTier.LIVE_NORMAL and not allow_live_normal_demote:
            # Never auto-demote LIVE_NORMAL without operator override
            verdict_reason = (
                f"DEMOTE suppressed for LIVE_NORMAL: ci_lower={ci_lower:.4f} < "
                f"breakeven={breakeven:.4f}; operator must approve LIVE_NORMAL demotion"
            )
            verdict = TribunalVerdict(
                strategy_id=report.strategy_id,
                verdict=VerdictKind.HOLD,
                tier_current=tier_current,
                tier_target=tier_current,
                verdict_reason=verdict_reason,
                ci_lower=ci_lower,
            )
            return verdict

        tier_target = EvidenceTier(max(0, tier_current.value - 1))
        verdict_reason = (
            f"DEMOTE: ci_lower={ci_lower:.4f} < breakeven={breakeven:.4f} "
            f"(cost_of_capital={cost_of_capital:.4f}); "
            f"{tier_current.name} → {tier_target.name}"
        )
        _write_verdict_row(
            strategy_id=report.strategy_id,
            tier=tier_target,
            verdict_reason=verdict_reason,
            operator_ref=operator_ref,
            conn=conn,
        )
        return TribunalVerdict(
            strategy_id=report.strategy_id,
            verdict=VerdictKind.DEMOTE,
            tier_current=tier_current,
            tier_target=tier_target,
            verdict_reason=verdict_reason,
            ci_lower=ci_lower,
        )

    # --- Check promotion: tier below required AND CI clears threshold ---
    if (
        tier_current < tier_required_for_live
        and ci_lower is not None
        and ci_lower > breakeven + cost_of_capital
    ):
        tier_target = EvidenceTier(min(7, tier_current.value + 1))
        verdict_reason = (
            f"PROMOTE: ci_lower={ci_lower:.4f} > breakeven={breakeven:.4f} + "
            f"cost_of_capital={cost_of_capital:.4f}; "
            f"{tier_current.name} → {tier_target.name}"
        )
        _write_verdict_row(
            strategy_id=report.strategy_id,
            tier=tier_target,
            verdict_reason=verdict_reason,
            operator_ref=operator_ref,
            conn=conn,
        )
        return TribunalVerdict(
            strategy_id=report.strategy_id,
            verdict=VerdictKind.PROMOTE,
            tier_current=tier_current,
            tier_target=tier_target,
            verdict_reason=verdict_reason,
            ci_lower=ci_lower,
        )

    # --- HOLD ---
    if ci_lower is None:
        verdict_reason = (
            f"HOLD: no settled decisions yet (n_settled=0); "
            f"tier_observed={tier_current.name} < required={tier_required_for_live.name}"
            if tier_current < tier_required_for_live
            else f"HOLD: no settled decisions; tier_observed={tier_current.name}"
        )
    elif tier_current >= tier_required_for_live:
        verdict_reason = (
            f"HOLD: tier_observed={tier_current.name} >= required={tier_required_for_live.name}; "
            f"no promotion needed"
        )
    else:
        verdict_reason = (
            f"HOLD: ci_lower={ci_lower:.4f} not above threshold "
            f"breakeven={breakeven:.4f} + cost_of_capital={cost_of_capital:.4f}; "
            f"tier_observed={tier_current.name} < required={tier_required_for_live.name}"
        )

    return TribunalVerdict(
        strategy_id=report.strategy_id,
        verdict=VerdictKind.HOLD,
        tier_current=tier_current,
        tier_target=tier_current,
        verdict_reason=verdict_reason,
        ci_lower=ci_lower,
    )


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def _write_verdict_row(
    strategy_id: str,
    tier: EvidenceTier,
    verdict_reason: str,
    operator_ref: Optional[str],
    conn: Optional[sqlite3.Connection],
) -> None:
    """Insert a row into evidence_tier_assignments.

    Intended reader contract
    -----------------------
    The latest tier for a strategy is determined by MAX(assigned_at) in
    evidence_tier_assignments.  Any auto-apply reader MUST also verify
    operator_ref IS NOT NULL before acting on a PROMOTE row into live tiers;
    rows with operator_ref=NULL are advisory only and must not be acted on
    without explicit operator confirmation.

    Operator-gate invariant
    -----------------------
    Any PROMOTE verdict targeting a live tier (tier >= LIVE_PILOT_TINY) MUST
    have a non-empty operator_ref.  The Tribunal proposes; operator approves.
    This prevents a future auto-apply reader from silently promoting strategies
    into live execution without an explicit operator trace.

    Raises
    ------
    RuntimeError
        If conn is None (DB write required for PROMOTE/DEMOTE).
    ValueError
        If tier >= LIVE_PILOT_TINY and operator_ref is None or empty.
        A PROMOTE into a live tier without operator reference is rejected
        fail-closed.

    INV-37: never auto-opens a connection.
    """
    if conn is None:
        raise RuntimeError(
            "LiveReadinessTribunal: conn is required for PROMOTE/DEMOTE DB write. "
            "Supply a world DB connection via the conn= parameter."
        )
    if tier >= EvidenceTier.LIVE_PILOT_TINY and not operator_ref:
        raise ValueError(
            f"Operator-gate violation: PROMOTE/DEMOTE targeting live tier "
            f"{tier.name} (>= LIVE_PILOT_TINY) requires a non-empty operator_ref. "
            "The Tribunal proposes; an operator must approve live-tier transitions."
        )
    assigned_at = datetime.now(tz=timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO evidence_tier_assignments
            (strategy_id, tier, assigned_at, rationale, operator_ref, verdict_reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_id,
            int(tier),
            assigned_at,
            f"Tribunal verdict: {tier.name}",
            operator_ref,
            verdict_reason,
        ),
    )
