# Created: 2026-05-11
# Last reused or audited: 2026-05-11
# Authority basis: PLAN.md §D.1, critic v4 ACCEPT 2026-05-11
"""Relationship tests — ingest-side harvester paginator bound (D.1).

Verifies:
  1. HTTP requests carry order=endDate&ascending=false
  2. Paginator stops when batch min(endDate) < 30-day cutoff
  3. Wall-cap fires when cumulative time > _CLOSED_EVENTS_MAX_WALL_SECONDS
  4. Dedup by conditionId removes overlapping events across pages
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


def _make_event(condition_id: str, end_date: str) -> dict:
    return {"conditionId": condition_id, "endDate": end_date, "markets": []}


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
def test_http_params_include_descending_order():
    """Paginator must pass order=endDate&ascending=false on every request."""
    from src.ingest.harvester_truth_writer import _fetch_open_settling_markets

    recent_event = _make_event("cid-001", _iso(1))
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append(params.copy())
        if len(calls) == 1:
            return FakeResponse([recent_event])
        return FakeResponse([])

    with patch("httpx.get", side_effect=fake_get):
        with patch("src.data.market_scanner.GAMMA_BASE", "http://test"):
            _fetch_open_settling_markets()

    assert len(calls) >= 1
    for p in calls:
        assert p.get("order") == "endDate", f"Missing order param in {p}"
        assert str(p.get("ascending")).lower() == "false", f"Expected ascending=false in {p}"


# ---------------------------------------------------------------------------
# Test 2: Paginator stops when batch min(endDate) < cutoff
# ---------------------------------------------------------------------------
def test_paginator_stops_at_cutoff():
    """Paginator must break when oldest endDate in batch falls below 30-day cutoff."""
    from src.ingest.harvester_truth_writer import (
        _fetch_open_settling_markets,
        _CLOSED_EVENTS_CUTOFF_DAYS,
        _CLOSED_EVENTS_PAGE_LIMIT,
    )

    # Page 0: all recent (5 days ago) — full page so loop continues
    page0 = [_make_event(f"cid-{i}", _iso(5)) for i in range(_CLOSED_EVENTS_PAGE_LIMIT)]
    # Page 1: all older than cutoff (40 days ago) — triggers break
    page1 = [_make_event(f"old-{i}", _iso(40)) for i in range(_CLOSED_EVENTS_PAGE_LIMIT)]
    pages = [page0, page1, []]

    call_count = 0

    def fake_get(url, *, params, timeout):
        nonlocal call_count
        resp = FakeResponse(pages[call_count] if call_count < len(pages) else [])
        call_count += 1
        return resp

    with patch("httpx.get", side_effect=fake_get):
        with patch("src.data.market_scanner.GAMMA_BASE", "http://test"):
            results = _fetch_open_settling_markets()

    # Must stop after page1 (which triggered cutoff), not fetch page2
    assert call_count == 2, f"Expected 2 HTTP calls (stop after old page), got {call_count}"
    # Both pages kept per PLAN "absorb same-day tie by keeping this page"
    assert len(results) == _CLOSED_EVENTS_PAGE_LIMIT * 2


# ---------------------------------------------------------------------------
# Test 3: Wall-cap fires and truncates
# ---------------------------------------------------------------------------
def test_wall_cap_truncates():
    """Paginator must break with a warning when wall time exceeds _CLOSED_EVENTS_MAX_WALL_SECONDS.

    Wall-cap is checked at the TOP of the while loop before each HTTP request.
    start_wall = first monotonic(); if second monotonic() already exceeds cap,
    zero HTTP calls are made (truncation fires before the first request).
    """
    from src.ingest.harvester_truth_writer import (
        _fetch_open_settling_markets,
        _CLOSED_EVENTS_MAX_WALL_SECONDS,
        _CLOSED_EVENTS_PAGE_LIMIT,
    )

    page = [_make_event(f"cid-{i}", _iso(1)) for i in range(_CLOSED_EVENTS_PAGE_LIMIT)]

    call_count = 0
    # start_wall = 0.0; first loop check returns cap+11 → immediate truncation
    # before any HTTP request (wall-cap fires at loop top, not after first call)
    fake_times = [0.0, _CLOSED_EVENTS_MAX_WALL_SECONDS + 11]

    def fake_get(url, *, params, timeout):
        nonlocal call_count
        call_count += 1
        return FakeResponse(page)

    with patch("httpx.get", side_effect=fake_get):
        with patch("src.data.market_scanner.GAMMA_BASE", "http://test"):
            with patch("time.monotonic", side_effect=fake_times):
                results = _fetch_open_settling_markets()

    # Wall-cap fires before first HTTP call → 0 calls, empty results
    assert call_count == 0, f"Expected 0 HTTP calls (wall-cap before first request), got {call_count}"
    assert results == []


# ---------------------------------------------------------------------------
# Test 4: Dedup by conditionId removes overlapping events
# ---------------------------------------------------------------------------
def test_dedup_by_condition_id():
    """Events with duplicate conditionId across pages must appear only once."""
    from src.ingest.harvester_truth_writer import (
        _fetch_open_settling_markets,
        _CLOSED_EVENTS_PAGE_LIMIT,
    )

    shared_id = "dup-cid-999"
    page0 = [_make_event(shared_id, _iso(1))] + [
        _make_event(f"cid-{i}", _iso(1)) for i in range(_CLOSED_EVENTS_PAGE_LIMIT - 1)
    ]
    page1 = [_make_event(shared_id, _iso(2))] + [
        _make_event(f"other-{i}", _iso(2)) for i in range(_CLOSED_EVENTS_PAGE_LIMIT - 1)
    ]
    pages = [page0, page1, []]

    call_count = 0

    def fake_get(url, *, params, timeout):
        nonlocal call_count
        resp = FakeResponse(pages[call_count] if call_count < len(pages) else [])
        call_count += 1
        return resp

    with patch("httpx.get", side_effect=fake_get):
        with patch("src.data.market_scanner.GAMMA_BASE", "http://test"):
            results = _fetch_open_settling_markets()

    matching = [r for r in results if r.get("conditionId") == shared_id]
    assert len(matching) == 1, f"Expected 1 deduped event with conditionId={shared_id}, got {len(matching)}"
