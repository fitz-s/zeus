# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: F25 audit / Strategy R sentinel contract (FIX_F25_DSI.md)

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from src.config import City
from src.engine.evaluator import (
    MarketCandidate,
    _PRE_SNAPSHOT_DSI_SENTINEL,
    evaluate_candidate,
)

_SENTINEL_RE = re.compile(r"^<pre_snapshot:.+>$")


def _city() -> City:
    return City(
        name="NYC",
        lat=40.78,
        lon=-73.87,
        timezone="America/New_York",
        cluster="NYC",
        settlement_unit="F",
        wu_station="KLGA",
    )


def _candidate_few_bins() -> MarketCandidate:
    """Empty outcomes — bins=[] triggers MARKET_FILTER before snapshot resolution."""
    return MarketCandidate(
        city=_city(),
        target_date="2026-06-01",
        outcomes=[],
        hours_since_open=24.0,
        temperature_metric="high",
    )


def test_early_rejection_dsi_is_sentinel():
    """< 3 bins triggers MARKET_FILTER before snapshot; DSI must be sentinel."""
    decisions = evaluate_candidate(
        _candidate_few_bins(),
        conn=None,
        portfolio=MagicMock(),
        clob=MagicMock(),
        limits=MagicMock(),
    )
    assert decisions, "evaluate_candidate must return at least one decision"
    d = decisions[0]
    assert d.rejection_stage == "MARKET_FILTER", (
        f"Expected MARKET_FILTER, got {d.rejection_stage!r}"
    )
    assert _SENTINEL_RE.match(d.decision_snapshot_id), (
        f"Expected sentinel matching {_SENTINEL_RE.pattern!r}, "
        f"got {d.decision_snapshot_id!r}"
    )
    assert d.decision_snapshot_id == _PRE_SNAPSHOT_DSI_SENTINEL


def test_edge_decision_rejects_none_dsi():
    """EdgeDecision.__post_init__ must raise ValueError when decision_snapshot_id is None."""
    from src.engine.evaluator import EdgeDecision

    with pytest.raises(ValueError, match="decision_snapshot_id must not be None"):
        EdgeDecision(
            should_trade=False,
            rejection_stage="TEST",
            decision_snapshot_id=None,  # type: ignore[arg-type]
        )
