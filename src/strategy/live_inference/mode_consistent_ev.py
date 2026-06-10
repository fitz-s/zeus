# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: FIX C for incident 0b5c305e26524042 (Milan 24C first fill;
#   docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md §3) + operator
#   directive 2026-06-10: mode-consistent evaluation. The system is structurally
#   a maker; evaluation must price the mode it will actually execute.
"""Mode-consistent EV: explicit taker and maker formulas, selected per candidate.

The pre-incident hybrid evaluated EVERY candidate at TAKER cost (depth-walked ask
+ taker fee) multiplied by a visible-depth p_fill (~1.0 for crossing) while the
execution design is maker-resting primary. That hybrid (a) overstated cost for
maker entries, (b) ignored maker fill probability (measured live: ~10.8% resting
fill rate, ZERO fills at p 0.30-0.80), and (c) ignored adverse selection — a
resting buy fills disproportionately when the news moved AGAINST us (q|fill < q:
a selection effect the q_lcb does NOT cover, because the LCB bounds parameter
uncertainty of q, not the conditioning event "we got filled").

Two explicit per-share EV formulas (same probability units as robust_trade_score,
penalty included for cross-candidate comparability):

  EV_taker = p_fill_taker x (q_lcb - taker_all_in_cost - penalty)
      (today's crossing formula; admissible ONLY when the relative-spread guard
       passes — crossing a wide spread is forbidden regardless of edge).

  EV_maker = p_fill_maker x (q_fill_adj - maker_limit - penalty)
      maker_limit = tick_down(min(bid + tick, ask - tick, reservation))
          (bid-improving; the ask - tick cap makes a crossing maker limit
           UNCONSTRUCTABLE at the price level even where the venue ignores
           post_only — at a one-tick spread the order joins the bid instead of
           lifting the ask)
      q_fill_adj = max(0, q_lcb - lambda x half_spread)
          (first-order microstructure adverse-selection haircut: a fill on our
           bid-side rest implies the mid moved toward us by ~half the spread of
           bad news; lambda = 1.0 until the settlement loop measures the real
           haircut from fill_tracker facts)
      p_fill_maker: a conservative resting-fill prior (NOT the visible-depth
          taker coverage). Provenance is recorded so settlement can recalibrate.

Mode selection: compute BOTH, choose the max admissible. Both EVs always travel
on the receipt so the settlement loop can learn the real fill/haircut parameters.

Pure module: no I/O, no settings reads, no engine imports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Crossing a book whose relative spread (ask-bid)/mid exceeds this is forbidden
# regardless of edge: a wide spread IS the illiquidity signal, and the measured
# "edge" against such a book is dominated by model error (incident: 56% relative
# spread, edge measured with an unlicensed tail q). Maker resting stays allowed.
TAKER_MAX_RELATIVE_SPREAD = 0.25

# Conservative resting-fill prior for maker EV (measured live fill rate ~10.8%,
# fee study 2026-06; recalibrate from fill_tracker facts as settlement accrues).
MAKER_FILL_PROBABILITY_PRIOR = 0.10
MAKER_FILL_PROBABILITY_SOURCE = "fee_study_2026_06_prior"

# Full half-spread = the standard first-order adverse-selection estimate.
MAKER_ADVERSE_SELECTION_LAMBDA = 1.0

TAKER_SPREAD_GUARD_REASON = "TAKER_FORBIDDEN_RELATIVE_SPREAD"

PLACEMENT_MAKER = "maker_bid_improve"
PLACEMENT_TAKER = "taker_cross"


def _finite(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def tick_round_down(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    return round(math.floor(price / tick_size + 1e-9) * tick_size, 10)


def relative_spread(best_bid: float | None, best_ask: float | None) -> float | None:
    """(ask - bid) / mid; None when the two-sided book is absent/invalid."""
    bid = _finite(best_bid)
    ask = _finite(best_ask)
    if bid is None or ask is None or bid <= 0.0 or ask <= 0.0 or ask < bid:
        return None
    mid = (ask + bid) / 2.0
    if mid <= 0.0:
        return None
    return (ask - bid) / mid


def taker_spread_guard_reason(
    best_bid: float | None,
    best_ask: float | None,
    *,
    max_relative_spread: float = TAKER_MAX_RELATIVE_SPREAD,
) -> str | None:
    """Reason the TAKER lane is forbidden, or None when crossing is allowed.

    Fail-CLOSED for crossing: a book with no measurable two-sided spread (missing
    bid or ask) is the extreme illiquidity case — taker is forbidden there too.
    """
    spread = relative_spread(best_bid, best_ask)
    if spread is None:
        return f"{TAKER_SPREAD_GUARD_REASON}:spread=unmeasurable:max={max_relative_spread:.2f}"
    if spread > max_relative_spread:
        return f"{TAKER_SPREAD_GUARD_REASON}:spread={spread:.4f}:max={max_relative_spread:.2f}"
    return None


def maker_limit_price(
    *,
    best_bid: float | None,
    best_ask: float | None,
    tick_size: float,
    reservation: float,
) -> float | None:
    """Bid-improving, structurally non-crossing maker limit.

    tick_down(min(bid + tick, ask - tick, reservation)). Missing bid -> rest at
    min(ask - tick, reservation); missing ask -> min(bid + tick, reservation);
    both missing -> reservation. Returns None when the result is not a positive
    price (no maker placement exists).
    """
    bid = _finite(best_bid)
    ask = _finite(best_ask)
    tick = max(float(tick_size), 0.0)
    bound = float(reservation)
    if bid is not None:
        bound = min(bound, bid + tick)
    if ask is not None:
        bound = min(bound, ask - tick)
    limit = tick_round_down(bound, tick)
    if not math.isfinite(limit) or limit <= 0.0:
        return None
    return limit


def maker_adverse_selection_haircut(
    *,
    best_bid: float | None,
    best_ask: float | None,
    maker_limit: float,
    lambda_adverse: float = MAKER_ADVERSE_SELECTION_LAMBDA,
) -> float:
    """lambda x half-spread; with no bid, the limit acts as our bid side."""
    ask = _finite(best_ask)
    bid = _finite(best_bid)
    if ask is None:
        return 0.0
    reference_bid = bid if bid is not None else float(maker_limit)
    half_spread = max(0.0, (ask - reference_bid) / 2.0)
    return float(lambda_adverse) * half_spread


@dataclass(frozen=True)
class ModeConsistentEv:
    """Per-candidate mode decision with BOTH EVs (settlement-loop provenance)."""

    chosen_mode: str  # "MAKER" | "TAKER"
    chosen_ev: float
    ev_taker: float | None
    ev_maker: float | None
    maker_limit_price: float | None
    relative_spread: float | None
    taker_forbidden_reason: str | None
    maker_fill_probability: float
    maker_fill_probability_source: str
    placement: str  # PLACEMENT_MAKER | PLACEMENT_TAKER


def select_mode_consistent_ev(
    *,
    q_lcb: float,
    taker_all_in_cost: float | None,
    p_fill_taker: float,
    best_bid: float | None,
    best_ask: float | None,
    tick_size: float,
    reservation: float,
    p_fill_maker: float = MAKER_FILL_PROBABILITY_PRIOR,
    p_fill_maker_source: str = MAKER_FILL_PROBABILITY_SOURCE,
    lambda_adverse: float = MAKER_ADVERSE_SELECTION_LAMBDA,
    max_relative_spread: float = TAKER_MAX_RELATIVE_SPREAD,
    penalty: float = 0.0,
) -> ModeConsistentEv:
    """Compute EV_taker and EV_maker; choose the better ADMISSIBLE one.

    Taker is admissible only when the relative-spread guard passes AND a taker
    cost exists. Maker is admissible whenever a positive non-crossing limit
    exists. When neither is admissible the result is a MAKER decision with
    chosen_ev = -inf: the candidate cannot be priced in either mode, and the
    non-positive EV blocks it at the trade-score gate.
    """
    q = float(q_lcb)
    spread = relative_spread(best_bid, best_ask)
    taker_forbidden = taker_spread_guard_reason(
        best_bid, best_ask, max_relative_spread=max_relative_spread
    )
    cost = _finite(taker_all_in_cost)
    ev_taker: float | None = None
    if cost is not None:
        ev_taker = max(0.0, min(1.0, float(p_fill_taker))) * (q - cost - float(penalty))

    limit = maker_limit_price(
        best_bid=best_bid, best_ask=best_ask, tick_size=tick_size, reservation=reservation
    )
    ev_maker: float | None = None
    if limit is not None:
        haircut = maker_adverse_selection_haircut(
            best_bid=best_bid,
            best_ask=best_ask,
            maker_limit=limit,
            lambda_adverse=lambda_adverse,
        )
        q_fill_adj = max(0.0, q - haircut)
        ev_maker = max(0.0, min(1.0, float(p_fill_maker))) * (
            q_fill_adj - limit - float(penalty)
        )

    taker_allowed = ev_taker is not None and taker_forbidden is None
    maker_allowed = ev_maker is not None
    if taker_allowed and (not maker_allowed or ev_taker >= ev_maker):
        chosen_mode, chosen_ev, placement = "TAKER", float(ev_taker), PLACEMENT_TAKER
    elif maker_allowed:
        chosen_mode, chosen_ev, placement = "MAKER", float(ev_maker), PLACEMENT_MAKER
    else:
        chosen_mode, chosen_ev, placement = "MAKER", float("-inf"), PLACEMENT_MAKER
    return ModeConsistentEv(
        chosen_mode=chosen_mode,
        chosen_ev=chosen_ev,
        ev_taker=ev_taker,
        ev_maker=ev_maker,
        maker_limit_price=limit,
        relative_spread=spread,
        taker_forbidden_reason=taker_forbidden,
        maker_fill_probability=float(p_fill_maker),
        maker_fill_probability_source=str(p_fill_maker_source),
        placement=placement,
    )
