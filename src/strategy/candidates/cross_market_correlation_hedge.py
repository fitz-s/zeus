# Created: 2026-04-27
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §12
#                  + docs/reference/zeus_strategy_spec.md §16.2-16.4
"""CrossMarketCorrelationHedge — shadow candidate: joint-distribution portfolio stat-arb.

Reframed 2026-05-22: replaces correlation-threshold heuristic (max corr > 0.10) with
the mean-variance portfolio theorem from §12 / §16.2:

    J(w) = wᵀe − (λ/2) wᵀΣ_shrunk w
    w* = λ⁻¹ Σ_shrunk⁻¹ e
    Enter iff J(w*) > 0    [i.e. eᵀΣ_shrunk⁻¹e > 0 with zero transaction cost]

DATA-GATED: regime_correlation_cache is empty/unfed → no_trade
(CORR_HEDGE_REGIME_UNAVAILABLE) until the shrinkage cache is populated. The
portfolio-optimization math is implemented and tested; the gate prevents live
evaluation until data actually feeds the cache. SHADOW-only.

Gate conditions:
  CORR_HEDGE_REGIME_UNAVAILABLE:
    1. City cannot be resolved from market_events_v2 for this market_slug.
    2. regime_tag_for() returns WeatherRegimeTag.UNKNOWN.
    3. regime_correlation_cache has no row for this regime (cache unfed).
    4. City not in stored matrix or fewer than 2 cities stored.
  CORR_HEDGE_OBJECTIVE_BELOW_COST:
    5. Portfolio objective J(w*) ≤ 0 — edge vector does not justify entry
       under the shrunk covariance (includes zero-edge data-gated case).

Enter path:
  - All guards pass.
  - J(w*) > 0.
  -> shadow enter logged to decision_events with edge = J(w*).

INV-37: conn supplied by caller; never auto-opened here.
live_status: shadow (NEVER live until cache and edge vector are fed).
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

# Risk-aversion parameter λ for mean-variance objective J(w) = wᵀe − (λ/2) wᵀΣw.
# Conservative default; no live sizing in shadow phase.
_LAMBDA: float = 2.0


def _compute_weights(
    e: np.ndarray,
    sigma_shrunk: np.ndarray,
    lam: float,
) -> np.ndarray:
    """Compute optimal portfolio weights w* = λ⁻¹ Σ_shrunk⁻¹ e.

    Solves Σ_shrunk w = e via np.linalg.solve (numerically preferred over
    explicit inversion). Returns w* = (1/lam) * Σ⁻¹ e.

    Args:
        e:            Edge vector of length p.
        sigma_shrunk: Shrunk correlation matrix of shape (p, p), positive definite.
        lam:          Risk-aversion coefficient λ > 0.

    Returns:
        w* array of length p.
    """
    return (1.0 / lam) * np.linalg.solve(sigma_shrunk, e)


def _portfolio_objective(
    e: np.ndarray,
    sigma_shrunk: np.ndarray,
    lam: float,
) -> float:
    """Compute J(w*) = w*ᵀe − (λ/2) w*ᵀ Σ_shrunk w* at optimal w*.

    Equivalent to (1/(2λ)) eᵀ Σ_shrunk⁻¹ e, non-negative when Σ is PD.
    Entry condition: J(w*) > 0, which holds iff e ≠ 0.

    Args:
        e:            Edge vector of length p.
        sigma_shrunk: Shrunk correlation matrix, shape (p, p), PD.
        lam:          Risk-aversion coefficient λ > 0.

    Returns:
        Scalar J(w*) ≥ 0.
    """
    w_star = _compute_weights(e, sigma_shrunk, lam)
    mu = float(w_star @ e)
    var_penalty = float(lam / 2.0 * (w_star @ sigma_shrunk @ w_star))
    return mu - var_penalty


def _resolve_city(market_slug: str, conn: sqlite3.Connection) -> Optional[str]:
    """Return the canonical city string for market_slug from market_events_v2.

    Returns None if the market is not found. INV-37: uses caller-supplied conn.
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
    """Shadow candidate: cross-market joint-distribution stat-arb via w*=Σ⁻¹e.

    Replaces max-correlation threshold gate with mean-variance portfolio theorem:
      Enter iff J(w*) = w*ᵀe − (λ/2) w*ᵀΣ_shrunk w* > 0, where w* = λ⁻¹Σ_shrunk⁻¹e.

    DATA-GATED: no entry until regime_correlation_cache is fed and edge_vector wired.
    live_status: shadow.
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="cross_market_correlation_hedge",
                family="cross_market_correlation_hedge",
                description=(
                    "Shadow candidate: joint-distribution portfolio stat-arb "
                    "(w*=Σ⁻¹e). Entry condition: J(w*)=w*ᵀe−(λ/2)w*ᵀΣw*>0 under "
                    "Ledoit-Wolf shrunk covariance. Replaces correlation-threshold "
                    "heuristic. DATA-GATED until regime_correlation_cache fed."
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
        """Evaluate joint-distribution portfolio stat-arb for this market context.

        Gate hierarchy:
          1. City resolution (CORR_HEDGE_REGIME_UNAVAILABLE)
          2. target_date parse (CORR_HEDGE_REGIME_UNAVAILABLE)
          3. Regime resolution — UNKNOWN → no_trade (CORR_HEDGE_REGIME_UNAVAILABLE)
          4. Cache lookup — not fitted → no_trade (CORR_HEDGE_REGIME_UNAVAILABLE)
          5. City membership in matrix (CORR_HEDGE_REGIME_UNAVAILABLE)
          6. Portfolio objective J(w*) > 0 (CORR_HEDGE_OBJECTIVE_BELOW_COST)

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

        # Require at least 2 cities and this city present.
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
            sigma_shrunk = store.get(regime, stored_cities)
        except Exception as exc:
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

        # --- Build edge vector ---
        # Edge vector: prefer analysis.edge_vector if pre-computed; otherwise zero
        # (data-gated — edge feed not wired in shadow phase → objective = 0).
        city_idx = stored_cities.index(city)
        p = len(stored_cities)
        raw_edge: Optional[np.ndarray] = getattr(analysis, "edge_vector", None)
        if raw_edge is not None:
            e = np.asarray(raw_edge, dtype=float)
            if e.shape != (p,):
                e = np.zeros(p)
        else:
            e = np.zeros(p)

        # --- Guard 5: portfolio objective gate (eᵀΣ⁻¹e > cost) ---
        try:
            obj = _portfolio_objective(e, sigma_shrunk, _LAMBDA)
        except np.linalg.LinAlgError as exc:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CORR_HEDGE_REGIME_UNAVAILABLE,
                reason_detail=(
                    f"cross_market_correlation_hedge: linear algebra failure "
                    f"inverting Σ_shrunk for regime={regime!r}: {exc}"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        if obj <= 0.0:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CORR_HEDGE_OBJECTIVE_BELOW_COST,
                reason_detail=(
                    f"cross_market_correlation_hedge: J(w*)={obj:.6f} ≤ 0 "
                    f"for city={city!r} regime={regime!r}. "
                    "DATA-GATED: edge_vector feed not wired → zero edge → no alpha."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Shadow enter ---
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
            edge=obj,  # J(w*) replaces placeholder _SHADOW_EDGE=0.02
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
