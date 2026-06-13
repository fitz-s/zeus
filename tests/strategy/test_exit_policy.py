# Created: 2026-06-13
# Last reused/audited: 2026-06-13
# Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md
#   E1-E6 + consult3 raw Q1 reference impls. Relationship-first per repo law: the
#   load-bearing tests pin the CROSS-MODULE invariants the authority makes law —
#   cost-basis independence (E1), stop-loss-not-distinct (E4), partial-exit FOC
#   matches the closed form (E6), depth-aware proceeds never assume hidden
#   liquidity (E6), q_exit blend defers correctly (E5a), the e-process fires on
#   the Denver class and not on a calibrated agent (E5b), and the flag-OFF golden
#   byte-identity of the shadow orchestration.
"""Relationship tests for the exit capability (consult-3 Q1, task #52)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.strategy import exit_belief
from src.strategy.exit_calibration_alarm import (
    derive_h_star,
    evaluate_alarm_series,
    log_e_increment,
    update_alarm,
)
from src.strategy.exit_policy import (
    _partial_exit_closed_form,
    exit_fraction_binary,
    sell_all_dominance_gap,
    take_profit_net_bid_threshold,
)


# ---------------------------------------------------------------------------
# E1 — cost basis is SUNK (the foundational law)
# ---------------------------------------------------------------------------
def test_exit_fraction_has_no_cost_basis_input():
    """E1 structural: the exit signature carries NO cost basis / entry price.

    The strongest form of "two positions with same state diff c → same exit" is
    that c cannot even be passed — it is not an argument. This pins the API so a
    future caller cannot reintroduce a 'down X%' stop-loss (E4) by threading c in.
    """
    import inspect

    params = set(inspect.signature(exit_fraction_binary).parameters)
    forbidden = {"cost_basis", "entry_price", "c", "pnl", "pnl_since_entry", "down_pct"}
    assert params.isdisjoint(forbidden), f"cost-basis leaked into signature: {params & forbidden}"


def test_cost_basis_independence_same_state_same_fraction():
    """E1: identical (q, bid, n, W, T, fees, depth) → identical fraction.

    Two 'positions' that differ ONLY in what they paid (which never enters) take
    the same optimal action. We model that by calling twice with the same state;
    a true cost-basis dependence would require c, which the API refuses to accept.
    """
    common = dict(
        q=0.58, bid=0.62, position_units=200, wealth_ex_position=1500,
        t_remaining_days=1.5, fees=0.01,
    )
    a = exit_fraction_binary(**common)
    b = exit_fraction_binary(**common)
    assert a.fraction_to_sell == b.fraction_to_sell


# ---------------------------------------------------------------------------
# E4 — stop-loss is NOT distinct: a posterior-against move triggers exit ONLY
# when sell-dominance holds with the updated q, never as a separate % rule.
# ---------------------------------------------------------------------------
def test_stop_loss_is_not_distinct_only_sell_dominance_fires():
    """E4: a posterior moving against the held side exits iff sell-dominance holds.

    Hold a side at a HIGH bid: even a posterior drop does NOT force a full exit
    unless the executable bid still dominates holding under the UPDATED q. The
    decision is sell_all_dominance_gap with the new q — there is no extra stop.
    """
    n, W = 100, 1000
    # Posterior drops from 0.7 -> 0.45 (against the held side). Bid is 0.40.
    # At q=0.45 with bid 0.40: is selling dominant? Only if the dominance gap > 0.
    gap_after = sell_all_dominance_gap(q=0.45, net_bid=0.40, position_units=n, wealth_ex_position=W)
    frac_after = exit_fraction_binary(q=0.45, bid=0.40, position_units=n, wealth_ex_position=W)
    # The fraction is governed by the SAME dominance comparison, not a % stop:
    if gap_after > 0:
        assert frac_after.fraction_to_sell > 0.0
    else:
        # A move against the position with a LOW bid does NOT mechanically force a
        # sale — holding the binary may still beat dumping at a deep discount.
        assert frac_after.fraction_to_sell == 0.0 or frac_after.sell_dominates is False
    # And the result exposes the dominance gap as the decision quantity, not a
    # cost-basis-relative loss.
    assert math.isclose(frac_after.dominance_gap, gap_after, rel_tol=1e-9) or not math.isfinite(gap_after)


# ---------------------------------------------------------------------------
# E6 — partial exit is a FOC fraction; closed form matches numeric on const depth
# ---------------------------------------------------------------------------
def test_partial_exit_foc_matches_closed_form_on_constant_depth():
    """E6: on a constant-depth (single deep level) book, the numeric FOC maximizer
    recovers the closed-form interior fraction x0. All-or-nothing is wrong."""
    # The interior region sits in the narrow band just ABOVE the take-profit
    # threshold: high enough that partial selling helps, but not so high that
    # sell-all dominates. tp_threshold(q=0.55, n=800, W=1000) ~= 0.477; bid 0.487
    # gives an interior x0 ~= 0.56.
    q, n, W = 0.55, 800, 1000
    bid = 0.487
    x_closed = _partial_exit_closed_form(q, bid, n, W)
    # single very-deep level == constant bid; force the numeric path (len(depth)!=1
    # would skip closed form, but here len==1 uses closed form — so build a 2-level
    # deep ladder at the same price to exercise the numeric optimizer).
    deep_ladder = [(n * 0.6, bid), (n * 0.6, bid)]
    r = exit_fraction_binary(q=q, bid=bid, position_units=n, wealth_ex_position=W, depth=deep_ladder)
    assert 0.0 < x_closed < 1.0, f"test setup should produce an interior x0, got {x_closed}"
    assert abs(r.fraction_to_sell - x_closed) < 5e-3, (r.fraction_to_sell, x_closed)


def test_partial_exit_is_a_fraction_not_all_or_nothing():
    """E6: a genuine interior optimum returns a fraction strictly inside (0,1)."""
    # bid 0.487 sits just above the take-profit threshold (~0.477) at this z, so
    # the FOC optimum is a partial sale, not all-or-nothing.
    r = exit_fraction_binary(q=0.55, bid=0.487, position_units=800, wealth_ex_position=1000)
    assert 0.0 < r.fraction_to_sell < 1.0


# ---------------------------------------------------------------------------
# E6 — depth-aware proceeds NEVER assume hidden liquidity
# ---------------------------------------------------------------------------
def test_depth_aware_never_assumes_hidden_liquidity():
    """E6: when the displayed ladder cannot absorb the desired quantity, the sale
    is capped at the fillable depth — never extrapolated past the book."""
    n, W = 100, 1000
    # A very attractive bid (sell-all wanted) but only 15 of 100 units fillable.
    depth = [(10, 0.95), (5, 0.92)]
    r = exit_fraction_binary(q=0.30, bid=0.95, position_units=n, wealth_ex_position=W, depth=depth)
    # Best feasible action sells exactly the fillable 15% — not 100%, not 0%.
    assert abs(r.fraction_to_sell - 0.15) < 1e-3, r.fraction_to_sell
    assert r.feasible is True


def test_depth_proceeds_walk_the_ladder_concavely():
    """E6: integrated proceeds use worse prices deeper in the book (concave S(x)),
    so a thin top level does not let the optimizer claim top-of-book for full size."""
    n, W = 100, 1000
    # Top level 10@0.90 then a cliff to 90@0.50. Selling all averages well below 0.90.
    depth = [(10, 0.90), (90, 0.50)]
    r_full = exit_fraction_binary(q=0.30, bid=0.90, position_units=n, wealth_ex_position=W, depth=depth)
    # The naive top-bid assumption would value the full sale at 0.90/contract; the
    # depth-aware proceeds value it far lower, so selling everything is NOT chosen
    # purely on the top bid. The fraction reflects the blended VWAP, not 0.90.
    naive = exit_fraction_binary(q=0.30, bid=0.90, position_units=n, wealth_ex_position=W, depth=None)
    assert r_full.fraction_to_sell <= naive.fraction_to_sell + 1e-9


# ---------------------------------------------------------------------------
# E3 — take-profit = sell-dominance threshold, no separate % gain target
# ---------------------------------------------------------------------------
def test_take_profit_is_the_sell_dominance_boundary():
    """E3: at exactly the take-profit threshold bid, the dominance gap is ~0."""
    q, n, W = 0.5, 100, 1000
    v = take_profit_net_bid_threshold(q, n, W)
    gap = sell_all_dominance_gap(q, v, n, W)
    assert abs(gap) < 1e-6, gap


def test_take_profit_bar_below_q_log_utility_pays_for_variance_removal():
    """E2 reading: log utility accepts a bid BELOW q (selling removes binary
    variance). The take-profit threshold is < q for any z > 0."""
    q, n, W = 0.6, 200, 1000  # z = 0.2 > 0
    v = take_profit_net_bid_threshold(q, n, W)
    assert v < q


# ---------------------------------------------------------------------------
# E5a — q_exit blend defers correctly
# ---------------------------------------------------------------------------
def _fit_market_better_artifact(tmp_path):
    """A fit where the market is the better forecaster (agent over-states)."""
    rng = np.random.default_rng(0)
    n = 400
    q_market = rng.uniform(0.1, 0.9, n)
    y = (rng.uniform(size=n) < q_market).astype(float)
    q_agent = np.clip(q_market + 0.18, 0.02, 0.98)
    fit = exit_belief.fit_blended_exit_belief(y, q_agent, q_market)
    path = tmp_path / "exit_belief_fit.json"
    exit_belief.write_exit_belief_fit(fit, path)
    return path, fit


def test_q_exit_unchanged_when_agent_equals_market(tmp_path):
    """E5a: when the agent already agrees with the market, the blend leaves q
    essentially unchanged (no spurious correction)."""
    path, _ = _fit_market_better_artifact(tmp_path)
    r = exit_belief.predict_q_exit(0.60, 0.60, path=path)
    # agent==market: the blend should not move q materially in either direction.
    assert abs(r.q_exit - 0.60) < 0.06, r.q_exit


def test_q_exit_follows_market_when_licensed_and_market_disagrees(tmp_path):
    """E5a (the Denver-class fix): a licensed blend pulls q_exit toward the market
    when the agent over-states and the market disagrees."""
    path, fit = _fit_market_better_artifact(tmp_path)
    assert fit.licensed, "test setup must produce an OOS-licensed blend"
    r = exit_belief.predict_q_exit(0.80, 0.55, path=path)
    assert r.blend_applied is True
    assert r.q_exit < 0.80, r.q_exit  # pulled down toward the market
    assert r.q_exit < r.q_agent


def test_q_exit_degrades_to_agent_when_unlicensed_or_missing():
    """E5a license: an absent / unlicensed artifact NEVER silently overrides the
    agent posterior — it degrades to the raw agent q with a loud source label."""
    r = exit_belief.predict_q_exit(0.80, 0.55, path="/nonexistent_exit_belief.json")
    assert r.blend_applied is False
    assert r.q_exit == pytest.approx(0.80, abs=1e-6)
    assert "degrade" in r.source


# ---------------------------------------------------------------------------
# E5b — anytime-valid e-process: cost-derived h*, fires on Denver, not on calibrated
# ---------------------------------------------------------------------------
def test_h_star_is_cost_derived_not_hardcoded():
    """E5b/Q4d: h* = c_miss / (c_false + c_impl), NOT a fixed 20 or 1/0.05."""
    assert derive_h_star(10, 1) == 10.0
    assert derive_h_star(3, 1, 0.5) == 2.0
    assert derive_h_star(1, 1) == 1.0  # not 20, not 1/0.05


def test_e_process_unit_martingale_under_null():
    """E5b: when the agent forecast EQUALS the alternative (null 'agent correct'),
    every increment is exactly 0, so E_n stays 1 — no false alarm."""
    inc = log_e_increment(q=0.5, r=0.5, y=1)
    assert abs(inc) < 1e-12
    st = evaluate_alarm_series(
        [0.6, 0.4, 0.55], [0.6, 0.4, 0.55], [1, 0, 1], c_miss=10, c_false=1
    )
    assert abs(st.e_value - 1.0) < 1e-9
    assert st.suspended is False


def test_e_process_fires_on_denver_class():
    """E5b (the Denver class): agent over-states the held side (q=0.75) while the
    market is right (r=0.45) and the side keeps losing → E_n explodes past h* →
    SUSPEND raw-posterior authority."""
    rng = np.random.default_rng(1)
    n = 40
    r = np.full(n, 0.45)
    q = np.full(n, 0.75)
    y = (rng.uniform(size=n) < 0.45).astype(int)  # truth tracks the market
    st = evaluate_alarm_series(q, r, y, c_miss=10, c_false=1)
    assert st.e_value > st.h_star
    assert st.suspended is True


def test_e_process_increment_sign_accumulates_against_agent():
    """E5b: the increment is positive exactly when the alternative explained the
    realized outcome better than the agent — evidence the agent is miscalibrated."""
    # Held side LOST (y=0); agent said 0.8 win, market said 0.3 win. Market better.
    inc = log_e_increment(q=0.8, r=0.3, y=0)
    assert inc > 0
    # Held side WON (y=1); agent said 0.8, market 0.3. Agent better -> increment < 0.
    inc2 = log_e_increment(q=0.8, r=0.3, y=1)
    assert inc2 < 0


def test_update_alarm_carries_state():
    """E5b: incremental update equals the batch series result over the same data."""
    qs, rs, ys = [0.75, 0.75, 0.75], [0.45, 0.45, 0.45], [0, 0, 1]
    log_e, n = 0.0, 0
    for q, r, y in zip(qs, rs, ys):
        st = update_alarm(log_e, n, q, r, y, c_miss=10, c_false=1)
        log_e, n = st.log_e, st.n
    batch = evaluate_alarm_series(qs, rs, ys, c_miss=10, c_false=1)
    assert abs(st.e_value - batch.e_value) < 1e-9
    assert st.n == 3
