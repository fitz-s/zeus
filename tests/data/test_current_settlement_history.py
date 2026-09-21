from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.data.current_settlement_history import read_current_settlement_history


AS_OF = datetime(2026, 9, 1, tzinfo=UTC)
ERA = "internal_resolver_post_2026_02_21"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY, city TEXT, target_date TEXT,
            temperature_metric TEXT, winning_bin TEXT, settlement_value REAL,
            settlement_source TEXT, settled_at TEXT, authority TEXT,
            provenance_json TEXT, recorded_at TEXT, settlement_unit TEXT,
            outcome_type INTEGER, resolution_state TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE observations (
            id INTEGER PRIMARY KEY, city TEXT, target_date TEXT, source TEXT,
            station_id TEXT, unit TEXT, data_source_version TEXT,
            high_temp REAL, low_temp REAL, high_fetch_utc TEXT, low_fetch_utc TEXT,
            high_provenance_metadata TEXT, low_provenance_metadata TEXT
        )"""
    )
    return conn


def _cities() -> dict[str, object]:
    return {
        "Chicago": SimpleNamespace(
            name="Chicago", settlement_source_type="noaa",
            previous_settlement_source_type="wu_icao",
            settlement_source_type_effective_date="2026-08-23",
            wu_station="KORD", settlement_unit="F", settlement_page_view="hourly",
        )
    }


def _insert_pair(
    conn: sqlite3.Connection,
    *,
    outcome_id: int,
    target_date: str = "2026-08-23",
    metric: str = "high",
    outcome_station: str = "KORD",
    observation_station: str = "KORD",
    page_view: str = "hourly",
    settled_at: str = "2026-08-24T00:30:00-05:00",
    recorded_at: str = "2026-08-24 06:00:00",
    fetched_at: str = "2026-08-24T06:01:00+00:00",
    value: float = 70.0,
    observation_value: float = 70.0,
    source_family: str = "NOAA",
    era: str = ERA,
    resolution_state: str | None = None,
    outcome_type: int | None = None,
    settlement_url: str | None = None,
) -> None:
    provenance = {
        "obs_id": outcome_id,
        "era": era,
        "era_start_date_utc": "2026-02-21",
        "source_family": source_family,
        "settlement_source_type": source_family,
        "rounding_rule": "wmo_half_up",
    }
    conn.execute(
        """INSERT INTO settlement_outcomes (
            settlement_id, city, target_date, temperature_metric, winning_bin,
            settlement_value, settlement_source, settled_at, authority,
            provenance_json, recorded_at, settlement_unit, outcome_type, resolution_state
        ) VALUES (?, 'Chicago', ?, ?, '70°F', ?, ?, ?, 'VERIFIED', ?, ?, 'F', ?, ?)""",
        (
            outcome_id, target_date, metric, value,
            settlement_url or f"https://www.weather.gov/wrh/timeseries?site={outcome_station}",
            settled_at, json.dumps(provenance), recorded_at, outcome_type, resolution_state,
        ),
    )
    meta = json.dumps({"settlement_page_view": page_view, "station": observation_station})
    conn.execute(
        """INSERT INTO observations VALUES (?, 'Chicago', ?, ?, ?, 'F',
           'noaa_wrh_timeseries_v1', ?, ?, ?, ?, ?, ?)""",
        (
            outcome_id, target_date, f"noaa_wrh_{observation_station.lower()}",
            observation_station,
            observation_value if metric == "high" else 69.0,
            observation_value if metric == "low" else 60.0,
            fetched_at if metric == "high" else None,
            fetched_at if metric == "low" else None,
            meta, meta,
        ),
    )


def _read(conn: sqlite3.Connection, as_of: datetime = AS_OF):
    return read_current_settlement_history(conn, cities_by_name=_cities(), as_of=as_of)


def test_accepts_exact_post_cutover_observation_and_returns_label_known_at() -> None:
    conn = _db()
    _insert_pair(conn, outcome_id=1)

    result = _read(conn)

    assert len(result.rows) == 1
    row = result.rows[0]
    assert (row.city, row.target_date, row.metric, row.settlement_value) == (
        "Chicago", "2026-08-23", "high", 70.0
    )
    assert row.station_id == "KORD"
    assert row.page_view == "hourly"
    assert row.label_known_at == datetime(2026, 8, 24, 6, 1, tzinfo=UTC)


def test_rejects_pre_effective_source_epoch_even_when_city_date_join_would_match() -> None:
    conn = _db()
    _insert_pair(conn, outcome_id=1, target_date="2026-08-22", source_family="WU")

    result = _read(conn)

    assert not result.rows
    assert result.excluded_reason_counts["PRE_CURRENT_SOURCE_EPOCH"] == 1


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"outcome_station": "KMDW"}, "OUTCOME_STATION_MISMATCH"),
        ({"observation_station": "KMDW"}, "EXACT_OBSERVATION_IDENTITY_MISMATCH"),
        ({"page_view": "all"}, "SETTLEMENT_PAGE_VIEW_MISMATCH"),
        ({"settled_at": "2026-08-24T00:30:00"}, "OUTCOME_TIME_MISSING_OR_INVALID"),
        ({"fetched_at": "2026-09-02T00:00:00+00:00"}, "OBSERVATION_NOT_KNOWN_AS_OF"),
        ({"recorded_at": "2026-09-02 00:00:00"}, "OUTCOME_NOT_KNOWN_AS_OF"),
        ({"value": float("nan")}, "OUTCOME_VALUE_INVALID"),
        ({"observation_value": 69.0}, "OBSERVATION_VALUE_MISMATCH"),
        ({"era": "uma_oo_v2"}, "NOT_CURRENT_RESOLVER_ERA"),
        ({"settlement_url": "https://www.wunderground.com/history/daily/us/il/chicago/KORD"}, "OUTCOME_SOURCE_FAMILY_MISMATCH"),
        ({"resolution_state": "VOID_50_50"}, "OUTCOME_NOT_LEARNING_FINAL"),
        ({"resolution_state": "DISPUTED"}, "OUTCOME_NOT_LEARNING_FINAL"),
    ],
)
def test_rejects_wrong_contract_or_unavailable_label(kwargs: dict[str, object], reason: str) -> None:
    conn = _db()
    _insert_pair(conn, outcome_id=1, **kwargs)

    result = _read(conn)

    assert not result.rows
    assert result.excluded_reason_counts[reason] == 1


def test_requires_aware_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _read(_db(), datetime(2026, 9, 1))


def _hko_cities() -> dict[str, object]:
    return {
        "Hong Kong": SimpleNamespace(
            name="Hong Kong", settlement_source_type="hko",
            previous_settlement_source_type=None,
            settlement_source_type_effective_date=None,
            wu_station=None, settlement_unit="C", settlement_page_view="all",
        )
    }


@pytest.mark.parametrize(
    ("settlement_url", "data_version", "expected_rows", "reason"),
    [
        ("https://www.weather.gov.hk/en/cis/climat.htm", "hko_dailyextract_live_v1", 1, None),
        ("https://www.wunderground.com/history/daily/hk/hong-kong/VHHH", "hko_dailyextract_live_v1", 0, "OUTCOME_SOURCE_FAMILY_MISMATCH"),
        ("https://www.weather.gov.hk/en/cis/climat.htm", "hko_realtime_v1", 0, "OBSERVATION_PRODUCT_MISMATCH"),
    ],
)
def test_hko_requires_explicit_current_url_and_daily_product(
    settlement_url: str, data_version: str, expected_rows: int, reason: str | None,
) -> None:
    conn = _db()
    provenance = json.dumps({
        "obs_id": 7, "era": ERA, "era_start_date_utc": "2026-02-21",
        "source_family": "HKO", "settlement_source_type": "HKO",
        "rounding_rule": "oracle_truncate",
    })
    conn.execute(
        """INSERT INTO settlement_outcomes (
            settlement_id, city, target_date, temperature_metric, winning_bin,
            settlement_value, settlement_source, settled_at, authority,
            provenance_json, recorded_at, settlement_unit
        ) VALUES (7, 'Hong Kong', '2026-08-23', 'high', '30°C', 30,
                  ?, '2026-08-24T00:00:00+08:00', 'VERIFIED', ?,
                  '2026-08-23 17:00:00', 'C')""",
        (settlement_url, provenance),
    )
    conn.execute(
        """INSERT INTO observations VALUES (
            7, 'Hong Kong', '2026-08-23', 'hko_daily_api', NULL, 'C',
            ?, 30, 25, '2026-08-23T17:00:00+00:00', '2026-08-23T17:00:00+00:00', '{}', '{}'
        )""",
        (data_version,),
    )

    result = read_current_settlement_history(conn, cities_by_name=_hko_cities(), as_of=AS_OF)

    assert len(result.rows) == expected_rows
    if reason is not None:
        assert result.excluded_reason_counts[reason] == 1
