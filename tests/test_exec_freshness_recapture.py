# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: docs/operations/EXEC_FRESHNESS_ROOTCAUSE_2026-05-24.md
"""Relationship test (cross-module invariant) for fresh-at-submit re-capture.

Invariant under test (discovery→execution boundary):
  A decision repriced against an executable snapshot that has aged past the 30s
  freshness window MUST trigger a fresh single-market re-capture and yield a
  fresh snapshot, NOT raise ``executable_snapshot_stale`` — *provided a CLOB
  client is available*. When no client is available the 30s safety gate is
  preserved (still raises).

This pins the root cause of zero live entries (2026-05-24): the 5-min mode run's
discovery→reprice latency exceeds the 30s window, so the persisted cycle snapshot
is already stale at submit, and reprice re-read it without re-capturing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.state.db import init_schema
from src.state.snapshot_repo import get_snapshot, insert_snapshot
from src.contracts.executable_market_snapshot_v2 import (
    ExecutableMarketSnapshotV2,
    FRESHNESS_WINDOW_DEFAULT,
    is_fresh,
)

NOW = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
HASH = "a" * 64


class _FakeClob:
    """Minimal CLOB client exposing the surface capture_executable_market_snapshot needs."""

    def __init__(self) -> None:
        self.orderbook = {
            "asset_id": "yes-token",
            "tick_size": "0.01",
            "min_order_size": "5",
            "neg_risk": False,
            "bids": [{"price": "0.49", "size": "100"}],
            "asks": [{"price": "0.51", "size": "100"}],
        }
        self.market_info = {
            "condition_id": "condition-1",
            "tokens": [{"token_id": "yes-token"}, {"token_id": "no-token"}],
            "feesEnabled": True,
        }

    def get_clob_market_info(self, condition_id: str) -> dict:
        return self.market_info

    def get_orderbook_snapshot(self, token_id: str) -> dict:
        return self.orderbook

    def get_fee_rate(self, token_id: str) -> float:
        return 0.0


def _market() -> dict:
    return {
        "event_id": "event-1",
        "slug": "weather-nyc-high",
        "outcomes": [
            {
                "title": "Will NYC high temp be 39-40F?",
                "token_id": "yes-token",
                "no_token_id": "no-token",
                "price": 0.49,
                "no_price": 0.51,
                "market_id": "condition-1",
                "condition_id": "condition-1",
                "question_id": "question-1",
                "gamma_market_id": "gamma-1",
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "executable": True,
                "neg_risk": False,
                "market_end_at": (NOW + timedelta(days=1)).isoformat(),
                "token_map_raw": {"YES": "yes-token", "NO": "no-token"},
                "raw_gamma_payload_hash": HASH,
                "gamma_market_raw": {
                    "id": "gamma-1",
                    "conditionId": "condition-1",
                    "questionID": "question-1",
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "negRisk": False,
                    "clobTokenIds": ["yes-token", "no-token"],
                },
            }
        ],
    }


def _decision(direction: str = "buy_yes"):
    return SimpleNamespace(
        tokens={"market_id": "condition-1", "token_id": "yes-token", "no_token_id": "no-token"},
        edge=SimpleNamespace(direction=direction),
    )


def _stale_snapshot(snapshot_id: str = "snap-stale", *, captured_at: datetime) -> ExecutableMarketSnapshotV2:
    return ExecutableMarketSnapshotV2(
        snapshot_id=snapshot_id,
        gamma_market_id="gamma-1",
        event_id="event-1",
        event_slug="weather-nyc-high",
        condition_id="condition-1",
        question_id="question-1",
        yes_token_id="yes-token",
        no_token_id="no-token",
        selected_outcome_token_id="yes-token",
        outcome_label="YES",
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=NOW - timedelta(hours=1),
        market_end_at=NOW + timedelta(days=1),
        market_close_at=NOW + timedelta(days=1, hours=1),
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        fee_details={"bps": 0, "source": "test"},
        token_map_raw={"YES": "yes-token", "NO": "no-token"},
        rfqe=None,
        neg_risk=False,
        orderbook_top_bid=Decimal("0.49"),
        orderbook_top_ask=Decimal("0.51"),
        orderbook_depth_jsonb='{"asks":[["0.51","100"]],"bids":[["0.49","100"]]}',
        raw_gamma_payload_hash=HASH,
        raw_clob_market_info_hash=HASH,
        raw_orderbook_hash=HASH,
        authority_tier="CLOB",
        captured_at=captured_at,
        freshness_deadline=captured_at + FRESHNESS_WINDOW_DEFAULT,
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


def _import_helper():
    from src.engine.cycle_runtime import _ensure_fresh_executable_snapshot

    return _ensure_fresh_executable_snapshot


def test_stale_snapshot_with_clob_recaptures_fresh(conn):
    """RED→GREEN: stale persisted snapshot + a client must re-capture, not raise stale."""
    stale_at = NOW - (FRESHNESS_WINDOW_DEFAULT + timedelta(seconds=30))
    insert_snapshot(conn, _stale_snapshot(captured_at=stale_at))
    assert not is_fresh(get_snapshot(conn, "snap-stale"), NOW)  # precondition: stale

    helper = _import_helper()
    fresh = helper(
        conn,
        "snap-stale",
        now=NOW,
        clob=_FakeClob(),
        decision=_decision(),
        market=_market(),
    )
    assert fresh is not None
    assert is_fresh(fresh, NOW), "re-captured snapshot must satisfy the 30s freshness gate"


def test_stale_snapshot_without_clob_preserves_safety_gate(conn):
    """No client available → 30s safety gate is preserved (still raises stale)."""
    stale_at = NOW - (FRESHNESS_WINDOW_DEFAULT + timedelta(seconds=30))
    insert_snapshot(conn, _stale_snapshot(captured_at=stale_at))
    helper = _import_helper()
    with pytest.raises(ValueError, match="executable_snapshot_stale"):
        helper(conn, "snap-stale", now=NOW, clob=None, decision=_decision(), market=_market())


def test_fresh_snapshot_passthrough_no_recapture(conn):
    """Fresh snapshot is returned as-is; no re-capture attempted (clob unused)."""
    insert_snapshot(conn, _stale_snapshot(captured_at=NOW))  # fresh

    class _Boom:
        def __getattr__(self, name):
            raise AssertionError("clob must not be touched when snapshot is fresh")

    helper = _import_helper()
    snap = helper(conn, "snap-stale", now=NOW, clob=_Boom(), decision=_decision(), market=_market())
    assert snap is not None and is_fresh(snap, NOW)
