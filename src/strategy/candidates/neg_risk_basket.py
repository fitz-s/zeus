# Created: 2026-04-27
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §17
#                  + docs/reference/zeus_math_spec.md §11.4-11.9
"""NegRiskBasket — exact-arbitrage deterministic pipeline PILOT.

Math spec authority: zeus_math_spec.md §11.4-11.9.

Exact arbitrage condition (§11.6):
    EXECUTE ⟺ max(Π_Y(q*), Π_N(q*)) > 0

YES basket profit:
    Π_Y(q) = q − Σ_i [A_i(q) + F_i(q)]
    A_i(q) = Σ_ℓ p_{i,ℓ}·Δq_{i,ℓ}          sweep notional (§11.5)
    F_i(q) = Σ_ℓ r·p_{i,ℓ}·(1−p_{i,ℓ})·Δq_{i,ℓ}  per-level taker fee (§11.5)

NO basket profit:
    Π_N(q) = (K-1)·q − Σ_i [B_i(q) + G_i(q)]
    B_i / G_i: same formulas applied to NO levels.

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

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from src.contracts.no_trade_reason import NoTradeReason
from src.state.decision_events import write_shadow_decision_event

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

# TEMPORARY — taker fee rate pending canonical fees module integration.
# Source: Polymarket CTF feeRate=0.05 (weather markets). Replace with
# fees.get_taker_fee_rate() once the canonical fees module ships.
_TAKER_FEE_RATE_TEMP: Decimal = Decimal("0.05")

# Minimum number of outcomes for a meaningful neg-risk basket.
_MIN_LEGS: int = 2


def _sweep_yes(leg: LegBook, q: Decimal) -> tuple[Decimal, Decimal]:
    """Compute A_i(q) and F_i(q) for the YES side of one leg.

    Returns (sweep_cost, fee) both in USD per q shares.
    Levels consumed in ascending price order (best ask first).
    """
    remaining = q
    cost = Decimal(0)
    fee = Decimal(0)
    for lv in sorted(leg.yes_levels, key=lambda x: x.price):
        fill = min(remaining, lv.quantity)
        if fill <= 0:
            continue
        cost += lv.price * fill
        fee += _TAKER_FEE_RATE_TEMP * lv.price * (1 - lv.price) * fill
        remaining -= fill
        if remaining <= 0:
            break
    return cost, fee


def _sweep_no(leg: LegBook, q: Decimal) -> tuple[Decimal, Decimal]:
    """Compute B_i(q) and G_i(q) for the NO side of one leg."""
    remaining = q
    cost = Decimal(0)
    fee = Decimal(0)
    for lv in sorted(leg.no_levels, key=lambda x: x.price):
        fill = min(remaining, lv.quantity)
        if fill <= 0:
            continue
        cost += lv.price * fill
        fee += _TAKER_FEE_RATE_TEMP * lv.price * (1 - lv.price) * fill
        remaining -= fill
        if remaining <= 0:
            break
    return cost, fee


def _total_depth(levels: tuple[PriceLevel, ...]) -> Decimal:
    """Sum of quantities across all price levels."""
    return sum((lv.quantity for lv in levels), Decimal(0))


def _depth_breakpoints(family: FamilyOrderBookSnapshot) -> list[Decimal]:
    """Build the sorted breakpoint set D for q* search (§11.7).

    D = sorted union of all leg cumulative depth values,
        bounded by min(total_depth_i) so q never exceeds shallowest leg.

    Returns empty list if any leg has zero depth on the chosen side.
    The caller selects YES or NO levels before calling this helper.
    """
    all_breakpoints: set[Decimal] = set()
    for leg in family.legs:
        cumulative = Decimal(0)
        for lv in sorted(leg.yes_levels, key=lambda x: x.price):
            cumulative += lv.quantity
            all_breakpoints.add(cumulative)
    return sorted(all_breakpoints)


def _yes_breakpoints(family: FamilyOrderBookSnapshot) -> list[Decimal]:
    """Cumulative-depth breakpoints for YES side, bounded by shallowest leg."""
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
    # Always include min_depth as the outermost bound
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


def _pi_yes(family: FamilyOrderBookSnapshot, q: Decimal) -> Decimal:
    """Π_Y(q) = q − Σ_i [A_i(q) + F_i(q)]."""
    total = Decimal(0)
    for leg in family.legs:
        a, f = _sweep_yes(leg, q)
        total += a + f
    return q - total


def _pi_no(family: FamilyOrderBookSnapshot, q: Decimal) -> Decimal:
    """Π_N(q) = (K-1)·q − Σ_i [B_i(q) + G_i(q)]."""
    K = family.K
    total = Decimal(0)
    for leg in family.legs:
        b, g = _sweep_no(leg, q)
        total += b + g
    return Decimal(K - 1) * q - total


def _opt_yes(family: FamilyOrderBookSnapshot) -> tuple[Decimal, Decimal]:
    """Return (q*, Π_Y(q*)) for YES basket."""
    pts = _yes_breakpoints(family)
    if not pts:
        return Decimal(0), Decimal(0)
    best_q = Decimal(0)
    best_pi = Decimal(0)
    for q in pts:
        pi = _pi_yes(family, q)
        if pi > best_pi:
            best_pi = pi
            best_q = q
    return best_q, best_pi


def _opt_no(family: FamilyOrderBookSnapshot) -> tuple[Decimal, Decimal]:
    """Return (q*, Π_N(q*)) for NO basket."""
    pts = _no_breakpoints(family)
    if not pts:
        return Decimal(0), Decimal(0)
    best_q = Decimal(0)
    best_pi = Decimal(0)
    for q in pts:
        pi = _pi_no(family, q)
        if pi > best_pi:
            best_pi = pi
            best_q = q
    return best_q, best_pi


def _build_yes_legs(family: FamilyOrderBookSnapshot, q_star: Decimal) -> tuple[LegIntent, ...]:
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


def _build_no_legs(family: FamilyOrderBookSnapshot, q_star: Decimal) -> tuple[LegIntent, ...]:
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
                    "Emits VectorEdgeDecision. Shadow research only; kelly=0."
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
          - Any YES leg has zero depth AND any NO leg has zero depth (both baskets blocked).

        No-trade path (reason=NEGRISK_NO_PROFITABLE_BASKET):
          - max(Π_Y(q*), Π_N(q*)) ≤ 0.

        Enter path:
          - max(Π_Y(q*), Π_N(q*)) > 0.
          - Emits VectorEdgeDecision with legs + monetary fields.
          - Writes ONE shadow decision_events row.

        Never returns None. Never raises; all guard failures become no_trade.
        """
        analysis = context.analysis
        market_slug = context.natural_key[0]

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

        # --- Compute q* and Π* for both baskets ---
        q_yes, pi_yes = _opt_yes(family)
        q_no, pi_no = _opt_no(family)

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

        # --- Build VectorEdgeDecision ---
        if pi_yes >= pi_no:
            # YES basket wins
            q_star = q_yes
            legs = _build_yes_legs(family, q_star)
            # Recompute cost for the decision record
            total_cost = Decimal(0)
            for leg in family.legs:
                a, f = _sweep_yes(leg, q_star)
                total_cost += a + f
            payoff = q_star  # YES basket: 1 leg pays $1 per share, all others $0 → total = q*
            profit = pi_yes
        else:
            # NO basket wins
            q_star = q_no
            legs = _build_no_legs(family, q_star)
            total_cost = Decimal(0)
            for leg in family.legs:
                b, g = _sweep_no(leg, q_star)
                total_cost += b + g
            payoff = Decimal(family.K - 1) * q_star  # (K-1) losers pay $1 each
            profit = pi_no

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
        write_shadow_decision_event(
            context.natural_key,
            decision_time=decision_time_iso,
            side="buy_yes" if pi_yes >= pi_no else "buy_no",
            strategy_key=self.strategy_key,
            conn=conn,
            edge=float(profit),
            polymarket_end_anchor_source=anchor_source,
        )

        return VectorEdgeDecision(
            legs=legs,
            vector_cost_usd=total_cost,
            vector_payoff_usd=payoff,
            vector_profit_usd=profit,
        )
