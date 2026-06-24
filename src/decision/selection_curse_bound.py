# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/evidence/live_order_pathology/2026-06-23_selection_curse_*.md
#   (counterfactual admission winner's-curse: admitted buy_no claims ~0.83 / realizes ~0.69, monotone
#   in NO price, favorites >=0.95 calibrated, buy_yes benign; walk-forward price-conditioned
#   correction collapses the OOS over-claim to +/-0.01) + operator laws (no hardcode, settlement-
#   evidenced, not-thin via counterfactual reconstruction, do not over-gate buy_yes, tighten-only).
"""Selection-curse authorization bound — the runtime serving rule (pure, no I/O).

THE PATHOLOGY (settlement-graded over the counterfactual admission ledger, not just traded fills):
the population per-bin q is ~calibrated, but the admission gate ``q_lcb_side > price`` adversely
selects mid-price buy_no — the gate believes ~83% NO-win where the SELECTED slice realizes ~69%
(+14pp), monotone in price (cheaper/contested NO = bigger curse; favorites >=0.95 are calibrated).
buy_yes is benign. So the correction is NOT a population recalibration and NOT a separate side gate:
it is a settlement-evidenced realized-rate LOWER BOUND on the bought side, conditioned on its price.

    corrected_q_lcb_no = min(served_q_lcb_no, realized_no_rate_lcb(no_price))

* ``realized_no_rate_lcb(price)`` is the monotone (isotonic, PAVA — no hand buckets, no MIN_N/z)
  lower confidence band of the realized NO settlement rate as a function of the NO price, fit
  WALK-FORWARD on the admitted slice. It deflates mid-price NO toward its evidenced realized rate
  so a settlement-negative cross self-rejects, while deep favorites and buy_yes pass unchanged.
* ``min(...)`` => it can only ever TIGHTEN; a high realized rate never licenses a cross the served
  belief would not.
* Absent / unarmed side / price out of training support => identity (today's exact behavior). The
  same one bound is consumed by ENTRY admission and the TAKER cross, so both use one consistent,
  settlement-grounded authority (closing the gap where takers cross on the raw q_lcb).

Pure module: no I/O, no settings reads, no engine imports. The artifact is fit offline by
``scripts/fit_selection_curse_bound.py`` over the counterfactual admission ledger and loaded
read-only by ``src/decision/selection_curse_bound_loader.py``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# Basis tags travel on the decision/order receipt so settlement can audit which rule bound (or none).
BOUND_ABSENT = "BOUND_ABSENT"
SIDE_NOT_ARMED = "SIDE_NOT_ARMED"
OUT_OF_SUPPORT = "OUT_OF_SUPPORT"
BUY_YES_IDENTITY = "BUY_YES_IDENTITY"
INVALID_INPUT = "INVALID_INPUT"


@dataclass(frozen=True)
class SelectionCurseBound:
    """Frozen monotone realized-NO-rate lower band, keyed by NO price.

    ``price_knots`` ascending; ``realized_lcb[i]`` = the walk-forward lower band of the realized NO
    settlement rate at ``price_knots[i]`` (monotone non-decreasing in price — the empirical curse
    shape). ``armed_sides`` = sides whose walk-forward arm gate passed (only those are corrected).
    ``built_at`` supplied by the caller (Date-free for deterministic resume).
    """

    price_knots: tuple[float, ...]
    realized_lcb: tuple[float, ...]
    n_train: int
    armed_sides: frozenset[str]
    artifact_hash: str
    built_at: str

    def __post_init__(self) -> None:
        if len(self.price_knots) != len(self.realized_lcb):
            raise ValueError("price_knots and realized_lcb must have equal length")
        if len(self.price_knots) < 2:
            raise ValueError("need >= 2 knots to interpolate")
        if list(self.price_knots) != sorted(self.price_knots):
            raise ValueError("price_knots must be ascending")
        if any(b > a + 1e-12 for b, a in zip(self.realized_lcb, self.realized_lcb[1:])):
            raise ValueError("realized_lcb must be monotone non-decreasing in price")


def _interp_lcb(bound: SelectionCurseBound, price: float) -> Optional[float]:
    """Monotone linear interpolation of the realized-rate lower band at ``price``.

    Returns None when ``price`` is outside the trained support [min_knot, max_knot] — we never
    fabricate a realized rate where there is no settlement evidence at that price.
    """
    knots = bound.price_knots
    vals = bound.realized_lcb
    if price < knots[0] - 1e-12 or price > knots[-1] + 1e-12:
        return None
    for i in range(1, len(knots)):
        if price <= knots[i] + 1e-12:
            x0, x1 = knots[i - 1], knots[i]
            y0, y1 = vals[i - 1], vals[i]
            if x1 <= x0:
                return y1
            t = (price - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return vals[-1]


def corrected_side_q_lcb(
    bound: Optional[SelectionCurseBound],
    *,
    side: str,
    price: float,
    raw_q_lcb: float,
) -> tuple[float, str]:
    """``min(raw_q_lcb, realized_rate_lcb(price))`` for an armed buy_no; identity otherwise.

    Returns ``(q_lcb, basis)``. The bound only ever lowers ``raw_q_lcb`` (tighten-only). buy_yes,
    an absent/unarmed bound, or a price outside training support all return ``raw_q_lcb`` unchanged
    with the corresponding basis tag (never raises, never fabricates).
    """
    # Non-finite / non-numeric inputs -> identity (never raise into the live gate, never deflate on
    # a garbage price). raw_q_lcb must be finite to compare; price must be finite to interpolate.
    try:
        raw = float(raw_q_lcb)
        px = float(price)
    except (TypeError, ValueError):
        return _safe_float(raw_q_lcb), INVALID_INPUT
    if not (math.isfinite(raw) and math.isfinite(px)):
        return (raw if math.isfinite(raw) else _safe_float(raw_q_lcb)), INVALID_INPUT
    s = str(side or "").strip().lower()
    if s == "buy_yes":
        return raw, BUY_YES_IDENTITY
    if bound is None:
        return raw, BOUND_ABSENT
    if s not in bound.armed_sides:
        return raw, SIDE_NOT_ARMED
    lcb = _interp_lcb(bound, px)
    if lcb is None:
        return raw, OUT_OF_SUPPORT
    return min(raw, float(lcb)), f"SELECTION_CURSE:{s}"


def _safe_float(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
