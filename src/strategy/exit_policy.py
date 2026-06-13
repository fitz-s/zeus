# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md
#   E1-E6 (cost basis SUNK; sell-all dominance; take-profit = sell-dominance;
#   stop-loss NOT distinct; partial exit is a FOC fraction, depth-aware proceeds)
#   + docs/authority/consult3_exit_portfolio_execution_2026-06-13_raw.txt Q1
#   (reference impls sell_all_dominance_gap / take_profit_net_bid_threshold /
#   exit_fraction_binary [depth-aware] / one_step_information_option_value).
#   Plan: docs/evidence/plans/2026-06-13_exit_capability.md.
"""Log-optimal exit policy for binary contracts (consult-3 Q1, task #52).

The system was entry-only: the only way to close a held position was settlement.
This module is the principled SELL decision. Its inputs are the held-side
posterior q_t, the executable bid-DEPTH curve, position size n, liquid wealth W,
time remaining T, the future-opportunity growth rate g*, and fees.

THE FOUNDATIONAL LAW (E1 — cost basis is SUNK):
    Under expected-log utility, entry cost c does NOT enter the exit decision.
    Two positions with identical (W, n, q_t, depth, T, fees) take the SAME
    optimal action regardless of what they paid. A "stop-loss because down X%"
    rule provably does not exist (E4). c enters ONLY via taxes/accounting
    (after-tax proceeds) or bankroll constraints (via W). This module therefore
    never accepts an entry price, cost basis, or P&L-since-entry argument. If a
    caller wants to thread one in, that is the bug the authority condemns.

STOP-LOSS IS NOT DISTINCT (E4): a posterior moving against the position changes
q_t; the optimal response is ALREADY sell-dominance (E2) with the updated q_t.
There is no separate percentage stop. The miscalibration fix (the Denver class)
is to feed a market-blended q_exit (E5, src/strategy/exit_belief.py) and an
anytime-valid suspension alarm (src/strategy/exit_calibration_alarm.py) — NOT a
hand-set threshold.

PARTIAL EXIT IS A FRACTION (E6): all-or-nothing exits are wrong. The first-order
condition gives an interior fraction x* in [0, 1]. For a constant executable bid
there is a closed form (the `_partial_exit_closed_form` fast path); for a finite
bid-depth ladder the integrated proceeds S(x) are concave and the maximizer is
found numerically — and we NEVER assume top-of-book liquidity for the full size
(no hidden-liquidity assumption: insufficient depth → the candidate fraction is
infeasible).

DEPTH (E6): S(x) = integrated proceeds through the bid-depth curve, concave. The
sale cash for selling qty walks the ladder best-to-worse; if the ladder cannot
absorb the requested quantity the proceeds are -inf (no-fill), so the optimizer
will never pretend liquidity exists.

Pure module: numpy/scipy only, no DB access, no engine imports, no logging side
effects. Shadow logging lives at the (impure) caller in cycle_runtime. Mirrors
the C2 selection_shrinkage / C3 james_stein_blend module shape.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import norm

_EPS = 1e-12


def _clip_prob(q: float, eps: float = 1e-9) -> float:
    return float(np.clip(q, eps, 1.0 - eps))


# ---------------------------------------------------------------------------
# E2 — Sell-all dominance
# ---------------------------------------------------------------------------
def sell_all_dominance_gap(
    q: float,
    net_bid: float,
    position_units: float,
    wealth_ex_position: float,
    t_remaining_days: float = 0.0,
    g_star: float = 0.0,
    win_payout: float = 1.0,
    lose_payout: float = 0.0,
) -> float:
    """Log-utility gap: selling the WHOLE position at ``net_bid`` minus holding.

    Positive gap ⇒ selling the entire position at the executable net bid strictly
    dominates holding to settlement (E2, boxed). ``position_units`` is the max
    payout units (n), NOT a cost basis — c never enters (E1).

    M(T) = e^{g*·T} is the future-opportunity multiplier (E3): released capital
    can compound at g* per day, lowering the bar to sell.
    """
    q = _clip_prob(q)
    n = float(position_units)
    W = float(wealth_ex_position)
    if n <= 0 or W <= 0:
        raise ValueError("position_units and wealth_ex_position must be positive")

    M = float(np.exp(g_star * t_remaining_days))
    w_sell = W + n * net_bid * M
    w_win = W + n * win_payout
    w_lose = W + n * lose_payout
    if min(w_sell, w_win, w_lose) <= 0:
        return -float("inf")

    u_sell = float(np.log(w_sell))
    u_hold = q * float(np.log(w_win)) + (1.0 - q) * float(np.log(w_lose))
    return u_sell - u_hold


def take_profit_net_bid_threshold(
    q: float,
    position_units: float,
    wealth_ex_position: float,
    t_remaining_days: float = 0.0,
    g_star: float = 0.0,
    win_payout: float = 1.0,
    lose_payout: float = 0.0,
) -> float:
    """Minimum net executable bid per contract for sell-all dominance (E3).

    There is NO separate "% gain target": take-profit is exactly the bid region
    where E2 sell-dominance holds. Reading: log utility accepts a bid BELOW q
    (selling removes binary variance); g* lowers the bar further.
    """
    q = _clip_prob(q)
    n = float(position_units)
    W = float(wealth_ex_position)
    z = n / W
    M = float(np.exp(g_star * t_remaining_days))
    if z <= _EPS:
        # small-z limit: v >= q·e^{-g*T} (binary win=1, lose=0)
        return (q * win_payout + (1.0 - q) * lose_payout) / M
    hold_ce_gross = float(
        np.exp(
            q * np.log1p(z * win_payout) + (1.0 - q) * np.log1p(z * lose_payout)
        )
    )
    return (hold_ce_gross - 1.0) / (z * M)


# ---------------------------------------------------------------------------
# E6 — Partial exit (closed-form fast path + depth-aware numeric)
# ---------------------------------------------------------------------------
def _partial_exit_closed_form(
    q_eff: float,
    net_bid: float,
    position_units: float,
    wealth_ex_position: float,
    t_remaining_days: float = 0.0,
    g_star: float = 0.0,
    win_payout: float = 1.0,
    lose_payout: float = 0.0,
) -> float:
    """Closed-form partial-exit fraction for a CONSTANT executable bid (E6, boxed).

    Binary payout (win=1, lose=0), r = v·M, z = n/W:
        r >= 1            -> x* = 1
        else interior x0  = ((1-q)·r·(1+z) - q·(1-r)) / (z·r·(1-r))
        x* = clip(x0, 0, 1)

    This is the FOC maximizer for constant depth; the depth-aware path in
    ``exit_fraction_binary`` recovers the same x* when depth is None or a single
    deep level. Only valid for the standard binary (win=1, lose=0); other payouts
    fall back to the numeric optimizer.
    """
    if not (abs(win_payout - 1.0) < 1e-12 and abs(lose_payout) < 1e-12):
        raise ValueError("closed form is for binary win=1 lose=0; use numeric path")
    n = float(position_units)
    W = float(wealth_ex_position)
    z = n / W
    M = float(np.exp(g_star * t_remaining_days))
    r = float(net_bid) * M
    if r >= 1.0:
        return 1.0
    if r <= 0.0:
        return 0.0
    if z <= _EPS:
        # degenerate: tiny position relative to wealth — sell iff r > q (boundary),
        # the interior formula's z->0 limit is all-or-nothing on the sign.
        return 1.0 if r > q_eff else 0.0
    x0 = ((1.0 - q_eff) * r * (1.0 + z) - q_eff * (1.0 - r)) / (z * r * (1.0 - r))
    return float(np.clip(x0, 0.0, 1.0))


@dataclass(frozen=True)
class ExitFractionResult:
    """Result of an exit-fraction evaluation (the SELL decision)."""

    fraction_to_sell: float
    feasible: bool                  # False ⇒ depth cannot absorb a profitable sell
    sell_dominates: bool            # full-size sell-all dominance gap > 0
    dominance_gap: float            # E2 gap at the executable net bid
    take_profit_threshold: float    # E3 min net bid for sell-all dominance
    q_eff: float                    # q after the robust quantile haircut
    source: str                     # provenance / decision tag


def exit_fraction_binary(
    q: float,
    bid: float,
    position_units: float,
    wealth_ex_position: float,
    t_remaining_days: float = 0.0,
    g_star: float = 0.0,
    fees: float = 0.0,
    win_payout: float = 1.0,
    lose_payout: float = 0.0,
    q_sd: float = 0.0,
    z_quantile: float = 0.0,
    depth: list[tuple[float, float]] | None = None,
) -> ExitFractionResult:
    """Fraction of the held position to sell now (E6 partial-exit FOC).

    Args:
        q: held-side posterior probability of winning. Use the market-blended
            q_exit (E5) here in production, NOT a raw miscalibrated posterior.
        bid: best executable bid per contract (pre-fee). Used when ``depth`` is
            None (the no-hidden-liquidity assumption must be DISABLED in
            production — pass ``depth`` so finite displayed liquidity is honored).
        position_units: n, max payout units held. NOT a cost basis (E1).
        wealth_ex_position: W, liquid wealth excluding this position.
        t_remaining_days: T, days to settlement.
        g_star: future-opportunity log-growth/day (E3). Default 0 (conservative):
            the exit only credits released-capital value once a fitted g* CI
            licenses the sell/hold sign (see scripts/fit_opportunity_growth_rate.py).
        fees: per-contract sell fee (subtracted from each level).
        q_sd, z_quantile: robust haircut — evaluate at q_eff = q - z_quantile·q_sd
            (the quantile α is derived in Q4, NOT fixed at 5%). Default 0 ⇒ raw q.
        depth: bid-depth ladder [(qty, net_bid_before_fee), ...] best-to-worse.
            If the ladder cannot absorb the requested quantity, those proceeds are
            -inf (no-fill) — we NEVER assume hidden liquidity (E6 depth law).

    Returns:
        ExitFractionResult. ``fraction_to_sell`` in [0, 1]; ``feasible`` False when
        no profitable sell fraction is fillable through the displayed depth.
    """
    q_eff = _clip_prob(q - z_quantile * q_sd)
    n = float(position_units)
    W = float(wealth_ex_position)
    net_top = max(0.0, float(bid) - float(fees))

    if n <= 0:
        return ExitFractionResult(
            0.0, False, False, float("-inf"),
            float("nan"), q_eff, "no_position_units",
        )
    if W <= 0:
        raise ValueError("wealth_ex_position must be positive")

    Mopp = float(np.exp(g_star * t_remaining_days))

    # E2/E3 diagnostics computed at the executable TOP net bid (full-size) and the
    # take-profit threshold. These are reported even when the FOC fraction is
    # interior, so the caller can shadow-log sell-dominance independently.
    gap = sell_all_dominance_gap(
        q_eff, net_top, n, W, t_remaining_days, g_star, win_payout, lose_payout,
    )
    tp_threshold = take_profit_net_bid_threshold(
        q_eff, n, W, t_remaining_days, g_star, win_payout, lose_payout,
    )
    sell_dominates = bool(np.isfinite(gap) and gap > 0.0)

    # ---- sale-cash function (depth-aware, no hidden liquidity) ----
    # x_hi is the largest sellable fraction: 1.0 with infinite (None) depth, else
    # the fraction the displayed ladder can absorb. Bounding the optimizer by x_hi
    # keeps the -inf no-fill cliff OUT of the search range so the maximizer lands on
    # the depth boundary (sell-all-fillable) when the FOC wants more than is fillable
    # — never silently under-selling to x=0 because the optimizer tripped on -inf.
    if depth is None:
        x_hi = 1.0

        def sale_cash(x: float) -> float:
            return n * float(x) * net_top * Mopp
    else:
        levels = [(float(qty), float(px)) for qty, px in depth if qty > 0]
        total_qty = float(sum(qty for qty, _ in levels))
        x_hi = float(np.clip(total_qty / n, 0.0, 1.0))

        def sale_cash(x: float) -> float:
            qty_to_sell = n * float(np.clip(x, 0.0, 1.0))
            rem = qty_to_sell
            cash = 0.0
            for qty, px in levels:
                take = min(rem, qty)
                if take <= 0:
                    break
                cash += take * max(0.0, px - float(fees))
                rem -= take
            if rem > 1e-9:  # ladder exhausted before filling — NO hidden liquidity
                return float("-inf")
            return cash * Mopp

    def utility(x: float) -> float:
        C = sale_cash(x)
        if not np.isfinite(C):
            return float("-inf")
        w1 = W + C + n * (1.0 - x) * win_payout
        w0 = W + C + n * (1.0 - x) * lose_payout
        if w1 <= 0 or w0 <= 0:
            return float("-inf")
        return q_eff * float(np.log(w1)) + (1.0 - q_eff) * float(np.log(w0))

    # Fast path: constant executable bid (depth None or a single deep level) AND a
    # standard binary payout ⇒ use the closed-form FOC x0, then verify feasibility.
    closed_form_ok = (
        win_payout == 1.0
        and lose_payout == 0.0
        and (depth is None or len(depth) == 1)
    )
    if closed_form_ok:
        const_bid = net_top if depth is None else max(0.0, float(depth[0][1]) - float(fees))
        x_cf = _partial_exit_closed_form(
            q_eff, const_bid, n, W, t_remaining_days, g_star, win_payout, lose_payout,
        )
        # Feasibility: the displayed depth must absorb the requested quantity. The
        # FOC utility is monotone increasing in x up to x*, so when x* exceeds the
        # fillable fraction x_hi the best FEASIBLE action is to sell all fillable
        # depth (x_hi), never to fall back to 0 (no hidden liquidity, E6).
        x_cf = max(0.0, min(x_cf, x_hi))
        u_cf = utility(x_cf)
        if np.isfinite(u_cf):
            return ExitFractionResult(
                float(x_cf), True, sell_dominates, gap, tp_threshold, q_eff,
                f"closed_form_x0 r={const_bid * Mopp:.4f} z={n / W:.4f}",
            )
        # else fall through to numeric (feasibility failed)

    # Depth-aware numeric path: bounded scalar maximizer over the FEASIBLE range
    # [0, x_hi]. Bounding by x_hi keeps the -inf no-fill cliff out of the search so
    # the optimizer lands on the depth boundary when the FOC wants more than is
    # fillable, instead of tripping on -inf and returning 0 (no hidden liquidity).
    candidates: list[tuple[float, float]] = [(0.0, utility(0.0)), (x_hi, utility(x_hi))]
    if x_hi > 1e-9:
        res = minimize_scalar(
            lambda x: -utility(x), bounds=(0.0, x_hi), method="bounded"
        )
        if getattr(res, "success", False):
            candidates.append((float(res.x), utility(float(res.x))))

    best_x, best_u = max(candidates, key=lambda t: t[1])
    if not np.isfinite(best_u):
        # Even holding (x=0) is -inf only under pathological wealth.
        return ExitFractionResult(
            0.0, False, sell_dominates, gap, tp_threshold, q_eff,
            "depth_infeasible_hold",
        )
    feasible = best_x <= 1e-9 or np.isfinite(sale_cash(best_x))
    return ExitFractionResult(
        float(best_x), bool(feasible), sell_dominates, gap, tp_threshold, q_eff,
        f"numeric_foc x={best_x:.4f} x_hi={x_hi:.4f}",
    )


# ---------------------------------------------------------------------------
# E5/option value — one-step future-information option value
# ---------------------------------------------------------------------------
def one_step_information_option_value(
    q: float,
    sigma_q: float,
    sale_log_value: float,
    hold_log_lose: float,
    hold_log_win: float,
) -> float:
    """Normal-approx option value of waiting one step for posterior information.

    E[max(S, H(Q'))] - max(S, H(q)) under a normal posterior transition Q' ~
    N(q, sigma_q^2). Only FUTURE-information variance has option value (E2 note:
    posterior PARAMETER variance does NOT move the fixed-action comparison since
    EU is linear in q). Use Monte-Carlo posterior-transition samples in
    production; this is the closed-form sanity check.
    """
    q = _clip_prob(q)
    sigma_q = max(float(sigma_q), 0.0)
    S = float(sale_log_value)
    L0 = float(hold_log_lose)
    L1 = float(hold_log_win)
    D = L1 - L0
    if sigma_q <= 0 or abs(D) <= 1e-12:
        return 0.0
    q0 = (S - L0) / D
    d = (q - q0) / sigma_q
    expected_max = (
        S * float(norm.cdf(-d))
        + (L0 + D * q) * float(norm.cdf(d))
        + D * sigma_q * float(norm.pdf(d))
    )
    return expected_max - max(S, L0 + q * D)
