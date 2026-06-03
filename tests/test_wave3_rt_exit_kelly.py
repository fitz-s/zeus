# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: WAVE 3 brief (2026-06-02) — collapse exit+sizing to ONE path each;
#   delete orphan twins (screen_exit / evaluate_exit_triggers) + dead Kelly params
#   (rolling_win_rate_20, drawdown_pct, max_drawdown). GOAL#36 invariant CI_OVERLAP_HOLD
#   lives in Position.evaluate_exit only.
"""WAVE 3 relationship tests — RED first, then GREEN after deletion/refactor.

RT-W3a  screen_exit and evaluate_exit_triggers are NOT importable after deletion.
RT-W3b  CI_OVERLAP_HOLD invariant in the ONE live path: overlapping CI + adverse price
        → Position.evaluate_exit returns should_exit=False, trigger='CI_OVERLAP_HOLD'.
RT-W3c  dynamic_kelly_mult no longer accepts rolling_win_rate_20 / drawdown_pct /
        max_drawdown; portfolio_heat (live, non-zero) is retained.
"""
from __future__ import annotations

import math
import pytest


# ---------------------------------------------------------------------------
# RT-W3a: dead twins must not be importable after deletion
# ---------------------------------------------------------------------------

class TestDeadTwinsRemoved:
    """These tests are RED while the symbols still exist, GREEN after deletion."""

    def test_screen_exit_not_in_continuous_redecision(self):
        """screen_exit must be removed from src.events.continuous_redecision."""
        import src.events.continuous_redecision as cr
        assert not hasattr(cr, "screen_exit"), (
            "screen_exit is dead code (zero live callers in src/); "
            "it must be deleted from continuous_redecision.py"
        )

    def test_screen_exit_cancel_not_in_continuous_redecision(self):
        """screen_exit_cancel must be removed from src.events.continuous_redecision."""
        import src.events.continuous_redecision as cr
        assert not hasattr(cr, "screen_exit_cancel"), (
            "screen_exit_cancel is dead code (zero live callers in src/); "
            "it must be deleted from continuous_redecision.py"
        )

    def test_evaluate_exit_triggers_module_deleted(self):
        """exit_triggers.py must be deleted (evaluate_exit_triggers has zero live callers)."""
        with pytest.raises(ImportError):
            from src.execution import exit_triggers  # noqa: F401


# ---------------------------------------------------------------------------
# RT-W3b: CI_OVERLAP_HOLD in the ONE live path (Position.evaluate_exit)
# ---------------------------------------------------------------------------

def _make_position(
    *,
    direction: str = "buy_yes",
    entry_price: float = 0.60,
    entry_ci_width: float = 0.20,
    cost_basis_usd: float = 50.0,
    shares: float = 83.0,
):
    """Minimal Position factory for exit tests."""
    from src.state.portfolio import Position
    return Position(
        trade_id="RT-W3b-TEST",
        market_id="mkt-test",
        city="Warsaw",
        cluster="Warsaw",
        target_date="2026-06-10",
        bin_label="bin-test",
        direction=direction,
        unit="F",
        entry_price=entry_price,
        entry_method="opening_inertia",
        entry_ci_width=entry_ci_width,
        shares=shares,
        shares_filled=shares,
        filled_cost_basis_usd=cost_basis_usd,
        cost_basis_usd=cost_basis_usd,
        size_usd=cost_basis_usd,
        p_posterior=entry_price,
    )


def _make_exit_context(
    *,
    fresh_prob: float,
    current_market_price: float,
    hours_to_settlement: float = 48.0,
    best_bid: float = 0.58,
    entry_posterior: float | None = None,
    entry_ci: tuple | None = None,
    current_ci: tuple | None = None,
):
    """Minimal ExitContext factory."""
    from src.state.portfolio import ExitContext
    return ExitContext(
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=True,
        current_market_price=current_market_price,
        current_market_price_is_fresh=True,
        best_bid=best_bid,
        hours_to_settlement=hours_to_settlement,
        position_state="active",
        divergence_score=0.0,
        market_velocity_1h=0.0,
        entry_posterior=entry_posterior,
        entry_ci=entry_ci,
        current_ci=current_ci,
    )


class TestCIOverlapHold:
    """RT-W3b: overlapping CI + adverse price must hold, not exit.

    GOAL#36 invariant: a large point-move whose CI still overlaps the entry
    CI is noisy — the system must HOLD. This property lives in
    Position.evaluate_exit via the _ci_intervals_separated gate (~line 1126),
    which fires when ExitContext carries entry_ci + current_ci + entry_posterior.
    The gate is the ONE live CI_OVERLAP_HOLD path (no band-gate twin in _buy_yes_exit).
    """

    def test_ci_overlap_holds_when_adverse_price_but_ci_overlaps(self):
        """Adverse price move with overlapping CI must not exit.

        entry_ci = (0.56, 0.74), current_ci = (0.52, 0.64) — they overlap.
        fresh_prob=0.58 is adverse (< entry_posterior=0.65) but CIs are not separated.
        _ci_intervals_separated returns False → CI_OVERLAP_HOLD fired.
        """
        pos = _make_position(direction="buy_yes", entry_price=0.65, entry_ci_width=0.18)
        # entry_ci and current_ci overlap → _ci_intervals_separated = False
        ctx = _make_exit_context(
            fresh_prob=0.58,
            current_market_price=0.58,
            best_bid=0.57,
            entry_posterior=0.65,
            entry_ci=(0.56, 0.74),   # entry: 0.65 ± 0.09
            current_ci=(0.52, 0.64), # overlaps entry CI
        )
        decision = pos.evaluate_exit(ctx)
        assert decision.should_exit is False, (
            f"CI_OVERLAP_HOLD: CI still overlaps entry, should HOLD. Got trigger={decision.trigger!r}"
        )
        assert decision.trigger == "CI_OVERLAP_HOLD", (
            f"Expected trigger='CI_OVERLAP_HOLD', got {decision.trigger!r}"
        )

    def test_ci_separated_adverse_exits(self):
        """When CI is fully separated AND price drops below entry, exit is expected.

        entry_ci = (0.74, 0.76) tight, current_ci = (0.28, 0.32) fully below.
        _ci_intervals_separated = True; fresh_prob=0.30 < entry_posterior=0.75 → EXIT.
        MINOR-5: assert should_exit is True (not merely trigger != CI_OVERLAP_HOLD).
        """
        pos = _make_position(direction="buy_yes", entry_price=0.75, entry_ci_width=0.02)
        ctx = _make_exit_context(
            fresh_prob=0.30,
            current_market_price=0.30,
            best_bid=0.29,
            entry_posterior=0.75,
            entry_ci=(0.74, 0.76),   # tight around entry
            current_ci=(0.28, 0.32), # fully disjoint below entry CI
        )
        decision = pos.evaluate_exit(ctx)
        assert decision.should_exit is True, (
            f"CI fully separated + adverse move must exit. Got should_exit={decision.should_exit}, "
            f"trigger={decision.trigger!r}"
        )
        assert decision.trigger != "CI_OVERLAP_HOLD", (
            "When CI is fully separated, CI_OVERLAP_HOLD must NOT fire"
        )

    def test_ci_overlap_holds_buy_no_symmetry(self):
        """buy_no symmetry: overlapping CI with adverse (upward) move must HOLD.

        For buy_no, 'adverse' means fresh_prob rose above entry_posterior.
        CIs overlap → CI_OVERLAP_HOLD fires regardless of direction.
        entry_ci = (0.26, 0.44), current_ci = (0.32, 0.50) — they overlap.
        fresh_prob=0.45 > entry_posterior=0.35 (adverse for buy_no).
        """
        pos = _make_position(
            direction="buy_no",
            entry_price=0.35,
            entry_ci_width=0.18,
        )
        ctx = _make_exit_context(
            fresh_prob=0.45,
            current_market_price=0.45,
            best_bid=0.44,
            entry_posterior=0.35,
            entry_ci=(0.26, 0.44),
            current_ci=(0.32, 0.50),
        )
        decision = pos.evaluate_exit(ctx)
        assert decision.should_exit is False, (
            f"buy_no CI_OVERLAP_HOLD: CI still overlaps entry, should HOLD. Got trigger={decision.trigger!r}"
        )
        assert decision.trigger == "CI_OVERLAP_HOLD", (
            f"Expected trigger='CI_OVERLAP_HOLD' for buy_no, got {decision.trigger!r}"
        )


# ---------------------------------------------------------------------------
# RT-W3c: dead Kelly params removed from dynamic_kelly_mult signature
# ---------------------------------------------------------------------------

class TestDeadKellyParamsRemoved:
    """RT-W3c: rolling_win_rate_20, drawdown_pct, max_drawdown must raise TypeError."""

    def test_rolling_win_rate_raises_type_error(self):
        """Passing rolling_win_rate_20 must raise TypeError after deletion."""
        from src.strategy.kelly import dynamic_kelly_mult
        with pytest.raises(TypeError):
            dynamic_kelly_mult(
                base=0.25,
                ci_width=0.12,
                lead_days=4,
                portfolio_heat=0.45,
                city="Warsaw",
                rolling_win_rate_20=0.55,
            )

    def test_drawdown_pct_raises_type_error(self):
        """Passing drawdown_pct must raise TypeError after deletion."""
        from src.strategy.kelly import dynamic_kelly_mult
        with pytest.raises(TypeError):
            dynamic_kelly_mult(base=0.25, drawdown_pct=0.05)

    def test_max_drawdown_raises_type_error(self):
        """Passing max_drawdown must raise TypeError after deletion."""
        from src.strategy.kelly import dynamic_kelly_mult
        with pytest.raises(TypeError):
            dynamic_kelly_mult(base=0.25, max_drawdown=0.15)

    def test_portfolio_heat_survives_and_active(self):
        """portfolio_heat MUST be retained and produce a non-trivial reduction at heat=0.45."""
        from src.strategy.kelly import dynamic_kelly_mult
        m_no_heat = dynamic_kelly_mult(base=0.25, ci_width=0.12, lead_days=4, portfolio_heat=0.0, city="Warsaw")
        m_with_heat = dynamic_kelly_mult(base=0.25, ci_width=0.12, lead_days=4, portfolio_heat=0.45, city="Warsaw")
        assert m_with_heat < m_no_heat, (
            f"portfolio_heat=0.45 should reduce sizing: {m_with_heat} >= {m_no_heat}"
        )
        upper = 0.25 * 0.7 * 0.55  # base * ci_haircut(>0.10) * (1-0.45)
        assert 0 < m_with_heat <= upper + 1e-9, (
            f"dynamic_kelly_mult(base=0.25, ci_width=0.12, lead_days=4, portfolio_heat=0.45, city='Warsaw') "
            f"= {m_with_heat} not in (0, {upper}]"
        )

    def test_canonical_call_returns_valid_range(self):
        """The canonical call from the brief must return a value in (0, 0.25*0.7*0.55]."""
        from src.strategy.kelly import dynamic_kelly_mult
        m = dynamic_kelly_mult(
            base=0.25,
            ci_width=0.12,
            lead_days=4,
            portfolio_heat=0.45,
            city="Warsaw",
        )
        upper = 0.25 * 0.7 * 0.55  # ci_width=0.12→×0.7; lead_days=4→no haircut; heat=0.45→×0.55
        assert 0 < m <= upper + 1e-9, (
            f"Expected in (0, {upper:.4f}], got {m:.6f}"
        )
        assert math.isfinite(m), "Result must be finite"
