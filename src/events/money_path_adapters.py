"""No-submit money-path adapter contracts for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.contracts.execution_price import ExecutionPrice
from src.riskguard.risk_level import RiskLevel

if TYPE_CHECKING:
    from src.sizing.sizing_context import SizingContext


@dataclass(frozen=True)
class FdrProof:
    fdr_family_id: str
    attempted_hypotheses: int
    selected_hypotheses: tuple[str, ...]
    selected_post_fdr: tuple[str, ...]
    passed: bool


@dataclass(frozen=True)
class KellyProof:
    kelly_decision_id: str
    execution_price: ExecutionPrice
    size_usd: float
    passed: bool


@dataclass(frozen=True)
class RiskProof:
    risk_decision_id: str
    level: RiskLevel
    passed: bool


def evaluate_fdr_full_family(
    *,
    family_id: str,
    all_hypothesis_ids: tuple[str, ...],
    selected_hypothesis_ids: tuple[str, ...],
    hypothesis_p_values: dict[str, float],
    passed_prefilter: dict[str, bool] | None = None,
    alpha: float | None = None,
    duplicate_event: bool = False,
) -> FdrProof:
    """Apply Zeus' canonical family-wise BH FDR over the full event family."""

    from src.strategy.fdr_filter import DEFAULT_FDR_ALPHA
    from src.strategy.selection_family import apply_familywise_fdr

    if duplicate_event:
        return FdrProof(family_id, len(all_hypothesis_ids), tuple(), tuple(), False)
    if not all_hypothesis_ids:
        raise ValueError("FDR requires full family hypotheses")
    missing = [hypothesis_id for hypothesis_id in all_hypothesis_ids if hypothesis_id not in hypothesis_p_values]
    if missing:
        raise ValueError(f"FDR requires p-values for every family hypothesis: {missing!r}")
    selected = tuple(h for h in selected_hypothesis_ids if h in set(all_hypothesis_ids))
    rows = [
        {
            "family_id": family_id,
            "hypothesis_id": hypothesis_id,
            "p_value": float(hypothesis_p_values[hypothesis_id]),
            "tested": True,
            "passed_prefilter": bool((passed_prefilter or {}).get(hypothesis_id, True)),
        }
        for hypothesis_id in all_hypothesis_ids
    ]
    selected_rows = apply_familywise_fdr(rows, q=DEFAULT_FDR_ALPHA if alpha is None else float(alpha))
    selected_post = tuple(
        str(row["hypothesis_id"])
        for row in selected_rows
        if bool(row.get("selected_post_fdr")) and bool(row.get("passed_prefilter"))
    )
    return FdrProof(
        fdr_family_id=family_id,
        attempted_hypotheses=len(all_hypothesis_ids),
        selected_hypotheses=selected,
        selected_post_fdr=selected_post,
        passed=any(hypothesis_id in selected_post for hypothesis_id in selected),
    )


def evaluate_kelly(
    *,
    kelly_decision_id: str,
    p_posterior: float,
    execution_price: ExecutionPrice,
    bankroll_usd: float,
    sizing_context: "SizingContext | None" = None,
    kelly_multiplier: float | None = None,
) -> KellyProof:
    """Run Zeus' typed Kelly sizing with the supplied executable price.

    S3 (variance-required Kelly, task #103/#111): the Kelly multiplier is
    no longer a flat scalar handed in by the caller. Callers pass a typed
    ``SizingContext`` carrying ``ci_width`` and ``lead_days``, and this
    adapter derives the multiplier via ``dynamic_kelly_mult`` so that size
    is NON-INCREASING in CI width (strictly smaller across a haircut
    threshold). ``dynamic_kelly_mult``'s ci_width haircut is STEPWISE
    (>0.10 → ×0.7, >0.15 → ×0.5), so two widths both under 0.10 size
    identically while widths straddling a threshold size strictly smaller.
    This makes the pre-S3 defect — variance silently dropped on the way to
    Kelly so two candidates identical except CI width sized identically —
    unconstructable at this boundary.

    The legacy ``kelly_multiplier`` flat-scalar parameter remains accepted
    for back-compat with callers not yet migrated (and is the explicit
    base for ``dynamic_kelly_mult`` when a context IS supplied). Exactly
    one of ``sizing_context`` / ``kelly_multiplier`` must be provided.

    ``KellyProof.passed = size_usd > 0`` — when the variance haircut (or a
    zero edge) collapses the size to 0.0, ``passed`` correctly flips
    True -> False.
    """

    from src.strategy.kelly import dynamic_kelly_mult, kelly_size

    if sizing_context is None and kelly_multiplier is None:
        raise ValueError(
            "evaluate_kelly requires either a SizingContext (variance-required, "
            "preferred) or a flat kelly_multiplier (legacy)"
        )

    execution_price.assert_kelly_safe()

    if sizing_context is not None:
        base = 0.25 if kelly_multiplier is None else float(kelly_multiplier)
        effective_multiplier = dynamic_kelly_mult(
            base=base,
            ci_width=float(sizing_context.ci_width),
            lead_days=float(sizing_context.lead_days),
        )
    else:
        effective_multiplier = float(kelly_multiplier)

    size_usd = kelly_size(
        float(p_posterior),
        execution_price,
        float(bankroll_usd),
        kelly_mult=effective_multiplier,
    )
    return KellyProof(
        kelly_decision_id=kelly_decision_id,
        execution_price=execution_price,
        size_usd=size_usd,
        passed=size_usd > 0,
    )


def evaluate_riskguard(*, risk_decision_id: str, level: RiskLevel) -> RiskProof:
    return RiskProof(
        risk_decision_id=risk_decision_id,
        level=level,
        passed=level is RiskLevel.GREEN,
    )
