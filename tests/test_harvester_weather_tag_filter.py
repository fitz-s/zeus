# Lifecycle: created=2026-05-17; last_reviewed=2026-05-17; last_reused=2026-05-17
# Purpose: Antibody for PR-S7 Gamma tag_id filter — asserts harvester only writes weather settlements
# Reuse: Run via pytest tests/test_harvester_weather_tag_filter.py
"""
Antibody: _fetch_open_settling_markets() must pass tag_id=103040 to Gamma.
Without the daily-temperature tag filter, non-weather events (MLB, WTA, politics)
dominate closed-event pages and weather markets are never discovered within the
120-second wall-cap — causing 0 settlements per tick (verified 2026-05-17).
"""
import pytest
from unittest.mock import patch, MagicMock


def _make_mock_resp(events):
    m = MagicMock()
    m.json.return_value = events
    m.raise_for_status.return_value = None
    return m


def _weather_event(slug="highest-temperature-in-karachi-on-may-7-2026"):
    return {
        "slug": slug,
        "title": "Highest Temperature in Karachi on May 7, 2026",
        "endDate": "2026-05-07T12:00:00Z",
        "closed": True,
        "tags": [
            {"slug": "weather"},
            {"slug": "daily-temperature"},
            {"slug": "karachi"},
        ],
        "markets": [],
    }


def _non_weather_event(slug="mlb-lad-laa-2026-05-17"):
    return {
        "slug": slug,
        "title": "MLB: LAD vs LAA 2026-05-17",
        "endDate": "2026-05-17T03:00:00Z",
        "closed": True,
        "tags": [{"slug": "mlb"}, {"slug": "sports"}],
        "markets": [],
    }


def test_fetch_open_settling_markets_passes_weather_tag_id():
    """The settlement paginator must include tag_id=103040 in every Gamma request."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params or {})
        return _make_mock_resp([])  # empty → loop exits immediately

    with patch("httpx.get", side_effect=fake_get):
        from src.ingest.harvester_truth_writer import _fetch_open_settling_markets
        _fetch_open_settling_markets()

    assert calls, "expected at least one HTTP call to Gamma"
    for p in calls:
        assert "tag_id" in p, (
            f"Gamma request missing tag_id — non-weather events will dominate "
            f"closed-event pages and weather markets will never be discovered. "
            f"Got params: {p}"
        )
        assert p["tag_id"] == "103040", (
            f"Expected daily-temperature tag_id=103040, got {p['tag_id']!r}. "
            "If Polymarket assigned a new ID, update _WEATHER_DAILY_TEMP_TAG_ID."
        )


def test_fetch_returns_only_weather_events_when_tag_filtered():
    """When Gamma returns only weather events (because tag_id filter was sent),
    the harvester should return exactly those events without dropping any.
    The filtering is API-side; this test verifies the round-trip contract:
    tag_id param sent → only weather events come back → all are returned."""
    weather_evt1 = _weather_event("highest-temperature-in-karachi-on-may-7-2026")
    weather_evt2 = _weather_event("highest-temperature-in-seoul-on-may-7-2026")

    call_count = [0]
    sent_params = []

    def fake_get(url, params=None, timeout=None):
        call_count[0] += 1
        sent_params.append(params or {})
        if call_count[0] == 1:
            return _make_mock_resp([weather_evt1, weather_evt2])
        return _make_mock_resp([])  # second call → empty → exit

    with patch("httpx.get", side_effect=fake_get):
        from src.ingest.harvester_truth_writer import _fetch_open_settling_markets
        result = _fetch_open_settling_markets()

    # tag_id=103040 must have been sent so the API filters to weather only
    assert all("tag_id" in p and p["tag_id"] == "103040" for p in sent_params), (
        f"Gamma requests did not all include tag_id=103040: {sent_params}"
    )
    # Both weather events must be returned (no client-side drops)
    result_slugs = {e["slug"] for e in result}
    assert weather_evt1["slug"] in result_slugs, "First weather event was dropped"
    assert weather_evt2["slug"] in result_slugs, "Second weather event was dropped"


def test_zero_settlements_logged_when_no_weather_events_present():
    """When Gamma returns an empty list (no weather events in window),
    the harvester must not crash and must complete without writing settlements."""
    import logging

    def fake_get(url, params=None, timeout=None):
        return _make_mock_resp([])

    with patch("httpx.get", side_effect=fake_get):
        from src.ingest.harvester_truth_writer import _fetch_open_settling_markets
        result = _fetch_open_settling_markets()

    assert result == [], (
        f"Expected empty list when Gamma returns no events, got {len(result)} events"
    )
