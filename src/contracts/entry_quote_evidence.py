# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave3 +
#                  §15.7 zeus_math_spec.md (market-cost executable seam + σ_c construction)
"""EntryQuoteEvidence — typed executable-cost evidence object.

Wave 3 (2026-05-27) deliverable. Wraps an orderbook snapshot, depth-walk fill
estimate, fee rate, quote freshness, and the derived ``cost_uncertainty`` that
will feed the edge-bootstrap σ_market sampling in Wave 5.

Conceptual model:
  upstream orderbook (bids/asks ladder) + target_shares estimate
  → walk_asks_for_target_shares (depth-walk math)
  → EntryQuoteEvidence (typed evidence)
  → .to_execution_price() (typed price at the Kelly boundary)

The dataclass deliberately keeps both ``best_ask`` and ``fill_price_walk``
separately so downstream consumers can choose: legacy VWMP-only path uses
``best_ask``; depth-aware path uses ``fill_price_walk``; Wave 5 σ_market uses
the difference (``slippage_bps``) plus ``spread_usd`` plus quote_age penalty.

NOTHING in this dataclass mutates runtime state. It is a pure value object
constructed by ``entry_quote_evidence_from_orderbook`` and consumed downstream.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from src.contracts.execution_price import (
    ExecutionPrice,
    polymarket_fee,
)
from src.data.orderbook_depth_walk import (
    DepthWalkResult,
    walk_asks_for_target_shares,
)

# Status taxonomy. Hard-veto-eligible statuses (THIN_BOOK, CROSSED) MUST cause
# the caller to skip executable sizing; soft statuses (STALE, ASK_ONLY) feed
# into ``cost_uncertainty`` and σ_market only.
ReliabilityStatus = Literal[
    "LIVE_OK",     # two-sided book, fresh, depth-sufficient
    "STALE",       # quote_age_ms > stale_threshold (caller-supplied)
    "ASK_ONLY",    # bids missing — buy still executable at best_ask
    "THIN_BOOK",   # depth_walked_shares < target_shares (insufficient depth)
    "CROSSED",     # bid >= ask (degenerate orderbook)
]

# Default stale threshold — Wave 5 may make this caller-configurable.
DEFAULT_STALE_THRESHOLD_MS = 2_500

# ASK_ONLY cost-uncertainty floor (probability units). When the book is
# one-sided (no bid), the spread is unknown and ``spread_usd`` defaults to 0,
# which understates the true execution uncertainty. This conservative
# absolute floor keeps σ_market from looking artificially tight on one-sided
# books so edge_LCB widens appropriately (PR #348 operator review, Blocker 5c).
ASK_ONLY_COST_UNCERTAINTY_FLOOR = 0.02


@dataclass(frozen=True)
class EntryQuoteEvidence:
    """Typed executable-cost evidence for one (token, side) at one snapshot.

    Attributes:
        token_id: CLOB token id (YES or NO leg of a Polymarket market).
        side: "yes" or "no" — which side the buy is executing against.
        best_bid: top-of-book bid price (None when book is one-sided/ask-only).
        best_ask: top-of-book ask price (always present — buy requires asks).
        spread_usd: best_ask - best_bid (0.0 when ASK_ONLY).
        top_of_book_size: shares at the best_ask price level.
        depth_at_target_size: shares the orderbook can fill at <= target_size.
            Equal to target_size when depth_sufficient, otherwise smaller.
        fill_price_walk: depth-weighted average fill price for target_size.
        slippage_bps: (fill_price_walk - best_ask) / best_ask * 10000.
        quote_age_ms: monotonic age of the snapshot at construction time.
        book_hash: deterministic hash of the orderbook (caller-supplied;
            empty string when not provided). Used for replay determinism +
            audit linkage to the captured snapshot.
        fee_rate: per-market taker fee rate (Polymarket fee formula —
            ``fee_per_share = fee_rate * p * (1 - p)`` — applied at
            ``fill_price_walk``).
        fee_per_share: ``polymarket_fee(fill_price_walk, fee_rate)``.
        all_in_entry_price: ``fill_price_walk + fee_per_share`` — the
            value that Kelly must use as the cost-of-entry.
        cost_uncertainty: σ_market input for Wave 5 bootstrap. RSS of
            independent error sources, all in ABSOLUTE price units:
            ``sqrt(half_spread^2 + slippage_abs^2 + ask_only_penalty^2 +
            quote_age_penalty^2 + fee_variance)``. ``slippage_bps`` is NOT
            used here (it is a relative ratio kept for audit only).
        reliability_status: see ReliabilityStatus above.
    """

    token_id: str
    side: Literal["yes", "no"]
    best_bid: float | None
    best_ask: float
    spread_usd: float
    top_of_book_size: float
    depth_at_target_size: float
    fill_price_walk: float
    slippage_bps: float
    quote_age_ms: int
    book_hash: str
    fee_rate: float
    fee_per_share: float
    all_in_entry_price: float
    cost_uncertainty: float
    reliability_status: ReliabilityStatus

    def to_execution_price(self) -> ExecutionPrice:
        """Return a typed ExecutionPrice ready for the Kelly boundary.

        ``price_type="fee_adjusted"`` because ``all_in_entry_price`` already
        includes the Polymarket fee; ``fee_deducted=True`` so
        ``assert_kelly_safe()`` passes without an additional ``with_taker_fee``
        application (which would double-charge the fee).
        """
        return ExecutionPrice(
            value=self.all_in_entry_price,
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        )

    @classmethod
    def schema_packet(cls) -> dict:
        """Return typed schema descriptor for K2/K3 consumption contracts."""
        return {
            "type": "EntryQuoteEvidence",
            "required_fields": [
                "token_id", "side", "best_bid", "best_ask", "spread_usd",
                "top_of_book_size", "depth_at_target_size", "fill_price_walk",
                "slippage_bps", "quote_age_ms", "book_hash",
                "fee_rate", "fee_per_share", "all_in_entry_price",
                "cost_uncertainty", "reliability_status",
            ],
        }


def _reliability_status(
    *,
    best_bid: float | None,
    best_ask: float,
    depth_sufficient: bool,
    quote_age_ms: int,
    stale_threshold_ms: int,
) -> ReliabilityStatus:
    # Severity order (PR #348 operator review, Blocker 5b):
    #   CROSSED > THIN_BOOK > STALE > ASK_ONLY > LIVE_OK
    # Hard-veto-eligible statuses (CROSSED, THIN_BOOK) MUST dominate the soft
    # statuses (STALE, ASK_ONLY). The previous order returned "STALE" for a
    # stale+thin book, which slipped past the market_analysis hard-veto
    # (``reliability_status in ("THIN_BOOK", "CROSSED")``) and let a degenerate
    # depth-insufficient book reach edge construction. THIN_BOOK before STALE
    # closes that boundary hole.
    if best_bid is not None and best_bid >= best_ask:
        return "CROSSED"
    if not depth_sufficient:
        return "THIN_BOOK"
    if quote_age_ms > stale_threshold_ms:
        return "STALE"
    if best_bid is None:
        return "ASK_ONLY"
    return "LIVE_OK"


def entry_quote_evidence_from_orderbook(
    *,
    token_id: str,
    side: Literal["yes", "no"],
    orderbook: dict,
    target_shares: float,
    fee_rate: float,
    quote_age_ms: int = 0,
    book_hash: str = "",
    stale_threshold_ms: int = DEFAULT_STALE_THRESHOLD_MS,
) -> EntryQuoteEvidence:
    """Build EntryQuoteEvidence from a normalized orderbook dict.

    Args:
        token_id: CLOB token id (YES or NO leg).
        side: "yes" or "no".
        orderbook: ``{"bids": [...], "asks": [...]}`` as returned by
            ``PolymarketClient.get_orderbook``.
        target_shares: estimated order size in shares (caller-supplied; Wave 5
            wires this to ``min_order_usd / best_ask`` proxy at edge-scan).
        fee_rate: market-specific Polymarket taker fee rate (0.0 when fees
            disabled).
        quote_age_ms: monotonic age of the orderbook snapshot. 0 when caller
            does not have an age (legacy paths).
        book_hash: deterministic content hash of the orderbook for replay +
            audit. Empty string allowed.
        stale_threshold_ms: quote-age cutoff for STALE reliability marker.

    Returns:
        EntryQuoteEvidence with all fields populated.

    Raises:
        ValueError: orderbook missing asks, depth walk fails, fee_rate or
            quote_age_ms invalid.
    """
    if fee_rate < 0 or fee_rate >= 1:
        raise ValueError(f"fee_rate out of [0, 1) bounds: {fee_rate}")
    if quote_age_ms < 0:
        raise ValueError(f"quote_age_ms must be >= 0, got {quote_age_ms}")

    asks = orderbook.get("asks") or []
    bids = orderbook.get("bids") or []
    if not asks:
        raise ValueError(f"orderbook for token {token_id!r} has no asks; cannot price BUY")

    walk: DepthWalkResult = walk_asks_for_target_shares(asks, target_shares)

    # Top-of-book aggregates: best_bid (None when one-sided), best_ask + size.
    best_ask = walk.best_ask
    top_of_book_size = 0.0
    for entry in asks:
        price = float(entry["price"]) if isinstance(entry, dict) else float(entry[0])
        size = float(entry["size"]) if isinstance(entry, dict) else float(entry[1])
        if price == best_ask:
            top_of_book_size += size

    best_bid: float | None
    if bids:
        try:
            best_bid = max(
                float(entry["price"]) if isinstance(entry, dict) else float(entry[0])
                for entry in bids
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"orderbook bids ladder malformed: {exc}") from exc
    else:
        best_bid = None

    spread_usd = (best_ask - best_bid) if best_bid is not None else 0.0

    fee_per_share = polymarket_fee(walk.fill_price_walk, fee_rate) if fee_rate > 0 else 0.0
    all_in_entry_price = walk.fill_price_walk + fee_per_share

    # cost_uncertainty (σ_market) — Blocker 2 (PR #348 operator review):
    # every term MUST be in ABSOLUTE price units (probability_units), the same
    # space as ``all_in_entry_price``. The earlier formula mixed an absolute
    # half-spread with a RELATIVE slippage ratio (``slippage_bps / 10_000`` =
    # (fill - ask) / ask), which is dimensionally invalid under RSS. Compose
    # independent error sources entirely in absolute price units:
    #     σ_market = sqrt( half_spread^2 + slippage_abs^2 + ask_only_penalty^2
    #                      + quote_age_penalty^2 + fee_variance )
    # ``slippage_bps`` is retained on the dataclass for trace/audit only — it
    # never enters the variance composition.
    slippage_abs = max(0.0, walk.fill_price_walk - best_ask)  # absolute price units
    half_spread = spread_usd / 2.0
    fee_variance = 0.0  # deterministic given realised fill; reserved if fee_rate uncertain
    # ASK_ONLY floor (Blocker 5c): bids missing → spread_usd=0 understates the
    # true execution uncertainty, so apply a conservative absolute floor.
    ask_only_penalty = ASK_ONLY_COST_UNCERTAINTY_FLOOR if best_bid is None else 0.0
    if quote_age_ms > stale_threshold_ms:
        # Linear penalty 0..0.005 over a 10s post-stale window (caps the
        # contribution; staler quotes are flagged STALE/THIN_BOOK reliability).
        excess_ms = min(10_000, max(0, quote_age_ms - stale_threshold_ms))
        quote_age_penalty = 0.005 * (excess_ms / 10_000.0)
    else:
        quote_age_penalty = 0.0
    cost_uncertainty = math.sqrt(
        half_spread * half_spread
        + slippage_abs * slippage_abs
        + ask_only_penalty * ask_only_penalty
        + quote_age_penalty * quote_age_penalty
        + fee_variance
    )

    status = _reliability_status(
        best_bid=best_bid,
        best_ask=best_ask,
        depth_sufficient=walk.depth_sufficient,
        quote_age_ms=quote_age_ms,
        stale_threshold_ms=stale_threshold_ms,
    )

    return EntryQuoteEvidence(
        token_id=token_id,
        side=side,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_usd=spread_usd,
        top_of_book_size=top_of_book_size,
        depth_at_target_size=walk.depth_walked_shares,
        fill_price_walk=walk.fill_price_walk,
        slippage_bps=walk.slippage_bps,
        quote_age_ms=quote_age_ms,
        book_hash=book_hash,
        fee_rate=fee_rate,
        fee_per_share=fee_per_share,
        all_in_entry_price=all_in_entry_price,
        cost_uncertainty=cost_uncertainty,
        reliability_status=status,
    )
