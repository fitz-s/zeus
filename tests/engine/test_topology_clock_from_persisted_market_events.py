# Created: 2026-05-30
# Last reused or audited: 2026-05-30
# Authority basis: TOPOLOGY_CLOCK_MISSING throughput gate (live EDLI shadow);
#   src/engine/event_reactor_adapter.py::_evidence_clock_from_topology_row
"""Relationship test: market_events topology rows written by the scanner
persist path MUST carry a non-null clock that the reactor's topology-clock
resolver accepts.

Cross-module invariant (Module A = src.data.market_scanner persist writer,
Module B = src.engine.event_reactor_adapter topology-clock resolver):

    When a market_events row produced by ``_persist_market_events_to_db``
    flows into ``_evidence_clock_from_topology_row``, the clock resolves
    (no TOPOLOGY_CLOCK_MISSING) — regardless of whether the upstream Gamma
    payload supplied a ``created_at``.

This is the gate that was blocking families pre-score in the live shadow
pipeline: every recent market_events row had created_at NULL, so the
topology-clock resolver raised and the family was rejected before it could
reach TRADE_SCORE.
"""

import sqlite3

import pytest

from src.engine.event_reactor_adapter import _evidence_clock_from_topology_row


_MARKET_EVENTS_DDL = """
    CREATE TABLE market_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_slug TEXT NOT NULL,
        city TEXT NOT NULL,
        target_date TEXT NOT NULL,
        temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
        condition_id TEXT,
        token_id TEXT,
        range_label TEXT,
        range_low REAL,
        range_high REAL,
        outcome TEXT,
        created_at TEXT,
        recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(market_slug, condition_id)
    );
"""


class _FakeCity:
    def __init__(self, name: str) -> None:
        self.name = name


def _scanner_event(*, created_at_value: object) -> dict:
    """Build a scanner-shaped event dict as returned by find_weather_markets.

    ``created_at_value`` simulates whether the upstream Gamma payload surfaced
    a discovery timestamp on the returned event dict. When None, the writer
    must still stamp a resolvable clock.
    """
    event = {
        "slug": "highest-temperature-in-beijing-on-june-1",
        "city": _FakeCity("Beijing"),
        "target_date": "2026-06-01",
        "temperature_metric": "high",
        "outcomes": [
            {
                "condition_id": "0xcond_clock_test",
                "token_id": "tok_clock_test",
                "title": "30-31°C",
                "range_low": 30.0,
                "range_high": 31.0,
            }
        ],
    }
    if created_at_value is not None:
        event["created_at"] = created_at_value
    return event


def _persist_and_fetch(tmp_path, event: dict) -> dict:
    from src.data.market_scanner import _persist_market_events_to_db

    db_path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_MARKET_EVENTS_DDL)
        conn.commit()
    finally:
        conn.close()

    result = _persist_market_events_to_db([event], db_path=db_path)
    assert result.inserted >= 1, f"expected a row inserted, got {result}"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM market_events WHERE condition_id = ? LIMIT 1",
            ("0xcond_clock_test",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "persisted market_events row not found"
    return dict(row)


def test_persisted_market_event_without_gamma_clock_still_resolves(tmp_path):
    """Gamma omitted created_at → writer must stamp a resolvable clock."""
    row = _persist_and_fetch(tmp_path, _scanner_event(created_at_value=None))
    assert row.get("created_at") not in (None, ""), (
        "writer left created_at NULL — topology clock will be missing"
    )
    clock = _evidence_clock_from_topology_row(row)
    assert clock.source_available_at is not None
    assert clock.agent_received_at is not None
    assert clock.persisted_at is not None


def test_persisted_market_event_with_gamma_clock_uses_it(tmp_path):
    """Gamma supplied created_at → writer propagates it and the clock resolves."""
    gamma_ts = "2026-05-30T12:00:00+00:00"
    row = _persist_and_fetch(tmp_path, _scanner_event(created_at_value=gamma_ts))
    assert row.get("created_at") == gamma_ts
    clock = _evidence_clock_from_topology_row(row)
    assert clock.source_available_at is not None


def test_raw_null_clock_row_still_raises_as_baseline():
    """Sanity: a hand-built NULL-clock row reproduces the pre-fix failure.

    This proves the resolver itself is the gate; the fix lives in the writer,
    not by loosening the resolver.
    """
    null_row = {
        "condition_id": "0xnoclock",
        "created_at": None,
        "recorded_at": "2026-05-30 17:39:12",  # space-separated, naive — unparseable
    }
    with pytest.raises(ValueError, match="TOPOLOGY_CLOCK_MISSING"):
        _evidence_clock_from_topology_row(null_row)
