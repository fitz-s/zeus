# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) + operator mandate. SECOND attribution axis (exit timing), separable
#   from and composable with the entry-skill grader in settlement_skill_attribution.
"""Exit-timing attribution — grade the EXIT decision against real settlement.

The entry-skill grader (``settlement_skill_attribution``) answers "was the original
entry aligned with settlement reality if held?" — it grades settlement payoff vs the
immutable entry decision-q and is unchanged by this module.

This module adds the orthogonal question the operator demands ("sell before market
notice and gain is ALSO a good trade"): "given the entry existed, did the EXIT improve
realized value versus the counterfactual hold-to-settlement value for the shares it
closed?"

Per-closed-lot counterfactual (entry-independent — entry_cost cancels, so this never
double-counts entry skill):

    would_have_settled_value_usd = closed_shares * settlement_payoff_per_share
    net_exit_value_usd           = closed_shares * avg_exit_price - exit_fees_usd
    exit_alpha_usd               = net_exit_value_usd - would_have_settled_value_usd

and the decomposition realized_closed_lot_pnl = hold_counterfactual_pnl + exit_alpha_usd
holds identically. settlement_payoff_per_share is 1.0 when the held native token's side
won, else 0.0 (long YES/NO sell-to-close; see load-bearing assumption in the consult).

Forward, real-chain only: a grade is produced ONLY after verified settlement; missing
settlement / proceeds / exit-q are branded UNATTRIBUTABLE, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

# Trigger reasons that constitute PREDICTIVE evidence against the held side (the exit
# was a model/physics/family-rank reversal call, not an operational/forced exit).
_PREDICTIVE_EXIT_TRIGGERS = frozenset(
    {
        "EDGE_REVERSED",
        "CI_SEPARATED_REVERSAL",
        "FAMILY_RANK",
        "FAMILY_RANK_REVERSAL",
        "PHYSICS_REVERSAL",
        "DAY0_HARD_FACT_EXIT",
        "SPURIOUS_MODEL_DIVERGENCE",
        "STRUCTURAL_WIN",
    }
)

# Triggers that are operational/forced rather than predictive — value delta is recorded
# but excluded from the exit-skill denominator (not a model-skill signal).
_ADMIN_RISK_EXIT_TRIGGERS = frozenset(
    {
        "ADMIN",
        "ADMIN_CLOSE",
        "RISK",
        "RISKGUARD",
        "KILL_SWITCH",
        "CUTOVER",
        "REDUCE_ONLY",
        "MANUAL",
    }
)


@dataclass(frozen=True)
class ExitTimingGrade:
    """Settlement-grounded grade of one closed lot's EXIT decision."""

    category: str
    exit_alpha_usd: float | None  # None only when value itself is unprovable
    net_exit_value_usd: float | None
    would_have_settled_value_usd: float | None
    is_skillful: bool
    counts_in_skill_denominator: bool
    rationale: str


def grade_exit_timing(
    *,
    closed_shares: float,
    avg_exit_price: float | None,
    exit_fees_usd: float = 0.0,
    settlement_won: bool | None,
    exit_q_authority_present: bool,
    exit_trigger_reason: str | None,
    materiality_usd: float = 0.01,
) -> ExitTimingGrade:
    """Grade one closed lot's exit timing against verified settlement.

    ``settlement_won`` is the held side's realized settlement outcome (True=won,
    False=lost, None=not yet settled). ``avg_exit_price`` is per-share proceeds
    (None when the exit fill/proceeds are not provable). ``exit_q_authority_present``
    is whether an immutable exit decision-q certificate exists for this exit (so the
    exit can be attributed to the EXIT decision, not the entry). ``exit_trigger_reason``
    is the exit cause (predictive vs operational).
    """

    trigger = (exit_trigger_reason or "").strip().upper()

    # --- unattributable gates (cannot grade value or skill) ---
    if settlement_won is None:
        return ExitTimingGrade(
            category="EXIT_UNATTRIBUTABLE_SETTLEMENT_MISSING",
            exit_alpha_usd=None,
            net_exit_value_usd=None,
            would_have_settled_value_usd=None,
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale="no verified settlement yet; exit value cannot be graded against reality.",
        )
    if avg_exit_price is None:
        return ExitTimingGrade(
            category="EXIT_UNATTRIBUTABLE_PROCEEDS_MISSING",
            exit_alpha_usd=None,
            net_exit_value_usd=None,
            would_have_settled_value_usd=None,
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale="settlement exists but exit fill/proceeds are not provable.",
        )

    # --- value computation (entry-independent counterfactual) ---
    settlement_payoff_per_share = 1.0 if settlement_won else 0.0
    net_exit_value_usd = closed_shares * float(avg_exit_price) - float(exit_fees_usd)
    would_have_settled_value_usd = closed_shares * settlement_payoff_per_share
    exit_alpha_usd = net_exit_value_usd - would_have_settled_value_usd

    base = dict(
        exit_alpha_usd=exit_alpha_usd,
        net_exit_value_usd=net_exit_value_usd,
        would_have_settled_value_usd=would_have_settled_value_usd,
    )

    # --- exit-q gate: value reported, but skill cannot be attributed ---
    if not exit_q_authority_present:
        return ExitTimingGrade(
            category="EXIT_UNATTRIBUTABLE_Q_MISSING",
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale=(
                f"exit realized {exit_alpha_usd:+.4f} vs counterfactual hold, but no exit "
                "decision-q certificate — value reported, not attributable to model skill."
            ),
            **base,
        )

    # --- operational/forced exits: report delta, exclude from skill denominator ---
    if trigger in _ADMIN_RISK_EXIT_TRIGGERS:
        return ExitTimingGrade(
            category="ADMIN_OR_RISK_EXIT_VALUE_DELTA",
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale=(
                f"forced exit (trigger={trigger}) realized {exit_alpha_usd:+.4f} vs hold; "
                "value recorded but excluded from model-skill denominator."
            ),
            **base,
        )

    # --- neutral: sold at ~settlement value ---
    if abs(exit_alpha_usd) <= float(materiality_usd):
        return ExitTimingGrade(
            category="NEUTRAL_EXIT",
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale=(
                f"exit value {exit_alpha_usd:+.4f} within materiality "
                f"{materiality_usd:.4f}; no meaningful timing edge."
            ),
            **base,
        )

    predictive = trigger in _PREDICTIVE_EXIT_TRIGGERS

    # --- positive alpha: sold above the counterfactual hold value ---
    if exit_alpha_usd > 0:
        if predictive:
            return ExitTimingGrade(
                category="SKILLFUL_REVERSAL_EXIT",
                is_skillful=True,
                counts_in_skill_denominator=True,
                rationale=(
                    f"predictive exit (trigger={trigger}) captured {exit_alpha_usd:+.4f} vs "
                    "hold-to-settlement — sold before the market priced the reversal; real exit skill."
                ),
                **base,
            )
        return ExitTimingGrade(
            category="LUCKY_EXIT_SAVED_LOSS",
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale=(
                f"exit saved {exit_alpha_usd:+.4f} vs hold but trigger={trigger or 'none'} is not "
                "predictive evidence against the held side — lucky, not skill."
            ),
            **base,
        )

    # --- negative alpha with predictive q-authority exit: a genuine skill MISS ---
    return ExitTimingGrade(
        category="PREMATURE_EXIT_COST",
        is_skillful=False,
        counts_in_skill_denominator=True,
        rationale=(
            f"predictive exit (trigger={trigger}) gave up {exit_alpha_usd:+.4f} vs holding to "
            "settlement — premature; counts as an exit-skill MISS."
        ),
        **base,
    )
