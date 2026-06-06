# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §13
#                  + docs/reference/zeus_strategy_spec.md combination sections
"""C1 — shoulder_buy × weather_event joint tail Bayes (§13).

THEOREM (STRATEGY_TAXONOMY_DIRECTIVE.md §13):
  Joint tail probability: Pr(T>u | X, A)
    X = ensemble/forecast physics covariates (EVT tail model)
    A = NWS alert / extreme signal (Bayes factor)
    u = open shoulder threshold

  Bayes factor combination (multiplicative):
    posterior_odds = prior_tail_odds · LR(A)
    where:
      prior_tail_odds = Pr(T>u|X) / (1 − Pr(T>u|X))  [from EVT model]
      LR(A)           = Pr(A|T>u,X) / Pr(A|T≤u,X)    [learned per (alertType, city, season, leadTime)]
      posterior p_combined = posterior_odds / (1 + posterior_odds)

  Conformal lower bound:
    p⁻_tail(X,A) = calibrated_bounds(p_combined, cal_p_hats, cal_outcomes, alpha).lo

  Entry condition (§13):
    p⁻_tail(X,A) − a_YES − phi(a_YES) > 0

MATH REUSE:
  - EVT tail component: imports calibrated_bounds from src.calibration.bounds
    (same as shoulder_buy_evt; does NOT re-implement conformal logic)
  - Bayes alert component: imports bayes_update + posterior_lower_bound from src.strategy.bayes_alert
    (same as weather_event_arbitrage; does NOT re-implement LR math)
  - Alert is a continuous/discrete covariate — NOT a hardcoded HEAT_DOME case.

DATA-GATED:
  Both EVT model AND alert LR table must be wired before entry fires.
  Missing either → no_trade until data pipeline is built:
    - evt_tail_prob_raw absent → JOINT_EVT_ALERT_UNWIRED
    - alert_source absent / no active alert → JOINT_EVT_ALERT_UNWIRED
    - LR table returns None → JOINT_EVT_ALERT_LR_MISSING
    - p⁻_tail(X,A) − a_YES − phi ≤ 0 → JOINT_EVT_TAIL_NO_EDGE

SHADOW-FIRST (operator directive 2026-05-22):
  executable_alpha=False — no live trades until operator promotes.
  kelly=0: no sizing. Registered via Pipeline B (calibrated stochastic promotion).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from src.calibration.bounds import calibrated_bounds
from src.contracts.no_trade_reason import NoTradeReason
from src.strategy.bayes_alert import AlertLRStub, AlertLRTable, bayes_update
from src.strategy.fees import phi, venue_fee_rate

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    write_candidate_no_trade_row,
)

_STRATEGY_KEY = "c1_joint_tail_bayes"

# Conformal miscoverage rate. 0.10 → 90% marginal coverage.
_DEFAULT_ALPHA: float = 0.10

_ONE_SHARE = Decimal("1")

# Alert sources considered trusted for this shadow candidate (same as weather_event_arbitrage).
_TRUSTED_ALERT_SOURCES: frozenset[str] = frozenset({
    "noaa_alerts", "nws_alerts", "ecmwf_extreme_events",
    "gfs_extreme_events", "zeus_internal_alert",
})

# Default LR table: data-gated stub (always returns None until fitted).
_DEFAULT_LR_TABLE: AlertLRTable = AlertLRStub()


@dataclass(frozen=True)
class JointTailBayesDecision:
    """Extended decision carrying §13 proof fields for shadow observability.

    outcome is always "enter" for this class.
    """

    outcome: str = field(default="enter", init=False)
    side: str = "buy_yes"
    strategy_key: str = _STRATEGY_KEY
    # EVT component
    evt_tail_prob_raw: Decimal = Decimal("0")
    # Bayes-combined posterior
    p_combined: Decimal = Decimal("0")
    # Conformal lower bound on the combined posterior
    p_tail_lower_bound: Decimal = Decimal("0")
    # Market inputs
    native_yes_ask: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    edge: Decimal = Decimal("0")


class JointTailBayes(BaseStrategyCandidate):
    """C1: shoulder_buy × weather_event joint tail Bayes shadow candidate (§13).

    Combines EVT tail Pr(T>u|X) with alert likelihood ratio LR(A) via Bayes update.
    Applies conformal lower bound to the combined posterior.
    Entry: p⁻_tail(X,A) − a_YES − phi(a_YES) > 0.

    Reads from context.analysis:
      - evt_tail_prob_raw: Optional[float]   — nonstationary tail Pr(T>u|X) raw estimate
      - evt_cal_p_hats: Optional[List[float]] — conformal calibration set point estimates
      - evt_cal_outcomes: Optional[List[int]] — conformal calibration outcomes
      - native_yes_ask: Optional[Decimal]    — YES ask for upper shoulder bin
      - alert_source: Optional[str]          — NWS / NOAA alert feed source identifier
      - active_weather_alert: Optional[bool] — active alert for this market/target_date
      - alert_prior_p: Optional[float]       — pre-alert prior p (from forecast to bin)
        NOTE: alert_prior_p is used ONLY for the Bayes posterior_lower_bound path
        (it must agree with evt_tail_prob_raw for the theorem to be consistent).
        In this implementation we use evt_tail_prob_raw as the prior tail probability
        for the multiplicative Bayes update (§13 theorem primary path).
      - alert_type: str                      — alert type string (continuous covariate)
      - alert_city: str                      — city slug
      - alert_season: str                    — season tag
      - alert_lead_time_hours: int           — lead time at time of alert

    Data-gate: any missing EVT or alert input → no_trade. LR table absent → no_trade.
    """

    def __init__(self, *, lr_table: Optional[AlertLRTable] = None) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key=_STRATEGY_KEY,
                family="c1_joint_tail_bayes",
                description=(
                    "Shadow candidate: joint tail Bayes combining EVT Pr(T>u|X) with "
                    "alert LR(A) via multiplicative Bayes update (§13). "
                    "Entry: p⁻_tail(X,A) − a_YES − phi > 0. "
                    "DATA-GATED: both EVT model and alert LR table must be wired."
                ),
                executable_alpha=False,
            )
        )
        self._lr_table: AlertLRTable = lr_table if lr_table is not None else _DEFAULT_LR_TABLE

    def evaluate(
        self,
        *,
        context: CandidateContext,
        conn: sqlite3.Connection,
        decision_time: datetime,
    ) -> "Union[CandidateDecision, JointTailBayesDecision]":
        """Evaluate joint tail Bayes entry condition (§13).

        Returns:
          JointTailBayesDecision (enter) if lower-bound EV > 0 (shadow enter).
          CandidateDecision(no_trade, JOINT_EVT_ALERT_UNWIRED) when EVT or alert inputs absent.
          CandidateDecision(no_trade, JOINT_EVT_ALERT_LR_MISSING) when LR table not fitted.
          CandidateDecision(no_trade, JOINT_EVT_TAIL_NO_EDGE) when lower bound ≤ ask + fee.
        """
        analysis = context.analysis

        # ── Gate 1: EVT tail model inputs ────────────────────────────────────
        evt_tail_prob_raw: Optional[float] = getattr(analysis, "evt_tail_prob_raw", None)
        evt_cal_p_hats: Optional[List[float]] = getattr(analysis, "evt_cal_p_hats", None)
        evt_cal_outcomes: Optional[List[int]] = getattr(analysis, "evt_cal_outcomes", None)
        native_yes_ask: Optional[Decimal] = getattr(analysis, "native_yes_ask", None)

        cal_available = (
            evt_cal_p_hats is not None
            and evt_cal_outcomes is not None
            and len(evt_cal_p_hats) > 0
            and len(evt_cal_outcomes) > 0
        )
        if evt_tail_prob_raw is None or not cal_available or native_yes_ask is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.JOINT_EVT_ALERT_UNWIRED,
                reason_detail=(
                    f"c1_joint_tail_bayes data-gated (EVT side): "
                    f"evt_tail_prob_raw={'present' if evt_tail_prob_raw is not None else 'MISSING'}, "
                    f"cal_set={'present' if cal_available else 'MISSING/EMPTY'}, "
                    f"native_yes_ask={'present' if native_yes_ask is not None else 'MISSING'}; "
                    "will emit no_trade until EVT tail model and calibration set wired"
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Gate 2: alert feed wiring ─────────────────────────────────────────
        alert_source: Optional[str] = getattr(analysis, "alert_source", None)
        if alert_source is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.JOINT_EVT_ALERT_UNWIRED,
                reason_detail=(
                    "c1_joint_tail_bayes data-gated (alert side): "
                    "alert_source absent; NWS alert feed not wired."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        alert_source_norm = str(alert_source).lower().strip()
        if alert_source_norm not in _TRUSTED_ALERT_SOURCES:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.JOINT_EVT_ALERT_UNWIRED,
                reason_detail=(
                    f"c1_joint_tail_bayes: alert_source={alert_source!r} not in trusted set."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        active_alert: Optional[bool] = getattr(analysis, "active_weather_alert", None)
        if not active_alert:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.JOINT_EVT_ALERT_UNWIRED,
                reason_detail=(
                    f"c1_joint_tail_bayes: alert_source={alert_source!r} trusted "
                    "but no active alert for this market/target_date."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Gate 3: LR table lookup ───────────────────────────────────────────
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
                reason=NoTradeReason.JOINT_EVT_ALERT_LR_MISSING,
                reason_detail=(
                    f"c1_joint_tail_bayes: LR table returned None for "
                    f"(alert_type={alert_type!r}, city={city!r}, "
                    f"season={season!r}, lead_time_hours={lead_time_hours}); "
                    "alert_event_fact archive not fitted."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Bayes update: posterior_odds = prior_tail_odds · LR_lower ────────
        # §13: use LR lower bound for conservative posterior
        lr_lower = lr_record.effective_lower()
        p_combined = bayes_update(evt_tail_prob_raw, lr_lower)

        # ── Conformal lower bound on the combined posterior ───────────────────
        # p⁻_tail(X,A) = inf of conformal interval for p_combined
        p_lo, _ = calibrated_bounds(
            p_combined, evt_cal_p_hats, evt_cal_outcomes, alpha=_DEFAULT_ALPHA
        )
        p_lower = Decimal(str(round(p_lo, 10)))
        p_combined_d = Decimal(str(round(p_combined, 10)))
        p_raw_d = Decimal(str(round(evt_tail_prob_raw, 10)))

        # ── Fee computation ───────────────────────────────────────────────────
        fee_rate = venue_fee_rate()
        fee = phi(shares=_ONE_SHARE, price=native_yes_ask, fee_rate=fee_rate)

        # ── Entry condition: p⁻_tail(X,A) − a_YES − phi > 0 ──────────────────
        edge = p_lower - native_yes_ask - fee

        if edge <= Decimal("0"):
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.JOINT_EVT_TAIL_NO_EDGE,
                reason_detail=(
                    f"c1_joint_tail_bayes: p_tail_lower={p_lower}, "
                    f"p_combined={p_combined_d}, "
                    f"evt_raw={p_raw_d}, "
                    f"lr_lower={lr_lower:.6f}, "
                    f"native_yes_ask={native_yes_ask}, "
                    f"fee={fee}, edge={edge} ≤ 0; "
                    "joint conformal lower bound does not prove positive EV"
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
            target_price=float(native_yes_ask),
        )

        return JointTailBayesDecision(
            evt_tail_prob_raw=p_raw_d,
            p_combined=p_combined_d,
            p_tail_lower_bound=p_lower,
            native_yes_ask=native_yes_ask,
            fee=fee,
            edge=edge,
        )
