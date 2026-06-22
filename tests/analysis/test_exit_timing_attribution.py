# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) + operator mandate "Trade that align with reality then get reversed
#   by correct physics and sell before market notice and gain is ALSO a good trade"
#   + "EVERY real chain decision audited with reality". The entry-skill grader
#   (settlement_skill_attribution) grades settlement payoff vs the ENTRY decision-q,
#   ignoring exit proceeds, so a skillful reversal exit (sold YES @0.85 right before
#   it settled 0) graded IDENTICALLY to a held-to-settlement loss. This adds the
#   SECOND, separable attribution axis: exit timing, graded against REAL settlement.
"""ANTIBODY: a skillful early/reversal exit is credited and a premature exit is
penalized, against real settlement, WITHOUT double-counting the entry skill.

Decomposition (entry-independent, the key property):
  realized_closed_lot_pnl = hold_counterfactual_pnl + exit_timing_alpha
where exit_timing_alpha = net_exit_proceeds - would_have_settled_value.
"""
from __future__ import annotations

import pytest

from src.analysis.exit_timing_attribution import grade_exit_timing


def test_skillful_reversal_exit_sold_high_before_loss():
    """YES sold @0.85 that later settles 0 (held side LOST): selling captured value
    the hold would have given up. With an exit q-authority + predictive reversal
    trigger, this is a SKILLFUL_REVERSAL_EXIT with positive alpha."""
    g = grade_exit_timing(
        closed_shares=10.0,
        avg_exit_price=0.85,
        exit_fees_usd=0.0,
        settlement_won=False,
        exit_q_authority_present=True,
        exit_trigger_reason="EDGE_REVERSED",
    )
    assert g.exit_alpha_usd == pytest.approx(8.5)  # 10*0.85 - 10*0.0
    assert g.category == "SKILLFUL_REVERSAL_EXIT"
    assert g.is_skillful is True
    assert g.counts_in_skill_denominator is True


def test_premature_exit_sold_low_before_win():
    """YES sold @0.20 that later settles 1 (held side WON): selling threw away value.
    Predictive exit that was wrong → PREMATURE_EXIT_COST, negative alpha, a skill MISS."""
    g = grade_exit_timing(
        closed_shares=10.0,
        avg_exit_price=0.20,
        exit_fees_usd=0.0,
        settlement_won=True,
        exit_q_authority_present=True,
        exit_trigger_reason="EDGE_REVERSED",
    )
    assert g.exit_alpha_usd == pytest.approx(-8.0)  # 10*0.20 - 10*1.0
    assert g.category == "PREMATURE_EXIT_COST"
    assert g.is_skillful is False
    assert g.counts_in_skill_denominator is True


def test_lucky_exit_saved_loss_without_predictive_evidence():
    """Positive alpha but no predictive reversal trigger (e.g. liquidity/operational
    exit that happened to save a loss) → LUCKY_EXIT_SAVED_LOSS, not skill."""
    g = grade_exit_timing(
        closed_shares=10.0,
        avg_exit_price=0.85,
        exit_fees_usd=0.0,
        settlement_won=False,
        exit_q_authority_present=True,
        exit_trigger_reason="LIQUIDITY",
    )
    assert g.exit_alpha_usd == pytest.approx(8.5)
    assert g.category == "LUCKY_EXIT_SAVED_LOSS"
    assert g.is_skillful is False
    assert g.counts_in_skill_denominator is False


def test_admin_or_risk_exit_reports_delta_excluded_from_skill():
    g = grade_exit_timing(
        closed_shares=10.0,
        avg_exit_price=0.85,
        exit_fees_usd=0.0,
        settlement_won=False,
        exit_q_authority_present=True,
        exit_trigger_reason="ADMIN",
    )
    assert g.exit_alpha_usd == pytest.approx(8.5)
    assert g.category == "ADMIN_OR_RISK_EXIT_VALUE_DELTA"
    assert g.counts_in_skill_denominator is False


def test_neutral_exit_within_materiality():
    """Sold at ~settlement value (won, sold @0.99): alpha within materiality → NEUTRAL."""
    g = grade_exit_timing(
        closed_shares=10.0,
        avg_exit_price=0.999,
        exit_fees_usd=0.0,
        settlement_won=True,
        exit_q_authority_present=True,
        exit_trigger_reason="EDGE_REVERSED",
        materiality_usd=0.05,
    )
    assert g.exit_alpha_usd == pytest.approx(-0.01)  # 9.99 - 10.0
    assert g.category == "NEUTRAL_EXIT"
    assert g.counts_in_skill_denominator is False


def test_partial_exit_graded_per_closed_lot():
    """Only the closed lot is graded; remaining shares settle separately."""
    g = grade_exit_timing(
        closed_shares=4.0,
        avg_exit_price=0.85,
        exit_fees_usd=0.0,
        settlement_won=False,
        exit_q_authority_present=True,
        exit_trigger_reason="EDGE_REVERSED",
    )
    assert g.exit_alpha_usd == pytest.approx(3.4)  # 4*0.85 - 4*0.0
    assert g.would_have_settled_value_usd == pytest.approx(0.0)
    assert g.net_exit_value_usd == pytest.approx(3.4)


def test_fees_reduce_net_exit_value():
    g = grade_exit_timing(
        closed_shares=10.0,
        avg_exit_price=0.85,
        exit_fees_usd=0.5,
        settlement_won=False,
        exit_q_authority_present=True,
        exit_trigger_reason="EDGE_REVERSED",
    )
    assert g.net_exit_value_usd == pytest.approx(8.0)  # 8.5 - 0.5
    assert g.exit_alpha_usd == pytest.approx(8.0)


def test_missing_settlement_is_unattributable():
    g = grade_exit_timing(
        closed_shares=10.0,
        avg_exit_price=0.85,
        exit_fees_usd=0.0,
        settlement_won=None,
        exit_q_authority_present=True,
        exit_trigger_reason="EDGE_REVERSED",
    )
    assert g.category == "EXIT_UNATTRIBUTABLE_SETTLEMENT_MISSING"
    assert g.exit_alpha_usd is None
    assert g.counts_in_skill_denominator is False


def test_missing_proceeds_is_unattributable():
    g = grade_exit_timing(
        closed_shares=10.0,
        avg_exit_price=None,
        exit_fees_usd=0.0,
        settlement_won=False,
        exit_q_authority_present=True,
        exit_trigger_reason="EDGE_REVERSED",
    )
    assert g.category == "EXIT_UNATTRIBUTABLE_PROCEEDS_MISSING"
    assert g.exit_alpha_usd is None
    assert g.counts_in_skill_denominator is False


def test_missing_exit_q_reports_value_but_not_skill():
    """Exit happened but no exit decision-q certificate: value delta still reported,
    but the exit cannot be attributed to skill."""
    g = grade_exit_timing(
        closed_shares=10.0,
        avg_exit_price=0.85,
        exit_fees_usd=0.0,
        settlement_won=False,
        exit_q_authority_present=False,
        exit_trigger_reason="EDGE_REVERSED",
    )
    assert g.exit_alpha_usd == pytest.approx(8.5)  # value still computed
    assert g.category == "EXIT_UNATTRIBUTABLE_Q_MISSING"
    assert g.counts_in_skill_denominator is False


def test_additive_decomposition_holds():
    """realized_closed_lot_pnl == hold_counterfactual_pnl + exit_timing_alpha.
    entry_cost cancels: (proceeds-entry) == (settle-entry) + (proceeds-settle)."""
    closed_shares, avg_exit_price, entry_price = 10.0, 0.85, 0.60
    g = grade_exit_timing(
        closed_shares=closed_shares,
        avg_exit_price=avg_exit_price,
        exit_fees_usd=0.0,
        settlement_won=False,
        exit_q_authority_present=True,
        exit_trigger_reason="EDGE_REVERSED",
    )
    entry_cost = closed_shares * entry_price
    realized = closed_shares * avg_exit_price - entry_cost
    hold_counterfactual = closed_shares * 0.0 - entry_cost  # lost → 0 payoff
    assert realized == pytest.approx(hold_counterfactual + g.exit_alpha_usd)
