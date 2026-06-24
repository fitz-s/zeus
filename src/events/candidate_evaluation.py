"""Pure opportunity candidate evaluation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.contracts.probability_arithmetic import one_minus, payout_odds
from src.strategy.live_inference.live_admission import (
    LIVE_DIRECTION_WIN_RATE_FLOOR,
    live_buy_no_conservative_evidence_rejection_reason,
    live_capital_efficiency_rejection_reason,
    live_lcb_consistency_rejection_reason,
    live_win_rate_floor_rejection_reason,
)


@dataclass(frozen=True)
class CandidateEvaluation:
    """One executable or diagnostic trade action in a family opportunity book."""

    candidate_id: str
    family_id: str
    condition_id: str
    token_id: str
    direction: str
    bin_label: str | None
    execution_price: float | None
    q_posterior: float
    q_lcb_5pct: float
    c_cost_95pct: float | None
    p_fill_lcb: float
    trade_score: float
    p_value: float
    passed_prefilter: bool
    native_quote_available: bool
    missing_reason: str | None = None
    kelly_size_usd: float = 0.0
    max_executable_shares: float | None = None
    book_hash: str | None = None
    quote_fresh: bool = True
    low_volume_usd: float | None = None
    q_lcb_calibration_source: str | None = None
    same_bin_yes_posterior: float | None = None
    # Twin-authority reconciliation #7 (2026-06-11): the family settlement-backward
    # coverage VERDICT status, copied from the proof so this receipt projection's
    # admission view (live_buy_no_conservative_evidence_*) matches the proof-
    # generation gate's verdict-aware outcome. None on canonical/legacy paths.
    settlement_coverage_status: str | None = None
    # FIX C (mode-consistent EV, 2026-06-10): the per-candidate maker/taker mode
    # decision and BOTH EVs (trade_score == the chosen mode's EV). Receipt-level
    # provenance for the settlement loop to recalibrate p_fill_maker / the
    # adverse-selection haircut from fill facts. None on legacy/unpriced proofs.
    execution_mode_intent: str | None = None
    ev_taker: float | None = None
    ev_maker: float | None = None
    maker_limit_price: float | None = None
    relative_spread_at_eval: float | None = None
    taker_forbidden_reason: str | None = None
    maker_fill_probability: float | None = None
    maker_fill_probability_source: str | None = None
    support_index: int | None = None
    bin_id: str | None = None
    # Live-path wiring of the selection-calibrator + city-skill gate (2026-06-22; team-lead). ALL
    # OPTIONAL (default None) so NO existing construction site breaks and DEFAULT-OFF behavior is
    # byte-identical. ``city`` enables the per-city skill gate + shadow log; the artifacts are
    # injected by the caller (else the live serving rules load them from state/). ``lead_days`` /
    # ``bin_class`` shape the calibrator cell; both have safe defaults.
    city: str | None = None
    target_date: str | None = None
    decision_time: str | None = None
    lead_days: float = 1.0
    bin_class: str = "nonmodal"
    posterior_version: str | None = None
    selection_calibrator_artifact: Any | None = None
    city_skill_artifact: Any | None = None

    @property
    def robust_ev_per_dollar(self) -> float:
        if self.execution_price is None or self.execution_price <= 0.0:
            return 0.0
        return float(self.trade_score) / float(self.execution_price)

    @property
    def robust_kelly_fraction_lcb(self) -> float:
        if self.execution_price is None or self.execution_price <= 0.0 or self.execution_price >= 1.0:
            return 0.0
        q_lcb = max(0.0, min(1.0, float(self.q_lcb_5pct)))
        if q_lcb <= self.execution_price:
            return 0.0
        price = float(self.execution_price)
        # Binary Kelly denominator at price p is (1 - p); de-obfuscated from the
        # value-identical (1/p - 1) * p that 16c35e7445 wrote (§0.2 / FIX-5a).
        denominator = one_minus(price)
        return (q_lcb - price) / denominator

    @property
    def robust_kelly_growth_score(self) -> float:
        return self.robust_ev_per_dollar * self.robust_kelly_fraction_lcb

    @property
    def capital_weighted_growth_score(self) -> float:
        return self.expected_robust_dollars * self.robust_kelly_growth_score

    @property
    def expected_robust_dollars(self) -> float:
        if self.execution_price is None or self.execution_price <= 0.0:
            return 0.0
        if self.kelly_size_usd > 0.0:
            return self.robust_ev_per_dollar * float(self.kelly_size_usd)
        if self.max_executable_shares is not None and self.max_executable_shares > 0.0:
            return float(self.trade_score) * float(self.max_executable_shares)
        return float(self.trade_score)

    @property
    def live_win_rate_floor_reason(self) -> str | None:
        return live_win_rate_floor_rejection_reason(q_lcb=self.q_lcb_5pct)

    @property
    def live_win_rate_admissible(self) -> bool:
        return self.live_win_rate_floor_reason is None

    @property
    def live_capital_efficiency_reason(self) -> str | None:
        return live_capital_efficiency_rejection_reason(
            q_lcb=self.q_lcb_5pct,
            execution_price=self.execution_price,
            trade_score=self.trade_score,
        )

    @property
    def live_capital_efficiency_admissible(self) -> bool:
        return self.live_capital_efficiency_reason is None

    @property
    def live_lcb_consistency_reason(self) -> str | None:
        return live_lcb_consistency_rejection_reason(
            q_direction=self.q_posterior,
            q_lcb=self.q_lcb_5pct,
        )

    @property
    def live_lcb_consistency_admissible(self) -> bool:
        return self.live_lcb_consistency_reason is None

    @property
    def live_buy_no_conservative_evidence_reason(self) -> str | None:
        return live_buy_no_conservative_evidence_rejection_reason(
            direction=self.direction,
            q_direction=self.q_posterior,
            q_lcb=self.q_lcb_5pct,
            execution_price=self.execution_price,
            q_lcb_calibration_source=self.q_lcb_calibration_source,
            same_bin_yes_posterior=self.same_bin_yes_posterior,
            settlement_coverage_status=self.settlement_coverage_status,
        )

    @property
    def live_buy_no_conservative_evidence_admissible(self) -> bool:
        return self.live_buy_no_conservative_evidence_reason is None

    @property
    def max_payout_roi(self) -> float:
        if self.execution_price is None or self.execution_price <= 0.0 or self.execution_price >= 1.0:
            return 0.0
        # Genuine payout odds (1 - price)/price, NOT a complement of 1.
        return payout_odds(float(self.execution_price))

    @property
    def _raw_side_prob(self) -> float:
        """The RAW point probability of THIS candidate's side (q_posterior is the YES-in-bin belief;
        NO raw prob = 1 - q_posterior). Used to resolve the calibrator cell."""
        q = max(0.0, min(1.0, float(self.q_posterior)))
        return (1.0 - q) if str(self.direction or "").lower() == "buy_no" else q

    @property
    def calibrated_admission_q_lcb(self) -> float:
        """The admission q_lcb after the selection-calibrator deflation. DEFAULT OFF
        (``ZEUS_SELECTION_CALIBRATOR_LIVE`` unset) -> the raw ``q_lcb_5pct`` unchanged."""
        from src.strategy.live_inference.live_admission import selection_calibrated_admission_q_lcb

        return selection_calibrated_admission_q_lcb(
            q_lcb=self.q_lcb_5pct,
            raw_side_prob=self._raw_side_prob,
            direction=self.direction,
            lead_days=self.lead_days,
            bin_class=self.bin_class,
            own_side_cost=self.execution_price,
            artifact=self.selection_calibrator_artifact,
            expected_posterior_version=self.posterior_version,
        )

    @property
    def selection_calibrator_admissible(self) -> bool:
        """The calibrator-deflated q_lcb must still clear the execution price (edge_lcb > 0). DEFAULT
        OFF -> the deflated q_lcb == raw q_lcb_5pct so this is the existing edge check (no change)."""
        if self.execution_price is None:
            return True
        return float(self.calibrated_admission_q_lcb) > float(self.execution_price)

    @property
    def city_skill_block_reason(self) -> str | None:
        """A rejection reason when the candidate's city is a confirmed temporally-stable loser.
        DEFAULT OFF (``ZEUS_CITY_SKILL_GATE_LIVE`` unset) -> None."""
        from src.strategy.live_inference.live_admission import city_skill_block_rejection_reason

        return city_skill_block_rejection_reason(
            city=self.city,
            artifact=self.city_skill_artifact,
            expected_posterior_version=self.posterior_version,
        )

    @property
    def city_skill_admissible(self) -> bool:
        return self.city_skill_block_reason is None

    def shadow_log(self, *, path: str | None = None) -> bool:
        """Record this candidate's would-admit decision to the shadow log (accrual). DEFAULT OFF
        (``ZEUS_SHADOW_ADMIT_LOG`` unset) -> no-op. Fail-soft. Call at EVERY evaluation, admitted or
        not, so the would-admit population accrues."""
        from src.strategy.live_inference.live_admission import shadow_log_admission

        return shadow_log_admission(
            path=path,
            decision_time=str(self.decision_time or ""),
            city=str(self.city or ""),
            target_date=str(self.target_date or ""),
            condition_id=str(self.condition_id or ""),
            bin_id=str(self.bin_id or self.bin_label or ""),
            direction=self.direction,
            raw_side_prob=self._raw_side_prob,
            q_lcb=float(self.q_lcb_5pct),
            own_side_cost=float(self.execution_price) if self.execution_price is not None else 1.0,
            native_quote_available=bool(self.native_quote_available),
            quote_fresh=bool(self.quote_fresh),
            posterior_version=str(self.posterior_version or ""),
            city_skill_admit=self.city_skill_admissible,
            selection_calibrator_q_safe=float(self.calibrated_admission_q_lcb),
        )

    @property
    def admitted(self) -> bool:
        return (
            self.execution_price is not None
            and self.execution_price > 0.0
            and self.trade_score > 0.0
            and self.passed_prefilter
            and self.missing_reason is None
            and self.quote_fresh
            and self.live_lcb_consistency_admissible
            and self.live_capital_efficiency_admissible
            and self.live_buy_no_conservative_evidence_admissible
            # Live-path wiring (2026-06-22; flag-gated default OFF = no change):
            and self.selection_calibrator_admissible
            and self.city_skill_admissible
        )

    @property
    def objective_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (
            self.capital_weighted_growth_score,
            self.robust_kelly_growth_score,
            self.robust_ev_per_dollar,
            self.expected_robust_dollars,
            float(self.q_lcb_5pct),
            float(self.trade_score),
        )

    def to_receipt_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "family_id": self.family_id,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "direction": self.direction,
            "bin_label": self.bin_label,
            "support_index": self.support_index,
            "bin_id": self.bin_id,
            "execution_price": self.execution_price,
            "q_posterior": self.q_posterior,
            "q_lcb_5pct": self.q_lcb_5pct,
            "c_cost_95pct": self.c_cost_95pct,
            "p_fill_lcb": self.p_fill_lcb,
            "trade_score": self.trade_score,
            "p_value": self.p_value,
            "passed_prefilter": self.passed_prefilter,
            "native_quote_available": self.native_quote_available,
            "missing_reason": self.missing_reason,
            "kelly_size_usd": self.kelly_size_usd,
            "max_executable_shares": self.max_executable_shares,
            "book_hash": self.book_hash,
            "quote_fresh": self.quote_fresh,
            "low_volume_usd": self.low_volume_usd,
            "q_lcb_calibration_source": self.q_lcb_calibration_source,
            "same_bin_yes_posterior": self.same_bin_yes_posterior,
            "settlement_coverage_status": self.settlement_coverage_status,
            "admitted": self.admitted,
            "live_win_rate_floor": LIVE_DIRECTION_WIN_RATE_FLOOR,
            "live_win_rate_admissible": self.live_win_rate_admissible,
            "live_lcb_consistency_admissible": self.live_lcb_consistency_admissible,
            "live_lcb_consistency_reason": self.live_lcb_consistency_reason,
            "live_capital_efficiency_admissible": self.live_capital_efficiency_admissible,
            "live_capital_efficiency_reason": self.live_capital_efficiency_reason,
            "live_buy_no_conservative_evidence_admissible": self.live_buy_no_conservative_evidence_admissible,
            "live_buy_no_conservative_evidence_reason": self.live_buy_no_conservative_evidence_reason,
            "execution_mode_intent": self.execution_mode_intent,
            "ev_taker": self.ev_taker,
            "ev_maker": self.ev_maker,
            "maker_limit_price": self.maker_limit_price,
            "relative_spread_at_eval": self.relative_spread_at_eval,
            "taker_forbidden_reason": self.taker_forbidden_reason,
            "maker_fill_probability": self.maker_fill_probability,
            "maker_fill_probability_source": self.maker_fill_probability_source,
            "robust_ev_per_dollar": self.robust_ev_per_dollar,
            "robust_kelly_fraction_lcb": self.robust_kelly_fraction_lcb,
            "robust_kelly_growth_score": self.robust_kelly_growth_score,
            "capital_weighted_growth_score": self.capital_weighted_growth_score,
            "expected_robust_dollars": self.expected_robust_dollars,
            "max_payout_roi": self.max_payout_roi,
        }
