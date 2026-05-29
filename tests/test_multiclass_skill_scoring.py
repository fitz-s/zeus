# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL replay redesign §4/§5 (categorical group scoring).
#   Verifies the categorical layer of src/calibration/scoring.py: a weather bin
#   market is ONE distribution over ordered bins, scored against the single
#   settled bin — p_winner / categorical_log_loss / multiclass_brier / RPS /
#   winner_rank / top-k / group integrity.
"""Tests for the multinomial categorical scoring layer."""

from __future__ import annotations

import math

import pytest

from src.calibration import scoring
from src.calibration.scoring import ProbabilityGroupError


# The canonical worked example from the plan: a 4-bin vector, winner at index 2.
P_EXAMPLE = [0.05, 0.15, 0.70, 0.10]
WINNER_EXAMPLE = 2


def test_p_winner_is_mass_on_settled_bin():
    assert scoring.p_winner(P_EXAMPLE, WINNER_EXAMPLE) == pytest.approx(0.70)


def test_categorical_log_loss_matches_neg_log_pwinner():
    assert scoring.categorical_log_loss(P_EXAMPLE, WINNER_EXAMPLE) == pytest.approx(
        -math.log(0.70)
    )


def test_categorical_log_loss_clamps_zero_mass_winner_to_finite():
    # A winning bin that received exactly 0 mass must yield a large FINITE penalty,
    # not +inf / ValueError (the clamped variant; log_score would raise).
    val = scoring.categorical_log_loss([0.5, 0.5, 0.0], 2)
    assert math.isfinite(val)
    assert val == pytest.approx(-math.log(scoring.LOG_LOSS_EPS))
    with pytest.raises(ValueError):
        scoring.log_score([0.5, 0.5, 0.0], 2)  # unclamped sibling still strict


def test_multiclass_brier_exact_and_aliases_brier_score():
    expected = 0.05**2 + 0.15**2 + (0.70 - 1) ** 2 + 0.10**2  # = 0.125
    assert scoring.multiclass_brier(P_EXAMPLE, WINNER_EXAMPLE) == pytest.approx(expected)
    assert scoring.multiclass_brier(P_EXAMPLE, WINNER_EXAMPLE) == scoring.brier_score(
        P_EXAMPLE, WINNER_EXAMPLE
    )


def test_ranked_probability_score_exact_worked_example():
    # cum_p over thresholds k=0,1,2: 0.05, 0.20, 0.90 ; cum_y steps to 1 at k=2.
    # RPS = 0.05² + 0.20² + (0.90-1)² = 0.0025 + 0.04 + 0.01 = 0.0525
    assert scoring.ranked_probability_score(P_EXAMPLE, WINNER_EXAMPLE) == pytest.approx(
        0.0525
    )


def test_rps_rewards_mass_near_the_winner():
    # Same p_winner (0.7) but the remaining 0.3 sits adjacent vs distant to winner=2.
    adjacent = [0.0, 0.30, 0.70, 0.0]  # mass on bin 1 (next to winner)
    distant = [0.30, 0.0, 0.70, 0.0]  # mass on bin 0 (far from winner)
    assert scoring.p_winner(adjacent, 2) == scoring.p_winner(distant, 2)
    assert scoring.ranked_probability_score(
        adjacent, 2
    ) < scoring.ranked_probability_score(distant, 2)


def test_winner_rank_and_reciprocal_rank():
    assert scoring.winner_rank(P_EXAMPLE, WINNER_EXAMPLE) == 1  # 0.70 is argmax
    assert scoring.reciprocal_rank(P_EXAMPLE, WINNER_EXAMPLE) == pytest.approx(1.0)
    # winner in the middle of the pack
    p = [0.40, 0.35, 0.20, 0.05]
    assert scoring.winner_rank(p, 2) == 3  # 0.20 is third
    assert scoring.reciprocal_rank(p, 2) == pytest.approx(1 / 3)
    # winner at the bottom
    p_bottom = [0.60, 0.30, 0.08, 0.02]
    assert scoring.winner_rank(p_bottom, 3) == 4


def test_winner_rank_ties_do_not_inflate():
    # winner shares top mass with another bin → still rank 1 (strict-greater count).
    assert scoring.winner_rank([0.4, 0.4, 0.2], 0) == 1
    assert scoring.winner_rank([0.4, 0.4, 0.2], 1) == 1


def test_top_k_hit_membership():
    p = [0.40, 0.35, 0.20, 0.05]
    assert scoring.top_k_hit(p, 0, 1) is True  # argmax
    assert scoring.top_k_hit(p, 2, 1) is False  # rank 3, not top-1
    assert scoring.top_k_hit(p, 2, 3) is True  # rank 3, within top-3
    assert scoring.top_k_hit(p, 3, 3) is False  # rank 4, outside top-3
    with pytest.raises(ValueError):
        scoring.top_k_hit(p, 0, 0)  # k must be >= 1


def test_validate_probability_group_accepts_normalized():
    scoring.validate_probability_group([0.25, 0.25, 0.25, 0.25])  # no raise
    scoring.validate_probability_group(P_EXAMPLE)


def test_validate_probability_group_rejects_bad_groups():
    with pytest.raises(ProbabilityGroupError, match="empty"):
        scoring.validate_probability_group([])
    with pytest.raises(ProbabilityGroupError, match="sums to"):
        scoring.validate_probability_group([0.5, 0.4])  # 0.9, outside tol
    with pytest.raises(ProbabilityGroupError, match="sums to"):
        scoring.validate_probability_group([0.5, 0.53])  # 1.03, outside tol
    with pytest.raises(ProbabilityGroupError, match="negative"):
        scoring.validate_probability_group([1.2, -0.2])
    with pytest.raises(ProbabilityGroupError, match="non-finite"):
        scoring.validate_probability_group([float("nan"), 1.0])


def test_validate_probability_group_tolerance_boundary():
    # exactly at tolerance edge passes; just beyond raises.
    tol = scoring.PROBABILITY_SUM_TOLERANCE
    scoring.validate_probability_group([1.0 + tol, 0.0])  # at edge → ok
    with pytest.raises(ProbabilityGroupError):
        scoring.validate_probability_group([1.0 + 2 * tol, 0.0])


def test_legacy_proper_rules_unchanged():
    # The pre-existing index-based rules used by skill.py must still behave.
    assert scoring.log_score(P_EXAMPLE, WINNER_EXAMPLE) == pytest.approx(-math.log(0.70))
    assert scoring.brier_score(P_EXAMPLE, WINNER_EXAMPLE) == pytest.approx(0.125)


# ── group-level SKILL composer (src/backtest/skill.py::score_forecast_vector) ──


def _resolution_for_value(value, **over):
    from src.contracts.calibration_bins import F_CANONICAL_GRID
    from src.contracts.settlement_resolution import SettlementResolution

    row = {
        "city": "nyc",
        "target_date": "2026-05-20",
        "temperature_metric": "high",
        "settlement_value": value,
        "settlement_unit": "F",
        "authority": "VERIFIED",
    }
    row.update(over)
    return SettlementResolution.from_settlement_row(row, F_CANONICAL_GRID), F_CANONICAL_GRID


def test_score_forecast_vector_valid_emits_metrics_no_pnl():
    from src.backtest.skill import score_forecast_vector
    from src.contracts.calibration_bins import F_CANONICAL_GRID

    bins = F_CANONICAL_GRID.as_bins()
    interior = next(b for b in bins if not b.is_shoulder and b.low is not None)
    value = (interior.low + interior.high) / 2.0
    res, grid = _resolution_for_value(value)

    labels = list(res.ordered_bin_labels)
    # put all mass on the winning bin
    p = [0.0] * len(labels)
    p[res.winning_bin_index] = 1.0

    out = score_forecast_vector(p, labels, res)
    assert out["group_integrity_status"] == "valid"
    assert out["p_winner"] == pytest.approx(1.0)
    assert out["top1_hit"] is True
    assert out["promotion_authority"] is False  # gating not wired in this PR
    # NO economics/PnL fields leak into a SKILL result
    for forbidden in ("realized_pnl", "win_rate", "sharpe", "max_drawdown"):
        assert forbidden not in out


def test_score_forecast_vector_excludes_bad_groups():
    from src.backtest.skill import score_forecast_vector
    from src.contracts.calibration_bins import F_CANONICAL_GRID

    interior = next(
        b for b in F_CANONICAL_GRID.as_bins() if not b.is_shoulder and b.low is not None
    )
    value = (interior.low + interior.high) / 2.0
    res, _ = _resolution_for_value(value)
    labels = list(res.ordered_bin_labels)

    # length mismatch
    out = score_forecast_vector([1.0], labels, res)
    assert out["group_integrity_status"] == "excluded"
    assert "length_mismatch" in out["group_exclusion_reason"]
    assert out["p_winner"] is None

    # bin grid mismatch (right length, wrong labels)
    wrong_labels = [f"bogus-{i}" for i in range(len(labels))]
    p_ok = [1.0 / len(labels)] * len(labels)
    out2 = score_forecast_vector(p_ok, wrong_labels, res)
    assert out2["group_integrity_status"] == "excluded"
    assert "bin_grid_mismatch" in out2["group_exclusion_reason"]

    # invalid distribution (does not sum to 1)
    bad = [0.1] * len(labels)
    out3 = score_forecast_vector(bad, labels, res)
    assert out3["group_integrity_status"] == "excluded"
    assert "invalid_distribution" in out3["group_exclusion_reason"]
