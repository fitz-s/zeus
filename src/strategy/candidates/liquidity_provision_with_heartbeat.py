# Created: 2026-05-21
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §11
#                  + docs/reference/zeus_strategy_spec.md §15
"""LiquidityProvisionWithHeartbeat — adverse-selection maker model.

Theorem (§11/§15):
  EV_maker = Pr(F) · [p_fair − q_bid − AS]
  AS = E[p_after − p_before | F]   (from full-market CLOB public data)
  Maker fee = 0 (post-only guarantees maker role; phi(q, p, 0) = 0).
  Application condition:  p⁻_fair − q_bid − AS⁺ > 0

  Pr(F) decides VOLUME not SIGN. Sign is decided by the adverse-selection bound.

DATA-GATED: AS(q,τ) estimation from full-market CLOB public trade/book data is
  not yet wired. Until wired, the strategy emits LIQPROV_ADVERSE_SELECTION_UNWIRED
  for every evaluation. The theorem + data gate are implemented now; the data feed
  lands separately.

SELF-REFERENCE ANTI-BIAS: Zeus's own venue_command fill history (passive_maker_estimate.
  expected_fill_probability) is NOT used for sign decisions. The sign oracle is the
  exogenous CLOB-derived AS estimate. Presence of passive_maker_estimate does not
  unlock entry.

live_status: shadow (NEVER live without AS data wired).
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


class LiquidityProvisionWithHeartbeat(BaseStrategyCandidate):
    """Adverse-selection maker model (STRATEGY_TAXONOMY_DIRECTIVE §11).

    Entry condition:  p⁻_fair − q_bid − AS⁺ > 0
    where:
      p⁻_fair  = calibrated lower bound on fair probability (from analysis.market_clob_adverse_selection)
      q_bid    = maker quote bid price
      AS⁺      = upper bound on adverse selection E[p_after−p_before|F]

    Required input field on analysis: `market_clob_adverse_selection` with:
      - p_fair_lower_bound: Decimal
      - maker_bid: Decimal
      - adverse_selection_upper_bound: Decimal
      - source: str  (must NOT be 'zeus_self_history' — self-reference guard)

    DATA-GATED: market_clob_adverse_selection is not yet populated. All evaluations
    return LIQPROV_ADVERSE_SELECTION_UNWIRED until the CLOB data feed is wired.

    live_status: shadow. executable_alpha=True (metadata-only).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="liquidity_provision_with_heartbeat",
                family="liquidity_provision_with_heartbeat",
                description=(
                    "Adverse-selection maker model (§11/§15 reframe). "
                    "Sign = p⁻_fair − q_bid − AS⁺ > 0. "
                    "DATA-GATED: full-market CLOB AS estimator not yet wired."
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
        """Evaluate adverse-selection maker edge.

        Data-gate (current): market_clob_adverse_selection absent on analysis
          → LIQPROV_ADVERSE_SELECTION_UNWIRED (no_trade).

        Anti-self-reference: passive_maker_estimate (Zeus fill history) is
          ignored for sign decisions. Only CLOB-derived AS estimate is consulted.

        Entry path (when AS data is wired):
          edge = p⁻_fair − q_bid − AS⁺
          maker_fee = 0 (post-only; phi(q, p, 0) = 0)
          enter iff edge > 0

        No-trade path (when edge ≤ 0): reason = LIQPROV_ADVERSE_SELECTION_UNWIRED
          (reused for both "unwired" and "wired but not profitable" until
          a separate LIQPROV_AS_EDGE_NEGATIVE reason is added on promotion).

        Never returns None.
        """
        # DATA-GATE: check for CLOB-derived AS estimator on analysis.
        # This field is intentionally absent until the CLOB data feed is wired.
        as_estimate = getattr(context.analysis, "market_clob_adverse_selection", None)

        if as_estimate is None:
            # DATA-GATED: AS from full-market CLOB not wired.
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.LIQPROV_ADVERSE_SELECTION_UNWIRED,
                reason_detail=(
                    "liquidity_provision_with_heartbeat: adverse selection estimator "
                    "(market_clob_adverse_selection) not wired; full-market CLOB public "
                    "trade/book data feed required. DATA-GATED per §11. "
                    "self_reference_guard=active."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Extract AS estimate fields.
        p_fair_lower: Decimal = Decimal(str(as_estimate.p_fair_lower_bound))
        maker_bid: Decimal = Decimal(str(as_estimate.maker_bid))
        as_upper: Decimal = Decimal(str(as_estimate.adverse_selection_upper_bound))

        # Compute adverse-selection-adjusted edge.
        # Maker fee = 0 (post-only guarantees maker role; phi(q, p, fee_rate=0) = 0).
        edge: Decimal = p_fair_lower - maker_bid - as_upper

        if edge <= Decimal("0"):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.LIQPROV_ADVERSE_SELECTION_UNWIRED,
                reason_detail=(
                    f"liquidity_provision_with_heartbeat: p⁻_fair={p_fair_lower} "
                    f"− bid={maker_bid} − AS⁺={as_upper} = {edge} ≤ 0; "
                    "adverse selection consumes edge."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Edge positive — shadow enter.
        decision_time_iso = (
            decision_time.replace(tzinfo=timezone.utc).isoformat()
            if decision_time.tzinfo is None
            else decision_time.isoformat()
        )
        metrics = getattr(context.analysis, "metrics", None)
        write_shadow_decision_event(
            context.natural_key,
            decision_time=decision_time_iso,
            side="buy_yes",
            strategy_key=self.strategy_key,
            conn=conn,
            edge=float(edge),
            polymarket_end_anchor_source=getattr(
                metrics,
                "polymarket_end_anchor_source",
                None,
            ),
        )

        return CandidateDecision(
            outcome="enter",
            side="buy_yes",
            target_price=None,
            target_size_usd=None,
            edge=edge,
            p_posterior=None,
        )
