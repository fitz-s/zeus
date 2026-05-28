"""No-submit money-path adapter contracts for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.execution_price import ExecutionPrice
from src.riskguard.risk_level import RiskLevel


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
    kelly_multiplier: float,
) -> KellyProof:
    """Run Zeus' typed Kelly sizing with the supplied executable price."""

    from src.strategy.kelly import kelly_size

    execution_price.assert_kelly_safe()
    size_usd = kelly_size(
        float(p_posterior),
        execution_price,
        float(bankroll_usd),
        kelly_mult=float(kelly_multiplier),
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
