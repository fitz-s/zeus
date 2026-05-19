# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Graded Kelly haircut based on spread × depth × order_type (PR 7)
# Reuse: Thread through _size_at_execution_price_boundary; import EffectiveKellyContext
#        and call context.haircut() to get the 5th multiplicative Kelly factor.
#        MissingEffectiveContextError enforces INV-kelly-effective on live paths.
"""EffectiveKellyContext — microstructure-aware Kelly multiplier haircut.

INV-kelly-effective: _size_at_execution_price_boundary must receive an
EffectiveKellyContext on live-money paths when wide_spread_display_substitution
is True.  Backtest / replay paths pass None and receive graceful degrade (no
haircut, WARNING logged).

Bucket policy: 3 spread tiers × 2 depth tiers × order_type column.
The $0.05 MID boundary is the convex fee-erosion midpoint; at spread ≥ $0.05
the Polymarket price-dependent fee formula (execution_price.py:130,
fee_per_share = fee_rate × p × (1-p)) erodes edge non-linearly for
mid-probability tokens.  $0.10 is the Polymarket UI display-substitution
threshold (docs.polymarket.com/trading).

FOK haircut ≥ FAK haircut at every (spread, depth) cell by construction:
FOK is price-guaranteed (fill-or-kill), so only fill probability is discounted.
FAK guarantees a partial fill at the ask price, so price risk compounds the
depth risk.  Non-FOK/FAK order types use FAK column (conservative).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

# ── Spread tier boundaries ──────────────────────────────────────────────────
SPREAD_MID_THRESHOLD_USD: Final[Decimal] = Decimal("0.05")
SPREAD_WIDE_THRESHOLD_USD: Final[Decimal] = Decimal("0.10")

# ── Depth tier boundary ─────────────────────────────────────────────────────
DEPTH_DEEP_THRESHOLD_SHARES: Final[int] = 100  # shares at best ask

# ── Haircut table (6 rows × FOK/FAK columns) ───────────────────────────────
# Rows indexed by (spread_tier, depth_tier) where:
#   spread_tier: "TIGHT" | "MID" | "WIDE"
#   depth_tier:  "DEEP"  | "SHALLOW"
# Values: (fok_haircut, fak_haircut)
_HAIRCUT_TABLE: Final[dict[tuple[str, str], tuple[float, float]]] = {
    ("TIGHT", "DEEP"):    (1.00, 1.00),
    ("TIGHT", "SHALLOW"): (0.85, 0.75),
    ("MID",   "DEEP"):    (0.90, 0.80),
    ("MID",   "SHALLOW"): (0.70, 0.55),
    ("WIDE",  "DEEP"):    (0.50, 0.30),
    ("WIDE",  "SHALLOW"): (0.30, 0.10),
}


class MissingEffectiveContextError(ValueError):
    """Raised when kelly_size is called without EffectiveKellyContext
    and wide_spread_display_substitution=True on a live-money path.

    INV-kelly-effective: wide-spread execution without microstructure context
    may systematically oversize positions.  Fail closed on live paths.
    """


@dataclass(frozen=True)
class EffectiveKellyContext:
    """Microstructure inputs for the 5th multiplicative Kelly haircut factor.

    Usage::

        context = EffectiveKellyContext(
            spread_usd=Decimal("0.08"),
            depth_at_best_ask=120,
            order_type="FOK",
        )
        km_effective = km_4x * context.haircut()

    The haircut() call returns a float in [0.0, 1.0].  Multiply it after
    the existing 4-multiplier chain (base × strategy × city × DDD).
    fee_erased=True short-circuits all bucket logic and returns 0.0.
    """

    spread_usd: Decimal          # observed bid-ask spread (ask - bid)
    depth_at_best_ask: int       # shares at best ask (0 = unavailable / one-sided)
    order_type: str              # "FOK" | "FAK" | "GTC" | "GTD" | "LIMIT"
    fee_erased: bool = False     # True when spread+fee fully erases edge

    def __post_init__(self) -> None:
        if self.depth_at_best_ask < 0:
            raise ValueError(
                f"depth_at_best_ask must be >= 0; got {self.depth_at_best_ask}"
            )
        if self.spread_usd < Decimal("0"):
            raise ValueError(
                f"spread_usd must be >= 0; got {self.spread_usd}"
            )

    def haircut(self) -> float:
        """Return the Kelly multiplier haircut scalar in [0.0, 1.0].

        fee_erased=True → 0.0 (forces kelly_size to return 0.0).
        Otherwise looks up the (spread_tier, depth_tier) cell and returns
        the FOK column for FOK orders, FAK column for all others.
        """
        if self.fee_erased:
            return 0.0

        spread_tier = _spread_tier(self.spread_usd)
        depth_tier = _depth_tier(self.depth_at_best_ask)
        fok_haircut, fak_haircut = _HAIRCUT_TABLE[(spread_tier, depth_tier)]

        if self.order_type == "FOK":
            return fok_haircut
        # FAK, GTC, GTD, LIMIT, or any unknown → use conservative FAK column
        return fak_haircut


# ── Private helpers ──────────────────────────────────────────────────────────

def _spread_tier(spread_usd: Decimal) -> str:
    """Classify spread into TIGHT / MID / WIDE bucket."""
    if spread_usd < SPREAD_MID_THRESHOLD_USD:
        return "TIGHT"
    if spread_usd < SPREAD_WIDE_THRESHOLD_USD:
        return "MID"
    return "WIDE"


def _depth_tier(depth_at_best_ask: int) -> str:
    """Classify depth into DEEP / SHALLOW bucket."""
    if depth_at_best_ask >= DEPTH_DEEP_THRESHOLD_SHARES:
        return "DEEP"
    return "SHALLOW"
