# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: /Users/leofitz/.claude/jobs/9ea6f95c/briefs/f44_recurrence_prevention.md §Slice 3
"""Tests for observation_instants_v2 freshness lane in write_ingest_status.

Covers (per F44 recurrence-prevention brief):
  - Both v1 and v2 `rows_last_day` keys are present and non-zero when rows exist.
  - `observation_instants_v2_max_imported_at` matches the most-recent v2 row.
  - Backwards compat: all existing v1 keys still present in output.
  - v2 lane returns 0 / None gracefully when the table is empty.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.data.ingest_status_writer import write_ingest_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=n)).isoformat()


def _make_world_db() -> sqlite3.Connection:
    """Create an in-memory world DB with the minimal table surface needed."""
    conn = sqlite3.connect(":memory:")

    # observation_instants (v1) — minimal schema
    conn.execute("""
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            imported_at TEXT
        )
    """)

    # observation_instants_v2 — minimal schema matching production
    conn.execute("""
        CREATE TABLE observation_instants_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            timezone_name TEXT NOT NULL,
            local_timestamp TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL,
            dst_active INTEGER NOT NULL DEFAULT 0,
            is_ambiguous_local_hour INTEGER NOT NULL DEFAULT 0,
            is_missing_local_hour INTEGER NOT NULL DEFAULT 0,
            time_basis TEXT NOT NULL,
            temp_unit TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            data_version TEXT NOT NULL DEFAULT 'v1',
            provenance_json TEXT NOT NULL DEFAULT '{}'
        )
    """)

    # forecasts — minimal schema
    conn.execute("""
        CREATE TABLE forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            imported_at TEXT
        )
    """)

    # solar_daily — minimal schema
    conn.execute("""
        CREATE TABLE solar_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            fetched_at TEXT
        )
    """)

    # observations — minimal schema (no reliable ts column)
    conn.execute("""
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL
        )
    """)

    # data_coverage — for _holes_by_city_count
    conn.execute("""
        CREATE TABLE data_coverage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            data_table TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            fetched_at TEXT
        )
    """)

    # availability_fact — for _last_quarantine_reason
    conn.execute("""
        CREATE TABLE availability_fact (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            failure_type TEXT NOT NULL,
            details_json TEXT,
            started_at TEXT
        )
    """)

    conn.commit()
    return conn


def _insert_v1_row(conn: sqlite3.Connection, city: str, utc_ts: str) -> None:
    conn.execute(
        "INSERT INTO observation_instants (city, utc_timestamp) VALUES (?, ?)",
        (city, utc_ts),
    )


def _insert_v2_row(conn: sqlite3.Connection, city: str, utc_ts: str, imported_at: str) -> None:
    conn.execute(
        """
        INSERT INTO observation_instants_v2
            (city, target_date, source, timezone_name, local_timestamp, utc_timestamp,
             utc_offset_minutes, time_basis, temp_unit, imported_at)
        VALUES (?, '2026-05-18', 'wu_icao_history', 'America/Chicago', '2026-05-18T12:00:00',
                ?, 0, 'HOURLY', 'C', ?)
        """,
        (city, utc_ts, imported_at),
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestIngestStatusV2Rollup:
    """Rollup tick emits correct v2 keys alongside v1 keys."""

    def test_both_v1_v2_rows_reported(self, tmp_path):
        """Insert v1 and v2 rows; rollup emits both *_rows_24h as non-zero."""
        conn = _make_world_db()
        now = _now_iso()

        _insert_v1_row(conn, "chicago", now)
        _insert_v1_row(conn, "london", now)
        _insert_v2_row(conn, "chicago", now, now)

        conn.commit()

        out_path = tmp_path / "ingest_status.json"
        write_ingest_status(conn, state_dir=tmp_path)

        assert out_path.exists(), "ingest_status.json should have been written"
        payload = json.loads(out_path.read_text())

        # v1 rows present
        v1_day = payload["tables"]["observation_instants"]["rows_last_day"]
        assert v1_day >= 2, f"Expected ≥2 v1 rows in last day, got {v1_day}"

        # v2 rows present
        v2_day = payload["tables"]["observation_instants_v2"]["rows_last_day"]
        assert v2_day >= 1, f"Expected ≥1 v2 rows in last day, got {v2_day}"

    def test_v2_max_imported_at_matches_latest_row(self, tmp_path):
        """observation_instants_v2_max_imported_at matches the most-recent row."""
        conn = _make_world_db()

        older = _hours_ago(3)
        newer = _hours_ago(1)

        _insert_v2_row(conn, "chicago", _hours_ago(3), older)
        _insert_v2_row(conn, "london", _hours_ago(1), newer)
        conn.commit()

        write_ingest_status(conn, state_dir=tmp_path)
        payload = json.loads((tmp_path / "ingest_status.json").read_text())

        reported = payload.get("observation_instants_v2_max_imported_at")
        assert reported is not None, "observation_instants_v2_max_imported_at must be present"
        assert reported == newer, (
            f"Expected max_imported_at={newer!r}, got {reported!r}"
        )

    def test_backwards_compat_existing_keys_preserved(self, tmp_path):
        """All pre-existing keys must still be present (backwards compat)."""
        conn = _make_world_db()
        conn.commit()

        write_ingest_status(conn, state_dir=tmp_path)
        payload = json.loads((tmp_path / "ingest_status.json").read_text())

        # Top-level fields that existing consumers depend on
        assert "written_at" in payload
        assert "tables" in payload
        assert "last_quarantine_reason" in payload
        assert "source_health_written_at" in payload
        assert "source_health_summary" in payload

        # Existing table keys must not vanish
        for required_table in ("observation_instants", "forecasts", "solar_daily", "observations"):
            assert required_table in payload["tables"], (
                f"Backwards-compat failure: '{required_table}' missing from tables"
            )
            tbl = payload["tables"][required_table]
            assert "rows_last_hour" in tbl
            assert "rows_last_day" in tbl
            assert "holes_by_city_count" in tbl

    def test_v2_new_keys_present(self, tmp_path):
        """New v2 keys are present in the output even when v2 table is empty."""
        conn = _make_world_db()
        conn.commit()

        write_ingest_status(conn, state_dir=tmp_path)
        payload = json.loads((tmp_path / "ingest_status.json").read_text())

        # New v2 table entry
        assert "observation_instants_v2" in payload["tables"], (
            "observation_instants_v2 must appear in tables dict"
        )
        v2 = payload["tables"]["observation_instants_v2"]
        assert "rows_last_hour" in v2
        assert "rows_last_day" in v2
        assert "holes_by_city_count" in v2

        # New top-level freshness key (may be None if empty)
        assert "observation_instants_v2_max_imported_at" in payload

    def test_v2_empty_table_returns_zero_and_none(self, tmp_path):
        """v2 table with zero rows: rows_last_day=0, max_imported_at=None."""
        conn = _make_world_db()
        conn.commit()

        write_ingest_status(conn, state_dir=tmp_path)
        payload = json.loads((tmp_path / "ingest_status.json").read_text())

        v2_day = payload["tables"]["observation_instants_v2"]["rows_last_day"]
        assert v2_day == 0, f"Expected 0 rows for empty v2 table, got {v2_day}"

        max_ts = payload["observation_instants_v2_max_imported_at"]
        assert max_ts is None, f"Expected None for empty v2 max_imported_at, got {max_ts!r}"
