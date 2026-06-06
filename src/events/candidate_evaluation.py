"""Pure opportunity candidate evaluation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    @property
    def robust_ev_per_dollar(self) -> float:
        if self.execution_price is None or self.execution_price <= 0.0:
            return 0.0
        return float(self.trade_score) / float(self.execution_price)

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
    def admitted(self) -> bool:
        return (
            self.execution_price is not None
            and self.execution_price > 0.0
            and self.trade_score > 0.0
            and self.passed_prefilter
            and self.missing_reason is None
            and self.quote_fresh
        )

    @property
    def objective_tuple(self) -> tuple[float, float, float, float]:
        return (
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
            "admitted": self.admitted,
            "robust_ev_per_dollar": self.robust_ev_per_dollar,
            "expected_robust_dollars": self.expected_robust_dollars,
        }
