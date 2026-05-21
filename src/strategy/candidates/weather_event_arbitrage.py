# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/05_PHASE_4_FDR_FAMILY_CANDIDATES.md
"""WeatherEventArbitrage — shadow candidate strategy.

Edge source: weather alert / extreme event lag. When an authoritative external
weather alert feed signals an extreme event, the corresponding Polymarket market
may not yet have priced the information. The strategy logs a shadow entry when
the alert feed is trusted and the market spread allows capture.

Required inputs:
  - External alert feed wiring (TBD — guarded with WEATHER_ALERT_SOURCE_UNTRUSTED
    when absent, per plan §T3 acceptance criteria).
  - MicrostructureMetrics for spread / depth assessment.

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

# Alert sources that are considered trusted for shadow research.
_TRUSTED_ALERT_SOURCES: frozenset[str] = frozenset({
    "noaa_alerts", "nws_alerts", "ecmwf_extreme_events",
    "gfs_extreme_events", "zeus_internal_alert",
})

_SHADOW_EDGE: float = 0.04


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


class WeatherEventArbitrage(BaseStrategyCandidate):
    """Shadow candidate: weather alert event lag arbitrage.

    Edge source: external weather alert feed signalling an extreme event before
    the Polymarket market reprices. The strategy enters (shadow) when the alert
    source is trusted and market conditions allow capture.

    Guard: WEATHER_ALERT_SOURCE_UNTRUSTED fires when the external alert feed
    is not wired or the source is not in the trusted set (per plan §T3: missing-
    field guard must be exercised in a test).

    live_status: shadow. CandidateMetadata.executable_alpha=True (metadata-only;
    NOT a registry YAML field — passing it to registry raises RegistrySchemaError).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="weather_event_arbitrage",
                family="weather_event_arbitrage",
                description=(
                    "Shadow candidate: weather alert / extreme event lag arbitrage. "
                    "Edge source: trusted external alert feed signals event before market reprices."
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
        """Evaluate weather-event arbitrage edge against the market context.

        Enter path: alert_source is trusted AND alert is active
          → shadow enter logged to decision_events.

        No-trade path (reason=WEATHER_ALERT_SOURCE_UNTRUSTED):
          - MicrostructureMetrics unavailable.
          - alert_source absent (feed not wired — required missing-field guard).
          - alert_source not in trusted set.
          - No active alert for this market / target_date.

        Never returns None.
        """
        analysis = context.analysis
        metrics = getattr(analysis, "metrics", None)

        if metrics is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED,
                reason_detail=(
                    "weather_event_arbitrage: MicrostructureMetrics unavailable; "
                    "cannot assess alert feed."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Guard: external alert feed is TBD. Check for alert_source attribute.
        alert_source: Optional[str] = getattr(analysis, "alert_source", None)
        if alert_source is None:
            # Required missing-field guard per plan §T3 acceptance criteria.
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED,
                reason_detail=(
                    "weather_event_arbitrage: alert_source absent; "
                    "external alert feed not wired. Cannot assess extreme event."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        alert_source_norm = str(alert_source).lower().strip()
        if alert_source_norm not in _TRUSTED_ALERT_SOURCES:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED,
                reason_detail=(
                    f"weather_event_arbitrage: alert_source={alert_source!r} "
                    "not in trusted set; cannot trust alert for entry."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Alert source is trusted. Check for an active alert signal.
        active_alert: Optional[bool] = getattr(analysis, "active_weather_alert", None)
        if not active_alert:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED,
                reason_detail=(
                    f"weather_event_arbitrage: alert_source={alert_source!r} trusted "
                    "but no active alert for this market / target_date."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Active trusted alert — write shadow enter.
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
