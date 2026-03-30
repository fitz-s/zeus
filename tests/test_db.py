"""Tests for database schema initialization."""

import sqlite3
import tempfile
from pathlib import Path

from src.state.db import get_connection, init_schema


def test_init_schema_creates_all_tables():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_schema(conn)

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row["name"] for row in cursor.fetchall()}

    expected = {
        "settlements", "observations", "market_events", "token_price_log",
        "ensemble_snapshots", "calibration_pairs", "platt_models",
        "trade_decisions", "shadow_signals", "chronicle"
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"
    conn.close()


def test_init_schema_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_schema(conn)
    init_schema(conn)  # Should not raise
    conn.close()


def test_ensemble_snapshots_unique_constraint():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_schema(conn)

    row = {
        "city": "NYC", "target_date": "2026-01-15",
        "issue_time": "2026-01-12T00:00:00Z",
        "valid_time": "2026-01-15T00:00:00Z",
        "available_at": "2026-01-12T06:00:00Z",
        "fetch_time": "2026-01-12T06:05:00Z",
        "lead_hours": 72.0,
        "members_json": "[50.0]",
        "model_version": "ecmwf_ifs025",
        "data_version": "v1"
    }

    conn.execute("""
        INSERT INTO ensemble_snapshots
        (city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version)
        VALUES (:city, :target_date, :issue_time, :valid_time, :available_at,
                :fetch_time, :lead_hours, :members_json, :model_version, :data_version)
    """, row)
    conn.commit()

    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO ensemble_snapshots
            (city, target_date, issue_time, valid_time, available_at, fetch_time,
             lead_hours, members_json, model_version, data_version)
            VALUES (:city, :target_date, :issue_time, :valid_time, :available_at,
                    :fetch_time, :lead_hours, :members_json, :model_version, :data_version)
        """, row)

    conn.close()


def test_wal_mode_enabled():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()
