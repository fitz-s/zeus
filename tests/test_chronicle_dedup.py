"""Tests for B4 chronicle event dedup.

Covers: duplicate events within 1-minute window are rejected.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.state.chronicler import log_event


def _make_chronicle_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chronicle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            trade_id INTEGER,
            timestamp TEXT NOT NULL,
            details_json TEXT NOT NULL,
            env TEXT NOT NULL DEFAULT 'legacy_env'
        )
    """)
    conn.commit()


class TestChronicleDedup:

    def test_first_insert_succeeds(self):
        conn = sqlite3.connect(":memory:")
        _make_chronicle_table(conn)
        log_event(conn, "ENTRY", trade_id="T001", details={"city": "Chicago"}, env="legacy_env")
        conn.commit()
        rows = conn.execute("SELECT * FROM chronicle").fetchall()
        assert len(rows) == 1

    def test_duplicate_within_window_rejected(self):
        """Same trade_id + event_type within 1 minute → deduplicated."""
        conn = sqlite3.connect(":memory:")
        _make_chronicle_table(conn)
        log_event(conn, "ENTRY", trade_id="T001", details={"city": "Chicago"}, env="legacy_env")
        conn.commit()
        log_event(conn, "ENTRY", trade_id="T001", details={"city": "Chicago"}, env="legacy_env")
        conn.commit()
        rows = conn.execute("SELECT * FROM chronicle").fetchall()
        assert len(rows) == 1, f"Expected 1 row (dedup), got {len(rows)}"

    def test_different_event_type_not_deduplicated(self):
        """Same trade_id but different event_type → both inserted."""
        conn = sqlite3.connect(":memory:")
        _make_chronicle_table(conn)
        log_event(conn, "ENTRY", trade_id="T001", details={}, env="legacy_env")
        conn.commit()
        log_event(conn, "EXIT", trade_id="T001", details={}, env="legacy_env")
        conn.commit()
        rows = conn.execute("SELECT * FROM chronicle").fetchall()
        assert len(rows) == 2

    def test_different_trade_id_not_deduplicated(self):
        """Different trade_id + same event_type → both inserted."""
        conn = sqlite3.connect(":memory:")
        _make_chronicle_table(conn)
        log_event(conn, "ENTRY", trade_id="T001", details={}, env="legacy_env")
        conn.commit()
        log_event(conn, "ENTRY", trade_id="T002", details={}, env="legacy_env")
        conn.commit()
        rows = conn.execute("SELECT * FROM chronicle").fetchall()
        assert len(rows) == 2

    def test_null_trade_id_never_deduped(self):
        """Events without trade_id are always inserted (no dedup key)."""
        conn = sqlite3.connect(":memory:")
        _make_chronicle_table(conn)
        log_event(conn, "ENTRY", trade_id=None, details={}, env="legacy_env")
        conn.commit()
        log_event(conn, "ENTRY", trade_id=None, details={}, env="legacy_env")
        conn.commit()
        rows = conn.execute("SELECT * FROM chronicle").fetchall()
        assert len(rows) == 2

    def test_event_outside_window_not_deduped(self):
        """Same (trade_id, event_type) but >1 minute apart → both inserted."""
        conn = sqlite3.connect(":memory:")
        _make_chronicle_table(conn)
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        conn.execute(
            "INSERT INTO chronicle (event_type, trade_id, timestamp, details_json, env) "
            "VALUES (?, ?, ?, ?, ?)",
            ("ENTRY", "T001", old_ts, "{}", "legacy_env"),
        )
        conn.commit()
        log_event(conn, "ENTRY", trade_id="T001", details={}, env="legacy_env")
        conn.commit()
        rows = conn.execute("SELECT * FROM chronicle").fetchall()
        assert len(rows) == 2
