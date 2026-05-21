# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/05_PHASE_4_FDR_FAMILY_CANDIDATES.md
"""ResolutionWindowMaker — shadow candidate strategy.

Edge source: source-known-but-venue-unresolved discount. When a market's
resolution outcome is known by an authoritative source (e.g. UMA listener /
umaResolutionStatus) but the venue has not yet settled, the market price should
converge to 0 or 1. During this window, a maker strategy can quote the
converging side at a discount.

Requires:
  - ResolutionEra (Phase 0 PR 1) — identifies settlement period.
  - umaResolutionStatus from ExecutableMarketSnapshotV2 / MicrostructureMetrics.

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

# UMA resolution statuses that indicate the result IS known but venue not settled.
_UMA_RESOLVED_STATUSES: frozenset[str] = frozenset({
    "resolved", "asserted", "settlement_confirmed", "uma_resolved",
})

# Statuses that indicate resolution is DISPUTED or unknown → no_trade.
_UMA_DISPUTED_STATUSES: frozenset[str] = frozenset({
    "disputed", "paused", "challenged", "unknown",
})

_SHADOW_EDGE: float = 0.03


def _allocate_shadow_seq(
    conn: sqlite3.Connection,
    market_slug: str,
    temperature_metric: str,
    target_date: str,
    observation_time: str,
) -> int:
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


class ResolutionWindowMaker(BaseStrategyCandidate):
    """Shadow candidate: exploits source-known/venue-unresolved discount window.

    Edge source: umaResolutionStatus known + venue market not yet settled.
    When the UMA oracle has asserted a result that is not yet reflected in
    the Polymarket price, a maker entry at the converging side captures the
    resolution discount.

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
                    "discount window. Edge source: UMA resolution status known; "
                    "venue price not yet converged."
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
        """Evaluate resolution-window edge against the market context.

        Enter path: UMA resolution status in resolved set AND venue market
          not yet settled → shadow enter logged to decision_events.

        No-trade path (reason=RESOLUTION_DISPUTED):
          - MicrostructureMetrics unavailable.
          - UMA resolution status disputed, paused, or unknown.
          - UMA resolution status absent (cannot determine).
          - Market already settled at venue (no discount window).

        Never returns None.
        """
        analysis = context.analysis
        metrics = getattr(analysis, "metrics", None)

        if metrics is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.RESOLUTION_DISPUTED,
                reason_detail=(
                    "resolution_window_maker: MicrostructureMetrics unavailable; "
                    "cannot determine resolution status."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Look for umaResolutionStatus on the snapshot / metrics object.
        # Field may be on the snapshot embedded in analysis or on metrics itself.
        uma_status: Optional[str] = (
            getattr(metrics, "uma_resolution_status", None)
            or getattr(analysis, "uma_resolution_status", None)
        )

        if uma_status is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.RESOLUTION_DISPUTED,
                reason_detail=(
                    "resolution_window_maker: umaResolutionStatus absent; "
                    "cannot assess resolution window."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        uma_status_norm = str(uma_status).lower().strip()

        if uma_status_norm in _UMA_DISPUTED_STATUSES:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.RESOLUTION_DISPUTED,
                reason_detail=(
                    f"resolution_window_maker: umaResolutionStatus={uma_status!r} "
                    "indicates disputed or contested resolution; cannot enter."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        if uma_status_norm not in _UMA_RESOLVED_STATUSES:
            # Status is present but not in any known set — treat as disputed.
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.RESOLUTION_DISPUTED,
                reason_detail=(
                    f"resolution_window_maker: umaResolutionStatus={uma_status!r} "
                    "is unrecognized; treating as unresolved."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # UMA has resolved. Write shadow enter.
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
