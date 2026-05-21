# Created: 2026-05-02
# Last reused/audited: 2026-05-21
# Lifecycle: created=2026-05-02; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Verify oracle-to-calibration coverage filtering with K1 forecast/world DB split.
# Reuse: Run before changing bridge_oracle_to_calibration DB routing or coverage thresholds.
# Authority basis: F40 K1 fix — bridge now uses get_forecasts_connection_with_world() (settlements
# is forecast_class post-K1-split); 2026-05-21 live oracle-penalty P0 canonical evidence repair.
"""Tests for oracle bridge coverage filtering."""

import json
import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from scripts.bridge_oracle_to_calibration import bridge


@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "test-forecasts.db"
    world_path = tmp_path / "test-world.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"ATTACH DATABASE {str(world_path)!r} AS world")
    conn.execute("""
        CREATE TABLE settlements (
            city TEXT, target_date TEXT, settlement_value REAL,
            pm_bin_lo REAL, pm_bin_hi REAL, settlement_source_type TEXT,
            unit TEXT, authority TEXT, temperature_metric TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE world.observation_instants_v2 (
            city TEXT,
            target_date TEXT,
            source TEXT,
            utc_timestamp TEXT,
            authority TEXT,
            temp_current REAL,
            running_max REAL,
            running_min REAL,
            temp_unit TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE observations (
            city TEXT,
            target_date TEXT,
            source TEXT,
            high_temp REAL,
            low_temp REAL,
            unit TEXT,
            authority TEXT
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


def _write_snapshot(tmp_path, city, target_date, payload):
    city_dir = tmp_path / "raw" / "oracle_shadow_snapshots" / city
    city_dir.mkdir(parents=True, exist_ok=True)
    snap = {"city": city, "target_date": target_date, **payload}
    (city_dir / f"{target_date}.json").write_text(json.dumps(snap))


def _insert_verified_hours(conn, city, target_date, source, count=22):
    for i in range(count):
        conn.execute(
            """
            INSERT INTO world.observation_instants_v2
                (city, target_date, source, utc_timestamp, authority, temp_current, running_max, running_min, temp_unit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city,
                target_date,
                source,
                f"{target_date}T{i:02d}:00:00Z",
                "VERIFIED",
                70.0,
                75.0,
                70.0,
                "F",
            ),
        )


def _insert_verified_high_hours(conn, city, target_date, source, high, *, unit="F", count=22):
    for i in range(count):
        value = float(high) if i == count - 1 else float(high) - 3.0
        conn.execute(
            """
            INSERT INTO world.observation_instants_v2
                (city, target_date, source, utc_timestamp, authority, temp_current, running_max, running_min, temp_unit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city,
                target_date,
                source,
                f"{target_date}T{i:02d}:00:00Z",
                "VERIFIED",
                value,
                value,
                None,
                unit,
            ),
        )


def _insert_verified_low_hours(conn, city, target_date, source, low, *, unit="F", count=22):
    for i in range(count):
        value = float(low) if i == 0 else float(low) + 3.0
        conn.execute(
            """
            INSERT INTO world.observation_instants_v2
                (city, target_date, source, utc_timestamp, authority, temp_current, running_max, running_min, temp_unit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city,
                target_date,
                source,
                f"{target_date}T{i:02d}:00:00Z",
                "VERIFIED",
                value,
                value,
                value,
                unit,
            ),
        )


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
    conn.execute("DELETE FROM world.observation_instants_v2")
    for i in range(21):
        conn.execute("INSERT INTO world.observation_instants_v2 (city, target_date, source, utc_timestamp, authority) VALUES (?, ?, ?, ?, ?)",
                     ('Chicago', '2026-05-01', 'wu_icao_history', f'2026-05-01T{i:02d}:00:00Z', 'VERIFIED'))
    conn.commit()

    stats = bridge(dry_run=True)
    assert stats["comparisons"] == 0
    assert stats["cities"] == 0

    # Case 2: Day with primary_hours < 22 but verified fallback >= 22 hours -> COUNTED
    for i in range(22):
        conn.execute("INSERT INTO world.observation_instants_v2 (city, target_date, source, utc_timestamp, authority) VALUES (?, ?, ?, ?, ?)",
                     ('Chicago', '2026-05-01', 'ogimet_metar_kord', f'2026-05-01T{i:02d}:00:00Z', 'VERIFIED'))
    conn.commit()

    stats = bridge(dry_run=True)
    assert stats["comparisons"] == 1
    assert stats["cities"] == 1

    # Case 3: Day with primary_hours >= 22 but UNVERIFIED authority -> SKIPPED
    conn.execute("DELETE FROM world.observation_instants_v2 WHERE source = 'ogimet_metar_kord'")
    conn.execute("DELETE FROM world.observation_instants_v2")
    for i in range(22):
        conn.execute("INSERT INTO world.observation_instants_v2 (city, target_date, source, utc_timestamp, authority) VALUES (?, ?, ?, ?, ?)",
                     ('Chicago', '2026-05-01', 'wu_icao_history', f'2026-05-01T{i:02d}:00:00Z', 'UNVERIFIED'))
    conn.commit()

    stats = bridge(dry_run=True)
    assert stats["comparisons"] == 0
    assert stats["cities"] == 0

    # Case 4: Day with VERIFIED primary_hours >= 22 -> COUNTED (regression)
    conn.execute("DELETE FROM world.observation_instants_v2")
    for i in range(22):
        conn.execute("INSERT INTO world.observation_instants_v2 (city, target_date, source, utc_timestamp, authority) VALUES (?, ?, ?, ?, ?)",
                     ('Chicago', '2026-05-01', 'wu_icao_history', f'2026-05-01T{i:02d}:00:00Z', 'VERIFIED'))
    conn.commit()

    stats = bridge(dry_run=True)
    assert stats["comparisons"] == 1
    assert stats["cities"] == 1


@patch("scripts.bridge_oracle_to_calibration.get_forecasts_connection_with_world")
def test_bridge_uses_wmo_half_up_for_wu_fahrenheit_to_celsius(
    mock_helper, mock_db, monkeypatch, tmp_path
):
    db_path, conn = mock_db
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

    @contextmanager
    def fake_ctx(*args, **kwargs):
        yield conn

    mock_helper.side_effect = fake_ctx

    city = "Kuala Lumpur"
    target_date = "2026-05-01"
    _write_snapshot(
        tmp_path,
        city,
        target_date,
        {
            "daily_high_f": 93.0,  # 33.888... C -> WMO half-up settlement 34 C
            "source": "wu_icao_history",
        },
    )
    conn.execute(
        """
        INSERT INTO settlements
            (city, target_date, settlement_value, pm_bin_lo, pm_bin_hi,
             settlement_source_type, unit, authority, temperature_metric)
        VALUES (?, ?, 34.0, 34.0, 34.0, 'wu_icao', 'C', 'VERIFIED', 'high')
        """,
        (city, target_date),
    )
    _insert_verified_high_hours(conn, city, target_date, "wu_icao_history", 93.0)
    conn.commit()

    stats = bridge(dry_run=True)

    assert stats == {"cities": 1, "comparisons": 1, "mismatches": 0}


@patch("scripts.bridge_oracle_to_calibration.get_forecasts_connection_with_world")
def test_bridge_keeps_hko_oracle_truncate_for_celsius_snapshots(
    mock_helper, mock_db, monkeypatch, tmp_path
):
    db_path, conn = mock_db
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

    @contextmanager
    def fake_ctx(*args, **kwargs):
        yield conn

    mock_helper.side_effect = fake_ctx

    city = "Hong Kong"
    target_date = "2026-05-01"
    _write_snapshot(
        tmp_path,
        city,
        target_date,
        {
            "source": "hko_hourly_accumulator",
            "hko_raw_payload": {
                "CLMMAXT": {"data": [[2026, 5, 1, 28.7, "C"]]},
            },
        },
    )
    conn.execute(
        """
        INSERT INTO settlements
            (city, target_date, settlement_value, pm_bin_lo, pm_bin_hi,
             settlement_source_type, unit, authority, temperature_metric)
        VALUES (?, ?, 28.0, 28.0, 28.0, 'HKO', 'C', 'VERIFIED', 'high')
        """,
        (city, target_date),
    )
    _insert_verified_high_hours(
        conn,
        city,
        target_date,
        "hko_hourly_accumulator",
        28.7,
        unit="C",
    )
    conn.commit()

    stats = bridge(dry_run=True)

    assert stats == {"cities": 1, "comparisons": 1, "mismatches": 0}


@patch("scripts.bridge_oracle_to_calibration.get_forecasts_connection_with_world")
def test_bridge_uses_canonical_observation_history_without_shadow_snapshots(
    mock_helper, mock_db, monkeypatch, tmp_path
):
    """Canonical verified observations must prevent false oracle MISSING.

    The live bug was at the bridge seam: raw shadow snapshots had only a tiny
    rolling window, while the K1 DB already had enough verified observations
    and settlements. The bridge must build the oracle artifact from canonical
    DB evidence even when there are no shadow snapshots.
    """
    db_path, conn = mock_db
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

    @contextmanager
    def fake_ctx(*args, **kwargs):
        yield conn

    mock_helper.side_effect = fake_ctx

    city = "Chicago"
    for day in range(1, 61):
        target_date = f"2026-04-{day:02d}" if day <= 30 else f"2026-05-{day - 30:02d}"
        conn.execute(
            """
            INSERT INTO settlements
                (city, target_date, settlement_value, pm_bin_lo, pm_bin_hi,
                 settlement_source_type, unit, authority, temperature_metric)
            VALUES (?, ?, 75.0, 75.0, 75.0, 'wu_icao', 'F', 'VERIFIED', 'high')
            """,
            (city, target_date),
        )
        _insert_verified_high_hours(conn, city, target_date, "wu_icao_history", 75.0)
    conn.commit()

    stats = bridge(dry_run=False)

    assert stats == {"cities": 1, "comparisons": 60, "mismatches": 0}
    artifact = json.loads((tmp_path / "data" / "oracle_error_rates.json").read_text())
    high = artifact[city]["high"]
    assert high["n"] == 60
    assert high["mismatches"] == 0
    assert high["source_role"] == "canonical_observation_instants_v2"
    assert high["status"] == "OK"
    assert high["penalty_multiplier"] == 1.0

    from src.strategy.oracle_penalty import get_oracle_info, reload
    from src.strategy.oracle_status import OracleStatus

    reload()
    info = get_oracle_info(city, "high")
    assert info.status == OracleStatus.OK
    assert info.penalty_multiplier == 1.0


@patch("scripts.bridge_oracle_to_calibration.get_forecasts_connection_with_world")
def test_bridge_uses_verified_settlement_value_when_legacy_bin_units_drift(
    mock_helper, mock_db, monkeypatch, tmp_path
):
    """Canonical settlement_value outranks stale display-unit bin labels."""
    db_path, conn = mock_db
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

    @contextmanager
    def fake_ctx(*args, **kwargs):
        yield conn

    mock_helper.side_effect = fake_ctx

    city = "London"
    target_date = "2026-05-01"
    conn.execute(
        """
        INSERT INTO settlements
            (city, target_date, settlement_value, pm_bin_lo, pm_bin_hi,
             settlement_source_type, unit, authority, temperature_metric)
        VALUES (?, ?, 5.0, 40.0, 41.0, 'wu_icao', 'C', 'VERIFIED', 'high')
        """,
        (city, target_date),
    )
    _insert_verified_high_hours(
        conn,
        city,
        target_date,
        "wu_icao_history",
        5.0,
        unit="C",
    )
    conn.commit()

    stats = bridge(dry_run=False)

    assert stats == {"cities": 1, "comparisons": 1, "mismatches": 0}
    artifact = json.loads((tmp_path / "data" / "oracle_error_rates.json").read_text())
    high = artifact[city]["high"]
    assert high["n"] == 1
    assert high["mismatches"] == 0


@patch("scripts.bridge_oracle_to_calibration.get_forecasts_connection_with_world")
def test_bridge_writes_low_metric_from_canonical_observation_history(
    mock_helper, mock_db, monkeypatch, tmp_path
):
    """Relationship: LOW canonical evidence must not collapse to metric-unsupported.

    Settlements and observation_instants_v2 carry enough low-track truth for
    normal cities. The bridge must write a low metric record so the evaluator
    sees penalty/no-penalty evidence rather than a structural zero multiplier.
    """
    db_path, conn = mock_db
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

    @contextmanager
    def fake_ctx(*args, **kwargs):
        yield conn

    mock_helper.side_effect = fake_ctx

    city = "London"
    for day in range(1, 61):
        target_date = f"2026-04-{day:02d}" if day <= 30 else f"2026-05-{day - 30:02d}"
        conn.execute(
            """
            INSERT INTO settlements
                (city, target_date, settlement_value, pm_bin_lo, pm_bin_hi,
                 settlement_source_type, unit, authority, temperature_metric)
            VALUES (?, ?, 9.0, 9.0, 9.0, 'wu_icao', 'C', 'VERIFIED', 'low')
            """,
            (city, target_date),
        )
        _insert_verified_low_hours(conn, city, target_date, "wu_icao_history", 9.0, unit="C")
    conn.commit()

    stats = bridge(dry_run=False)

    assert stats == {"cities": 1, "comparisons": 60, "mismatches": 0}
    artifact = json.loads((tmp_path / "data" / "oracle_error_rates.json").read_text())
    low = artifact[city]["low"]
    assert low["metric"] == "low"
    assert low["n"] == 60
    assert low["mismatches"] == 0
    assert low["source_role"] == "canonical_observation_instants_v2"
    assert low["status"] == "OK"
    assert low["penalty_multiplier"] == 1.0

    from src.strategy.oracle_penalty import get_oracle_info, reload
    from src.strategy.oracle_status import OracleStatus

    reload()
    info = get_oracle_info(city, "low")
    assert info.status == OracleStatus.OK
    assert info.penalty_multiplier == 1.0


@patch("scripts.bridge_oracle_to_calibration.get_forecasts_connection_with_world")
def test_bridge_writes_low_proxy_when_low_observations_exist_but_low_settlements_are_sparse(
    mock_helper, mock_db, monkeypatch, tmp_path
):
    """Relationship: LOW coverage plus city oracle evidence is not MISSING.

    Low markets can have sparse settlement history while the hourly observation
    store already has verified LOW support. In that case the bridge carries the
    city/source oracle verdict across metrics instead of injecting a MISSING
    penalty into every normal LOW candidate.
    """
    db_path, conn = mock_db
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

    @contextmanager
    def fake_ctx(*args, **kwargs):
        yield conn

    mock_helper.side_effect = fake_ctx

    city = "Chicago"
    for day in range(1, 61):
        target_date = f"2026-04-{day:02d}" if day <= 30 else f"2026-05-{day - 30:02d}"
        conn.execute(
            """
            INSERT INTO settlements
                (city, target_date, settlement_value, pm_bin_lo, pm_bin_hi,
                 settlement_source_type, unit, authority, temperature_metric)
            VALUES (?, ?, 75.0, 75.0, 75.0, 'wu_icao', 'F', 'VERIFIED', 'high')
            """,
            (city, target_date),
        )
        _insert_verified_high_hours(conn, city, target_date, "wu_icao_history", 75.0)
        _insert_verified_low_hours(conn, city, target_date, "wu_icao_history", 55.0)
    conn.commit()

    stats = bridge(dry_run=False)

    assert stats == {"cities": 2, "comparisons": 120, "mismatches": 0}
    artifact = json.loads((tmp_path / "data" / "oracle_error_rates.json").read_text())
    low = artifact[city]["low"]
    assert low["metric"] == "low"
    assert low["n"] == 60
    assert low["mismatches"] == 0
    assert low["source_role"] == "shared_city_oracle_source_proxy"
    assert low["source_metric"] == "high"
    assert low["observation_support_days"] == 60
    assert low["status"] == "OK"
    assert low["penalty_multiplier"] == 1.0


@patch("scripts.bridge_oracle_to_calibration.get_forecasts_connection_with_world")
def test_bridge_uses_verified_daily_observations_when_hourly_metric_table_is_empty(
    mock_helper, mock_db, monkeypatch, tmp_path
):
    """Relationship: daily canonical observations are valid oracle evidence."""
    db_path, conn = mock_db
    monkeypatch.setenv("ZEUS_STORAGE_ROOT", str(tmp_path))

    @contextmanager
    def fake_ctx(*args, **kwargs):
        yield conn

    mock_helper.side_effect = fake_ctx

    city = "Hong Kong"
    for day in range(1, 31):
        target_date = f"2026-04-{day:02d}"
        conn.execute(
            """
            INSERT INTO settlements
                (city, target_date, settlement_value, pm_bin_lo, pm_bin_hi,
                 settlement_source_type, unit, authority, temperature_metric)
            VALUES (?, ?, 28.0, 28.0, 28.0, 'HKO', 'C', 'VERIFIED', 'high')
            """,
            (city, target_date),
        )
        conn.execute(
            """
            INSERT INTO settlements
                (city, target_date, settlement_value, pm_bin_lo, pm_bin_hi,
                 settlement_source_type, unit, authority, temperature_metric)
            VALUES (?, ?, 22.0, 22.0, 22.0, 'HKO', 'C', 'VERIFIED', 'low')
            """,
            (city, target_date),
        )
        conn.execute(
            """
            INSERT INTO observations
                (city, target_date, source, high_temp, low_temp, unit, authority)
            VALUES (?, ?, 'hko_daily_api', 28.7, 22.9, 'C', 'VERIFIED')
            """,
            (city, target_date),
        )
    conn.commit()

    stats = bridge(dry_run=False)

    assert stats == {"cities": 2, "comparisons": 60, "mismatches": 0}
    artifact = json.loads((tmp_path / "data" / "oracle_error_rates.json").read_text())
    assert artifact[city]["high"]["status"] == "OK"
    assert artifact[city]["low"]["status"] == "OK"
    assert artifact[city]["low"]["source_role"] == "canonical_observation_instants_v2"
