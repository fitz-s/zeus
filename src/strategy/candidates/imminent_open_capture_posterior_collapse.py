# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §9
#                  + docs/reference/zeus_strategy_spec.md §10
"""ImminentOpenCapturePosteriorCollapse — short-horizon posterior-collapse arbitrage (shadow).

Theorem (§9 / §10.2):
  T* = μ_t + η_t,  Var(η_t) = σ²(τ) ↓ 0 as τ ↓ 0.

  Closer to resolution → tighter calibration interval [p⁻, p⁺] → binds gate harder.

  YES entry: p⁻_i(t) − a_i − phi(1, a_i, fee_rate) > 0
  NO  entry: 1 − p⁺_i(t) − b_i − phi(1, b_i, fee_rate) > 0

  where p⁻, p⁺ = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=0.10).

Calibration via split conformal (src/calibration/bounds.py):
  - Provides exact finite-sample marginal coverage guarantee.
  - Empty cal set → ValueError → fail-closed no_trade (IMMINENT_CALIBRATION_UNAVAILABLE).

DATA-GATED fields on analysis (emit no_trade when absent):
  - analysis.p_hat            (float)
  - analysis.ask              (float — YES ask a_i)
  - analysis.bid              (float — NO ask b_i)
  - analysis.cal_p_hats       (list[float] — calibration set estimates)
  - analysis.cal_outcomes     (list[int]   — calibration set outcomes)

SHADOW-FIRST: imminent_open_capture is LIVE; this candidate is shadow-only.
  - NEVER change live entry/sizing/Kelly/routing.
  - Shadow rows written to decision_events with source='shadow_decision'.
  - live_status: shadow (operator-gated promotion only).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Optional

from src.calibration.bounds import calibrated_bounds
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

_STRATEGY_KEY = "imminent_open_capture_posterior_collapse"
_ALPHA = 0.10  # 90% conformal coverage; per §9 default


class ImminentOpenCapturePosteriorCollapse(BaseStrategyCandidate):
    """Shadow candidate: short-horizon posterior-collapse arbitrage (§9 / §10).

    Edge source: as τ → 0, σ²(τ) → 0, so calibration interval [p⁻, p⁺] shrinks.
    Trade when p⁻ − ask − phi > 0 (YES) or 1 − p⁺ − bid − phi > 0 (NO).

    DATA-GATED: analysis fields cal_p_hats/cal_outcomes/p_hat/ask/bid must be
    present; empty or absent → fail-closed no_trade.

    live_status: shadow. SHADOW-FIRST — never routes to live execution.
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key=_STRATEGY_KEY,
                family="imminent_open_capture",
                description=(
                    "Shadow candidate: short-horizon posterior-collapse arb. "
                    "Trade YES iff p⁻−ask−phi>0, NO iff 1−p⁺−bid−phi>0. "
                    "p⁻/p⁺ from split-conformal calibrated_bounds(). "
                    "Data-gated: no_trade until cal feed is wired."
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
        """Evaluate §9 posterior-collapse theorem for this market observation.

        No-trade paths:
          - analysis is None (data-gated; all fields absent).
          - cal_p_hats is empty or absent (calibration unavailable → fail-closed).
          - calibrated_bounds() raises (invalid inputs → fail-closed).
          - Neither YES gate (p⁻ − ask − phi > 0) nor NO gate (1−p⁺−bid−phi > 0) met.

        Enter path: first gate that passes wins; YES preferred over NO on tie
        (asymmetric only if BOTH pass, which is rare for well-calibrated estimates).

        Never returns None.
        """
        analysis = context.analysis

        # Gate 0: analysis or required fields absent → fail-closed
        if analysis is None:
            return self._no_trade(
                conn, context,
                NoTradeReason.IMMINENT_CALIBRATION_UNAVAILABLE,
                "imminent_open_capture_posterior_collapse: analysis=None; data-gated.",
            )

        p_hat_raw = getattr(analysis, "p_hat", None)
        ask_raw = getattr(analysis, "ask", None)
        bid_raw = getattr(analysis, "bid", None)
        cal_p_hats_raw = getattr(analysis, "cal_p_hats", None)
        cal_outcomes_raw = getattr(analysis, "cal_outcomes", None)

        if any(v is None for v in (p_hat_raw, ask_raw, bid_raw, cal_p_hats_raw, cal_outcomes_raw)):
            return self._no_trade(
                conn, context,
                NoTradeReason.IMMINENT_CALIBRATION_UNAVAILABLE,
                (
                    "imminent_open_capture_posterior_collapse: required field(s) absent "
                    f"(p_hat={p_hat_raw}, ask={ask_raw}, bid={bid_raw}, "
                    f"cal_p_hats={'present' if cal_p_hats_raw is not None else 'None'}, "
                    f"cal_outcomes={'present' if cal_outcomes_raw is not None else 'None'}); "
                    "data-gated."
                ),
            )

        # Gate 1: compute calibrated bounds (fail-closed on ValueError)
        try:
            p_lo, p_hi = calibrated_bounds(
                float(p_hat_raw),
                [float(v) for v in cal_p_hats_raw],
                [int(v) for v in cal_outcomes_raw],
                alpha=_ALPHA,
            )
        except (ValueError, TypeError) as exc:
            return self._no_trade(
                conn, context,
                NoTradeReason.IMMINENT_CALIBRATION_UNAVAILABLE,
                f"imminent_open_capture_posterior_collapse: calibrated_bounds failed: {exc}",
            )

        p_lower = Decimal(str(p_lo))
        p_upper = Decimal(str(p_hi))

        fee_rate = venue_fee_rate()

        ask = Decimal(str(ask_raw))
        bid = Decimal(str(bid_raw))

        # Gate 2: YES — p⁻ − ask − phi(1, ask, fee_rate) > 0
        yes_edge: Optional[Decimal] = None
        if Decimal("0") < ask < Decimal("1"):
            fee_yes = phi(Decimal("1"), ask, fee_rate)
            yes_edge = p_lower - ask - fee_yes

        # Gate 3: NO — 1 − p⁺ − bid − phi(1, bid, fee_rate) > 0
        no_edge: Optional[Decimal] = None
        if Decimal("0") < bid < Decimal("1"):
            fee_no = phi(Decimal("1"), bid, fee_rate)
            no_edge = Decimal("1") - p_upper - bid - fee_no

        # Pick best positive edge; YES preferred on tie
        best_side: Optional[str] = None
        best_edge: Optional[Decimal] = None

        if yes_edge is not None and yes_edge > Decimal("0"):
            best_side = "buy_yes"
            best_edge = yes_edge

        if no_edge is not None and no_edge > Decimal("0"):
            if best_edge is None or no_edge > best_edge:
                best_side = "buy_no"
                best_edge = no_edge

        if best_side is None or best_edge is None:
            return self._no_trade(
                conn, context,
                NoTradeReason.IMMINENT_NO_EDGE,
                (
                    f"imminent_open_capture_posterior_collapse: "
                    f"p⁻={float(p_lower):.4f}, p⁺={float(p_upper):.4f}, "
                    f"ask={float(ask):.4f}, bid={float(bid):.4f}; "
                    f"yes_edge={float(yes_edge) if yes_edge is not None else 'N/A':.4f}, "
                    f"no_edge={float(no_edge) if no_edge is not None else 'N/A':.4f}; "
                    "neither gate positive."
                ),
            )

        # Enter: write shadow row
        write_shadow_decision_event(
            context.natural_key,
            decision_time=decision_time.isoformat(),
            side=best_side,
            strategy_key=_STRATEGY_KEY,
            conn=conn,
            edge=float(best_edge),
            p_posterior=float(p_lower if best_side == "buy_yes" else (Decimal("1") - p_upper)),
            target_size_usd=None,   # shadow — no live sizing
            target_price=float(ask if best_side == "buy_yes" else bid),
        )

        return CandidateDecision(
            outcome="enter",
            side=best_side,
            edge=best_edge,
            p_posterior=p_lower if best_side == "buy_yes" else (Decimal("1") - p_upper),
            target_price=ask if best_side == "buy_yes" else bid,
            target_size_usd=None,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _no_trade(
        self,
        conn: sqlite3.Connection,
        context: CandidateContext,
        reason: NoTradeReason,
        reason_detail: str,
    ) -> CandidateDecision:
        decision = CandidateDecision(
            outcome="no_trade",
            reason=reason,
            reason_detail=reason_detail,
        )
        write_candidate_no_trade_row(conn, context, decision)
        return decision
