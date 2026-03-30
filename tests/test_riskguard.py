"""Tests for RiskGuard metrics and risk levels."""

import pytest

from src.riskguard.risk_level import RiskLevel, overall_level
from src.riskguard.metrics import (
    brier_score, directional_accuracy, win_rate,
    evaluate_brier, evaluate_win_rate,
)


class TestRiskLevel:
    def test_overall_all_green(self):
        assert overall_level(RiskLevel.GREEN, RiskLevel.GREEN) == RiskLevel.GREEN

    def test_overall_worst_wins(self):
        assert overall_level(RiskLevel.GREEN, RiskLevel.ORANGE) == RiskLevel.ORANGE
        assert overall_level(RiskLevel.YELLOW, RiskLevel.RED) == RiskLevel.RED

    def test_overall_empty(self):
        assert overall_level() == RiskLevel.GREEN


class TestMetrics:
    def test_brier_perfect(self):
        """Perfect forecasts → Brier = 0."""
        assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)

    def test_brier_worst(self):
        """Completely wrong → Brier = 1."""
        assert brier_score([0.0, 1.0], [1, 0]) == pytest.approx(1.0)

    def test_brier_moderate(self):
        score = brier_score([0.7, 0.3, 0.6], [1, 0, 1])
        assert 0 < score < 0.5

    def test_directional_accuracy_perfect(self):
        assert directional_accuracy([0.8, 0.2, 0.9], [1, 0, 1]) == pytest.approx(1.0)

    def test_win_rate(self):
        assert win_rate([1.0, -0.5, 2.0, -1.0]) == pytest.approx(0.5)

    def test_win_rate_all_wins(self):
        assert win_rate([1.0, 2.0, 0.5]) == pytest.approx(1.0)


class TestRiskEvaluation:
    def test_brier_green(self):
        thresholds = {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35}
        assert evaluate_brier(0.20, thresholds) == RiskLevel.GREEN

    def test_brier_yellow(self):
        thresholds = {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35}
        assert evaluate_brier(0.27, thresholds) == RiskLevel.YELLOW

    def test_brier_red(self):
        thresholds = {"brier_yellow": 0.25, "brier_orange": 0.30, "brier_red": 0.35}
        assert evaluate_brier(0.40, thresholds) == RiskLevel.RED

    def test_win_rate_green(self):
        thresholds = {"win_rate_yellow": 0.40, "win_rate_orange": 0.35}
        assert evaluate_win_rate(0.55, thresholds) == RiskLevel.GREEN

    def test_win_rate_orange(self):
        thresholds = {"win_rate_yellow": 0.40, "win_rate_orange": 0.35}
        assert evaluate_win_rate(0.30, thresholds) == RiskLevel.ORANGE
