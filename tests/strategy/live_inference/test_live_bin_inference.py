# Created: 2026-05-24
# Last reused/audited: 2026-07-08
# Authority basis: EDLI v1 implementation prompt §11 LiveBinInferenceLayer contract.
# 2026-07-08: bayesian_factors.py and markov_smoothing.py deleted as zero-caller corpses
# (R0-c purge); tests that exercised only those two dead modules were removed with them.
# The state.py-only tests below (LiveBinState, assert_available_at, apply_orderbook_event,
# apply_day0_mask) are retained — state.py is still live (used by trade_score.py and
# inference_engine.py).
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.strategy.live_inference.state import (
    LiveBinState,
    LiveInferenceBlocked,
    apply_day0_mask,
    apply_orderbook_event,
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


def test_orderbook_event_does_not_change_q():
    state = _state()
    assert apply_orderbook_event(state, {"best_bid": 0.7, "best_ask": 0.8}) is state


def test_zero_mass_after_boundary_no_trade():
    with pytest.raises(LiveInferenceBlocked, match="zero probability mass"):
        apply_day0_mask(_state(), {"70-74": 0.0, "75-79": 0.0})
