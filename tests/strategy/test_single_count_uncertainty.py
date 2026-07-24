# Created: 2026-07-23
# Last reused/audited: 2026-07-23
# Authority basis: INV-40 current-q/global-solver single-count uncertainty repair
"""INV-40 antibodies for current-q global-solver Kelly composition."""

from __future__ import annotations

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.engine.event_reactor_adapter import (
    _single_count_current_q_global_solver_uncertainty,
)
from src.events.money_path_adapters import evaluate_kelly
from src.sizing.sizing_context import SizingContext
from src.strategy.kelly import dynamic_kelly_mult


def _price(value: float = 0.40) -> ExecutionPrice:
    return ExecutionPrice(
        value=value,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )


def _kelly(context: SizingContext):
    return evaluate_kelly(
        kelly_decision_id="inv-40",
        p_posterior=0.70,
        execution_price=_price(),
        bankroll_usd=100.0,
        sizing_context=context,
        kelly_multiplier=0.25,
    )


def test_current_q_global_solver_counts_ci_width_only_in_q_band():
    raw = SizingContext.from_candidate_proof(
        q_posterior=0.70,
        q_lcb_5pct=0.50,
        lead_days=1.0,
    )

    current = _single_count_current_q_global_solver_uncertainty(
        raw,
        global_actuation=object(),
    )
    proof = _kelly(current)

    assert raw.ci_width == pytest.approx(0.40)
    assert current.ci_width == 0.0
    assert current.counted_ci_width == pytest.approx(raw.ci_width)
    assert proof.effective_multiplier == pytest.approx(0.25)
    assert proof.size_usd == pytest.approx((0.70 - 0.40) / 0.60 * 0.25 * 100.0)


def test_non_global_kelly_keeps_ci_width_haircut():
    raw = SizingContext.from_candidate_proof(
        q_posterior=0.70,
        q_lcb_5pct=0.50,
        lead_days=1.0,
    )

    non_global = _single_count_current_q_global_solver_uncertainty(
        raw,
        global_actuation=None,
    )
    proof = _kelly(non_global)

    assert non_global is raw
    assert proof.effective_multiplier == pytest.approx(0.25 * 0.7 * 0.5)


def test_single_count_context_preserves_lead_and_portfolio_pressure():
    cool = SizingContext.from_candidate_proof_with_portfolio(
        q_posterior=0.70,
        q_lcb_5pct=0.50,
        lead_days=5.0,
        bankroll_usd=100.0,
        corr_committed_usd=0.0,
        raw_committed_usd=0.0,
    ).for_current_q_global_solver()
    hot = SizingContext.from_candidate_proof_with_portfolio(
        q_posterior=0.70,
        q_lcb_5pct=0.50,
        lead_days=5.0,
        bankroll_usd=100.0,
        corr_committed_usd=20.0,
        raw_committed_usd=20.0,
    ).for_current_q_global_solver()

    cool_proof = _kelly(cool)
    hot_proof = _kelly(hot)

    assert hot.lead_days == cool.lead_days == 5.0
    assert hot.bankroll_usd == cool.bankroll_usd == 100.0
    assert hot.corr_committed_usd == hot.raw_committed_usd == 20.0
    assert hot.counted_ci_width == cool.counted_ci_width == pytest.approx(0.40)
    assert cool_proof.effective_multiplier == pytest.approx(
        dynamic_kelly_mult(base=0.25, ci_width=0.0, lead_days=5.0)
    )
    assert 0.0 < hot_proof.effective_multiplier < cool_proof.effective_multiplier
