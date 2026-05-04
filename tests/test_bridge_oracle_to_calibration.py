# Created: 2026-05-02
# Last reused/audited: 2026-05-04
# Authority basis: Operator directive; skip-thin-days-for-oracle-bridge + docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A2 (path centralization migration; tests now redirect storage via ZEUS_STORAGE_ROOT instead of @patch on SNAPSHOT_DIR/ORACLE_FILE module constants).
"""Tests for oracle bridge coverage filtering."""

import json
import sqlite3
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


@patch("scripts.bridge_oracle_to_calibration.DB_PATH")
def test_bridge_coverage_filtering(
    mock_db_path, mock_db, storage_root_with_snapshot, tmp_path
):
    db_path, conn = mock_db
    mock_db_path.__str__.return_value = str(db_path)

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
