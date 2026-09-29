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
        settlement_source_type="wu_icao",
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


def test_edge_decision_rejects_none_dsi():
    """EdgeDecision.__post_init__ must raise ValueError when decision_snapshot_id is None."""
    from src.engine.evaluator import EdgeDecision

    with pytest.raises(ValueError, match="decision_snapshot_id must not be None"):
        EdgeDecision(
            should_trade=False,
            rejection_stage="TEST",
            decision_snapshot_id=None,  # type: ignore[arg-type]
        )
