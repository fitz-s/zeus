# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md R6 proof.

import pytest

from src.contracts.execution_price import ExecutionPrice, ExecutionPriceContractError
from src.events.money_path_adapters import evaluate_fdr_full_family, evaluate_kelly, evaluate_riskguard
from src.riskguard.risk_level import RiskLevel


def _safe_price():
    return ExecutionPrice(0.40, "ask", fee_deducted=False, currency="probability_units").with_taker_fee()


def test_full_family_hypotheses_logged_before_fdr():
    result = evaluate_fdr_full_family(
        family_id="family-1",
        all_hypothesis_ids=("h1", "h2", "h3"),
        selected_hypothesis_ids=("h2",),
    )

    assert result.passed is True
    assert result.attempted_hypotheses == 3


def test_duplicate_event_does_not_change_family_denominator():
    result = evaluate_fdr_full_family(
        family_id="family-1",
        all_hypothesis_ids=("h1", "h2", "h3"),
        selected_hypothesis_ids=("h2",),
        duplicate_event=True,
    )

    assert result.passed is False
    assert result.attempted_hypotheses == 3


def test_kelly_requires_typed_execution_price():
    result = evaluate_kelly(kelly_decision_id="kelly-1", execution_price=_safe_price(), size_usd=5.0)

    assert result.passed is True


def test_kelly_bare_or_unsafe_float_forbidden():
    unsafe = ExecutionPrice(0.4, "implied_probability", fee_deducted=False, currency="probability_units")

    with pytest.raises(ExecutionPriceContractError):
        evaluate_kelly(kelly_decision_id="kelly-1", execution_price=unsafe, size_usd=5.0)


def test_riskguard_red_blocks():
    result = evaluate_riskguard(risk_decision_id="risk-1", level=RiskLevel.RED)

    assert result.passed is False
