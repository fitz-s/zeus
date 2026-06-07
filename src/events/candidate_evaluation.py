"""Pure opportunity candidate evaluation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        return (q_lcb - float(self.execution_price)) / (1.0 + (-float(self.execution_price)))

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
        )

    @property
    def live_buy_no_conservative_evidence_admissible(self) -> bool:
        return self.live_buy_no_conservative_evidence_reason is None

    @property
    def max_payout_roi(self) -> float:
        if self.execution_price is None or self.execution_price <= 0.0 or self.execution_price >= 1.0:
            return 0.0
        return (1.0 / float(self.execution_price)) - 1.0

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
            "admitted": self.admitted,
            "live_win_rate_floor": LIVE_DIRECTION_WIN_RATE_FLOOR,
            "live_win_rate_admissible": self.live_win_rate_admissible,
            "live_lcb_consistency_admissible": self.live_lcb_consistency_admissible,
            "live_lcb_consistency_reason": self.live_lcb_consistency_reason,
            "live_capital_efficiency_admissible": self.live_capital_efficiency_admissible,
            "live_capital_efficiency_reason": self.live_capital_efficiency_reason,
            "live_buy_no_conservative_evidence_admissible": self.live_buy_no_conservative_evidence_admissible,
            "live_buy_no_conservative_evidence_reason": self.live_buy_no_conservative_evidence_reason,
            "robust_ev_per_dollar": self.robust_ev_per_dollar,
            "robust_kelly_fraction_lcb": self.robust_kelly_fraction_lcb,
            "robust_kelly_growth_score": self.robust_kelly_growth_score,
            "capital_weighted_growth_score": self.capital_weighted_growth_score,
            "expected_robust_dollars": self.expected_robust_dollars,
            "max_payout_roi": self.max_payout_roi,
        }
