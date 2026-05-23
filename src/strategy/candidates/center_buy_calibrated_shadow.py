# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §5
#                  + src/strategy/candidates/__init__.py (CandidateDecision / CandidateContext)
#                  + src/calibration/bounds.py (calibrated_bounds — split conformal)
#                  + src/strategy/fees.py (phi, venue_fee_rate)
"""CenterBuyCalibratedShadow — §5 calibrated multinomial EV shadow candidate.

THEOREM (§5):
  Bins are exhaustive: Σ p_i = 1.
  Trade i* = argmax_i [ p⁻_i − a_i − ϕ(a_i) ]
    where p⁻_i = inf{ p_i : p_i in calibrated conformal set }
                = calibrated_bounds(p̂_i, cal_p_hats, cal_outcomes, alpha)[0]
  Enter only if p⁻_{i*} − a_{i*} − ϕ(a_{i*}) > 0.
  Calibration unavailable / insufficient → emit no_trade (this IS correct behavior).

DECISION TYPE: CandidateDecision (stochastic path). Do NOT use DeterministicEdgeDecision.
  Populate: side = token_id of i*-bin; edge = Decimal(p⁻_{i*} − a_{i*} − ϕ).

SHADOW-FIRST: Does NOT modify evaluator.py or promotion_proof_router.py.
  live_status: shadow (NEVER live until F3-B evidence gates promotion).
  Writes shadow decision_events row on enter via write_shadow_decision_event.
  Writes no_trade_events row on no_trade via write_candidate_no_trade_row.

Analysis contract (via SimpleNamespace or any object with these attrs):
  multinomial_bins: list[dict] | None
    Each dict: {"p_hat": float, "ask": float, "token_id": str}
    One entry per center bin in the current family. Shoulder bins excluded.
  cal_p_hats: list[float] | None   — calibration set posteriors (split, held-out)
  cal_outcomes: list[int] | None   — calibration set binary outcomes (0/1)
  fee_rate: Decimal | None         — taker fee rate (uses venue_fee_rate() if None)
  alpha: float | None              — conformal coverage level (default 0.10)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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

# Minimum calibration set size required; fewer pairs → p⁻ is unreliable.
_MIN_CAL_PAIRS: int = 30

# Default conformal coverage level (alpha=0.10 → 90% marginal coverage).
_DEFAULT_ALPHA: float = 0.10


@dataclass(frozen=True)
class _BinEV:
    """Computed calibrated EV for a single center bin."""
    token_id: str
    p_hat: float
    p_lower: float   # p⁻ from split conformal
    ask: float
    fee: float       # ϕ(1 share, ask, fee_rate)
    ev: float        # p⁻ − ask − fee


class CenterBuyCalibratedShadow(BaseStrategyCandidate):
    """§5 calibrated multinomial EV shadow candidate for center_buy.

    Reads per-bin (p̂, ask, token_id) from analysis.multinomial_bins and
    a held-out calibration set from analysis.cal_p_hats / cal_outcomes.
    Computes p⁻_i via split conformal calibrated_bounds(), then selects
    i* = argmax_i [ p⁻_i − a_i − ϕ(a_i) ].

    No evaluator.py changes. No promotion_proof_router.py changes.
    live_status: shadow until operator-gated F3-B promotion.
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="center_buy",
                family="center_buy",
                description=(
                    "Shadow: §5 calibrated multinomial EV. "
                    "i* = argmax_i [ p⁻_i − a_i − ϕ(a_i) ]. "
                    "p⁻ from split conformal calibrated_bounds(). "
                    "Calibration unavailable → no_trade. Shadow-only."
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
        """Evaluate the §5 calibrated multinomial EV theorem.

        Enter path: p⁻_{i*} − a_{i*} − ϕ > 0 for some bin.
          → CandidateDecision(outcome='enter', side=token_id, edge=calibrated_ev)
          → writes shadow decision_events row.

        No-trade paths:
          INSUFFICIENT_VERIFIED_CALIBRATION — cal_p_hats absent or too small.
          CENTER_BUY_DATA_GATE             — multinomial_bins absent or empty.
          CENTER_BUY_NO_POSITIVE_EV        — all bins: p⁻_i − a_i − ϕ ≤ 0.

        Never returns None.
        """
        analysis = context.analysis

        # ── Calibration gate ─────────────────────────────────────────────────
        cal_p_hats = getattr(analysis, "cal_p_hats", None)
        cal_outcomes = getattr(analysis, "cal_outcomes", None)
        if (
            cal_p_hats is None
            or cal_outcomes is None
            or len(cal_p_hats) < _MIN_CAL_PAIRS
            or len(cal_outcomes) < _MIN_CAL_PAIRS
        ):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.INSUFFICIENT_VERIFIED_CALIBRATION,
                reason_detail=(
                    f"center_buy_calibrated_shadow: calibration set absent or "
                    f"insufficient (n={len(cal_p_hats) if cal_p_hats else 0}, "
                    f"min={_MIN_CAL_PAIRS}). §5 requires split conformal p⁻; "
                    "no_trade is the correct behavior when calibration is unavailable."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Bin data gate ────────────────────────────────────────────────────
        multinomial_bins = getattr(analysis, "multinomial_bins", None)
        if not multinomial_bins:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.INSUFFICIENT_VERIFIED_CALIBRATION,
                reason_detail=(
                    "center_buy_calibrated_shadow: analysis.multinomial_bins absent "
                    "or empty. Cannot apply §5 argmax without per-bin (p̂, ask, token_id)."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Fee rate ─────────────────────────────────────────────────────────
        fee_rate_raw = getattr(analysis, "fee_rate", None)
        fee_rate: Decimal = fee_rate_raw if fee_rate_raw is not None else venue_fee_rate()

        # ── Alpha ────────────────────────────────────────────────────────────
        alpha: float = float(getattr(analysis, "alpha", None) or _DEFAULT_ALPHA)

        # ── Per-bin calibrated EV computation ────────────────────────────────
        bin_evs: list[_BinEV] = []
        for b in multinomial_bins:
            p_hat: float = float(b["p_hat"])
            ask: float = float(b["ask"])
            token_id: str = str(b["token_id"])

            try:
                p_lo, _p_hi = calibrated_bounds(
                    p_hat, cal_p_hats, cal_outcomes, alpha=alpha
                )
            except Exception:
                # calibrated_bounds raised (e.g. degenerate input); skip this bin
                continue

            ask_dec = Decimal(str(ask))
            fee = float(phi(Decimal("1"), ask_dec, fee_rate))
            ev = p_lo - ask - fee
            bin_evs.append(_BinEV(
                token_id=token_id,
                p_hat=p_hat,
                p_lower=p_lo,
                ask=ask,
                fee=fee,
                ev=ev,
            ))

        if not bin_evs:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.INSUFFICIENT_VERIFIED_CALIBRATION,
                reason_detail=(
                    "center_buy_calibrated_shadow: all bins failed calibrated_bounds "
                    "computation (degenerate inputs). Cannot determine i*."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── argmax over calibrated EV ────────────────────────────────────────
        best: _BinEV = max(bin_evs, key=lambda b: b.ev)

        if best.ev <= 0.0:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.INSUFFICIENT_VERIFIED_CALIBRATION,
                reason_detail=(
                    f"center_buy_calibrated_shadow: no bin has positive calibrated EV. "
                    f"Best i*={best.token_id}: p⁻={best.p_lower:.4f} − "
                    f"a={best.ask:.4f} − ϕ={best.fee:.4f} = {best.ev:.4f} ≤ 0."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Enter: write shadow decision_events row ───────────────────────────
        decision_time_iso = _to_utc_iso(decision_time)
        write_shadow_decision_event(
            context.natural_key,
            decision_time=decision_time_iso,
            side="buy_yes",
            strategy_key=self.strategy_key,
            conn=conn,
            edge=best.ev,
            p_posterior=best.p_lower,
            target_price=best.ask,
        )

        return CandidateDecision(
            outcome="enter",
            side=best.token_id,       # token_id of the best bin
            edge=Decimal(str(round(best.ev, 8))),
            p_posterior=Decimal(str(round(best.p_lower, 8))),
            target_price=Decimal(str(best.ask)),
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()
