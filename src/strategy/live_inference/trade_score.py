"""Robust executable TradeScore for EDLI v1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

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


def robust_trade_score(inputs: TradeScoreInputs) -> float:
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


def assert_taker_live_allowed(*, taker_fok_fak_live_enabled: bool) -> None:
    if not taker_fok_fak_live_enabled:
        raise LiveInferenceBlocked("taker FOK/FAK live disabled by execution policy")


def assert_passive_post_only_gate(*, passive_fill_lcb: float, min_passive_fill_lcb: float) -> None:
    if passive_fill_lcb < min_passive_fill_lcb:
        raise LiveInferenceBlocked("passive post-only fill gate blocks live trade")
