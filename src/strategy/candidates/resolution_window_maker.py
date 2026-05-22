# Created: 2026-05-21
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §2
#                  + docs/reference/zeus_strategy_spec.md §19.2
"""ResolutionWindowMaker — shadow candidate strategy.

Edge source: source-known-but-venue-unresolved deterministic payoff. When the
Phase-7 typed SettlementOutcome is SOURCE_PUBLISHED_VENUE_UNRESOLVED (official
source published; venue not yet settled), the market price should converge to
the winning-side payoff of $1. During this window a taker entry on the winning
token (or NO of a confirmed-losing token) captures the resolution discount.

Payoff theorem (STRATEGY_TAXONOMY_DIRECTIVE.md §2):
  Let i* be the winning token; a_{i*} the best YES ask; b_j the best NO ask
  for a losing token j:
    Π_yes = 1 − a_{i*} − phi(a_{i*})   [positive iff a_{i*} + phi < 1]
    Π_no  = 1 − b_j    − phi(b_j)      [positive iff b_j  + phi < 1]
  Trade the max positive leg.

Data-gating: all price/token inputs are read from analysis attributes injected
by the caller.  Until the SettlementOutcome + ask-price wiring is live, every
evaluate() call will data-gate with RESOLUTION_TYPED_OUTCOME_UNAVAILABLE.

live_status: shadow (NEVER live in Phase 4).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Union

from src.contracts.no_trade_reason import NoTradeReason
from src.contracts.settlement_outcome import SettlementOutcome
from src.state.decision_events import write_shadow_decision_event
from src.strategy.fees import phi, venue_fee_rate

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    DeterministicEdgeDecision,
    write_candidate_no_trade_row,
)

_PROOF_TYPE = "source_known_venue_unresolved"


def _compute_proof_inputs_hash(
    strategy_key: str,
    proof_type: str,
    token_id: str,
    side: str,
    executable_price: Decimal,
    settlement_outcome_value: int,
    fee_rate: Decimal,
) -> str:
    """SHA-256 hex of canonical proof inputs tuple.

    Stable serialisation: each field str()-converted, joined by '|'.
    """
    raw = "|".join([
        strategy_key,
        proof_type,
        token_id,
        side,
        str(executable_price),
        str(settlement_outcome_value),
        str(fee_rate),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


class ResolutionWindowMaker(BaseStrategyCandidate):
    """Shadow candidate: exploits source-known / venue-unresolved discount window.

    Edge source: typed SettlementOutcome == SOURCE_PUBLISHED_VENUE_UNRESOLVED
    (official source has published; Polymarket/UMA has not yet settled). Payoff is
    deterministic once the winning direction is known from the source.

    live_status: shadow. CandidateMetadata.executable_alpha=True (metadata-only;
    NOT a registry YAML field — passing it to registry raises RegistrySchemaError).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="resolution_window_maker",
                family="resolution_window_maker",
                description=(
                    "Shadow candidate: exploits source-known but venue-unresolved "
                    "discount window. Payoff theorem: Π = 1 − ask − phi(ask). "
                    "Gated on typed SettlementOutcome SOURCE_PUBLISHED_VENUE_UNRESOLVED."
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
    ) -> Union[CandidateDecision, DeterministicEdgeDecision]:
        """Evaluate resolution-window deterministic edge.

        Gate sequence:
          1. analysis.settlement_outcome must be typed SettlementOutcome.
             Absent / wrong type → no_trade(RESOLUTION_TYPED_OUTCOME_UNAVAILABLE).
          2. Value must equal SOURCE_PUBLISHED_VENUE_UNRESOLVED.
             Any other outcome → no_trade(RESOLUTION_DISPUTED).
          3. analysis.yes_ask / no_ask / yes_token_id / no_token_id must be present.
             Absent → no_trade(RESOLUTION_TYPED_OUTCOME_UNAVAILABLE) [data-gated].
          4. Compute Π_yes = 1 − yes_ask − phi(1, yes_ask, r) and
                       Π_no  = 1 − no_ask  − phi(1, no_ask,  r).
             Max positive leg → DeterministicEdgeDecision (shadow, logged).
             Neither positive → no_trade(RESOLUTION_DISPUTED).

        Never returns None.
        """
        analysis = context.analysis

        # ── Gate 1: typed SettlementOutcome present ───────────────────────────
        settlement_outcome: Optional[object] = getattr(analysis, "settlement_outcome", None)

        if settlement_outcome is None or not isinstance(settlement_outcome, SettlementOutcome):
            decision: CandidateDecision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.RESOLUTION_TYPED_OUTCOME_UNAVAILABLE,
                reason_detail=(
                    "resolution_window_maker: typed SettlementOutcome not present on "
                    "analysis context (data-gated); wiring pending."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Gate 2: must be SOURCE_PUBLISHED_VENUE_UNRESOLVED ────────────────
        if settlement_outcome is not SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.RESOLUTION_DISPUTED,
                reason_detail=(
                    f"resolution_window_maker: settlement_outcome={settlement_outcome.name!r} "
                    "is not SOURCE_PUBLISHED_VENUE_UNRESOLVED; no discount window."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Gate 3: ask prices + token IDs present ───────────────────────────
        yes_ask_raw: Optional[object] = getattr(analysis, "yes_ask", None)
        no_ask_raw: Optional[object] = getattr(analysis, "no_ask", None)
        yes_token_id: Optional[str] = getattr(analysis, "yes_token_id", None)
        no_token_id: Optional[str] = getattr(analysis, "no_token_id", None)

        if any(v is None for v in (yes_ask_raw, no_ask_raw, yes_token_id, no_token_id)):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.RESOLUTION_TYPED_OUTCOME_UNAVAILABLE,
                reason_detail=(
                    "resolution_window_maker: yes_ask / no_ask / token IDs absent on "
                    "analysis context (data-gated); wiring pending."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        try:
            yes_ask = Decimal(str(yes_ask_raw))
            no_ask = Decimal(str(no_ask_raw))
        except Exception as exc:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.RESOLUTION_TYPED_OUTCOME_UNAVAILABLE,
                reason_detail=(
                    f"resolution_window_maker: cannot parse yes_ask={yes_ask_raw!r} or "
                    f"no_ask={no_ask_raw!r} as Decimal: {exc}"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Payoff theorem ────────────────────────────────────────────────────
        fee_rate = venue_fee_rate()

        # Π_yes = 1 − a − phi(1, a, r); shares=1 (single-token normalised)
        fee_yes = phi(Decimal("1"), yes_ask, fee_rate)
        profit_yes = Decimal("1") - yes_ask - fee_yes

        # Π_no = 1 − b − phi(1, b, r)
        fee_no = phi(Decimal("1"), no_ask, fee_rate)
        profit_no = Decimal("1") - no_ask - fee_no

        # Pick max positive leg
        if profit_yes >= profit_no and profit_yes > Decimal("0"):
            best_side: str = "buy_yes"
            best_token_id: str = str(yes_token_id)
            best_ask: Decimal = yes_ask
            best_fee: Decimal = fee_yes
            best_profit: Decimal = profit_yes
        elif profit_no > Decimal("0"):
            best_side = "buy_no"
            best_token_id = str(no_token_id)
            best_ask = no_ask
            best_fee = fee_no
            best_profit = profit_no
        else:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.RESOLUTION_DISPUTED,
                reason_detail=(
                    f"resolution_window_maker: neither leg profitable after fee. "
                    f"Π_yes={profit_yes}, Π_no={profit_no} (fee_rate={fee_rate})."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        proof_hash = _compute_proof_inputs_hash(
            strategy_key=self.strategy_key,
            proof_type=_PROOF_TYPE,
            token_id=best_token_id,
            side=best_side,
            executable_price=best_ask,
            settlement_outcome_value=int(SettlementOutcome.SOURCE_PUBLISHED_VENUE_UNRESOLVED),
            fee_rate=fee_rate,
        )

        # ── Shadow provenance row ─────────────────────────────────────────────
        decision_time_iso = (
            decision_time.replace(tzinfo=timezone.utc).isoformat()
            if decision_time.tzinfo is None
            else decision_time.isoformat()
        )
        metrics = getattr(analysis, "metrics", None)
        write_shadow_decision_event(
            context.natural_key,
            decision_time=decision_time_iso,
            side=best_side,
            strategy_key=self.strategy_key,
            conn=conn,
            edge=float(best_profit),
            target_price=float(best_ask),
            polymarket_end_anchor_source=getattr(
                metrics, "polymarket_end_anchor_source", None
            ),
        )

        return DeterministicEdgeDecision(
            strategy_key=self.strategy_key,
            proof_type=_PROOF_TYPE,
            side=best_side,  # type: ignore[arg-type]
            token_id=best_token_id,
            executable_price=best_ask,
            fee=best_fee,
            deterministic_payoff=Decimal("1"),
            deterministic_profit=best_profit,
            proof_inputs_hash=proof_hash,
        )
