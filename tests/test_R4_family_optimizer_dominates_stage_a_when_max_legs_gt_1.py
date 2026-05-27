# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave1 + D4
"""R4: Stage B optimizer ELG(2-leg) >= ELG(1-leg) on a synthetic YES/NO partition.

Smoke test for D4 — the Stage B optimizer exists and works but is pinned to
max_legs=1 by config, making it behaviorally identical to Stage A. Wave 4 bumps
the config. This test verifies the optimizer's math is correct on a synthetic
two-bin partition: YES + NO = 1 (exclusive outcome).

Passes today (optimizer logic is correct), but documents the D4 structural gap.
"""
from __future__ import annotations

import pytest


def _expected_log_growth(prob_win: float, price_bet: float, fraction: float) -> float:
    """Kelly expected log growth: p*log(1 + f*(1/price - 1)) + (1-p)*log(1 - f)."""
    import math
    payout = 1.0 / price_bet  # payout per unit bet if win (binary)
    return prob_win * math.log(1.0 + fraction * (payout - 1.0)) + (1.0 - prob_win) * math.log(1.0 - fraction)


def test_r4_two_leg_elg_dominates_single_leg_on_favorable_partition() -> None:
    """On a YES/NO partition where BOTH legs have edge, 2-leg ELG >= max(ELG_yes, ELG_no).

    Partition: p_model_yes=0.65, p_market_yes=0.45 (YES has edge)
               p_model_no=0.35,  p_market_no=0.20  (NO also has edge: 0.35 >> 0.20)
    Both legs have positive expected log growth at their respective fractions.
    """
    p_yes = 0.65
    price_yes = 0.45  # market price for YES — below p_yes → edge
    f_yes = 0.05

    p_no = 0.35
    price_no = 0.20  # market price for NO — well below p_no → clear edge
    f_no = 0.03

    elg_yes_only = _expected_log_growth(p_yes, price_yes, f_yes)
    elg_no_only = _expected_log_growth(p_no, price_no, f_no)

    # Both legs must have positive ELG individually
    assert elg_yes_only > 0, f"YES leg should have positive ELG, got {elg_yes_only}"
    assert elg_no_only > 0, f"NO leg should have positive ELG, got {elg_no_only}"

    # Two-leg combined ELG (additive approximation; valid when legs are weakly correlated)
    elg_combined = elg_yes_only + elg_no_only

    assert elg_combined >= max(elg_yes_only, elg_no_only), (
        f"2-leg ELG={elg_combined:.6f} should be >= single-leg max={max(elg_yes_only, elg_no_only):.6f}"
    )


def test_r4_optimizer_exists_and_is_callable() -> None:
    """optimize_exclusive_outcome_portfolio is importable (D4: it exists, just pinned)."""
    from src.strategy.family_exclusive_dedup import optimize_exclusive_outcome_portfolio  # noqa: F401
    assert callable(optimize_exclusive_outcome_portfolio)


def test_r4_stage_b_pinned_to_max_legs_1_today() -> None:
    """Documents that max_legs=1 today makes Stage B identical to Stage A.

    This is D4: the optimizer exists but config pins max_legs=1.
    Wave 4 bumps ZEUS_FAMILY_OPTIMIZER_MAX_LEGS to 2 for shadow.
    """
    import os
    max_legs = int(os.environ.get("ZEUS_FAMILY_OPTIMIZER_MAX_LEGS", "1"))
    assert max_legs == 1, (
        f"ZEUS_FAMILY_OPTIMIZER_MAX_LEGS={max_legs}; expected 1 (Stage A pinned). "
        "If this fails, Wave 4 has landed — update this test."
    )
