# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §6
#                  + docs/reference/zeus_strategy_spec.md §8.2 (model-NO theorem)
#                  + src/calibration/bounds.py (split-conformal p⁺)
"""CenterSellModelNo — calibrated stochastic NO buy (shadow candidate).

Theorem (§6 / §8.2): for finite bin B_i, buy NO at ask b_i when the model
says YES is overpriced. Expected value per share:

    EV^NO_i = 1 − p_i − b_i − phi(b_i)

Application condition uses the calibrated upper bound p⁺ (§0 / §6):

    1 − p⁺_i − b_i − phi(b_i, fee_rate) > 0

where p⁺ = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha)[1]
(split-conformal upper bound — same calibration system as center_buy, upper
bound instead of lower).

NO payoff N_i = 1 − Y_i: if the bin does NOT settle, the NO token pays $1.
Positive EV requires market to overprice the bin more than calibration spread +
taker fee.

Data inputs (pulled via getattr from MarketAnalysisVNext):
  center_sell_model_no_p_hat       — point probability estimate for bin i (float ∈ (0,1))
  center_sell_model_no_no_ask      — executable NO ask price b_i (float ∈ (0,1))
  center_sell_model_no_cal_p_hats  — calibration-set point estimates (Sequence[float])
  center_sell_model_no_cal_outcomes — calibration-set binary outcomes (Sequence[int])
  center_sell_model_no_alpha        — miscoverage rate (float, default 0.10)

Any absent / None field → no_trade CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE.
Edge ≤ 0 after calibration → no_trade CENTER_SELL_MODEL_NO_NO_EDGE.

live_status: shadow only. No evaluator routing. Pipeline-B (calibrated stochastic).
kelly=0. executable_alpha=False.

Proof type: "center_sell_model_no" (distinguishes from parity arb).
Strategy key: "center_sell" (shared with CenterSellParity per §8.4).

INV-37: conn supplied by caller; never auto-opened here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

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

_DEFAULT_ALPHA = 0.10


def _ev_no(p_plus: float, b: Decimal, fee_rate: Decimal) -> Decimal:
    """Compute calibrated NO edge: 1 − p⁺ − b − phi(1, b, fee_rate).

    Args:
        p_plus: calibrated upper bound p⁺ ∈ [0, 1].
        b: NO ask price as Decimal ∈ (0, 1).
        fee_rate: venue taker fee rate.

    Returns:
        Decimal edge (may be negative or zero).
    """
    return Decimal("1") - Decimal(str(p_plus)) - b - phi(Decimal("1"), b, fee_rate)


class CenterSellModelNo(BaseStrategyCandidate):
    """Calibrated stochastic NO buy — model-NO layer of center_sell.

    Complements CenterSellParity (deterministic YES/NO parity) with the
    statistical edge: enter buy_no when calibrated upper bound p⁺ implies
    the market overprices the YES bin.

    proof_type = "center_sell_model_no" (§8.4).
    shadow only; no live routing; kelly=0; executable_alpha=False.
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="center_sell",
                family="center_sell",
                description=(
                    "Calibrated stochastic NO buy — center_sell model-NO layer. "
                    "Enter buy_no iff 1 − p⁺_i − b_i − phi(b_i) > 0. "
                    "p⁺ = calibrated upper bound (split conformal). "
                    "proof_type='center_sell_model_no'. Shadow only; kelly=0. "
                    "Authority: zeus_strategy_spec §8.2, STRATEGY_TAXONOMY_DIRECTIVE §6."
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
        """Evaluate calibrated NO edge for a single bin.

        Guard path (reason=CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE):
          - Any of p_hat, no_ask, cal_p_hats, cal_outcomes absent or None.

        No-trade path (reason=CENTER_SELL_MODEL_NO_NO_EDGE):
          - 1 − p⁺ − b − phi ≤ 0 (calibrated edge non-positive).

        Enter path:
          - outcome="enter", side="buy_no".
          - edge = 1 − p⁺ − b − phi (Decimal, > 0).
          - p_posterior = p⁺ (upper bound stored, not raw p_hat).
          - target_size_usd = None (shadow; no Kelly sizing).
          - Writes ONE shadow decision_events row.

        Never returns None. Never raises; all guard failures become no_trade.
        """
        analysis = context.analysis
        market_slug = context.natural_key[0]

        # --- Pull data-gated inputs via getattr ---
        p_hat_raw: Optional[float] = getattr(analysis, "center_sell_model_no_p_hat", None)
        no_ask_raw: Optional[float] = getattr(analysis, "center_sell_model_no_no_ask", None)
        cal_p_hats: Optional[Sequence[float]] = getattr(
            analysis, "center_sell_model_no_cal_p_hats", None
        )
        cal_outcomes: Optional[Sequence[int]] = getattr(
            analysis, "center_sell_model_no_cal_outcomes", None
        )
        alpha: float = float(
            getattr(analysis, "center_sell_model_no_alpha", _DEFAULT_ALPHA) or _DEFAULT_ALPHA
        )

        # --- Guard: all calibration inputs must be present ---
        if any(v is None for v in (p_hat_raw, no_ask_raw, cal_p_hats, cal_outcomes)):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE,
                reason_detail=(
                    f"center_sell_model_no: calibration inputs absent for "
                    f"market_slug={market_slug!r}; "
                    f"p_hat={p_hat_raw!r}, no_ask={no_ask_raw!r}, "
                    f"cal_p_hats={'present' if cal_p_hats is not None else 'None'}, "
                    f"cal_outcomes={'present' if cal_outcomes is not None else 'None'}."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Compute p⁺ via split-conformal calibration ---
        try:
            _p_lo, p_plus = calibrated_bounds(
                float(p_hat_raw),  # type: ignore[arg-type]
                cal_p_hats,        # type: ignore[arg-type]
                cal_outcomes,      # type: ignore[arg-type]
                alpha=alpha,
            )
        except Exception as exc:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CENTER_SELL_MODEL_NO_CALIBRATION_UNAVAILABLE,
                reason_detail=(
                    f"center_sell_model_no: calibrated_bounds() raised for "
                    f"market_slug={market_slug!r}: {exc!r}"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        fee_rate = venue_fee_rate()
        b = Decimal(str(float(no_ask_raw)))  # type: ignore[arg-type]
        edge = _ev_no(p_plus, b, fee_rate)

        # --- Gate: edge must be strictly positive ---
        if edge <= Decimal("0"):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.CENTER_SELL_MODEL_NO_NO_EDGE,
                reason_detail=(
                    f"center_sell_model_no: edge={edge} ≤ 0 for "
                    f"market_slug={market_slug!r}; "
                    f"p_hat={p_hat_raw}, p⁺={p_plus}, no_ask={no_ask_raw}."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # --- Enter: write shadow decision_events row ---
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
            side="buy_no",
            strategy_key=self.strategy_key,
            conn=conn,
            edge=float(edge),
            p_posterior=p_plus,
            polymarket_end_anchor_source=anchor_source,
        )

        return CandidateDecision(
            outcome="enter",
            side="buy_no",
            edge=edge,
            p_posterior=Decimal(str(p_plus)),
            target_price=b,
            target_size_usd=None,  # shadow; Kelly sizing deferred
        )
