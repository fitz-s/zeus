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
        hypothesis_p_values={"h1": 0.80, "h2": 0.01, "h3": 0.70},
    )

    assert result.passed is True
    assert result.attempted_hypotheses == 3


def test_duplicate_event_does_not_change_family_denominator():
    result = evaluate_fdr_full_family(
        family_id="family-1",
        all_hypothesis_ids=("h1", "h2", "h3"),
        selected_hypothesis_ids=("h2",),
        hypothesis_p_values={"h1": 0.80, "h2": 0.01, "h3": 0.70},
        duplicate_event=True,
    )

    assert result.passed is False
    assert result.attempted_hypotheses == 3


def test_kelly_requires_typed_execution_price():
    result = evaluate_kelly(
        kelly_decision_id="kelly-1",
        p_posterior=0.64,
        execution_price=_safe_price(),
        bankroll_usd=100.0,
        kelly_multiplier=0.25,
    )

    assert result.passed is True
    assert result.size_usd > 0


def test_kelly_bare_or_unsafe_float_forbidden():
    unsafe = ExecutionPrice(0.4, "implied_probability", fee_deducted=False, currency="probability_units")

    with pytest.raises(ExecutionPriceContractError):
        evaluate_kelly(
            kelly_decision_id="kelly-1",
            p_posterior=0.64,
            execution_price=unsafe,
            bankroll_usd=100.0,
            kelly_multiplier=0.25,
        )


def test_riskguard_red_blocks():
    result = evaluate_riskguard(risk_decision_id="risk-1", level=RiskLevel.RED)

    assert result.passed is False
