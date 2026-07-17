# Created: 2026-06-06
# Last reused/audited: 2026-07-17
# Lifecycle: created=2026-06-06; last_reviewed=2026-07-17; last_reused=2026-07-17
# Purpose: Protect DB materialization for Open-Meteo ECMWF IFS 9km + Bayes-fusion replacement live layer.
# Reuse: Run before changing replacement forecast live/experiment write path.
# Authority basis: Operator-directed replacement forecast simple-switch readiness.
"""Replacement forecast materializer tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
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
    REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
    REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET,
    ReplacementForecastMaterializeRequest,
    _QLCB_BASIS,
    _ensure_forecast_posteriors_runtime_layer,
    _ensure_replacement_identity_columns,
    _replacement_is_live_layer,
    materialize_replacement_forecast_live,
)
import src.data.replacement_forecast_materializer as materializer_mod
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


def _install_live_fusion(monkeypatch: pytest.MonkeyPatch, *, complete: bool = True) -> None:
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
    )


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
        day0_observed_extreme_source="durable_observation_instants",
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


def test_materialize_script_batch_reuses_connection_and_reports_each_request(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db as state_db

    inputs = [tmp_path / "a.json", tmp_path / "b.json"]
    calls = []

    class _Connection:
        closed = False

        def close(self):
            self.closed = True

    conn = _Connection()
    monkeypatch.setattr(state_db, "get_forecasts_connection", lambda **_: conn)

    def _run_one(input_json, **kwargs):
        calls.append((input_json, kwargs))
        return 0, json.dumps({"status": "READY"}) + "\n", ""

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
    envelopes = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
    ]
    assert [Path(envelope["input_json"]) for envelope in envelopes] == inputs
    assert [envelope["returncode"] for envelope in envelopes] == [0, 0]


def test_materialize_script_batch_keeps_first_wake_immediate_and_batches_rest(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db as state_db

    inputs = [tmp_path / "a.json", tmp_path / "b.json"]
    families = [
        ("Shanghai", "2026-07-18", "high"),
        ("Paris", "2026-07-18", "low"),
    ]
    conn = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(state_db, "get_forecasts_connection", lambda **_: conn)

    def _run_one(input_json, **kwargs):
        index = inputs.index(input_json)
        response = {
            "status": "READY",
            "committed": True,
            "posterior_id": index + 1,
            "reactor_wake_published": kwargs["publish_wake"],
            "forecast_family": list(families[index]),
        }
        return 0, json.dumps(response) + "\n", ""

    published = []
    monkeypatch.setattr(cli, "_run_one", _run_one)
    monkeypatch.setattr(
        cli,
        "_publish_materialization_wake_families",
        lambda changed: published.append(changed) or True,
    )

    rc = cli.main(
        ["--batch-input-json", *(str(path) for path in inputs), "--commit"]
    )

    assert rc == 0
    assert published == [(families[1],)]
    envelopes = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(envelopes) == 3
    latest = {Path(row["input_json"]): json.loads(row["stdout"]) for row in envelopes}
    assert all(response["reactor_wake_published"] is True for response in latest.values())


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
