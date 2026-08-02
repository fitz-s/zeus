# Lifecycle: created=2026-07-24; last_reviewed=2026-08-02; last_reused=2026-08-02
# Purpose: Pin the depth-honest liquidation curve in the one-law exit stopping
#   law (Position._exit_bid_breakpoints -> predicted_bin_law.exit_decision).
#   Every cumulative executable prefix is compared against retaining the same
#   shares; neither top-bid extrapolation nor a negative lower rung may replace
#   that feasible set with a single synthetic point.
# Authority basis: the one-law exit previously exposed only the deepest
#   cumulative breakpoint, which could erase a profitable upper prefix.
#   Actuation is ALL-shares (build_exit_intent hard-codes shares=effective_shares;
#   the partial-quantity path is the separate global-auction authority), so the
#   honest comparison prices the fillable prefix against holding that same prefix.
"""Depth-honest exit curve relationship tests (one-law lineage).

DHC-a  every positive ladder rung emits one cumulative prefix, preserving a
       profitable upper prefix when deeper rungs have negative marginal value.
DHC-b  deep book (top size >= held) reproduces the depth-blind single-level verdict.
DHC-c  empty ladder falls back to a single top rung capped at bid_size (never the
       uncapped all-at-top form).
DHC-d  no depth info at all reverts to the legacy uncapped rung.
DHC-e  LockState.IMPOSSIBLE (q folded to ~0) still SELLs on any positive fillable
       bid depth, and an empty book (x_fill=0) still HOLDs.
DHC-f  ExitContext carries bid_ladder/bid_size; cycle_runtime._build_exit_context
       threads pos.last_monitor_* into it end-to-end.
"""
from __future__ import annotations

from decimal import Decimal

import pytest


def _make_position(*, shares: float = 100.0, cost_basis_usd: float = 30.0,
                   direction: str = "buy_yes", entry_price: float = 0.30):
    from src.state.portfolio import Position
    return Position(
        trade_id="DHC-TEST",
        market_id="mkt-test",
        city="Warsaw",
        cluster="Warsaw",
        target_date="2026-08-01",
        bin_label="bin-test",
        direction=direction,
        unit="F",
        entry_price=entry_price,
        entry_method="opening_inertia",
        entry_ci_width=0.20,
        shares=shares,
        shares_filled=shares,
        filled_cost_basis_usd=cost_basis_usd,
        cost_basis_usd=cost_basis_usd,
        size_usd=cost_basis_usd,
        p_posterior=entry_price,
    )


def _ctx(*, fresh_prob, best_bid, current_ci=None, bid_size=None, bid_ladder=(),
         day0_authority=False):
    from src.state.portfolio import ExitContext
    return ExitContext(
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=True,
        current_market_price=best_bid,
        current_market_price_is_fresh=True,
        best_bid=best_bid,
        bid_size=bid_size,
        bid_ladder=bid_ladder,
        hours_to_settlement=48.0,
        position_state="active",
        current_ci=current_ci,
        day0_zero_probability_exit_authority=day0_authority,
    )


# --- Unit: the breakpoint curve itself -------------------------------------

class TestBreakpointCurve:
    def _bp(self, ctx, held=100.0):
        pos = _make_position(shares=held)
        return pos._exit_bid_breakpoints(ctx, Decimal(str(held)))

    def test_thin_ladder_prices_fillable_prefix(self):
        # DHC-a curve: 10 @ .30 (net .2895) + 90 @ .20 (net .192) = 20.175, x=100.
        bps = self._bp(_ctx(fresh_prob=0.25, best_bid=0.30,
                            bid_ladder=((0.30, 10.0), (0.20, 90.0))))
        assert len(bps) == 2
        first_x, first_proceeds = bps[0]
        assert first_x == Decimal("10.0")
        assert float(first_proceeds) == pytest.approx(2.895, abs=1e-6)
        x, proceeds = bps[-1]
        assert x == Decimal("100.0")
        assert float(proceeds) == pytest.approx(20.175, abs=1e-6)

    def test_each_positive_rung_emits_one_cumulative_prefix(self):
        bps = self._bp(_ctx(
            fresh_prob=0.25,
            best_bid=0.40,
            bid_ladder=((0.40, 3.0), (0.35, 4.0), (0.30, 50.0)),
        ), held=10.0)
        assert tuple(x for x, _ in bps) == (
            Decimal("3.0"),
            Decimal("7.0"),
            Decimal("10.0"),
        )

    def test_deep_ladder_matches_legacy_uncapped(self):
        # DHC-b: top size >= held -> whole position fills at the top rung.
        deep = self._bp(_ctx(fresh_prob=0.25, best_bid=0.30, bid_ladder=((0.30, 500.0),)))
        legacy = self._bp(_ctx(fresh_prob=0.25, best_bid=0.30))
        assert float(deep[0][1]) == pytest.approx(28.95, abs=1e-6)
        assert float(legacy[0][1]) == pytest.approx(28.95, abs=1e-6)
        assert deep[0][0] == legacy[0][0] == Decimal("100.0")

    def test_no_ladder_capped_at_bid_size(self):
        # DHC-c: known top size, no ladder -> single rung capped at bid_size.
        bps = self._bp(_ctx(fresh_prob=0.25, best_bid=0.30, bid_size=10.0))
        x, proceeds = bps[0]
        assert x == Decimal("10.0")
        assert float(proceeds) == pytest.approx(10 * 0.2895, abs=1e-6)

    def test_no_depth_info_legacy_uncapped(self):
        # DHC-d: no ladder, no bid_size -> legacy full-position rung.
        bps = self._bp(_ctx(fresh_prob=0.25, best_bid=0.30))
        assert bps[0][0] == Decimal("100.0")
        assert float(bps[0][1]) == pytest.approx(28.95, abs=1e-6)

    def test_zero_bid_size_yields_empty_fill(self):
        # bid_size known and zero -> nothing fills; honest (0, 0) rung.
        bps = self._bp(_ctx(fresh_prob=0.25, best_bid=0.30, bid_size=0.0))
        assert bps[0][0] == Decimal("0")
        assert float(bps[0][1]) == pytest.approx(0.0, abs=1e-12)

    def test_no_finite_bid_empty_curve(self):
        bps = self._bp(_ctx(fresh_prob=0.25, best_bid=None,
                            bid_ladder=((0.30, 10.0),)))
        assert bps == ()


# --- End-to-end: evaluate_exit routes the ladder ---------------------------

class TestEvaluateExitDepthHonest:
    def test_amsterdam_selects_best_positive_partial_prefix(self):
        from src.decision.predicted_bin_law import (
            ExitAction,
            LockState,
            exit_decision,
        )

        held = Decimal("104.1")
        q = Decimal("0.714317590273214")
        margin_per_share = Decimal("0.01")
        pos = _make_position(shares=float(held))
        breakpoints = pos._exit_bid_breakpoints(
            _ctx(
                fresh_prob=float(q),
                best_bid=0.77,
                bid_ladder=(
                    (0.77, 8.0),
                    (0.76, 10.0),
                    (0.75, 10.0),
                    (0.70, 7.14),
                    (0.58, 40.0),
                ),
            ),
            held,
        )
        deltas = tuple(
            (x, proceeds - x * (q + margin_per_share))
            for x, proceeds in breakpoints
        )
        expected_x, expected_delta = max(deltas, key=lambda item: item[1])
        assert expected_delta > 0
        assert deltas[-1][1] <= 0  # deepest-only syntax produced the old HOLD

        verdict = exit_decision(
            held_shares=held,
            q_mean=q,
            bid_breakpoints=breakpoints,
            exit_margin_per_share=margin_per_share,
            lock=LockState.NONE,
            evidence_ok=True,
            riskguard_red=False,
        )

        assert verdict.action is ExitAction.SELL_REVERSAL
        assert verdict.shares_to_sell == expected_x
        assert Decimal(0) < verdict.shares_to_sell < held
        assert all(
            delta < expected_delta
            for x, delta in deltas
            if x > expected_x
        )

    def test_no_depth_fallback_can_select_full_exit(self):
        from src.decision.predicted_bin_law import (
            ExitAction,
            LockState,
            exit_decision,
        )

        held = Decimal("12.5")
        pos = _make_position(shares=float(held))
        breakpoints = pos._exit_bid_breakpoints(
            _ctx(fresh_prob=0.20, best_bid=0.60),
            held,
        )
        assert breakpoints[0][0] == held
        verdict = exit_decision(
            held_shares=held,
            q_mean=Decimal("0.20"),
            bid_breakpoints=breakpoints,
            exit_margin_per_share=Decimal("0.01"),
            lock=LockState.NONE,
            evidence_ok=True,
            riskguard_red=False,
        )
        assert verdict.action is ExitAction.SELL_REVERSAL
        assert verdict.shares_to_sell == held

    def test_thin_book_sells_only_the_positive_top_prefix(self):
        """DHC-a: the 0.30 rung clears q + margin; the 0.20 rung does not.

        Exposing both cumulative prefixes lets the law retain the profitable
        10-share point instead of letting the negative lower rung erase it.
        """
        pos = _make_position(shares=100.0)
        thin = pos.evaluate_exit(_ctx(
            fresh_prob=0.25, best_bid=0.30, current_ci=(0.25, 0.30),
            bid_ladder=((0.30, 10.0), (0.20, 90.0)),
        ))
        assert thin.should_exit is True
        assert thin.trigger == "SELL_REVERSAL"

        pos_blind = _make_position(shares=100.0)
        blind = pos_blind.evaluate_exit(_ctx(
            fresh_prob=0.25, best_bid=0.30, current_ci=(0.25, 0.30),
        ))
        assert blind.should_exit is True
        assert blind.trigger == "SELL_REVERSAL"

    def test_deep_book_reproduces_blind_verdict_both_ways(self):
        # SELL case: q=0.25, deep ladder and blind both SELL.
        deep_sell = _make_position(shares=100.0).evaluate_exit(_ctx(
            fresh_prob=0.25, best_bid=0.30, current_ci=(0.25, 0.30),
            bid_ladder=((0.30, 500.0),),
        ))
        blind_sell = _make_position(shares=100.0).evaluate_exit(_ctx(
            fresh_prob=0.25, best_bid=0.30, current_ci=(0.25, 0.30),
        ))
        assert deep_sell.should_exit is True and blind_sell.should_exit is True

        # HOLD case: q=0.30, proceeds 28.95 < 100*0.30 + 1.0 = 31 -> both HOLD.
        deep_hold = _make_position(shares=100.0).evaluate_exit(_ctx(
            fresh_prob=0.30, best_bid=0.30, current_ci=(0.30, 0.35),
            bid_ladder=((0.30, 500.0),),
        ))
        blind_hold = _make_position(shares=100.0).evaluate_exit(_ctx(
            fresh_prob=0.30, best_bid=0.30, current_ci=(0.30, 0.35),
        ))
        assert deep_hold.should_exit is False and blind_hold.should_exit is False

    def test_bid_size_capped_fallback_sells_on_prefix(self):
        # DHC-c end-to-end: no ladder, bid_size=10; x_fill=10, per-share bid 0.30
        # net 0.2895 > q 0.25 + per-share margin 0.01, so the fillable prefix
        # strictly dominates retaining those same 10 shares.
        pos = _make_position(shares=100.0)
        capped = pos.evaluate_exit(_ctx(
            fresh_prob=0.25, best_bid=0.30, current_ci=(0.25, 0.30), bid_size=10.0,
        ))
        assert capped.should_exit is True
        assert capped.trigger == "SELL_REVERSAL"


class TestImpossibleLock:
    def test_impossible_sells_on_any_positive_fillable_bid(self):
        """DHC-e: day0 absorbing q->0 -> IMPOSSIBLE lock -> terminal hold ~0, so
        any positive fillable prefix at a positive bid SELLs."""
        pos = _make_position(shares=100.0)
        verdict = pos.evaluate_exit(_ctx(
            fresh_prob=1e-12, best_bid=0.10, bid_ladder=((0.10, 50.0),),
            day0_authority=True,
        ))
        assert verdict.should_exit is True
        assert "settlement_preimage_lock:impossible" in verdict.applied_validations

    def test_impossible_empty_book_still_holds(self):
        """DHC-e: IMPOSSIBLE but x_fill=0 (empty book) -> nothing to sell -> HOLD."""
        pos = _make_position(shares=100.0)
        verdict = pos.evaluate_exit(_ctx(
            fresh_prob=1e-12, best_bid=0.10, bid_size=0.0, day0_authority=True,
        ))
        assert verdict.should_exit is False


class TestExitContextWiring:
    def test_exit_context_carries_depth_fields(self):
        from src.state.portfolio import ExitContext
        ctx = ExitContext(bid_size=12.0, bid_ladder=((0.30, 12.0),))
        assert ctx.bid_size == 12.0
        assert ctx.bid_ladder == ((0.30, 12.0),)
        # Defaults keep pre-change callers depth-blind (single-level fallback).
        assert ExitContext().bid_ladder == ()
        assert ExitContext().bid_size is None

    def test_build_exit_context_threads_ladder_from_position(self):
        """DHC-f: cycle_runtime._build_exit_context lifts pos.last_monitor_bid_*
        (set by monitor_refresh from the same-cycle quote) into ExitContext."""
        from types import SimpleNamespace
        from src.engine import cycle_runtime as cr
        from src.state.portfolio import ExitContext

        pos = _make_position(shares=100.0)
        pos.last_monitor_best_bid = 0.30
        pos.last_monitor_bid_size = 12.0
        pos.last_monitor_bid_ladder = ((0.30, 12.0), (0.20, 40.0))
        pos.last_monitor_market_price = 0.30
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_prob = 0.25
        pos.last_monitor_prob_is_fresh = True

        edge_ctx = SimpleNamespace(
            p_market=[0.30], p_posterior=0.25,
            confidence_band_lower=-0.05, confidence_band_upper=0.05,
        )
        ctx = cr._build_exit_context(
            pos, edge_ctx, hours_to_settlement=48.0,
            ExitContext=ExitContext, portfolio=None,
        )
        assert ctx.bid_size == 12.0
        assert ctx.bid_ladder == ((0.30, 12.0), (0.20, 40.0))
