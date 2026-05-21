# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=2026-05-19
# Purpose: Antibody tests for negRisk event.closed semantic — client-side acceptingOrders gate
# Reuse: Run via pytest tests/test_market_scanner_negrisk.py
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: Polymarket negRisk semantic verified 2026-05-19; task brief fix/market-scanner-negrisk-acceptingorders
"""Antibody tests: negRisk event.closed semantic drift.

Polymarket negRisk multi-outcome events have event.closed=True while still
tradeable (child.acceptingOrders=True). The closed=false API filter returns 0
results for these events. These tests pin the client-side tradeability gate.

Relationship invariant:
  "An event with event.closed=True, endDate>=now, and at least one
   child.acceptingOrders=True MUST be admitted by _event_has_active_children."

Sed-break verification: at least one test must FAIL if the old closed=false
filter is restored (i.e. if _event_has_active_children is removed and
closed=false is put back).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.data.market_scanner import _event_has_active_children


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future_iso(days: int = 5) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 5) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _negrisk_event(
    *,
    end_date: str,
    event_closed: bool = True,
    event_active: bool = True,
    child_accepting: bool = True,
) -> dict:
    """Build a negRisk-shaped event (Tokyo May-23 template)."""
    return {
        "id": "tokyo-lowest-temp-may-23-2026",
        "slug": "lowest-temperature-in-tokyo-on-may-23-2026",
        "title": "Lowest temperature in Tokyo on May 23, 2026",
        "active": event_active,
        "closed": event_closed,
        "endDate": end_date,
        "markets": [
            {
                "id": f"child-{i}",
                "question": f"Lowest temp bin {i}",
                "acceptingOrders": child_accepting if i == 0 else False,
                "active": False,
                "closed": False,
            }
            for i in range(3)
        ],
    }


# ---------------------------------------------------------------------------
# Core negRisk admission test (sed-break target)
# ---------------------------------------------------------------------------

def test_find_weather_markets_negrisk_event_closed_true_with_accepting_children_is_admitted():
    """negRisk event: event.closed=True, future endDate, child accepting → admitted.

    This is the Tokyo May-23 shape that was broken by the closed=false API filter.
    If _event_has_active_children is removed (reverted to closed=false), this test
    still passes in isolation — but the live query returns 0, causing the scanner
    to fall back to keyword search. The sed-break test below catches the revert.
    """
    event = _negrisk_event(end_date=_future_iso(4), event_closed=True, child_accepting=True)
    assert _event_has_active_children(event, _now_utc()) is True


def test_find_weather_markets_excludes_past_enddate():
    """Event with endDate in the past must be excluded even if child is accepting."""
    event = _negrisk_event(end_date=_past_iso(2), child_accepting=True)
    assert _event_has_active_children(event, _now_utc()) is False


def test_find_weather_markets_excludes_no_accepting_children():
    """Event with future endDate but ALL children have acceptingOrders=False → excluded."""
    event = _negrisk_event(end_date=_future_iso(4), child_accepting=False)
    assert _event_has_active_children(event, _now_utc()) is False


def test_find_weather_markets_no_markets_field_excluded():
    """Event with no markets list at all → excluded (no accepting children)."""
    event = {
        "id": "no-markets",
        "slug": "no-markets",
        "endDate": _future_iso(3),
        "active": True,
        "closed": False,
    }
    assert _event_has_active_children(event, _now_utc()) is False


def test_find_weather_markets_empty_markets_list_excluded():
    """Event with empty markets list → excluded."""
    event = {
        "id": "empty-markets",
        "slug": "empty-markets",
        "endDate": _future_iso(3),
        "markets": [],
    }
    assert _event_has_active_children(event, _now_utc()) is False


def test_find_weather_markets_missing_enddate_passes_through():
    """Event with missing endDate is not blocked by date check (left to _parse_event)."""
    event = {
        "id": "no-date",
        "slug": "no-date",
        "markets": [{"id": "c1", "acceptingOrders": True}],
    }
    assert _event_has_active_children(event, _now_utc()) is True


# ---------------------------------------------------------------------------
# Sed-break meta-verify: must FAIL if closed=false filter is naively restored
#
# The helper _event_has_active_children is what makes a negRisk-closed event
# visible. If a future refactor removes the helper and restores closed=false
# on the API call, the token-accepting-but-closed events would be filtered
# server-side and never reach the client. This test verifies that the helper
# itself is the discriminator by testing the exact shape that the old filter
# missed.
# ---------------------------------------------------------------------------

def test_sed_break_negrisk_closed_event_is_not_rejected_by_helper():
    """Sed-break: _event_has_active_children must NOT reject closed=True events.

    Old behavior (closed=false param): server filtered these out → 0 results.
    New behavior: client gate admits them based on child.acceptingOrders.

    If this function is deleted or replaced with a `not event.get('closed')`
    guard, this test fails — signalling the regression.
    """
    event = _negrisk_event(
        end_date=_future_iso(6),
        event_closed=True,   # <-- this is what the old filter rejected server-side
        event_active=True,
        child_accepting=True,
    )
    # Must be admitted
    result = _event_has_active_children(event, _now_utc())
    assert result is True, (
        "Regression: _event_has_active_children rejected an event with "
        "event.closed=True + child.acceptingOrders=True. The negRisk fix was reverted."
    )


# ---------------------------------------------------------------------------
# API discriminator: _fetch_events_by_tags must NOT pass closed=false
#
# If `closed=false` is reintroduced in the API params, Polymarket returns 0
# results for negRisk events — causing the scanner to miss all weather markets.
# This test mocks _gamma_get and asserts the discriminator is absent from params.
# ---------------------------------------------------------------------------

def test_fetch_events_by_tags_does_not_pass_closed_false_param():
    """API discriminator: _fetch_events_by_tags must never include closed=false in params.

    Sed-break: restoring `"closed": "false"` to the _gamma_get params in
    _fetch_events_by_tags will cause this test to FAIL immediately.
    """
    from unittest.mock import MagicMock, patch
    from src.data.market_scanner import _fetch_events_by_tags

    captured_params: list[dict] = []

    def fake_gamma_get(path, *, params=None, **kwargs):
        if params:
            captured_params.append(dict(params))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Return tag data for /tags/slug/... calls, empty event list otherwise
        if "/tags/slug/" in path:
            mock_resp.json.return_value = {"id": 99, "slug": path.split("/")[-1]}
        else:
            mock_resp.json.return_value = []  # empty batch → stops pagination
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    with patch("src.data.market_scanner._gamma_get", side_effect=fake_gamma_get):
        _fetch_events_by_tags()

    event_params = [p for p in captured_params if "tag_id" in p]
    assert event_params, "No event-fetch params captured — _fetch_events_by_tags did not call _gamma_get"
    for p in event_params:
        assert p.get("closed") != "false", (
            f"Regression: 'closed=false' found in API params {p}. "
            "negRisk events are invisible to this filter — revert causes 0 weather markets found."
        )


# ---------------------------------------------------------------------------
# Phase 2 antibody: child-level _market_child_is_tradable negRisk semantic
# (added 2026-05-19 after live-stage 0-markets blocker — PR #184 fixed event
# level; same active=False ≠ untradeable invariant applies at the child level)
# ---------------------------------------------------------------------------

from src.data.market_scanner import (
    _market_child_is_tradable,
    capture_executable_market_snapshot,
)
from src.state.snapshot_repo import init_snapshot_schema


_NEGRISK_TRADEABLE_CHILD = {
    # Verified 2026-05-19 via direct Gamma probe: every highest-temperature
    # child returned active=False but was accepting and shown on UI.
    "active": False,
    "acceptingOrders": True,
    "closed": False,
    "enableOrderBook": True,
}


def test_negrisk_child_active_false_is_tradable() -> None:
    """negRisk child with active=False and acceptingOrders=True MUST admit.

    Sed-break: reintroducing 'active is True' in _market_child_is_tradable
    flips this to False and live finds 0 markets again.
    """
    assert _market_child_is_tradable(_NEGRISK_TRADEABLE_CHILD) is True


def test_child_missing_accepting_is_not_tradable() -> None:
    """Unknown/missing acceptingOrders MUST remain not-tradable (fail-closed)."""
    child = {k: v for k, v in _NEGRISK_TRADEABLE_CHILD.items() if k != "acceptingOrders"}
    assert _market_child_is_tradable(child) is False


def test_child_closed_overrides_accepting() -> None:
    """closed=True wins even with acceptingOrders=True (stale Gamma routing)."""
    child = dict(_NEGRISK_TRADEABLE_CHILD)
    child["closed"] = True
    assert _market_child_is_tradable(child) is False


def test_child_orderbook_disabled_is_not_tradable() -> None:
    """enableOrderBook=False means no CLOB liquidity — must reject."""
    child = dict(_NEGRISK_TRADEABLE_CHILD)
    child["enableOrderBook"] = False
    assert _market_child_is_tradable(child) is False


class _FakeClob:
    def get_clob_market_info(self, condition_id: str) -> dict:
        return {
            "condition_id": condition_id,
            "tokens": [
                {"token_id": "yes-token"},
                {"token_id": "no-token"},
            ],
            "tick_size": "0.01",
            "min_order_size": "5",
            "neg_risk": True,
        }

    def get_orderbook_snapshot(self, token_id: str) -> dict:
        return {
            "asset_id": token_id,
            "tick_size": "0.01",
            "min_order_size": "5",
            "neg_risk": True,
            "bids": [{"price": "0.02", "size": "10"}],
            "asks": [{"price": "0.03", "size": "12"}],
        }

    def get_fee_rate_details(self, token_id: str) -> dict:
        return {"bps": 0, "source": "clob_fee_rate", "token_id": token_id}


def test_negrisk_child_active_false_accepting_true_capture_snapshot_admits() -> None:
    """Snapshot capture must share scanner tradeability semantics.

    Sed-break: reintroducing ``not active`` in capture_executable_market_snapshot
    rejects this otherwise tradable negRisk child before CLOB evidence can be
    captured.
    """

    conn = sqlite3.connect(":memory:")
    init_snapshot_schema(conn)
    gamma_child = {
        **_NEGRISK_TRADEABLE_CHILD,
        "id": "gamma-child",
        "condition_id": "cond-active-false",
        "question_id": "question-active-false",
        "clobTokenIds": ["yes-token", "no-token"],
    }
    market = {
        "id": "event-active-false",
        "event_id": "event-active-false",
        "slug": "highest-temperature-in-chicago-on-june-15-2026",
        "outcomes": [
            {
                "market_id": "cond-active-false",
                "condition_id": "cond-active-false",
                "question_id": "question-active-false",
                "gamma_market_id": "gamma-child",
                "token_id": "yes-token",
                "no_token_id": "no-token",
                "active": False,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "gamma_market_raw": gamma_child,
            }
        ],
    }
    decision = SimpleNamespace(
        tokens={
            "market_id": "cond-active-false",
            "token_id": "yes-token",
            "no_token_id": "no-token",
        },
        edge=SimpleNamespace(direction="buy_yes"),
    )

    result = capture_executable_market_snapshot(
        conn,
        market=market,
        decision=decision,
        clob=_FakeClob(),
        captured_at=datetime.now(timezone.utc),
        scan_authority="VERIFIED",
    )

    row = conn.execute(
        "SELECT condition_id, active, closed, accepting_orders FROM executable_market_snapshots"
    ).fetchone()
    assert result["condition_id"] == "cond-active-false"
    assert row == ("cond-active-false", 0, 0, 1)
