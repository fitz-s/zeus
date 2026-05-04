# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_tigge_ingest_resilience/
#                  DESIGN_PHASE2_75_ROBUST_KELLY.md
#                  + may4math.md Finding 5 (CRITICAL_QUANT_RISK)
"""Phase 2.75 contract tests: robust lower-bound Kelly + SizingEvidence.

Relationship invariants:
    * For any inputs, robust f ≤ point f (strictly more conservative)
    * BLOCK / SHADOW_ONLY transfer status hard-zeros final size, even
      when the point estimate would size aggressively
    * Higher oracle posterior_upper → lower p_lower → smaller f_robust
    * SizingEvidence carries both point and robust legs for shadow
      comparison
    * Construction-time validation rejects malformed CIs
"""

from __future__ import annotations

import pytest

from src.strategy.robust_kelly import (
    SizingEvidence,
    SizingUncertaintyInputs,
    build_sizing_evidence,
    compute_p_lower,
    domain_mismatch_multiplier,
    robust_kelly_size,
)


def _inp(
    *,
    p_point=0.56,
    platt=(0.55, 0.57),
    dg=(0.54, 0.58),
    transfer=(0.55, 0.57),
    oracle_upper=0.005,
    cost_point=0.50,
    cost_eff_upper=0.51,
) -> SizingUncertaintyInputs:
    return SizingUncertaintyInputs(
        p_point=p_point,
        platt_param_ci=platt,
        decision_group_ci=dg,
        transfer_ci=transfer,
        oracle_posterior_upper=oracle_upper,
        cost_point=cost_point,
        cost_eff_upper=cost_eff_upper,
    )


def test_robust_kelly_le_point_kelly_for_tight_inputs():
    inp = _inp()
    ev = build_sizing_evidence(
        inputs=inp, base_lambda=0.25,
        m_strategy=1.0, m_oracle=1.0, m_cycle_domain=1.0, m_liquidity=1.0,
        bankroll=10_000.0,
    )
    assert ev.f_robust_kelly <= ev.f_point_kelly + 1e-12
    assert ev.final_size_units > 0.0


def test_robust_kelly_zero_when_p_lower_le_cost():
    """Wide CI lower bound below cost → no robust edge → f_robust=0."""
    inp = _inp(platt=(0.40, 0.60), dg=(0.40, 0.60), transfer=(0.40, 0.60),
               cost_point=0.45, cost_eff_upper=0.50)
    f = robust_kelly_size(inp, base_lambda=0.25)
    assert f == 0.0


def test_block_transfer_status_zeros_final_size_regardless_of_edge():
    """BLOCK is a hard gate. Even a 65% point edge sizes to 0."""
    inp = _inp(p_point=0.65, platt=(0.62, 0.68), dg=(0.62, 0.68),
               transfer=(0.62, 0.68))
    m = domain_mismatch_multiplier("BLOCK")
    ev = build_sizing_evidence(
        inputs=inp, base_lambda=0.25,
        m_strategy=1.0, m_oracle=1.0, m_cycle_domain=m, m_liquidity=1.0,
        bankroll=10_000.0,
    )
    assert ev.final_size_units == 0.0


def test_shadow_only_transfer_status_zeros_final_size():
    """SHADOW_ONLY also produces zero live size (no validated transfer)."""
    inp = _inp(p_point=0.65, platt=(0.62, 0.68), dg=(0.62, 0.68),
               transfer=(0.62, 0.68))
    m = domain_mismatch_multiplier("SHADOW_ONLY")
    ev = build_sizing_evidence(
        inputs=inp, base_lambda=0.25,
        m_strategy=1.0, m_oracle=1.0, m_cycle_domain=m, m_liquidity=1.0,
        bankroll=10_000.0,
    )
    assert ev.final_size_units == 0.0


@pytest.mark.parametrize(
    "days,expected",
    [
        (None, 1.0),  # exact match — full weight
        (10.0, 1.0),
        (30.0, 1.0),
        (45.0, 0.5),
        (90.0, 0.5),
        (120.0, 0.25),
        (365.0, 0.25),
    ],
)
def test_live_eligible_recency_downweighting(days, expected):
    assert domain_mismatch_multiplier(
        "LIVE_ELIGIBLE", days_since_validation=days
    ) == expected


def test_unknown_transfer_status_fails_closed():
    """Any status not in {BLOCK,SHADOW_ONLY,LIVE_ELIGIBLE} → 0.0.

    Fail-closed protects against silent acceptance of an unrecognized
    status string (e.g., a typo in a downstream caller).
    """
    assert domain_mismatch_multiplier("WHATEVER") == 0.0
    assert domain_mismatch_multiplier("") == 0.0


def test_higher_oracle_posterior_upper_reduces_p_lower():
    """Stronger oracle uncertainty → tighter lower bound → smaller size."""
    base = _inp(oracle_upper=0.001)
    high = _inp(oracle_upper=0.05)
    p_l_base = compute_p_lower(base)
    p_l_high = compute_p_lower(high)
    assert p_l_high < p_l_base


def test_construction_rejects_inverted_ci():
    with pytest.raises(ValueError, match="valid"):
        SizingUncertaintyInputs(
            p_point=0.56,
            platt_param_ci=(0.60, 0.55),  # inverted
            decision_group_ci=(0.54, 0.58),
            transfer_ci=(0.55, 0.57),
            oracle_posterior_upper=0.005,
            cost_point=0.50,
            cost_eff_upper=0.51,
        )


def test_construction_rejects_cost_eff_below_cost_point():
    """Effective execution cost cannot be cheaper than the mid quote."""
    with pytest.raises(ValueError, match="cost_eff_upper"):
        SizingUncertaintyInputs(
            p_point=0.56,
            platt_param_ci=(0.55, 0.57),
            decision_group_ci=(0.54, 0.58),
            transfer_ci=(0.55, 0.57),
            oracle_posterior_upper=0.005,
            cost_point=0.50,
            cost_eff_upper=0.45,
        )


def test_sizing_evidence_carries_both_kelly_legs():
    """Evidence row must carry point AND robust legs for post-trade replay."""
    inp = _inp()
    ev = build_sizing_evidence(
        inputs=inp, base_lambda=0.25,
        m_strategy=1.0, m_oracle=1.0, m_cycle_domain=1.0, m_liquidity=1.0,
        bankroll=10_000.0,
    )
    assert isinstance(ev, SizingEvidence)
    assert ev.f_point_kelly > 0.0
    assert ev.f_robust_kelly > 0.0
    assert ev.f_robust_kelly <= ev.f_point_kelly
    assert ev.sizing_policy_id.startswith("robust_kelly_v1")
    assert "platt_param_ci_low" in ev.components_uncertainty
    assert "oracle_posterior_upper" in ev.components_uncertainty


def test_invalid_base_lambda_rejected():
    inp = _inp()
    with pytest.raises(ValueError, match="base_lambda"):
        robust_kelly_size(inp, base_lambda=0.0)
    with pytest.raises(ValueError, match="base_lambda"):
        robust_kelly_size(inp, base_lambda=1.5)
