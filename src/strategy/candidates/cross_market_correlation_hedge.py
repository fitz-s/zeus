# Created: 2026-04-27
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/05_PHASE_4_FDR_FAMILY_CANDIDATES.md
"""CrossMarketCorrelationHedge — shadow candidate strategy.

Edge source: cross-city same-weather-system correlation under a regime-conditional
Ledoit-Wolf shrunk correlation matrix (Phase 5 surfaces).

When two cities share a common weather system under the same WeatherRegimeTag, their
temperature anomalies are correlated. A shadow hedge position on the correlated
market is sized proportionally to the off-diagonal shrunk correlation entry between
the two cities' residual series.

Gate conditions (emit no_trade with reason=CORR_HEDGE_REGIME_UNAVAILABLE):
  1. City cannot be resolved from market_events_v2 for this market_slug.
  2. regime_tag_for() returns WeatherRegimeTag.UNKNOWN (fail-open; UNKNOWN never
     enters the cache).
  3. RegimeCorrelationStore.get() raises KeyError (regime not yet fit; store empty).

live_status: shadow (NEVER live in Phase 4).

INV-37: conn supplied by caller; never auto-opened here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Optional

import numpy as np

from src.contracts.no_trade_reason import NoTradeReason
from src.contracts.weather_regime_tag import WeatherRegimeTag, regime_tag_for
from src.state.decision_events import write_shadow_decision_event
from src.strategy.regime_correlation_store import RegimeCorrelationStore

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    write_candidate_no_trade_row,
)

# Placeholder shadow edge for decision_events row (no live sizing in Phase 4).
_SHADOW_EDGE: float = 0.02

# Minimum off-diagonal correlation magnitude to emit a shadow enter.
# Below this threshold the correlated market offers no meaningful hedge.
_MIN_CORR_FOR_ENTER: float = 0.10


def _resolve_city(market_slug: str, conn: sqlite3.Connection) -> Optional[str]:
    """Return the canonical city string for market_slug from market_events_v2.

    Returns None if the market is not found (table missing or no row). INV-37:
    we query the supplied conn; never open an independent connection.
    """
    try:
        row = conn.execute(
            "SELECT city FROM market_events_v2 WHERE market_slug = ? LIMIT 1",
            (market_slug,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return str(row[0]).strip() or None


class CrossMarketCorrelationHedge(BaseStrategyCandidate):
    """Shadow candidate: cross-market correlation hedge via regime-conditional shrinkage.

    Edge source: off-diagonal entry of the Ledoit-Wolf shrunk correlation matrix for
    the current WeatherRegimeTag. When a meaningful positive correlation is present,
    a shadow buy_yes entry on the hedging market is logged to decision_events.

    live_status: shadow. CandidateMetadata.executable_alpha=True (metadata-only;
    NOT a registry YAML field — passing it to registry raises RegistrySchemaError).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="cross_market_correlation_hedge",
                family="cross_market_correlation_hedge",
                description=(
                    "Shadow candidate: cross-market correlation hedge using regime-conditional "
                    "Ledoit-Wolf shrunk correlation matrices. Edge source: cross-city "
                    "same-weather-system correlation under WeatherRegimeTag (Phase 5 shrinkage). "
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
        """Evaluate cross-market correlation hedge against the market context.

        No-trade path (reason=CORR_HEDGE_REGIME_UNAVAILABLE):
          - City cannot be resolved from market_events_v2.
          - regime_tag_for() returns WeatherRegimeTag.UNKNOWN.
          - RegimeCorrelationStore has no fitted matrix for this regime.
          - City not present in stored matrix or fewer than 2 cities stored.
          - Max off-diagonal correlation < _MIN_CORR_FOR_ENTER.

        Enter path:
          - Regime is a known non-UNKNOWN tag.
          - RegimeCorrelationStore has a fitted matrix for this regime.
          - City is in the stored matrix with at least one other city.
          - Off-diagonal max correlation >= _MIN_CORR_FOR_ENTER.
          -> shadow enter logged to decision_events.

        Never returns None. Never raises; all guard failures become no_trade.
        """
        analysis = context.analysis
        market_slug, temperature_metric, target_date_str, observation_time, _ = context.natural_key

        # --- Guard 1: resolve city ---
        city: Optional[str] = getattr(analysis, "city", None)
        if city is None:
            city = _resolve_city(market_slug, conn)
        if not city:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE,
                reason_detail=(
                    f"cross_market_correlation_hedge: city not resolvable for "
                    f"market_slug={market_slug!r}; cannot determine regime."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Guard 2: parse target_date ---
        try:
            target_date = date.fromisoformat(target_date_str)
        except (ValueError, TypeError):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE,
                reason_detail=(
                    f"cross_market_correlation_hedge: cannot parse target_date "
                    f"{target_date_str!r} as ISO date."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Guard 3: resolve regime ---
        regime = regime_tag_for(city, target_date, decision_time, conn)

        if regime is WeatherRegimeTag.UNKNOWN:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE,
                reason_detail=(
                    f"cross_market_correlation_hedge: regime_tag_for returned UNKNOWN "
                    f"for city={city!r} target_date={target_date_str!r}; "
                    "insufficient history or in-memory conn. Fail-open."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Guard 4: RegimeCorrelationStore lookup ---
        # Fetch the full stored cities list before get() to check city membership
        # and avoid a KeyError that would mask the CORR_HEDGE_REGIME_UNAVAILABLE reason.
        try:
            row = conn.execute(
                "SELECT cities_json FROM regime_correlation_cache WHERE regime = ?",
                (str(regime),),
            ).fetchone()
            if row is None:
                raise KeyError(f"Regime {regime!r} not fitted in regime_correlation_cache.")
            stored_cities: list[str] = json.loads(row[0])
        except Exception as exc:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE,
                reason_detail=(
                    f"cross_market_correlation_hedge: correlation store unavailable for "
                    f"regime={regime!r}: {exc}"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # Require at least 2 cities (need off-diagonal entries) and this city present.
        if len(stored_cities) < 2 or city not in stored_cities:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE,
                reason_detail=(
                    f"cross_market_correlation_hedge: city={city!r} not in stored matrix "
                    f"or matrix has fewer than 2 cities for regime={regime!r}. "
                    f"Stored cities: {stored_cities}"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        try:
            store = RegimeCorrelationStore(conn)
            corr_matrix = store.get(regime, stored_cities)
        except Exception as exc:
            # Catch KeyError (regime not fitted), json.JSONDecodeError (corrupt cache),
            # ValueError/IndexError (unexpected matrix shape), or any other store failure.
            # All map to CORR_HEDGE_REGIME_UNAVAILABLE to preserve fail-open contract.
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE,
                reason_detail=(
                    f"cross_market_correlation_hedge: store.get failed "
                    f"({type(exc).__name__}): {exc}"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Guard 5: correlation magnitude gate ---
        city_idx = stored_cities.index(city)
        city_row = corr_matrix[city_idx, :]
        off_diag_vals = np.array([
            city_row[i] for i in range(len(stored_cities)) if i != city_idx
        ])

        if len(off_diag_vals) == 0:
            max_corr = 0.0
        else:
            max_corr = float(np.max(np.abs(off_diag_vals)))

        if max_corr < _MIN_CORR_FOR_ENTER:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE,
                reason_detail=(
                    f"cross_market_correlation_hedge: max off-diagonal correlation "
                    f"{max_corr:.4f} < threshold {_MIN_CORR_FOR_ENTER} for city={city!r} "
                    f"regime={regime!r}; no meaningful cross-market hedge."
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
