# Created: 2026-09-17
# Last reused/audited: 2026-09-20
# Authority basis: current-resolver settlement history contract; source-clock
# precision fitting may use only durable labels under the current source epoch.
"""Current-resolver labels are the only fitter truth surface."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest


def _city(*, effective_date: str = "2026-08-23") -> SimpleNamespace:
    return SimpleNamespace(
        name="Houston",
        settlement_source_type="noaa",
        previous_settlement_source_type="wu_icao",
        settlement_source_type_effective_date=effective_date,
        wu_station="KHOU",
        settlement_unit="F",
        settlement_page_view="all",
        lat=29.64582,
        lon=-95.28214,
        timezone="America/Chicago",
    )


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
            temperature_metric TEXT, winning_bin TEXT, settlement_value REAL,
            settlement_source TEXT, settled_at TEXT, authority TEXT,
            provenance_json TEXT, recorded_at TEXT, settlement_unit TEXT,
            outcome_type INTEGER, resolution_state TEXT
        );
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY, city TEXT, target_date TEXT, source TEXT,
            station_id TEXT, unit TEXT, data_source_version TEXT, high_temp REAL,
            low_temp REAL, high_fetch_utc TEXT, low_fetch_utc TEXT,
            high_provenance_metadata TEXT, low_provenance_metadata TEXT
        );
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY, model TEXT, city TEXT,
            target_date TEXT, metric TEXT, source_cycle_time TEXT,
            source_available_at TEXT, captured_at TEXT, lead_days INTEGER,
            forecast_value_c REAL, endpoint TEXT, training_allowed INTEGER,
            recorded_at TEXT, coverage_status TEXT, source_id TEXT,
            source_family TEXT, product_id TEXT, request_url_hash TEXT,
            model_name TEXT, provider TEXT, endpoint_mode TEXT, request_params_json TEXT,
            latitude_requested REAL, longitude_requested REAL, timezone_requested TEXT
        );
        """
    )
    target = "2026-09-11"
    c.execute(
        "INSERT INTO observations VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "Houston", target, "noaa_wrh_khou", "KHOU", "F",
            "noaa_wrh_timeseries_v1", 94.0, 78.0,
            "2026-09-12T12:00:00+00:00", "2026-09-12T12:00:00+00:00",
            json.dumps({"settlement_page_view": "all", "station": "KHOU"}),
            json.dumps({"settlement_page_view": "all", "station": "KHOU"}),
        ),
    )
    c.execute(
        """INSERT INTO settlement_outcomes (
            settlement_id, city, target_date, temperature_metric, winning_bin,
            settlement_value, settlement_source, settled_at, authority,
            provenance_json, recorded_at, settlement_unit, resolution_state
        ) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "Houston", target, "high", "94", 94.0,
            "https://weather.gov/wrh/timeseries?site=KHOU",
            "2026-09-12T13:00:00+00:00", "VERIFIED",
            json.dumps({
                "era": "internal_resolver_post_2026_02_21",
                "era_start_date_utc": "2026-02-21",
                "source_family": "noaa",
                "settlement_source_type": "noaa",
                "obs_id": 1,
                "obs_source": "noaa_wrh_khou",
                "data_version": "noaa_wrh_timeseries_v1",
                "rounding_rule": "wmo_half_up",
            }),
            "2026-09-12T13:01:00+00:00", "F", "VENUE_RESOLVED",
        ),
    )
    c.execute(
        """INSERT INTO raw_model_forecasts VALUES (
            1, 'ecmwf_ifs', 'Houston', ?, 'high',
            '2026-09-10T00:00:00+00:00', '2026-09-10T06:00:00+00:00',
            '2026-09-10T06:01:00+00:00', 1, 34.0, 'single_runs', 0,
            '2026-09-10 06:02:00', 'COVERED', 'ecmwf_ifs_single_runs',
            'openmeteo_single_runs', 'ecmwf_ifs::single_runs', 'request-hash',
            'ecmwf_ifs', 'open-meteo', 'single_runs',
            '{"cell_selection":"land","hourly":"temperature_2m","latitude":29.64582,"longitude":-95.28214,"models":"ecmwf_ifs","temperature_unit":"celsius","timezone":"America/Chicago"}',
            29.64582, -95.28214, 'America/Chicago'
        )""",
        (target,),
    )
    c.commit()
    return c


def _load(conn: sqlite3.Connection, *, city: SimpleNamespace | None = None, as_of: str = "2026-12-31T00:00:00+00:00") -> dict:
    from scripts.fit_source_clock_city_weights import load_walk_forward_rows

    return load_walk_forward_rows(
        conn,
        as_of=as_of,
        cities_by_name={"Houston": city or _city()},
    )


def test_current_resolver_outcome_is_the_training_label(conn: sqlite3.Connection) -> None:
    loaded = _load(conn)

    assert loaded["settle"][("Houston", "high")]["2026-09-11"] == pytest.approx(
        (94.0 - 32.0) * 5.0 / 9.0
    )
    assert loaded["obs"][("Houston", "high")]["2026-09-11"] == {"ecmwf_ifs": 34.0}


def test_pre_current_source_epoch_is_excluded(conn: sqlite3.Connection) -> None:
    loaded = _load(conn, city=_city(effective_date="2026-09-12"))

    assert loaded["settle"] == {}
    assert loaded["excluded_reason_counts"]["PRE_CURRENT_SOURCE_EPOCH"] == 1


def test_observation_only_row_cannot_replace_current_resolver_outcome(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settlement_outcomes")
    conn.commit()

    loaded = _load(conn)

    assert loaded["settle"] == {}
    assert loaded["excluded_reason_counts"]["RAW_LABEL_NOT_CURRENT_RESOLVER_ELIGIBLE"] == 1


def test_label_and_raw_input_must_be_known_before_as_of(conn: sqlite3.Connection) -> None:
    loaded = _load(conn, as_of="2026-09-12T13:00:30+00:00")

    assert loaded["settle"] == {}
    assert loaded["excluded_reason_counts"]["OUTCOME_NOT_KNOWN_AS_OF"] == 1
