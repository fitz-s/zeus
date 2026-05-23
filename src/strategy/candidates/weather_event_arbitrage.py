# Created: 2026-05-21
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §10
#                  + docs/reference/zeus_strategy_spec.md §14
"""WeatherEventArbitrage — shadow candidate strategy (Bayes-factor alert arbitrage).

§10 theorem (STRATEGY_TAXONOMY_DIRECTIVE):
  Alert A is a public signal. For YES bin B_i:
    O_i  = Pr(B_i) / (1 − Pr(B_i))          pre-alert odds from forecast
    LR_i = Pr(A|B_i) / Pr(A|¬B_i)           likelihood ratio, learned per
                                              (alertType, city, season, leadTime)
    O'_i = O_i · LR_i                         Bayes-updated odds
    p'_i = O'_i / (1 + O'_i)                posterior probability
  Enter iff  p'⁻_i − a_i − φ(a_i) > 0       posterior lower-bound beats ask + fee

DATA-GATED: NWS alert feed + alert_event_fact table are not yet wired.
  1. alert_source absent → WEATHER_ALERT_SOURCE_UNTRUSTED (feed not wired).
  2. prior_p absent from analysis → WEATHER_ALERT_SOURCE_UNTRUSTED (forecast not wired).
  3. LR table absent / returns None → WEATHER_ALERT_LR_TABLE_MISSING.
  The strategy emits no_trade on all three gates until the data pipeline is built.

live_status: shadow (NEVER live until all gates are wired and LR table is fitted).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from src.calibration.bounds import calibrated_bounds
from src.contracts.no_trade_reason import NoTradeReason
from src.state.decision_events import write_shadow_decision_event
from src.strategy.bayes_alert import AlertLRStub, AlertLRTable, posterior_lower_bound
from src.strategy.fees import phi, venue_fee_rate

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    write_candidate_no_trade_row,
)

# Alert sources considered trusted for shadow research.
_TRUSTED_ALERT_SOURCES: frozenset[str] = frozenset({
    "noaa_alerts", "nws_alerts", "ecmwf_extreme_events",
    "gfs_extreme_events", "zeus_internal_alert",
})

# Default LR table: data-gated stub (always returns None until fitted).
_DEFAULT_LR_TABLE: AlertLRTable = AlertLRStub()

# Shadow entry size for decision_events provenance (shares — no live sizing yet).
_SHADOW_SHARES: Decimal = Decimal("1")


class WeatherEventArbitrage(BaseStrategyCandidate):
    """Shadow candidate: Bayes-factor alert arbitrage (§10).

    Reframe: alert A is a public signal. The strategy Bayes-updates pre-alert
    bin probability p_i with a learned likelihood ratio LR_i, then checks the
    posterior lower bound p'⁻_i against ask + fee to assess edge.

    Guards (all emit no_trade until wired):
      WEATHER_ALERT_SOURCE_UNTRUSTED — feed absent, untrusted source, no active alert,
                                       or prior_p not available from forecast.
      WEATHER_ALERT_LR_TABLE_MISSING — LR table returns None for this (alertType,
                                       city, season, leadTime) combination.

    live_status: shadow. CandidateMetadata.executable_alpha=True (metadata-only;
    NOT a registry YAML field — passing it to registry raises RegistrySchemaError).
    """

    def __init__(self, *, lr_table: Optional[AlertLRTable] = None) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="weather_event_arbitrage",
                family="weather_event_arbitrage",
                description=(
                    "Shadow candidate: Bayes-factor alert arbitrage (§10). "
                    "Enters when posterior lower-bound p'⁻ beats ask + fee after "
                    "updating prior bin probability with a learned alert LR."
                ),
                executable_alpha=True,
            )
        )
        self._lr_table: AlertLRTable = lr_table if lr_table is not None else _DEFAULT_LR_TABLE

    def evaluate(
        self,
        *,
        context: CandidateContext,
        conn: sqlite3.Connection,
        decision_time: datetime,
    ) -> CandidateDecision:
        """Evaluate Bayes-factor alert arbitrage edge against market context.

        Gate sequence (all emit no_trade until the data pipeline is wired):
          1. MicrostructureMetrics unavailable → WEATHER_ALERT_SOURCE_UNTRUSTED.
          2. alert_source absent → WEATHER_ALERT_SOURCE_UNTRUSTED (feed not wired).
          3. alert_source not in trusted set → WEATHER_ALERT_SOURCE_UNTRUSTED.
          4. No active alert → WEATHER_ALERT_SOURCE_UNTRUSTED.
          5. prior_p absent from analysis → WEATHER_ALERT_SOURCE_UNTRUSTED (forecast not wired).
          6. LR table returns None → WEATHER_ALERT_LR_TABLE_MISSING (table not fitted).
          7. posterior lower-bound gate: p'⁻ − ask − φ ≤ 0 → INSUFFICIENT_EDGE_AFTER_FEES.
          8. Enter path: write shadow decision_event with posterior + computed edge.

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
                    "cannot assess alert feed or price."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 2: external alert feed wiring check.
        alert_source: Optional[str] = getattr(analysis, "alert_source", None)
        if alert_source is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED,
                reason_detail=(
                    "weather_event_arbitrage: alert_source absent; "
                    "NWS alert feed not wired."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 3: trusted source check.
        alert_source_norm = str(alert_source).lower().strip()
        if alert_source_norm not in _TRUSTED_ALERT_SOURCES:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED,
                reason_detail=(
                    f"weather_event_arbitrage: alert_source={alert_source!r} "
                    "not in trusted set."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 4: active alert signal.
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

        # Gate 5: pre-alert prior probability (from forecast — data-gated).
        prior_p: Optional[float] = getattr(analysis, "alert_prior_p", None)
        if prior_p is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED,
                reason_detail=(
                    "weather_event_arbitrage: alert_prior_p absent; "
                    "forecast-to-bin prior not wired."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 6: LR table lookup.
        alert_type: str = str(getattr(analysis, "alert_type", "") or "")
        city: str = str(getattr(analysis, "alert_city", "") or "")
        season: str = str(getattr(analysis, "alert_season", "") or "")
        lead_time_hours: int = int(getattr(analysis, "alert_lead_time_hours", 0) or 0)

        lr_record = self._lr_table.lookup(
            alert_type=alert_type,
            city=city,
            season=season,
            lead_time_hours=lead_time_hours,
        )
        if lr_record is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_LR_TABLE_MISSING,
                reason_detail=(
                    f"weather_event_arbitrage: LR table returned None for "
                    f"(alert_type={alert_type!r}, city={city!r}, "
                    f"season={season!r}, lead_time_hours={lead_time_hours}); "
                    "alert_event_fact archive not fitted."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Bayes update: p'⁻ via posterior lower bound.
        p_posterior_lower = posterior_lower_bound(prior_p, lr_record)

        # Ask price from snapshot (data-gated — may be None until book wired).
        ask: Optional[Decimal] = getattr(metrics, "best_ask", None)
        if ask is None:
            # Fallback: try the snapshot directly.
            snapshot = getattr(analysis, "_snapshot", None)
            ask = getattr(snapshot, "orderbook_top_ask", None) if snapshot else None
        if ask is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED,
                reason_detail=(
                    "weather_event_arbitrage: ask price unavailable; "
                    "cannot evaluate p'⁻ − ask − φ."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        ask_d = Decimal(str(ask))
        fee_rate = venue_fee_rate()
        fee = phi(_SHADOW_SHARES, ask_d, fee_rate)
        # Gate 7: posterior lower-bound entry condition (§10 theorem).
        edge_d = Decimal(str(p_posterior_lower)) - ask_d - fee
        if edge_d <= Decimal("0"):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.WEATHER_ALERT_EDGE_NONPOSITIVE,
                reason_detail=(
                    f"weather_event_arbitrage: p'⁻={p_posterior_lower:.6f} "
                    f"ask={ask_d} fee={fee} edge={edge_d} ≤ 0."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Gate 8: enter — write shadow decision_event with posterior provenance.
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
            edge=float(edge_d),
            polymarket_end_anchor_source=getattr(
                metrics,
                "polymarket_end_anchor_source",
                None,
            ),
        )

        return CandidateDecision(
            outcome="enter",
            side="buy_yes",
            target_price=ask_d,
            target_size_usd=None,
            edge=float(edge_d),
            p_posterior=p_posterior_lower,
        )
