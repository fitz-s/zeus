# Created: 2026-05-22
# Last reused/audited: 2026-07-08
# Authority basis: docs/archive/2026-Q2/operations_historical/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-C
# Lifecycle: created=2026-05-22; last_reviewed=2026-07-08; last_reused=2026-07-08
# Purpose: Regression antibody for Root C — high_so_far must be MAX(running_max) not latest row's value.
# Reuse: Run when day0_observation_reader.read_day0_high_so_far or observation_instants schema changes.
"""Tests for src/data/day0_observation_reader.py — Root C regression antibody.

Root C: observation_instants.running_max = per-hour bucket max (non-monotonic).
The naive approach (latest row's running_max) returns the wrong value whenever
the daily peak occurred before the last observation.

The antibody: high_so_far == MAX(running_max) over ALL qualifying rows,
not the running_max of the latest row.

Concrete case: Amsterdam 2026-05-22
  - 15:00 local (13:00 UTC): running_max=25.0 (the daily peak)
  - 23:00 local (21:00 UTC): running_max=17.0 (temperature dropped)
  - naive reader → 17.0 (wrong)
  - correct reader → 25.0 (correct)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.data.day0_observation_reader import (
    COVERAGE_LOW,
    COVERAGE_NONE,
    COVERAGE_OK,
    Day0ObservedExtrema,
    read_day0_observation_context_from_instants,
    read_day0_observed_extrema,
)


_HKO_OFFICIAL_PROVENANCE = (
    '{"observation_basis":"hko_since_midnight_extrema_1min_mean"}'
)


# ---------------------------------------------------------------------------
# Fixture: minimal in-memory DB with observation_instants schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
    CREATE TABLE observation_instants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT NOT NULL,
        target_date TEXT NOT NULL,
        source TEXT NOT NULL,
        timezone_name TEXT NOT NULL,
        local_hour REAL,
        local_timestamp TEXT NOT NULL,
        utc_timestamp TEXT NOT NULL,
        utc_offset_minutes INTEGER NOT NULL DEFAULT 0,
        dst_active INTEGER NOT NULL DEFAULT 0,
        is_ambiguous_local_hour INTEGER NOT NULL DEFAULT 0,
        is_missing_local_hour INTEGER NOT NULL DEFAULT 0,
        time_basis TEXT NOT NULL DEFAULT 'observation',
        temp_current REAL,
        running_max REAL,
        running_min REAL,
        delta_rate_per_h REAL,
        temp_unit TEXT NOT NULL DEFAULT 'C',
        station_id TEXT,
        observation_count INTEGER,
        raw_response TEXT,
        source_file TEXT,
        imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
        data_version TEXT NOT NULL DEFAULT 'v1',
        provenance_json TEXT NOT NULL DEFAULT '{}',
        temperature_metric TEXT,
        physical_quantity TEXT,
        observation_field TEXT,
        training_allowed INTEGER DEFAULT 1,
        causality_status TEXT DEFAULT 'OK',
        source_role TEXT
    )
"""


def _make_conn() -> sqlite3.Connection:
    """Return an in-memory connection with observation_instants created."""
    conn = sqlite3.connect(":memory:")
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


def _insert(conn: sqlite3.Connection, **kwargs: object) -> None:
    """Insert a minimal row, filling defaults for unspecified columns."""
    defaults = {
        "city": "Amsterdam",
        "target_date": "2026-05-22",
        "source": "wu_icao_history",
        "timezone_name": "Europe/Amsterdam",
        "local_hour": 12.0,
        "local_timestamp": "2026-05-22T12:00:00+02:00",
        "utc_timestamp": "2026-05-22T10:00:00+00:00",
        "utc_offset_minutes": 120,
        "dst_active": 1,
        "is_ambiguous_local_hour": 0,
        "is_missing_local_hour": 0,
        "time_basis": "observation",
        "temp_current": None,
        "running_max": None,
        "running_min": None,
        "delta_rate_per_h": None,
        "temp_unit": "C",
        "station_id": "EHAM",
        "observation_count": 1,
        "raw_response": None,
        "source_file": None,
        "imported_at": "2026-05-22T22:00:00+00:00",
        "authority": "VERIFIED",
        "data_version": "v1.wu-native",
        "provenance_json": '{"source_url": "x", "station_id": "EHAM", "station_registry_version": "1"}',
        "temperature_metric": None,
        "physical_quantity": None,
        "observation_field": None,
        "training_allowed": 1,
        "causality_status": "OK",
        "source_role": "historical_hourly",
    }
    defaults.update(kwargs)
    cols = list(defaults.keys())
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO observation_instants ({', '.join(cols)}) VALUES ({placeholders})",
        [defaults[c] for c in cols],
    )
    conn.commit()


def test_reader_rejects_hko_reaudit_rows_until_runtime_monitoring_role():
    conn = _make_conn()
    _insert(
        conn,
        city="Hong Kong",
        target_date="2026-06-26",
        source="hko_hourly_accumulator",
        timezone_name="Asia/Hong_Kong",
        local_hour=7.0,
        local_timestamp="2026-06-26T07:00:00+08:00",
        utc_timestamp="2026-06-25T23:00:00+00:00",
        utc_offset_minutes=480,
        temp_current=27.0,
        running_max=27.0,
        running_min=27.0,
        authority="ICAO_STATION_NATIVE",
        training_allowed=0,
        causality_status="REQUIRES_SOURCE_REAUDIT",
        source_role="coverage_fill_evidence",
        station_id="HKO",
    )

    out = read_day0_observed_extrema(
        conn,
        city="Hong Kong",
        target_date="2026-06-26",
        timezone_name="Asia/Hong_Kong",
        decision_time_utc=datetime(2026, 6, 26, 1, 0, tzinfo=timezone.utc),
        source_priority=("hko_hourly_accumulator",),
    )

    assert out.coverage_status == COVERAGE_NONE
    assert out.row_count == 0


def test_reader_accepts_hko_runtime_monitoring_rows_without_training():
    conn = _make_conn()
    _insert(
        conn,
        city="Hong Kong",
        target_date="2026-06-26",
        source="hko_hourly_accumulator",
        timezone_name="Asia/Hong_Kong",
        local_hour=7.0,
        local_timestamp="2026-06-26T07:00:00+08:00",
        utc_timestamp="2026-06-25T23:00:00+00:00",
        utc_offset_minutes=480,
        temp_current=27.0,
        running_max=27.0,
        running_min=27.0,
        authority="ICAO_STATION_NATIVE",
        training_allowed=0,
        causality_status="OK",
        source_role="runtime_monitoring",
        station_id="HKO",
        provenance_json=_HKO_OFFICIAL_PROVENANCE,
    )

    out = read_day0_observed_extrema(
        conn,
        city="Hong Kong",
        target_date="2026-06-26",
        timezone_name="Asia/Hong_Kong",
        decision_time_utc=datetime(2026, 6, 26, 1, 0, tzinfo=timezone.utc),
        source_priority=("hko_hourly_accumulator",),
    )

    assert out.coverage_status == COVERAGE_LOW
    assert out.row_count == 1
    assert out.low_so_far == 27.0


def test_reader_rejects_hko_current_temperature_pseudo_extrema():
    conn = _make_conn()
    _insert(
        conn,
        city="Hong Kong",
        target_date="2026-07-13",
        source="hko_hourly_accumulator",
        timezone_name="Asia/Hong_Kong",
        utc_timestamp="2026-07-13T06:00:00+00:00",
        temp_current=34.0,
        running_max=34.0,
        running_min=34.0,
        authority="ICAO_STATION_NATIVE",
        training_allowed=0,
        causality_status="OK",
        source_role="runtime_monitoring",
        station_id="HKO",
        provenance_json='{"payload_scope":"hko_current_temperature"}',
    )

    out = read_day0_observed_extrema(
        conn,
        city="Hong Kong",
        target_date="2026-07-13",
        timezone_name="Asia/Hong_Kong",
        decision_time_utc=datetime(2026, 7, 13, 7, 0, tzinfo=timezone.utc),
        source_priority=("hko_hourly_accumulator",),
    )

    assert out.coverage_status == COVERAGE_NONE
    assert out.chosen_source is None
    assert out.high_so_far is None


def test_reader_uses_only_official_hko_extrema_when_legacy_rows_are_mixed():
    conn = _make_conn()
    common = {
        "city": "Hong Kong",
        "target_date": "2026-07-13",
        "source": "hko_hourly_accumulator",
        "timezone_name": "Asia/Hong_Kong",
        "authority": "ICAO_STATION_NATIVE",
        "training_allowed": 0,
        "causality_status": "OK",
        "source_role": "runtime_monitoring",
        "station_id": "HKO",
    }
    _insert(
        conn,
        **common,
        utc_timestamp="2026-07-13T06:00:00+00:00",
        temp_current=34.0,
        running_max=34.0,
        running_min=34.0,
        provenance_json='{"payload_scope":"hko_current_temperature"}',
    )
    _insert(
        conn,
        **common,
        utc_timestamp="2026-07-13T06:01:00+00:00",
        temp_current=33.0,
        running_max=33.0,
        running_min=29.0,
        provenance_json=_HKO_OFFICIAL_PROVENANCE,
    )

    out = read_day0_observed_extrema(
        conn,
        city="Hong Kong",
        target_date="2026-07-13",
        timezone_name="Asia/Hong_Kong",
        decision_time_utc=datetime(2026, 7, 13, 7, 0, tzinfo=timezone.utc),
        source_priority=("hko_hourly_accumulator",),
    )

    assert out.row_count == 1
    assert out.high_so_far == 33.0
    assert out.low_so_far == 29.0
    assert out.current_temp == 33.0


def test_context_reader_builds_executable_wu_context_without_temp_current():
    conn = _make_conn()
    for hour, running_max, running_min in (
        (0, 14.0, 11.0),
        (1, 15.0, 10.0),
        (2, 13.0, 12.0),
        (3, 12.0, 12.0),
        (4, 11.0, 11.0),
        (5, 10.0, 10.0),
    ):
        _insert(
            conn,
            city="Buenos Aires",
            target_date="2026-07-01",
            source="wu_icao_history",
            timezone_name="America/Argentina/Buenos_Aires",
            local_hour=float(hour),
            local_timestamp=f"2026-07-01T{hour:02d}:00:00-03:00",
            utc_timestamp=f"2026-07-01T{hour + 3:02d}:00:00+00:00",
            running_max=running_max,
            running_min=running_min,
            temp_current=None,
            station_id="SAEZ",
            imported_at=f"2026-07-01T{hour + 3:02d}:11:00+00:00",
            authority="VERIFIED",
            training_allowed=1,
            causality_status="OK",
            source_role="historical_hourly",
        )

    class CityLike:
        name = "Buenos Aires"
        timezone = "America/Argentina/Buenos_Aires"
        settlement_unit = "C"
        settlement_source_type = "wu_icao"
        wu_station = "SAEZ"

    obs = read_day0_observation_context_from_instants(
        conn,
        city=CityLike(),
        target_date="2026-07-01",
        decision_time_utc=datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
    )

    assert obs is not None
    assert obs.source == "wu_icao_history"
    assert obs.station_id == "SAEZ"
    assert obs.coverage_status == COVERAGE_OK
    assert obs.high_so_far == 15.0
    assert obs.low_so_far == 10.0
    assert obs.current_temp == 10.0
    assert obs.observation_available_at == "2026-07-01T08:11:00+00:00"
    assert obs.provider_reported_time == "canonical_observation_instants"
    assert obs.source_role == "historical_hourly"
    assert obs.source_authority == "VERIFIED"
    assert obs.data_version == "v1.wu-native"
    assert obs.training_allowed is True
    assert obs.causality_status == "OK"


def test_context_reader_fallback_schema_keeps_provenance_fields_aligned():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            timezone_name TEXT NOT NULL,
            local_hour REAL,
            local_timestamp TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            temp_current REAL,
            running_max REAL,
            running_min REAL,
            authority TEXT NOT NULL,
            data_version TEXT NOT NULL,
            training_allowed INTEGER DEFAULT 1,
            causality_status TEXT DEFAULT 'OK',
            source_role TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    for hour in range(6):
        conn.execute(
            """
            INSERT INTO observation_instants (
                city, target_date, source, timezone_name, local_hour,
                local_timestamp, utc_timestamp, temp_current, running_max, running_min,
                authority, data_version, training_allowed, causality_status, source_role,
                provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Hong Kong",
                "2026-07-01",
                "hko_hourly_accumulator",
                "Asia/Hong_Kong",
                float(hour),
                f"2026-07-01T{hour:02d}:00:00+08:00",
                f"2026-06-30T{16 + hour:02d}:00:00+00:00",
                27.0 + hour,
                27.0 + hour,
                27.0,
                "ICAO_STATION_NATIVE",
                "v1.hko-native",
                0,
                "OK",
                "runtime_monitoring",
                _HKO_OFFICIAL_PROVENANCE,
            ),
        )
    conn.commit()

    class CityLike:
        name = "Hong Kong"
        timezone = "Asia/Hong_Kong"
        settlement_unit = "C"
        settlement_source_type = "hko"
        wu_station = "HKO"

    obs = read_day0_observation_context_from_instants(
        conn,
        city=CityLike(),
        target_date="2026-07-01",
        decision_time_utc=datetime(2026, 6, 30, 23, 0, tzinfo=timezone.utc),
    )

    assert obs is not None
    assert obs.source == "hko_hourly_accumulator"
    assert obs.station_id == ""
    assert obs.unit == "C"
    assert obs.observation_available_at == "2026-06-30T23:00:00+00:00"
    assert obs.source_role == "runtime_monitoring"
    assert obs.source_authority == "ICAO_STATION_NATIVE"
    assert obs.data_version == "v1.hko-native"
    assert obs.training_allowed is False
    assert obs.causality_status == "OK"
    assert obs.current_temp == 32.0


# ---------------------------------------------------------------------------
# Core Root C regression: MAX aggregation beats latest-row
# ---------------------------------------------------------------------------

class TestHighSoFarMaxAggregation:
    """Root C antibody: high_so_far = MAX(running_max), not latest row."""

    @pytest.fixture
    def amsterdam_conn(self) -> sqlite3.Connection:
        """Amsterdam 2026-05-22 with peak at 15:00 local (13:00 UTC)."""
        conn = _make_conn()
        # Row 1: 15:00 local / 13:00 UTC — the daily peak
        _insert(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            source="wu_icao_history",
            local_hour=15.0,
            local_timestamp="2026-05-22T15:00:00+02:00",
            utc_timestamp="2026-05-22T13:00:00+00:00",
            running_max=25.0,
            running_min=14.0,
            temp_current=25.0,
            authority="VERIFIED",
        )
        # Row 2: 23:00 local / 21:00 UTC — temperature dropped
        _insert(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            source="wu_icao_history",
            local_hour=23.0,
            local_timestamp="2026-05-22T23:00:00+02:00",
            utc_timestamp="2026-05-22T21:00:00+00:00",
            running_max=17.0,
            running_min=13.0,
            temp_current=17.0,
            authority="VERIFIED",
        )
        return conn

    def test_high_so_far_is_max_not_latest(self, amsterdam_conn: sqlite3.Connection) -> None:
        """high_so_far must equal 25.0 (peak at 15:00), not 17.0 (latest at 23:00)."""
        result = read_day0_observed_extrema(
            amsterdam_conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        # Primary assertion: correct semantics
        assert result.high_so_far == 25.0, (
            f"high_so_far should be 25.0 (peak row), got {result.high_so_far}. "
            "Naive latest-row reads would return 17.0 — Root C regression."
        )

    def test_naive_latest_row_would_be_wrong(self, amsterdam_conn: sqlite3.Connection) -> None:
        """Document that 17.0 is the wrong (naive) answer — for clarity."""
        result = read_day0_observed_extrema(
            amsterdam_conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        # Explicitly assert the naive-reader answer is NOT returned.
        assert result.high_so_far != 17.0, (
            "17.0 is the latest row's running_max (the naive wrong answer). "
            "Root C bug is present."
        )

    def test_low_so_far_is_min_not_latest(self, amsterdam_conn: sqlite3.Connection) -> None:
        """low_so_far must equal 13.0 (lowest of 14.0 and 13.0)."""
        result = read_day0_observed_extrema(
            amsterdam_conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        assert result.low_so_far == 13.0

    def test_coverage_status_low_with_two_rows(self, amsterdam_conn: sqlite3.Connection) -> None:
        """2 rows < 6 threshold → LOW_COVERAGE."""
        result = read_day0_observed_extrema(
            amsterdam_conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        assert result.coverage_status == COVERAGE_LOW
        assert result.row_count == 2


# ---------------------------------------------------------------------------
# Decision-time cutoff: rows AFTER decision_time_utc are excluded
# ---------------------------------------------------------------------------

class TestDecisionTimeCutoff:
    def test_rows_after_decision_time_excluded(self) -> None:
        """A row at 21:00 UTC must not appear when decision_time is 14:00 UTC."""
        conn = _make_conn()
        # Row before decision: 13:00 UTC, running_max=25.0
        _insert(
            conn,
            utc_timestamp="2026-05-22T13:00:00+00:00",
            running_max=25.0,
            authority="VERIFIED",
        )
        # Row after decision: 21:00 UTC, running_max=99.0
        _insert(
            conn,
            utc_timestamp="2026-05-22T21:00:00+00:00",
            running_max=99.0,
            authority="VERIFIED",
        )
        result = read_day0_observed_extrema(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 14, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        assert result.high_so_far == 25.0
        assert result.row_count == 1


# ---------------------------------------------------------------------------
# Authority filter: only VERIFIED and ICAO_STATION_NATIVE rows count
# ---------------------------------------------------------------------------

class TestAuthorityFilter:
    def test_unverified_rows_excluded(self) -> None:
        conn = _make_conn()
        _insert(
            conn,
            utc_timestamp="2026-05-22T13:00:00+00:00",
            running_max=30.0,
            authority="UNVERIFIED",
        )
        _insert(
            conn,
            utc_timestamp="2026-05-22T14:00:00+00:00",
            running_max=20.0,
            authority="VERIFIED",
        )
        result = read_day0_observed_extrema(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        # UNVERIFIED row with running_max=30.0 must be excluded
        assert result.high_so_far == 20.0

    def test_icao_station_native_rows_included(self) -> None:
        conn = _make_conn()
        _insert(
            conn,
            utc_timestamp="2026-05-22T13:00:00+00:00",
            running_max=28.0,
            authority="ICAO_STATION_NATIVE",
        )
        result = read_day0_observed_extrema(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        assert result.high_so_far == 28.0


# ---------------------------------------------------------------------------
# Source priority: first source with rows wins; never mix sources
# ---------------------------------------------------------------------------

class TestSourcePriority:
    def test_preferred_source_used_when_available(self) -> None:
        conn = _make_conn()
        # wu_icao_history row
        _insert(
            conn,
            source="wu_icao_history",
            utc_timestamp="2026-05-22T13:00:00+00:00",
            running_max=25.0,
            authority="VERIFIED",
        )
        # fallback source row with higher max — must NOT be used
        _insert(
            conn,
            source="ogimet_metar_eham",
            utc_timestamp="2026-05-22T14:00:00+00:00",
            running_max=35.0,
            authority="VERIFIED",
        )
        result = read_day0_observed_extrema(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history", "ogimet_metar_eham"),
        )
        assert result.chosen_source == "wu_icao_history"
        assert result.high_so_far == 25.0  # NOT 35.0 from the fallback source

    def test_fallback_source_used_when_primary_absent(self) -> None:
        conn = _make_conn()
        # Only fallback source rows present
        _insert(
            conn,
            source="ogimet_metar_eham",
            utc_timestamp="2026-05-22T13:00:00+00:00",
            running_max=22.0,
            authority="VERIFIED",
        )
        result = read_day0_observed_extrema(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history", "ogimet_metar_eham"),
        )
        assert result.chosen_source == "ogimet_metar_eham"
        assert result.high_so_far == 22.0


# ---------------------------------------------------------------------------
# No-data / coverage states
# ---------------------------------------------------------------------------

class TestCoverageStates:
    def test_no_data(self) -> None:
        conn = _make_conn()
        result = read_day0_observed_extrema(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        assert result.coverage_status == COVERAGE_NONE
        assert result.high_so_far is None
        assert result.low_so_far is None
        assert result.chosen_source is None
        assert result.row_count == 0

    def test_ok_coverage_with_six_rows(self) -> None:
        conn = _make_conn()
        for hour in range(6):
            _insert(
                conn,
                utc_timestamp=f"2026-05-22T{hour:02d}:00:00+00:00",
                running_max=20.0 + hour,
                authority="VERIFIED",
            )
        result = read_day0_observed_extrema(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        assert result.coverage_status == COVERAGE_OK
        assert result.row_count == 6
        assert result.high_so_far == 25.0  # max of 20..25


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_provenance_running_max_semantics_field(self) -> None:
        """Provenance must record hour_bucket_max_aggregated_by_MAX."""
        conn = _make_conn()
        _insert(
            conn,
            utc_timestamp="2026-05-22T13:00:00+00:00",
            running_max=25.0,
            authority="VERIFIED",
        )
        result = read_day0_observed_extrema(
            conn,
            city="Amsterdam",
            target_date="2026-05-22",
            timezone_name="Europe/Amsterdam",
            decision_time_utc=datetime(2026, 5, 22, 22, 0, tzinfo=timezone.utc),
            source_priority=("wu_icao_history",),
        )
        assert result.provenance["running_max_semantics"] == "hour_bucket_max_aggregated_by_MAX"


# ---------------------------------------------------------------------------
# Naive datetime raises ValueError
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_naive_decision_time_raises(self) -> None:
        conn = _make_conn()
        naive_dt = datetime(2026, 5, 22, 22, 0)  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            read_day0_observed_extrema(
                conn,
                city="Amsterdam",
                target_date="2026-05-22",
                timezone_name="Europe/Amsterdam",
                decision_time_utc=naive_dt,
                source_priority=("wu_icao_history",),
            )
