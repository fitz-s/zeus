# Created: 2026-04-17
# Last reused/audited: 2026-04-17
# Authority basis: audit bug B017 (STILL_OPEN P1 SD-H), Fitz methodology
#     constraint #4 "Data Provenance > Code Correctness"
"""B017 relationship tests: market_scanner cache must expose provenance.

These tests pin the cross-module invariant:

  "When the underlying Gamma fetch fails, any events returned from
   ``_get_active_events_snapshot`` MUST carry authority != 'VERIFIED',
   and ``get_last_scan_authority()`` MUST reflect the same state that
   downstream callers would observe."

They run against the module-level globals so they must reset cache
state between cases (conftest-free isolation).
"""
from __future__ import annotations

import httpx
import pytest

from src.data import market_scanner as ms
from src.data.market_scanner import (
    MarketSnapshot,
    _clear_active_events_cache,
    _get_active_events,
    _get_active_events_snapshot,
    get_last_scan_authority,
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Reset the scanner's module-level cache around every test."""
    _clear_active_events_cache()
    yield
    _clear_active_events_cache()


def _make_dummy_event(market_id: str = "m1") -> dict:
    """Minimal event shape enough to survive downstream filtering."""
    return {
        "id": "evt-1",
        "slug": "temp-evt-1",
        "title": "Highest temperature in Test City",
        "markets": [
            {
                "id": market_id,
                "question": "Temp 40-50F",
                "outcomePrices": "[0.3, 0.7]",
                "clobTokenIds": '["yes-tok", "no-tok"]',
                "outcomes": '["Yes", "No"]',
                "startDate": "2026-04-17T00:00:00Z",
                "endDate": "2026-04-17T23:00:00Z",
                "active": True,
                "closed": False,
            }
        ],
    }


class TestB017MarketSnapshotProvenance:
    """Snapshot API exposes provenance on every code path."""

    def test_b017_fresh_fetch_authority_is_verified(self, monkeypatch):
        """A successful fetch returns authority=VERIFIED and
        stale_age_seconds=0."""
        monkeypatch.setattr(
            ms, "_fetch_events_by_tags", lambda: [_make_dummy_event()]
        )
        snap = _get_active_events_snapshot()
        assert isinstance(snap, MarketSnapshot)
        assert snap.authority == "VERIFIED"
        assert snap.stale_age_seconds == 0.0
        assert snap.fetched_at_utc is not None
        assert len(snap.events) == 1
        assert get_last_scan_authority() == "VERIFIED"

    def test_b017_network_failure_with_cache_returns_stale(self, monkeypatch):
        """When the fetch raises, a populated cache is returned but
        authority=STALE and stale_age_seconds>=0."""
        # First, prime the cache with one successful fetch.
        monkeypatch.setattr(
            ms, "_fetch_events_by_tags", lambda: [_make_dummy_event("m-primed")]
        )
        _get_active_events_snapshot()
        assert get_last_scan_authority() == "VERIFIED"

        # Force the cache to look expired so the next call re-fetches.
        ms._ACTIVE_EVENTS_CACHE_AT -= ms._ACTIVE_EVENTS_TTL + 1.0

        def _raise(*_a, **_kw):
            raise httpx.ConnectError("simulated network failure")

        monkeypatch.setattr(ms, "_fetch_events_by_tags", _raise)

        snap = _get_active_events_snapshot()
        assert snap.authority == "STALE"
        assert snap.stale_age_seconds is not None
        assert snap.stale_age_seconds > 0
        assert any(
            m["id"] == "m-primed"
            for evt in snap.events
            for m in evt.get("markets", [])
        )
        assert get_last_scan_authority() == "STALE"

    def test_b017_network_failure_without_cache_returns_empty_fallback(
        self, monkeypatch
    ):
        """No cache + fetch failure => authority=EMPTY_FALLBACK and
        empty events, NOT VERIFIED."""
        def _raise(*_a, **_kw):
            raise httpx.ConnectError("simulated network failure")

        monkeypatch.setattr(ms, "_fetch_events_by_tags", _raise)

        snap = _get_active_events_snapshot()
        assert snap.authority == "EMPTY_FALLBACK"
        assert snap.events == []
        assert snap.stale_age_seconds is None
        assert get_last_scan_authority() == "EMPTY_FALLBACK"

    def test_b017_legacy_api_still_returns_list_for_backwards_compat(
        self, monkeypatch
    ):
        """Dual-Track callers use ``_get_active_events`` (returns
        list[dict]). That signature MUST not change."""
        monkeypatch.setattr(
            ms, "_fetch_events_by_tags", lambda: [_make_dummy_event()]
        )
        result = _get_active_events()
        assert isinstance(result, list)
        assert all(isinstance(e, dict) for e in result)

    def test_b017_authority_reflects_last_call_not_last_fetch(
        self, monkeypatch
    ):
        """After a VERIFIED call followed by a STALE call,
        ``get_last_scan_authority()`` reports STALE (the latest call),
        not VERIFIED."""
        monkeypatch.setattr(
            ms, "_fetch_events_by_tags", lambda: [_make_dummy_event()]
        )
        _get_active_events_snapshot()
        assert get_last_scan_authority() == "VERIFIED"

        ms._ACTIVE_EVENTS_CACHE_AT -= ms._ACTIVE_EVENTS_TTL + 1.0

        def _raise(*_a, **_kw):
            raise httpx.ReadTimeout("simulated timeout")

        monkeypatch.setattr(ms, "_fetch_events_by_tags", _raise)
        _get_active_events_snapshot()
        assert get_last_scan_authority() == "STALE"
