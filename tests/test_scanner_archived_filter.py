# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: Polymarket V2 cutover (2026-05-11) — gamma acceptingOrders lies for archived markets
"""Antibody tests: tag-84 priority ordering + CLOB archived cross-check.

Two stacked failures blocked Zeus from discovering V2-native weather markets
post-V2 cutover (2026-05-11):

1. TAG_SLUGS put "temperature" (tag 104615, stale Dec/Jan archives) before
   "weather" (tag 84, live arch-arch-* markets). seen_ids dedup then
   suppressed tag-84 results.

2. _event_has_active_children trusted Gamma's acceptingOrders=True for
   markets CLOB /markets/{cid} reports as archived=True.

These five tests (T1–T5) are sed-flip antibodies: each verifies one property
and fails when the targeted code path is reverted.
"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# T1: TAG_SLUGS ordering — "weather" must precede "temperature"
# ---------------------------------------------------------------------------

def test_tag_slugs_weather_before_temperature():
    """T1 (TAG_PRIORITY): weather (tag 84) must appear before temperature (tag 104615).

    Sed-revert: swap list to ["temperature", "weather", "daily-temperature"]
    → this test goes RED because index("weather") > index("temperature").
    """
    import src.data.market_scanner as ms
    importlib.reload(ms)
    slugs = ms.TAG_SLUGS
    assert "weather" in slugs, "TAG_SLUGS must contain 'weather'"
    assert "temperature" in slugs, "TAG_SLUGS must contain 'temperature'"
    assert slugs.index("weather") < slugs.index("temperature"), (
        f"'weather' (tag 84) must precede 'temperature' (tag 104615) in TAG_SLUGS "
        f"so live V2 markets are not suppressed by seen_ids dedup. "
        f"Current order: {slugs}"
    )


# ---------------------------------------------------------------------------
# Helpers — build minimal Gamma event payload
# ---------------------------------------------------------------------------

def _make_event(
    *,
    condition_id: str = "0xabc123",
    accepting_orders: bool = True,
    end_date: str = "2099-12-31T00:00:00Z",
) -> dict:
    return {
        "id": "evt-1",
        "endDate": end_date,
        "markets": [
            {
                "conditionId": condition_id,
                "acceptingOrders": accepting_orders,
            }
        ],
    }


_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# T2: ARCHIVED_FILTERED — Gamma says live, CLOB says archived → reject
# ---------------------------------------------------------------------------

def test_event_rejected_when_clob_reports_archived():
    """T2 (ARCHIVED_FILTERED): CLOB archived=True overrides Gamma acceptingOrders=True.

    Sed-revert: remove CLOB cross-check (return True after Gamma check)
    → _event_has_active_children returns True for archived market → RED.
    """
    import src.data.market_scanner as ms

    clob_payload = {"archived": True, "enable_order_book": False}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = clob_payload

    with patch("httpx.get", return_value=mock_resp) as mock_get:
        ms._CLOB_ARCHIVED_CACHE.clear()
        event = _make_event(condition_id="0xdeadbeef", accepting_orders=True)
        result = ms._event_has_active_children(event, _NOW)

    assert result is False, (
        "_event_has_active_children must return False when CLOB reports "
        "archived=True, even if Gamma's acceptingOrders=True"
    )
    mock_get.assert_called_once()


# ---------------------------------------------------------------------------
# T3: LIVE_PASSES — CLOB says live → admit
# ---------------------------------------------------------------------------

def test_event_admitted_when_clob_confirms_live():
    """T3 (LIVE_PASSES): CLOB archived=False + enable_order_book=True → event admitted."""
    import src.data.market_scanner as ms

    clob_payload = {"archived": False, "enable_order_book": True}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = clob_payload

    with patch("httpx.get", return_value=mock_resp):
        ms._CLOB_ARCHIVED_CACHE.clear()
        event = _make_event(condition_id="0xcafebabe", accepting_orders=True)
        result = ms._event_has_active_children(event, _NOW)

    assert result is True, (
        "_event_has_active_children must return True when CLOB confirms "
        "archived=False AND enable_order_book=True"
    )


# ---------------------------------------------------------------------------
# T4: CLOB_ERROR_FALLBACK — CLOB 5xx → fall back to Gamma trust
# ---------------------------------------------------------------------------

def test_clob_5xx_falls_back_to_gamma():
    """T4 (CLOB_ERROR_FALLBACK): CLOB non-200 must not over-block; fall back to Gamma.

    Sed-revert: return False on any CLOB error
    → this test goes RED (False ≠ True).
    """
    import src.data.market_scanner as ms

    mock_resp = MagicMock()
    mock_resp.status_code = 503

    with patch("httpx.get", return_value=mock_resp):
        ms._CLOB_ARCHIVED_CACHE.clear()
        event = _make_event(condition_id="0xfeed1234", accepting_orders=True)
        result = ms._event_has_active_children(event, _NOW)

    assert result is True, (
        "_event_has_active_children must return True (Gamma fallback) "
        "when CLOB returns non-200, to avoid over-blocking on transient outages"
    )


# ---------------------------------------------------------------------------
# T5: CACHE_HIT — two calls in same tick → only ONE httpx request
# ---------------------------------------------------------------------------

def test_clob_cache_prevents_duplicate_requests():
    """T5 (CACHE_HIT): second call for same condition_id in same tick hits cache.

    Sed-revert: remove _CLOB_ARCHIVED_CACHE lookup
    → httpx.get called twice → mock_get.call_count == 2 → RED.
    """
    import src.data.market_scanner as ms

    clob_payload = {"archived": False, "enable_order_book": True}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = clob_payload

    cid = "0xcachedcid"
    event = _make_event(condition_id=cid, accepting_orders=True)

    with patch("httpx.get", return_value=mock_resp) as mock_get:
        ms._CLOB_ARCHIVED_CACHE.clear()
        # First call — populates cache
        r1 = ms._event_has_active_children(event, _NOW)
        # Second call — same cid, should hit cache
        r2 = ms._event_has_active_children(event, _NOW)

    assert r1 is True
    assert r2 is True
    assert mock_get.call_count == 1, (
        f"httpx.get should only be called ONCE per condition_id per tick "
        f"(cache hit on second call). Got {mock_get.call_count} calls."
    )
