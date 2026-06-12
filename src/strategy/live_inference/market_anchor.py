# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: objective-math audit 2026-06-11 (docs/evidence/2026_06_11_objective_math/).
#   Part A settled-record + live-fused evidence: the replacement chain's tradable q carries NO
#   market term, so the ranking objective max(q_lcb - price) peaks where model q disagrees most
#   with market. On the fused-N sigma shape this manufactures a +4.8pt-mean (30-54pt tail) phantom
#   NO edge in the adjacent-center class C3, exactly where the model fights its own forecast.
#   This module restores the LEGACY antibody (alpha-weighted market fusion, the single blending
#   authority in src/strategy/market_fusion.compute_posterior) as a CAP on the tradable NO lower
#   bound. It reuses that authority's semantics (raw = alpha*q_model + (1-alpha)*q_market) and the
#   per-level alpha registry (config edge.base_alpha) -- it does NOT invent a second blend formula.
"""market_anchor — cap a tradable NO q_lcb at the alpha-blended market-anchored belief.

The replacement forecast chain (docs/authority/replacement_final_form_2026_06_09.md) blends
NWP models into a posterior with NO market term. The ranking objective is max(q_lcb - price),
so the conservative edge is largest exactly where model belief disagrees most with the market
price. Whenever the model has a systematic shape error (the fused-N sigma flattens mass off the
adjacent-center bins relative to the sharper realised/market distribution), that disagreement is
a phantom NO edge, and it ranks FIRST.

The legacy chain anchored against this with alpha-weighted market fusion (compute_posterior:
P = alpha*p_model + (1-alpha)*p_market). The replacement chain dropped it. This module reuses
that exact semantic -- as a ONE-SIDED CAP on the tradable NO lower bound, not a re-derivation:

    q_market_no    = market-implied NO probability (= 1 - market_implied_yes; in practice the
                     NO all-in execution price, which IS the market's NO probability)
    q_anchor_no    = alpha * q_model_no + (1 - alpha) * q_market_no        (legacy alpha-fusion)
    q_lcb_no_out   = min(q_lcb_no_in, q_anchor_no)                          (one-sided honesty cap)

A NO candidate can therefore never claim a more conservative edge than the market-anchored
belief licenses. The cap only ever LOWERS the tradable lower bound (one-sided, like the
settlement-coverage shrink) -- it never widens an edge, so it cannot create a new trade.

Scope discipline (the evidence dictates WHERE):
  * The cap is applied ONLY to buy_no candidates whose bin is near the forecast center
    (within `near_center_steps` settlement steps of mu). That is class C1-C3 -- the only place
    the audit found a material model<-market gap. C4 (far NO, the legitimate favorite-longshot
    harvest) is LEFT UNTOUCHED: the audit showed market ~= model there (gap -0.2pt), so the cap
    is a near-no-op on C4 anyway, but excluding it structurally guarantees the harvest survives.
  * alpha comes from the per-decision legacy registry value passed by the caller (edge.base_alpha
    by calibration level). Higher alpha = trust model more = weaker cap. There is no second alpha.

Pure module: no I/O, no settings reads, no engine imports. The flag gate + alpha lookup + market
price extraction live at the (impure) caller in event_reactor_adapter.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Default near-center reach (in settlement steps) for the cap's scope. The audit's near-center
# classes are C1 (forecast bin), C2 (boundary zone), C3 (<=1.5 steps). 1.5 steps captures all
# three; beyond it is C4 (the harvest), left untouched. Matches direction_law's adjacent reach.
DEFAULT_NEAR_CENTER_STEPS = 1.5

# Default alpha when the caller cannot supply a per-level value (conservative mid-trust: equal
# weight floored toward the legacy level-3 value). Never used when the caller passes a real alpha.
DEFAULT_FALLBACK_ALPHA = 0.4

MARKET_ANCHOR_REASON = "MARKET_ANCHOR_QLCB_NO_CAPPED"


@dataclass(frozen=True)
class MarketAnchorResult:
    """Outcome of the market-anchor cap for one NO candidate.

    ``q_lcb_no_out`` is the (possibly lowered) tradable NO lower bound. ``capped`` is True iff
    the anchor blend was BELOW the input lower bound and actually moved it. ``reason`` is a
    receipt tag when capped, else None. ``q_anchor_no`` / ``q_market_no`` are recorded for the
    receipt so the settlement loop can later re-derive the realised cap quality.
    """

    q_lcb_no_out: float
    capped: bool
    q_anchor_no: float
    q_market_no: float
    alpha: float
    reason: str | None


def _finite(x: object) -> float | None:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def market_anchored_no_lcb(
    *,
    q_lcb_no: float,
    q_model_no: float,
    market_no_price: float | None,
    alpha: float | None,
    bin_distance_steps: float | None,
    near_center_steps: float = DEFAULT_NEAR_CENTER_STEPS,
) -> MarketAnchorResult:
    """Cap a buy_no tradable q_lcb at the legacy alpha-blended market-anchored NO belief.

    Args:
        q_lcb_no: the model's tradable NO lower bound (1 - q_ucb_yes), in [0, 1].
        q_model_no: the model's NO point belief (1 - q_yes), in [0, 1]. The alpha-fusion blends
            the POINT belief with the market (mirrors compute_posterior, which blends p_cal not
            its lower bound); the result then CAPS the lower bound.
        market_no_price: the market-implied NO probability (the NO all-in execution price, which
            in a complete market IS the market's NO probability). None -> no market evidence ->
            NEVER cap (fail-open: a missing market price must not fabricate a haircut).
        alpha: legacy model-trust weight in [0, 1] (edge.base_alpha by level). None -> fallback.
        bin_distance_steps: |distance(bin, mu)| in settlement steps (0 inside the bin). None ->
            treated as near-center (fail-toward-applying the cap, since a missing center on a NO
            leg is itself the incident category). Far bins (> near_center_steps) are NOT capped.
        near_center_steps: scope reach; bins beyond it (C4 harvest) are returned unchanged.

    Returns:
        MarketAnchorResult. ``q_lcb_no_out`` <= ``q_lcb_no`` always (one-sided). When the market
        price is missing, or the bin is far, or the anchor blend is >= the input, the input is
        returned unchanged (capped=False).
    """
    q_in = _finite(q_lcb_no)
    q_pt = _finite(q_model_no)
    if q_in is None:
        # Degenerate input -> pass through unchanged (the caller's clamps own validity).
        return MarketAnchorResult(
            q_lcb_no_out=float(q_lcb_no), capped=False, q_anchor_no=float("nan"),
            q_market_no=float("nan"), alpha=float("nan"), reason=None,
        )
    q_in = min(max(q_in, 0.0), 1.0)
    if q_pt is None:
        q_pt = q_in  # no separate point -> blend the lower bound itself (still one-sided)

    mkt = _finite(market_no_price)
    if mkt is None:
        return MarketAnchorResult(
            q_lcb_no_out=q_in, capped=False, q_anchor_no=float("nan"),
            q_market_no=float("nan"), alpha=float("nan"), reason=None,
        )
    mkt = min(max(mkt, 0.0), 1.0)

    # Scope: only near-center bins (C1-C3). Far bins (C4) are the legitimate harvest -> untouched.
    dist = _finite(bin_distance_steps)
    if dist is not None and dist > float(near_center_steps):
        return MarketAnchorResult(
            q_lcb_no_out=q_in, capped=False, q_anchor_no=float("nan"),
            q_market_no=mkt, alpha=float("nan"), reason=None,
        )

    a = _finite(alpha)
    if a is None:
        a = DEFAULT_FALLBACK_ALPHA
    a = min(max(a, 0.0), 1.0)

    # Legacy alpha-fusion semantics (compute_posterior): blend the POINT belief with the market.
    q_anchor_no = a * min(max(q_pt, 0.0), 1.0) + (1.0 - a) * mkt
    q_anchor_no = min(max(q_anchor_no, 0.0), 1.0)

    # One-sided cap: only ever LOWER the tradable lower bound.
    if q_anchor_no < q_in:
        return MarketAnchorResult(
            q_lcb_no_out=q_anchor_no, capped=True, q_anchor_no=q_anchor_no,
            q_market_no=mkt, alpha=a,
            reason=(
                f"{MARKET_ANCHOR_REASON}:q_lcb_no={q_in:.4f}->{q_anchor_no:.4f}:"
                f"q_market_no={mkt:.4f}:alpha={a:.3f}"
            ),
        )
    return MarketAnchorResult(
        q_lcb_no_out=q_in, capped=False, q_anchor_no=q_anchor_no,
        q_market_no=mkt, alpha=a, reason=None,
    )
