# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: FIX_SEV1_BUNDLE.md §F18 — INSERT OR IGNORE silent loss antibody
"""Antibody test: _persist_market_events_to_db must warn when 0 rows inserted with non-empty input.

Relationship invariant:
    When all INSERT OR IGNORE statements are ignored (because rows already exist),
    a WARNING log must be emitted by src.data.market_scanner containing
    "0 rows inserted".

F18 bug: cursor.rowcount was accumulated unconditionally; 0-insert case was silent.
Fix: rowcount==1 branch + post-commit zero-insert warning.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.market_scanner import _persist_market_events_to_db
from src.state.schema.v2_schema import apply_v2_schema


def _make_sample_events() -> list[dict]:
    """Minimal results list shape that _persist_market_events_to_db can process."""
    city = SimpleNamespace(name="Karachi")
    return [
        {
            "slug": "test-market-slug",
            "city": city,
            "target_date": "2026-05-17",
            "temperature_metric": "high",
            "created_at": "2026-05-17T00:00:00+00:00",
            "outcomes": [
                {
                    "condition_id": "cond-abc-001",
                    "token_id": "tok-abc-001",
                    "title": "90-95F",
                    "range_low": 90.0,
                    "range_high": 95.0,
                },
                {
                    "condition_id": "cond-abc-002",
                    "token_id": "tok-abc-002",
                    "title": "95-100F",
                    "range_low": 95.0,
                    "range_high": 100.0,
                },
            ],
        }
    ]


@pytest.fixture()
def forecasts_db(tmp_path) -> Path:
    """Create a tmp forecasts DB with the v2 schema applied."""
    db_path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db_path))
    try:
        apply_v2_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_zero_insert_warning_on_duplicate_events(forecasts_db, caplog):
    """Second call with identical events must insert 0 rows and emit a WARNING."""
    sample_events = _make_sample_events()

    # Prime: first call inserts the rows
    first_count = _persist_market_events_to_db(sample_events, db_path=forecasts_db)
    assert first_count == 2, f"First insert should insert 2 rows, got {first_count}"

    # Second call: all rows already exist — INSERT OR IGNORE should fire warning
    with caplog.at_level(logging.WARNING, logger="src.data.market_scanner"):
        second_count = _persist_market_events_to_db(sample_events, db_path=forecasts_db)

    assert second_count == 0, f"Second insert should return 0 (all ignored), got {second_count}"
    assert "0 rows inserted" in caplog.text, (
        f"Expected '0 rows inserted' WARNING in logs; got: {caplog.text!r}"
    )


def test_first_insert_does_not_warn(forecasts_db, caplog):
    """First call with new events must NOT emit the zero-insert warning."""
    sample_events = _make_sample_events()

    with caplog.at_level(logging.WARNING, logger="src.data.market_scanner"):
        count = _persist_market_events_to_db(sample_events, db_path=forecasts_db)

    assert count == 2
    assert "0 rows inserted" not in caplog.text, (
        "Should not warn on a successful insert"
    )
