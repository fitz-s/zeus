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


# R5: Gate_50 — terminal evaluation at 50 settled trades
_gate_50_state: str = "pending"  # "pending" | "passed" | "failed"


def evaluate_gate_50(settled_count: int, accuracy: float) -> str:
    """R5: Gate_50 terminal evaluation. Irreversible once passed/failed.

    At 50 settled trades: accuracy >= 55% → passed, < 50% → failed (permanent halt).
    50-55%: still pending, re-evaluate at 100.
    """
    global _gate_50_state

    if _gate_50_state in ("passed", "failed"):
        return _gate_50_state  # Permanent — never re-evaluate

    if settled_count < 50:
        return "pending"

    if accuracy >= 0.55:
        _gate_50_state = "passed"
        logger.info("Gate_50 PASSED: %d trades, %.1f%% accuracy", settled_count, accuracy * 100)
        return "passed"
    elif accuracy < 0.50:
        _gate_50_state = "failed"
        logger.error("Gate_50 FAILED: %d trades, %.1f%% accuracy. Model has no measurable edge. "
                      "Rebuild required.", settled_count, accuracy * 100)
        return "failed"
    else:
        return "pending"  # 50-55%: re-evaluate at 100
