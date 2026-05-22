# Created: 2026-05-21
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §3
#                  + docs/reference/zeus_strategy_spec.md §13
"""StaleQuoteDetector — FOK information-delay arbitrage (shadow candidate).

Theorem (§3 / §13.2): given canonical InfoEvent E at t_E updating fair probability
from p_0 to p_1, if the book hash is still unresponsive (stale resting ask a_0),
per-share filled EV is:

    EV_filled = p_1 - a_0 - phi(1, a_0, fee_rate)

With FOK no-fill payoff = 0:

    E[R] = Pr(F) * EV_filled

If EV_filled > 0 and Pr(F) > 0 → E[R] > 0. Fill probability scales expected
opportunity rate but does NOT change the sign of edge.

DATA-GATED: the info-event feed (info_event_observed, p_after_lower_bound) and
executable-quote capture (stale_quote_price) are unwired in MarketAnalysisVNext.
Where any input is missing, emit no_trade — the theorem and code structure are
implemented now so the strategy activates when the feed lands.

live_status: shadow (NEVER live; promotion-time, operator-gated).
"""

from __future__ import annotations

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
    write_candidate_no_trade_row,
)

# Stale threshold: book hash unchanged longer than this many ms after info event
# is considered stale (book has not yet responded to the info event).
_STALE_THRESHOLD_MS: float = 120_000.0

# Minimum depth required to log an enter (shadow research only).
_MIN_DEPTH_FOR_ENTER: int = 1


def _is_book_hash_stale(
    book_hash: Optional[str],
    book_hash_transition_delta_ms: Optional[int],
) -> bool:
    """Return True when the book hash appears stale (has not responded to an info event).

    Stale when:
      1. A book_hash is present (market is observable).
      2. hash_transition_delta_ms is absent (no transition) OR exceeds threshold.

    Note: caller is responsible for checking info_event_observed before calling this.
    """
    if not book_hash:
        return False
    if book_hash_transition_delta_ms is None:
        return True
    return float(book_hash_transition_delta_ms) > _STALE_THRESHOLD_MS


def _compute_fok_edge(
    p1: Decimal,
    a0: Decimal,
    fee_rate: Decimal,
) -> Optional[Decimal]:
    """Compute per-share FOK edge: p1 - a0 - phi(1, a0, fee_rate).

    Returns None if a0 is not in the valid price domain (0, 1) — phi raises
    outside that domain and the theorem is undefined.
    """
    if not (Decimal("0") < a0 < Decimal("1")):
        return None
    fee = phi(Decimal("1"), a0, fee_rate)
    return p1 - a0 - fee


class StaleQuoteDetector(BaseStrategyCandidate):
    """Shadow candidate: FOK information-delay arbitrage.

    Edge source: canonical InfoEvent updates fair probability p_0 → p_1;
    resting ask a_0 has not responded (book hash stale); EV_filled = p_1 - a_0 - phi > 0.

    DATA-GATED inputs (emit no_trade when absent):
      - metrics.info_event_observed  (bool, default False in MarketAnalysisVNext)
      - metrics.p_after_lower_bound  (Optional[Decimal], default None)
      - metrics.stale_quote_price    (Optional[Decimal], default None)

    live_status: shadow. CandidateMetadata.executable_alpha=True (metadata-only;
    NOT a registry YAML field — passing it to registry raises RegistrySchemaError).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="stale_quote_detector",
                family="stale_quote_detector",
                description=(
                    "Shadow candidate: FOK information-delay arbitrage. "
                    "InfoEvent updates p_0 → p_1; stale resting ask a_0 yields "
                    "EV_filled = p_1 - a_0 - phi(a_0). Data-gated: no_trade "
                    "until info-event feed and executable-quote capture are wired."
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
        """Evaluate FOK information-delay arb condition.

        No-trade paths (all use reason=STALE_QUOTE_FILL_INFEASIBLE):
          - MicrostructureMetrics unavailable.
          - info_event_observed=False (data-gated: no canonical InfoEvent).
          - p_after_lower_bound=None (data-gated: no posterior p1).
          - stale_quote_price=None (data-gated: no executable a0).
          - book hash is fresh (responded to info event; stale-quote edge gone).
          - depth_at_best_ask == 0 (quote already consumed).
          - edge = p1 - a0 - phi ≤ 0 (no positive EV theorem).

        Enter path: InfoEvent known + p1 present + a0 present + book stale +
          depth > 0 + edge > 0 → shadow enter, shadow row carries computed edge.

        Never returns None.
        """
        analysis = context.analysis
        metrics = getattr(analysis, "metrics", None)

        if metrics is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                reason_detail=(
                    "stale_quote_detector: MicrostructureMetrics unavailable; "
                    "cannot assess FOK information-delay arb conditions."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 1: canonical InfoEvent must be known
        info_event_observed: bool = bool(getattr(metrics, "info_event_observed", False))
        if not info_event_observed:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                reason_detail=(
                    "stale_quote_detector: info_event_observed=False; "
                    "data-gated — info-event feed not wired, no FOK theorem applies."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 2: post-event posterior p1 must be present
        p_after_raw = getattr(metrics, "p_after_lower_bound", None)
        p1: Optional[Decimal] = (
            Decimal(str(p_after_raw)) if p_after_raw is not None else None
        )
        if p1 is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                reason_detail=(
                    "stale_quote_detector: p_after_lower_bound=None; "
                    "data-gated — post-event posterior unavailable."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 3: executable stale ask a0 must be present
        stale_price_raw = getattr(metrics, "stale_quote_price", None)
        a0: Optional[Decimal] = (
            Decimal(str(stale_price_raw)) if stale_price_raw is not None else None
        )
        if a0 is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                reason_detail=(
                    "stale_quote_detector: stale_quote_price=None; "
                    "data-gated — executable quote capture not wired."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 4: book hash must still be stale (has not responded to info event)
        book_hash: Optional[str] = getattr(metrics, "snapshot_id", None)
        hash_delta_ms: Optional[int] = getattr(
            metrics, "raw_orderbook_hash_transition_delta_ms", None
        )
        stale = _is_book_hash_stale(
            book_hash=book_hash,
            book_hash_transition_delta_ms=hash_delta_ms,
        )
        if not stale:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                reason_detail=(
                    "stale_quote_detector: book hash responded to info event; "
                    "stale-quote edge is gone."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 5: depth at best ask must be > 0 (executable quote present)
        depth_at_best_ask: int = int(getattr(metrics, "depth_at_best_ask", 0) or 0)
        if depth_at_best_ask < _MIN_DEPTH_FOR_ENTER:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                reason_detail=(
                    "stale_quote_detector: depth_at_best_ask=0; "
                    "quote likely already consumed."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 6: compute edge = p1 - a0 - phi(1, a0, fee_rate); must be > 0
        fee_rate = venue_fee_rate()
        edge = _compute_fok_edge(p1, a0, fee_rate)
        if edge is None or edge <= Decimal("0"):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                reason_detail=(
                    f"stale_quote_detector: edge={edge} ≤ 0; "
                    f"p1={p1}, a0={a0}, fee_rate={fee_rate}. "
                    "No positive EV theorem."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Shadow enter: all theorem conditions satisfied.
        # Emit computed edge (not placeholder) so shadow rows can be inspected
        # to verify the theorem is correctly implemented.
        decision_time_iso = (
            decision_time.replace(tzinfo=timezone.utc).isoformat()
            if decision_time.tzinfo is None
            else decision_time.isoformat()
        )
        write_shadow_decision_event(
            context.natural_key,
            decision_time=decision_time_iso,
            side="buy_yes",
            strategy_key=self.strategy_key,
            conn=conn,
            edge=float(edge),
            p_posterior=float(p1),
            target_price=float(a0),
            polymarket_end_anchor_source=getattr(
                metrics,
                "polymarket_end_anchor_source",
                None,
            ),
        )

        return CandidateDecision(
            outcome="enter",
            side="buy_yes",
            target_price=a0,
            target_size_usd=None,
            edge=edge,
            p_posterior=p1,
        )
