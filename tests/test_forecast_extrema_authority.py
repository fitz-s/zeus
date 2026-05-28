# Created: 2026-05-22
# Last reused/audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-A
# Lifecycle: created=2026-05-22; last_reviewed=2026-05-22; last_reused=never
# Purpose: Unit + integration tests for ForecastExtremaAuthority classifier and reader ORDER BY ranking.
# Reuse: Run when classify_forecast_extrema_authority, POSITIVE_ATTRIBUTION_STATUSES, or _snapshot_query_sql ORDER BY changes.
"""Tests for ForecastExtremaAuthority classifier and reader ORDER BY preference."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.executable_forecast_reader import read_executable_forecast_snapshot
from src.data.forecast_extrema_authority import (
    ForecastExtremaEligibility,
    classify_forecast_extrema_authority,
)
from src.data.forecast_target_contract import build_forecast_target_scope
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema


UTC_SUFFIX = "+00:00"

_TARGET_DATE = date(2026, 5, 22)
_TAIPEI_CITY = "Taipei"
_AMSTERDAM_CITY = "Amsterdam"
_WINDOW_START = "2026-05-21T16:00:00+00:00"  # Taipei UTC day-start


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _scope(city: str = _TAIPEI_CITY):
    return build_forecast_target_scope(
        city_id=city.upper(),
        city_name=city,
        city_timezone="Asia/Taipei",
        target_local_date=_TARGET_DATE,
        temperature_metric="high",
        source_cycle_time=__import__("datetime").datetime(2026, 5, 22, 0, 0, tzinfo=__import__("datetime").timezone.utc),
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        market_refs=("cond-test-1",),
    )


def _insert_snapshot_row(
    conn: sqlite3.Connection,
    *,
    city: str,
    source_cycle_time: str,
    available_at: str,
    contributes_to_target_extrema: int | None,
    forecast_window_attribution_status: str | None,
    members_values: list[float],
    source_run_id: str = "run-1",
    boundary_ambiguous: int = 0,
    causality_status: str = "OK",
    authority: str = "VERIFIED",
    data_version: str | None = None,
) -> None:
    scope = _scope(city)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, valid_time, available_at, fetch_time,
            lead_hours, members_json, model_version, data_version,
            source_id, source_transport, source_run_id, release_calendar_key,
            source_cycle_time, source_release_time, source_available_at,
            training_allowed, causality_status, boundary_ambiguous,
            ambiguous_member_count, manifest_hash, provenance_json, authority,
            members_unit, local_day_start_utc, step_horizon_hours,
            contributes_to_target_extrema, forecast_window_attribution_status
        ) VALUES (
            :city, :target_date, :temperature_metric, :physical_quantity,
            :observation_field, :issue_time, :valid_time, :available_at, :fetch_time,
            :lead_hours, :members_json, :model_version, :data_version,
            :source_id, :source_transport, :source_run_id, :release_calendar_key,
            :source_cycle_time, :source_release_time, :source_available_at,
            :training_allowed, :causality_status, :boundary_ambiguous,
            :ambiguous_member_count, :manifest_hash, :provenance_json, :authority,
            :members_unit, :local_day_start_utc, :step_horizon_hours,
            :contributes_to_target_extrema, :forecast_window_attribution_status
        )
        """,
        {
            "city": city,
            "target_date": _TARGET_DATE.isoformat(),
            "temperature_metric": "high",
            "physical_quantity": "mx2t6_local_calendar_day_max",
            "observation_field": "high_temp",
            "issue_time": source_cycle_time,
            "valid_time": _TARGET_DATE.isoformat(),
            "available_at": available_at,
            "fetch_time": available_at,
            "lead_hours": 144.0,
            "members_json": json.dumps(members_values),
            "model_version": "ecmwf_ens",
            "data_version": data_version or scope.data_version,
            "source_id": "ecmwf_open_data",
            "source_transport": "ensemble_snapshots_db_reader",
            "source_run_id": source_run_id,
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "source_cycle_time": source_cycle_time,
            "source_release_time": source_cycle_time,
            "source_available_at": available_at,
            "training_allowed": 1,
            "causality_status": causality_status,
            "boundary_ambiguous": boundary_ambiguous,
            "ambiguous_member_count": 0,
            "manifest_hash": "a" * 64,
            "provenance_json": "{}",
            "authority": authority,
            "members_unit": "degC",
            "local_day_start_utc": _WINDOW_START,
            "step_horizon_hours": 144.0,
            "contributes_to_target_extrema": contributes_to_target_extrema,
            "forecast_window_attribution_status": forecast_window_attribution_status,
        },
    )


# ---------------------------------------------------------------------------
# Unit tests: classify_forecast_extrema_authority
# ---------------------------------------------------------------------------

class TestClassifyForecastExtremaAuthority:
    def test_full_contributor(self):
        row = {
            "contributes_to_target_extrema": 1,
            "forecast_window_attribution_status": "EXPLICIT",
            "boundary_ambiguous": 0,
        }
        auth = classify_forecast_extrema_authority(row)
        assert auth.eligibility == ForecastExtremaEligibility.FULL_CONTRIBUTOR
        assert auth.contributes_to_target_extrema is True
        assert auth.boundary_ambiguous is False

    def test_full_contributor_all_positive_statuses(self):
        for status in ("VERIFIED", "OK", "CONTRIBUTES", "FULLY_INSIDE_TARGET_LOCAL_DAY"):
            row = {
                "contributes_to_target_extrema": 1,
                "forecast_window_attribution_status": status,
                "boundary_ambiguous": 0,
            }
            auth = classify_forecast_extrema_authority(row)
            assert auth.eligibility == ForecastExtremaEligibility.FULL_CONTRIBUTOR, status

    def test_boundary_ambiguous_is_non_contributor_fail_closed(self):
        # review5.23 P1-4: boundary_ambiguous=1 is fail-closed NON_CONTRIBUTOR,
        # not PARTIAL_CONTRIBUTOR.  The snapshot reader independently blocks these
        # rows with EXECUTABLE_FORECAST_CAUSALITY_NOT_OK; both policies now agree.
        row = {
            "contributes_to_target_extrema": 1,
            "forecast_window_attribution_status": "EXPLICIT",
            "boundary_ambiguous": 1,
        }
        auth = classify_forecast_extrema_authority(row)
        assert auth.eligibility == ForecastExtremaEligibility.NON_CONTRIBUTOR
        assert auth.boundary_ambiguous is True
        assert auth.contributes_to_target_extrema is False

    def test_non_contributor_explicit_zero(self):
        row = {
            "contributes_to_target_extrema": 0,
            "forecast_window_attribution_status": "UNKNOWN",
            "boundary_ambiguous": 0,
        }
        auth = classify_forecast_extrema_authority(row)
        assert auth.eligibility == ForecastExtremaEligibility.NON_CONTRIBUTOR
        assert auth.contributes_to_target_extrema is False

    def test_current_version_contributes_none_is_unknown_fail_closed(self):
        # P0 follow-up §2: NULL contribution on a CURRENT data_version fails closed.
        row = {
            "contributes_to_target_extrema": None,
            "forecast_window_attribution_status": None,
            "boundary_ambiguous": 0,
            "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
        }
        auth = classify_forecast_extrema_authority(row)
        assert auth.eligibility == ForecastExtremaEligibility.UNKNOWN

    def test_legacy_version_contributes_none_is_passthrough(self):
        # P0 follow-up §2: NULL contribution on a LEGACY data_version passes through.
        row = {
            "contributes_to_target_extrema": None,
            "forecast_window_attribution_status": None,
            "boundary_ambiguous": 0,
            "data_version": "ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
        }
        auth = classify_forecast_extrema_authority(row)
        assert auth.eligibility == ForecastExtremaEligibility.LEGACY_NULL_PASSTHROUGH

    def test_missing_data_version_is_unknown_fail_closed(self):
        # p0-2-hardening: missing data_version (empty row / lookup failure) must
        # fail-closed as UNKNOWN, NOT pass through as LEGACY_NULL_PASSTHROUGH.
        # The earlier passthrough for None was the hidden hole this fix seals:
        # _snapshot_row_for_classification returned {} on DB miss → data_version=None
        # → silently treated as legacy passthrough, bypassing the P0 gate.
        row = {
            "contributes_to_target_extrema": None,
            "forecast_window_attribution_status": None,
            "boundary_ambiguous": 0,
        }
        auth = classify_forecast_extrema_authority(row)
        assert auth.eligibility == ForecastExtremaEligibility.UNKNOWN

    def test_unknown_contributes_1_attribution_unknown(self):
        row = {
            "contributes_to_target_extrema": 1,
            "forecast_window_attribution_status": "UNKNOWN",
            "boundary_ambiguous": 0,
        }
        auth = classify_forecast_extrema_authority(row)
        assert auth.eligibility == ForecastExtremaEligibility.UNKNOWN

    def test_unknown_contributes_1_attribution_missing(self):
        row = {
            "contributes_to_target_extrema": 1,
            "forecast_window_attribution_status": None,
            "boundary_ambiguous": 0,
        }
        auth = classify_forecast_extrema_authority(row)
        assert auth.eligibility == ForecastExtremaEligibility.UNKNOWN

    def test_short_alias_attribution_status(self):
        # Falls back to 'attribution_status' key when DB column absent.
        row = {
            "contributes_to_target_extrema": 1,
            "attribution_status": "OK",
            "boundary_ambiguous": 0,
        }
        auth = classify_forecast_extrema_authority(row)
        assert auth.eligibility == ForecastExtremaEligibility.FULL_CONTRIBUTOR


# ---------------------------------------------------------------------------
# Integration tests: reader ORDER BY preference via in-memory SQLite
# ---------------------------------------------------------------------------

class TestReaderExtremaPreference:
    """Taipei-style test: 00Z contributes=1 runs warm; 12Z contributes=0 runs cold.

    The reader must select the 00Z run even though 12Z is the *later* cycle.
    """

    def test_taipei_prefers_contributing_00z_over_later_noncontributing_12z(self):
        conn = _conn()
        _insert_snapshot_row(
            conn,
            city=_TAIPEI_CITY,
            source_cycle_time="2026-05-22T00:00:00+00:00",
            available_at="2026-05-22T07:00:00+00:00",
            contributes_to_target_extrema=1,
            forecast_window_attribution_status="EXPLICIT",
            members_values=[33.0 + i * 0.03 for i in range(33)],
            source_run_id="run-taipei",
        )
        _insert_snapshot_row(
            conn,
            city=_TAIPEI_CITY,
            source_cycle_time="2026-05-22T12:00:00+00:00",
            available_at="2026-05-22T19:00:00+00:00",
            contributes_to_target_extrema=0,
            forecast_window_attribution_status="UNKNOWN",
            members_values=[27.0 + i * 0.03 for i in range(27)],
            source_run_id="run-taipei",
        )
        scope = _scope(_TAIPEI_CITY)
        # Pass source_run_id so the correct-table branch fires.
        result = read_executable_forecast_snapshot(
            conn,
            scope=scope,
            source_id="ecmwf_open_data",
            source_transport="ensemble_snapshots_db_reader",
            source_run_id="run-taipei",
        )
        assert result.ok, f"Expected LIVE_ELIGIBLE, got {result.status}/{result.reason_code}"
        assert result.snapshot is not None
        # Must have selected the warm 00Z row (~33 members, mean near 33.5).
        assert result.snapshot.source_cycle_time == "2026-05-22T00:00:00+00:00", (
            f"Expected 00Z cycle, got {result.snapshot.source_cycle_time}"
        )
        assert len(result.snapshot.members) == 33

    def test_non_contributing_only_row_is_blocked(self):
        conn = _conn()
        _insert_snapshot_row(
            conn,
            city=_TAIPEI_CITY,
            source_cycle_time="2026-05-22T12:00:00+00:00",
            available_at="2026-05-22T19:00:00+00:00",
            contributes_to_target_extrema=0,
            forecast_window_attribution_status="UNKNOWN",
            members_values=[27.0 + i * 0.03 for i in range(27)],
            source_run_id="run-taipei-nc",
        )
        scope = _scope(_TAIPEI_CITY)
        result = read_executable_forecast_snapshot(
            conn,
            scope=scope,
            source_id="ecmwf_open_data",
            source_transport="ensemble_snapshots_db_reader",
            source_run_id="run-taipei-nc",
        )
        assert not result.ok
        assert result.reason_code == "EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA"

    def test_contributes1_unknown_attribution_is_blocked(self):
        """contributes=1 but attribution_status=UNKNOWN: explicit bad determination blocks."""
        conn = _conn()
        _insert_snapshot_row(
            conn,
            city=_TAIPEI_CITY,
            source_cycle_time="2026-05-22T12:00:00+00:00",
            available_at="2026-05-22T19:00:00+00:00",
            contributes_to_target_extrema=1,
            forecast_window_attribution_status="UNKNOWN",
            members_values=[28.0 + i * 0.05 for i in range(30)],
            source_run_id="run-taipei-unk",
        )
        scope = _scope(_TAIPEI_CITY)
        result = read_executable_forecast_snapshot(
            conn,
            scope=scope,
            source_id="ecmwf_open_data",
            source_transport="ensemble_snapshots_db_reader",
            source_run_id="run-taipei-unk",
        )
        assert not result.ok
        assert result.reason_code == "EXECUTABLE_FORECAST_EXTREMA_AUTHORITY_UNKNOWN"

    def test_current_version_null_contributes_blocks_fail_closed(self):
        """P0 follow-up §2: NULL contributes on a CURRENT data_version now fails
        closed (was passthrough). A live mx2t3 row with missing provenance must
        not enter the trade chain."""
        conn = _conn()
        _insert_snapshot_row(
            conn,
            city=_TAIPEI_CITY,
            source_cycle_time="2026-05-22T00:00:00+00:00",
            available_at="2026-05-22T07:00:00+00:00",
            contributes_to_target_extrema=None,
            forecast_window_attribution_status=None,
            members_values=[30.0 + i * 0.1 for i in range(51)],
            source_run_id="run-taipei-current-null",
        )
        scope = _scope(_TAIPEI_CITY)  # data_version = ECMWF_OPENDATA_HIGH_DATA_VERSION (current)
        result = read_executable_forecast_snapshot(
            conn,
            scope=scope,
            source_id="ecmwf_open_data",
            source_transport="ensemble_snapshots_db_reader",
            source_run_id="run-taipei-current-null",
        )
        assert not result.ok
        assert result.reason_code == "EXECUTABLE_FORECAST_EXTREMA_AUTHORITY_UNKNOWN"

    def test_legacy_version_null_contributes_passthrough(self):
        """P0 follow-up §2: NULL contributes on a LEGACY data_version still passes
        through (historical rows remain readable)."""
        import dataclasses

        legacy_version = "ecmwf_opendata_mx2t6_local_calendar_day_max_v1"
        conn = _conn()
        _insert_snapshot_row(
            conn,
            city=_TAIPEI_CITY,
            source_cycle_time="2026-05-22T00:00:00+00:00",
            available_at="2026-05-22T07:00:00+00:00",
            contributes_to_target_extrema=None,
            forecast_window_attribution_status=None,
            members_values=[30.0 + i * 0.1 for i in range(51)],
            source_run_id="run-taipei-legacy",
            data_version=legacy_version,
        )
        scope = dataclasses.replace(_scope(_TAIPEI_CITY), data_version=legacy_version)
        result = read_executable_forecast_snapshot(
            conn,
            scope=scope,
            source_id="ecmwf_open_data",
            source_transport="ensemble_snapshots_db_reader",
            source_run_id="run-taipei-legacy",
        )
        assert result.ok, f"Legacy NULL row should pass: {result.reason_code}"

    def test_amsterdam_keeps_latest_when_latest_contributes(self):
        """Amsterdam-style: latest cycle also contributes=1 — must still be selected."""
        conn = _conn()
        # Earlier contributing row (00Z).
        _insert_snapshot_row(
            conn,
            city=_AMSTERDAM_CITY,
            source_cycle_time="2026-05-22T00:00:00+00:00",
            available_at="2026-05-22T07:00:00+00:00",
            contributes_to_target_extrema=1,
            forecast_window_attribution_status="EXPLICIT",
            members_values=[18.0 + i * 0.1 for i in range(51)],
            source_run_id="run-ams",
        )
        # Later contributing row (12Z) — same run_id, later cycle.
        _insert_snapshot_row(
            conn,
            city=_AMSTERDAM_CITY,
            source_cycle_time="2026-05-22T12:00:00+00:00",
            available_at="2026-05-22T19:00:00+00:00",
            contributes_to_target_extrema=1,
            forecast_window_attribution_status="EXPLICIT",
            members_values=[20.0 + i * 0.1 for i in range(51)],
            source_run_id="run-ams",
        )
        scope = build_forecast_target_scope(
            city_id="AMSTERDAM",
            city_name=_AMSTERDAM_CITY,
            city_timezone="Europe/Amsterdam",
            target_local_date=_TARGET_DATE,
            temperature_metric="high",
            source_cycle_time=__import__("datetime").datetime(2026, 5, 22, 0, 0, tzinfo=__import__("datetime").timezone.utc),
            data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
            market_refs=("cond-ams-1",),
        )
        result = read_executable_forecast_snapshot(
            conn,
            scope=scope,
            source_id="ecmwf_open_data",
            source_transport="ensemble_snapshots_db_reader",
            source_run_id="run-ams",
        )
        assert result.ok, f"Expected LIVE_ELIGIBLE, got {result.status}/{result.reason_code}"
        assert result.snapshot is not None
        # Both contribute, so tiebreaker is source_cycle_time DESC → 12Z wins.
        assert result.snapshot.source_cycle_time == "2026-05-22T12:00:00+00:00", (
            f"Expected 12Z (latest), got {result.snapshot.source_cycle_time}"
        )
