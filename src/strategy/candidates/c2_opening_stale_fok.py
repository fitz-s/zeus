# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §14
#                  + docs/reference/zeus_strategy_spec.md combination sections
"""C2 — opening_inertia × stale_quote opening-stale-FOK (§14).

THEOREM (STRATEGY_TAXONOMY_DIRECTIVE.md §14):
  At open, mid m0 unrelaxed + posterior p exists;
  if orderbook hash unchanged AND ask a0 < calibrated lower bound:

      EV = Pr(F) · (p⁻ − a0 − phi(a0))

  FOK → no-fill produces 0 loss; Pr(F) affects volume not edge sign.
  Enter iff p⁻ − a0 − phi(a0) > 0   (Pr(F) > 0 assumed; does not change sign).

MATH REUSE:
  - Opening posterior p⁻: calibrated_bounds() from src.calibration.bounds
    (same as opening_inertia_relaxation; does NOT re-implement conformal logic)
  - Book staleness check: _is_book_hash_stale() from stale_quote_detector module
    (imports the helper directly; does NOT re-implement hash-stale logic)
  - Fee: phi() from src.strategy.fees

DATA-GATED:
  Both opening posterior (calibration set) AND stale-quote inputs must be wired:
    - cal_p_hats / cal_outcomes absent / empty → OPENING_STALE_FOK_UNWIRED
    - info_event_observed=False → OPENING_STALE_FOK_UNWIRED (stale-quote side not wired)
    - stale_quote_price=None → OPENING_STALE_FOK_UNWIRED
    - p⁻ − a0 − phi ≤ 0 → OPENING_STALE_FOK_NO_EDGE

SHADOW-FIRST (operator directive 2026-05-22):
  executable_alpha=False — no live trades until operator promotes.
  kelly=0: no sizing. Registered via Pipeline B (calibrated stochastic promotion).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from src.calibration.bounds import calibrated_bounds
from src.contracts.no_trade_reason import NoTradeReason
from src.strategy.candidates.stale_quote_detector import _is_book_hash_stale
from src.strategy.fees import phi, venue_fee_rate

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    write_candidate_no_trade_row,
)

_STRATEGY_KEY = "c2_opening_stale_fok"

# Conformal miscoverage rate. 0.10 → 90% marginal coverage.
_DEFAULT_ALPHA: float = 0.10

_ONE_SHARE = Decimal("1")


from dataclasses import dataclass, field as _field


@dataclass(frozen=True)
class C2OpeningStaleDecision:
    """Extended decision carrying §14 proof fields for shadow observability.

    outcome is always "enter" for this class.
    """

    outcome: str = _field(default="enter", init=False)
    side: str = "buy_yes"
    strategy_key: str = _STRATEGY_KEY
    p_lower_bound: Decimal = Decimal("0")
    stale_ask: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    edge: Decimal = Decimal("0")
    # CandidateDecision-compatible aliases used by callers that read these fields
    p_posterior: Decimal = Decimal("0")
    target_price: Decimal = Decimal("0")


class OpeningStaleQuoteFOK(BaseStrategyCandidate):
    """C2: opening_inertia × stale_quote opening-stale-FOK shadow candidate (§14).

    Combines opening_inertia prediction edge (calibrated lower-bound on posterior p)
    with stale_quote latency edge (book hash unchanged after info event).

    EV = Pr(F) · (p⁻ − a0 − phi(a0)).
    FOK no-fill payoff = 0 (no position taken, no loss).
    Sign of edge is determined solely by (p⁻ − a0 − phi(a0)).

    Reads from context.analysis:
      Opening-inertia side:
        - p_hat: float            — posterior point estimate
        - cal_p_hats: List[float] — conformal calibration set point estimates
        - cal_outcomes: List[int] — conformal calibration outcomes
        - ask: float              — YES ask price at open (unrelaxed mid context)

      Stale-quote side (on analysis directly, not analysis.metrics):
        - info_event_observed: bool          — canonical InfoEvent observed
        - stale_quote_price: Optional[Decimal] — executable stale ask a0
        - book_hash: Optional[str]           — current book hash
        - book_hash_transition_delta_ms: Optional[int] — ms since last hash change

    Data-gate: missing calibration OR missing stale-quote inputs → OPENING_STALE_FOK_UNWIRED.
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key=_STRATEGY_KEY,
                family="c2_opening_stale_fok",
                description=(
                    "Shadow candidate: opening-stale-FOK combining opening_inertia "
                    "calibrated posterior p⁻ with stale_quote book-hash check (§14). "
                    "EV = Pr(F)·(p⁻ − a0 − phi(a0)). FOK no-fill → 0 loss. "
                    "DATA-GATED: both opening calibration and stale-quote feed must be wired."
                ),
                executable_alpha=False,
            )
        )

    def evaluate(
        self,
        *,
        context: CandidateContext,
        conn: sqlite3.Connection,
        decision_time: datetime,
    ) -> CandidateDecision:
        """Evaluate opening-stale-FOK entry condition (§14).

        Returns:
          CandidateDecision(enter) if p⁻ − a0 − phi > 0 and all inputs wired (shadow enter).
          CandidateDecision(no_trade, OPENING_STALE_FOK_UNWIRED) when inputs absent.
          CandidateDecision(no_trade, OPENING_STALE_FOK_NO_EDGE) when edge ≤ 0.
        """
        analysis = context.analysis

        # ── Gate 1: opening posterior — calibration set must be present ───────
        p_hat: float = float(getattr(analysis, "p_hat", 0.5))
        cal_p_hats: List[float] = list(getattr(analysis, "cal_p_hats", []) or [])
        cal_outcomes: List[int] = list(getattr(analysis, "cal_outcomes", []) or [])

        if not cal_p_hats or not cal_outcomes:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.OPENING_STALE_FOK_UNWIRED,
                reason_detail=(
                    "c2_opening_stale_fok data-gated (opening side): "
                    "calibration set empty; cannot compute p⁻ for opening-inertia component. "
                    "No fallback to raw p_hat (theorem requires calibrated bound)."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Gate 2: stale-quote inputs — info event + stale price ─────────────
        info_event_observed: bool = bool(getattr(analysis, "info_event_observed", False))
        stale_price_raw = getattr(analysis, "stale_quote_price", None)
        a0: Optional[Decimal] = (
            Decimal(str(stale_price_raw)) if stale_price_raw is not None else None
        )

        if not info_event_observed or a0 is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.OPENING_STALE_FOK_UNWIRED,
                reason_detail=(
                    "c2_opening_stale_fok data-gated (stale-quote side): "
                    f"info_event_observed={info_event_observed}, "
                    f"stale_quote_price={'present' if a0 is not None else 'MISSING'}; "
                    "data-gated until info-event feed and quote capture wired."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Gate 3: book hash staleness check ────────────────────────────────
        book_hash: Optional[str] = getattr(analysis, "book_hash", None)
        hash_delta_ms: Optional[int] = getattr(analysis, "book_hash_transition_delta_ms", None)
        stale = _is_book_hash_stale(
            book_hash=book_hash,
            book_hash_transition_delta_ms=hash_delta_ms,
        )
        if not stale:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.OPENING_STALE_FOK_UNWIRED,
                reason_detail=(
                    "c2_opening_stale_fok: book hash has responded to info event; "
                    "stale-quote edge is gone (book updated)."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Compute p⁻ via split conformal ───────────────────────────────────
        try:
            p_lo, _ = calibrated_bounds(
                p_hat, cal_p_hats, cal_outcomes, alpha=_DEFAULT_ALPHA
            )
        except (ValueError, Exception) as exc:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.OPENING_STALE_FOK_UNWIRED,
                reason_detail=(
                    f"c2_opening_stale_fok: calibrated_bounds() failed: {exc}; "
                    "no_trade to preserve theorem integrity."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        p_lower = Decimal(str(round(p_lo, 10)))

        # ── Fee + EV computation ──────────────────────────────────────────────
        # EV = Pr(F) · (p⁻ − a0 − phi(a0))
        # Sign determined solely by (p⁻ − a0 − phi(a0)); Pr(F) > 0 assumed.
        if not (Decimal("0") < a0 < Decimal("1")):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.OPENING_STALE_FOK_NO_EDGE,
                reason_detail=(
                    f"c2_opening_stale_fok: a0={a0} outside (0,1); phi undefined."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        fee_rate = venue_fee_rate()
        fee = phi(shares=_ONE_SHARE, price=a0, fee_rate=fee_rate)
        edge = p_lower - a0 - fee

        if edge <= Decimal("0"):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.OPENING_STALE_FOK_NO_EDGE,
                reason_detail=(
                    f"c2_opening_stale_fok: p_lower={p_lower}, "
                    f"a0={a0}, fee={fee}, edge={edge} ≤ 0; "
                    "opening calibrated lower bound does not beat stale ask + fee"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Shadow enter ──────────────────────────────────────────────────────
        from src.state.decision_events import write_shadow_decision_event

        decision_time_iso = (
            decision_time.replace(tzinfo=timezone.utc).isoformat()
            if decision_time.tzinfo is None
            else decision_time.isoformat()
        )
        write_shadow_decision_event(
            context.natural_key,
            decision_time=decision_time_iso,
            side="buy_yes",
            strategy_key=_STRATEGY_KEY,
            conn=conn,
            edge=float(edge),
            p_posterior=float(p_lower),
            target_price=float(a0),
        )

        return C2OpeningStaleDecision(
            p_lower_bound=p_lower,
            stale_ask=a0,
            fee=fee,
            edge=edge,
            p_posterior=p_lower,
            target_price=a0,
        )
