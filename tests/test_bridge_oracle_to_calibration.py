# Created: 2026-05-02
# Last reused/audited: 2026-05-17
# Authority basis: F40 K1 fix — bridge now uses get_forecasts_connection_with_world() (settlements
# is forecast_class post-K1-split). Test patches the context-manager helper instead of removed
# DB_PATH constant. See docs/operations/task_2026-05-17_post_karachi_remediation/FIX_K1_READERS.md §A.
"""Tests for oracle bridge coverage filtering."""

import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from scripts.bridge_oracle_to_calibration import bridge


@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "test-world.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE settlements (
            city TEXT, target_date TEXT, settlement_value REAL,
            pm_bin_lo REAL, pm_bin_hi REAL, settlement_source_type TEXT,
            unit TEXT, authority TEXT, temperature_metric TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE observation_instants_v2 (
            city TEXT, target_date TEXT, source TEXT, utc_timestamp TEXT, authority TEXT
        )
    """)
    conn.commit()
    return db_path, conn


@pytest.fixture
def storage_root_with_snapshot(monkeypatch, tmp_path):
    """Redirect storage to tmp_path and place a synthetic snapshot at the
    canonical layout that the bridge will discover (no mocks needed)."""
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))
    snap_dir = tmp_path / "raw" / "oracle_shadow_snapshots"
    city_dir = snap_dir / "Chicago"
    city_dir.mkdir(parents=True)
    snap = {
        "city": "Chicago",
        "target_date": "2026-05-01",
        "daily_high_f": 75.0,
        "source": "wu_icao_history",
    }
    (city_dir / "2026-05-01.json").write_text(json.dumps(snap))
    return tmp_path


@patch("scripts.bridge_oracle_to_calibration.get_forecasts_connection_with_world")
def test_bridge_coverage_filtering(
    mock_helper, mock_db, storage_root_with_snapshot, tmp_path
):
    db_path, conn = mock_db

    @contextmanager
    def fake_ctx(*args, **kwargs):
        yield conn

    mock_helper.side_effect = fake_ctx

    # 1. Setup settlement
    conn.execute("""
        INSERT INTO settlements (city, target_date, settlement_value, pm_bin_lo, pm_bin_hi, settlement_source_type, unit, authority, temperature_metric)
        VALUES ('Chicago', '2026-05-01', 24.0, 23.0, 25.0, 'wu_icao', 'F', 'VERIFIED', 'high')
    """)

    # Case 1: Day with primary_hours < 22 and no verified fallback -> SKIPPED
    conn.execute("DELETE FROM observation_instants_v2")
    for i in range(21):
        conn.execute("INSERT INTO observation_instants_v2 (city, target_date, source, utc_timestamp, authority) VALUES (?, ?, ?, ?, ?)",
                     ('Chicago', '2026-05-01', 'wu_icao_history', f'2026-05-01T{i:02d}:00:00Z', 'VERIFIED'))
    conn.commit()

    stats = bridge(dry_run=True)
    assert stats["comparisons"] == 0
    assert stats["cities"] == 0

    # Case 2: Day with primary_hours < 22 but verified fallback >= 22 hours -> COUNTED
    for i in range(22):
        conn.execute("INSERT INTO observation_instants_v2 (city, target_date, source, utc_timestamp, authority) VALUES (?, ?, ?, ?, ?)",
                     ('Chicago', '2026-05-01', 'ogimet_metar_kord', f'2026-05-01T{i:02d}:00:00Z', 'VERIFIED'))
    conn.commit()

    stats = bridge(dry_run=True)
    assert stats["comparisons"] == 1
    assert stats["cities"] == 1

    # Case 3: Day with primary_hours >= 22 but UNVERIFIED authority -> SKIPPED
    conn.execute("DELETE FROM observation_instants_v2 WHERE source = 'ogimet_metar_kord'")
    conn.execute("DELETE FROM observation_instants_v2")
    for i in range(22):
        conn.execute("INSERT INTO observation_instants_v2 (city, target_date, source, utc_timestamp, authority) VALUES (?, ?, ?, ?, ?)",
                     ('Chicago', '2026-05-01', 'wu_icao_history', f'2026-05-01T{i:02d}:00:00Z', 'UNVERIFIED'))
    conn.commit()

    stats = bridge(dry_run=True)
    assert stats["comparisons"] == 0
    assert stats["cities"] == 0

    # Case 4: Day with VERIFIED primary_hours >= 22 -> COUNTED (regression)
    conn.execute("DELETE FROM observation_instants_v2")
    for i in range(22):
        conn.execute("INSERT INTO observation_instants_v2 (city, target_date, source, utc_timestamp, authority) VALUES (?, ?, ?, ?, ?)",
                     ('Chicago', '2026-05-01', 'wu_icao_history', f'2026-05-01T{i:02d}:00:00Z', 'VERIFIED'))
    conn.commit()

    stats = bridge(dry_run=True)
    assert stats["comparisons"] == 1
    assert stats["cities"] == 1
