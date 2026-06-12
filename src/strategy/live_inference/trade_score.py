"""Robust executable TradeScore for EDLI v1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.contracts.execution_price import ExecutionPrice
from src.strategy.live_inference.state import LiveInferenceBlocked

UTC = timezone.utc


@dataclass(frozen=True)
class TradeScoreInputs:
    p_fill_lcb: float
    q_5pct: float
    q_posterior: float
    c_95pct: float
    c_stress: float
    lambda_edge: float
    lambda_stress: float


@dataclass(frozen=True)
class RobustExecutableTradeScore:
    trade_score_id: str
    q_posterior: float
    q_5pct: float
    c_95pct: ExecutionPrice
    c_stress: ExecutionPrice
    p_fill_lcb: float
    score: float


def robust_trade_score(inputs: TradeScoreInputs | None = None, **kwargs):
    if inputs is None:
        return _robust_trade_score_receipt(**kwargs)
    return _robust_trade_score_value(inputs)


def _robust_trade_score_value(inputs: TradeScoreInputs) -> float:
    for field, value in inputs.__dict__.items():
        if not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be numeric")
    if inputs.p_fill_lcb < 0 or inputs.p_fill_lcb > 1:
        raise ValueError("p_fill_lcb must be in [0, 1]")
    robust_edge = min(
        inputs.q_5pct - inputs.c_95pct - inputs.lambda_edge,
        inputs.q_posterior - inputs.c_stress - inputs.lambda_stress,
    )
    return inputs.p_fill_lcb * robust_edge


def _robust_trade_score_receipt(
    *,
    trade_score_id: str,
    q_posterior: float,
    q_5pct: float,
    c_95pct: ExecutionPrice,
    c_stress: ExecutionPrice,
    p_fill_lcb: float,
    penalty: float = 0.0,
    stress_penalty: float = 0.0,
) -> RobustExecutableTradeScore:
    c_95pct.assert_kelly_safe()
    c_stress.assert_kelly_safe()
    edge_bound = min(
        q_5pct - c_95pct.value - penalty,
        q_posterior - c_stress.value - stress_penalty,
    )
    return RobustExecutableTradeScore(
        trade_score_id=trade_score_id,
        q_posterior=q_posterior,
        q_5pct=q_5pct,
        c_95pct=c_95pct,
        c_stress=c_stress,
        p_fill_lcb=p_fill_lcb,
        score=p_fill_lcb * edge_bound,
    )


def assert_positive_trade_score(score: float) -> None:
    if score <= 0:
        raise LiveInferenceBlocked("TradeScore <= 0")


def assert_causal_snapshot(causal_snapshot_id: str | None) -> None:
    if not causal_snapshot_id:
        raise LiveInferenceBlocked("missing causal_snapshot_id")


def assert_available_for_trade(*, available_at: datetime, decision_time: datetime) -> None:
    if available_at.tzinfo is None or available_at.utcoffset() is None:
        raise LiveInferenceBlocked("available_at must be timezone-aware")
    if available_at.astimezone(UTC) > decision_time.astimezone(UTC):
        raise LiveInferenceBlocked("available_at after decision_time")


def assert_passive_post_only_gate(*, passive_fill_lcb: float, min_passive_fill_lcb: float) -> None:
    if passive_fill_lcb < min_passive_fill_lcb:
        raise LiveInferenceBlocked("passive post-only fill gate blocks live trade")
