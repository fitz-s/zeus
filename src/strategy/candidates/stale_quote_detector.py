# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/05_PHASE_4_FDR_FAMILY_CANDIDATES.md
"""StaleQuoteDetector — shadow candidate strategy.

Edge source: book hash unchanged after an information event (stale resting quote).
When a market's book hash has not transitioned after a known info event, a resting
quote at the best ask is likely stale and fill-infeasible at that price. The strategy
logs a shadow decision when stale-quote conditions are detected.

live_status: shadow (NEVER live in Phase 4).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from src.contracts.no_trade_reason import NoTradeReason
from src.state.db import SCHEMA_VERSION

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    write_candidate_no_trade_row,
)

# Stale threshold: book hash unchanged longer than this many ms after info event
# is considered stale.
_STALE_THRESHOLD_MS: float = 120_000.0

# Minimum depth required to log an enter (shadow research only).
_MIN_DEPTH_FOR_ENTER: int = 1

# Placeholder shadow edge for decision_events row (no live sizing).
_SHADOW_EDGE: float = 0.02


def _is_book_hash_stale(
    book_hash: Optional[str],
    has_info_event: bool,
    book_hash_transition_delta_ms: Optional[int],
) -> bool:
    """Return True when the book hash appears stale after a known info event.

    Stale when:
      1. A book_hash is present (market is observable).
      2. An info event is known to have occurred.
      3. hash_transition_delta_ms is absent (no transition) OR exceeds threshold.
    """
    if not book_hash or not has_info_event:
        return False
    if book_hash_transition_delta_ms is None:
        return True
    return float(book_hash_transition_delta_ms) > _STALE_THRESHOLD_MS


def _allocate_shadow_seq(
    conn: sqlite3.Connection,
    market_slug: str,
    temperature_metric: str,
    target_date: str,
    observation_time: str,
) -> int:
    """Return the next decision_seq for this 4-tuple (UNION of both tables)."""
    row = conn.execute(
        """
        SELECT COALESCE(MAX(seq), -1) FROM (
            SELECT decision_seq AS seq FROM decision_events
             WHERE market_slug=? AND temperature_metric=? AND target_date=? AND observation_time=?
            UNION ALL
            SELECT decision_seq AS seq FROM no_trade_events
             WHERE market_slug=? AND temperature_metric=? AND target_date=? AND observation_time=?
        )
        """,
        (
            market_slug, temperature_metric, target_date, observation_time,
            market_slug, temperature_metric, target_date, observation_time,
        ),
    ).fetchone()
    return (row[0] if row else -1) + 1


class StaleQuoteDetector(BaseStrategyCandidate):
    """Shadow candidate: detects stale resting quotes post info-event.

    Edge source: book_hash unchanged after info_event_time (microstructure_metrics).
    Required inputs: snapshot_id (book_hash proxy), depth_at_best_ask,
    spread_observed_window_ms (info-event presence proxy) from MicrostructureMetrics.

    live_status: shadow. CandidateMetadata.executable_alpha=True (metadata-only;
    NOT a registry YAML field — passing it to registry raises RegistrySchemaError).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="stale_quote_detector",
                family="stale_quote_detector",
                description=(
                    "Shadow candidate: detects fill-infeasible stale resting quotes "
                    "after information events. Edge source: book hash stasis post info-event."
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
        """Evaluate stale-quote condition against the market context.

        Enter path: book hash stale post info-event AND best-ask depth present
          → shadow enter decision logged to decision_events (source='phase0_backfill').
          Returns CandidateDecision(outcome='enter', ...).

        No-trade path (reason=STALE_QUOTE_FILL_INFEASIBLE):
          - MicrostructureMetrics unavailable.
          - book hash fresh (responded to info event; no stale-quote edge).
          - book hash stale but depth_at_best_ask == 0 (quote already consumed).

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
                    "cannot assess book hash freshness."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        book_hash: Optional[str] = getattr(metrics, "snapshot_id", None)
        depth_at_best_ask: int = int(getattr(metrics, "depth_at_best_ask", 0) or 0)
        # spread_observed_window_ms non-None = recent spread observation = info-event proxy.
        spread_window_ms: Optional[int] = getattr(metrics, "spread_observed_window_ms", None)
        has_info_event = spread_window_ms is not None
        hash_delta_ms: Optional[int] = getattr(metrics, "raw_orderbook_hash_transition_delta_ms", None)

        stale = _is_book_hash_stale(
            book_hash=book_hash,
            has_info_event=has_info_event,
            book_hash_transition_delta_ms=hash_delta_ms,
        )

        if not stale:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                reason_detail=(
                    "stale_quote_detector: book hash is fresh or no info event detected; "
                    "no fill-infeasibility edge."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        if depth_at_best_ask < _MIN_DEPTH_FOR_ENTER:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STALE_QUOTE_FILL_INFEASIBLE,
                reason_detail=(
                    "stale_quote_detector: book hash stale but depth_at_best_ask=0; "
                    "quote likely already consumed."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Shadow enter: write decision_events row (source='phase0_backfill').
        market_slug, temperature_metric, target_date, observation_time, _ = context.natural_key
        decision_time_iso = (
            decision_time.replace(tzinfo=timezone.utc).isoformat()
            if decision_time.tzinfo is None
            else decision_time.isoformat()
        )
        seq = _allocate_shadow_seq(
            conn, market_slug, temperature_metric, target_date, observation_time
        )
        anchor_source = getattr(metrics, "polymarket_end_anchor_source", "gamma_explicit") or "gamma_explicit"

        conn.execute(
            """
            INSERT INTO decision_events (
                market_slug, temperature_metric, target_date, observation_time, decision_seq,
                condition_id, decision_event_id, decision_time,
                outcome, side, strategy_key,
                cycle_id, cycle_iteration,
                p_posterior, edge, target_size_usd, target_price,
                forecast_time, provider_reported_time,
                observation_available_at, polymarket_end_anchor_source,
                first_member_observed_time, run_complete_time,
                zeus_submit_intent_time, venue_ack_time,
                first_inclusion_block_time, finality_confirmed_time,
                clock_skew_estimate_ms_at_submit, raw_orderbook_hash_transition_delta_ms,
                schema_version, source
            ) VALUES (
                ?,?,?,?,?,
                NULL,NULL,?,
                ?,?,?,
                NULL,NULL,
                NULL,?,NULL,NULL,
                NULL,NULL,
                '',?,
                NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                ?,?
            )
            """,
            (
                market_slug, temperature_metric, target_date, observation_time, seq,
                decision_time_iso,
                "shadow_enter", "buy_yes", self.strategy_key,
                _SHADOW_EDGE,
                anchor_source,
                SCHEMA_VERSION, "phase0_backfill",
            ),
        )
        conn.commit()

        return CandidateDecision(
            outcome="enter",
            side="buy_yes",
            target_price=None,
            target_size_usd=None,
            edge=None,
            p_posterior=None,
        )
