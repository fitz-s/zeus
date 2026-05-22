# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §6
#                  + docs/reference/zeus_strategy_spec.md §8.3-8.5
#                  + docs/reference/zeus_math_spec.md §11.5 (sweep/fee)
"""CenterSellParity — YES/NO binary parity arbitrage, deterministic layer.

Theorem (§6 / §8.3): for a binary market where YES+NO are fully collateralised
to $1, if executable asks satisfy:

    a_YES + a_NO + fees < 1

then buying both legs and merging/redeeming gives pathwise profit:

    Π = q − A_YES(q) − A_NO(q) − F_YES(q) − F_NO(q)  > 0

where:
    A_side(q) = Σ_ℓ p_ℓ · Δq_ℓ          sweep notional (§11.5)
    F_side(q) = Σ_ℓ phi(Δq_ℓ, p_ℓ, r)   taker fee per level

Payoff identity: one of {YES, NO} settles to 1 and the other to 0,
so the pair pays exactly q* regardless of outcome. vector_payoff = q_star.

Decision schema: §19.3 VectorEdgeDecision (2-leg basket).
    strategy_key = "center_sell"
    proof_type   = "center_pair_parity"

Data source:  analysis.binary_book_snapshot: LegBook
    Absent / None → no_trade CENTER_PAIR_PARITY_BOOK_UNAVAILABLE.

Bid-side reverse unwind (bid_YES + bid_NO > 1): DEFERRED.
    LegBook carries only ask levels; bid book is not available in the
    current type system. This path is out of scope.

live_status: shadow only. No evaluator routing. kelly=0.
basket_execution_id = "" until multi-leg execution lands (per §11.8).
INV-37: conn supplied by caller; never auto-opened here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from src.contracts.no_trade_reason import NoTradeReason
from src.state.decision_events import write_shadow_decision_event
from src.strategy.fees import phi, venue_fee_rate

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    LegBook,
    LegIntent,
    PriceLevel,
    VectorEdgeDecision,
    write_candidate_no_trade_row,
)


# ---------------------------------------------------------------------------
# Level-by-level sweep helpers (§11.5) — reuse pattern from neg_risk_basket
# ---------------------------------------------------------------------------

def _sweep(
    levels: tuple[PriceLevel, ...], q: Decimal, fee_rate: Decimal
) -> tuple[Decimal, Decimal]:
    """Compute A(q) and F(q) for one side of the binary token.

    §11.5: A(q) = Σ_ℓ p_ℓ·Δq_ℓ; F(q) = Σ_ℓ phi(Δq_ℓ, p_ℓ, r).
    Levels consumed in ascending price order (best ask first).
    Returns (sweep_cost, fee).
    """
    remaining = q
    cost = Decimal(0)
    fee_total = Decimal(0)
    for lv in sorted(levels, key=lambda x: x.price):
        fill = min(remaining, lv.quantity)
        if fill <= 0:
            continue
        cost += lv.price * fill
        fee_total += phi(fill, lv.price, fee_rate)
        remaining -= fill
        if remaining <= 0:
            break
    return cost, fee_total


def _total_depth(levels: tuple[PriceLevel, ...]) -> Decimal:
    """Sum of quantities across all price levels."""
    return sum((lv.quantity for lv in levels), Decimal(0))


def _merged_breakpoints(leg: LegBook) -> list[Decimal]:
    """Cumulative-depth breakpoints from the merged YES+NO depth ladders.

    §11.7 adapted: D = sorted union of cumulative YES depths ∪ cumulative NO depths,
    bounded by min(depth_YES, depth_NO). Π is piecewise-linear → q* at a breakpoint.
    """
    depth_yes = _total_depth(leg.yes_levels)
    depth_no = _total_depth(leg.no_levels)
    bound = min(depth_yes, depth_no)
    if bound <= Decimal(0):
        return []

    pts: set[Decimal] = set()
    # YES breakpoints
    cumulative = Decimal(0)
    for lv in sorted(leg.yes_levels, key=lambda x: x.price):
        cumulative += lv.quantity
        if cumulative <= bound:
            pts.add(cumulative)
    # NO breakpoints
    cumulative = Decimal(0)
    for lv in sorted(leg.no_levels, key=lambda x: x.price):
        cumulative += lv.quantity
        if cumulative <= bound:
            pts.add(cumulative)
    pts.add(bound)
    return sorted(pts)


def _pi(leg: LegBook, q: Decimal, fee_rate: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Π(q) = q − A_YES(q) − A_NO(q) − F_YES(q) − F_NO(q).

    Returns (total_cost, total_fee, profit, payoff=q).
    payoff is always q (binary pair collateralised to $1).
    """
    a_yes, f_yes = _sweep(leg.yes_levels, q, fee_rate)
    a_no, f_no = _sweep(leg.no_levels, q, fee_rate)
    total_cost = a_yes + a_no
    total_fee = f_yes + f_no
    profit = q - total_cost - total_fee
    return total_cost, total_fee, profit, q


def _opt(leg: LegBook, fee_rate: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return (q*, total_cost, total_fee, Π(q*)) via breakpoint search."""
    pts = _merged_breakpoints(leg)
    if not pts:
        return Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    best = (Decimal(0), Decimal(0), Decimal(0), Decimal(0))  # (q, cost, fee, profit)
    for q in pts:
        cost, fee, pi, _ = _pi(leg, q, fee_rate)
        if pi > best[3]:
            best = (q, cost, fee, pi)
    return best


# ---------------------------------------------------------------------------
# CenterSellParity candidate
# ---------------------------------------------------------------------------

class CenterSellParity(BaseStrategyCandidate):
    """YES/NO binary parity arb — deterministic layer only.

    Emits VectorEdgeDecision on enter (2-leg basket: buy_yes + buy_no on same
    condition_id). Writes ONE shadow decision_events row.

    proof_type = "center_pair_parity" (§8.4).
    live_status: shadow. No evaluator routing. kelly=0.

    Bid-side reverse unwind (bid_YES+bid_NO>1) is DEFERRED — no bid book
    available in LegBook; see module docstring.
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="center_sell",
                family="center_sell",
                description=(
                    "YES/NO binary parity arbitrage — deterministic layer. "
                    "Enter iff a_YES + a_NO + fees < 1 (pathwise Π > 0). "
                    "proof_type='center_pair_parity'. Shadow shadow only; kelly=0. "
                    "Authority: zeus_strategy_spec §8.3, STRATEGY_TAXONOMY_DIRECTIVE §6."
                ),
                executable_alpha=False,
            )
        )

    def evaluate(
        self,
        *,
        context: CandidateContext,
        conn: sqlite3.Connection,
        decision_time: datetime,
    ) -> CandidateDecision | VectorEdgeDecision:
        """Evaluate binary YES/NO parity arbitrage.

        Guard path (reason=CENTER_PAIR_PARITY_BOOK_UNAVAILABLE):
          - No binary_book_snapshot on analysis (attribute absent or None).

        No-trade path (reason=CENTER_PAIR_PARITY_NO_EDGE):
          - Π(q*) ≤ 0 at all breakpoints (data present, no profitable arb).
          - Zero depth on either leg (q* = 0).

        Enter path:
          - Π(q*) > 0.
          - Emits §19.3 VectorEdgeDecision with 2 LegIntents (buy_yes + buy_no,
            same condition_id), q_star, vector_cost, vector_fee,
            vector_payoff=q_star, vector_profit.
          - Writes ONE shadow decision_events row.

        Never returns None. Never raises; all guard failures become no_trade.
        """
        analysis = context.analysis
        market_slug = context.natural_key[0]

        fee_rate = venue_fee_rate()

        # --- Guard: binary_book_snapshot present ---
        leg: Optional[LegBook] = getattr(analysis, "binary_book_snapshot", None)
        if leg is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CENTER_PAIR_PARITY_BOOK_UNAVAILABLE,
                reason_detail=(
                    f"center_sell: binary_book_snapshot absent on analysis "
                    f"for market_slug={market_slug!r}; parity arb requires YES+NO book."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Compute q* and Π(q*) via breakpoint search ---
        try:
            q_star, vector_cost, vector_fee_val, pi_star = _opt(leg, fee_rate)
        except Exception as exc:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CENTER_PAIR_PARITY_NO_EDGE,
                reason_detail=(
                    f"center_sell: sweep/fee computation failed for "
                    f"market_slug={market_slug!r}: {exc!r}"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        if pi_star <= Decimal(0):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CENTER_PAIR_PARITY_NO_EDGE,
                reason_detail=(
                    f"center_sell: Π(q*={q_star})={pi_star} ≤ 0 "
                    f"for market_slug={market_slug!r}; no deterministic parity arb."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Build §19.3 VectorEdgeDecision — 2 legs, same condition_id ---
        yes_best_ask = min(
            (lv.price for lv in leg.yes_levels), default=Decimal(0)
        )
        no_best_ask = min(
            (lv.price for lv in leg.no_levels), default=Decimal(0)
        )
        legs = (
            LegIntent(
                side="buy_yes",
                condition_id=leg.condition_id,
                quantity=q_star,
                price_limit=yes_best_ask,
            ),
            LegIntent(
                side="buy_no",
                condition_id=leg.condition_id,
                quantity=q_star,
                price_limit=no_best_ask,
            ),
        )

        # Payoff identity: binary YES+NO → one side settles to 1, the other to 0.
        # Together they pay exactly q* regardless of outcome.
        vector_payoff = q_star

        # --- proof_inputs_hash (§19.3): SHA-256 of legs + q_star + fee_rate ---
        _proof_blob = json.dumps(
            {
                "legs": [
                    {
                        "condition_id": li.condition_id,
                        "side": li.side,
                        "quantity": str(li.quantity),
                        "price_limit": str(li.price_limit),
                    }
                    for li in legs
                ],
                "q_star": str(q_star),
                "fee_rate": str(fee_rate),
            },
            sort_keys=True,
        )
        proof_inputs_hash = hashlib.sha256(_proof_blob.encode()).hexdigest()

        # --- Write ONE shadow decision_events row ---
        decision_time_iso = (
            decision_time.replace(tzinfo=timezone.utc).isoformat()
            if decision_time.tzinfo is None
            else decision_time.isoformat()
        )
        metrics = getattr(analysis, "metrics", None)
        anchor_source = (
            getattr(metrics, "polymarket_end_anchor_source", None)
            if metrics is not None
            else None
        )
        edge_normalized = float(pi_star / vector_payoff) if vector_payoff > Decimal(0) else None
        write_shadow_decision_event(
            context.natural_key,
            decision_time=decision_time_iso,
            side="buy_yes",  # composite signal; YES leg is the primary tag
            strategy_key=self.strategy_key,
            conn=conn,
            edge=edge_normalized,
            polymarket_end_anchor_source=anchor_source,
        )

        return VectorEdgeDecision(
            strategy_key=self.strategy_key,
            proof_type="center_pair_parity",
            basket_execution_id="",  # DEFERRED until §11.8 multi-leg execution ships
            legs=legs,
            q_star=q_star,
            vector_cost=vector_cost,
            vector_fee=vector_fee_val,
            vector_payoff=vector_payoff,
            vector_profit=pi_star,
            proof_inputs_hash=proof_inputs_hash,
        )
