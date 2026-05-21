# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/05_PHASE_4_FDR_FAMILY_CANDIDATES.md
"""LiquidityProvisionWithHeartbeat — shadow candidate strategy.

Edge source: market-maker batch quoting cadence. When a market's book shows
regular heartbeat transitions (fill_probability evidence from prior commands)
the strategy can post passive quotes in anticipation of the next cadence window.

Required inputs:
  - fill_probability from PassiveMakerExecutionEstimate (TBD field — guarded
    here with LIQPROV_HEARTBEAT_ABSENT when absent).
  - depth_at_best_ask from MicrostructureMetrics.
  - book hash transition evidence.

live_status: shadow (NEVER live in Phase 4).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
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

# Minimum fill probability (evidence-backed) to proceed with a shadow enter.
_MIN_FILL_PROBABILITY: Decimal = Decimal("0.30")

_SHADOW_EDGE: float = 0.025


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


class LiquidityProvisionWithHeartbeat(BaseStrategyCandidate):
    """Shadow candidate: heartbeat-aware passive liquidity provision.

    Edge source: market-maker quoting cadence + fill_probability evidence.
    When fill_probability is present and above minimum threshold, the strategy
    logs a shadow enter decision for passive quoting research.

    Guard: LIQPROV_HEARTBEAT_ABSENT fires when fill_probability field is absent
    on MarketAnalysisVNext (field is TBD per authority doc — this is the
    missing-field guard the plan requires to be tested).

    live_status: shadow. CandidateMetadata.executable_alpha=True (metadata-only;
    NOT a registry YAML field — passing it to registry raises RegistrySchemaError).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="liquidity_provision_with_heartbeat",
                family="liquidity_provision_with_heartbeat",
                description=(
                    "Shadow candidate: heartbeat-aware passive liquidity provision. "
                    "Edge source: fill_probability evidence + book hash cadence."
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
        """Evaluate heartbeat-aware liquidity provision edge.

        Enter path: fill_probability present AND above threshold AND depth present
          → shadow enter logged to decision_events.

        No-trade path (reason=LIQPROV_HEARTBEAT_ABSENT):
          - MicrostructureMetrics unavailable.
          - fill_probability field absent on analysis (field TBD per authority doc).
          - fill_probability below minimum threshold.
          - depth_at_best_ask == 0.

        Never returns None. The LIQPROV_HEARTBEAT_ABSENT guard for missing
        fill_probability is the required missing-field guard per plan §T3.
        """
        analysis = context.analysis
        metrics = getattr(analysis, "metrics", None)

        if metrics is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.LIQPROV_HEARTBEAT_ABSENT,
                reason_detail=(
                    "liquidity_provision_with_heartbeat: MicrostructureMetrics unavailable; "
                    "cannot assess fill probability."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Guard: fill_probability is a TBD field. Check for PassiveMakerExecutionEstimate
        # on the analysis object — it may be None when the estimate has not been computed.
        passive_estimate = getattr(analysis, "passive_maker_estimate", None)
        if passive_estimate is None:
            # Required missing-field guard per plan §T3 acceptance criteria.
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.LIQPROV_HEARTBEAT_ABSENT,
                reason_detail=(
                    "liquidity_provision_with_heartbeat: fill_probability absent "
                    "(passive_maker_estimate not computed); heartbeat cadence unobservable."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        fill_probability: Decimal = getattr(passive_estimate, "expected_fill_probability", Decimal("0"))
        if fill_probability < _MIN_FILL_PROBABILITY:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.LIQPROV_HEARTBEAT_ABSENT,
                reason_detail=(
                    f"liquidity_provision_with_heartbeat: fill_probability={fill_probability} "
                    f"below minimum {_MIN_FILL_PROBABILITY}; heartbeat cadence insufficient."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        depth_at_best_ask: int = int(getattr(metrics, "depth_at_best_ask", 0) or 0)
        if depth_at_best_ask <= 0:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.LIQPROV_HEARTBEAT_ABSENT,
                reason_detail=(
                    "liquidity_provision_with_heartbeat: depth_at_best_ask=0; "
                    "no passive queue depth for heartbeat entry."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Shadow enter: fill probability present and sufficient.
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
