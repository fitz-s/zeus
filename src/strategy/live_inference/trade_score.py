"""Robust executable TradeScore for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.execution_price import ExecutionPrice


@dataclass(frozen=True)
class RobustExecutableTradeScore:
    trade_score_id: str
    q_posterior: float
    q_5pct: float
    c_95pct: ExecutionPrice
    c_stress: ExecutionPrice
    p_fill_lcb: float
    score: float


def robust_trade_score(
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
    score = p_fill_lcb * edge_bound
    return RobustExecutableTradeScore(
        trade_score_id=trade_score_id,
        q_posterior=q_posterior,
        q_5pct=q_5pct,
        c_95pct=c_95pct,
        c_stress=c_stress,
        p_fill_lcb=p_fill_lcb,
        score=score,
    )
