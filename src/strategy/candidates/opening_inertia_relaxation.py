# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §4
#                  + docs/reference/zeus_strategy_spec.md §9 (opening_inertia)
#                  + docs/reference/zeus_math_spec.md §6 (calibration)
"""OpeningInertiaRelaxation — calibrated lower-bound EV on opening price discovery.

Theorem (STRATEGY_TAXONOMY_DIRECTIVE §4):
  After market open, mid-price relaxes exponentially:

      m(t) = p + (m(0) − p) · e^{−λt} + ε_t

  where p is the true probability, m(0) is the opening mid, λ is the
  price-discovery rate.

  Buy YES iff EV⁻(t) = p⁻ − a(t) − φ(a(t)) > 0   (strict)
  Buy NO  iff EV⁻_NO  = 1 − p⁺ − b(t) − φ(b(t)) > 0  (strict)

  where p⁻ = calibrated lower bound (split conformal), p⁺ = upper bound.

  Verifiable params: λ̂, σ_cal, m(0)−p  (NOT win-rate).

Shadow-first: this candidate is SHADOW-ONLY. The existing live evaluator
routing for opening_inertia (evaluator.py:2244-2272) is NOT changed.
This candidate shadow-logs the calibrated-EV path alongside the live
heuristic, providing a comparison cohort for promotion evidence.

Data model: MarketAnalysisVNext provides p_hat, ask, no_ask, cal_p_hats,
cal_outcomes (calibration set from prior settled markets, same source-family).
Optional fields opening_ticks [(t_seconds, mid_price)] and m0 enable λ
estimation; absent these, λ is not estimated (no_trade is NOT forced —
the EV gate still runs without λ context).

live_status: shadow (NEVER live; operator-gated promotion per Evidence Ladder).
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Optional, Sequence, Tuple

from src.calibration.bounds import calibrated_bounds
from src.contracts.no_trade_reason import NoTradeReason
from src.strategy.fees import phi, venue_fee_rate

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    write_candidate_no_trade_row,
)


# ---------------------------------------------------------------------------
# Public: λ estimator
# ---------------------------------------------------------------------------

def estimate_lambda(
    *,
    ticks: Sequence[Tuple[float, float]],
    p_target: float,
) -> Optional[Tuple[float, float, float]]:
    """Estimate price-discovery rate λ from opening mid-price ticks.

    Algorithm: OLS on log|m(t) − p| vs t.

      log|m(t) − p| = log|m(0) − p| − λ · t

    Points where m(t) == p (residual = 0) are dropped (log undefined).
    Requires ≥3 valid points after filtering.

    Args:
        ticks: Sequence of (t_seconds, mid_price) since open. t=0 is open.
        p_target: estimated true probability p (the asymptotic target).

    Returns:
        (lambda_hat, sigma_cal, t_half) or None if estimation fails.
        - lambda_hat: estimated decay rate (λ̂), positive.
        - sigma_cal: calibration uncertainty = std dev of OLS residuals / √n.
        - t_half: half-life = ln(2) / λ̂.

    OLS derivation:
        Let y_i = log|m(tᵢ) − p|, x_i = tᵢ.
        Fit y = a − λ·x → slope = −λ̂.
        σ_cal = std(residuals) / sqrt(n).
    """
    # Filter: drop where m(t) == p_target (log undefined)
    valid = [
        (t, mid)
        for t, mid in ticks
        if mid != p_target and abs(mid - p_target) > 1e-12
    ]

    if len(valid) < 3:
        return None

    # OLS on (t, log|m−p|)
    log_y = [math.log(abs(mid - p_target)) for t, mid in valid]
    t_vals = [t for t, mid in valid]
    n = len(valid)

    t_mean = sum(t_vals) / n
    y_mean = sum(log_y) / n

    ss_xx = sum((t - t_mean) ** 2 for t in t_vals)
    ss_xy = sum((t_vals[i] - t_mean) * (log_y[i] - y_mean) for i in range(n))

    if ss_xx == 0.0:
        return None

    slope = ss_xy / ss_xx   # = −λ̂
    intercept = y_mean - slope * t_mean

    lambda_hat = -slope
    if lambda_hat <= 0:
        # Non-decaying: cannot estimate meaningful λ
        return None

    # σ_cal from OLS residuals
    residuals = [log_y[i] - (intercept + slope * t_vals[i]) for i in range(n)]
    mse = sum(r ** 2 for r in residuals) / n
    sigma_cal = math.sqrt(mse) / math.sqrt(n)

    t_half = math.log(2) / lambda_hat

    return lambda_hat, sigma_cal, t_half


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------

class OpeningInertiaRelaxation(BaseStrategyCandidate):
    """Shadow candidate: calibrated lower-bound EV on opening price discovery.

    Shadow-first per STRATEGY_TAXONOMY_DIRECTIVE §4.
    Does NOT alter live evaluator routing; shadow-logs alongside heuristic live path.
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="opening_inertia_relaxation",
                family="opening_inertia",
                description=(
                    "Shadow candidate: exponential price-discovery relaxation model. "
                    "Buy YES iff p⁻ − ask − phi > 0; buy NO iff 1 − p⁺ − noAsk − phi > 0. "
                    "Uses split-conformal calibrated bounds. Verifiable params: λ, σ_cal, m(0)−p. "
                    "Data-gated on calibration set; λ estimation optional."
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
        """Evaluate calibrated EV gate for opening_inertia relaxation.

        Gate 1: Calibration set must be non-empty → INSUFFICIENT_VERIFIED_CALIBRATION.
        Gate 2: Compute p⁻ / p⁺ via split conformal calibrated_bounds().
        Gate 3: YES buy iff p⁻ − ask − phi(ask) > 0 (strict).
                NO buy iff 1 − p⁺ − noAsk − phi(noAsk) > 0 (strict).
                Neither positive → no_trade(CONFIDENCE_BAND_INSUFFICIENT).
        Shadow log: reason_detail carries λ̂, σ_cal, m(0)−p for regret decomposition.

        Never returns None.
        """
        analysis = context.analysis

        # ── Extract inputs ──────────────────────────────────────────────────
        p_hat: float = float(getattr(analysis, "p_hat", 0.5))
        ask_raw = getattr(analysis, "ask", None)
        no_ask_raw = getattr(analysis, "no_ask", None)
        cal_p_hats: list[float] = list(getattr(analysis, "cal_p_hats", []))
        cal_outcomes: list[int] = list(getattr(analysis, "cal_outcomes", []))
        opening_ticks = getattr(analysis, "opening_ticks", None) or []
        m0_raw = getattr(analysis, "m0", None)

        # ── Gate 1: calibration set must be non-empty ────────────────────────
        if not cal_p_hats or not cal_outcomes:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.INSUFFICIENT_VERIFIED_CALIBRATION,
                reason_detail=(
                    "opening_inertia_relaxation: calibration set empty; "
                    "cannot compute p⁻/p⁺ without calibration data. "
                    "No fallback to raw p_hat (theorem requires calibrated bound)."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Gate 2: compute calibrated bounds ───────────────────────────────
        try:
            p_lo, p_hi = calibrated_bounds(
                p_hat, cal_p_hats, cal_outcomes, alpha=0.10
            )
        except (ValueError, Exception) as exc:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.INSUFFICIENT_VERIFIED_CALIBRATION,
                reason_detail=(
                    f"opening_inertia_relaxation: calibrated_bounds() failed: {exc}; "
                    "no_trade to preserve theorem integrity."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── λ estimation (optional; enriches shadow log) ─────────────────────
        lambda_result = None
        if opening_ticks and len(opening_ticks) >= 3:
            lambda_result = estimate_lambda(ticks=opening_ticks, p_target=p_hat)

        lambda_hat: Optional[float] = lambda_result[0] if lambda_result else None
        sigma_cal_est: Optional[float] = lambda_result[1] if lambda_result else None
        t_half: Optional[float] = lambda_result[2] if lambda_result else None
        m0_minus_p: Optional[float] = (
            float(m0_raw) - p_hat if m0_raw is not None else None
        )

        fee_rate = venue_fee_rate()

        # ── Gate 3a: YES buy EV⁻ = p⁻ − ask − phi(ask) ─────────────────────
        yes_enter = False
        yes_edge: Optional[Decimal] = None
        if ask_raw is not None:
            ask_d = Decimal(str(ask_raw))
            fee_yes = phi(Decimal("1"), ask_d, fee_rate)
            ev_yes = Decimal(str(p_lo)) - ask_d - fee_yes
            if ev_yes > Decimal("0"):
                yes_enter = True
                yes_edge = ev_yes

        # ── Gate 3b: NO buy EV⁻_NO = 1 − p⁺ − noAsk − phi(noAsk) ──────────
        no_enter = False
        no_edge: Optional[Decimal] = None
        if no_ask_raw is not None:
            no_ask_d = Decimal(str(no_ask_raw))
            fee_no = phi(Decimal("1"), no_ask_d, fee_rate)
            ev_no = Decimal("1") - Decimal(str(p_hi)) - no_ask_d - fee_no
            if ev_no > Decimal("0"):
                no_enter = True
                no_edge = ev_no

        # ── Build verifiable-params detail string ────────────────────────────
        lambda_str = f"{lambda_hat:.6f}" if lambda_hat is not None else "not_estimated"
        sigma_str = f"{sigma_cal_est:.6f}" if sigma_cal_est is not None else "not_estimated"
        m0_str = f"{m0_minus_p:.6f}" if m0_minus_p is not None else "not_observed"
        t_half_str = f"{t_half:.4f}s" if t_half is not None else "not_estimated"

        params_detail = (
            f"lambda={lambda_str}; sigma_cal={sigma_str}; "
            f"m(0)_minus_p={m0_str}; t_half={t_half_str}; "
            f"p_hat={p_hat:.6f}; p_lo={p_lo:.6f}; p_hi={p_hi:.6f}"
        )

        # ── Prefer YES enter; fall through to NO; else no_trade ──────────────
        if yes_enter:
            side = "buy_yes"
            edge = yes_edge
            p_posterior = Decimal(str(p_lo))
            decision = CandidateDecision(
                outcome="enter",
                side=side,
                edge=edge,
                p_posterior=p_posterior,
                reason_detail=params_detail,
            )
            # Shadow log the enter
            try:
                from src.state.decision_events import write_shadow_decision_event
                write_shadow_decision_event(
                    context.natural_key,
                    decision_time=decision_time.isoformat(),
                    side=side,
                    strategy_key="opening_inertia_relaxation",
                    conn=conn,
                    edge=float(edge) if edge is not None else None,
                    p_posterior=float(p_posterior),
                )
            except Exception:
                # Shadow log failure is non-fatal; theorem decision stands.
                pass
            return decision

        if no_enter:
            side = "buy_no"
            edge = no_edge
            p_posterior = Decimal(str(p_hi))
            decision = CandidateDecision(
                outcome="enter",
                side=side,
                edge=edge,
                p_posterior=p_posterior,
                reason_detail=params_detail,
            )
            try:
                from src.state.decision_events import write_shadow_decision_event
                write_shadow_decision_event(
                    context.natural_key,
                    decision_time=decision_time.isoformat(),
                    side=side,
                    strategy_key="opening_inertia_relaxation",
                    conn=conn,
                    edge=float(edge) if edge is not None else None,
                    p_posterior=float(p_posterior),
                )
            except Exception:
                pass
            return decision

        # ── No positive edge on either side ─────────────────────────────────
        decision = CandidateDecision(
            outcome="no_trade",
            reason=NoTradeReason.CONFIDENCE_BAND_INSUFFICIENT,
            reason_detail=(
                f"opening_inertia_relaxation: no positive EV on YES or NO side. "
                f"{params_detail}"
            ),
        )
        write_candidate_no_trade_row(conn, context, decision)
        return decision
