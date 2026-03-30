"""RiskGuard metrics: Brier, directional accuracy, win rate. Spec §7.2."""

import numpy as np

from src.riskguard.risk_level import RiskLevel


def brier_score(p_forecasts: list[float], outcomes: list[int]) -> float:
    """Brier score: mean squared error of probability forecasts. Lower = better."""
    if not p_forecasts:
        return 0.0
    p = np.array(p_forecasts)
    o = np.array(outcomes)
    return float(np.mean((p - o) ** 2))


def directional_accuracy(
    p_forecasts: list[float], outcomes: list[int]
) -> float:
    """Fraction of forecasts where direction was correct (p > 0.5 ↔ outcome=1)."""
    if not p_forecasts:
        return 0.5
    correct = sum(
        1 for p, o in zip(p_forecasts, outcomes)
        if (p > 0.5 and o == 1) or (p <= 0.5 and o == 0)
    )
    return correct / len(p_forecasts)


def win_rate(pnl_list: list[float]) -> float:
    """Fraction of trades with positive P&L."""
    if not pnl_list:
        return 0.5
    wins = sum(1 for p in pnl_list if p > 0)
    return wins / len(pnl_list)


def evaluate_brier(score: float, thresholds: dict) -> RiskLevel:
    """Map Brier score to risk level. Spec §7.3."""
    if score >= thresholds["brier_red"]:
        return RiskLevel.RED
    elif score >= thresholds["brier_orange"]:
        return RiskLevel.ORANGE
    elif score >= thresholds["brier_yellow"]:
        return RiskLevel.YELLOW
    return RiskLevel.GREEN


def evaluate_win_rate(rate: float, thresholds: dict) -> RiskLevel:
    """Map win rate to risk level."""
    if rate < thresholds["win_rate_orange"]:
        return RiskLevel.ORANGE
    elif rate < thresholds["win_rate_yellow"]:
        return RiskLevel.YELLOW
    return RiskLevel.GREEN
