from __future__ import annotations

# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Authority basis: Operator request — portfolio rotation must be universal and must not rotate live capital into lower-than-51% win-rate lottery legs.

import pytest

from src.strategy.portfolio_rotation import (
    RotationCandidate,
    RotationHold,
    best_rotation,
    candidate_future_value,
    evaluate_rotation,
    held_future_value,
    held_net_sell_value,
)


def _hold(**overrides) -> RotationHold:
    fields = {
        "position_id": "pos-1",
        "city": "Seoul",
        "target_date": "2026-06-08",
        "metric": "high",
        "bin_label": "25C",
        "direction": "buy_no",
        "shares": 10.0,
        "held_probability": 0.80,
        "held_side_best_bid": 0.79,
        "token_id": "held-token",
        "condition_id": "held-condition",
    }
    fields.update(overrides)
    return RotationHold(**fields)


def _candidate(**overrides) -> RotationCandidate:
    fields = {
        "event_id": "evt-1",
        "city": "Madrid",
        "target_date": "2026-06-08",
        "metric": "high",
        "bin_label": "34C",
        "direction": "buy_no",
        "q_lcb": 0.88,
        "fee_adjusted_cost": 0.55,
        "trade_score": 0.20,
        "p_fill_lcb": 1.0,
        "token_id": "candidate-token",
        "condition_id": "candidate-condition",
        "rejection_reason": "KELLY_REJECTED:corr_budget",
    }
    fields.update(overrides)
    return RotationCandidate(**fields)


def test_rotation_uses_after_fee_sell_value_and_candidate_entry_cost() -> None:
    hold = _hold(shares=10.0, held_probability=0.80, held_side_best_bid=0.79)
    candidate = _candidate(q_lcb=0.88, fee_adjusted_cost=0.55)

    decision = evaluate_rotation(hold, candidate, fee_rate=0.05)

    assert decision.action == "ROTATE"
    assert decision.reason == "ROTATION_REPLACE_CANDIDATE"
    assert decision.sell_value_usd == pytest.approx(7.81705)
    assert decision.hold_future_value_usd == pytest.approx(8.0)
    assert decision.candidate_future_value_usd == pytest.approx(12.50728, rel=1e-5)
    assert decision.net_improvement_usd > 4.0


def test_high_quality_hold_is_not_replaced_by_lower_future_value() -> None:
    hold = _hold(shares=10.0, held_probability=0.99, held_side_best_bid=0.94)
    candidate = _candidate(q_lcb=0.82, fee_adjusted_cost=0.80)

    decision = evaluate_rotation(hold, candidate, fee_rate=0.05)

    assert decision.action == "HOLD"
    assert decision.reason == "HOLD_VALUE_DOMINANT"
    assert decision.candidate_future_value_usd < decision.hold_future_value_usd


def test_same_token_never_generates_rotation() -> None:
    hold = _hold(token_id="same-token")
    candidate = _candidate(token_id="same-token", q_lcb=0.99, fee_adjusted_cost=0.25)

    decision = evaluate_rotation(hold, candidate, fee_rate=0.05)

    assert decision.action == "HOLD"
    assert decision.reason == "SAME_TOKEN"


def test_very_low_fill_lcb_blocks_non_atomic_sell_then_buy_regression() -> None:
    hold = _hold(shares=10.0, held_probability=0.80, held_side_best_bid=0.79)
    candidate = _candidate(q_lcb=0.95, fee_adjusted_cost=0.20, p_fill_lcb=0.001)

    decision = evaluate_rotation(hold, candidate, fee_rate=0.05)

    assert decision.action == "HOLD"
    assert decision.reason == "HOLD_VALUE_DOMINANT"
    assert decision.fill_lcb_used == pytest.approx(0.001)


def test_missing_fill_lcb_fails_closed_by_default() -> None:
    candidate = _candidate(p_fill_lcb=None)

    future, fill_lcb = candidate_future_value(
        candidate,
        released_cash_usd=10.0,
    )

    assert future == 0.0
    assert fill_lcb == 0.0


def test_best_rotation_is_global_not_first_or_city_specific() -> None:
    holds = [
        _hold(position_id="weak", city="Seoul", held_probability=0.80, held_side_best_bid=0.79),
        _hold(position_id="strong", city="Tokyo", held_probability=0.99, held_side_best_bid=0.20),
    ]
    candidates = [
        _candidate(event_id="small", city="Madrid", q_lcb=0.84, fee_adjusted_cost=0.65),
        _candidate(event_id="large", city="Hong Kong", q_lcb=0.98, fee_adjusted_cost=0.50),
    ]

    decision = best_rotation(holds, candidates, fee_rate=0.05)

    assert decision is not None
    assert decision.hold.position_id == "weak"
    assert decision.candidate.event_id == "large"


def test_rotation_rejects_low_win_rate_positive_ev_lottery_candidate() -> None:
    hold = _hold(shares=24.68, held_probability=0.94, held_side_best_bid=0.70)
    lottery_candidate = _candidate(
        event_id="low-q-positive-ev",
        city="Jeddah",
        bin_label="42C",
        direction="buy_yes",
        q_lcb=0.0784,
        fee_adjusted_cost=0.0084,
        trade_score=0.0236,
        p_fill_lcb=0.40,
    )

    decision = evaluate_rotation(hold, lottery_candidate, fee_rate=0.05)

    assert decision.action == "HOLD"
    assert decision.reason.startswith("ADMISSION_WIN_RATE_FLOOR:")
    assert decision.candidate_future_value_usd == 0.0


def test_hurdles_prevent_churn_on_tiny_improvements() -> None:
    hold = _hold(shares=10.0, held_probability=0.80, held_side_best_bid=0.79)
    candidate = _candidate(q_lcb=0.565, fee_adjusted_cost=0.55)

    decision = evaluate_rotation(
        hold,
        candidate,
        fee_rate=0.05,
        min_net_improvement_usd=0.10,
    )

    assert 0.0 < decision.net_improvement_usd < 0.10
    assert decision.action == "HOLD"
    assert decision.reason == "IMPROVEMENT_BELOW_DOLLAR_HURDLE"


def test_invalid_candidate_cost_fails_closed() -> None:
    with pytest.raises(ValueError, match="fee_adjusted_cost"):
        _candidate(fee_adjusted_cost=1.0)


def test_hold_value_primitives_are_direction_agnostic_held_side_math() -> None:
    buy_yes = _hold(direction="buy_yes", held_probability=0.70, held_side_best_bid=0.68)
    buy_no = _hold(direction="buy_no", held_probability=0.70, held_side_best_bid=0.68)

    assert held_future_value(buy_yes) == pytest.approx(held_future_value(buy_no))
    assert held_net_sell_value(buy_yes, fee_rate=0.05) == pytest.approx(
        held_net_sell_value(buy_no, fee_rate=0.05)
    )
