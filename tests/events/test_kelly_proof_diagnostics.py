# Created: 2026-06-04
# Last reused or audited: 2026-06-04
# Authority basis: Operator GOAL 2026-06-04 — Kelly size=0 observability (zero-receipt root-cause)
"""Relationship tests for KellyProof diagnostic fields."""

from __future__ import annotations

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.events.money_path_adapters import evaluate_kelly
from src.sizing.sizing_context import SizingContext


def _fee_adjusted_price(value: float = 0.50) -> ExecutionPrice:
    return ExecutionPrice(
        value=value,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )


# ── Test (a): corr pressure soft-damps but does not hard-zero ────────────────

def test_kelly_corr_budget_pressure_stays_positive_for_positive_edge():
    """Corr budget pressure is a multiplier haircut, not a hard rejection.

    Fixture operates BELOW the restored INV-K3 single-position ceiling (0.05×B=$5).
    With p=0.65, price=0.50: f*=0.30, heat=1.0 (corr_committed exhausts corr budget),
    effective_multiplier=F_CAP/(1+1.0)=0.125, size=0.30×0.125×100=$3.75 < $5 ceiling.
    The binding_constraint stays 'sized_ok' (ceiling does not bind), isolating the
    corr-budget soft-haircut signal (effective_multiplier < F_CAP, heat≈1.0).
    """
    B = 100.0
    F_CAP = 0.25
    corr_committed = F_CAP * B  # == 25.0; exhausts corr ceiling
    raw_committed = 0.0

    ctx = SizingContext.from_candidate_proof_with_portfolio(
        q_posterior=0.65,
        q_lcb_5pct=0.63,
        lead_days=1.0,
        bankroll_usd=B,
        corr_committed_usd=corr_committed,
        raw_committed_usd=raw_committed,
    )
    proof = evaluate_kelly(
        kelly_decision_id="kelly-corr-budget-test",
        p_posterior=0.65,
        execution_price=_fee_adjusted_price(0.50),
        bankroll_usd=B,
        sizing_context=ctx,
        kelly_multiplier=F_CAP,
    )

    assert proof.passed is True, f"expected passed=True, got {proof.passed}"
    assert proof.size_usd > 0.0, f"expected positive size, got {proof.size_usd}"
    assert proof.binding_constraint == "sized_ok", (
        f"expected binding_constraint='sized_ok', got {proof.binding_constraint!r}"
    )
    # Diagnostic fields populated.
    assert proof.sizing_bankroll is not None
    assert proof.eff_corr_bankroll is not None
    assert proof.effective_multiplier is not None
    assert proof.portfolio_heat == pytest.approx(1.0)
    assert proof.effective_multiplier < F_CAP


def test_kelly_raw_heat_over_soft_budget_stays_positive_for_positive_edge():
    """Raw heat above the old 50% line is not a KELLY_REJECTED hard cap.

    Fixture operates BELOW the restored INV-K3 single-position ceiling (0.05×185=$9.25).
    raw_committed=98 is KEPT (mirrors live 52.9% raw-heat symptom). heat≈1.06 (raw budget
    pressure dominates), effective_multiplier≈0.25/2.06≈0.121. With p=0.72, price=0.55+fee:
    f*≈0.37, size≈$8.48 < $9.25 ceiling. binding_constraint stays 'sized_ok', isolating
    the soft-haircut signal (effective_multiplier<0.25, heat>1.0).
    """
    B = 185.0
    raw_committed = 98.0  # Mirrors live symptom: 52.9% raw heat.

    ctx = SizingContext.from_candidate_proof_with_portfolio(
        q_posterior=0.72,
        q_lcb_5pct=0.70,
        lead_days=1.0,
        bankroll_usd=B,
        corr_committed_usd=20.0,
        raw_committed_usd=raw_committed,
    )
    proof = evaluate_kelly(
        kelly_decision_id="kelly-raw-heat-soft-budget-test",
        p_posterior=0.72,
        execution_price=_fee_adjusted_price(0.55),
        bankroll_usd=B,
        sizing_context=ctx,
        kelly_multiplier=0.25,
    )

    assert proof.passed is True
    assert proof.size_usd > 0.0
    assert proof.binding_constraint == "sized_ok"
    assert proof.portfolio_heat is not None
    assert proof.portfolio_heat > 1.0
    assert proof.effective_multiplier is not None
    assert proof.effective_multiplier < 0.25


# ── Test (b): zero-edge candidate => zero_edge binding ───────────────────────

def test_kelly_binding_constraint_zero_edge_when_no_edge():
    """(b) p_posterior <= execution_price => kelly_size=0 regardless of bankroll.

    binding_constraint must be "zero_edge".
    """
    ep = _fee_adjusted_price(0.55)
    ctx = SizingContext.from_candidate_proof(
        q_posterior=0.50,
        q_lcb_5pct=0.48,
        lead_days=2.0,
    )
    proof = evaluate_kelly(
        kelly_decision_id="kelly-zero-edge-test",
        p_posterior=0.50,
        execution_price=ep,
        bankroll_usd=1000.0,
        sizing_context=ctx,
    )

    assert proof.passed is False, f"expected passed=False, got {proof.passed}"
    assert proof.size_usd == 0.0, f"expected size_usd=0.0, got {proof.size_usd}"
    assert proof.binding_constraint == "zero_edge", (
        f"expected binding_constraint='zero_edge', got {proof.binding_constraint!r}"
    )
