# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: BUG #113 (守護 CI-separation exit must be LIVE) — design §4.6 / SD-7;
#   continuous_redecision.screen_exit CI-separation + EVIDENCE_UNAVAILABLE third state;
#   src/state/portfolio.py Position.evaluate_exit (the LIVE exit path).
"""RED→GREEN proof that the SD-7 CI-separation exit gate is LIVE on Position.evaluate_exit.

Before this fix the live path (Position.evaluate_exit → _buy_yes_exit/_buy_no_exit) exited
on a FLAT 2-consecutive neg_edge_count with NO CI-separation and NO EVIDENCE_UNAVAILABLE
third state — the screen_exit logic that carries those was unwired dead code (severed
2026-05-31 due to a 2nd-world-connection-inside-SAVEPOINT deadlock).

These tests assert the gate now fires on the live path WITHOUT any DB read (the CI bounds
are threaded through ExitContext from the cycle's already-computed bootstrap CI — no fresh
get_world_connection(), so the 2026-05-31 deadlock category is impossible by construction).
"""
from __future__ import annotations

import numpy as np

from src.state.portfolio import ExitContext, Position, consecutive_confirmations


def _held_position(direction: str = "buy_yes", *, entry_posterior: float = 0.70) -> Position:
    return Position(
        trade_id="pos-113",
        market_id="mkt-113",
        city="Warsaw",
        cluster="europe",
        target_date="2026-06-03",
        bin_label="20-21°C",
        direction=direction,
        entry_price=0.55,
        size_usd=20.0,
        shares=40.0,
        cost_basis_usd=20.0,
        p_posterior=entry_posterior,   # entry held-side belief (frozen at entry)
        entry_ci_width=0.10,           # entry CI = entry_posterior ± 0.05
    )


def _exit_context(
    *,
    fresh_prob: float,
    entry_posterior: float | None,
    entry_ci: tuple[float, float] | None,
    current_ci: tuple[float, float] | None,
    belief_available: bool = True,
    market_velocity_1h: float = 0.0,
) -> ExitContext:
    return ExitContext(
        exit_reason="",
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=True,
        current_market_price=0.55,
        current_market_price_is_fresh=True,
        best_bid=0.54,
        best_ask=0.56,
        market_vig=1.0,
        hours_to_settlement=12.0,
        position_state="holding",
        day0_active=False,
        whale_toxicity=False,
        divergence_score=0.0,
        market_velocity_1h=market_velocity_1h,
        entry_posterior=entry_posterior,
        entry_ci=entry_ci,
        current_ci=current_ci,
        belief_available=belief_available,
    )


# --- (a) DISJOINT-below current CI → CI-separation EXIT ---------------------------------

def test_ci_disjoint_below_entry_exits_via_ci_separation():
    """Current belief CI is disjoint from entry CI AND lies BELOW it → exit on CI separation."""
    pos = _held_position(entry_posterior=0.70)
    ctx = _exit_context(
        fresh_prob=0.40,
        entry_posterior=0.70,
        entry_ci=(0.65, 0.75),
        current_ci=(0.30, 0.45),  # disjoint, entirely below entry
    )
    decision = pos.evaluate_exit(ctx)
    assert decision.should_exit is True
    assert decision.trigger == "CI_SEPARATED_REVERSAL"


# --- (b) OVERLAPPING current CI → a bare/large price move does NOT exit -----------------

def test_ci_overlap_does_not_exit_on_mere_move():
    """Belief point dropped sharply but its CI still OVERLAPS entry → noisy snapshot → HOLD.

    Critically: drive neg_edge_count past the flat 2-confirm floor so that under the OLD
    (pre-fix) flat path this WOULD exit. The CI-overlap gate must suppress that exit.
    """
    pos = _held_position(entry_posterior=0.70)
    # Pre-arm the flat consecutive-cycle counter to the exit threshold.
    pos.neg_edge_count = consecutive_confirmations()
    ctx = _exit_context(
        fresh_prob=0.50,
        entry_posterior=0.70,
        entry_ci=(0.60, 0.80),
        current_ci=(0.45, 0.78),  # still overlaps entry CI → not separated
    )
    decision = pos.evaluate_exit(ctx)
    assert decision.should_exit is False
    assert "CI_OVERLAP" in (decision.reason + decision.trigger).upper()


# --- (c) EVIDENCE_UNAVAILABLE third state → distinct HOLD, not an exit ------------------

def test_evidence_unavailable_returns_distinct_hold_not_exit():
    """belief_available=False (degraded day0/obs math) → third state, HOLD with a distinct
    reason, NEVER an exit and NEVER a blind hold collapsed into a normal price hold."""
    pos = _held_position(entry_posterior=0.70)
    pos.neg_edge_count = consecutive_confirmations()
    ctx = _exit_context(
        fresh_prob=float("nan"),
        entry_posterior=0.70,
        entry_ci=(0.60, 0.80),
        current_ci=None,
        belief_available=False,
    )
    decision = pos.evaluate_exit(ctx)
    assert decision.should_exit is False
    assert decision.reason.startswith("EVIDENCE_UNAVAILABLE") or decision.trigger == "EVIDENCE_UNAVAILABLE"


# --- Proof the OLD flat path WOULD have exited on the (b) snapshot ----------------------

def test_flat_path_would_exit_without_ci_inputs_proves_gate_is_load_bearing():
    """Same adverse snapshot as (b) but with NO CI inputs supplied → the legacy flat
    2-confirm path still fires EDGE_REVERSAL. This proves the CI-separation gate in (b)
    is what suppressed the exit, not some unrelated hold."""
    pos = _held_position(entry_posterior=0.70)
    pos.neg_edge_count = consecutive_confirmations()
    ctx = _exit_context(
        fresh_prob=0.50,
        entry_posterior=None,   # no CI inputs → fall back to flat path
        entry_ci=None,
        current_ci=None,
    )
    decision = pos.evaluate_exit(ctx)
    assert decision.should_exit is True
    assert decision.trigger == "EDGE_REVERSAL"


# --- (d) "claimed-done-is-wired" guard: the gate is REACHED on the live path ------------

def test_ci_separation_gate_is_wired_into_live_evaluate_exit():
    """#113 immune guard: assert the CI-separation gate is actually REACHED by the live
    evaluate_exit (not dead code). A future severance that drops the gate fails this test.

    Proof of reach: the same DISJOINT-below CI snapshot must produce the CI_SEPARATED_REVERSAL
    trigger string that ONLY the gate emits — and it must do so via the public live entrypoint
    Position.evaluate_exit, applied for BOTH directions.
    """
    for direction, entry_post in (("buy_yes", 0.70), ("buy_no", 0.70)):
        pos = _held_position(direction=direction, entry_posterior=entry_post)
        ctx = _exit_context(
            fresh_prob=0.35,
            entry_posterior=entry_post,
            entry_ci=(0.65, 0.75),
            current_ci=(0.25, 0.40),
        )
        decision = pos.evaluate_exit(ctx)
        assert decision.trigger == "CI_SEPARATED_REVERSAL", (
            f"CI-separation gate NOT reached on live path for {direction} "
            f"(got trigger={decision.trigger!r}, reason={decision.reason!r})"
        )
        assert "ci_separation_gate" in decision.applied_validations
