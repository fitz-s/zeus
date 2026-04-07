"""Tests for data migration from Rainstorm."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

# We test the migration functions directly
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.state.db import get_connection, get_shared_connection, init_schema, ZEUS_DB_PATH


def _create_mock_rainstorm_db(path: Path) -> None:
    """Create a minimal rainstorm.db with known test data."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE settlements (
            id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
            event_id TEXT, settled_at TEXT, winning_range TEXT,
            actual_temp_f REAL, inferred_actual_temp_f REAL,
            temp_unit TEXT, noaa_forecast_f REAL, wu_forecast_f REAL,
            openmeteo_forecast_f REAL, ensemble_forecast_f REAL,
            ensemble_std REAL, noaa_error_f REAL, wu_error_f REAL,
            openmeteo_error_f REAL, source TEXT, source_file TEXT,
            imported_at TEXT, actual_temp_source TEXT,
            actual_temp_updated_at TEXT
        );

        CREATE TABLE observations (
            id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
            local_hour REAL, granularity TEXT, source TEXT,
            temp_high_f REAL, temp_low_f REAL, temp_current_f REAL,
            running_max_f REAL, delta_rate_f_per_h REAL,
            raw_response TEXT, source_file TEXT, imported_at TEXT,
            station_id TEXT, observation_count INTEGER
        );

        CREATE TABLE market_events (
            id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
            event_id TEXT, condition_id TEXT, range_label TEXT,
            range_low REAL, range_high REAL, question_text TEXT,
            outcome TEXT, outcome_price REAL,
            probability_space_quality REAL, raw_response TEXT,
            source_file TEXT, imported_at TEXT
        );

        CREATE TABLE token_price_log (
            id INTEGER PRIMARY KEY, token_id TEXT, city TEXT,
            target_date TEXT, range_label TEXT, observed_at TEXT,
            price REAL, imported_at TEXT
        );

        INSERT INTO settlements (city, target_date, event_id, winning_range,
            actual_temp_f, temp_unit, settled_at, actual_temp_source)
        VALUES ('NYC', '2025-01-15', '12345', '30-32', 31.0, 'F',
                '2025-01-15T20:00:00Z', 'wu_daily_observed');

        INSERT INTO settlements (city, target_date, event_id, winning_range,
            actual_temp_f, temp_unit, settled_at, actual_temp_source)
        VALUES ('London', '2025-01-16', '12346', '4-5', 4.5, 'C',
                '2025-01-16T20:00:00Z', 'wu_daily_observed');

        INSERT INTO observations (city, target_date, granularity, source,
            temp_high_f, temp_low_f, station_id, imported_at)
        VALUES ('NYC', '2025-01-15', 'daily', 'iem_asos', 31.0, 22.0,
                'NYC', '2025-01-16T00:00:00Z');

        INSERT INTO observations (city, target_date, granularity, source,
            temp_high_f, station_id, imported_at)
        VALUES ('NYC', '2025-01-15', 'hourly', 'openmeteo_archive', 31.0,
                NULL, '2025-01-16T00:00:00Z');

        INSERT INTO market_events (city, target_date, event_id, condition_id,
            range_label, range_low, range_high, outcome, imported_at)
        VALUES ('NYC', '2025-01-15', '12345', 'cond_001',
                '30-32', 30.0, 32.0, 'yes', '2025-01-10T00:00:00Z');

        INSERT INTO token_price_log (token_id, city, target_date,
            range_label, observed_at, price)
        VALUES ('abc123', 'NYC', '2025-01-15', '30-32',
                '2025-01-14T12:00:00Z', 0.45);

        -- Test data that should be excluded
        INSERT INTO token_price_log (token_id, city, target_date,
            range_label, observed_at, price)
        VALUES ('test-token-123', 'NYC', '2025-01-15', '30-32',
                '2025-01-14T12:00:00Z', 0.55);
    """)
    conn.commit()
    conn.close()


def test_migration_with_mock_data(tmp_path, monkeypatch):
    """Test migration with a small mock rainstorm.db."""
    mock_rs = tmp_path / "rainstorm.db"
    _create_mock_rainstorm_db(mock_rs)

    zeus_db = tmp_path / "zeus.db"
    zeus_shared_db = tmp_path / "zeus-shared.db"
    monkeypatch.setattr("src.state.db.ZEUS_DB_PATH", zeus_db)
    monkeypatch.setattr("src.state.db.ZEUS_SHARED_DB_PATH", zeus_shared_db)
    monkeypatch.setattr("src.config.STATE_DIR", tmp_path)

    from scripts.migrate_rainstorm_data import migrate
    counts = migrate(rainstorm_path=mock_rs)

    assert counts["settlements"] == 2
    # Only daily observations migrate (hourly excluded)
    assert counts["observations"] == 1
    assert counts["market_events"] == 1
    # test-token-123 excluded
    assert counts["token_price_log"] == 1

    # Verify data correctness — migration now writes to shared DB (zeus-shared.db)
    conn = get_shared_connection()

    settlement = conn.execute(
        "SELECT * FROM settlements WHERE city='NYC'"
    ).fetchone()
    assert settlement["winning_bin"] == "30-32"
    assert settlement["settlement_value"] == 31.0

    london = conn.execute(
        "SELECT * FROM settlements WHERE city='London'"
    ).fetchone()
    assert london["settlement_value"] == 4.5

    obs = conn.execute("SELECT * FROM observations").fetchone()
    assert obs["high_temp"] == 31.0
    assert obs["unit"] == "F"

    conn.close()
