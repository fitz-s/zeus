# Created: 2026-04-27
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/05_PHASE_4_FDR_FAMILY_CANDIDATES.md
"""NegRiskBasket — shadow candidate strategy.

Edge source: family completeness of the negRisk YES token book vs theoretical.
When all YES tokens in a negRisk family are known, the sum of YES ask prices must
be >= 1.0 (otherwise an arbitrage exists by buying all YES sides). A basket arb
is available when the sum of best asks across the full token book is < 1.0.

Gate conditions (emit no_trade with reason=NEGRISK_FAMILY_INCOMPLETE):
  1. analysis.neg_risk_family_complete is absent or falsy: token book completeness
     metadata not yet wired — cannot assess basket arb. Fail-open.
  2. analysis.neg_risk_token_count is absent or < 2: family has fewer than 2
     tokens; basket arb requires at least 2 YES sides.
  3. analysis.neg_risk_yes_ask_sum is absent or None: sum of YES asks not
     computed; cannot assess the basket spread.
  4. neg_risk flag not set on snapshot: market is not a negRisk market.

Note: All completeness fields (neg_risk_family_complete, neg_risk_token_count,
neg_risk_yes_ask_sum) are not yet wired in MarketAnalysisVNext (as of Phase 4 T4,
2026-05-21). This candidate operates in permanent no_trade shadow mode until the
negRisk metadata audit in a future phase wires these fields.

live_status: shadow (NEVER live in Phase 4).

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
    write_candidate_no_trade_row,
)

# Placeholder shadow edge for decision_events row (no live sizing in Phase 4).
_SHADOW_EDGE: float = 0.02

# Basket arb threshold: sum of YES asks must be strictly below this to signal arb.
# In a negRisk family, sum of YES probabilities should equal 1.0.
# A sum < _BASKET_ARB_THRESHOLD indicates mis-pricing across the family.
_BASKET_ARB_THRESHOLD: Decimal = Decimal("0.97")

# Minimum token count for a meaningful basket.
_MIN_TOKEN_COUNT: int = 2


class NegRiskBasket(BaseStrategyCandidate):
    """Shadow candidate: negRisk family completeness basket arbitrage.

    Edge source: when the full YES token book for a negRisk family is available and
    the sum of YES ask prices is below the basket arbitrage threshold, a shadow
    buy_yes entry is logged to decision_events.

    Token book completeness is a required precondition: if any YES side of the family
    is missing from the token book, the basket sum is not comparable to the theoretical
    1.0 total, and a no_trade with reason=NEGRISK_FAMILY_INCOMPLETE is emitted.

    live_status: shadow. CandidateMetadata.executable_alpha=True (metadata-only;
    NOT a registry YAML field — passing it to registry raises RegistrySchemaError).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="neg_risk_basket",
                family="neg_risk_basket",
                description=(
                    "Shadow candidate: negRisk family completeness basket arbitrage. "
                    "Edge source: sum of YES ask prices across full negRisk token book "
                    "vs theoretical 1.0 total. Requires complete family token book. "
                    "Shadow research only."
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
    ) -> CandidateDecision:
        """Evaluate negRisk basket completeness arb against the market context.

        No-trade path (reason=NEGRISK_FAMILY_INCOMPLETE):
          - analysis.neg_risk_family_complete is absent or falsy.
          - analysis.neg_risk_token_count is absent or < _MIN_TOKEN_COUNT.
          - analysis.neg_risk_yes_ask_sum is absent or None.
          - Snapshot is not a negRisk market.
          - YES ask sum >= _BASKET_ARB_THRESHOLD (no basket arb available).

        Enter path:
          - neg_risk flag confirmed on snapshot.
          - neg_risk_family_complete truthy.
          - neg_risk_token_count >= _MIN_TOKEN_COUNT.
          - neg_risk_yes_ask_sum present and < _BASKET_ARB_THRESHOLD.
          -> shadow enter logged to decision_events.

        Never returns None. Never raises; all guard failures become no_trade.
        """
        analysis = context.analysis
        market_slug, temperature_metric, target_date_str, observation_time, _ = context.natural_key

        # --- Guard 1: negRisk flag on snapshot ---
        snapshot = getattr(analysis, "_snapshot", None)
        is_neg_risk: bool = bool(getattr(snapshot, "neg_risk", False))
        if not is_neg_risk:
            # Fall back to checking analysis directly for test scenarios.
            is_neg_risk = bool(getattr(analysis, "neg_risk", False))
        if not is_neg_risk:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_FAMILY_INCOMPLETE,
                reason_detail=(
                    f"neg_risk_basket: market_slug={market_slug!r} is not a negRisk market "
                    "(neg_risk=False on snapshot); basket arb not applicable."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Guard 2: family completeness metadata ---
        family_complete: Optional[bool] = getattr(analysis, "neg_risk_family_complete", None)
        if not family_complete:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_FAMILY_INCOMPLETE,
                reason_detail=(
                    f"neg_risk_basket: neg_risk_family_complete absent or False for "
                    f"market_slug={market_slug!r}; token book completeness not confirmed. "
                    "Fail-open (metadata not yet wired in MarketAnalysisVNext)."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Guard 3: token count ---
        token_count: Optional[int] = getattr(analysis, "neg_risk_token_count", None)
        if token_count is None or int(token_count) < _MIN_TOKEN_COUNT:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_FAMILY_INCOMPLETE,
                reason_detail=(
                    f"neg_risk_basket: neg_risk_token_count={token_count!r} < "
                    f"minimum {_MIN_TOKEN_COUNT} for market_slug={market_slug!r}; "
                    "cannot compute basket arb on a single token."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Guard 4: YES ask sum ---
        yes_ask_sum_raw = getattr(analysis, "neg_risk_yes_ask_sum", None)
        if yes_ask_sum_raw is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_FAMILY_INCOMPLETE,
                reason_detail=(
                    f"neg_risk_basket: neg_risk_yes_ask_sum absent for "
                    f"market_slug={market_slug!r}; cannot assess basket spread."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        try:
            yes_ask_sum = Decimal(str(yes_ask_sum_raw))
        except Exception:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_FAMILY_INCOMPLETE,
                reason_detail=(
                    f"neg_risk_basket: neg_risk_yes_ask_sum={yes_ask_sum_raw!r} cannot "
                    f"be parsed as Decimal for market_slug={market_slug!r}."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Guard 5: basket arb threshold ---
        if yes_ask_sum >= _BASKET_ARB_THRESHOLD:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.NEGRISK_FAMILY_INCOMPLETE,
                reason_detail=(
                    f"neg_risk_basket: YES ask sum {yes_ask_sum} >= threshold "
                    f"{_BASKET_ARB_THRESHOLD} for market_slug={market_slug!r}; "
                    "no basket arb available."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Shadow enter: write decision_events row ---
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
            side="buy_yes",
            strategy_key=self.strategy_key,
            conn=conn,
            edge=_SHADOW_EDGE,
            polymarket_end_anchor_source=anchor_source,
        )

        return CandidateDecision(
            outcome="enter",
            side="buy_yes",
            target_price=None,
            target_size_usd=None,
            edge=None,
            p_posterior=None,
        )
