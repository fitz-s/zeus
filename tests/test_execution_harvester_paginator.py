# Created: 2026-05-11
# Last reused or audited: 2026-07-13
# Authority basis: current Gamma weather scope and 100-row server cap, verified 2026-07-13
"""Relationship tests — trading-side harvester paginator bound (D.1).

Mirrors test_harvester_truth_writer_paginator.py for src/execution/harvester.py.
Additional assertion: temperature-keyword filter is preserved.
"""
from __future__ import annotations

import time
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _make_event(condition_id: str, end_date: str, title: str = "temperature high/low") -> dict:
    return {"conditionId": condition_id, "endDate": end_date, "title": title, "markets": []}


def _iso(days_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


class FakeResponse:
    def __init__(self, batch: list[dict]):
        self._batch = batch

    def raise_for_status(self):
        pass

    def json(self):
        return self._batch


# ---------------------------------------------------------------------------
# Test 1: HTTP params include order=endDate&ascending=false
# ---------------------------------------------------------------------------
def test_execution_http_params_include_descending_order():
    """Trading-side paginator must request the current weather-scoped server page."""
    from src.execution.harvester import (
        _fetch_settled_events,
        _GAMMA_EVENTS_PAGE_CAP,
        _SETTLEMENT_EVENT_TAG_SLUG,
    )

    recent_event = _make_event("cid-001", _iso(1), "temperature high/low")
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append(params.copy())
        if len(calls) == 1:
            return FakeResponse([recent_event])
        return FakeResponse([])

    with patch("httpx.get", side_effect=fake_get):
        _fetch_settled_events()

    assert len(calls) >= 1
    for p in calls:
        assert p.get("order") == "endDate", f"Missing order param in {p}"
        assert str(p.get("ascending")).lower() == "false", f"Expected ascending=false in {p}"
        assert p.get("limit") == _GAMMA_EVENTS_PAGE_CAP
        assert p.get("tag_slug") == _SETTLEMENT_EVENT_TAG_SLUG == "weather"


def test_execution_paginator_advances_by_server_capped_page_length():
    """A full 100-row Gamma page must not be mistaken for the final page."""
    from src.execution.harvester import (
        _fetch_settled_events,
        _GAMMA_EVENTS_PAGE_CAP,
    )

    page0 = [
        _make_event(f"future-{i}", _iso(-1), "temperature future")
        for i in range(_GAMMA_EVENTS_PAGE_CAP)
    ]
    target = _make_event("current-weather", _iso(1), "temperature current")
    pages = [page0, [target]]
    offsets = []

    def fake_get(url, *, params, timeout):
        offsets.append(params["offset"])
        return FakeResponse(pages[len(offsets) - 1])

    with patch("httpx.get", side_effect=fake_get):
        results = _fetch_settled_events()

    assert offsets == [0, _GAMMA_EVENTS_PAGE_CAP]
    assert any(event.get("conditionId") == "current-weather" for event in results)


# ---------------------------------------------------------------------------
# Test 2: Temperature-keyword filter is preserved
# ---------------------------------------------------------------------------
def test_execution_temperature_filter_preserved():
    """Non-temperature events must be filtered out; temperature events retained."""
    from src.execution.harvester import _fetch_settled_events

    events = [
        _make_event("cid-temp", _iso(1), "Will the temperature exceed 80°F?"),
        _make_event("cid-rain", _iso(1), "Will it rain tomorrow?"),
        _make_event("cid-deg", _iso(1), "High °c tomorrow"),
    ]

    call_count = 0

    def fake_get(url, *, params, timeout):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeResponse(events)
        return FakeResponse([])

    with patch("httpx.get", side_effect=fake_get):
        results = _fetch_settled_events()

    titles = [r.get("title", "") for r in results]
    # rain event must be filtered
    assert not any("rain" in t for t in titles), f"Rain event should have been filtered: {titles}"
    # temperature and °c events must be kept
    assert any("temperature" in t.lower() for t in titles), "Temperature event should be retained"


# ---------------------------------------------------------------------------
# Test 3: Paginator stops when batch min(endDate) < cutoff
# ---------------------------------------------------------------------------
def test_execution_paginator_stops_at_cutoff():
    """Trading-side paginator must break when oldest endDate falls below 30-day cutoff."""
    from src.execution.harvester import (
        _fetch_settled_events,
        _CLOSED_EVENTS_CUTOFF_DAYS,
        _CLOSED_EVENTS_PAGE_LIMIT,
    )

    # Use temperature events so they pass the filter
    page0 = [_make_event(f"cid-{i}", _iso(5), "temperature high") for i in range(_CLOSED_EVENTS_PAGE_LIMIT)]
    page1 = [_make_event(f"old-{i}", _iso(40), "temperature low") for i in range(_CLOSED_EVENTS_PAGE_LIMIT)]
    pages = [page0, page1, []]

    call_count = 0

    def fake_get(url, *, params, timeout):
        nonlocal call_count
        resp = FakeResponse(pages[call_count] if call_count < len(pages) else [])
        call_count += 1
        return resp

    with patch("httpx.get", side_effect=fake_get):
        results = _fetch_settled_events()

    assert call_count == 2, f"Expected 2 HTTP calls (stop after old page), got {call_count}"


# ---------------------------------------------------------------------------
# Test 4: Wall-cap truncates
# ---------------------------------------------------------------------------
def test_execution_wall_cap_truncates():
    """Trading-side paginator must break when wall time exceeds _CLOSED_EVENTS_MAX_WALL_SECONDS.

    Wall-cap is checked at the TOP of the while loop before each HTTP request.
    start_wall = first monotonic(); if second monotonic() already exceeds cap,
    zero HTTP calls are made (truncation fires before the first request).
    """
    from src.execution.harvester import (
        _fetch_settled_events,
        _CLOSED_EVENTS_MAX_WALL_SECONDS,
        _CLOSED_EVENTS_PAGE_LIMIT,
    )

    page = [_make_event(f"cid-{i}", _iso(1), "temperature high") for i in range(_CLOSED_EVENTS_PAGE_LIMIT)]
    call_count = 0
    # start_wall = 0.0; first loop check returns cap+11 → immediate truncation
    fake_times = [0.0, _CLOSED_EVENTS_MAX_WALL_SECONDS + 11]

    def fake_get(url, *, params, timeout):
        nonlocal call_count
        call_count += 1
        return FakeResponse(page)

    with patch("httpx.get", side_effect=fake_get):
        with patch("time.monotonic", side_effect=fake_times):
            results = _fetch_settled_events()

    # Wall-cap fires before first HTTP call → 0 calls, empty results
    assert call_count == 0, f"Expected 0 HTTP calls (wall-cap before first request), got {call_count}"
    assert results == []


# ---------------------------------------------------------------------------
# Test 5: Dedup by conditionId
# ---------------------------------------------------------------------------
def test_execution_dedup_by_condition_id():
    """Duplicate conditionId across pages must appear only once in results."""
    from src.execution.harvester import (
        _fetch_settled_events,
        _CLOSED_EVENTS_PAGE_LIMIT,
    )

    shared_id = "dup-cid-999"
    page0 = [_make_event(shared_id, _iso(1), "temperature high")] + [
        _make_event(f"cid-{i}", _iso(1), "temperature high") for i in range(_CLOSED_EVENTS_PAGE_LIMIT - 1)
    ]
    page1 = [_make_event(shared_id, _iso(2), "temperature low")] + [
        _make_event(f"other-{i}", _iso(2), "temperature low") for i in range(_CLOSED_EVENTS_PAGE_LIMIT - 1)
    ]
    pages = [page0, page1, []]

    call_count = 0

    def fake_get(url, *, params, timeout):
        nonlocal call_count
        resp = FakeResponse(pages[call_count] if call_count < len(pages) else [])
        call_count += 1
        return resp

    with patch("httpx.get", side_effect=fake_get):
        results = _fetch_settled_events()

    matching = [r for r in results if r.get("conditionId") == shared_id]
    assert len(matching) == 1, f"Expected 1 deduped event with conditionId={shared_id}, got {len(matching)}"


def test_held_position_event_is_fetched_by_exact_snapshot_slug():
    from src.execution.harvester import _supplement_held_position_settlement_events

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE executable_market_snapshots "
        "(condition_id TEXT, event_slug TEXT, captured_at TEXT)"
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?, ?, ?)",
        ("condition-held", "held-weather-event", "2026-07-13T18:00:00Z"),
    )
    portfolio = SimpleNamespace(
        positions=[SimpleNamespace(condition_id="condition-held")]
    )
    held_event = {
        "id": "event-held",
        "slug": "held-weather-event",
        "closed": True,
        "title": "Highest temperature in Held City?",
        "markets": [],
    }
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append(params)
        return FakeResponse([held_event])

    with patch(
        "src.state.db.get_trade_connection_read_only",
        return_value=conn,
    ), patch("httpx.get", side_effect=fake_get):
        result = _supplement_held_position_settlement_events(portfolio, [])

    assert calls == [{"slug": "held-weather-event"}]
    assert result == [held_event]
