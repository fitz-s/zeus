# Created: 2026-06-06
# Last reused/audited: 2026-08-23
# Lifecycle: created=2026-06-06; last_reviewed=2026-08-23; last_reused=2026-08-23
# Purpose: Protect DB materialization for Open-Meteo ECMWF IFS 9km + Bayes-fusion replacement live layer.
# Reuse: Run before changing replacement forecast live/experiment write path.
# Authority basis: Operator-directed replacement forecast simple-switch readiness.
"""Replacement forecast materializer tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_materializer import (
    _BayesPrecisionFusionFusionOverride,
    Day0EnqueueOwnershipWitness,
    REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
    REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET,
    ReplacementForecastMaterializeRequest,
    STALE_DAY0_ENQUEUE_OWNER,
    _QLCB_BASIS,
    _ensure_forecast_posteriors_runtime_layer,
    _ensure_replacement_frontier_indexes,
    _ensure_replacement_identity_columns,
    _replacement_is_live_layer,
    materialize_replacement_forecast_live,
)
import src.data.replacement_forecast_materializer as materializer_mod
from src.data import replacement_cycle_advance_trigger as cycle_advance
from src.data.replacement_forecast_readiness import LIVE_RUNTIME_LAYER, STRATEGY_KEY
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import (
    _ensure_forecast_posteriors_runtime_layer_compatibility,
    apply_canonical_schema,
)
from src.state.source_run_repo import write_source_run

UTC = timezone.utc
_DEFAULT_PRECISION_GUARD = object()
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _Evidence:
    source_run_id: str


@dataclass(frozen=True)
class _BaselineBundle:
    evidence: _Evidence


@dataclass(frozen=True)
class _TemperatureBin:
    bin_id: str
    lower_c: float | None = None
    upper_c: float | None = None
    center_c: float | None = None
    display_unit: str = "C"
    settlement_unit: str = "C"
    rounding_rule: str = "wmo_half_up"


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_readiness_state(conn)
    return conn


def _ensure_source_run_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            track TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            ingest_mode TEXT NOT NULL,
            origin_mode TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_issue_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            fetch_started_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            imported_at TEXT,
            valid_time_start TEXT,
            valid_time_end TEXT,
            target_local_date TEXT,
            city_id TEXT,
            city_timezone TEXT,
            temperature_metric TEXT,
            physical_quantity TEXT,
            observation_field TEXT,
            dataset_id TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            expected_steps_json TEXT NOT NULL DEFAULT '[]',
            observed_steps_json TEXT NOT NULL DEFAULT '[]',
            expected_count INTEGER,
            observed_count INTEGER,
            completeness_status TEXT NOT NULL,
            partial_run INTEGER NOT NULL DEFAULT 0,
            raw_payload_hash TEXT,
            manifest_hash TEXT,
            status TEXT NOT NULL,
            reason_code TEXT,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def _ensure_source_run_coverage_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            city TEXT NOT NULL,
            target_local_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            expected_members INTEGER NOT NULL,
            observed_members INTEGER NOT NULL,
            expected_steps_json TEXT NOT NULL,
            observed_steps_json TEXT NOT NULL,
            snapshot_ids_json TEXT NOT NULL,
            completeness_status TEXT NOT NULL,
            readiness_status TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            recorded_at TEXT NOT NULL
        )
        """
    )


def _anchor(*, source_cycle_time: datetime | None = None) -> OpenMeteoIfs9LocalDayAnchor:
    local_tz = timezone(timedelta(hours=8))
    contributing_local_times = tuple(datetime(2026, 6, 7, hour, tzinfo=local_tz) for hour in range(24))
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 7),
        high_c=27.0,
        low_c=18.5,
        sample_count=24,
        contributing_local_times=contributing_local_times,
        contributing_valid_times_utc=tuple(item.astimezone(UTC) for item in contributing_local_times),
        source_cycle_time=source_cycle_time or _dt(0),
    )


def _anchor_with_local_hours(*, hours: range | tuple[int, ...]) -> OpenMeteoIfs9LocalDayAnchor:
    local_tz = timezone(timedelta(hours=8))
    contributing_local_times = tuple(datetime(2026, 6, 7, hour, tzinfo=local_tz) for hour in hours)
    return replace(
        _anchor(),
        sample_count=len(contributing_local_times),
        contributing_local_times=contributing_local_times,
        contributing_valid_times_utc=tuple(item.astimezone(UTC) for item in contributing_local_times),
    )


def _precision_guard(**overrides: object):
    values = {
        "city": "Shanghai",
        "station_id": "ZSSS",
        "city_lat": 31.2304,
        "city_lon": 121.4737,
        "station_lat": 31.1979,
        "station_lon": 121.3363,
        "requested_lat": 31.1979,
        "requested_lon": 121.3363,
        "requested_coordinate_precision_decimals": 4,
        "nearest_grid_lat": 31.2,
        "nearest_grid_lon": 121.3,
        "nearest_grid_distance_km": 3.5,
        "native_grid": "openmeteo_ecmwf_ifs_9km",
        "delivery_grid_resolution": "0p1",
        "interpolation_method": "nearest_gridpoint",
        "endpoint_mode": "hourly_zeus_aggregated",
        "local_day_start_utc": _dt(16),
        "local_day_end_utc": datetime(2026, 6, 7, 16, tzinfo=UTC),
        "timezone_name": "Asia/Shanghai",
        "target_local_date": date(2026, 6, 7),
        "temperature_unit": "C",
        "anchor_sigma_c": 3.0,
        "grid_elevation_m": 4.0,
        "station_elevation_m": 3.0,
        "land_sea_mask": "land",
        "city_class": "flat_inland",
        "station_mapping_policy": "settlement_station",
    }
    values.update(overrides)
    return evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(**values)  # type: ignore[arg-type]
    )


def _bins() -> tuple[_TemperatureBin, ...]:
    return (
        _TemperatureBin("cool", upper_c=20.0, center_c=19.0),
        _TemperatureBin("warm", lower_c=21.0, upper_c=30.0),
        _TemperatureBin("hot", lower_c=31.0, center_c=32.0),
    )


def _install_live_fusion(
    monkeypatch: pytest.MonkeyPatch,
    *,
    complete: bool = True,
    shape_lag_hours: float = 0.0,
) -> None:
    members = tuple(25.0 + (index - 25) * 0.02 for index in range(51))
    override = _BayesPrecisionFusionFusionOverride(
        anchor_value_c=25.0,
        anchor_sigma_c=0.35,
        method="test_bayes_precision_fusion",
        used_models=("ecmwf_ifs9", "gfs", "icon", "gem", "jma"),
        model_set_hash="test-model-set",
        resolution_mix_hash="test-resolution-mix",
        lead_bucket="d1",
        dropped_models=(),
        excluded_regionals=(),
        dropped_aliases=(),
        raw_model_forecast_ids=(101, 102, 103),
        anchor_bridge={"test": True},
        predictive_sigma_c=2.0,
        decorrelated_providers_complete=complete,
        decorrelated_providers_served=5 if complete else 4,
        decorrelated_providers_expected=5,
        current_value_serving={"ecmwf_ifs9": {"served_via": "single_runs"}},
        current_evidence_shape={
            "snapshot_id": 9001,
            "shape_hash": "test-current-shape",
            "semantics_revision": (
                materializer_mod.CURRENT_EVIDENCE_SEMANTICS_REVISION
                if shape_lag_hours == 0.0
                else materializer_mod.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
            ),
            "source_cycle_time": (
                _dt(0) - timedelta(hours=shape_lag_hours)
            ).isoformat(),
            "source_available_at": _dt(1).isoformat(),
            "shape_lag_hours": shape_lag_hours,
            "stale_shape_reused": shape_lag_hours > 0.0,
            "translation_applied": False,
            "member_count": len(members),
            "between_cohort_status": "SIMULTANEOUS_PROVEN",
        },
        current_evidence_members_c=members,
    )
    monkeypatch.setattr(materializer_mod, "_replacement_bayes_precision_fusion_override", lambda *args, **kwargs: override)


def _request(
    *,
    baseline_data_version: str = "ecmwf_opendata_mx2t3_local_calendar_day_max",
    baseline_source_run_id: str = "b0-run",
    baseline_source_available_at: datetime | None = None,
    openmeteo_source_run_id: str | None = "om9-run",
    openmeteo_source_available_at: datetime | None = None,
    source_cycle_time: datetime | None = None,
    computed_at: datetime | None = None,
    expires_at: datetime | None = None,
    anchor_artifact_id: int | None = None,
    openmeteo_precision_guard=_DEFAULT_PRECISION_GUARD,
    day0_observed_extreme_c: float | None = None,
    day0_observed_extreme_source: str | None = None,
    day0_observed_extreme_observation_time: str | None = None,
    day0_observed_extreme_sample_count: int | None = None,
    day0_observation_state: str | None = None,
) -> ReplacementForecastMaterializeRequest:
    guard = _precision_guard() if openmeteo_precision_guard is _DEFAULT_PRECISION_GUARD else openmeteo_precision_guard
    return ReplacementForecastMaterializeRequest(
        city="Shanghai",
        city_id="Shanghai",
        city_timezone="Asia/Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        baseline_source_run_id=baseline_source_run_id,
        baseline_data_version=baseline_data_version,
        baseline_source_available_at=baseline_source_available_at or _dt(2),
        openmeteo_anchor=_anchor(source_cycle_time=source_cycle_time),
        openmeteo_source_run_id=openmeteo_source_run_id,
        openmeteo_source_available_at=openmeteo_source_available_at or _dt(3),
        bins=_bins(),
        source_cycle_time=source_cycle_time or _dt(0),
        computed_at=computed_at or _dt(4),
        expires_at=expires_at or _dt(6),
        anchor_artifact_id=anchor_artifact_id,
        openmeteo_precision_guard=guard,
        day0_observed_extreme_c=day0_observed_extreme_c,
        day0_observed_extreme_source=day0_observed_extreme_source,
        day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
        day0_observed_extreme_sample_count=day0_observed_extreme_sample_count,
        day0_observed_extreme_unit="C" if day0_observed_extreme_c is not None else None,
        day0_observation_state=day0_observation_state,
    )


def _day0_owner_witness(
    request: ReplacementForecastMaterializeRequest,
    *,
    seed_file: Path,
) -> Day0EnqueueOwnershipWitness:
    identity = cycle_advance._day0_conditioning_identity(
        source=request.day0_observed_extreme_source,
        observation_time=request.day0_observed_extreme_observation_time,
        observed_extreme_c=request.day0_observed_extreme_c,
        unit=request.day0_observed_extreme_unit,
    )
    assert identity is not None
    return Day0EnqueueOwnershipWitness(
        city=request.city,
        target_date=request.target_date.isoformat(),
        metric=request.temperature_metric,
        target_cycle_time=request.source_cycle_time.isoformat(),
        seed_file=str(seed_file),
        conditioning_identity=identity,
    )


def _record_day0_owner(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    witness: Day0EnqueueOwnershipWitness,
) -> None:
    assert cycle_advance._record_enqueue(
        conn,
        city=witness.city,
        target_date=witness.target_date,
        metric=witness.metric,
        consumed_cycle_iso=witness.target_cycle_time,
        target_cycle_iso=witness.target_cycle_time,
        held_position=True,
        seed_file=witness.seed_file,
        day0_observed_extreme_source=request.day0_observed_extreme_source,
        day0_observed_extreme_observation_time=(
            request.day0_observed_extreme_observation_time
        ),
        day0_observed_extreme_c=request.day0_observed_extreme_c,
        day0_observed_extreme_unit=request.day0_observed_extreme_unit,
    ) is True
    conn.commit()


def _prepare_for_final_write(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
):
    conn.execute("BEGIN")
    prepared = materializer_mod.prepare_replacement_forecast_live(conn, request)
    conn.rollback()
    assert isinstance(
        prepared, materializer_mod.PreparedReplacementForecastMaterialization
    )
    return prepared


def test_day0_owner_witness_allows_current_owner_posterior_write(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unchanged Day0 owner survives final-write revalidation and writes a posterior."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    request = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=12,
    )
    witness = _day0_owner_witness(request, seed_file=tmp_path / "owner-a.json")
    _record_day0_owner(conn, request, witness)
    prepared = _prepare_for_final_write(
        conn, replace(request, day0_enqueue_owner_witness=witness)
    )

    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()

    assert result.ok is True
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "absorbing_extreme", "fast_extreme"),
    [
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", 30.0, 31.0),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", 21.0, 20.0),
    ],
)
def test_day0_owner_witness_keeps_newer_fast_residual_over_absorbing_frontier(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    absorbing_extreme: float,
    fast_extreme: float,
) -> None:
    """A newer fast extreme keeps its residual likelihood and exact enqueue owner."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    absorbing = replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=absorbing_extreme,
            day0_observed_extreme_source="wu_icao_history",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
            day0_observed_extreme_sample_count=12,
        ),
        temperature_metric=metric,
        baseline_data_version=baseline_data_version,
    )
    assert materialize_replacement_forecast_live(conn, absorbing).ok is True

    current = replace(
        absorbing,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=fast_extreme,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        day0_observed_extreme_sample_count=13,
    )
    likelihood = SimpleNamespace(
        residual_weights_c=((0.0, 1.0),),
        unknown_weight=0.0,
        settlement_extreme_c=absorbing_extreme,
        identity_hash="1" * 64,
        as_payload=lambda: {
            "identity_hash": "1" * 64,
            "settlement_extreme_c": absorbing_extreme,
        },
    )
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *args, **kwargs: likelihood,
    )
    witness = _day0_owner_witness(current, seed_file=tmp_path / "fast-owner.json")
    _record_day0_owner(conn, current, witness)
    prepared = _prepare_for_final_write(
        conn, replace(current, day0_enqueue_owner_witness=witness)
    )

    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()

    assert result.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert provenance["q_shape"] == "fused_day0_fast_residual_likelihood"
    assert (
        provenance["day0_provisional_observation"]["observed_extreme_c"]
        == fast_extreme
    )
    assert (
        provenance["day0_provisional_observation"]["source"]
        == "wu_api+same_station_fast_tail"
    )


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "absorbing_extreme", "fast_extreme", "bound"),
    [
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", 30.0, 31.0, None),
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", 30.0, 31.0, float("nan")),
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", 30.0, 31.0, 29.0),
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", 30.0, 29.0, 30.0),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", 21.0, 20.0, None),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", 21.0, 20.0, float("nan")),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", 21.0, 20.0, 22.0),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", 21.0, 22.0, 21.0),
    ],
)
def test_fast_residual_frontier_fails_closed_when_bound_cannot_cover_history(
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    absorbing_extreme: float,
    fast_extreme: float,
    bound: float | None,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    absorbing = replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=absorbing_extreme,
            day0_observed_extreme_source="wu_icao_history",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        ),
        temperature_metric=metric,
        baseline_data_version=baseline_data_version,
    )
    assert materialize_replacement_forecast_live(conn, absorbing).ok is True
    provisional = replace(
        absorbing,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=fast_extreme,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
    )
    likelihood = (
        None if bound is None else SimpleNamespace(settlement_extreme_c=bound)
    )
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *args, **kwargs: likelihood,
    )

    reduced = materializer_mod._request_with_day0_physical_frontier(
        conn,
        provisional,
        metric=metric,
    )

    assert isinstance(reduced, ReplacementForecastMaterializeRequest)
    assert reduced.day0_observed_extreme_c == absorbing_extreme
    assert reduced.day0_observed_extreme_source == "wu_icao_history"
    assert reduced.day0_observed_extreme_observation_time == _dt(17, 55).isoformat()


def test_stronger_absorbing_frontier_after_prepare_invalidates_fast_owner(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final writer revalidation rejects a fast request superseded after prepare."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    prior = replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=21.0,
            day0_observed_extreme_source="wu_icao_history",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        ),
        temperature_metric="low",
        baseline_data_version="ecmwf_opendata_mn2t3_local_calendar_day_min",
    )
    assert materialize_replacement_forecast_live(conn, prior).ok is True
    current = replace(
        prior,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=20.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
    )
    likelihood_bound = {"value": 21.0}
    likelihood = SimpleNamespace(
        residual_weights_c=((0.0, 1.0),),
        unknown_weight=0.0,
        settlement_extreme_c=21.0,
        identity_hash="2" * 64,
        as_payload=lambda: {
            "identity_hash": "2" * 64,
            "settlement_extreme_c": likelihood_bound["value"],
        },
    )

    def _likelihood(*_args, **_kwargs):
        likelihood.settlement_extreme_c = likelihood_bound["value"]
        return likelihood

    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        _likelihood,
    )
    witness = _day0_owner_witness(current, seed_file=tmp_path / "fast-owner.json")
    _record_day0_owner(conn, current, witness)
    prepared = _prepare_for_final_write(
        conn, replace(current, day0_enqueue_owner_witness=witness)
    )

    stronger = replace(
        prior,
        computed_at=_dt(18, 8),
        day0_observed_extreme_c=19.0,
        day0_observed_extreme_observation_time=_dt(18, 7).isoformat(),
    )
    assert materialize_replacement_forecast_live(conn, stronger).ok is True
    conn.commit()
    likelihood_bound["value"] = 19.0

    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(
        materializer_mod.PreparedReplacementForecastSnapshotStale
    ):
        materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.rollback()

    refreshed = _prepare_for_final_write(conn, prepared.request)
    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(
        conn, refreshed
    )
    conn.commit()

    assert result.status == "BLOCKED"
    assert result.reason_codes == (STALE_DAY0_ENQUEUE_OWNER,)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 2


def test_day0_owner_witness_blocks_swapped_owner_before_posterior_insert(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A swap after read preparation blocks A, while the current B witness can write."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    owner_a = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=12,
    )
    witness_a = _day0_owner_witness(owner_a, seed_file=tmp_path / "owner-a.json")
    _record_day0_owner(conn, owner_a, witness_a)
    prepared_a = _prepare_for_final_write(
        conn, replace(owner_a, day0_enqueue_owner_witness=witness_a)
    )

    owner_b = replace(
        owner_a,
        computed_at=_dt(18, 1),
        day0_observed_extreme_c=26.25,
        day0_observed_extreme_source="wu_api_same_time_revision",
    )
    witness_b = _day0_owner_witness(owner_b, seed_file=tmp_path / "owner-b.json")
    assert cycle_advance._record_enqueue(
        conn,
        city=witness_b.city,
        target_date=witness_b.target_date,
        metric=witness_b.metric,
        consumed_cycle_iso=witness_b.target_cycle_time,
        target_cycle_iso=witness_b.target_cycle_time,
        held_position=True,
        seed_file=witness_b.seed_file,
        reason="DAY0_OBSERVATION_ADVANCED",
        replace_existing_seed_file=True,
        day0_observed_extreme_source=owner_b.day0_observed_extreme_source,
        day0_observed_extreme_observation_time=(
            owner_b.day0_observed_extreme_observation_time
        ),
        day0_observed_extreme_c=owner_b.day0_observed_extreme_c,
        day0_observed_extreme_unit=owner_b.day0_observed_extreme_unit,
    ) is True
    conn.commit()

    conn.execute("BEGIN IMMEDIATE")
    stale = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared_a)
    conn.commit()
    assert stale.status == "BLOCKED"
    assert stale.reason_codes == (STALE_DAY0_ENQUEUE_OWNER,)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0

    prepared_b = _prepare_for_final_write(
        conn, replace(owner_b, day0_enqueue_owner_witness=witness_b)
    )
    conn.execute("BEGIN IMMEDIATE")
    current = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared_b)
    conn.commit()
    assert current.ok is True
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1


def test_materializer_blocks_non_live_posterior_before_execution_authority_table() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(conn, _request())

    assert result.ok is False
    # Catch-all reason stays first (byte-identical prefix for existing consumers);
    # a typed sub-reason is appended so the operator sees WHICH requirement failed
    # (2026-07-13/14 incident: the catch-all alone told 277 receipts nothing).
    assert result.reason_codes[0] == REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET
    assert len(result.reason_codes) > 1
    assert any(code.startswith("Q_MODE:") for code in result.reason_codes)
    assert result.posterior_id is None
    assert result.anchor_id is not None
    assert result.readiness_id is None
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_materializer_writes_authorized_06z_cycle_as_live_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)

    result = materialize_replacement_forecast_live(
        conn,
        _request(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12)),
    )

    assert result.ok is True
    assert result.anchor_id is not None
    assert result.posterior_id is not None
    row = conn.execute("SELECT runtime_layer, provenance_json FROM forecast_posteriors").fetchone()
    provenance = json.loads(row["provenance_json"])
    assert row["runtime_layer"] == LIVE_RUNTIME_LAYER
    assert provenance["cycle_phase"] == "synoptic"


def test_materializer_surfaces_bounds_missing_sub_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-07-13/14 incident fix: a fused-q bounds-build failure must surface the
    Q_MODE:FUSED_NORMAL_BOUNDS_MISSING sub-reason, not just the catch-all code — so
    a BLOCKED receipt tells the operator WHICH requirement failed without opening a
    subprocess log."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    monkeypatch.setattr(
        materializer_mod,
        "_build_fused_q_bounds",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bootstrap exploded")),
    )

    result = materialize_replacement_forecast_live(
        conn,
        _request(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12)),
    )

    assert result.ok is False
    assert result.reason_codes[0] == REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET
    assert "Q_MODE:FUSED_NORMAL_BOUNDS_MISSING" in result.reason_codes
    assert result.posterior_id is None
    # Shadow accrual still happens: a row is NOT written for the live-blocked mode,
    # but the anchor row is (unchanged prior contract).
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


def test_runtime_layer_requires_live_flags_and_bootstrap_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(materializer_mod, "REQUIRED_FLAGS", ())

    live_layer = _replacement_is_live_layer(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1, "warm": 0.6, "hot": 0.05},
        q_ucb_map={"cool": 0.3, "warm": 0.9, "hot": 0.2},
        q_lcb_basis=_QLCB_BASIS,
    )

    assert live_layer is True


def test_runtime_layer_rejects_wilson_or_missing_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(materializer_mod, "REQUIRED_FLAGS", ())

    assert _replacement_is_live_layer(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1},
        q_ucb_map={"cool": 0.3},
        q_lcb_basis="legacy_wilson_member_votes",
    ) is False


def test_forecast_posteriors_runtime_layer_migration_preserves_legacy_live_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_authority_status TEXT NOT NULL DEFAULT 'DIAGNOSTIC_ONLY'
                CHECK (trade_authority_status IN ('DIAGNOSTIC_ONLY', 'LIVE_AUTHORITY')),
            q_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO forecast_posteriors (trade_authority_status, q_json) VALUES (?, ?)",
        ("DIAGNOSTIC_ONLY", '{"bad":1}'),
    )
    conn.execute(
        "INSERT INTO forecast_posteriors (trade_authority_status, q_json) VALUES (?, ?)",
        ("LIVE_AUTHORITY", '{"good":1}'),
    )

    _ensure_forecast_posteriors_runtime_layer(conn)

    rows = conn.execute("SELECT posterior_id, runtime_layer, q_json FROM forecast_posteriors").fetchall()
    assert [dict(row) for row in rows] == [
        {"posterior_id": 2, "runtime_layer": LIVE_RUNTIME_LAYER, "q_json": '{"good":1}'}
    ]
    assert "trade_authority_status" not in {
        row["name"] for row in conn.execute("PRAGMA table_info(forecast_posteriors)")
    }
    conn.execute(
        "INSERT INTO forecast_posteriors (runtime_layer, q_json) VALUES (?, ?)",
        (LIVE_RUNTIME_LAYER, "{}"),
    )
    statuses = [
        row["runtime_layer"]
        for row in conn.execute("SELECT runtime_layer FROM forecast_posteriors ORDER BY posterior_id")
    ]
    assert statuses == [LIVE_RUNTIME_LAYER, LIVE_RUNTIME_LAYER]
    _ensure_forecast_posteriors_runtime_layer(conn)
    assert [
        row["runtime_layer"]
        for row in conn.execute("SELECT runtime_layer FROM forecast_posteriors ORDER BY posterior_id")
    ] == [LIVE_RUNTIME_LAYER, LIVE_RUNTIME_LAYER]
    assert _replacement_is_live_layer(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1},
        q_ucb_map=None,
        q_lcb_basis=_QLCB_BASIS,
    ) is False


def test_forecast_posteriors_runtime_layer_migration_does_not_write_when_already_live() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            runtime_layer TEXT NOT NULL DEFAULT 'live'
                CHECK (runtime_layer IN ('live')),
            q_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO forecast_posteriors (runtime_layer, q_json) VALUES (?, ?)",
        (LIVE_RUNTIME_LAYER, "{}"),
    )

    traced: list[str] = []
    conn.set_trace_callback(lambda sql: traced.append(sql))
    _ensure_forecast_posteriors_runtime_layer(conn)
    _ensure_forecast_posteriors_runtime_layer_compatibility(conn)
    conn.set_trace_callback(None)

    forecast_posterior_mutations = [
        sql.strip().upper()
        for sql in traced
        if "FORECAST_POSTERIORS" in sql.upper()
        and (
            sql.lstrip().upper().startswith("DELETE")
            or sql.lstrip().upper().startswith("UPDATE")
        )
    ]
    assert forecast_posterior_mutations == []


def test_forecast_posteriors_runtime_layer_migration_repairs_invalid_observation_view() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            running_max REAL
        );
        CREATE VIEW observation_hourly_extrema AS
            SELECT o.*, o.running_max AS hour_bucket_max, o.running_min AS hour_bucket_min
            FROM observation_instants o;
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_authority_status TEXT NOT NULL DEFAULT 'DIAGNOSTIC_ONLY'
                CHECK (trade_authority_status IN ('LIVE_AUTHORITY')),
            runtime_layer TEXT,
            q_json TEXT NOT NULL
        );
        INSERT INTO forecast_posteriors (trade_authority_status, runtime_layer, q_json)
        VALUES ('LIVE_AUTHORITY', 'live', '{}');
        """
    )

    _ensure_forecast_posteriors_runtime_layer(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(observation_instants)")}
    posterior_cols = {row["name"] for row in conn.execute("PRAGMA table_info(forecast_posteriors)")}
    assert "running_min" in cols
    assert "trade_authority_status" not in posterior_cols
    conn.execute("SELECT * FROM observation_hourly_extrema").fetchall()
    conn.execute(
        "INSERT INTO forecast_posteriors (runtime_layer, q_json) VALUES (?, ?)",
        (LIVE_RUNTIME_LAYER, "{}"),
    )


def test_legacy_anchor_schema_migration_does_not_rewrite_legacy_status_columns() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE raw_forecast_artifacts (
            artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_authority_status TEXT NOT NULL DEFAULT 'BLOCKED'
                CHECK (trade_authority_status IN ('BLOCKED'))
        );
        INSERT INTO raw_forecast_artifacts (artifact_id, trade_authority_status)
        VALUES (1, 'BLOCKED');

        CREATE TABLE deterministic_forecast_anchors (
            anchor_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            data_version TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
            anchor_value_c REAL NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            artifact_id INTEGER REFERENCES raw_forecast_artifacts(artifact_id),
            model TEXT NOT NULL,
            native_grid TEXT,
            delivery_grid_resolution TEXT,
            interpolation_method TEXT,
            contributing_times_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            trade_authority_status TEXT NOT NULL DEFAULT 'BLOCKED'
                CHECK (trade_authority_status IN ('BLOCKED')),
            training_allowed INTEGER NOT NULL DEFAULT 0
                CHECK (training_allowed = 0),
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            anchor_identity_hash TEXT,
            UNIQUE(source_id, product_id, data_version, city, target_date, temperature_metric, source_cycle_time)
        );
        INSERT INTO deterministic_forecast_anchors (
            source_id, product_id, data_version, city, target_date, temperature_metric,
            anchor_value_c, source_cycle_time, source_available_at, captured_at,
            artifact_id, model, trade_authority_status, anchor_identity_hash
        ) VALUES (
            'openmeteo_ecmwf_ifs_9km',
            'openmeteo_ecmwf_ifs9_deterministic_anchor_v1',
            'openmeteo_ecmwf_ifs9_anchor_localday_high',
            'Chengdu',
            '2026-06-17',
            'high',
            25.65,
            '2026-06-17T00:00:00+00:00',
            '2026-06-17T11:21:16+00:00',
            '2026-06-17T12:08:19+00:00',
            1,
            'ecmwf_ifs9',
            'BLOCKED',
            'anchor-hash'
        );

        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            openmeteo_anchor_id INTEGER REFERENCES deterministic_forecast_anchors(anchor_id),
            trade_authority_status TEXT NOT NULL DEFAULT 'BLOCKED'
                CHECK (trade_authority_status IN ('BLOCKED', 'BLOCKED'))
        );
        """
    )

    _ensure_replacement_identity_columns(conn)

    raw_status = conn.execute(
        "SELECT trade_authority_status FROM raw_forecast_artifacts WHERE artifact_id = 1"
    ).fetchone()["trade_authority_status"]
    anchor_status = conn.execute(
        "SELECT trade_authority_status FROM deterministic_forecast_anchors WHERE anchor_id = 1"
    ).fetchone()["trade_authority_status"]
    assert "trade_authority_status" not in {
        row["name"] for row in conn.execute("PRAGMA table_info(forecast_posteriors)")
    }
    conn.execute(
        "INSERT INTO forecast_posteriors (openmeteo_anchor_id, runtime_layer) VALUES (?, ?)",
        (1, LIVE_RUNTIME_LAYER),
    )

    assert raw_status == "BLOCKED"
    assert anchor_status == "BLOCKED"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_materializer_keeps_readiness_separate_by_baseline_source_run(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)

    first = materialize_replacement_forecast_live(
        conn,
        _request(
            baseline_source_run_id="ecmwf_open_data:mx2t6_high:2026-06-06T12Z",
            baseline_source_available_at=_dt(2),
            computed_at=_dt(4),
            expires_at=_dt(6),
        ),
    )
    second = materialize_replacement_forecast_live(
        conn,
        _request(
            baseline_source_run_id="ecmwf_open_data:mx2t6_high:2026-06-07T00Z",
            baseline_source_available_at=_dt(2, 15),
            computed_at=_dt(4, 15),
            expires_at=_dt(6, 15),
        ),
    )

    assert first.ok is True
    assert second.ok is True
    rows = conn.execute(
        """
        SELECT track, dependency_json
        FROM readiness_state
        WHERE city = 'Shanghai'
          AND target_local_date = '2026-06-07'
          AND temperature_metric = 'high'
          AND strategy_key = ?
        ORDER BY track
        """,
        (STRATEGY_KEY,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["track"] == "soft_anchor_posterior"
    assert "2026-06-07T00Z" in rows[0]["dependency_json"]


def test_materializer_writes_certified_bootstrap_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)

    result = materialize_replacement_forecast_live(conn, _request())

    assert result.ok is True
    posterior_row = conn.execute("SELECT q_json, q_lcb_json, q_ucb_json, provenance_json, runtime_layer FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    q = json.loads(posterior_row["q_json"])
    q_lcb = json.loads(posterior_row["q_lcb_json"])
    q_ucb = json.loads(posterior_row["q_ucb_json"])
    provenance = json.loads(posterior_row["provenance_json"])
    assert posterior_row["runtime_layer"] == LIVE_RUNTIME_LAYER
    assert set(q_lcb) == set(q) == set(q_ucb)
    for key, point in q.items():
        assert q_lcb[key] <= point <= q_ucb[key]
    assert not any(str(key).startswith(("buy_no:", "no:")) for key in q_lcb)
    assert provenance["q_lcb_json_role"] == "fused_center_bootstrap_lcb"


def test_materializer_does_not_publish_stale_ensemble_as_live_probability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch, shape_lag_hours=6.0)

    result = materialize_replacement_forecast_live(conn, _request())

    assert result.ok is False
    assert "CAPTURE:CURRENT_EVIDENCE_NOT_LIVE" in result.reason_codes
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_prepared_materialization_keeps_compute_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)

    conn.execute("BEGIN")
    prepared = materializer_mod.prepare_replacement_forecast_live(conn, _request())
    assert isinstance(
        prepared,
        materializer_mod.PreparedReplacementForecastMaterialization,
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM deterministic_forecast_anchors"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    conn.rollback()

    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()

    assert result.ok is True
    assert conn.execute(
        "SELECT COUNT(*) FROM deterministic_forecast_anchors"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1


def test_materializer_lifts_computed_at_to_source_run_possession(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _ensure_source_run_table(conn)
    _install_live_fusion(monkeypatch)
    late_possession = _dt(4, 5)
    for source_run_id, source_id, track in (
        ("b0-run", "ecmwf_open_data", "mx2t3_high"),
        ("om9-run", "openmeteo_ecmwf_ifs9", "localday_high"),
    ):
        write_source_run(
            conn,
            source_run_id=source_run_id,
            source_id=source_id,
            track=track,
            release_calendar_key=f"{source_id}:{track}",
            source_cycle_time=_dt(0),
            source_available_at=_dt(2),
            fetch_finished_at=late_possession,
            captured_at=late_possession,
            imported_at=late_possession,
            status="SUCCESS",
            completeness_status="COMPLETE",
            city_id="Shanghai",
            city_timezone="Asia/Shanghai",
            target_local_date=date(2026, 6, 7),
            temperature_metric="high",
            data_version="forecast_v2",
        )

    result = materialize_replacement_forecast_live(
        conn,
        _request(computed_at=_dt(4), expires_at=_dt(6)),
    )

    assert result.ok is True
    row = conn.execute(
        "SELECT source_available_at, computed_at FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    assert row["source_available_at"] == late_possession.isoformat()
    assert row["computed_at"] == late_possession.isoformat()


def test_materializer_blocks_day0_without_observed_extreme() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        ),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_DAY0_OBSERVED_EXTREME_REQUIRED",)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


def test_materializer_allows_typed_day0_zero_observation_full_day_posterior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.contracts.replacement_pipeline_files import (
        DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS,
    )

    conn = _conn()
    _install_live_fusion(monkeypatch)

    result = materialize_replacement_forecast_live(
        conn,
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observation_state=(
                DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS
            ),
        ),
    )

    assert result.ok is True
    row = conn.execute(
        """
        SELECT posterior_config_hash, provenance_json
        FROM forecast_posteriors
        WHERE posterior_id = ?
        """,
        (result.posterior_id,),
    ).fetchone()
    provenance = json.loads(row["provenance_json"])
    assert provenance["day0_observation_state"] == (
        DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS
    )
    assert "day0_conditioning" not in provenance
    assert "day0_provisional_observation" not in provenance
    assert row["posterior_config_hash"]


def test_materializer_rejects_conflicting_day0_zero_and_observed_extreme() -> None:
    from src.contracts.replacement_pipeline_files import (
        DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS,
    )

    conn = _conn()
    result = materialize_replacement_forecast_live(
        conn,
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=26.0,
            day0_observation_state=(
                DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS
            ),
        ),
    )

    assert result.ok is False
    assert (
        "REPLACEMENT_MATERIALIZATION_DAY0_ZERO_OBSERVATION_STATE_CONFLICT"
        in result.reason_codes
    )


def test_materializer_day0_observed_extreme_conditions_q_and_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)

    result = materialize_replacement_forecast_live(
        conn,
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=26.0,
            day0_observed_extreme_source="wu_api",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
            day0_observed_extreme_sample_count=12,
        ),
    )

    assert result.ok is True
    row = conn.execute(
        "SELECT q_json, q_lcb_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    q = json.loads(row["q_json"])
    q_lcb = json.loads(row["q_lcb_json"])
    provenance = json.loads(row["provenance_json"])
    assert q["cool"] == pytest.approx(0.0)
    assert q_lcb["cool"] == pytest.approx(0.0)
    assert q["warm"] > q["hot"]
    assert provenance["q_shape"] == "fused_day0_conditioned_normal"
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 26.0


def test_materializer_write_replaces_retracted_same_source_high(monkeypatch: pytest.MonkeyPatch) -> None:
    """A newer snapshot may retract its own HIGH without erasing independent evidence."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    awc = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=12,
    )
    old_wu = replace(
        awc,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=30.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 45).isoformat(),
        day0_observed_extreme_sample_count=10,
    )
    prepared = materializer_mod.prepare_replacement_forecast_live(conn, old_wu)
    assert isinstance(prepared, materializer_mod.PreparedReplacementForecastMaterialization)

    # The old WU worker computed from an earlier read snapshot. A delayed AWC
    # writer commits the stronger, still-causal HIGH31 before that worker owns
    # the writer lock. The writer rejects the stale payload; recomputation must
    # happen after its caller releases the lock.
    assert materialize_replacement_forecast_live(conn, awc).ok is True
    with pytest.raises(
        materializer_mod.PreparedReplacementForecastSnapshotStale
    ):
        materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    refreshed = materializer_mod.prepare_replacement_forecast_live(
        conn, prepared.request
    )
    assert isinstance(
        refreshed, materializer_mod.PreparedReplacementForecastMaterialization
    )
    result = materializer_mod.write_prepared_replacement_forecast_live(
        conn, refreshed
    )

    assert result.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 31.0
    assert provenance["day0_conditioning"]["source"] == "aviationweather_metar"
    assert provenance["day0_conditioning"]["observation_time"] == _dt(17, 55).isoformat()

    plateau = materialize_replacement_forecast_live(
        conn,
        replace(
            awc,
            computed_at=_dt(18, 15),
            day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
            day0_observed_extreme_sample_count=13,
        ),
    )
    assert plateau.ok is True
    plateau_provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (plateau.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert plateau_provenance["day0_conditioning"]["observed_extreme_c"] == 31.0
    assert plateau_provenance["day0_conditioning"]["observation_time"] == _dt(18, 5).isoformat()

    same_source_regression = materialize_replacement_forecast_live(
        conn,
        replace(
            awc,
            computed_at=_dt(18, 20),
            day0_observed_extreme_c=30.0,
            day0_observed_extreme_observation_time=_dt(18, 10).isoformat(),
            day0_observed_extreme_sample_count=14,
        ),
    )
    assert same_source_regression.ok is True
    regression_provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (same_source_regression.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert regression_provenance["day0_conditioning"]["observed_extreme_c"] == 30.0
    assert regression_provenance["day0_conditioning"]["observation_time"] == _dt(18, 10).isoformat()


def test_wu_newer_snapshot_retracts_stale_source_frontier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shenzhen antibody: WU 37 -> 36 must not leave the posterior pinned at 37."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    first = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=37.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=22,
    )
    assert materialize_replacement_forecast_live(conn, first).ok is True

    revised = materializer_mod._request_with_day0_physical_frontier(
        conn,
        replace(
            first,
            computed_at=_dt(18, 20),
            day0_observed_extreme_c=36.0,
            day0_observed_extreme_observation_time=_dt(18, 10).isoformat(),
            day0_observed_extreme_sample_count=24,
        ),
        metric="high",
    )

    assert isinstance(revised, ReplacementForecastMaterializeRequest)
    assert revised.day0_observed_extreme_c == 36.0
    assert revised.day0_observed_extreme_observation_time == _dt(18, 10).isoformat()


def test_materializer_readonly_replaces_retracted_same_source_low(monkeypatch: pytest.MonkeyPatch) -> None:
    """A newer snapshot may retract its own LOW without reopening other sources."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    awc = replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=19.0,
            day0_observed_extreme_source="aviationweather_metar",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
            day0_observed_extreme_sample_count=12,
        ),
        temperature_metric="low",
        baseline_data_version="ecmwf_opendata_mn2t3_local_calendar_day_min",
    )
    same_source_regression = replace(
        awc,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=20.0,
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        day0_observed_extreme_sample_count=13,
    )
    prepared = materializer_mod.prepare_replacement_forecast_live(
        conn,
        same_source_regression,
    )
    assert isinstance(
        prepared,
        materializer_mod.PreparedReplacementForecastMaterialization,
    )
    assert materialize_replacement_forecast_live(conn, awc).ok is True

    with pytest.raises(
        materializer_mod.PreparedReplacementForecastSnapshotStale
    ):
        materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    refreshed = materializer_mod.prepare_replacement_forecast_live(
        conn, prepared.request
    )
    assert isinstance(
        refreshed, materializer_mod.PreparedReplacementForecastMaterialization
    )
    write_result = materializer_mod.write_prepared_replacement_forecast_live(
        conn, refreshed
    )
    assert write_result.ok is True
    write_provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (write_result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert write_provenance["day0_conditioning"]["observed_extreme_c"] == 20.0
    assert write_provenance["day0_conditioning"]["observation_time"] == _dt(
        18, 5
    ).isoformat()

    old_wu = replace(
        awc,
        computed_at=_dt(18, 15),
        day0_observed_extreme_c=20.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 45).isoformat(),
        day0_observed_extreme_sample_count=10,
    )
    posterior = materializer_mod.compute_replacement_posterior_readonly(conn, old_wu)

    assert posterior is not None
    assert posterior.provenance_payload is not None
    assert posterior.provenance_payload["day0_conditioning"]["observed_extreme_c"] == 19.0
    assert posterior.provenance_payload["day0_conditioning"]["source"] == "aviationweather_metar"
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 2


def test_materializer_equal_frontier_uses_current_request_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retired carrier cannot ratchet its source/clock into every later posterior."""

    conn = _conn()
    _install_live_fusion(monkeypatch)
    prior = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=12,
    )
    assert materialize_replacement_forecast_live(conn, prior).ok is True

    current = replace(
        prior,
        computed_at=_dt(18, 10),
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 50).isoformat(),
        day0_observed_extreme_sample_count=10,
    )
    result = materialize_replacement_forecast_live(conn, current)

    assert result.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 31.0
    assert provenance["day0_conditioning"]["source"] == "wu_icao_history"
    assert provenance["day0_conditioning"]["observation_time"] == _dt(
        17, 50
    ).isoformat()


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "extreme"),
    [
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", 31.0),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", 19.0),
    ],
)
def test_materializer_blocks_future_day0_observation(
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    extreme: float,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    request = replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=extreme,
            day0_observed_extreme_source="aviationweather_metar",
            day0_observed_extreme_observation_time=_dt(18, 30).isoformat(),
            day0_observed_extreme_sample_count=12,
        ),
        temperature_metric=metric,
        baseline_data_version=baseline_data_version,
    )

    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_OBSERVATION_AFTER_COMPUTED_AT",
    )
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


def test_materializer_blocks_when_day0_frontier_ledger_read_fails() -> None:
    class BrokenFrontierLedger:
        def execute(self, sql, params):
            del sql, params
            raise sqlite3.DatabaseError("frontier ledger unavailable")

    request = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
    )

    result = materializer_mod._request_with_day0_physical_frontier(
        BrokenFrontierLedger(),
        request,
        metric="high",
    )

    assert isinstance(result, materializer_mod.ReplacementForecastMaterializeResult)
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_READ_FAILED",
    )


def test_materializer_blocks_malformed_day0_frontier_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    first = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
    )
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        ("{malformed", written.posterior_id),
    )

    result = materialize_replacement_forecast_live(
        conn,
        replace(
            first,
            computed_at=_dt(18, 10),
            day0_observed_extreme_c=30.0,
            day0_observed_extreme_source="wu_icao_history",
            day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        ),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "legacy_extreme", "current_extreme"),
    [
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", 31.0, 32.0),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", 19.0, 18.0),
    ],
)
def test_materializer_ignores_typed_legacy_provisional_frontier_ledger(
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    legacy_extreme: float,
    current_extreme: float,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    legacy = replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=legacy_extreme,
            day0_observed_extreme_source="aviationweather_metar",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        ),
        temperature_metric=metric,
        baseline_data_version=baseline_data_version,
    )
    written = materialize_replacement_forecast_live(conn, legacy)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    provenance["day0_conditioning"]["evidence_finality"] = (
        "PROVISIONAL_CURRENT_SNAPSHOT"
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    current = replace(
        legacy,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=current_extreme,
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
    )
    result = materialize_replacement_forecast_live(conn, current)

    assert result.ok is True
    current_provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert (
        current_provenance["day0_conditioning"]["observed_extreme_c"]
        == current_extreme
    )
    assert current_provenance["day0_conditioning"]["source"] == (
        "aviationweather_metar"
    )


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "malformation"),
    [
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", "missing"),
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", "nonfinite"),
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", "future"),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", "missing"),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", "nonfinite"),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", "future"),
    ],
)
def test_materializer_blocks_malformed_typed_provisional_frontier_ledger(
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    malformation: str,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    first = replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=31.0 if metric == "high" else 19.0,
            day0_observed_extreme_source="aviationweather_metar",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        ),
        temperature_metric=metric,
        baseline_data_version=baseline_data_version,
    )
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    conditioning = provenance["day0_conditioning"]
    conditioning["evidence_finality"] = "PROVISIONAL_CURRENT_SNAPSHOT"
    if malformation == "missing":
        del conditioning["observed_extreme_c"]
    elif malformation == "nonfinite":
        conditioning["observed_extreme_c"] = "nan"
    else:
        conditioning["observation_time"] = _dt(18, 5).isoformat()
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    result = materialize_replacement_forecast_live(
        conn,
        replace(
            first,
            computed_at=_dt(18, 10),
            day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        ),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )


@pytest.mark.parametrize(
    ("metric", "baseline_data_version"),
    [
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max"),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min"),
    ],
)
def test_materializer_blocks_unknown_frontier_finality(
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    first = replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=31.0 if metric == "high" else 19.0,
            day0_observed_extreme_source="aviationweather_metar",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        ),
        temperature_metric=metric,
        baseline_data_version=baseline_data_version,
    )
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    provenance["day0_conditioning"]["source"] = "unclassified_sensor"
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    result = materialize_replacement_forecast_live(
        conn,
        replace(
            first,
            computed_at=_dt(18, 10),
            day0_observed_extreme_c=32.0,
            day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        ),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "declared_finality"),
    [
        (
            "high",
            "ecmwf_opendata_mx2t3_local_calendar_day_max",
            "TYPO_OR_UNKNOWN_FINALITY",
        ),
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", "UNKNOWN"),
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max", None),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", ""),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", "UNKNOWN"),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min", 1),
    ],
)
def test_materializer_blocks_unknown_declared_frontier_finality(
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    declared_finality: object,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    first = replace(
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=31.0 if metric == "high" else 19.0,
            day0_observed_extreme_source="aviationweather_metar",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        ),
        temperature_metric=metric,
        baseline_data_version=baseline_data_version,
    )
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    provenance["day0_conditioning"]["evidence_finality"] = declared_finality
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    result = materialize_replacement_forecast_live(
        conn,
        replace(
            first,
            computed_at=_dt(18, 10),
            day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        ),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )


def test_materializer_blocks_ledger_observation_after_its_own_compute_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    first = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
    )
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    provenance["day0_conditioning"]["observation_time"] = _dt(18, 5).isoformat()
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    result = materialize_replacement_forecast_live(
        conn,
        replace(
            first,
            computed_at=_dt(18, 10),
            day0_observed_extreme_c=30.0,
            day0_observed_extreme_source="wu_icao_history",
            day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        ),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )


def test_materializer_ignores_malformed_pre_day0_frontier_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    pre_day0 = materialize_replacement_forecast_live(conn, _request())
    assert pre_day0.ok is True
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        ("{malformed", pre_day0.posterior_id),
    )

    day0 = materialize_replacement_forecast_live(
        conn,
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=31.0,
            day0_observed_extreme_source="aviationweather_metar",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        ),
    )

    assert day0.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (day0.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 31.0


def test_materializer_hko_provisional_observation_does_not_truncate_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    request = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="hko_hourly_accumulator",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=12,
    )
    result = materialize_replacement_forecast_live(
        conn,
        request,
    )

    assert result.ok is True
    row = conn.execute(
        "SELECT q_json, q_lcb_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    q = json.loads(row["q_json"])
    q_lcb = json.loads(row["q_lcb_json"])
    provenance = json.loads(row["provenance_json"])
    assert q["cool"] > 0.0
    assert q_lcb["cool"] >= 0.0
    assert provenance["day0_provisional_observation"]["support_truncation"] is False
    assert provenance["q_shape"] == "fused_normal_direct"
    assert "day0_conditioning" not in provenance
    assert provenance["day0_provisional_observation"] == {
        "active": True,
        "metric": "high",
        "observed_extreme_c": 26.0,
        "source": "hko_hourly_accumulator",
        "observation_time": _dt(17, 55).isoformat(),
        "sample_count": 12,
        "unit": "C",
        "support_truncation": False,
    }

    revised = materialize_replacement_forecast_live(
        conn,
        replace(
            request,
            computed_at=_dt(18, 10),
            day0_observed_extreme_c=25.7,
            day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        ),
    )
    assert revised.ok is True
    assert revised.posterior_id != result.posterior_id
    hashes = conn.execute(
        "SELECT posterior_config_hash FROM forecast_posteriors WHERE posterior_id IN (?, ?)",
        (result.posterior_id, revised.posterior_id),
    ).fetchall()
    assert len({row["posterior_config_hash"] for row in hashes}) == 2


def test_wu_fast_residual_is_provisional_while_direct_noaa_fast_is_absorbing() -> None:
    composite = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
    )
    direct = replace(
        composite,
        day0_observed_extreme_source="aviationweather_metar",
    )

    assert materializer_mod._day0_absorbing_observed_extreme_c(composite) is None
    assert materializer_mod._day0_absorbing_observed_extreme_c(direct) == 31.0


def test_materializer_day0_allows_elapsed_om9_hours_covered_by_observed_extreme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    request = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=2,
    )
    partial_request = replace(request, openmeteo_anchor=_anchor_with_local_hours(hours=range(2, 24)))

    result = materialize_replacement_forecast_live(conn, partial_request)

    assert result.ok is True
    assert "REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE" not in result.reason_codes


def test_materializer_day0_allows_post_localday_observation_to_cover_elapsed_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    request = _request(
        source_cycle_time=datetime(2026, 6, 7, 6, tzinfo=UTC),
        computed_at=datetime(2026, 6, 7, 17, tzinfo=UTC),
        expires_at=datetime(2026, 6, 8, 0, tzinfo=UTC),
        day0_observed_extreme_c=32.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=datetime(2026, 6, 7, 15, 0, tzinfo=UTC).isoformat(),
        day0_observed_extreme_sample_count=24,
    )
    partial_anchor = replace(
        _anchor_with_local_hours(hours=range(14, 24)),
        source_cycle_time=datetime(2026, 6, 7, 6, tzinfo=UTC),
    )
    partial_request = replace(request, openmeteo_anchor=partial_anchor)

    result = materialize_replacement_forecast_live(conn, partial_request)

    assert result.ok is True
    row = conn.execute(
        "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    provenance = json.loads(row["provenance_json"])
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 32.0
    assert provenance["day0_conditioning"]["sample_count"] == 24
    assert "REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE" not in result.reason_codes


def test_materializer_day0_blocks_om9_missing_future_hours_after_observed_extreme() -> None:
    request = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=2,
    )
    partial_request = replace(request, openmeteo_anchor=_anchor_with_local_hours(hours=range(10, 24)))

    result = materialize_replacement_forecast_live(_conn(), partial_request)

    assert result.ok is False
    assert "REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE" in result.reason_codes


def test_materializer_blocks_readiness_when_baseline_identity_is_wrong() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(baseline_data_version="wrong_baseline_data_version"),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_BASELINE_DATA_VERSION_MISMATCH",)
    assert result.posterior_id is None
    assert result.anchor_id is None
    assert result.readiness_id is None
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_materializer_preserves_openmeteo_artifact_lineage_without_aifs(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)

    result = materialize_replacement_forecast_live(conn, _request(anchor_artifact_id=11))

    assert result.ok is True
    anchor_row = conn.execute("SELECT artifact_id FROM deterministic_forecast_anchors WHERE anchor_id = ?", (result.anchor_id,)).fetchone()
    assert anchor_row["artifact_id"] == 11
    posterior_row = conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    assert "aifs_artifact_id" not in posterior_row["provenance_json"]
    assert '"openmeteo_anchor_artifact_id":11' in posterior_row["provenance_json"]
    readiness_row = conn.execute("SELECT dependency_json FROM readiness_state WHERE readiness_id = ?", (result.readiness_id,)).fetchone()
    assert '"artifact_id":22' not in readiness_row["dependency_json"]
    assert '"artifact_id":11' in readiness_row["dependency_json"]


def test_materializer_records_precision_guard_in_anchor_and_posterior_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)

    result = materialize_replacement_forecast_live(
        conn,
        _request(openmeteo_precision_guard=_precision_guard()),
    )

    assert result.ok is True
    anchor_row = conn.execute("SELECT provenance_json FROM deterministic_forecast_anchors WHERE anchor_id = ?", (result.anchor_id,)).fetchone()
    posterior_row = conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    anchor_provenance = json.loads(anchor_row["provenance_json"])
    posterior_provenance = json.loads(posterior_row["provenance_json"])
    assert anchor_provenance["precision_guard"]["status"] == "PASS"
    assert anchor_provenance["precision_guard"]["high_risk_bucket"] == "standard"
    assert posterior_provenance["openmeteo_precision_guard"]["reason_codes"] == ["OM9_PRECISION_METADATA_PASS"]


def test_materializer_blocks_when_precision_guard_missing_or_blocked() -> None:
    conn = _conn()

    missing = materialize_replacement_forecast_live(
        conn,
        _request(openmeteo_precision_guard=None),
    )

    assert missing.ok is False
    assert missing.reason_codes == ("OM9_PRECISION_GUARD_REQUIRED_FOR_MATERIALIZATION",)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0

    blocked = materialize_replacement_forecast_live(
        conn,
        _request(openmeteo_precision_guard=_precision_guard(endpoint_mode="daily_vendor_aggregated")),
    )

    assert blocked.ok is False
    assert "OM9_PRECISION_GUARD_NOT_LIVE_PASS" in blocked.reason_codes
    assert "OM9_ENDPOINT_MUST_BE_HOURLY_ZEUS_AGGREGATED" in blocked.reason_codes
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0


def test_materializer_blocks_future_dependency_before_writing_shadow_rows() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(openmeteo_source_available_at=_dt(5)),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_DEPENDENCY_AFTER_COMPUTED_AT",)
    assert result.posterior_id is None
    assert result.anchor_id is None
    assert result.readiness_id is None
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_materializer_requires_dependency_source_run_ids_before_writing_shadow_rows() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(openmeteo_source_run_id=""),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_OPENMETEO_SOURCE_RUN_ID_MISSING",)
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_materializer_posterior_available_at_includes_baseline_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)

    result = materialize_replacement_forecast_live(
        conn,
        _request(baseline_source_available_at=_dt(3, 30), openmeteo_source_available_at=_dt(3)),
    )

    assert result.ok is True
    posterior_row = conn.execute("SELECT source_available_at FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    assert posterior_row["source_available_at"] == _dt(3, 30).isoformat()


def test_materializer_blocks_expired_request_before_writing_shadow_rows() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(expires_at=_dt(4)),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_EXPIRY_NOT_AFTER_COMPUTED_AT",)
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


def test_materialize_script_template_requires_precision_metadata() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/materialize_replacement_forecast_live.py", "--print-template"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    template = json.loads(result.stdout)

    assert template["precision_metadata_json"] == "openmeteo_precision_metadata.json"


def test_materialize_script_attaches_world_observations_read_only(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db as state_db

    forecasts_path = tmp_path / "forecasts.db"
    world_path = tmp_path / "world.db"
    world = sqlite3.connect(world_path)
    world.execute("CREATE TABLE observation_prints (value_native REAL NOT NULL)")
    world.execute("INSERT INTO observation_prints VALUES (8.0)")
    world.commit()
    world.close()
    conn = sqlite3.connect(forecasts_path)
    monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_path)

    cli._attach_world_read_only(conn)

    assert conn.execute(
        "SELECT value_native FROM world.observation_prints"
    ).fetchone()[0] == 8.0
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO world.observation_prints VALUES (9.0)")
    conn.close()


def test_materializer_connection_skips_journal_bootstrap_behind_bulk_writer(
    tmp_path, monkeypatch
) -> None:
    import src.state.db as state_db

    forecast_path = tmp_path / "forecasts.db"
    bootstrap = sqlite3.connect(forecast_path)
    assert bootstrap.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    bootstrap.execute("CREATE TABLE source_rows (value INTEGER NOT NULL)")
    bootstrap.commit()
    bootstrap.execute("BEGIN IMMEDIATE")
    bootstrap.execute("INSERT INTO source_rows VALUES (1)")
    monkeypatch.setattr(state_db, "ZEUS_FORECASTS_DB_PATH", forecast_path)

    started = time.monotonic()
    materializer = (
        state_db.connect_existing_forecasts_db_without_journal_bootstrap()
    )
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 0.5
        assert materializer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        materializer.close()
        bootstrap.rollback()
        bootstrap.close()


def test_materialize_script_batch_reuses_connection_and_wakes_each_commit(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db as state_db

    inputs = [tmp_path / "a.json", tmp_path / "b.json"]
    calls = []
    lock_held = False

    class _Connection:
        closed = False

        def close(self):
            self.closed = True

    conn = _Connection()
    monkeypatch.setattr(
        state_db,
        "connect_existing_forecasts_db_without_journal_bootstrap",
        lambda: conn,
    )
    monkeypatch.setattr(cli, "_attach_world_read_only", lambda _conn: None)

    @contextmanager
    def _writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    monkeypatch.setattr(cli, "_forecast_writer_lock", _writer_lock)

    def _prepare(*_args, **_kwargs):
        assert lock_held is False
        with _kwargs["writer_lock"]():
            assert lock_held is True
        return cli._DurablePreparationReceipt(
            schema_ready=True,
            anchor_artifact_id=None,
            manifest_committed=False,
        )

    monkeypatch.setattr(
        cli,
        "_prepare_live_schema_and_manifest",
        _prepare,
    )

    def _run_one(input_json, **kwargs):
        assert lock_held is False
        with kwargs["writer_lock"]():
            assert lock_held is True
        calls.append((input_json, kwargs))
        return 0, json.dumps(
            {
                "status": "READY",
                "committed": True,
                "posterior_id": len(calls),
                "reactor_wake_published": kwargs["publish_wake"],
            }
        ) + "\n", ""

    monkeypatch.setattr(cli, "_run_one", _run_one)
    rc = cli.main(
        [
            "--batch-input-json",
            *(str(path) for path in inputs),
            "--commit",
        ]
    )

    assert rc == 0
    assert conn.closed is True
    assert [call[0] for call in calls] == inputs
    assert all(call[1]["conn"] is conn for call in calls)
    assert all(call[1]["commit"] is True for call in calls)
    assert all(call[1]["publish_wake"] is True for call in calls)
    assert all(call[1]["schema_ready"] is True for call in calls)
    assert all(call[1]["init_schema"] is False for call in calls)
    envelopes = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
    ]
    assert [Path(envelope["input_json"]) for envelope in envelopes] == inputs
    assert [envelope["returncode"] for envelope in envelopes] == [0, 0]


def test_materialize_script_batch_prepares_schema_before_first_input_error(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db as state_db

    inputs = [tmp_path / "malformed.json", tmp_path / "valid.json"]
    calls = []
    preparations = []

    class _Connection:
        def close(self):
            return None

    conn = _Connection()
    monkeypatch.setattr(
        state_db,
        "connect_existing_forecasts_db_without_journal_bootstrap",
        lambda: conn,
    )
    monkeypatch.setattr(cli, "_attach_world_read_only", lambda _conn: None)

    @contextmanager
    def _writer_lock():
        yield

    monkeypatch.setattr(cli, "_forecast_writer_lock", _writer_lock)

    def _prepare(*args, **kwargs):
        preparations.append(kwargs)
        return cli._DurablePreparationReceipt(
            schema_ready=True,
            anchor_artifact_id=None,
            manifest_committed=False,
        )

    def _run_one(input_json, **kwargs):
        calls.append((input_json, kwargs))
        if input_json == inputs[0]:
            return 2, "", '{"status":"ERROR"}\n'
        return 0, '{"status":"READY"}\n', ""

    monkeypatch.setattr(cli, "_prepare_live_schema_and_manifest", _prepare)
    monkeypatch.setattr(cli, "_run_one", _run_one)

    rc = cli.main(
        [
            "--batch-input-json",
            *(str(path) for path in inputs),
            "--commit",
            "--init-schema",
        ]
    )

    assert rc == 0
    assert len(preparations) == 1
    assert preparations[0]["init_schema"] is True
    assert [call[0] for call in calls] == inputs
    assert all(call[1]["schema_ready"] is True for call in calls)
    assert all(call[1]["init_schema"] is False for call in calls)
    envelopes = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [envelope["returncode"] for envelope in envelopes] == [2, 0]


def test_materialize_manifest_persistence_does_not_verify_files_under_lock(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    calls = []

    def _write_manifest(_conn, manifest, **kwargs):
        calls.append((manifest, kwargs, _conn.in_transaction))
        return 17

    manifest = object()
    monkeypatch.setattr(cli, "write_manifest_to_db", _write_manifest)
    receipt = cli._prepare_live_schema_and_manifest(
        conn,
        init_schema=False,
        schema_ready=True,
        openmeteo_manifest=manifest,
        anchor_artifact_id=None,
    )
    conn.close()

    assert calls == [
        (
            manifest,
            {"root": cli.ROOT, "verify_artifact": False},
            True,
        )
    ]
    assert receipt.anchor_artifact_id == 17
    assert receipt.manifest_committed is True


def test_materialize_script_publishes_family_wake_after_commit(monkeypatch) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    from src.runtime import reactor_wake

    published = []
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **kwargs: published.append(kwargs)
        or SimpleNamespace(wake_id="wake-1"),
    )
    request = _request()

    assert cli._publish_materialization_wake(request) is True
    assert published == [
        {
            "source": "replacement_forecast_materializer",
            "reason": "forecast_posterior_advanced",
            "forecast_families": (
                (
                    request.city,
                    request.target_date.isoformat(),
                    request.temperature_metric,
                ),
            ),
        }
    ]


def test_materialize_script_initial_compute_precedes_writer_lock(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE frontier (value INTEGER NOT NULL)")
    conn.commit()
    trace: list[str] = []
    conn.set_trace_callback(trace.append)
    prepared = object()
    prepare_calls = 0
    witness_lock_states = []

    lock_held = False

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    def prepare(_conn, _request):
        nonlocal prepare_calls
        assert lock_held is False
        prepare_calls += 1
        return prepared

    def witness(_conn, value):
        assert value is prepared
        witness_lock_states.append(lock_held)
        return "stable-target"

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", prepare)
    monkeypatch.setattr(cli, "_target_dependency_witness", witness)
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda conn, value, _baseline: witness(conn, value),
    )
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda _conn, value: (
            materializer_mod.ReplacementForecastMaterializeResult(
                status="READY",
                reason_codes=(),
                posterior_id=1,
                anchor_id=1,
                readiness_id="ready-1",
            )
            if lock_held and value is prepared
            else pytest.fail("write occurred without the writer lock")
        ),
    )

    result = cli._commit_from_read_snapshot(
        conn,
        SimpleNamespace(
            city="London",
            target_date=date(2026, 7, 19),
            temperature_metric="high",
        ),
        writer_lock=writer_lock,
    )
    conn.close()

    statements = [statement.upper() for statement in trace]
    assert result.ok is True
    assert prepare_calls == 1
    assert witness_lock_states == [False, True]
    assert statements.index("BEGIN") < statements.index("ROLLBACK")
    assert statements.index("ROLLBACK") < statements.index("BEGIN IMMEDIATE")


def test_materialize_script_releases_live_flock_while_sqlite_writer_is_busy(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    holder = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("CREATE TABLE frontier (value INTEGER NOT NULL)")
    reader.commit()
    reader.execute("PRAGMA busy_timeout = 4321")
    holder.execute("BEGIN IMMEDIATE")
    prepared = object()
    lock_held = False
    lock_durations: list[float] = []

    monkeypatch.setattr(cli, "_IMMEDIATE_BUSY_TIMEOUT_MS", 1)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_LIMIT", 3)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", lambda *_args: prepared)
    monkeypatch.setattr(cli, "_target_dependency_witness", lambda *_args: "stable")
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda *_args: "stable",
    )
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: _ready_materialization_result(),
    )

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        started = time.monotonic()
        try:
            yield
        finally:
            lock_durations.append(time.monotonic() - started)
            lock_held = False
            if len(lock_durations) == 1:
                holder.rollback()

    result = cli._commit_from_read_snapshot(
        reader,
        SimpleNamespace(
            city="London",
            target_date=date(2026, 7, 19),
            temperature_metric="high",
        ),
        writer_lock=writer_lock,
    )

    assert result.ok is True
    assert len(lock_durations) == 2
    assert lock_durations[0] < 0.1
    assert reader.execute("PRAGMA busy_timeout").fetchone()[0] == 4321
    reader.close()
    holder.close()


def test_materialize_schema_prelude_releases_live_flock_on_sqlite_contention(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    holder = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.commit()
    reader.execute("PRAGMA busy_timeout = 7654")
    holder.execute("BEGIN IMMEDIATE")
    lock_durations: list[float] = []
    manifest = object()

    monkeypatch.setattr(cli, "_IMMEDIATE_BUSY_TIMEOUT_MS", 1)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_LIMIT", 3)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(cli, "write_manifest_to_db", lambda *_args, **_kwargs: 17)

    @contextmanager
    def writer_lock():
        started = time.monotonic()
        try:
            yield
        finally:
            lock_durations.append(time.monotonic() - started)
            if len(lock_durations) == 1:
                holder.rollback()

    receipt = cli._prepare_live_schema_and_manifest(
        reader,
        init_schema=False,
        schema_ready=True,
        openmeteo_manifest=manifest,
        anchor_artifact_id=None,
        writer_lock=writer_lock,
    )

    assert receipt.anchor_artifact_id == 17
    assert len(lock_durations) == 2
    assert lock_durations[0] < 0.1
    assert reader.execute("PRAGMA busy_timeout").fetchone()[0] == 7654
    reader.close()
    holder.close()


def test_materialize_writer_contention_exhaustion_is_retryable(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    holder = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.commit()
    reader.execute("PRAGMA busy_timeout = 9876")
    holder.execute("BEGIN IMMEDIATE")
    lock_calls = 0

    monkeypatch.setattr(cli, "_IMMEDIATE_BUSY_TIMEOUT_MS", 1)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_LIMIT", 2)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_DELAY_SECONDS", 0.0)

    @contextmanager
    def writer_lock():
        nonlocal lock_calls
        lock_calls += 1
        yield

    with pytest.raises(
        cli.ReplacementForecastWriteDeferred,
        match="^REPLACEMENT_FORECAST_WRITE_DEFERRED$",
    ) as raised:
        with cli._immediate_writer_transaction(reader, writer_lock):
            pytest.fail("busy SQLite writer must prevent transaction entry")

    response = cli._error_response(raised.value)
    assert lock_calls == 2
    assert reader.in_transaction is False
    assert reader.execute("PRAGMA busy_timeout").fetchone()[0] == 9876
    assert response["reason_codes"] == ["REPLACEMENT_FORECAST_WRITE_DEFERRED"]
    holder.rollback()
    reader.close()
    holder.close()


def test_forecast_writer_lock_never_blocks_outside_bounded_retry(monkeypatch) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db_writer_lock as lock_mod
    from src.state.db import ZEUS_FORECASTS_DB_PATH

    observed: dict[str, object] = {}

    @contextmanager
    def nonblocking_lock(db_path, write_class, *, blocking=True):
        observed.update(
            db_path=db_path,
            write_class=write_class,
            blocking=blocking,
        )
        yield

    monkeypatch.setattr(lock_mod, "db_writer_lock", nonblocking_lock)

    with cli._forecast_writer_lock():
        pass

    assert observed["db_path"] == ZEUS_FORECASTS_DB_PATH
    assert observed["write_class"] is lock_mod.WriteClass.LIVE
    assert observed["blocking"] is False


def test_materialize_transaction_body_busy_is_retryable() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA busy_timeout = 2468")

    with pytest.raises(
        cli.ReplacementForecastWriteDeferred,
        match="^REPLACEMENT_FORECAST_WRITE_DEFERRED$",
    ):
        with cli._immediate_writer_transaction(conn, nullcontext):
            raise sqlite3.OperationalError("database is locked during commit")

    assert conn.in_transaction is False
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 2468
    conn.close()


def test_materialize_transaction_body_blocking_error_is_not_lock_contention() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    lock_calls = 0

    @contextmanager
    def writer_lock():
        nonlocal lock_calls
        lock_calls += 1
        yield

    with pytest.raises(BlockingIOError, match="permanent body error"):
        with cli._immediate_writer_transaction(conn, writer_lock):
            raise BlockingIOError("permanent body error")

    assert lock_calls == 1
    assert conn.in_transaction is False
    conn.close()


def test_materialize_script_recomputes_when_snapshot_changes(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    writer = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("CREATE TABLE frontier (value INTEGER NOT NULL)")
    reader.commit()
    prepare_calls = []
    written_values: list[int] = []
    changed = False

    def _prepare(_conn, _request):
        prepare_calls.append(True)
        return object()

    def _witness(conn, _prepared):
        return int(conn.execute("SELECT COUNT(*) FROM frontier").fetchone()[0])

    @contextmanager
    def _writer_lock():
        nonlocal changed
        if not changed:
            writer.execute("INSERT INTO frontier (value) VALUES (1)")
            writer.commit()
            changed = True
        yield

    def _write(conn, _value):
        written_values.append(
            int(conn.execute("SELECT COUNT(*) FROM frontier").fetchone()[0])
        )
        return materializer_mod.ReplacementForecastMaterializeResult(
            status="READY",
            reason_codes=(),
            posterior_id=1,
            anchor_id=1,
            readiness_id="ready-1",
        )

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(cli, "_target_dependency_witness", _witness)
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda conn, value, _baseline: _witness(conn, value),
    )
    monkeypatch.setattr(cli, "write_prepared_replacement_forecast_live", _write)

    result = cli._commit_from_read_snapshot(reader, SimpleNamespace(
        city="London",
        target_date=date(2026, 7, 19),
        temperature_metric="high",
    ), writer_lock=_writer_lock)
    reader.close()
    writer.close()

    assert result.ok is True
    assert len(prepare_calls) == 2
    assert written_values == [1]


def _prepared_target_frontier(marker: object):
    request = _request(anchor_artifact_id=17)
    return materializer_mod.PreparedReplacementForecastMaterialization(
        request=request,
        metric="high",
        day0_ledger_frontier_identity=None,
        posterior=SimpleNamespace(
            live_eligible=True,
            dependency_payload={
                "baseline_b0": request.baseline_source_run_id,
                "openmeteo_ifs9_anchor": request.openmeteo_source_run_id,
                "current_ensemble_snapshot": marker,
            },
            dependency_hash=f"dependency-{marker}",
            source_cycle_time=request.source_cycle_time.isoformat(),
            available_at=request.openmeteo_source_available_at.isoformat(),
            posterior_config_hash="posterior-config",
            provenance_payload={
                "bayes_precision_fusion": {
                    "raw_model_forecast_ids": [marker],
                    "current_value_serving": {
                        "ecmwf_ifs9": {"raw_model_forecast_id": marker}
                    },
                    "current_evidence_shape": {
                        "snapshot_id": marker,
                        "shape_hash": f"shape-{marker}",
                    },
                }
            },
        ),
    )


@pytest.mark.parametrize(
    "fusion",
    (
        None,
        {},
        {"current_value_serving": {}},
        {"current_value_serving": {"ecmwf_ifs9": {}}},
        {"current_value_serving": {"ecmwf_ifs9": {"raw_model_forecast_id": "bad"}}},
        {"current_value_serving": {"ecmwf_ifs9": {"raw_model_forecast_id": 1.5}}},
        {"current_value_serving": {"ecmwf_ifs9": {"raw_model_forecast_id": True}}},
    ),
)
def test_target_witness_allows_live_ineligible_missing_serving_provenance(
    fusion,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    prepared.posterior.live_eligible = False
    prepared.posterior.provenance_payload = (
        {} if fusion is None else {"bayes_precision_fusion": fusion}
    )

    witness = cli._target_dependency_witness(conn, prepared)
    conn.close()

    assert witness.prepared_provider_row_ids == ()
    assert witness.prepared_snapshot_id is None


@pytest.mark.parametrize(
    "fusion",
    (
        None,
        {},
        {"current_value_serving": {}},
        {"current_value_serving": {"ecmwf_ifs9": {}}},
        {"current_value_serving": {"ecmwf_ifs9": {"raw_model_forecast_id": "bad"}}},
        {"current_value_serving": {"ecmwf_ifs9": {"raw_model_forecast_id": 1.5}}},
        {"current_value_serving": {"ecmwf_ifs9": {"raw_model_forecast_id": True}}},
    ),
)
def test_target_witness_rejects_live_eligible_missing_serving_provenance(
    fusion,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    prepared.posterior.provenance_payload = (
        {} if fusion is None else {"bayes_precision_fusion": fusion}
    )

    with pytest.raises(
        cli._TargetDependencyWitnessUnavailable,
        match="current value serving witness unavailable",
    ):
        cli._target_dependency_witness(conn, prepared)
    conn.close()


def test_commit_returns_typed_blocked_for_ineligible_missing_serving(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    prepared.posterior.live_eligible = False
    prepared.posterior.provenance_payload = {}
    blocked = materializer_mod.ReplacementForecastMaterializeResult(
        status="BLOCKED",
        reason_codes=("PREDICTIVE_SIGMA:MISSING",),
        posterior_id=None,
        anchor_id=17,
        readiness_id=None,
    )
    monkeypatch.setattr(
        cli, "prepare_replacement_forecast_live", lambda *_args: prepared
    )
    monkeypatch.setattr(
        cli, "write_prepared_replacement_forecast_live", lambda *_args: blocked
    )

    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=nullcontext
    )
    conn.close()

    assert result == blocked


def _ready_materialization_result():
    return materializer_mod.ReplacementForecastMaterializeResult(
        status="READY",
        reason_codes=(),
        posterior_id=1,
        anchor_id=1,
        readiness_id="ready-1",
    )


def _blocked_materialization_result():
    return materializer_mod.ReplacementForecastMaterializeResult(
        status="BLOCKED",
        reason_codes=("TARGET_DEPENDENCY_UNAVAILABLE",),
        posterior_id=None,
        anchor_id=None,
        readiness_id=None,
    )


def _create_target_frontier_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT, track TEXT, release_calendar_key TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT, fetch_finished_at TEXT, captured_at TEXT,
            imported_at TEXT,
            expected_count INTEGER, observed_count INTEGER,
            completeness_status TEXT, partial_run INTEGER, raw_payload_hash TEXT,
            manifest_hash TEXT, status TEXT, reason_code TEXT
        );
        CREATE TABLE source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT, source_id TEXT, release_calendar_key TEXT,
            track TEXT, city TEXT, target_local_date TEXT,
            temperature_metric TEXT, expected_members INTEGER,
            observed_members INTEGER, expected_steps_json TEXT,
            observed_steps_json TEXT, snapshot_ids_json TEXT,
            completeness_status TEXT, readiness_status TEXT,
            computed_at TEXT, expires_at TEXT, recorded_at TEXT
        );
        CREATE INDEX idx_source_run_coverage_test_run
            ON source_run_coverage(source_run_id, city, target_local_date,
                                   temperature_metric);
        CREATE TABLE raw_forecast_artifacts (
            artifact_id INTEGER PRIMARY KEY,
            source_id TEXT, product_id TEXT, data_version TEXT,
            source_cycle_time TEXT, source_available_at TEXT, captured_at TEXT,
            sha256 TEXT, byte_size INTEGER
        );
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY,
            model TEXT, city TEXT, target_date TEXT, metric TEXT,
            source_cycle_time TEXT, source_available_at TEXT, captured_at TEXT,
            lead_days INTEGER, forecast_value_c REAL, endpoint TEXT
        );
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, temperature_metric TEXT,
            source_id TEXT, model_version TEXT, authority TEXT,
            source_run_id TEXT,
            causality_status TEXT, boundary_ambiguous INTEGER,
            forecast_window_attribution_status TEXT,
            contributes_to_target_extrema INTEGER,
            source_cycle_time TEXT, issue_time TEXT,
            source_available_at TEXT, available_at TEXT,
            members_json TEXT, members_unit TEXT
        );
        CREATE TABLE unrelated_writer (value INTEGER);
        INSERT INTO source_run VALUES (
            'b0-run', 'ecmwf_open_data', 'mx2t6_high',
            'ecmwf_open_data:mx2t6_high:short',
            '2026-06-06T00:00:00+00:00', '2026-06-06T02:00:00+00:00',
            '2026-06-06T02:00:00+00:00', '2026-06-06T02:00:00+00:00',
            '2026-06-06T02:00:00+00:00',
            51, 51, 'COMPLETE', 0,
            'b0-raw', 'b0-manifest', 'SUCCESS', NULL
        );
        INSERT INTO source_run VALUES (
            'om9-run', 'openmeteo', 'ifs9_high',
            'openmeteo:ifs9_high',
            '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '2026-06-06T03:00:00+00:00',
            1, 1, 'COMPLETE', 0,
            'om9-raw', 'om9-manifest', 'SUCCESS', NULL
        );
        INSERT INTO raw_forecast_artifacts VALUES (
            17, 'openmeteo', 'ifs9', 'anchor-v1',
            '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '2026-06-06T03:00:00+00:00', 'anchor-sha-a', 10
        );
        INSERT INTO raw_model_forecasts VALUES (
            101, 'ecmwf_ifs9', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '2026-06-06T03:00:00+00:00', 1, 27.0, 'single_runs'
        );
        INSERT INTO ensemble_snapshots VALUES (
            101, 'Shanghai', '2026-06-07', 'high',
            'ecmwf_open_data', 'ecmwf_ens', 'VERIFIED', 'b0-run', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
            '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '[20.0,21.0]', 'degC'
        );
        """
    )


def test_target_dependency_witness_is_bounded_to_exact_target_rows() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)

    baseline = cli._target_dependency_witness(conn, prepared)
    assert baseline.prepared_snapshot_id == 101
    assert baseline.prepared_shape_id == "shape-101"
    conn.execute("INSERT INTO unrelated_writer VALUES (1)")
    assert cli._target_dependency_witness(conn, prepared) == baseline

    conn.execute(
        "UPDATE raw_forecast_artifacts SET sha256 = 'anchor-sha-b' WHERE artifact_id = 17"
    )
    assert cli._target_dependency_witness(conn, prepared) != baseline
    conn.execute(
        "UPDATE raw_forecast_artifacts SET sha256 = 'anchor-sha-a' WHERE artifact_id = 17"
    )
    conn.execute("INSERT INTO unrelated_writer VALUES (2)")
    assert cli._revalidate_target_dependency_witness(
        conn, prepared, baseline
    ) == baseline

    conn.execute(
        """
        INSERT INTO raw_model_forecasts VALUES (
            102, 'ecmwf_ifs9', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T01:00:00+00:00', '2026-06-06T03:30:00+00:00',
            '2026-06-06T03:30:00+00:00', 1, 26.0, 'single_runs'
        )
        """
    )
    changed_provider = cli._revalidate_target_dependency_witness(
        conn, prepared, baseline
    )
    assert changed_provider != baseline
    assert changed_provider.provider_family_latest_id == 102
    conn.execute("DELETE FROM raw_model_forecasts WHERE raw_model_forecast_id = 102")

    conn.execute(
        """
        INSERT INTO ensemble_snapshots VALUES (
            102, 'Shanghai', '2026-06-07', 'high',
            'ecmwf_open_data', 'ecmwf_ens', 'VERIFIED', 'b0-run', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
            '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:30:00+00:00', '2026-06-06T03:30:00+00:00',
            '[19.0,22.0]', 'degC'
        )
        """
    )
    with pytest.raises(cli._TargetDependencyWitnessUnavailable):
        cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    conn.close()


def test_materialize_script_ignores_unrelated_data_version_changes(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    writer = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    _create_target_frontier_tables(reader)
    reader.commit()
    prepared = _prepared_target_frontier(101)
    prepare_calls = []
    written = []
    initial_data_version = int(reader.execute("PRAGMA data_version").fetchone()[0])
    lock_entries = 0

    @contextmanager
    def writer_lock():
        nonlocal lock_entries
        lock_entries += 1
        if lock_entries == 1:
            writer.execute("INSERT INTO unrelated_writer (value) VALUES (1)")
            writer.commit()
        yield

    def _prepare(_conn, _request):
        prepare_calls.append(True)
        return prepared

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda _conn, value: written.append(value) or _ready_materialization_result(),
    )

    result = cli._commit_from_read_snapshot(
        reader, prepared.request, writer_lock=writer_lock
    )
    current_data_version = int(reader.execute("PRAGMA data_version").fetchone()[0])
    reader.close()
    writer.close()

    assert result.ok is True
    assert current_data_version > initial_data_version
    assert len(prepare_calls) == 1
    assert lock_entries == 1
    assert written == [prepared]


@pytest.mark.parametrize(
    "kind",
    ("source_run_available_at", "source_run_disappears"),
)
def test_materialize_script_refuses_changed_or_missing_target_source_run(
    monkeypatch, kind: str
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    prepared = _prepared_target_frontier(101)
    prepare_results = iter((prepared, _blocked_materialization_result()))
    baseline_witness = ("present", "fetch-finished-a")
    changed_witness = (
        "missing" if kind == "source_run_disappears" else "present",
        None
        if kind == "source_run_disappears"
        else "fetch-finished-b",
    )
    prepare_calls = []
    writes = []

    def _prepare(*_args):
        prepare_calls.append(True)
        return next(prepare_results)

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(
        cli, "_target_dependency_witness", lambda *_args: baseline_witness
    )
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda *_args: changed_witness,
    )
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: writes.append(True) or _ready_materialization_result(),
    )

    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=nullcontext
    )
    conn.close()

    assert result.status == "BLOCKED"
    assert len(prepare_calls) == 2
    assert writes == []


def test_source_run_witness_distinguishes_missing_from_present_empty() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, fetch_finished_at TEXT)"
    )
    columns = cli._table_columns(conn, "source_run")
    missing_rows = cli._exact_rows_witness(
        conn,
        table="source_run",
        pk="source_run_id",
        ids=("run-1",),
        columns=columns,
    )
    conn.execute(
        "INSERT INTO source_run (source_run_id, fetch_finished_at) VALUES (?, NULL)",
        ("run-1",),
    )
    present_empty_rows = cli._exact_rows_witness(
        conn,
        table="source_run",
        pk="source_run_id",
        ids=("run-1",),
        columns=columns,
    )
    conn.execute(
        "UPDATE source_run SET fetch_finished_at = ? WHERE source_run_id = ?",
        ("2026-08-03T01:00:00+00:00", "run-1"),
    )
    present_rows = cli._exact_rows_witness(
        conn,
        table="source_run",
        pk="source_run_id",
        ids=("run-1",),
        columns=columns,
    )
    requested = (("run-1", "request-available"),)
    missing = cli._source_run_states(missing_rows, requested=requested)[0]
    present_empty = cli._source_run_states(
        present_empty_rows, requested=requested
    )[0]
    present = cli._source_run_states(present_rows, requested=requested)[0]
    conn.close()

    assert missing.state == "missing"
    assert present_empty.state == "present_empty"
    assert present.state == "present"
    assert present.fetch_finished_at == "2026-08-03T01:00:00+00:00"


def test_target_witness_detects_same_fetch_time_source_run_replacement() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    baseline = cli._target_dependency_witness(conn, prepared)
    conn.execute(
        """
        UPDATE source_run
           SET observed_count = 50,
               completeness_status = 'PARTIAL',
               raw_payload_hash = 'b0-replaced',
               status = 'PARTIAL',
               reason_code = 'ONE_MEMBER_MISSING'
         WHERE source_run_id = 'b0-run'
        """
    )

    with pytest.raises(cli._TargetDependencyWitnessUnavailable):
        cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    conn.close()


@pytest.mark.parametrize(
    "delete_sql,raises",
    (
        ("DELETE FROM source_run WHERE source_run_id = 'b0-run'", True),
        ("DELETE FROM raw_forecast_artifacts WHERE artifact_id = 17", True),
        ("DELETE FROM raw_model_forecasts WHERE raw_model_forecast_id = 101", True),
        ("DELETE FROM ensemble_snapshots WHERE snapshot_id = 101", True),
    ),
)
def test_target_witness_refuses_disappeared_exact_dependency(
    delete_sql: str, raises: bool
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    baseline = cli._target_dependency_witness(conn, prepared)
    conn.execute(delete_sql)

    if raises:
        with pytest.raises(cli._TargetDependencyWitnessUnavailable):
            cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    else:
        changed = cli._revalidate_target_dependency_witness(
            conn, prepared, baseline
        )
        assert changed != baseline
        assert changed.source_run_states[0].state == "missing"
    conn.close()


def test_shared_frontier_helpers_match_materializer_selectors() -> None:
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema,
        read_current_instrument_frontier_identity,
        read_current_instrument_values,
    )
    from src.data.replacement_forecast_materializer import (
        read_current_evidence_snapshot_id,
        read_current_evidence_snapshot_identity,
    )

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    request = prepared.request
    served = read_current_instrument_values(
        conn,
        city=request.city,
        metric=prepared.metric,
        target_date=request.target_date.isoformat(),
        source_cycle_time_iso=request.source_cycle_time.isoformat(),
        decision_time_iso=request.computed_at.isoformat(),
        include_station_sources=True,
    )
    bounded = read_current_instrument_frontier_identity(
        conn,
        city=request.city,
        metric=prepared.metric,
        target_date=request.target_date.isoformat(),
        decision_time_iso=request.computed_at.isoformat(),
        models=tuple(served),
        schema=current_value_serving_schema(conn),
    )
    snapshot = read_current_evidence_snapshot_identity(
        conn, request, metric=prepared.metric
    )
    snapshot_id = read_current_evidence_snapshot_id(
        conn, request, metric=prepared.metric
    )
    conn.close()

    assert bounded == tuple(
        sorted((model, value.raw_model_forecast_id) for model, value in served.items())
    )
    assert snapshot is not None
    assert snapshot_id == snapshot.snapshot_id == 101


def test_current_ensemble_accepts_only_exact_complete_target_window_from_partial_run() -> None:
    from src.data.replacement_forecast_materializer import (
        read_current_evidence_snapshot_identity,
    )
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    conn = _conn()
    _ensure_source_run_table(conn)
    _ensure_source_run_coverage_table(conn)
    request = _request()

    def write_run(
        *, status: str, completeness: str, partial: bool, imported_at: datetime
    ) -> None:
        observed_count = 3 if partial else 48
        write_source_run(
            conn,
            source_run_id="ens-run",
            source_id="ecmwf_open_data",
            track="mx2t6_high_short_horizon",
            release_calendar_key="ecmwf_open_data:mx2t6_high:short",
            source_cycle_time=_dt(0),
            source_available_at=_dt(2),
            fetch_finished_at=imported_at,
            captured_at=imported_at,
            imported_at=imported_at,
            status=status,
            completeness_status=completeness,
            partial_run=partial,
            expected_steps_json=list(range(3, 145, 3)),
            observed_steps_json=list(range(3, observed_count + 1, 3)),
            expected_count=48,
            observed_count=observed_count,
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        )

    write_run(status="PARTIAL", completeness="PARTIAL", partial=True, imported_at=_dt(3))
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric,
            physical_quantity, observation_field, issue_time, available_at,
            fetch_time, lead_hours, members_json, model_version, dataset_id,
            source_id, source_run_id, source_cycle_time, source_available_at,
            authority, causality_status, boundary_ambiguous,
            forecast_window_attribution_status, contributes_to_target_extrema,
            members_unit
        ) VALUES (
            101, 'Shanghai', '2026-06-07', 'high',
            'temperature_max', 'high_temp', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00', 24,
            '[20.0,21.0]', 'ecmwf_ens',
            'ecmwf_opendata_mx2t3_local_calendar_day_max', 'ecmwf_open_data',
            'ens-run', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:00:00+00:00', 'VERIFIED', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1, 'degC'
        )
        """
    )

    def selected() -> tuple[object | None, datetime | None]:
        return (
            read_current_evidence_snapshot_identity(conn, request, metric="high"),
            latest_eligible_ensemble_input_cycle(
                conn,
                city=request.city,
                target_date=request.target_date,
                metric="high",
                decision_time=request.computed_at,
            ),
        )

    assert selected() == (None, None)

    conn.execute(
        """
        INSERT INTO source_run_coverage VALUES (
            'coverage-1', 'ens-run', 'ecmwf_open_data',
            'ecmwf_open_data:mx2t6_high:short',
            'mx2t6_high_short_horizon', 'Shanghai', '2026-06-07', 'high',
            51, 51, '[0,3,6]', '[0,3,6]', '[101]',
            'COMPLETE', 'LIVE_ELIGIBLE',
            '2026-06-06T03:10:00+00:00',
            '2026-06-06T06:00:00+00:00',
            '2026-06-06T03:10:00+00:00'
        )
        """
    )
    identity, cycle = selected()
    assert identity is not None
    assert identity.snapshot_id == 101
    assert cycle == _dt(0)

    conn.execute(
        "UPDATE source_run_coverage SET observed_steps_json = '[0,3]'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET observed_steps_json = 'not-json'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET observed_steps_json = '[0,3,6]', "
        "expected_steps_json = '[]'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET expected_steps_json = '[0,3,6]', "
        "snapshot_ids_json = '[999]'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET snapshot_ids_json = '[101]'"
    )
    conn.execute(
        "UPDATE source_run_coverage SET recorded_at = '2026-06-06T05:00:00+00:00'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET recorded_at = '2026-06-06T03:10:00+00:00'"
    )

    write_run(
        status="SUCCESS",
        completeness="COMPLETE",
        partial=False,
        imported_at=_dt(3, 30),
    )
    identity, cycle = selected()
    assert identity is not None
    assert identity.snapshot_id == 101
    assert cycle == _dt(0)

    write_run(status="SUCCESS", completeness="COMPLETE", partial=False, imported_at=_dt(5))
    assert selected() == (None, None)
    conn.close()


def test_new_target_family_provider_changes_final_witness() -> None:
    """A newly eligible provider must invalidate the prepared family frontier."""
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    baseline = cli._target_dependency_witness(conn, prepared)
    conn.execute(
        """
        INSERT INTO raw_model_forecasts VALUES (
            104, 'gfs', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T01:00:00+00:00', '2026-06-06T03:30:00+00:00',
            '2026-06-06T03:30:00+00:00', 1, 26.0, 'single_runs'
        )
        """
    )

    current = cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    refreshed = cli._target_dependency_witness(conn, prepared)
    conn.close()

    assert current != baseline
    assert current.provider_family_latest_id == 104
    assert ("gfs", 104) in refreshed.provider_frontier
    assert "gfs" in refreshed.provider_models


def test_final_lock_uses_real_writer_without_revalidation_or_unbounded_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final lock may run bounded witnesses and the real target writer only."""
    import scripts.materialize_replacement_forecast_live as cli

    conn = _conn()
    _install_live_fusion(monkeypatch)
    prepared = _prepare_for_final_write(conn, _request())
    locked_sql: list[str] = []

    @contextmanager
    def writer_lock():
        conn.set_trace_callback(locked_sql.append)
        try:
            yield
        finally:
            conn.set_trace_callback(None)

    witness = object()
    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", lambda *_args: prepared)
    monkeypatch.setattr(cli, "_target_dependency_witness", lambda *_args: witness)
    monkeypatch.setattr(cli, "_revalidate_target_dependency_witness", lambda *_args: witness)
    monkeypatch.setattr(
        materializer_mod,
        "_validated_replacement_forecast_request",
        lambda *_args: pytest.fail("final writer must reuse the prepared validation"),
    )

    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=writer_lock
    )
    conn.close()

    assert result.ok is True
    assert not any("PRAGMA" in sql.upper() for sql in locked_sql)
    assert not any(
        "FORECAST_POSTERIORS" in sql.upper()
        and sql.lstrip().upper().startswith("SELECT")
        and "LIMIT" not in sql.upper()
        for sql in locked_sql
    )


def test_final_ens_frontier_preserves_production_casefold_fallback() -> None:
    from src.data.replacement_forecast_materializer import (
        read_current_evidence_snapshot_id,
        read_current_evidence_snapshot_identity,
    )

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    request = replace(prepared.request, city="shanghai")

    production = read_current_evidence_snapshot_identity(
        conn, request, metric=prepared.metric
    )
    assert production is not None
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    final_id = read_current_evidence_snapshot_id(
        conn,
        request,
        metric=prepared.metric,
    )
    conn.set_trace_callback(None)
    conn.close()

    assert production.snapshot_id == 101
    assert production.city == "Shanghai"
    assert final_id == production.snapshot_id
    final_sql = [
        sql
        for sql in traced
        if sql.lstrip().upper().startswith("SELECT")
        and "FROM ENSEMBLE_SNAPSHOTS" in sql.upper()
    ]
    assert final_sql
    assert len(final_sql) == 2
    assert "CITY = 'SHANGHAI'" in final_sql[0].upper()
    assert "LOWER(CITY) = LOWER('SHANGHAI')" in final_sql[1].upper()


def test_final_ens_frontier_detects_absent_to_present() -> None:
    """A prepared missing ENS identity must not become a permanent final gate."""
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    conn.execute("DELETE FROM ensemble_snapshots")
    prepared = _prepared_target_frontier(101)
    prepared.posterior.provenance_payload["bayes_precision_fusion"][
        "current_evidence_shape"
    ] = {"snapshot_id": None, "shape_hash": None}
    baseline = cli._target_dependency_witness(conn, prepared)
    assert baseline.ensemble_identity is None
    conn.execute(
        """
        INSERT INTO ensemble_snapshots VALUES (
            102, 'Shanghai', '2026-06-07', 'high',
            'ecmwf_open_data', 'ecmwf_ens', 'VERIFIED', 'b0-run', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
            '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:30:00+00:00', '2026-06-06T03:30:00+00:00',
            '[19.0,22.0]', 'degC'
        )
        """
    )

    current = cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    conn.close()

    assert current != baseline
    assert current.ensemble_frontier_id == 102


def test_final_ens_frontier_exact_city_update_supersedes_casefold() -> None:
    """Final selection must preserve production exact-first semantics after UPDATE."""
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    conn.execute("UPDATE raw_model_forecasts SET city = 'shanghai'")
    prepared = _prepared_target_frontier(101)
    prepared = replace(prepared, request=replace(prepared.request, city="shanghai"))
    conn.execute(
        """
        INSERT INTO ensemble_snapshots VALUES (
            102, 'shanghai', '2026-06-07', 'high',
            'ecmwf_open_data', 'ecmwf_ens', 'UNVERIFIED', 'b0-run', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
            '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:30:00+00:00', '2026-06-06T03:30:00+00:00',
            '[19.0,22.0]', 'degC'
        )
        """
    )
    baseline = cli._target_dependency_witness(conn, prepared)
    assert baseline.ensemble_identity is not None
    assert baseline.ensemble_identity.city == "Shanghai"
    conn.execute(
        "UPDATE ensemble_snapshots SET authority = 'VERIFIED' WHERE snapshot_id = 102"
    )

    with pytest.raises(cli._TargetDependencyWitnessUnavailable):
        cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    current_id = materializer_mod.read_current_evidence_snapshot_id(
        conn, prepared.request, metric=prepared.metric
    )
    conn.close()

    assert current_id == 102


def test_existing_frontier_indexes_require_no_live_ddl() -> None:
    """A normal posterior write must not wait on an already-complete schema."""

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    _ensure_replacement_frontier_indexes(conn)

    def deny_index_ddl(
        action: int,
        _arg1: str | None,
        _arg2: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_CREATE_INDEX:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(deny_index_ddl)
    try:
        _ensure_replacement_frontier_indexes(conn)
    finally:
        conn.close()


def test_final_ens_selector_has_indexed_logarithmic_work() -> None:
    """Canonical exact/folded target selectors must not scan their target range."""
    from src.data.replacement_forecast_materializer import (
        _ensure_replacement_frontier_indexes,
        read_current_evidence_snapshot_id,
    )

    samples: list[tuple[int, int, int]] = []
    captured_plans: list[tuple[str, ...]] = []
    for row_count in (10, 1_000, 10_000):
        conn = sqlite3.connect(":memory:")
        _create_target_frontier_tables(conn)
        _ensure_replacement_frontier_indexes(conn)
        conn.execute("DELETE FROM ensemble_snapshots")
        conn.executemany(
            """
            INSERT INTO ensemble_snapshots VALUES (
                ?, 'Shanghai', '2026-06-07', 'high',
                'ecmwf_open_data', 'ecmwf_ens', 'VERIFIED', 'b0-run', 'OK', 0,
                'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
                '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
                '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00',
                '[20.0,21.0]', 'degC'
            )
            """,
            ((snapshot_id,) for snapshot_id in range(1, row_count + 1)),
        )
        request = _prepared_target_frontier(row_count).request

        def measured(city: str) -> tuple[int | None, int, list[str]]:
            steps = 0
            traced: list[str] = []

            def count_step() -> int:
                nonlocal steps
                steps += 1
                return 0

            conn.set_trace_callback(traced.append)
            conn.set_progress_handler(count_step, 1)
            try:
                selected = read_current_evidence_snapshot_id(
                    conn, replace(request, city=city), metric="high"
                )
            finally:
                conn.set_progress_handler(None, 0)
                conn.set_trace_callback(None)
            return selected, steps, traced

        exact_id, exact_steps, exact_sql = measured("Shanghai")
        folded_id, folded_steps, folded_sql = measured("shanghai")
        assert exact_id == folded_id == row_count
        samples.append((row_count, exact_steps, folded_steps))
        if row_count == 10_000:
            selector_sql = [
                sql
                for sql in (*exact_sql, *folded_sql)
                if sql.lstrip().upper().startswith("SELECT")
                and "FROM ENSEMBLE_SNAPSHOTS" in sql.upper()
            ]
            captured_plans = [
                tuple(
                    str(row[3])
                    for row in conn.execute("EXPLAIN QUERY PLAN " + sql)
                )
                for sql in selector_sql
            ]
        conn.close()

    assert captured_plans
    plan_text = "\n".join(detail for plan in captured_plans for detail in plan).upper()
    assert "IDX_ENSEMBLE_SNAPSHOTS_REPLACEMENT_EXACT_FRONTIER" in plan_text
    assert "IDX_ENSEMBLE_SNAPSHOTS_REPLACEMENT_CASEFOLD_FRONTIER" in plan_text
    assert "USE TEMP B-TREE" not in plan_text
    assert not any(
        detail.upper().startswith("SCAN ")
        for plan in captured_plans
        for detail in plan
    )
    assert samples[-1][1] < samples[0][1] * 2
    assert samples[-1][2] < samples[0][2] * 2


def test_provider_frontier_skips_invalid_rows_like_production_selector() -> None:
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema,
        read_current_instrument_frontier_identity,
        read_current_instrument_values,
    )

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    conn.executemany(
        """
        INSERT INTO raw_model_forecasts VALUES (
            ?, 'ecmwf_ifs9', 'Shanghai', '2026-06-07', 'high',
            ?, '2026-06-06T03:30:00+00:00', '2026-06-06T03:30:00+00:00',
            ?, ?, 'single_runs'
        )
        """,
        (
            (102, "2026-06-06T01:00:00+00:00", 1, float("inf"),),
            (103, "2026-06-06T02:00:00+00:00", "bad-lead", 28.0),
        ),
    )
    served = read_current_instrument_values(
        conn,
        city="Shanghai",
        metric="high",
        target_date="2026-06-07",
        source_cycle_time_iso="2026-06-06T00:00:00+00:00",
        decision_time_iso="2026-06-06T04:00:00+00:00",
        include_station_sources=True,
    )
    frontier = read_current_instrument_frontier_identity(
        conn,
        city="Shanghai",
        metric="high",
        target_date="2026-06-07",
        decision_time_iso="2026-06-06T04:00:00+00:00",
        models=("ecmwf_ifs9",),
        schema=current_value_serving_schema(conn),
    )
    conn.close()

    assert served["ecmwf_ifs9"].raw_model_forecast_id == 101
    assert frontier == (("ecmwf_ifs9", 101),)


def test_day0_final_writer_uses_frozen_frontier_without_likelihood_recompute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real final writer compares Day0 identity without rebuilding fast likelihood."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    absorbing = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=30.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
    )
    assert materialize_replacement_forecast_live(conn, absorbing).ok is True
    conn.commit()
    provisional = replace(
        absorbing,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
    )
    likelihood = SimpleNamespace(
        residual_weights_c=((0.0, 1.0),),
        unknown_weight=0.0,
        settlement_extreme_c=30.0,
        identity_hash="4" * 64,
        as_payload=lambda: {
            "identity_hash": "4" * 64,
            "settlement_extreme_c": 30.0,
        },
    )
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *_args, **_kwargs: likelihood,
    )
    prepared = _prepare_for_final_write(conn, provisional)
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *_args, **_kwargs: pytest.fail(
            "final writer must not rebuild Day0 likelihood"
        ),
    )
    locked_sql: list[str] = []
    conn.set_trace_callback(locked_sql.append)
    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()
    conn.set_trace_callback(None)
    conn.close()

    assert result.ok is True
    final_selects = [
        sql for sql in locked_sql if sql.lstrip().upper().startswith("SELECT")
    ]
    assert not any("OBSERVATION_PRINTS" in sql.upper() for sql in locked_sql)
    assert not any(
        "SELECT PROVENANCE_JSON, COMPUTED_AT" in sql.upper()
        and "FROM FORECAST_POSTERIORS" in sql.upper()
        for sql in final_selects
    )


def test_day0_ledger_frontier_allows_65_rows_and_retries_on_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Append-only Day0 history has no row-count ratchet; a real append stales prepare."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    request = _request(
        computed_at=_dt(18, 10),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=30.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
    )
    conditioning = json.dumps(
        {
            "day0_conditioning": {
                "active": True,
                "metric": "high",
                "observed_extreme_c": 30.0,
                "source": "wu_icao_history",
                "observation_time": _dt(16).isoformat(),
            }
        }
    )
    rows = [
        (
            materializer_mod.SOURCE_ID,
            materializer_mod.PRODUCT_ID,
            f"history-{index}",
            request.city,
            request.target_date.isoformat(),
            "high",
            request.source_cycle_time.isoformat(),
            request.openmeteo_source_available_at.isoformat(),
            _dt(17, index % 60).isoformat(),
            "{}",
            "history",
            conditioning,
        )
        for index in range(65)
    ]
    conn.executemany(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, posterior_method, provenance_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    prepared = _prepare_for_final_write(conn, request)

    conn.execute("BEGIN IMMEDIATE")
    first = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()
    assert first.ok is True

    stale = _prepare_for_final_write(conn, replace(request, computed_at=_dt(18, 20)))
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, posterior_method, provenance_json
        ) VALUES (?, ?, 'append', ?, ?, 'high', ?, ?, ?, '{}', 'history', ?)
        """,
        (
            materializer_mod.SOURCE_ID,
            materializer_mod.PRODUCT_ID,
            request.city,
            request.target_date.isoformat(),
            request.source_cycle_time.isoformat(),
            request.openmeteo_source_available_at.isoformat(),
            _dt(18, 15).isoformat(),
            conditioning,
        ),
    )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(materializer_mod.PreparedReplacementForecastSnapshotStale):
        materializer_mod.write_prepared_replacement_forecast_live(conn, stale)
    conn.rollback()
    refreshed = _prepare_for_final_write(
        conn, replace(request, computed_at=_dt(18, 20))
    )
    latest_id = conn.execute(
        "SELECT MAX(posterior_id) FROM forecast_posteriors"
    ).fetchone()[0]
    changed_conditioning = json.loads(conditioning)
    changed_conditioning["day0_conditioning"]["observed_extreme_c"] = 31.0
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(changed_conditioning), latest_id),
    )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(materializer_mod.PreparedReplacementForecastSnapshotStale):
        materializer_mod.write_prepared_replacement_forecast_live(conn, refreshed)
    conn.rollback()
    conn.close()


def test_final_frontier_queries_use_exact_target_indexes_without_temp_sort() -> None:
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema,
        read_current_instrument_family_latest_id,
        read_current_instrument_frontier_identity,
    )
    from src.data.replacement_forecast_materializer import (
        read_current_evidence_snapshot_id,
        read_current_evidence_snapshot_identity,
    )

    conn = _conn()
    _ensure_source_run_table(conn)
    _ensure_source_run_coverage_table(conn)
    write_source_run(
        conn,
        source_run_id="ens-run",
        source_id="ecmwf_open_data",
        track="mx2t6_high_short_horizon",
        release_calendar_key="ecmwf_open_data:mx2t6_high:short",
        source_cycle_time=_dt(0),
        source_available_at=_dt(3),
        fetch_finished_at=_dt(3),
        captured_at=_dt(3),
        imported_at=_dt(3),
        status="SUCCESS",
        completeness_status="COMPLETE",
        partial_run=False,
    )
    _ensure_replacement_identity_columns(conn)
    _ensure_replacement_frontier_indexes(conn)
    prepared = _prepared_target_frontier(101)
    request = prepared.request
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at, lead_days,
            forecast_value_c, endpoint
        ) VALUES (101, 'ecmwf_ifs9', 'Shanghai', '2026-06-07', 'high',
                  '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
                  '2026-06-06T03:00:00+00:00', 1, 27.0, 'single_runs')
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, available_at, fetch_time, lead_hours,
            members_json, model_version, dataset_id, source_id, source_cycle_time,
            source_available_at, source_run_id, forecast_window_attribution_status,
            contributes_to_target_extrema, causality_status, boundary_ambiguous,
            members_unit
        ) VALUES (101, 'Shanghai', '2026-06-07', 'high', 'temperature_max',
                  'high_temp', '2026-06-06T00:00:00+00:00',
                  '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00',
                  24, '[20.0,21.0]', 'ecmwf_ens', 'ens', 'ecmwf_open_data',
                  '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
                  'ens-run', 'FULLY_INSIDE_TARGET_LOCAL_DAY', 1, 'OK', 0, 'degC')
        """
    )
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    read_current_instrument_frontier_identity(
        conn,
        city=request.city,
        metric="high",
        target_date=request.target_date.isoformat(),
        decision_time_iso=request.computed_at.isoformat(),
        models=("ecmwf_ifs9",),
        schema=current_value_serving_schema(conn),
    )
    read_current_instrument_family_latest_id(
        conn,
        city=request.city,
        metric="high",
        target_date=request.target_date.isoformat(),
    )
    materializer_mod._day0_ledger_frontier_identity(conn, request, metric="high")
    identity = read_current_evidence_snapshot_identity(conn, request, metric="high")
    assert identity is not None
    read_current_evidence_snapshot_id(conn, request, metric="high")
    conn.set_trace_callback(None)
    frontier_sql = [
        sql
        for sql in traced
        if sql.lstrip().upper().startswith("SELECT")
        and (
            "FROM RAW_MODEL_FORECASTS" in sql.upper()
            or "FROM FORECAST_POSTERIORS" in sql.upper()
            or (
                "FROM ENSEMBLE_SNAPSHOTS" in sql.upper()
                and "ORDER BY COALESCE" not in sql.upper()
            )
        )
    ]
    plans = [
        tuple(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
        for sql in frontier_sql
    ]
    conn.close()

    assert plans
    assert all(
        not any("USE TEMP B-TREE" in detail.upper() for detail in plan)
        for plan in plans
    )
    assert any(
        any("IDX_RAW_MODEL_FORECASTS_TARGET_MODEL_FRONTIER" in detail.upper() for detail in plan)
        for plan in plans
    )
    assert any(
        any("IDX_FORECAST_POSTERIORS_SOURCE_FAMILY_FRONTIER" in detail.upper() for detail in plan)
        for plan in plans
    )
    assert all(
        not any(detail.upper().startswith("SCAN ") for detail in plan)
        for plan in plans
    )


def test_final_provider_witness_query_count_is_fixed_with_many_invalid_rows() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    conn.executescript(
        """
        CREATE INDEX idx_raw_model_forecasts_target_model_frontier
            ON raw_model_forecasts(
                city, target_date, metric, model,
                datetime(source_cycle_time) DESC,
                CASE endpoint WHEN 'single_runs' THEN 0 ELSE 1 END,
                lead_days, captured_at DESC, raw_model_forecast_id DESC
            );
        CREATE INDEX idx_raw_model_forecasts_target_frontier
            ON raw_model_forecasts(
                city, target_date, metric, raw_model_forecast_id DESC
            );
        """
    )
    conn.executemany(
        """
        INSERT INTO raw_model_forecasts VALUES (
            ?, 'ecmwf_ifs9', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T02:00:00+00:00', '2026-06-06T03:30:00+00:00',
            '2026-06-06T03:30:00+00:00', 'bad-lead', NULL, 'single_runs'
        )
        """,
        ((1000 + index,) for index in range(1000)),
    )
    prepared = _prepared_target_frontier(101)
    baseline = cli._target_dependency_witness(conn, prepared)
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    current = cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    conn.set_trace_callback(None)

    provider_selects = [
        sql
        for sql in traced
        if sql.lstrip().upper().startswith("SELECT")
        and "FROM RAW_MODEL_FORECASTS" in sql.upper()
    ]
    assert current == baseline
    assert len(provider_selects) == 2
    assert all("OFFSET" not in sql.upper() for sql in provider_selects)
    assert any("ORDER BY RAW_MODEL_FORECAST_ID DESC" in sql.upper() for sql in provider_selects)
    assert any("RAW_MODEL_FORECAST_ID IN" in sql.upper() for sql in provider_selects)
    conn.execute(
        """
        INSERT INTO raw_model_forecasts VALUES (
            3000, 'new_invalid', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T02:00:00+00:00', '2026-06-06T03:30:00+00:00',
            '2026-06-06T03:30:00+00:00', 'bad-lead', NULL, 'single_runs'
        )
        """
    )
    assert cli._revalidate_target_dependency_witness(
        conn, prepared, baseline
    ) != baseline
    conn.close()


def test_source_clock_production_selector_preserves_520_valid_rows() -> None:
    from src.data.replacement_current_value_serving import read_current_instrument_values

    conn = _conn()
    conn.executemany(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time,
            source_available_at, captured_at, lead_days, forecast_value_c, endpoint
        ) VALUES (?, 'Shanghai', '2026-06-07', 'high',
                  '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
                  '2026-06-06T03:00:00+00:00', 1, 27.0, 'single_runs')
        """,
        ((f"provider_{index:03d}",) for index in range(520)),
    )
    served = read_current_instrument_values(
        conn,
        city="Shanghai",
        metric="high",
        target_date="2026-06-07",
        source_cycle_time_iso="2026-06-06T00:00:00+00:00",
        decision_time_iso="2026-06-06T04:00:00+00:00",
        include_station_sources=True,
    )
    conn.close()

    assert len(served) == 520


def test_final_lock_reads_only_exact_ids_and_bounded_frontiers(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    lock_held = False
    locked_sql: list[str] = []

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        lock_held = True
        conn.set_trace_callback(locked_sql.append)
        try:
            yield
        finally:
            conn.set_trace_callback(None)
            lock_held = False

    def _prepare(*_args):
        assert lock_held is False
        return prepared

    def _write(*_args):
        assert lock_held is True
        return _ready_materialization_result()

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(cli, "write_prepared_replacement_forecast_live", _write)
    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=writer_lock
    )
    conn.close()

    selects = [sql for sql in locked_sql if sql.lstrip().upper().startswith("SELECT")]
    assert result.ok is True
    assert selects
    assert not any("PRAGMA" in sql.upper() for sql in locked_sql)
    assert any("SOURCE_RUN_ID IN" in sql.upper() for sql in selects)
    assert any("ARTIFACT_ID IN" in sql.upper() for sql in selects)
    assert any("RAW_MODEL_FORECAST_ID IN" in sql.upper() for sql in selects)
    assert any("SNAPSHOT_ID IN" in sql.upper() for sql in selects)
    frontier_selects = [
        sql
        for sql in selects
        if "ORDER BY RAW_MODEL_FORECAST_ID DESC" in sql.upper()
        or "SNAPSHOT_ID >" in sql.upper()
    ]
    assert frontier_selects
    assert all("LIMIT" in sql.upper() for sql in frontier_selects)
    assert not any("OFFSET" in sql.upper() for sql in selects)


def test_exact_target_supersession_retries_before_commit(monkeypatch) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    prepare_results = iter((prepared, _blocked_materialization_result()))
    prepare_calls = 0
    writes = []

    def _prepare(*_args):
        nonlocal prepare_calls
        prepare_calls += 1
        return next(prepare_results)

    @contextmanager
    def writer_lock():
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_model_forecasts VALUES (
                102, 'ecmwf_ifs9', 'Shanghai', '2026-06-07', 'high',
                '2026-06-06T01:00:00+00:00', '2026-06-06T03:30:00+00:00',
                '2026-06-06T03:30:00+00:00', 1, 26.0, 'single_runs'
            )
            """
        )
        conn.commit()
        yield

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: writes.append(True) or _ready_materialization_result(),
    )
    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=writer_lock
    )
    conn.close()

    assert result.status == "BLOCKED"
    assert prepare_calls == 2
    assert writes == []


def test_materialize_script_refuses_changed_anchor_or_provider_frontier(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    original = _prepared_target_frontier(101)
    prepare_results = iter((original, _blocked_materialization_result()))
    baseline_witness = "provider-row-101"
    writes = []
    monkeypatch.setattr(
        cli, "prepare_replacement_forecast_live", lambda *_args: next(prepare_results)
    )
    monkeypatch.setattr(
        cli,
        "_target_dependency_witness",
        lambda *_args: baseline_witness,
    )
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda *_args: "provider-row-102",
    )
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: writes.append(True) or _ready_materialization_result(),
    )

    result = cli._commit_from_read_snapshot(
        conn, original.request, writer_lock=nullcontext
    )
    conn.close()

    assert result.status == "BLOCKED"
    assert writes == []


def test_materialize_script_snapshot_retry_exhaustion_never_computes_under_writer_lock(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    prepare_calls = []
    witness_calls = []
    write_calls = []

    def _prepare(_conn, _request):
        prepare_calls.append(True)
        return object()

    def _witness(*_args):
        value = len(witness_calls) * 2
        witness_calls.append(value)
        return value

    def _revalidate(*_args):
        value = len(witness_calls) * 2 + 1
        witness_calls.append(value)
        return value

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(cli, "_target_dependency_witness", _witness)
    monkeypatch.setattr(cli, "_revalidate_target_dependency_witness", _revalidate)
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: write_calls.append(True),
    )

    with pytest.raises(
        RuntimeError,
        match="^REPLACEMENT_FORECAST_SNAPSHOT_RETRY_EXHAUSTED$",
    ):
        cli._commit_from_read_snapshot(
            conn,
            SimpleNamespace(
                city="London",
                target_date=date(2026, 7, 19),
                temperature_metric="high",
            ),
            writer_lock=nullcontext,
        )
    conn.close()

    assert len(prepare_calls) == cli._SNAPSHOT_RETRY_LIMIT
    assert len(witness_calls) == cli._SNAPSHOT_RETRY_LIMIT * 2
    assert write_calls == []


def test_materialize_script_retries_changed_frontier_before_writer(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    prepared = [object(), object()]
    witness_values = iter(("before", "stable"))
    revalidated_values = iter(("after", "stable"))
    prepare_calls = []
    witness_lock_states = []
    write_calls = []
    lock_held = False

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    def _prepare(_conn, _request):
        assert lock_held is False
        value = prepared[len(prepare_calls)]
        prepare_calls.append(value)
        return value

    def _witness(_conn, _prepared):
        witness_lock_states.append(lock_held)
        return next(witness_values)

    def _revalidate(_conn, _prepared, _baseline):
        witness_lock_states.append(lock_held)
        return next(revalidated_values)

    def _write(_conn, value):
        assert lock_held is True
        assert value is prepared[1]
        write_calls.append(value)
        return _ready_materialization_result()

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(cli, "_target_dependency_witness", _witness)
    monkeypatch.setattr(cli, "_revalidate_target_dependency_witness", _revalidate)
    monkeypatch.setattr(cli, "write_prepared_replacement_forecast_live", _write)

    result = cli._commit_from_read_snapshot(
        conn,
        SimpleNamespace(
            city="London",
            target_date=date(2026, 7, 19),
            temperature_metric="high",
        ),
        writer_lock=writer_lock,
    )
    conn.close()

    assert result.ok is True
    assert prepare_calls == prepared
    assert witness_lock_states == [False, True, False, True]
    assert write_calls == [prepared[1]]


def test_prepared_writer_never_revalidates_or_recomputes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    prepared = materializer_mod.PreparedReplacementForecastMaterialization(
        request=_request(),
        metric="high",
        day0_ledger_frontier_identity=None,
        posterior=SimpleNamespace(
            live_eligible=False,
            replacement_q_mode="BLOCKED",
            capture_status="MISSING",
            predictive_sigma_c=None,
            q_lcb_map=None,
            q_ucb_map=None,
        ),
    )
    monkeypatch.setattr(
        materializer_mod,
        "_validated_replacement_forecast_request",
        lambda *_args: pytest.fail("prepared writer must reuse prepare validation"),
    )
    monkeypatch.setattr(
        materializer_mod,
        "_insert_anchor",
        lambda *_args, **_kwargs: 1,
    )

    result = materializer_mod.write_prepared_replacement_forecast_live(
        conn, prepared
    )
    conn.close()
    assert result.status == "BLOCKED"


def test_materialize_script_commit_helper_requires_writer_lock() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    with pytest.raises(
        RuntimeError, match="^REPLACEMENT_FORECAST_WRITER_LOCK_REQUIRED$"
    ):
        cli._commit_from_read_snapshot(
            conn,
            SimpleNamespace(
                city="London",
                target_date=date(2026, 7, 19),
                temperature_metric="high",
            ),
        )
    conn.close()


def test_materialize_script_injected_commit_connection_requires_writer_lock(
    tmp_path,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    with pytest.raises(
        RuntimeError, match="^REPLACEMENT_FORECAST_WRITER_LOCK_REQUIRED$"
    ):
        cli._materialize(
            tmp_path / "not-read.json",
            commit=True,
            init_schema=False,
            conn=conn,
        )
    conn.close()


def test_materialize_cli_bootstraps_hot_indexes_outside_writer_lock(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reachable commit path must finish index DDL before final flock/txn."""
    import scripts.materialize_replacement_forecast_live as cli

    payload = {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "openmeteo_payload_json": "anchor.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0}],
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "anchor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "precision.json").write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    lock_held = False
    bootstrap_states: list[tuple[bool, bool]] = []
    real_bootstrap_indexes = cli._ensure_replacement_frontier_indexes

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    def bootstrap_indexes(connection: sqlite3.Connection) -> None:
        bootstrap_states.append((lock_held, connection.in_transaction))
        real_bootstrap_indexes(connection)

    def prepare_schema(*_args, **_kwargs):
        _create_target_frontier_tables(conn)
        return cli._DurablePreparationReceipt(
            schema_ready=True,
            anchor_artifact_id=None,
            manifest_committed=False,
        )

    monkeypatch.setattr(
        cli,
        "extract_openmeteo_ecmwf_ifs9_localday_anchor",
        lambda *_args, **_kwargs: _anchor(),
    )
    monkeypatch.setattr(cli, "OpenMeteoIfs9PrecisionMetadata", lambda **_kwargs: object())
    monkeypatch.setattr(
        cli,
        "evaluate_openmeteo_ecmwf_ifs9_precision_guard",
        lambda _metadata: _precision_guard(),
    )
    monkeypatch.setattr(cli, "_bins", lambda _payload: _bins())
    monkeypatch.setattr(cli, "_ensure_replacement_frontier_indexes", bootstrap_indexes)
    monkeypatch.setattr(
        cli,
        "_prepare_live_schema_and_manifest",
        prepare_schema,
    )
    monkeypatch.setattr(
        cli,
        "_commit_from_read_snapshot",
        lambda *_args, **_kwargs: _ready_materialization_result(),
    )

    returncode, response = cli._materialize(
        input_json,
        commit=True,
        init_schema=True,
        conn=conn,
        writer_lock=writer_lock,
    )
    index_names = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    conn.close()

    assert returncode == 0
    assert response["status"] == "READY"
    assert bootstrap_states
    assert all(state == (False, False) for state in bootstrap_states)
    assert "idx_raw_model_forecasts_target_model_frontier" in index_names
    assert "idx_ensemble_snapshots_replacement_exact_frontier" in index_names
    assert "idx_ensemble_snapshots_replacement_casefold_frontier" in index_names


def test_materialize_script_dry_run_compute_does_not_hold_writer_lock(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    writer = sqlite3.connect(db_path, timeout=0)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("CREATE TABLE frontier (value INTEGER NOT NULL)")
    reader.commit()
    trace = []
    reader.set_trace_callback(trace.append)

    def _prepare(_conn, _request):
        writer.execute("BEGIN IMMEDIATE")
        writer.rollback()
        return SimpleNamespace(
            posterior=SimpleNamespace(live_eligible=True),
            anchor_id=None,
        )

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda _conn, _prepared: materializer_mod.ReplacementForecastMaterializeResult(
            status="READY",
            reason_codes=("REPLACEMENT_FORECAST_DRY_RUN_READY",),
            posterior_id=1,
            anchor_id=1,
            readiness_id="ready-1",
        ),
    )
    result = cli._dry_run_from_read_snapshot(reader, _request())
    reader.close()
    writer.close()

    assert result.ok is True
    statements = [statement.upper() for statement in trace]
    assert statements.index("BEGIN") < statements.index("ROLLBACK")
    assert statements.index("ROLLBACK") < statements.index("BEGIN IMMEDIATE")


def test_materialize_script_dry_run_matches_readiness_cert_regression(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = _conn()
    _install_live_fusion(monkeypatch)
    incumbent = materialize_replacement_forecast_live(
        conn,
        _request(computed_at=_dt(11), expires_at=_dt(13)),
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0]

    result = cli._dry_run_from_read_snapshot(
        conn,
        _request(computed_at=_dt(9), expires_at=_dt(13)),
    )
    after = conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0]
    conn.close()

    assert incumbent.ok is True
    assert result.ok is False
    assert result.reason_codes == ("READINESS_CERT_CYCLE_REGRESSION",)
    assert after == before


def test_materialize_script_reports_durable_manifest_when_posterior_fails(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    payload = {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "openmeteo_payload_json": "anchor.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0}],
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "anchor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "precision.json").write_text("{}", encoding="utf-8")
    receipt = cli._DurablePreparationReceipt(
        schema_ready=True,
        anchor_artifact_id=17,
        manifest_committed=True,
    )

    monkeypatch.setattr(cli, "extract_openmeteo_ecmwf_ifs9_localday_anchor", lambda *args, **kwargs: _anchor())
    monkeypatch.setattr(cli, "OpenMeteoIfs9PrecisionMetadata", lambda **kwargs: object())
    monkeypatch.setattr(cli, "evaluate_openmeteo_ecmwf_ifs9_precision_guard", lambda _metadata: _precision_guard())
    monkeypatch.setattr(cli, "_bins", lambda _payload: _bins())
    monkeypatch.setattr(cli, "_prepare_live_schema_and_manifest", lambda *args, **kwargs: receipt)
    monkeypatch.setattr(
        cli,
        "_commit_from_read_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("posterior failed")
        ),
    )
    conn = sqlite3.connect(":memory:")

    returncode, response = cli._materialize(
        input_json,
        commit=True,
        init_schema=False,
        conn=conn,
        writer_lock=nullcontext,
    )
    conn.close()

    assert returncode == 2
    assert response["status"] == "ERROR"
    assert response["error"] == "posterior failed"
    assert response["posterior_committed"] is False
    assert response["retry_safe"] is True
    assert response["durable_preparation"] == {
        "schema_ready": True,
        "openmeteo_anchor_artifact_id": 17,
        "manifest_committed": True,
    }


def test_materialize_script_threads_day0_zero_observation_state(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    payload = {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T18:00:00+00:00",
        "expires_at": "2026-06-08T00:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "openmeteo_payload_json": "anchor.json",
        "precision_metadata_json": "precision.json",
        "day0_observation_state": "zero_target_date_observations",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0}],
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "anchor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "precision.json").write_text("{}", encoding="utf-8")
    captured = []

    monkeypatch.setattr(
        cli,
        "extract_openmeteo_ecmwf_ifs9_localday_anchor",
        lambda *args, **kwargs: _anchor(),
    )
    monkeypatch.setattr(
        cli,
        "OpenMeteoIfs9PrecisionMetadata",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        cli,
        "evaluate_openmeteo_ecmwf_ifs9_precision_guard",
        lambda _metadata: _precision_guard(),
    )
    monkeypatch.setattr(cli, "_bins", lambda _payload: _bins())
    monkeypatch.setattr(
        cli,
        "_dry_run_from_read_snapshot",
        lambda _conn, request: (
            captured.append(request)
            or cli.ReplacementForecastMaterializeResult(
                status="READY",
                reason_codes=(),
                posterior_id=1,
                anchor_id=1,
                readiness_id="ready-1",
            )
        ),
    )
    conn = sqlite3.connect(":memory:")

    returncode, response = cli._materialize(
        input_json,
        commit=False,
        init_schema=False,
        conn=conn,
    )
    conn.close()

    assert returncode == 0
    assert response["status"] == "READY"
    assert (
        captured[0].day0_observation_state
        == "zero_target_date_observations"
    )


def test_materialize_script_fails_closed_without_precision_metadata(tmp_path) -> None:
    (tmp_path / "openmeteo_payload.json").write_text(
        json.dumps(
            {
                "hourly_units": {"temperature_2m": "C"},
                "hourly": {
                    "time": ["2026-06-07T00:00", "2026-06-07T06:00"],
                    "temperature_2m": [23.0, 27.0],
                },
            }
        ),
        encoding="utf-8",
    )
    request = {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0, "center_c": 25.0}],
        "openmeteo_payload_json": "openmeteo_payload.json",
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(request), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/materialize_replacement_forecast_live.py", "--input-json", str(input_json)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["status"] == "ERROR"
    assert "precision_metadata_json" in payload["error"]


def test_boot_current_posterior_family_scan_uses_covering_index(
    tmp_path: Path,
) -> None:
    from src.data import replacement_forecast_production as production

    forecast_db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(forecast_db)
    conn.executescript(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            runtime_layer TEXT NOT NULL,
            training_allowed INTEGER NOT NULL,
            q_json TEXT NOT NULL
        );
        CREATE INDEX idx_forecast_posteriors_runtime_layer_target
            ON forecast_posteriors(
                runtime_layer, city, target_date, temperature_metric, computed_at
            );
        """
    )
    conn.executemany(
        "INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                1,
                "Paris",
                "2026-08-23",
                "high",
                "2026-08-23T09:00:00+00:00",
                "live",
                0,
                "x" * 100_000,
            ),
            (
                2,
                "Munich",
                "2026-08-23",
                "high",
                "2026-08-23T09:01:00+00:00",
                "live",
                0,
                "y" * 100_000,
            ),
            (
                3,
                "Paris",
                "2026-08-23",
                "high",
                "2026-08-23T09:02:00+00:00",
                "live",
                0,
                "z" * 100_000,
            ),
            (
                4,
                "Experiment",
                "2026-08-23",
                "high",
                "2026-08-23T09:03:00+00:00",
                "experiment",
                1,
                "e" * 100_000,
            ),
        ),
    )
    plan = tuple(
        str(row[3])
        for row in conn.execute(
            "EXPLAIN QUERY PLAN "
            + production._CURRENT_POSTERIOR_FAMILY_SCAN_SQL,
            (100,),
        )
    )
    conn.commit()
    conn.close()

    assert any(
        "USING COVERING INDEX idx_forecast_posteriors_runtime_layer_target"
        in detail
        for detail in plan
    )
    assert production._current_forecast_posterior_families(
        {"forecast_db": str(forecast_db)},
        limit=2,
    ) == (
        ("Paris", "2026-08-23", "high"),
        ("Munich", "2026-08-23", "high"),
    )


def test_seed_cycle_boundary_uses_ordered_live_family_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.data import replacement_input_hwm

    forecast_db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(forecast_db)
    conn.executescript(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            runtime_layer TEXT NOT NULL,
            q_json TEXT NOT NULL
        );
        CREATE INDEX idx_forecast_posteriors_runtime_layer_target
            ON forecast_posteriors(
                runtime_layer, city, target_date, temperature_metric, computed_at
            );
        """
    )
    conn.executemany(
        "INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                1,
                queue.SOURCE_ID,
                "Ankara",
                "2026-08-23",
                "high",
                "2026-08-23T06:00:00+00:00",
                "2026-08-23T08:00:00+00:00",
                "live",
                "x" * 100_000,
            ),
            (
                2,
                queue.SOURCE_ID,
                "Ankara",
                "2026-08-23",
                "high",
                "2026-08-23T18:00:00+00:00",
                "2026-08-23T09:00:00+00:00",
                "offline",
                "y" * 100_000,
            ),
        ),
    )
    conn.commit()
    plan = "\n".join(
        str(row[3])
        for row in conn.execute(
            "EXPLAIN QUERY PLAN " + queue._CURRENT_LIVE_POSTERIOR_CYCLE_SQL,
            (queue.SOURCE_ID, "Ankara", "2026-08-23", "high"),
        ).fetchall()
    )
    conn.close()
    monkeypatch.setattr(
        replacement_input_hwm,
        "latest_eligible_ensemble_input_cycle",
        lambda *_args, **_kwargs: datetime(
            2026, 8, 23, 12, tzinfo=timezone.utc
        ),
    )

    boundary = queue._seed_source_cycle_boundary(
        forecast_db=forecast_db,
        seed={
            "city": "Ankara",
            "target_date": "2026-08-23",
            "temperature_metric": "high",
            "source_cycle_time": "2026-08-23T12:00:00+00:00",
        },
    )

    assert boundary is None
    assert "USING INDEX idx_forecast_posteriors_runtime_layer_target" in plan
    assert "USE TEMP B-TREE FOR ORDER BY" not in plan
