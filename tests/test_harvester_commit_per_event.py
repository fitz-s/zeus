# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: lock-storm antibody (data-ingest bootout 2026-05-27, "database is locked" flood); task #81-class commit-per-item
"""Relationship test: write_settlement_truth_for_open_markets releases the write lock between events.

Root: write_settlement_truth_for_open_markets committed ONCE after the full batch, holding
the forecasts WAL write lock across all events. On startup catch-up (many events) this produced
a "database is locked" flood contending with forecast-live daemon → data-ingest bootout 2026-05-27
→ 35 cities dark for 5+ days.

Fix: commit after EACH successful event write (per-event atomicity).

The relationship test proves: conn.commit() is called once PER settled event (K times for K events),
NOT once total. On pre-fix code this must FAIL (1 commit seen, K expected).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Minimal forecasts-DB schema (subset that _write_settlement_truth touches)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settlements (
    city TEXT, target_date TEXT, market_slug TEXT,
    winning_bin TEXT, settlement_value REAL, settlement_source TEXT,
    settled_at TEXT, authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
    pm_bin_lo REAL, pm_bin_hi REAL, unit TEXT, settlement_source_type TEXT,
    temperature_metric TEXT, physical_quantity TEXT, observation_field TEXT,
    data_version TEXT, provenance_json TEXT,
    UNIQUE(city, target_date, temperature_metric)
);
CREATE TABLE IF NOT EXISTS settlement_outcomes (
    settlement_id INTEGER PRIMARY KEY,
    city TEXT NOT NULL, target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL, market_slug TEXT,
    winning_bin TEXT, settlement_value REAL, settlement_source TEXT,
    settled_at TEXT, authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS market_events (
    event_id INTEGER PRIMARY KEY,
    market_slug TEXT NOT NULL, city TEXT NOT NULL,
    target_date TEXT NOT NULL, temperature_metric TEXT NOT NULL,
    condition_id TEXT, token_id TEXT, range_label TEXT,
    range_low REAL, range_high REAL, outcome TEXT,
    created_at TEXT, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL, target_date TEXT NOT NULL,
    source TEXT NOT NULL, high_temp REAL, low_temp REAL,
    unit TEXT, fetched_at TEXT, authority TEXT,
    UNIQUE(city, target_date, source)
);
"""

K_EVENTS = 4  # number of settled events fed through the function


def _make_forecasts_db():
    """Return a real on-disk temp SQLite forecasts DB (path, conn)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return tmp.name, conn


def _minimal_event(slug: str) -> dict:
    """Minimal event dict that can be processed end-to-end with mocked helpers."""
    return {
        "title": f"highest temperature in london on target-date",
        "slug": slug,
        "markets": [
            {
                "question": f"Will it be 17°C? ({slug})",
                "groupItemTitle": "",
                "conditionId": f"cond-{slug}",
                "clobTokenIds": '["yes","no"]',
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["1","0"]',
                "umaResolutionStatus": "resolved",
            }
        ],
    }


# ---------------------------------------------------------------------------
# CommitCounterConn — thin proxy that counts .commit() calls
# ---------------------------------------------------------------------------

class _CommitCounterConn:
    """Wrap a real sqlite3.Connection to count commit calls without suppressing them."""

    def __init__(self, real: sqlite3.Connection):
        self._real = real
        self.commit_count = 0

    def __getattr__(self, name):
        return getattr(self._real, name)

    def commit(self):
        self.commit_count += 1
        return self._real.commit()

    # sqlite3.Connection.execute / executemany / executescript are NOT in
    # __dict__; they come through slot access, so __getattr__ covers them.


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST: commit is called once PER event (K times), NOT once total
# ---------------------------------------------------------------------------

def test_commit_called_once_per_settled_event(monkeypatch, tmp_path):
    """Relationship invariant: conn.commit() fires K times for K settled events.

    Pre-fix behaviour: 1 commit (batch boundary).
    Post-fix behaviour: K commits (per-event boundary) + optional final flush.

    This test asserts >= K commits, which is satisfied only by per-event commits.
    A single batch commit will produce commit_count=1 and fail.
    """
    import os
    os.environ["ZEUS_HARVESTER_LIVE_ENABLED"] = "1"

    from src.config import City
    from src.ingest.harvester_truth_writer import write_settlement_truth_for_open_markets

    _, real_conn = _make_forecasts_db()
    proxy = _CommitCounterConn(real_conn)

    # Build K minimal events
    events = [_minimal_event(f"event-{i:02d}") for i in range(K_EVENTS)]

    # City fixture: London-style C city
    london = City(
        name="London",
        lat=51.47,
        lon=-0.45,
        timezone="Europe/London",
        settlement_unit="C",
        cluster="London",
        wu_station="EGLC",
        country_code="GB",
        settlement_source_type="wu_icao",
    )

    # Obs fixture
    obs_row = {
        "id": 1,
        "source": "wu_icao_history",
        "high_temp": 17.0,
        "low_temp": None,
        "unit": "C",
        "fetched_at": "2026-06-01T12:00:00Z",
        "station_id": "EGLC",
        "authority": "VERIFIED",
        "observation_field": "high_temp",
        "observed_temp": 17.0,
    }

    with patch(
        "src.ingest.harvester_truth_writer._fetch_open_settling_markets",
        return_value=events,
    ), patch(
        "src.data.market_scanner._match_city",
        return_value=london,
    ), patch(
        "src.data.market_scanner.infer_temperature_metric",
        return_value="high",
    ), patch(
        "src.data.market_scanner._parse_target_date",
        return_value="2026-06-01",
    ), patch(
        "src.ingest.harvester_truth_writer._lookup_settlement_obs",
        return_value=obs_row,
    ), patch(
        "src.ingest.harvester_truth_writer._extract_resolved_market_outcomes",
        return_value=[{
            "yes_won": True,
            "range_low": 17.0,
            "range_high": 17.0,
            "range_label": "17°C",
            "condition_id": "cond-abc",
            "yes_token_id": "tok-abc",
        }],
    ), patch(
        "src.ingest.harvester_truth_writer._detect_bin_unit",
        return_value="C",
    ):
        result = write_settlement_truth_for_open_markets(proxy, dry_run=False)

    real_conn.close()

    assert result["markets_resolved"] == K_EVENTS, (
        f"Expected {K_EVENTS} markets_resolved, got {result['markets_resolved']}"
    )
    assert result["settlements_written"] == K_EVENTS, (
        f"Expected {K_EVENTS} settlements_written, got {result['settlements_written']}"
    )
    assert result["errors"] == 0, f"Unexpected errors: {result['errors']}"

    # THE RELATIONSHIP INVARIANT:
    # Per-event commit means commit_count >= K (K per-event + possible final flush).
    # Batch commit means commit_count == 1.
    # This assertion FAILS on pre-fix code (1 commit) and PASSES post-fix (K commits).
    assert proxy.commit_count >= K_EVENTS, (
        f"LOCK-STORM ANTIBODY FAILED: expected >= {K_EVENTS} commits (one per event), "
        f"got {proxy.commit_count}. "
        f"The write lock is being held across the entire batch — "
        f"this is the exact root of the 2026-05-27 data-ingest bootout."
    )


def test_dry_run_never_commits(monkeypatch):
    """dry_run=True must produce zero commits regardless of event count."""
    import os
    os.environ["ZEUS_HARVESTER_LIVE_ENABLED"] = "1"

    from src.config import City
    from src.ingest.harvester_truth_writer import write_settlement_truth_for_open_markets

    _, real_conn = _make_forecasts_db()
    proxy = _CommitCounterConn(real_conn)

    events = [_minimal_event(f"dry-{i}") for i in range(3)]

    london = City(
        name="London", lat=51.47, lon=-0.45, timezone="Europe/London",
        settlement_unit="C", cluster="London", wu_station="EGLC",
        country_code="GB", settlement_source_type="wu_icao",
    )
    obs_row = {
        "id": 1, "source": "wu_icao_history", "high_temp": 17.0,
        "unit": "C", "fetched_at": "2026-06-01T12:00:00Z",
        "authority": "VERIFIED", "observation_field": "high_temp", "observed_temp": 17.0,
    }

    with patch(
        "src.ingest.harvester_truth_writer._fetch_open_settling_markets",
        return_value=events,
    ), patch(
        "src.data.market_scanner._match_city",
        return_value=london,
    ), patch(
        "src.data.market_scanner.infer_temperature_metric",
        return_value="high",
    ), patch(
        "src.data.market_scanner._parse_target_date",
        return_value="2026-06-01",
    ), patch(
        "src.ingest.harvester_truth_writer._lookup_settlement_obs",
        return_value=obs_row,
    ), patch(
        "src.ingest.harvester_truth_writer._extract_resolved_market_outcomes",
        return_value=[{
            "yes_won": True,
            "range_low": 17.0,
            "range_high": 17.0,
            "range_label": "17°C",
            "condition_id": "cond-abc",
            "yes_token_id": "tok-abc",
        }],
    ), patch(
        "src.ingest.harvester_truth_writer._detect_bin_unit",
        return_value="C",
    ):
        result = write_settlement_truth_for_open_markets(proxy, dry_run=True)

    real_conn.close()

    assert proxy.commit_count == 0, (
        f"dry_run=True must never commit; got {proxy.commit_count} commits"
    )
    assert result["settlements_written"] == 3
    assert result["dry_run"] is True
