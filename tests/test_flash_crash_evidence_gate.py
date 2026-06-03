# Created: 2026-06-02
# Last reused/audited: 2026-06-03 (Wave 3 consolidation — exit_triggers twin deleted)
# Authority basis: BUG#127 (守護 SEV1, GOAL#36 "a short price change is NOT edge reversal");
#   src/state/portfolio.py flash_crash_should_fire + Position.evaluate_exit (single live site)
# Purpose: Lock the evidence gate on FLASH_CRASH_PANIC so a bare single-cycle quote wiggle
#   (adverse market_velocity_1h with UNCHANGED belief) can no longer force an exit, while a
#   belief-confirmed move OR a persistent deep catastrophe still exits. After unblock-W3
#   deleted the dead exit_triggers.py twin, the live gate lives solely in portfolio.py
#   (flash_crash_should_fire, shared by Position.evaluate_exit).
# Reuse: Run when FLASH_CRASH gating, exit_triggers ordering, or the flash_crash_* config changes.
"""BUG#127 antibody: FLASH_CRASH_PANIC must be evidence-gated, not a bare price-delta trigger."""
from __future__ import annotations

import numpy as np
import pytest

from src.contracts.edge_context import EdgeContext
from src.contracts.semantic_types import EntryMethod
from src.state.portfolio import (
    ExitContext,
    Position,
    consecutive_confirmations,
    divergence_soft_threshold,
    flash_crash_catastrophe_velocity,
    flash_crash_confirmations,
    flash_crash_should_fire,
    flash_crash_velocity,
)


def _edge_context(
    *,
    market_velocity_1h: float,
    divergence_score: float = 0.0,
    p_posterior: float = 0.60,
    forward_edge: float = 0.05,
    ci_lower: float = 0.50,
    ci_upper: float = 0.70,
) -> EdgeContext:
    arr = np.array([0.5])
    return EdgeContext(
        p_raw=arr,
        p_cal=arr,
        p_market=arr,
        p_posterior=p_posterior,
        forward_edge=forward_edge,
        alpha=0.0,
        confidence_band_upper=ci_upper,
        confidence_band_lower=ci_lower,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="snap-127",
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=market_velocity_1h,
        divergence_score=divergence_score,
    )


def _held_position(direction: str = "buy_no") -> Position:
    return Position(
        trade_id="pos-127",
        market_id="mkt-127",
        city="Warsaw",
        cluster="europe",
        target_date="2026-06-03",
        bin_label="20-21°C",
        direction=direction,
        entry_price=0.40,
        size_usd=20.0,
        shares=50.0,
        cost_basis_usd=20.0,
        entry_ci_width=0.20,
    )


def _exit_context(
    *,
    market_velocity_1h: float,
    divergence_score: float = 0.0,
    fresh_prob: float = 0.60,
    current_market_price: float = 0.55,
) -> ExitContext:
    return ExitContext(
        exit_reason="",
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=True,
        current_market_price=current_market_price,
        current_market_price_is_fresh=True,
        best_bid=0.54,
        best_ask=0.56,
        market_vig=1.0,
        hours_to_settlement=12.0,
        position_state="holding",
        day0_active=False,
        whale_toxicity=False,
        divergence_score=divergence_score,
        market_velocity_1h=market_velocity_1h,
    )


# --- 1. The shared gate helper (single source of truth for both sites) ----------------


def test_bare_single_cycle_wiggle_does_not_fire():
    """A sharp adverse price move with UNCHANGED belief and no persistence is NOT a crash."""
    assert flash_crash_should_fire(
        market_velocity_1h=flash_crash_velocity() - 0.01,  # below the arming threshold
        divergence_score=0.0,                              # belief UNCHANGED
        has_probability_authority=True,
        flash_crash_count=0,                               # first cycle
    ) is False


def test_belief_confirmed_move_fires():
    """Adverse velocity + belief confirms (divergence past soft threshold) -> fire."""
    assert flash_crash_should_fire(
        market_velocity_1h=flash_crash_velocity() - 0.01,
        divergence_score=divergence_soft_threshold() + 0.01,
        has_probability_authority=True,
        flash_crash_count=0,
    ) is True


def test_persistent_deep_catastrophe_fires_without_belief():
    """Even with degraded belief, a sustained DEEP crash (>= catastrophe bound, N cycles) fires."""
    assert flash_crash_should_fire(
        market_velocity_1h=flash_crash_catastrophe_velocity() - 0.01,
        divergence_score=0.0,
        has_probability_authority=False,
        flash_crash_count=flash_crash_confirmations(),
    ) is True


def test_moderate_persistent_dip_does_not_self_confirm():
    """Persistence alone, below the deep catastrophe bound, must NOT fire without belief."""
    assert flash_crash_should_fire(
        market_velocity_1h=flash_crash_velocity() - 0.01,   # armed but not catastrophic
        divergence_score=0.0,
        has_probability_authority=False,
        flash_crash_count=flash_crash_confirmations() + 5,  # persisted a long time
    ) is False


def test_velocity_above_arming_threshold_never_fires():
    assert flash_crash_should_fire(
        market_velocity_1h=0.0,
        divergence_score=1.0,
        has_probability_authority=True,
        flash_crash_count=99,
    ) is False


# --- 2. Site A REMOVED (Wave 3, 2026-06-03) ------------------------------------------
# The dead twin src/execution/exit_triggers.py (evaluate_exit_triggers) was deleted in
# unblock-W3 (one exit path: only Position.evaluate_exit remains live; zero src callers).
# BUG#127's substantive belief gate lives in portfolio.py::flash_crash_should_fire (tested
# in §1) and Position.evaluate_exit (tested in §3) — both preserved. The former Site-A
# assertions had no surviving subject, so they are dropped rather than repointed to a
# duplicate of Site B. See PR-A consolidation report (W3 conflict resolution).


# --- 3. Site B: Position.evaluate_exit -----------------------------------------------


def test_portfolio_evaluate_exit_bare_wiggle_no_flash_crash():
    pos = _held_position()
    ctx = _exit_context(market_velocity_1h=-0.20, divergence_score=0.0)
    decision = pos.evaluate_exit(ctx)
    assert "FLASH_CRASH_PANIC" not in (decision.trigger or "")
    assert "FLASH_CRASH_PANIC" not in (decision.reason or "")


def test_portfolio_evaluate_exit_belief_confirmed_crash_exits():
    pos = _held_position()
    ctx = _exit_context(
        market_velocity_1h=-0.20,
        divergence_score=divergence_soft_threshold() + 0.01,
    )
    decision = pos.evaluate_exit(ctx)
    assert decision.should_exit is True
    assert decision.trigger in {"FLASH_CRASH_PANIC", "MODEL_DIVERGENCE_PANIC"}


# --- 4. Single-site coherence (Wave 3, 2026-06-03) -----------------------------------
# Previously a two-site agreement check (exit_triggers.py vs portfolio.py). After W3
# deleted the dead exit_triggers twin, only the live Position.evaluate_exit site remains;
# the invariant collapses to: the single live exit site must not flash-crash on a bare
# 1-cycle wiggle (belief unchanged).


def test_live_site_does_not_flash_crash_on_bare_wiggle():
    """Relationship invariant: the single live exit site must not exit on a bare wiggle."""
    pos_b = _held_position()
    exit_ctx = _exit_context(market_velocity_1h=-0.25, divergence_score=0.0)

    dec = pos_b.evaluate_exit(exit_ctx)

    site_b_flash = "FLASH_CRASH_PANIC" in (dec.trigger or "")
    assert site_b_flash is False
