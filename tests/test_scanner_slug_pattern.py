# Created: 2026-05-19
# Last reused or audited: 2026-05-20
# Authority basis: slug-pattern discovery (2026-05-19 alpha window) — tag-based gamma queries
#   do not surface newly-opened weather markets until tag is applied; direct slug lookup works immediately.
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=2026-05-20
# Purpose: Antibody tests for _fetch_events_by_slug_pattern — slug fallback discovery path in market_scanner.
# Reuse: Run when modifying SLUG_DISCOVERY_CITIES, SLUG_DISCOVERY_PREFIXES, _fetch_events_by_slug_pattern,
#   or _fetch_events_by_tags integration. Verify CLOB cross-check still applies on slug path.
"""Antibody tests: slug-pattern fallback discovery in market_scanner.

Five tests (T1–T5) cover:
  T1: valid slug → gamma returns event → scanner discovers it
  T2: slug returns 404 / empty → graceful skip, no crash
  T3: dedup with tag-fetched events on same event id
  T4: CLOB archived cross-check rejects archived markets on slug path
  T5: cache key (seen_ids) reused across slug + tag paths prevents duplicates
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import src.data.market_scanner as ms
from src.data.market_scanner import (
    SLUG_DISCOVERY_CITIES,
    SLUG_DISCOVERY_PREFIXES,
    _fetch_events_by_slug_pattern,
)

_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gamma_event(
    *,
    event_id: str = "evt-slug-1",
    slug: str = "highest-temperature-in-amsterdam-on-may-20-2026",
    condition_id: str = "0xabc001",
    accepting_orders: bool = True,
    archived: bool = False,
    end_date: str = "2099-12-31T00:00:00Z",
) -> dict:
    """Minimal negRisk parent event shape as returned by Gamma /events?slug=."""
    return {
        "id": event_id,
        "slug": slug,
        "title": "Highest temperature in Amsterdam on May 20?",
        "endDate": end_date,
        "archived": archived,
        "markets": [
            {
                "conditionId": condition_id,
                "acceptingOrders": accepting_orders,
                "closed": False,
                "active": False,  # negRisk semantic: active=False but tradeable
                "enableOrderBook": True,
            }
        ],
    }


def _clob_live_response(archived: bool = False, eob: bool = True) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"archived": archived, "enable_order_book": eob}
    return mock


def _clob_error_response(status: int = 503) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status
    return mock


# ---------------------------------------------------------------------------
# T1: valid slug returns event → scanner discovers it
# ---------------------------------------------------------------------------

def test_slug_pattern_discovers_new_event(monkeypatch):
    """T1 (SLUG_DISCOVERS): gamma returns valid event for slug → included in output.

    Sed-revert: remove _fetch_events_by_slug_pattern call from _fetch_events_by_tags
    → slug-only markets never appear in output → RED (len == 0).
    """
    event = _make_gamma_event(event_id="evt-ams-0520", condition_id="0xfresh01")

    # Restrict to one city so we control exactly which HTTP calls happen.
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_CITIES", ["amsterdam"])
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_PREFIXES", [
        "highest-temperature-in-{city}-on-{date}",
    ])

    clob_live = _clob_live_response(archived=False, eob=True)

    def _httpx_get(url, *, params=None, timeout=None):
        slug = (params or {}).get("slug", "")
        if "highest-temperature-in-amsterdam" in slug:
            # gamma response
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = [event]
            return r
        # CLOB archived check
        return clob_live

    ms._CLOB_ARCHIVED_CACHE.clear()
    seen_ids: set = set()

    with patch("httpx.get", side_effect=_httpx_get):
        results = _fetch_events_by_slug_pattern(
            seen_ids,
            _NOW,
            target_dates=["2026-05-20"],
        )

    matching = [e for e in results if e.get("id") == "evt-ams-0520"]
    assert len(matching) >= 1, (
        "slug_pattern must include events returned by Gamma /events?slug= "
        "that are not already in seen_ids"
    )
    assert matching[0].get("_discovery_path") == "slug_pattern"


# ---------------------------------------------------------------------------
# T2: slug returns 404 / empty → graceful skip, no crash
# ---------------------------------------------------------------------------

def test_slug_pattern_graceful_on_404(monkeypatch):
    """T2 (GRACEFUL_404): gamma returns 404 or empty list → no crash, empty result.

    Sed-revert: remove status_code != 200 guard
    → json() on 404 response raises / returns invalid data → RED (exception).
    """
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_CITIES", ["amsterdam"])
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_PREFIXES", ["highest-temperature-in-{city}-on-{date}"])

    not_found = MagicMock()
    not_found.status_code = 404

    seen_ids: set = set()
    with patch("httpx.get", return_value=not_found):
        ms._CLOB_ARCHIVED_CACHE.clear()
        results = _fetch_events_by_slug_pattern(
            seen_ids,
            _NOW,
            target_dates=["2026-05-20"],
        )

    assert results == [], (
        "_fetch_events_by_slug_pattern must return [] when gamma returns 404, not raise"
    )


def test_slug_pattern_graceful_on_empty_list(monkeypatch):
    """T2b (GRACEFUL_EMPTY): gamma returns empty array → no crash, empty result."""
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_CITIES", ["amsterdam"])
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_PREFIXES", ["highest-temperature-in-{city}-on-{date}"])

    empty_resp = MagicMock()
    empty_resp.status_code = 200
    empty_resp.json.return_value = []

    seen_ids: set = set()
    with patch("httpx.get", return_value=empty_resp):
        ms._CLOB_ARCHIVED_CACHE.clear()
        results = _fetch_events_by_slug_pattern(
            seen_ids,
            _NOW,
            target_dates=["2026-05-20"],
        )

    assert results == []


# ---------------------------------------------------------------------------
# T3: dedup against tag-fetched events by event id
# ---------------------------------------------------------------------------

def test_slug_pattern_dedup_with_tag_events(monkeypatch):
    """T3 (DEDUP): event already in seen_ids is not added again.

    Sed-revert: remove ``if event_id in seen_ids: continue`` guard
    → same event returned twice → len(results) > 0 → RED.
    """
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_CITIES", ["amsterdam"])
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_PREFIXES", ["highest-temperature-in-{city}-on-{date}"])

    event_id = "evt-already-tagged"
    event = _make_gamma_event(event_id=event_id, condition_id="0xdup01")
    gamma_resp = MagicMock()
    gamma_resp.status_code = 200
    gamma_resp.json.return_value = [event]

    # Pre-populate seen_ids as if tag path already found this event
    seen_ids: set = {event_id}

    with patch("httpx.get", return_value=gamma_resp):
        ms._CLOB_ARCHIVED_CACHE.clear()
        results = _fetch_events_by_slug_pattern(
            seen_ids,
            _NOW,
            target_dates=["2026-05-20"],
        )

    dup = [e for e in results if e.get("id") == event_id]
    assert len(dup) == 0, (
        "slug_pattern must not return events already present in seen_ids "
        "(dedup against tag-fetched results)"
    )


# ---------------------------------------------------------------------------
# T4: CLOB cross-check rejects archived markets on slug path
# ---------------------------------------------------------------------------

def test_slug_path_clob_check_rejects_archived(monkeypatch):
    """T4 (CLOB_ARCHIVED_ON_SLUG): archived market from slug path is rejected by CLOB check.

    Sed-revert: remove _event_has_active_children call in _fetch_events_by_slug_pattern
    → archived event passes through → RED (len > 0).
    """
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_CITIES", ["amsterdam"])
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_PREFIXES", ["highest-temperature-in-{city}-on-{date}"])

    event = _make_gamma_event(
        event_id="evt-archived-slug",
        condition_id="0xarchived01",
        accepting_orders=True,  # Gamma lies: says live
    )
    clob_archived = _clob_live_response(archived=True, eob=False)

    def _httpx_get(url, *, params=None, timeout=None):
        slug = (params or {}).get("slug", "")
        if slug:  # gamma lookup
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = [event]
            return r
        return clob_archived  # CLOB archived check

    seen_ids: set = set()
    with patch("httpx.get", side_effect=_httpx_get):
        ms._CLOB_ARCHIVED_CACHE.clear()
        results = _fetch_events_by_slug_pattern(
            seen_ids,
            _NOW,
            target_dates=["2026-05-20"],
        )

    archived_in_results = [e for e in results if e.get("id") == "evt-archived-slug"]
    assert len(archived_in_results) == 0, (
        "slug_pattern must apply CLOB archived cross-check: market with "
        "Gamma acceptingOrders=True but CLOB archived=True must be rejected"
    )


# ---------------------------------------------------------------------------
# T5: seen_ids shared between tag + slug paths prevents double-entry
# ---------------------------------------------------------------------------

def test_seen_ids_shared_between_tag_and_slug_paths(monkeypatch):
    """T5 (SHARED_SEEN_IDS): seen_ids set is shared; events found by tags are not
    duplicated by slug_pattern path.

    This verifies that _fetch_events_by_tags passes its local seen_ids set by
    reference into _fetch_events_by_slug_pattern. Sed-revert: pass a fresh
    set() to slug fetch → tag events appear twice in output → RED.
    """
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_CITIES", ["amsterdam"])
    monkeypatch.setattr(ms, "SLUG_DISCOVERY_PREFIXES", ["highest-temperature-in-{city}-on-{date}"])

    event_id = "evt-shared-seen"
    event = _make_gamma_event(event_id=event_id, condition_id="0xshared01")
    gamma_resp = MagicMock()
    gamma_resp.status_code = 200
    gamma_resp.json.return_value = [event]

    seen_ids: set = {event_id}  # simulates tag path having found this event

    with patch("httpx.get", return_value=gamma_resp):
        ms._CLOB_ARCHIVED_CACHE.clear()
        results = _fetch_events_by_slug_pattern(seen_ids, _NOW, target_dates=["2026-05-20"])

    assert not any(e.get("id") == event_id for e in results), (
        "seen_ids must be shared between tag fetch and slug_pattern fetch to "
        "prevent duplicate event entries in the combined output"
    )


def test_live_slug_pattern_market_reader_never_runs_tag_scan(monkeypatch):
    """Live background substrate discovery must stay bounded to slug lookups."""

    slug_event = _make_gamma_event(event_id="evt-live-slug", condition_id="0xlive01")
    parsed = {
        "event_id": "evt-live-slug",
        "slug": "highest-temperature-in-amsterdam-on-may-20-2026",
        "title": "Highest temperature in Amsterdam on May 20?",
        "city": ms.cities_by_name["Amsterdam"],
        "target_date": "2026-05-20",
        "temperature_metric": "high",
        "hours_to_resolution": 12.0,
        "hours_since_open": 1.0,
        "outcomes": [{"condition_id": "0xlive01", "executable": True}],
        "condition_ids": ["0xlive01"],
        "source_contract": {"status": "MATCH"},
    }
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        ms,
        "_fetch_events_by_tags",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("slug-only live reader must not tag-scan")),
    )
    monkeypatch.setattr(
        ms,
        "_fetch_events_by_slug_pattern",
        lambda seen_ids, now_utc, *, target_dates=None: calls.append(("slug", target_dates)) or [slug_event],
    )
    monkeypatch.setattr(ms, "_parse_event", lambda event, now, min_hours: parsed)
    monkeypatch.setattr(ms, "_persist_market_events_to_db", lambda results: calls.append(("persist", len(results))) or len(results))

    results = ms.find_slug_pattern_weather_markets(
        min_hours_to_resolution=0.0,
        target_dates=["2026-05-20"],
    )

    assert results == [parsed]
    assert calls == [("slug", ["2026-05-20"]), ("persist", 1)]


def test_default_slug_pattern_dates_skip_expired_and_cover_opening_hunt_horizon():
    """Opening-hunt markets can open two future calendar dates before settlement."""

    dates = ms._slug_pattern_target_dates(_NOW)

    assert dates == ["2026-05-20", "2026-05-21"]


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

def test_slug_discovery_cities_nonempty():
    """Structural: SLUG_DISCOVERY_CITIES must be non-empty and contain known cities."""
    assert len(SLUG_DISCOVERY_CITIES) >= 7
    for city in ("amsterdam", "denver", "jeddah"):
        assert city in SLUG_DISCOVERY_CITIES, (
            f"Verified-tradeable city {city!r} missing from SLUG_DISCOVERY_CITIES"
        )


def test_slug_discovery_prefixes_cover_high_low():
    """Structural: SLUG_DISCOVERY_PREFIXES must enumerate both high- and low-temp patterns."""
    prefixes_str = " ".join(SLUG_DISCOVERY_PREFIXES)
    assert "highest-temperature" in prefixes_str, "SLUG_DISCOVERY_PREFIXES must cover highest-temp"
    assert "lowest-temperature" in prefixes_str, "SLUG_DISCOVERY_PREFIXES must cover lowest-temp"
