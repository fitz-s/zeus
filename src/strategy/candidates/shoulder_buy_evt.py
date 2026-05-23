# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §8
#                  + docs/reference/zeus_strategy_spec.md §12
#                  + src/calibration/bounds.py (conformal lower bound)
"""ShoulderBuyEVT — nonstationary EVT / conformal tail-underpricing shadow candidate.

THEOREM (STRATEGY_TAXONOMY_DIRECTIVE.md §8):
  open upper shoulder B=[u,∞); tail event p_u = Pr(T>u | X).
  Nonstationary tail model: p_u(X) = 1 − F_θ(u | X)
  Covariates X (continuous, no discrete regime flags):
    ensemble_mean, ensemble_spread, temp_anomaly_850mb,
    soil_moisture_proxy, advection, station_bias,
    season_harmonic_sin, season_harmonic_cos

  Conformal lower bound via split conformal (src/calibration/bounds.py):
    p⁻_u = calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha).lo
  Coverage guarantee: Pr(Y=1 | p⁻_u ≥ q) ≥ q (frequentist marginal).

  Entry condition: p⁻_u − a_YES − phi(1, a_YES, fee_rate) > 0

DATA-GATED:
  EVT tail model covariates, calibrated tail probability, and the
  conformal calibration set are NOT yet wired from the data pipeline.
  → emits EVT_TAIL_MODEL_UNWIRED no_trade until wired.

SHADOW-FIRST per operator directive 2026-05-22:
  executable_alpha=False — no live trades until operator promotes.

§12.5 decision fields:
  strategy_key: shoulder_buy
  proof_type: calibrated_tail_yes
  p_tail_raw: raw EVT tail probability before conformal
  p_tail_lower_bound: calibrated lower bound p⁻_u
  native_yes_ask: YES ask price
  fee: phi(1, native_yes_ask, fee_rate)
  edge: p⁻_u − native_yes_ask − fee  (= edge_lower_bound in spec)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple, Union

from src.calibration.bounds import calibrated_bounds
from src.contracts.no_trade_reason import NoTradeReason
from src.strategy.fees import phi, venue_fee_rate

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
)

_STRATEGY_KEY = "shoulder_buy"
_PROOF_TYPE = "calibrated_tail_yes"

# Conformal miscoverage rate.  0.10 → 90% marginal coverage.
# Conservative default; operator can tune after tail model is wired.
_DEFAULT_ALPHA: float = 0.10

# Shares = 1 for the single-leg YES taker entry.
_ONE_SHARE = Decimal("1")


@dataclass(frozen=True)
class ShoulderBuyDecision:
    """Extended decision dataclass carrying §12.5 proof fields.

    Replaces a plain CandidateDecision for the enter path so that
    p_tail_raw, p_tail_lower_bound, and fee are surfaced for shadow
    observability and future promotion validation.

    outcome is always "enter" for this class.
    """

    outcome: str = field(default="enter", init=False)
    side: str = "buy_yes"
    strategy_key: str = _STRATEGY_KEY
    proof_type: str = _PROOF_TYPE
    p_tail_raw: Decimal = Decimal("0")
    p_tail_lower_bound: Decimal = Decimal("0")
    native_yes_ask: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    edge: Decimal = Decimal("0")


class ShoulderBuyEVT(BaseStrategyCandidate):
    """Nonstationary EVT / conformal tail-underpricing shadow candidate.

    Reads the following fields from context.analysis:
      - native_yes_ask: Optional[Decimal] — YES ask price for upper shoulder bin
      - evt_tail_prob_raw: Optional[float] — nonstationary tail Pr(T>u | X) raw estimate
      - evt_covariates: Optional[dict] — continuous physical covariate dict (see COVARIATE_NAMES)
      - evt_cal_p_hats: Optional[List[float]] — conformal calibration set point estimates
      - evt_cal_outcomes: Optional[List[int]] — conformal calibration set binary outcomes

    Data-gate: any of (evt_tail_prob_raw, evt_covariates, native_yes_ask) None,
    or calibration set absent/empty → no_trade(EVT_TAIL_MODEL_UNWIRED).

    Theorem gate: p⁻_u − a_YES − phi ≤ 0 → no_trade(SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE).

    Enter: ShoulderBuyDecision with edge = p⁻_u − a_YES − phi.

    live_status: shadow. SHADOW-FIRST: does NOT execute live trades.
    """

    # Required continuous covariates per §8 (no discrete regime flags).
    COVARIATE_NAMES: Tuple[str, ...] = (
        "ensemble_mean",
        "ensemble_spread",
        "temp_anomaly_850mb",
        "soil_moisture_proxy",
        "advection",
        "station_bias",
        "season_harmonic_sin",
        "season_harmonic_cos",
    )

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key=_STRATEGY_KEY,
                family="shoulder_buy",
                description=(
                    "Shadow candidate: nonstationary EVT conformal tail-underpricing "
                    "for open upper shoulder (buy YES). Theorem: p⁻_u − a_YES − phi > 0, "
                    "where p⁻_u is split-conformal lower bound of Pr(T>u|X). "
                    "DATA-GATED until EVT tail model / covariate feed wired."
                ),
                executable_alpha=False,
            )
        )

    # ── Public helper (exposed for unit tests) ────────────────────────────────

    def compute_lower_bound(
        self,
        p_hat: float,
        cal_p_hats: Sequence[float],
        cal_outcomes: Sequence[int],
        alpha: float = _DEFAULT_ALPHA,
    ) -> Tuple[float, float]:
        """Return (p_lo, p_hi) conformal bounds for p_hat.

        Delegates to src.calibration.bounds.calibrated_bounds — the shared
        conformal implementation used across all stochastic Zeus strategies.
        Exposed as a method so relationship tests can probe the invariant
        p_lo ≤ p_hat without going through evaluate().
        """
        return calibrated_bounds(p_hat, cal_p_hats, cal_outcomes, alpha=alpha)

    # ── evaluate ─────────────────────────────────────────────────────────────

    def evaluate(
        self,
        *,
        context: CandidateContext,
        conn: sqlite3.Connection,
        decision_time: datetime,
    ) -> Union[CandidateDecision, ShoulderBuyDecision]:
        """Evaluate EVT conformal tail-underpricing theorem for open upper shoulder.

        Returns:
          ShoulderBuyDecision if lower-bound EV > 0 (shadow enter).
          CandidateDecision(no_trade, EVT_TAIL_MODEL_UNWIRED) when EVT inputs
            or calibration set are absent (data-gated).
          CandidateDecision(no_trade, SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE)
            when lower bound minus ask minus fee is non-positive.
        """
        analysis = context.analysis

        # ── Read inputs (may be absent on legacy / unwired contexts) ─────────
        native_yes_ask: Optional[Decimal] = getattr(analysis, "native_yes_ask", None)
        evt_tail_prob_raw: Optional[float] = getattr(analysis, "evt_tail_prob_raw", None)
        evt_covariates: Optional[dict] = getattr(analysis, "evt_covariates", None)
        evt_cal_p_hats: Optional[List[float]] = getattr(analysis, "evt_cal_p_hats", None)
        evt_cal_outcomes: Optional[List[int]] = getattr(analysis, "evt_cal_outcomes", None)

        # ── Data-gate ─────────────────────────────────────────────────────────
        # All of: raw tail prob, covariates, native YES ask, non-empty cal set
        # must be wired before we can compute a calibrated lower bound.
        cal_available = (
            evt_cal_p_hats is not None
            and evt_cal_outcomes is not None
            and len(evt_cal_p_hats) > 0
            and len(evt_cal_outcomes) > 0
        )
        if (
            evt_tail_prob_raw is None
            or evt_covariates is None
            or native_yes_ask is None
            or not cal_available
        ):
            return CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.EVT_TAIL_MODEL_UNWIRED,
                reason_detail=(
                    f"shoulder_buy_evt data-gated: "
                    f"evt_tail_prob_raw={'present' if evt_tail_prob_raw is not None else 'MISSING'}, "
                    f"evt_covariates={'present' if evt_covariates is not None else 'MISSING'}, "
                    f"native_yes_ask={'present' if native_yes_ask is not None else 'MISSING'}, "
                    f"cal_set={'present' if cal_available else 'MISSING/EMPTY'}; "
                    "will emit no_trade until EVT tail model and calibration set wired"
                ),
            )

        # ── Conformal lower bound ─────────────────────────────────────────────
        # p⁻_u = inf Pr(T>u | X) per split-conformal construction.
        # calibrated_bounds guarantees p_lo ≤ p_hat (R1 invariant).
        p_lo, _ = self.compute_lower_bound(
            evt_tail_prob_raw,
            evt_cal_p_hats,
            evt_cal_outcomes,
            alpha=_DEFAULT_ALPHA,
        )
        p_lower = Decimal(str(round(p_lo, 10)))
        p_raw_d = Decimal(str(round(evt_tail_prob_raw, 10)))

        # ── Fee computation ───────────────────────────────────────────────────
        fee_rate = venue_fee_rate()
        fee = phi(shares=_ONE_SHARE, price=native_yes_ask, fee_rate=fee_rate)

        # ── Theorem evaluation: p⁻_u − a_YES − phi > 0 ───────────────────────
        edge = p_lower - native_yes_ask - fee

        if edge <= Decimal("0"):
            return CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE,
                reason_detail=(
                    f"p_tail_lower_bound={p_lower}, "
                    f"native_yes_ask={native_yes_ask}, "
                    f"fee={fee}, "
                    f"edge={edge} ≤ 0; "
                    "conformal lower bound does not prove positive EV"
                ),
            )

        # ── Shadow enter ──────────────────────────────────────────────────────
        return ShoulderBuyDecision(
            p_tail_raw=p_raw_d,
            p_tail_lower_bound=p_lower,
            native_yes_ask=native_yes_ask,
            fee=fee,
            edge=edge,
        )
