# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §11 LiveBinInferenceLayer contract.
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.strategy.live_inference.bayesian_factors import (
    apply_capped_llr,
    apply_market_prior_if_validated,
    assert_forecast_complete_for_live,
)
from src.strategy.live_inference.markov_smoothing import apply_markov_transition
from src.strategy.live_inference.state import (
    LiveBinState,
    LiveInferenceBlocked,
    apply_day0_mask,
    apply_orderbook_event_v1,
    assert_available_at,
)


def _state() -> LiveBinState:
    return LiveBinState({"70-74": 0.5, "75-79": 0.5}, datetime(2026, 5, 24, tzinfo=timezone.utc))


def test_p_live_normalizes():
    state = LiveBinState({"70-74": 2.0, "75-79": 1.0}, datetime(2026, 5, 24, tzinfo=timezone.utc))
    normalized = state.normalized()
    assert normalized.probabilities == pytest.approx({"70-74": 2 / 3, "75-79": 1 / 3})


def test_available_at_future_blocks():
    with pytest.raises(LiveInferenceBlocked, match="after decision_time"):
        assert_available_at(
            available_at=datetime(2026, 5, 24, 12, tzinfo=timezone.utc),
            decision_time=datetime(2026, 5, 24, 11, tzinfo=timezone.utc),
        )


def test_day0_mask_overrides_markov():
    transitioned = apply_markov_transition(
        _state(),
        {"70-74": {"70-74": 0.9, "75-79": 0.1}, "75-79": {"70-74": 0.1, "75-79": 0.9}},
    )
    masked = apply_day0_mask(transitioned, {"70-74": 0.0, "75-79": 1.0})
    assert masked.probabilities == {"70-74": 0.0, "75-79": 1.0}


def test_llr_cap_enforced():
    result = apply_capped_llr(_state(), {"75-79": 100.0}, llr_cap=1.0)
    assert result.probabilities["75-79"] < 0.75


def test_orderbook_event_does_not_change_q():
    state = _state()
    assert apply_orderbook_event_v1(state, {"best_bid": 0.7, "best_ask": 0.8}) is state


def test_market_prior_requires_validated_for_live():
    with pytest.raises(LiveInferenceBlocked, match="not validated"):
        apply_market_prior_if_validated(_state(), {"70-74": 0.8, "75-79": 0.2}, validated_for_live=False)


def test_partial_forecast_no_live_trade():
    with pytest.raises(LiveInferenceBlocked, match="partial forecast"):
        assert_forecast_complete_for_live("PARTIAL_ALLOWED")


def test_zero_mass_after_boundary_no_trade():
    with pytest.raises(LiveInferenceBlocked, match="zero probability mass"):
        apply_day0_mask(_state(), {"70-74": 0.0, "75-79": 0.0})
