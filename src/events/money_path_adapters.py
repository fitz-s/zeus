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
    duplicate_event: bool = False,
) -> FdrProof:
    if duplicate_event:
        return FdrProof(family_id, len(all_hypothesis_ids), tuple(), False)
    if not all_hypothesis_ids:
        raise ValueError("FDR requires full family hypotheses")
    selected = tuple(h for h in selected_hypothesis_ids if h in set(all_hypothesis_ids))
    return FdrProof(family_id, len(all_hypothesis_ids), selected, bool(selected))


def evaluate_kelly(
    *,
    kelly_decision_id: str,
    execution_price: ExecutionPrice,
    size_usd: float,
) -> KellyProof:
    execution_price.assert_kelly_safe()
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
