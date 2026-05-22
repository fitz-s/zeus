# Created: 2026-04-27
# Last reused or audited: 2026-05-22
# Authority basis: docs/reference/zeus_strategy_spec.md §19.3
#                  + docs/reference/zeus_math_spec.md §11.4-11.9
"""NegRiskBasket — exact-arbitrage deterministic pipeline PILOT.

Math spec authority: zeus_math_spec.md §11.4-11.9.
Decision schema authority: zeus_strategy_spec.md §19.3 (VectorEdgeDecision).

Exact arbitrage condition (§11.6):
    EXECUTE ⟺ max(Π_Y(q*), Π_N(q*)) > 0

YES basket profit:
    Π_Y(q) = q − Σ_i [A_i(q) + F_i(q)]
    A_i(q) = Σ_ℓ p_{i,ℓ}·Δq_{i,ℓ}                   sweep notional (§11.5)
    F_i(q) = Σ_ℓ phi(Δq_{i,ℓ}, p_{i,ℓ}, r)           per-level taker fee (§11.5)
    r = venue_fee_rate() from src.strategy.fees — never hardcoded

NO basket profit:
    Π_N(q) = (K-1)·q − Σ_i [B_i(q) + G_i(q)]

q* sizing (§11.7):
    q* = argmax_{q∈D} Π(q)
    D = sorted union of cumulative depth breakpoints across all legs,
        bounded by min(total_depth_i for all legs).
    Π piecewise-linear ⟹ optimum at a breakpoint.

Vector-fill accounting (§11.8):
    q_complete = min_{i=1..K} f_i  (shallowest leg total depth)
    Partial fill (q < q_complete) is NOT strategy alpha.

live_status: shadow (kelly 0.0). NO live multi-leg order placement.
basket_execution_id execution: DEFERRED (out of scope per §11.8 brief).

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
    FamilyOrderBookSnapshot,
    LegBook,
    LegIntent,
    PriceLevel,
    VectorEdgeDecision,
    write_candidate_no_trade_row,
)

# Minimum number of outcomes for a meaningful neg-risk basket.
_MIN_LEGS: int = 2


def _sweep_yes(
    leg: LegBook, q: Decimal, fee_rate: Decimal
) -> tuple[Decimal, Decimal]:
    """Compute A_i(q) and F_i(q) for the YES side of one leg.

    §11.5: A_i(q) = Σ_ℓ p_{i,ℓ}·Δq_{i,ℓ}; F_i(q) = Σ_ℓ phi(Δq_{i,ℓ}, p_{i,ℓ}, r).
    Levels consumed in ascending price order (best ask first).
    Returns (sweep_cost, fee).
    """
    remaining = q
    cost = Decimal(0)
    fee_total = Decimal(0)
    for lv in sorted(leg.yes_levels, key=lambda x: x.price):
        fill = min(remaining, lv.quantity)
        if fill <= 0:
            continue
        # §11.5 level-by-level sweep: notional + phi per level
        cost += lv.price * fill
        fee_total += phi(fill, lv.price, fee_rate)
        remaining -= fill
        if remaining <= 0:
            break
    return cost, fee_total


def _sweep_no(
    leg: LegBook, q: Decimal, fee_rate: Decimal
) -> tuple[Decimal, Decimal]:
    """Compute B_i(q) and G_i(q) for the NO side of one leg.

    §11.5: same formula as _sweep_yes applied to NO levels.
    Returns (sweep_cost, fee).
    """
    remaining = q
    cost = Decimal(0)
    fee_total = Decimal(0)
    for lv in sorted(leg.no_levels, key=lambda x: x.price):
        fill = min(remaining, lv.quantity)
        if fill <= 0:
            continue
        # §11.5 level-by-level sweep: notional + phi per level
        cost += lv.price * fill
        fee_total += phi(fill, lv.price, fee_rate)
        remaining -= fill
        if remaining <= 0:
            break
    return cost, fee_total


def _total_depth(levels: tuple[PriceLevel, ...]) -> Decimal:
    """Sum of quantities across all price levels."""
    return sum((lv.quantity for lv in levels), Decimal(0))


def _yes_breakpoints(family: FamilyOrderBookSnapshot) -> list[Decimal]:
    """Cumulative-depth breakpoints for YES side, bounded by shallowest leg.

    §11.7: D = sorted union of cumulative depth values across all legs,
    bounded by min(total_depth_i). Π piecewise-linear ⟹ q* at a breakpoint.
    """
    min_depth = min(
        (_total_depth(leg.yes_levels) for leg in family.legs),
        default=Decimal(0),
    )
    pts: set[Decimal] = set()
    for leg in family.legs:
        cumulative = Decimal(0)
        for lv in sorted(leg.yes_levels, key=lambda x: x.price):
            cumulative += lv.quantity
            if cumulative <= min_depth:
                pts.add(cumulative)
    if min_depth > 0:
        pts.add(min_depth)
    return sorted(pts)


def _no_breakpoints(family: FamilyOrderBookSnapshot) -> list[Decimal]:
    """Cumulative-depth breakpoints for NO side, bounded by shallowest leg."""
    min_depth = min(
        (_total_depth(leg.no_levels) for leg in family.legs),
        default=Decimal(0),
    )
    pts: set[Decimal] = set()
    for leg in family.legs:
        cumulative = Decimal(0)
        for lv in sorted(leg.no_levels, key=lambda x: x.price):
            cumulative += lv.quantity
            if cumulative <= min_depth:
                pts.add(cumulative)
    if min_depth > 0:
        pts.add(min_depth)
    return sorted(pts)


def _pi_yes(
    family: FamilyOrderBookSnapshot, q: Decimal, fee_rate: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """Π_Y(q) = q − Σ_i [A_i(q) + F_i(q)].

    Returns (total_cost, total_fee, profit).
    """
    total_cost = Decimal(0)
    total_fee = Decimal(0)
    for leg in family.legs:
        a, f = _sweep_yes(leg, q, fee_rate)
        total_cost += a
        total_fee += f
    profit = q - total_cost - total_fee
    return total_cost, total_fee, profit


def _pi_no(
    family: FamilyOrderBookSnapshot, q: Decimal, fee_rate: Decimal
) -> tuple[Decimal, Decimal, Decimal]:
    """Π_N(q) = (K-1)·q − Σ_i [B_i(q) + G_i(q)].

    Returns (total_cost, total_fee, profit).
    """
    K = family.K
    total_cost = Decimal(0)
    total_fee = Decimal(0)
    for leg in family.legs:
        b, g = _sweep_no(leg, q, fee_rate)
        total_cost += b
        total_fee += g
    profit = Decimal(K - 1) * q - total_cost - total_fee
    return total_cost, total_fee, profit


def _opt_yes(
    family: FamilyOrderBookSnapshot, fee_rate: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return (q*, cost, fee, Π_Y(q*)) for YES basket (§11.7 breakpoint search)."""
    pts = _yes_breakpoints(family)
    if not pts:
        return Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    best = (Decimal(0), Decimal(0), Decimal(0), Decimal(0))  # (q, cost, fee, profit)
    for q in pts:
        cost, fee, pi = _pi_yes(family, q, fee_rate)
        if pi > best[3]:
            best = (q, cost, fee, pi)
    return best


def _opt_no(
    family: FamilyOrderBookSnapshot, fee_rate: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return (q*, cost, fee, Π_N(q*)) for NO basket (§11.7 breakpoint search)."""
    pts = _no_breakpoints(family)
    if not pts:
        return Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    best = (Decimal(0), Decimal(0), Decimal(0), Decimal(0))
    for q in pts:
        cost, fee, pi = _pi_no(family, q, fee_rate)
        if pi > best[3]:
            best = (q, cost, fee, pi)
    return best


def _build_yes_legs(
    family: FamilyOrderBookSnapshot, q_star: Decimal
) -> tuple[LegIntent, ...]:
    """Build YES LegIntents at q_star (best YES ask for each leg)."""
    intents = []
    for leg in family.legs:
        best_ask = min((lv.price for lv in leg.yes_levels), default=Decimal(0))
        intents.append(
            LegIntent(
                side="buy_yes",
                condition_id=leg.condition_id,
                quantity=q_star,
                price_limit=best_ask,
            )
        )
    return tuple(intents)


def _build_no_legs(
    family: FamilyOrderBookSnapshot, q_star: Decimal
) -> tuple[LegIntent, ...]:
    """Build NO LegIntents at q_star (best NO ask for each leg)."""
    intents = []
    for leg in family.legs:
        best_ask = min((lv.price for lv in leg.no_levels), default=Decimal(0))
        intents.append(
            LegIntent(
                side="buy_no",
                condition_id=leg.condition_id,
                quantity=q_star,
                price_limit=best_ask,
            )
        )
    return tuple(intents)


class NegRiskBasket(BaseStrategyCandidate):
    """Exact-arbitrage neg-risk basket — deterministic pipeline PILOT.

    Execute iff max(Π_Y(q*), Π_N(q*)) > 0.  (§11.6)
    Decision schema: §19.3 VectorEdgeDecision.

    Emits VectorEdgeDecision on enter. Writes ONE shadow decision_events row.
    Multi-leg DB persistence and basket_execution_id are DEFERRED (out of scope).

    live_status: shadow. CandidateMetadata.executable_alpha=True (metadata-only;
    NOT a registry YAML field — passing it to registry raises RegistrySchemaError).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="neg_risk_basket",
                family="neg_risk_basket",
                description=(
                    "Exact-arbitrage neg-risk basket — deterministic pipeline PILOT. "
                    "Execute iff max(Π_Y(q*), Π_N(q*)) > 0 per zeus_math_spec §11.6. "
                    "Decision schema: §19.3 VectorEdgeDecision. "
                    "Shadow research only; kelly=0."
                ),
                executable_alpha=True,
            )
        )

    def evaluate(
        self,
        *,
        context: CandidateContext,
        conn: sqlite3.Connection,
        decision_time: datetime,
    ) -> CandidateDecision | VectorEdgeDecision:
        """Evaluate neg-risk basket exact arb.

        Guard path (reason=NEGRISK_FAMILY_INCOMPLETE):
          - No FamilyOrderBookSnapshot on analysis.
          - Family has fewer than _MIN_LEGS legs.

        No-trade path (reason=NEGRISK_NO_PROFITABLE_BASKET):
          - max(Π_Y(q*), Π_N(q*)) ≤ 0 (data present, no profitable basket).

        Enter path:
          - max(Π_Y(q*), Π_N(q*)) > 0.
          - Emits §19.3 VectorEdgeDecision with strategy_key, proof_type,
            basket_execution_id="", legs, q_star, vector_cost, vector_fee,
            vector_payoff, vector_profit.
          - Writes ONE shadow decision_events row (multi-leg DB persistence deferred).

        Never returns None. Never raises; all guard failures become no_trade.
        """
        analysis = context.analysis
        market_slug = context.natural_key[0]

        # Resolve fee rate once per evaluate() (§11.5: never hardcode r)
        fee_rate = venue_fee_rate()

        # --- Resolve FamilyOrderBookSnapshot ---
        family: Optional[FamilyOrderBookSnapshot] = getattr(
            analysis, "family_book_snapshot", None
        )
        if family is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_FAMILY_INCOMPLETE,
                reason_detail=(
                    f"neg_risk_basket: FamilyOrderBookSnapshot absent on analysis "
                    f"for market_slug={market_slug!r}; exact-arb requires per-leg book."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Guard: minimum legs ---
        if family.K < _MIN_LEGS:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_FAMILY_INCOMPLETE,
                reason_detail=(
                    f"neg_risk_basket: family has {family.K} legs < minimum {_MIN_LEGS} "
                    f"for market_slug={market_slug!r}."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Compute q* and Π* for both baskets (§11.7 breakpoint search) ---
        try:
            q_yes, cost_yes, fee_yes, pi_yes = _opt_yes(family, fee_rate)
            q_no, cost_no, fee_no, pi_no = _opt_no(family, fee_rate)
        except Exception as exc:  # phi/sweep can raise ValueError/TypeError on bad level data
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_FAMILY_INCOMPLETE,
                reason_detail=(
                    f"neg_risk_basket: sweep/fee computation failed for "
                    f"market_slug={market_slug!r}: {exc!r}"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        best_pi = max(pi_yes, pi_no)

        if best_pi <= Decimal(0):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_NO_PROFITABLE_BASKET,
                reason_detail=(
                    f"neg_risk_basket: max(Π_Y({q_yes})={pi_yes}, Π_N({q_no})={pi_no}) "
                    f"= {best_pi} ≤ 0 for market_slug={market_slug!r}; no exact arb."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Build §19.3 VectorEdgeDecision ---
        if pi_yes >= pi_no:
            q_star = q_yes
            legs = _build_yes_legs(family, q_star)
            basket_side = "buy_yes"
            vector_cost = cost_yes
            vector_fee_val = fee_yes
            payoff = q_star  # YES basket: Σ Y_i(T) = 1 → payoff = q*
            profit = pi_yes
        else:
            q_star = q_no
            legs = _build_no_legs(family, q_star)
            basket_side = "buy_no"
            vector_cost = cost_no
            vector_fee_val = fee_no
            payoff = Decimal(family.K - 1) * q_star  # NO basket: (K-1) pay $1 each
            profit = pi_no

        # --- Compute proof_inputs_hash (§19.3): SHA-256 of legs + q_star + fee_rate ---
        _proof_blob = json.dumps(
            {
                "legs": [
                    {
                        "condition_id": lg.condition_id,
                        "side": lg.side,
                        "quantity": str(lg.quantity),
                        "price_limit": str(lg.price_limit),
                    }
                    for lg in legs
                ],
                "q_star": str(q_star),
                "fee_rate": str(fee_rate),
            },
            sort_keys=True,
        )
        proof_inputs_hash = hashlib.sha256(_proof_blob.encode()).hexdigest()

        # --- Write ONE shadow decision_events row (multi-leg DB persistence deferred) ---
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
        # edge: normalized per-share profit signal (vector_profit / vector_payoff);
        # vector_profit alone is multi-leg notional, not comparable to single-leg p − q signals.
        edge_normalized = float(profit / payoff) if payoff > Decimal(0) else None
        write_shadow_decision_event(
            context.natural_key,
            decision_time=decision_time_iso,
            side=basket_side,
            strategy_key=self.strategy_key,
            conn=conn,
            edge=edge_normalized,
            polymarket_end_anchor_source=anchor_source,
        )

        return VectorEdgeDecision(
            strategy_key=self.strategy_key,
            proof_type="complete_family_basket",
            basket_execution_id="",  # DEFERRED until §11.8 multi-leg execution ships
            legs=legs,
            q_star=q_star,
            vector_cost=vector_cost,
            vector_fee=vector_fee_val,
            vector_payoff=payoff,
            vector_profit=profit,
            proof_inputs_hash=proof_inputs_hash,
        )
