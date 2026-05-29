# Lifecycle: created=2026-05-23; last_reviewed=2026-05-23; last_reused=never
# Purpose: Relationship tests proving full-bundle-layer forecast selection prefers a
#   contributing 00Z bundle over a later non-contributing 12Z bundle. These are the
#   tests the production fix must satisfy: DISTINCT source_run_id / coverage_id per
#   cycle, but only ONE readiness_state row (the latest, pointing at the 12Z coverage)
#   — the real production shape (write_readiness_state UPSERTs on the scope tuple).
# Reuse: Run when _candidate_forecast_bundles, _bundle_rank, _evaluate_candidate, or
#   classify_forecast_extrema_authority changes. Authority: docs/operations/
#   task_2026-05-21_mainline_completion_authority/ and P0_FOLLOWUP_BUNDLE_LAYER_SPEC §1,§6.
"""Relationship tests for executable forecast bundle-layer selection (P0 follow-up).

The production bug (PR #309 trace, 2026-05-23): read_executable_forecast() resolved
ONE producer_readiness (latest) -> ONE coverage -> ONE source_run, locking the entire
bundle to whichever cycle was computed last. If the latest cycle was a post-peak 12Z
NON_CONTRIBUTOR, the contributor-first ORDER BY inside the snapshot SQL only reshuffled
snapshots WITHIN that 12Z run; the 00Z contributor bundle never entered the candidate
set. These tests fail against the single-path reader and pass once selection is lifted
to enumerate all eligible bundles and rank by extrema authority.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.executable_forecast_reader import read_executable_forecast
from src.data.forecast_target_contract import build_forecast_target_scope
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.db import init_schema
from src.state.readiness_repo import write_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema
from src.state.source_run_coverage_repo import write_source_run_coverage
from src.state.source_run_repo import write_source_run

UTC = timezone.utc

_TARGET_DATE = date(2026, 5, 8)
_CITY_ID = "LONDON"
_CITY = "London"
_CITY_TZ = "Europe/London"
_TRACK = "mx2t3_high_full_horizon"
_REL_KEY = "ecmwf_open_data:mx2t3_high:full"
_TRANSPORT = "ensemble_snapshots_db_reader"
_SOURCE_ID = "ecmwf_open_data"
_PHYSQ = "mx2t3_local_calendar_day_max"
_OBS_FIELD = "high_temp"
_CONDITION = "condition-123"
_FAMILY = "family-1"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _scope():
    return build_forecast_target_scope(
        city_id=_CITY_ID,
        city_name=_CITY,
        city_timezone=_CITY_TZ,
        target_local_date=_TARGET_DATE,
        temperature_metric="high",
        source_cycle_time=_utc(2026, 5, 3),
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        market_refs=(_CONDITION,),
    )


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    source_run_id: str,
    source_cycle_time: str,
    available_at: str,
    members_values: list[float],
    contributes_to_target_extrema: int | None,
    forecast_window_attribution_status: str | None,
    data_version: str = ECMWF_OPENDATA_HIGH_DATA_VERSION,
    boundary_ambiguous: int = 0,
    causality_status: str = "OK",
    authority: str = "VERIFIED",
) -> None:
    scope = _scope()
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, valid_time, available_at, fetch_time,
            lead_hours, members_json, model_version, dataset_id,
            source_id, source_transport, source_run_id, release_calendar_key,
            source_cycle_time, source_release_time, source_available_at,
            training_allowed, causality_status, boundary_ambiguous,
            ambiguous_member_count, manifest_hash, provenance_json, authority,
            members_unit, local_day_start_utc, step_horizon_hours,
            contributes_to_target_extrema, forecast_window_attribution_status
        ) VALUES (
            :snapshot_id, :city, :target_date, :temperature_metric, :physical_quantity,
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
            "snapshot_id": snapshot_id,
            "city": _CITY,
            "target_date": _TARGET_DATE.isoformat(),
            "temperature_metric": "high",
            "physical_quantity": _PHYSQ,
            "observation_field": _OBS_FIELD,
            "issue_time": source_cycle_time,
            "valid_time": _TARGET_DATE.isoformat(),
            "available_at": available_at,
            "fetch_time": available_at,
            "lead_hours": 120.0,
            "members_json": json.dumps(members_values),
            "model_version": "ecmwf_ens",
            "data_version": data_version,
            "source_id": _SOURCE_ID,
            "source_transport": _TRANSPORT,
            "source_run_id": source_run_id,
            "release_calendar_key": _REL_KEY,
            "source_cycle_time": source_cycle_time,
            "source_release_time": source_cycle_time,
            "source_available_at": available_at,
            "training_allowed": 1,
            "causality_status": causality_status,
            "boundary_ambiguous": boundary_ambiguous,
            "ambiguous_member_count": 0,
            "manifest_hash": "2" * 64,
            "provenance_json": "{}",
            "authority": authority,
            "members_unit": "degC",
            "local_day_start_utc": scope.target_window_start_utc.isoformat(),
            "step_horizon_hours": 144.0,
            "contributes_to_target_extrema": contributes_to_target_extrema,
            "forecast_window_attribution_status": forecast_window_attribution_status,
        },
    )


def _insert_source_run(
    conn: sqlite3.Connection,
    *,
    source_run_id: str,
    source_cycle_time: datetime,
    source_available_at: datetime,
    captured_at: datetime,
    status: str = "SUCCESS",
    completeness_status: str = "COMPLETE",
) -> None:
    scope = _scope()
    write_source_run(
        conn,
        source_run_id=source_run_id,
        source_id=_SOURCE_ID,
        track=_TRACK,
        release_calendar_key=_REL_KEY,
        source_cycle_time=source_cycle_time,
        source_issue_time=source_cycle_time,
        source_release_time=source_available_at,
        source_available_at=source_available_at,
        fetch_started_at=source_available_at,
        fetch_finished_at=captured_at,
        captured_at=captured_at,
        imported_at=captured_at,
        target_local_date=scope.target_local_date,
        city_id=scope.city_id,
        city_timezone=scope.city_timezone,
        temperature_metric=scope.temperature_metric,
        physical_quantity=_PHYSQ,
        observation_field=_OBS_FIELD,
        data_version=scope.data_version,
        expected_members=51,
        observed_members=51,
        expected_steps_json=scope.required_step_hours,
        observed_steps_json=scope.required_step_hours,
        completeness_status=completeness_status,
        status=status,
        raw_payload_hash="a" * 64,
        manifest_hash="b" * 64,
    )


def _insert_coverage(
    conn: sqlite3.Connection,
    *,
    coverage_id: str,
    source_run_id: str,
    snapshot_ids: list[int],
    computed_at: datetime,
    completeness_status: str = "COMPLETE",
    readiness_status: str = "LIVE_ELIGIBLE",
    expires_at: datetime | None = None,
    data_version: str = ECMWF_OPENDATA_HIGH_DATA_VERSION,
) -> None:
    scope = _scope()
    write_source_run_coverage(
        conn,
        coverage_id=coverage_id,
        source_run_id=source_run_id,
        source_id=_SOURCE_ID,
        source_transport=_TRANSPORT,
        release_calendar_key=_REL_KEY,
        track=_TRACK,
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity=_PHYSQ,
        observation_field=_OBS_FIELD,
        data_version=data_version,
        expected_members=51,
        observed_members=51,
        expected_steps_json=scope.required_step_hours,
        observed_steps_json=list(scope.required_step_hours),
        snapshot_ids_json=snapshot_ids,
        target_window_start_utc=scope.target_window_start_utc,
        target_window_end_utc=scope.target_window_end_utc,
        completeness_status=completeness_status,
        readiness_status=readiness_status,
        computed_at=computed_at,
        expires_at=expires_at if expires_at is not None else _utc(2026, 5, 8, 23),
    )


def _insert_latest_producer_readiness(
    conn: sqlite3.Connection,
    *,
    coverage_id: str,
    source_run_id: str,
    computed_at: datetime,
    data_version: str = ECMWF_OPENDATA_HIGH_DATA_VERSION,
) -> None:
    """Write the SINGLE surviving producer_readiness row.

    Mirrors production: write_readiness_state UPSERTs on the scope tuple (which
    excludes source_run_id), so after the 12Z cycle writes, only ONE readiness
    row remains — pointing at the LATEST (12Z) coverage via dependency_json.
    """
    scope = _scope()
    write_readiness_state(
        conn,
        readiness_id=f"producer_readiness:{coverage_id}",
        scope_type="city_metric",
        status="LIVE_ELIGIBLE",
        computed_at=computed_at,
        expires_at=_utc(2026, 5, 8, 23),
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity=_PHYSQ,
        observation_field=_OBS_FIELD,
        data_version=data_version,
        source_id=_SOURCE_ID,
        track=_TRACK,
        source_run_id=source_run_id,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        reason_codes_json=["PRODUCER_COVERAGE_READY"],
        dependency_json={"coverage_id": coverage_id, "source_run_id": source_run_id},
        provenance_json={"contract": "LiveEntryForecastTargetContract.v1"},
    )


def _insert_entry_readiness(conn: sqlite3.Connection) -> None:
    scope = _scope()
    write_readiness_state(
        conn,
        readiness_id="entry-readiness-1",
        scope_type="city_metric",
        status="LIVE_ELIGIBLE",
        computed_at=_utc(2026, 5, 8, 6),
        expires_at=_utc(2026, 5, 8, 23),
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity=_PHYSQ,
        observation_field=_OBS_FIELD,
        data_version=scope.data_version,
        source_id=_SOURCE_ID,
        track=_TRACK,
        strategy_key="entry_forecast",
        market_family=_FAMILY,
        condition_id=_CONDITION,
        reason_codes_json=["READY"],
        dependency_json={},
        provenance_json={"contract": "LiveEntryForecastTargetContract.v1"},
    )


def _read_full(conn: sqlite3.Connection, *, require_entry_readiness: bool = False):
    scope = _scope()
    return read_executable_forecast(
        conn,
        city_id=scope.city_id,
        city_name=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        source_id=_SOURCE_ID,
        source_transport=_TRANSPORT,
        data_version=scope.data_version,
        track=_TRACK,
        strategy_key="entry_forecast",
        market_family=_FAMILY,
        condition_id=_CONDITION,
        decision_time=_utc(2026, 5, 8, 6),
        require_entry_readiness=require_entry_readiness,
    )


# ---------------------------------------------------------------------------
# Shared production-shape fixture: 00Z contributing + 12Z non-contributing.
#   00Z: source_run="run-00z", coverage="cov-00z", snapshot_id=100, contributes=1
#   12Z: source_run="run-12z", coverage="cov-12z", snapshot_id=200, contributes=0
#   Only ONE producer_readiness row survives, pointing at 12Z (the latest cycle).
# ---------------------------------------------------------------------------


def _build_00z_contributor(conn: sqlite3.Connection) -> None:
    _insert_snapshot(
        conn,
        snapshot_id=100,
        source_run_id="run-00z",
        source_cycle_time="2026-05-08T00:00:00+00:00",
        available_at="2026-05-08T05:00:00+00:00",
        members_values=[18.0 + i * 0.1 for i in range(51)],
        contributes_to_target_extrema=1,
        forecast_window_attribution_status="FULLY_INSIDE_TARGET_LOCAL_DAY",
    )
    _insert_source_run(
        conn,
        source_run_id="run-00z",
        source_cycle_time=_utc(2026, 5, 8, 0),
        source_available_at=_utc(2026, 5, 8, 5),
        captured_at=_utc(2026, 5, 8, 5, 10),
    )
    _insert_coverage(
        conn,
        coverage_id="cov-00z",
        source_run_id="run-00z",
        snapshot_ids=[100],
        computed_at=_utc(2026, 5, 8, 5, 30),
    )


def _build_12z_noncontributor(conn: sqlite3.Connection) -> None:
    _insert_snapshot(
        conn,
        snapshot_id=200,
        source_run_id="run-12z",
        source_cycle_time="2026-05-08T12:00:00+00:00",
        available_at="2026-05-08T05:55:00+00:00",
        members_values=[10.0 + i * 0.1 for i in range(51)],
        contributes_to_target_extrema=0,
        forecast_window_attribution_status="POST_PEAK_OUTSIDE_TARGET",
    )
    _insert_source_run(
        conn,
        source_run_id="run-12z",
        source_cycle_time=_utc(2026, 5, 8, 12),
        source_available_at=_utc(2026, 5, 8, 5, 55),
        captured_at=_utc(2026, 5, 8, 5, 58),
    )
    _insert_coverage(
        conn,
        coverage_id="cov-12z",
        source_run_id="run-12z",
        snapshot_ids=[200],
        computed_at=_utc(2026, 5, 8, 5, 59),  # latest -> wins single-path
    )


# 6.1 -----------------------------------------------------------------------
def test_prefers_00z_contributing_over_later_12z_noncontributing() -> None:
    """6.1: with a 00Z FULL_CONTRIBUTOR and a later 12Z NON_CONTRIBUTOR, the
    selected bundle's snapshot AND source_run AND coverage must all be 00Z.

    Against the single-path reader this FAILS: the latest producer_readiness
    points at cov-12z, so the bundle locks to 12Z and the gate returns
    EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA (or selects the 12Z snapshot)."""
    conn = _conn()
    _build_00z_contributor(conn)
    _build_12z_noncontributor(conn)
    # ONE surviving readiness row, pointing at the latest (12Z) coverage.
    _insert_latest_producer_readiness(
        conn,
        coverage_id="cov-12z",
        source_run_id="run-12z",
        computed_at=_utc(2026, 5, 8, 5, 59),
    )

    result = _read_full(conn)

    assert result.ok, f"expected LIVE_ELIGIBLE, got {result.status}/{result.reason_code}"
    assert result.bundle is not None
    assert result.bundle.snapshot.snapshot_id == 100
    assert result.bundle.snapshot.source_run_id == "run-00z"
    assert result.bundle.evidence.coverage_id == "cov-00z"
    assert result.bundle.evidence.source_run_id == "run-00z"


# 6.2 -----------------------------------------------------------------------
def test_blocks_when_only_noncontributor_exists() -> None:
    """6.2: when the only eligible bundle is a NON_CONTRIBUTOR, block with
    EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA."""
    conn = _conn()
    _build_12z_noncontributor(conn)
    _insert_latest_producer_readiness(
        conn,
        coverage_id="cov-12z",
        source_run_id="run-12z",
        computed_at=_utc(2026, 5, 8, 5, 59),
    )

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA"


# 6.3 -----------------------------------------------------------------------
def test_selected_snapshot_is_in_selected_coverage() -> None:
    """6.3: evidence coherence — the selected snapshot_id must be a member of
    the selected coverage's snapshot_ids_json (cov-00z -> [100], not cov-12z)."""
    conn = _conn()
    _build_00z_contributor(conn)
    _build_12z_noncontributor(conn)
    _insert_latest_producer_readiness(
        conn,
        coverage_id="cov-12z",
        source_run_id="run-12z",
        computed_at=_utc(2026, 5, 8, 5, 59),
    )

    result = _read_full(conn)

    assert result.ok
    assert result.bundle is not None
    coverage_row = dict(
        conn.execute(
            "SELECT snapshot_ids_json FROM source_run_coverage WHERE coverage_id = ?",
            (result.bundle.evidence.coverage_id,),
        ).fetchone()
    )
    coverage_snapshot_ids = json.loads(coverage_row["snapshot_ids_json"])
    assert result.bundle.snapshot.snapshot_id in coverage_snapshot_ids
    assert result.bundle.evidence.coverage_id == "cov-00z"


# 6.4 -----------------------------------------------------------------------
def test_amsterdam_control_recency_wins_within_equal_contributor_class() -> None:
    """6.4: when BOTH cycles contribute (equal FULL_CONTRIBUTOR class), recency
    breaks the tie — the later 12Z bundle is selected."""
    conn = _conn()
    _build_00z_contributor(conn)
    # 12Z that ALSO contributes (the control: both are FULL_CONTRIBUTOR).
    _insert_snapshot(
        conn,
        snapshot_id=200,
        source_run_id="run-12z",
        source_cycle_time="2026-05-08T12:00:00+00:00",
        available_at="2026-05-08T05:55:00+00:00",
        members_values=[19.0 + i * 0.1 for i in range(51)],
        contributes_to_target_extrema=1,
        forecast_window_attribution_status="FULLY_INSIDE_TARGET_LOCAL_DAY",
    )
    _insert_source_run(
        conn,
        source_run_id="run-12z",
        source_cycle_time=_utc(2026, 5, 8, 12),
        source_available_at=_utc(2026, 5, 8, 5, 55),
        captured_at=_utc(2026, 5, 8, 5, 58),
    )
    _insert_coverage(
        conn,
        coverage_id="cov-12z",
        source_run_id="run-12z",
        snapshot_ids=[200],
        computed_at=_utc(2026, 5, 8, 5, 59),
    )
    _insert_latest_producer_readiness(
        conn,
        coverage_id="cov-12z",
        source_run_id="run-12z",
        computed_at=_utc(2026, 5, 8, 5, 59),
    )

    result = _read_full(conn)

    assert result.ok
    assert result.bundle is not None
    assert result.bundle.snapshot.snapshot_id == 200
    assert result.bundle.evidence.source_run_id == "run-12z"
    assert result.bundle.evidence.coverage_id == "cov-12z"


# 6.7 -----------------------------------------------------------------------
def test_current_ecmwf_opendata_null_contribution_blocks() -> None:
    """6.7 (§2): a CURRENT ECMWF Open Data snapshot with NULL contribution must
    fail closed — EXECUTABLE_FORECAST_EXTREMA_AUTHORITY_UNKNOWN — not pass through.
    A live mx2t3 row with missing provenance would otherwise re-open the P0
    cold-bias."""
    conn = _conn()
    _insert_snapshot(
        conn,
        snapshot_id=300,
        source_run_id="run-null",
        source_cycle_time="2026-05-08T00:00:00+00:00",
        available_at="2026-05-08T05:00:00+00:00",
        members_values=[18.0 + i * 0.1 for i in range(51)],
        contributes_to_target_extrema=None,  # schema-drift / writer-bug shape
        forecast_window_attribution_status=None,
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,  # CURRENT version
    )
    _insert_source_run(
        conn,
        source_run_id="run-null",
        source_cycle_time=_utc(2026, 5, 8, 0),
        source_available_at=_utc(2026, 5, 8, 5),
        captured_at=_utc(2026, 5, 8, 5, 10),
    )
    _insert_coverage(
        conn,
        coverage_id="cov-null",
        source_run_id="run-null",
        snapshot_ids=[300],
        computed_at=_utc(2026, 5, 8, 5, 30),
    )
    _insert_latest_producer_readiness(
        conn,
        coverage_id="cov-null",
        source_run_id="run-null",
        computed_at=_utc(2026, 5, 8, 5, 30),
    )

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "EXECUTABLE_FORECAST_EXTREMA_AUTHORITY_UNKNOWN"


# 6.8 -----------------------------------------------------------------------
def test_legacy_null_contribution_passes_through_recorded() -> None:
    """6.8 (§2): a LEGACY data_version snapshot with NULL contribution passes
    through (prior behavior), and the passthrough is recorded in the bundle's
    extrema_authority_applied_validations for auditability."""
    legacy_version = "ecmwf_opendata_mx2t6_local_calendar_day_max"
    conn = _conn()
    _insert_snapshot(
        conn,
        snapshot_id=400,
        source_run_id="run-legacy",
        source_cycle_time="2026-05-08T00:00:00+00:00",
        available_at="2026-05-08T05:00:00+00:00",
        members_values=[18.0 + i * 0.1 for i in range(51)],
        contributes_to_target_extrema=None,
        forecast_window_attribution_status=None,
        data_version=legacy_version,
    )
    _insert_source_run(
        conn,
        source_run_id="run-legacy",
        source_cycle_time=_utc(2026, 5, 8, 0),
        source_available_at=_utc(2026, 5, 8, 5),
        captured_at=_utc(2026, 5, 8, 5, 10),
    )
    _insert_coverage(
        conn,
        coverage_id="cov-legacy",
        source_run_id="run-legacy",
        snapshot_ids=[400],
        computed_at=_utc(2026, 5, 8, 5, 30),
        data_version=legacy_version,
    )
    _insert_latest_producer_readiness(
        conn,
        coverage_id="cov-legacy",
        source_run_id="run-legacy",
        computed_at=_utc(2026, 5, 8, 5, 30),
        data_version=legacy_version,
    )

    scope = _scope()
    result = read_executable_forecast(
        conn,
        city_id=scope.city_id,
        city_name=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        source_id=_SOURCE_ID,
        source_transport=_TRANSPORT,
        data_version=legacy_version,
        track=_TRACK,
        strategy_key="entry_forecast",
        market_family=_FAMILY,
        condition_id=_CONDITION,
        decision_time=_utc(2026, 5, 8, 6),
        require_entry_readiness=False,
    )

    assert result.ok, f"expected LIVE_ELIGIBLE, got {result.status}/{result.reason_code}"
    assert result.bundle is not None
    ens_result = result.bundle.to_ens_result()
    assert (
        "forecast_extrema_authority_legacy_null_passthrough"
        in ens_result["extrema_authority_applied_validations"]
    )


# 6.5 -----------------------------------------------------------------------
def test_sanity_gate_uses_settled_samples_for_point_support() -> None:
    """6.5 (§3): the day0 HIGH sanity gate must receive settlement-ROUNDED member
    samples (the space p_raw/p_cal live in), not raw member extrema.  Raw [22.6]*51
    settle to 23 under wmo_half_up; with p_cal[bin_23]=0.9 the settled samples give
    full support and PASS, whereas raw 22.6 samples give 0 support for [23,23] and
    would FALSE-BLOCK.  This proves the caller-side rounding contract."""
    import numpy as np

    from src.contracts.settlement_semantics import SettlementSemantics
    from src.signal.probability_sanity import validate_high_distribution
    from src.types.market import Bin

    bins = [Bin(low=c, high=c, unit="C", label=f"{c}C") for c in (21.0, 22.0, 23.0, 24.0, 25.0)]
    p_raw = np.array([0.02, 0.03, 0.90, 0.03, 0.02])
    p_cal = np.array([0.02, 0.03, 0.90, 0.03, 0.02])
    raw_members = np.full(51, 22.6)  # raw extrema, all just below 23

    # WMO half-up at 1.0°C precision (the standard C-city settlement contract):
    # 22.6 -> 23.  Mirrors settlement_semantics.round_values used to build p_raw.
    sem = SettlementSemantics(
        resolution_source="test",
        measurement_unit="C",
        precision=1.0,
        rounding_rule="wmo_half_up",
        finalization_time="12:00:00Z",
    )
    settled = sem.round_values(raw_members)

    # Raw samples (the OLD, buggy input) would false-block: 0 support for [23,23].
    ok_raw, reason_raw = validate_high_distribution(
        bins=bins, p_raw=p_raw, p_cal=p_cal,
        member_samples=raw_members, market_prices=None,
        strategy_key="day0_high:Amsterdam:test-raw",
    )
    assert ok_raw is False
    assert "POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT" in (reason_raw or "")

    # Settled samples (the §3 fix) pass: 22.6 -> 23 gives full support.
    ok_settled, reason_settled = validate_high_distribution(
        bins=bins, p_raw=p_raw, p_cal=p_cal,
        member_samples=settled, market_prices=None,
        strategy_key="day0_high:Amsterdam:test-settled",
    )
    assert ok_settled is True, f"settled samples should pass, got {reason_settled!r}"


# ---------------------------------------------------------------------------
# P1-1 antibody: BLOCKED latest producer_readiness must not suppress valid 00Z
# ---------------------------------------------------------------------------

def _insert_blocked_producer_readiness(
    conn: sqlite3.Connection,
    *,
    coverage_id: str,
    source_run_id: str,
    computed_at: datetime,
) -> None:
    """Write a BLOCKED producer_readiness row (simulates 12Z cycle that didn't pass readiness).

    With the pre-P1-1 code, _is_live_readiness(producer) returned a reason and the
    reader returned BLOCKED immediately before enumeration.  The fix stores the reason
    and continues to enumerate source_run_coverage rows — so the valid 00Z coverage
    (which is in source_run_coverage with readiness_status=LIVE_ELIGIBLE) is still found.
    """
    scope = _scope()
    write_readiness_state(
        conn,
        readiness_id=f"producer_readiness:blocked:{coverage_id}",
        scope_type="city_metric",
        status="BLOCKED",
        computed_at=computed_at,
        expires_at=_utc(2026, 5, 8, 23),
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity=_PHYSQ,
        observation_field=_OBS_FIELD,
        data_version=scope.data_version,
        source_id=_SOURCE_ID,
        track=_TRACK,
        source_run_id=source_run_id,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        reason_codes_json=["PRODUCER_COVERAGE_NOT_READY"],
        dependency_json={"coverage_id": coverage_id, "source_run_id": source_run_id},
        provenance_json={"contract": "LiveEntryForecastTargetContract.v1"},
    )


# 6.9 -----------------------------------------------------------------------
def test_blocked_producer_readiness_does_not_suppress_valid_00z_coverage() -> None:
    """6.9 (P1-1): when the latest producer_readiness is BLOCKED (e.g. 12Z incomplete)
    but a valid 00Z LIVE_ELIGIBLE coverage exists in source_run_coverage, the 00Z
    bundle must still be elected.

    Pre-fix: _is_live_readiness(producer) returned "PRODUCER_COVERAGE_NOT_READY" and
    the reader returned BLOCKED before enumeration — the 00Z coverage was never seen.
    Post-fix: producer_reason is stored but the reader enumerates ALL source_run_coverage
    rows independently; the 00Z coverage passes every gate and is elected.
    """
    conn = _conn()
    _build_00z_contributor(conn)
    # BLOCKED readiness pointing at the 00Z coverage (simulates 12Z blocking but 00Z data intact).
    _insert_blocked_producer_readiness(
        conn,
        coverage_id="cov-00z",
        source_run_id="run-00z",
        computed_at=_utc(2026, 5, 8, 5, 31),  # slightly after coverage computed_at
    )

    result = _read_full(conn)

    assert result.ok, (
        f"Expected LIVE_ELIGIBLE despite BLOCKED readiness, got {result.status}/{result.reason_code}"
    )
    assert result.bundle is not None
    assert result.bundle.snapshot.snapshot_id == 100
    assert result.bundle.evidence.coverage_id == "cov-00z"
    assert result.bundle.evidence.source_run_id == "run-00z"


# 6.10 -----------------------------------------------------------------------
def test_blocked_producer_readiness_surfaces_reason_when_no_valid_coverage() -> None:
    """6.10 (P1-1 diagnostic): when the latest producer_readiness is BLOCKED and
    there are NO valid coverage candidates, the BLOCKED reason from the readiness
    row is surfaced as the result reason (not the generic SOURCE_RUN_COVERAGE_MISSING).

    This exercises the post-enumeration fallback: if candidates is empty and
    producer_reason is not None, return producer_reason directly (diagnosability).
    """
    conn = _conn()
    # No source_run / coverage / snapshot inserted — the 00Z coverage does NOT exist.
    # The BLOCKED readiness row exists but there is nothing to enumerate.
    _insert_blocked_producer_readiness(
        conn,
        coverage_id="cov-nonexistent",
        source_run_id="run-nonexistent",
        computed_at=_utc(2026, 5, 8, 5, 31),
    )

    result = _read_full(conn)

    assert not result.ok
    # The producer_reason fallback must surface "PRODUCER_COVERAGE_NOT_READY" because
    # producer_reason is not None and the candidates list is empty.
    assert result.reason_code == "PRODUCER_COVERAGE_NOT_READY", (
        f"Expected PRODUCER_COVERAGE_NOT_READY (producer_reason fallback), got {result.reason_code!r}"
    )
