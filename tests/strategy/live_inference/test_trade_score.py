# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §12 robust TradeScore and execution-policy gates.
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.strategy.live_inference.state import LiveInferenceBlocked
from src.strategy.live_inference.trade_score import (
    TradeScoreInputs,
    assert_available_for_trade,
    assert_causal_snapshot,
    assert_passive_post_only_gate,
    assert_positive_trade_score,
    assert_taker_live_allowed,
    robust_trade_score,
)


def test_midpoint_edge_positive_ask_edge_negative_no_trade():
    score = robust_trade_score(
        TradeScoreInputs(
            p_fill_lcb=0.5,
            q_5pct=0.52,
            q_posterior=0.53,
            c_95pct=0.54,
            c_stress=0.55,
            lambda_edge=0.0,
            lambda_stress=0.0,
        )
    )
    with pytest.raises(LiveInferenceBlocked, match="TradeScore"):
        assert_positive_trade_score(score)


def test_missing_causal_snapshot_blocks():
    with pytest.raises(LiveInferenceBlocked, match="causal_snapshot"):
        assert_causal_snapshot(None)


def test_available_at_future_blocks_trade():
    with pytest.raises(LiveInferenceBlocked, match="after decision_time"):
        assert_available_for_trade(
            available_at=datetime(2026, 5, 24, 12, tzinfo=timezone.utc),
            decision_time=datetime(2026, 5, 24, 11, tzinfo=timezone.utc),
        )


def test_taker_fok_live_requires_execution_policy_review():
    with pytest.raises(LiveInferenceBlocked, match="taker FOK/FAK"):
        assert_taker_live_allowed(taker_fok_fak_live_enabled=False)


def test_passive_post_only_live_uses_passive_fill_gate():
    with pytest.raises(LiveInferenceBlocked, match="passive post-only"):
        assert_passive_post_only_gate(passive_fill_lcb=0.2, min_passive_fill_lcb=0.8)


def test_positive_trade_score_passes():
    score = robust_trade_score(
        TradeScoreInputs(
            p_fill_lcb=0.8,
            q_5pct=0.7,
            q_posterior=0.72,
            c_95pct=0.6,
            c_stress=0.61,
            lambda_edge=0.01,
            lambda_stress=0.01,
        )
    )
    assert score > 0
    assert_positive_trade_score(score)
