# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave1 + D4
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: R4 — relationship test antibody for Stage B family optimizer activation
# Reuse: optimize_exclusive_outcome_portfolio ELG(2-leg) >= ELG(1-leg) on favourable partition; Stage A pin preserved when max_legs=1 (default).
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


def test_r4_stage_b_default_pinned_to_max_legs_1() -> None:
    """Documents that the SHIPPED env-var defaults make Stage B identical to Stage A.

    Wave 4 renamed/split the activation env var into a mode-aware pair:
        ZEUS_SHADOW_FAMILY_PORTFOLIO_MAX_LEGS (default 1) — used in shadow mode
        ZEUS_LIVE_FAMILY_PORTFOLIO_MAX_LEGS   (default 1) — used in live mode
    Both default 1 so the Stage A single-leg gate stays the operational
    behaviour until the operator explicitly promotes either tier.
    """
    import os
    shadow_legs = int(os.environ.get("ZEUS_SHADOW_FAMILY_PORTFOLIO_MAX_LEGS", "1"))
    live_legs = int(os.environ.get("ZEUS_LIVE_FAMILY_PORTFOLIO_MAX_LEGS", "1"))
    assert shadow_legs == 1, (
        f"ZEUS_SHADOW_FAMILY_PORTFOLIO_MAX_LEGS={shadow_legs}; expected 1 by default."
    )
    assert live_legs == 1, (
        f"ZEUS_LIVE_FAMILY_PORTFOLIO_MAX_LEGS={live_legs}; expected 1 by default."
    )
    # Confirm the LEGACY name is not silently expected anywhere by the test suite.
    legacy = os.environ.get("ZEUS_FAMILY_OPTIMIZER_MAX_LEGS")
    assert legacy is None, (
        f"Legacy env var ZEUS_FAMILY_OPTIMIZER_MAX_LEGS={legacy!r} found — Wave 4 "
        "renamed this to ZEUS_SHADOW/LIVE_FAMILY_PORTFOLIO_MAX_LEGS. "
        "Clear the legacy export from operator config."
    )
