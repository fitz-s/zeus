# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=2026-05-19
# Purpose: Antibody tests for ensemble member statistical floor
# Reuse: pytest tests/test_executable_forecast_reader_member_floor.py
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: 2026-05-19 source_run_coverage analysis — ~250 rows partial-coverage at 40-50 valid members, statistically sufficient
"""Antibody tests for the ensemble member statistical-sufficiency floor.

Problem: ECMWF Open Data routinely delivers 48-50 members out of 51.
Strict gate (observed >= expected) blocked ~40-45% of forecasts daily.

Fix: settings["ensemble"]["min_members_floor"] = 40 replaces strict equality.
     observed >= floor (40) is LIVE_ELIGIBLE; observed < floor is BLOCKED.

Each test is named for the invariant it antibodies.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.executable_forecast_reader import read_executable_forecast
from src.data.forecast_target_contract import build_forecast_target_scope
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.db import init_schema, init_schema_forecasts
from src.state.readiness_repo import write_readiness_state
from src.state.schema.v2_schema import apply_v2_schema
from src.state.source_run_coverage_repo import write_source_run_coverage
from src.state.source_run_repo import write_source_run

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _scope():
    return build_forecast_target_scope(
        city_id="LONDON",
        city_name="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_cycle_time=_utc(2026, 5, 3),
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        market_refs=("condition-floor-test",),
    )


def _insert_snapshot(conn: sqlite3.Connection, *, n_members: int = 51) -> None:
    scope = _scope()
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, valid_time, available_at, fetch_time,
            lead_hours, members_json, model_version, data_version,
            source_id, source_transport, source_run_id, release_calendar_key,
            source_cycle_time, source_release_time, source_available_at,
            training_allowed, causality_status, boundary_ambiguous,
            ambiguous_member_count, manifest_hash, provenance_json, authority,
            members_unit, local_day_start_utc, step_horizon_hours
        ) VALUES (
            :city, :target_date, :temperature_metric, :physical_quantity,
            :observation_field, :issue_time, :valid_time, :available_at, :fetch_time,
            :lead_hours, :members_json, :model_version, :data_version,
            :source_id, :source_transport, :source_run_id, :release_calendar_key,
            :source_cycle_time, :source_release_time, :source_available_at,
            :training_allowed, :causality_status, :boundary_ambiguous,
            :ambiguous_member_count, :manifest_hash, :provenance_json, :authority,
            :members_unit, :local_day_start_utc, :step_horizon_hours
        )
        """,
        {
            "city": scope.city_name,
            "target_date": scope.target_local_date.isoformat(),
            "temperature_metric": scope.temperature_metric,
            "physical_quantity": "mx2t6_local_calendar_day_max",
            "observation_field": "high_temp",
            "issue_time": "2026-05-03T00:00:00+00:00",
            "valid_time": scope.target_local_date.isoformat(),
            "available_at": "2026-05-03T08:10:00+00:00",
            "fetch_time": "2026-05-03T08:15:00+00:00",
            "lead_hours": 120.0,
            "members_json": json.dumps([18.0 + i * 0.1 for i in range(n_members)]),
            "model_version": "ecmwf_ens",
            "data_version": scope.data_version,
            "source_id": "ecmwf_open_data",
            "source_transport": "ensemble_snapshots_v2_db_reader",
            "source_run_id": "source-run-floor-1",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "source_cycle_time": "2026-05-03T00:00:00+00:00",
            "source_release_time": "2026-05-03T08:05:00+00:00",
            "source_available_at": "2026-05-03T08:10:00+00:00",
            "training_allowed": 1,
            "causality_status": "OK",
            "boundary_ambiguous": 0,
            "ambiguous_member_count": 0,
            "manifest_hash": "c" * 64,
            "provenance_json": "{}",
            "authority": "VERIFIED",
            "members_unit": "degC",
            "local_day_start_utc": scope.target_window_start_utc.isoformat(),
            "step_horizon_hours": 144.0,
        },
    )


def _insert_source_run(
    conn: sqlite3.Connection,
    *,
    expected_members: int = 51,
    observed_members: int = 51,
) -> None:
    scope = _scope()
    write_source_run(
        conn,
        source_run_id="source-run-floor-1",
        source_id="ecmwf_open_data",
        track="mx2t6_high_full_horizon",
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
        source_cycle_time=_utc(2026, 5, 3),
        source_issue_time=_utc(2026, 5, 3),
        source_release_time=_utc(2026, 5, 3, 8, 5),
        source_available_at=_utc(2026, 5, 3, 8, 10),
        fetch_started_at=_utc(2026, 5, 3, 8, 10),
        fetch_finished_at=_utc(2026, 5, 3, 8, 15),
        captured_at=_utc(2026, 5, 3, 8, 20),
        imported_at=_utc(2026, 5, 3, 8, 30),
        target_local_date=scope.target_local_date,
        city_id=scope.city_id,
        city_timezone=scope.city_timezone,
        temperature_metric=scope.temperature_metric,
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version=scope.data_version,
        expected_members=expected_members,
        observed_members=observed_members,
        expected_steps_json=scope.required_step_hours,
        observed_steps_json=scope.required_step_hours,
        completeness_status="COMPLETE",
        status="SUCCESS",
        raw_payload_hash="a" * 64,
        manifest_hash="b" * 64,
    )


def _insert_coverage(
    conn: sqlite3.Connection,
    *,
    observed_members: int = 51,
    expected_members: int = 51,
) -> None:
    scope = _scope()
    write_source_run_coverage(
        conn,
        coverage_id="coverage-floor-1",
        source_run_id="source-run-floor-1",
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
        track="mx2t6_high_full_horizon",
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version=scope.data_version,
        expected_members=expected_members,
        observed_members=observed_members,
        expected_steps_json=scope.required_step_hours,
        observed_steps_json=list(scope.required_step_hours),
        snapshot_ids_json=[1],
        target_window_start_utc=scope.target_window_start_utc,
        target_window_end_utc=scope.target_window_end_utc,
        completeness_status="COMPLETE",
        readiness_status="LIVE_ELIGIBLE",
        computed_at=_utc(2026, 5, 3, 8, 45),
        expires_at=_utc(2026, 5, 3, 12),
    )


def _insert_readiness(
    conn: sqlite3.Connection,
    *,
    readiness_id: str = "readiness-floor-1",
    condition_id: str | None = None,
    coverage_id: str = "coverage-floor-1",
) -> None:
    scope = _scope()
    write_readiness_state(
        conn,
        readiness_id=readiness_id,
        scope_type="city_metric",
        status="LIVE_ELIGIBLE",
        computed_at=_utc(2026, 5, 3, 9),
        expires_at=_utc(2026, 5, 3, 12),
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version=scope.data_version,
        source_id="ecmwf_open_data",
        track="mx2t6_high_full_horizon",
        source_run_id="source-run-floor-1",
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        market_family=None,
        condition_id=condition_id,
        reason_codes_json=["READY"],
        dependency_json={"coverage_id": coverage_id},
    )


def _make_full_db(
    conn: sqlite3.Connection,
    *,
    observed_members: int = 51,
    expected_members: int = 51,
) -> None:
    """Insert a complete, LIVE_ELIGIBLE row with configurable member counts.

    Uses require_entry_readiness=False so only the producer readiness row is
    needed. The member floor test targets the coverage gate, not entry readiness.
    """
    _insert_snapshot(conn, n_members=observed_members)
    _insert_source_run(conn, expected_members=expected_members, observed_members=observed_members)
    _insert_coverage(conn, observed_members=observed_members, expected_members=expected_members)
    _insert_readiness(conn, readiness_id="readiness-floor-producer", condition_id=None)


def _call(conn: sqlite3.Connection, floor_config: dict) -> str:
    """Call read_executable_forecast with a patched ensemble config.

    floor_config is merged into {"primary_members": 51, ...} so tests
    control only the keys they care about.
    """
    scope = _scope()
    base_ensemble = {
        "primary": "ecmwf_ifs025",
        "crosscheck": "gfs025",
        "primary_members": 51,
        "crosscheck_members": 31,
        "n_mc": 10000,
        "instrument_noise_f": 0.5,
        "instrument_noise_c": 0.28,
        "bimodal_kde_order": 10,
        "bimodal_gap_ratio": 0.3,
        "boundary_window": 0.5,
        "unimodal_range_epsilon": 0.5,
        "conflict_kl_threshold": 0.15,
    }
    ensemble_cfg = {**base_ensemble, **floor_config}

    # settings["ensemble"] uses __getitem__; use MagicMock with side_effect
    mock_settings = MagicMock()
    mock_settings.__getitem__.side_effect = lambda key: ensemble_cfg if key == "ensemble" else {}
    with patch("src.data.executable_forecast_reader.settings", mock_settings):
        result = read_executable_forecast(
            conn,
            city_id=scope.city_id,
            city_name=scope.city_name,
            city_timezone=scope.city_timezone,
            target_local_date=scope.target_local_date,
            temperature_metric=scope.temperature_metric,
            source_id="ecmwf_open_data",
            source_transport="ensemble_snapshots_v2_db_reader",
            data_version=scope.data_version,
            track="mx2t6_high_full_horizon",
            strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
            market_family=None,
            condition_id="condition-floor-test",
            decision_time=_utc(2026, 5, 3, 9, 30),
            require_entry_readiness=False,
        )
    return result.status


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnsembleMemberFloor:
    """Antibody suite for statistical-sufficiency floor (min_members_floor=40)."""

    def test_strict_pass_full_members(self):
        """Rationale: Full 51/51 ensemble must remain LIVE_ELIGIBLE. No regression."""
        conn = _conn()
        _make_full_db(conn, observed_members=51, expected_members=51)
        assert _call(conn, {"min_members_floor": 40}) == "LIVE_ELIGIBLE"

    def test_partial_pass_at_floor_boundary(self):
        """Rationale: observed=40 equals floor → LIVE_ELIGIBLE.
        This was BLOCKED before the fix and is the primary category being unblocked."""
        conn = _conn()
        _make_full_db(conn, observed_members=40, expected_members=51)
        assert _call(conn, {"min_members_floor": 40}) == "LIVE_ELIGIBLE"

    def test_below_floor_remains_blocked(self):
        """Rationale: observed=39 < floor=40 → BLOCKED. Fail-closed preserved.
        Genuinely degraded ensembles (< 40 members) must not pass through."""
        conn = _conn()
        _make_full_db(conn, observed_members=39, expected_members=51)
        assert _call(conn, {"min_members_floor": 40}) == "BLOCKED"

    def test_default_behavior_when_floor_unset(self):
        """Rationale: When min_members_floor is absent from config, fallback is
        expected_members (strict). observed=50, expected=51, no floor → BLOCKED.
        Guarantees the fix is inert on configs that haven't adopted the new key."""
        conn = _conn()
        _make_full_db(conn, observed_members=50, expected_members=51)
        # No min_members_floor in ensemble config
        assert _call(conn, {}) == "BLOCKED"

    def test_zero_expected_members_blocked(self):
        """Rationale: expected_members=0 → BLOCKED regardless of floor.
        Guard against misconfigured coverage rows."""
        conn = _conn()
        _make_full_db(conn, observed_members=0, expected_members=0)
        assert _call(conn, {"min_members_floor": 40}) == "BLOCKED"

    def test_realistic_ecmwf_partial_48(self):
        """Rationale: observed=48, expected=51, floor=40 → LIVE_ELIGIBLE.
        48-member dissemination is the most common ECMWF Open Data partial case
        (observed 58 times in source_run_coverage). This test antibodies the
        specific day-to-day pattern that caused 40-45% systematic loss."""
        conn = _conn()
        _make_full_db(conn, observed_members=48, expected_members=51)
        assert _call(conn, {"min_members_floor": 40}) == "LIVE_ELIGIBLE"
