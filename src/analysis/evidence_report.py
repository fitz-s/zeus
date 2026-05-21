# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T4
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/07_PHASE_6_EVIDENCE_LADDER.md §Object model
"""EvidenceReport — per-strategy evidence aggregator.

Aggregates decision_events, no_trade_events, regret_decompositions, and
shadow_experiments data for a given strategy into a structured report consumed
by the LiveReadinessTribunal.

Bayesian Beta(2,2) credible interval
--------------------------------------
For a strategy with n observed decisions and k wins (edge-positive outcomes),
the posterior under a Beta(2,2) prior is Beta(2+k, 2+n-k). The 95% credible
interval lower bound is the 2.5th percentile of this posterior.

Beta(2,2) is a "weak" prior centered on 0.5 — appropriate when we have limited
prior information and want to avoid extreme extrapolation from small samples.

Promotion gate fires only when CI_lower > breakeven + cost_of_capital.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from scipy.stats import beta as scipy_beta

from src.contracts.evidence_tier import EvidenceTier


# ---------------------------------------------------------------------------
# Domain object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceReport:
    """Per-strategy evidence summary for tribunal input.

    Fields
    ------
    strategy_id:
        Strategy key.
    tier_observed:
        Current EvidenceTier from registry (what we have evidence for).
    n_decisions:
        Total shadow/paper decisions logged for this strategy.
    n_wins:
        Decisions with positive realized edge (win-rate numerator).
    n_no_trades:
        Structured no-trade events logged for this strategy. Current schema
        stores strategy_key directly; older compatibility rows are counted only
        when they carry the candidate_strategy_key marker in reason_detail.
    n_settled:
        Settled decisions (subset of n_decisions with known outcome).
    mean_regret_usd:
        Mean total_regret_usd across regret_decompositions rows.
    ci_lower:
        Lower bound of 95% Beta(2,2) credible interval on win-rate.
        None if n_settled == 0.
    ci_upper:
        Upper bound of 95% Beta(2,2) credible interval on win-rate.
        None if n_settled == 0.
    breakeven_win_rate:
        Strategy-specific breakeven win-rate (from profile metadata or caller).
    promotion_blockers:
        List of operator-recorded promotion blockers from registry.
    """
    strategy_id: str
    tier_observed: EvidenceTier
    n_decisions: int
    n_wins: int
    n_no_trades: int
    n_settled: int
    mean_regret_usd: float
    ci_lower: Optional[float]
    ci_upper: Optional[float]
    breakeven_win_rate: float
    promotion_blockers: tuple[str, ...] = ()


def _bayesian_ci(
    n_wins: int,
    n_trials: int,
    alpha_prior: float = 2.0,
    beta_prior: float = 2.0,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Beta(alpha_prior, beta_prior) posterior credible interval.

    Posterior: Beta(alpha_prior + n_wins, beta_prior + n_trials - n_wins).
    Returns (lower, upper) bounds for the given confidence level.
    """
    lower_p = (1.0 - confidence) / 2.0
    upper_p = 1.0 - lower_p
    a = alpha_prior + n_wins
    b = beta_prior + (n_trials - n_wins)
    lower = float(scipy_beta.ppf(lower_p, a, b))
    upper = float(scipy_beta.ppf(upper_p, a, b))
    return lower, upper


def build_evidence_report(
    strategy_id: str,
    tier_observed: EvidenceTier,
    *,
    conn: sqlite3.Connection,
    breakeven_win_rate: float = 0.5,
    promotion_blockers: tuple[str, ...] = (),
) -> EvidenceReport:
    """Build an EvidenceReport by querying the world DB.

    Queries:
      - decision_events: authoritative n_decisions denominator (strategy_key filter)
      - no_trade_events: structured strategy_key count, excluding degraded rows
      - regret_decompositions: n_settled, n_wins (total_regret_usd > 0 = WIN),
        mean_regret (supplemental; may lag decision_events)

    Sign convention: total_regret_usd > 0 means realized > counterfactual (WIN).
    Consistent with regret_decomposer.py SEV2-1 canonical convention.

    INV-37: caller supplies conn; never auto-opens.
    """
    # Count decisions from decision_events (authoritative denominator).
    # decision_events is the source of truth for how many decisions a strategy
    # produced; regret rows may lag or be absent, so using them as denominator
    # risks false HOLD/DEMOTE outcomes from an artificially shrunken sample.
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "decision_events" in tables:
        n_decisions_row = conn.execute(
            "SELECT COUNT(*) FROM decision_events WHERE strategy_key = ?",
            (strategy_id,),
        ).fetchone()
        n_decisions = int(n_decisions_row[0] or 0)
    else:
        n_decisions = 0

    # Win/regret analytics from regret_decompositions (supplemental).
    regret_row = conn.execute(
        """
        SELECT
            COUNT(*) as n_regret,
            SUM(CASE WHEN rd.total_regret_usd > 0 THEN 1 ELSE 0 END) as n_wins,
            AVG(rd.total_regret_usd) as mean_regret
        FROM regret_decompositions rd
        JOIN shadow_experiments se ON rd.experiment_id = se.experiment_id
        WHERE se.strategy_id = ?
        """,
        (strategy_id,),
    ).fetchone()

    n_wins = int(regret_row[1] or 0)
    mean_regret_usd = float(regret_row[2] or 0.0)
    n_settled = int(regret_row[0] or 0)  # rows with settled regret outcomes

    if "no_trade_events" in tables:
        no_trade_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(no_trade_events)").fetchall()
        }
        if "strategy_key" in no_trade_columns:
            if "schema_compatibility" in no_trade_columns:
                n_no_trade_row = conn.execute(
                    """
                    SELECT COUNT(*) FROM no_trade_events
                    WHERE strategy_key = ?
                      AND schema_compatibility = 'current'
                    """,
                    (strategy_id,),
                ).fetchone()
            else:
                n_no_trade_row = conn.execute(
                    """
                    SELECT COUNT(*) FROM no_trade_events
                    WHERE strategy_key = ?
                    """,
                    (strategy_id,),
                ).fetchone()
            n_no_trades = int(n_no_trade_row[0] or 0)
        else:
            n_no_trade_row = conn.execute(
                """
                SELECT COUNT(*) FROM no_trade_events
                WHERE reason_detail LIKE ?
                """,
                (f"%candidate_strategy_key={strategy_id};%",),
            ).fetchone()
            n_no_trades = int(n_no_trade_row[0] or 0)
    else:
        n_no_trades = 0

    # Bayesian CI
    ci_lower: Optional[float] = None
    ci_upper: Optional[float] = None
    if n_settled > 0:
        ci_lower, ci_upper = _bayesian_ci(n_wins, n_settled)

    return EvidenceReport(
        strategy_id=strategy_id,
        tier_observed=tier_observed,
        n_decisions=n_decisions,
        n_wins=n_wins,
        n_no_trades=n_no_trades,
        n_settled=n_settled,
        mean_regret_usd=mean_regret_usd,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        breakeven_win_rate=breakeven_win_rate,
        promotion_blockers=promotion_blockers,
    )
