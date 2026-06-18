# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect DB materialization for Open-Meteo ECMWF IFS 9km + AIFS sampled-2t replacement posterior authority.
# Reuse: Run before changing replacement forecast live/diagnostic write path.
# Authority basis: Operator-directed replacement forecast simple-switch readiness.
"""Replacement forecast materializer tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.data.ecmwf_aifs_sampled_2t_localday import AifsMemberLocalDayExtrema, AifsSampledLocalDayExtraction
from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_bundle_reader import read_replacement_forecast_bundle
from src.data.replacement_forecast_materializer import (
    REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
    REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET,
    ReplacementForecastMaterializeRequest,
    _QLCB_BASIS,
    _ensure_forecast_posteriors_live_authority_check,
    _ensure_replacement_identity_columns,
    _replacement_is_live_authority,
    materialize_replacement_forecast_shadow,
)
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin


UTC = timezone.utc
_DEFAULT_PRECISION_GUARD = object()
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _Evidence:
    source_run_id: str


@dataclass(frozen=True)
class _BaselineBundle:
    evidence: _Evidence


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_readiness_state(conn)
    return conn


def _aifs_extraction(*, source_cycle_time: datetime | None = None) -> AifsSampledLocalDayExtraction:
    cycle = source_cycle_time or _dt(0)
    return AifsSampledLocalDayExtraction(
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 7),
        source_cycle_time=cycle,
        target_window_start_utc=_dt(16),
        target_window_end_utc=datetime(2026, 6, 7, 16, tzinfo=UTC),
        members=(
            AifsMemberLocalDayExtrema("pf-001", high_c=24.0, low_c=18.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-002", high_c=26.0, low_c=19.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-003", high_c=32.0, low_c=21.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
        ),
    )


def _anchor(*, source_cycle_time: datetime | None = None) -> OpenMeteoIfs9LocalDayAnchor:
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 7),
        high_c=27.0,
        low_c=18.5,
        sample_count=4,
        contributing_local_times=(
            datetime(2026, 6, 7, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 7, 6, tzinfo=timezone.utc),
            datetime(2026, 6, 7, 12, tzinfo=timezone.utc),
            datetime(2026, 6, 7, 18, tzinfo=timezone.utc),
        ),
        contributing_valid_times_utc=(_dt(16), _dt(22), datetime(2026, 6, 7, 4, tzinfo=UTC), datetime(2026, 6, 7, 10, tzinfo=UTC)),
        source_cycle_time=source_cycle_time or _dt(0),
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


def _bins() -> tuple[AifsTemperatureBin, ...]:
    return (
        AifsTemperatureBin("cool", upper_c=20.0, center_c=19.0),
        AifsTemperatureBin("warm", lower_c=21.0, upper_c=30.0),
        AifsTemperatureBin("hot", lower_c=31.0, center_c=32.0),
    )


def _request(
    *,
    baseline_data_version: str = "ecmwf_opendata_mx2t3_local_calendar_day_max",
    baseline_source_run_id: str = "b0-run",
    baseline_source_available_at: datetime | None = None,
    aifs_source_run_id: str = "aifs-run",
    aifs_source_available_at: datetime | None = None,
    openmeteo_source_run_id: str | None = "om9-run",
    openmeteo_source_available_at: datetime | None = None,
    source_cycle_time: datetime | None = None,
    computed_at: datetime | None = None,
    expires_at: datetime | None = None,
    anchor_artifact_id: int | None = None,
    aifs_artifact_id: int | None = None,
    openmeteo_precision_guard=_DEFAULT_PRECISION_GUARD,
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
        aifs_extraction=_aifs_extraction(source_cycle_time=source_cycle_time),
        aifs_source_run_id=aifs_source_run_id,
        aifs_source_available_at=aifs_source_available_at or _dt(2, 30),
        openmeteo_anchor=_anchor(source_cycle_time=source_cycle_time),
        openmeteo_source_run_id=openmeteo_source_run_id,
        openmeteo_source_available_at=openmeteo_source_available_at or _dt(3),
        bins=_bins(),
        source_cycle_time=source_cycle_time or _dt(0),
        computed_at=computed_at or _dt(4),
        expires_at=expires_at or _dt(6),
        anchor_artifact_id=anchor_artifact_id,
        aifs_artifact_id=aifs_artifact_id,
        openmeteo_precision_guard=guard,
    )


def test_materializer_blocks_non_live_posterior_before_execution_authority_table() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(conn, _request())

    assert result.ok is False
    assert result.reason_codes == (REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET,)
    assert result.posterior_id is None
    assert result.anchor_id is not None
    assert result.readiness_id is None
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_materializer_does_not_write_unlicensed_intermediate_cycle_as_live_authority() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(
        conn,
        replace(
            _request(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12)),
            aifs_extraction=None,
            aifs_source_run_id=None,
            aifs_source_available_at=None,
        ),
    )

    assert result.ok is False
    assert result.reason_codes == (REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET,)
    assert result.posterior_id is None
    assert result.anchor_id is not None
    assert result.readiness_id is None
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_live_authority_status_requires_live_flags_and_bootstrap_bounds() -> None:
    live_authority = _replacement_is_live_authority(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1, "warm": 0.6, "hot": 0.05},
        q_ucb_map={"cool": 0.3, "warm": 0.9, "hot": 0.2},
        q_lcb_basis=_QLCB_BASIS,
        cycle_phase="synoptic",
    )

    assert live_authority is True


def test_live_authority_status_rejects_wilson_or_missing_bounds() -> None:
    assert _replacement_is_live_authority(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1},
        q_ucb_map={"cool": 0.3},
        q_lcb_basis="wilson_aifs_member_votes",
        cycle_phase="synoptic",
    ) is False


def test_forecast_posteriors_live_authority_check_migration_discards_non_live_rows() -> None:
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

    _ensure_forecast_posteriors_live_authority_check(conn)

    rows = conn.execute("SELECT posterior_id, trade_authority_status, q_json FROM forecast_posteriors").fetchall()
    assert [dict(row) for row in rows] == [
        {"posterior_id": 2, "trade_authority_status": "LIVE_AUTHORITY", "q_json": '{"good":1}'}
    ]
    conn.execute(
        "INSERT INTO forecast_posteriors (trade_authority_status, q_json) VALUES (?, ?)",
        ("LIVE_AUTHORITY", "{}"),
    )
    statuses = [
        row["trade_authority_status"]
        for row in conn.execute("SELECT trade_authority_status FROM forecast_posteriors ORDER BY posterior_id")
    ]
    assert statuses == ["LIVE_AUTHORITY", "LIVE_AUTHORITY"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO forecast_posteriors (trade_authority_status, q_json) VALUES (?, ?)",
            ("DIAGNOSTIC_ONLY", "{}"),
        )
    assert _replacement_is_live_authority(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1},
        q_ucb_map=None,
        q_lcb_basis=_QLCB_BASIS,
        cycle_phase="synoptic",
    ) is False


def test_legacy_anchor_schema_migration_preserves_raw_shadow_parent_fk() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE raw_forecast_artifacts (
            artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_authority_status TEXT NOT NULL DEFAULT 'SHADOW_ONLY'
                CHECK (trade_authority_status IN ('SHADOW_ONLY'))
        );
        INSERT INTO raw_forecast_artifacts (artifact_id, trade_authority_status)
        VALUES (1, 'SHADOW_ONLY');

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
            trade_authority_status TEXT NOT NULL DEFAULT 'SHADOW_ONLY'
                CHECK (trade_authority_status IN ('SHADOW_ONLY')),
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
            'SHADOW_ONLY',
            'anchor-hash'
        );

        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            openmeteo_anchor_id INTEGER REFERENCES deterministic_forecast_anchors(anchor_id),
            trade_authority_status TEXT NOT NULL DEFAULT 'SHADOW_ONLY'
                CHECK (trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY'))
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
    conn.execute(
        "INSERT INTO forecast_posteriors (openmeteo_anchor_id, trade_authority_status) VALUES (?, ?)",
        (1, "LIVE_AUTHORITY"),
    )

    assert raw_status == "SHADOW_ONLY"
    assert anchor_status == "DIAGNOSTIC_ONLY"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_materializer_keeps_readiness_separate_by_baseline_source_run() -> None:
    conn = _conn()

    first = materialize_replacement_forecast_shadow(
        conn,
        _request(
            baseline_source_run_id="ecmwf_open_data:mx2t6_high:2026-06-06T12Z",
            baseline_source_available_at=_dt(2),
            computed_at=_dt(4),
            expires_at=_dt(6),
        ),
    )
    second = materialize_replacement_forecast_shadow(
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
          AND strategy_key = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
        ORDER BY track
        """
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["track"] == "soft_anchor_posterior:ecmwf_open_data:mx2t6_high:2026-06-06T12Z"
    assert rows[1]["track"] == "soft_anchor_posterior:ecmwf_open_data:mx2t6_high:2026-06-07T00Z"
    assert "2026-06-06T12Z" in rows[0]["dependency_json"]
    assert "2026-06-07T00Z" in rows[1]["dependency_json"]


def test_materializer_does_not_fabricate_directional_no_lcb() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(conn, _request())

    assert result.ok is True
    posterior_row = conn.execute("SELECT q_json, q_lcb_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    q = json.loads(posterior_row["q_json"])
    q_lcb = json.loads(posterior_row["q_lcb_json"])
    provenance = json.loads(posterior_row["provenance_json"])
    assert q_lcb == q
    assert not any(str(key).startswith(("buy_no:", "no:")) for key in q_lcb)
    assert provenance["q_lcb_json_role"] == "shadow_point_probability_capped_downstream"


def test_materializer_blocks_readiness_when_baseline_identity_is_wrong() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(
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


def test_materializer_preserves_raw_artifact_lineage_in_anchor_posterior_and_readiness() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(conn, _request(anchor_artifact_id=11, aifs_artifact_id=22))

    assert result.ok is True
    anchor_row = conn.execute("SELECT artifact_id FROM deterministic_forecast_anchors WHERE anchor_id = ?", (result.anchor_id,)).fetchone()
    assert anchor_row["artifact_id"] == 11
    posterior_row = conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    assert '"aifs_artifact_id":22' in posterior_row["provenance_json"]
    assert '"openmeteo_anchor_artifact_id":11' in posterior_row["provenance_json"]
    readiness_row = conn.execute("SELECT dependency_json FROM readiness_state WHERE readiness_id = ?", (result.readiness_id,)).fetchone()
    assert '"artifact_id":22' in readiness_row["dependency_json"]
    assert '"artifact_id":11' in readiness_row["dependency_json"]


def test_materializer_records_precision_guard_in_anchor_and_posterior_provenance() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(
        conn,
        _request(openmeteo_precision_guard=_precision_guard(city_class="coastal", land_sea_mask="sea")),
    )

    assert result.ok is True
    anchor_row = conn.execute("SELECT provenance_json FROM deterministic_forecast_anchors WHERE anchor_id = ?", (result.anchor_id,)).fetchone()
    posterior_row = conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    anchor_provenance = json.loads(anchor_row["provenance_json"])
    posterior_provenance = json.loads(posterior_row["provenance_json"])
    assert anchor_provenance["precision_guard"]["status"] == "DIAGNOSTIC_ONLY"
    assert anchor_provenance["precision_guard"]["high_risk_bucket"] == "coastal"
    assert posterior_provenance["openmeteo_precision_guard"]["reason_codes"] == ["OM9_LAND_SEA_HIGH_RISK_FOR_CITY_CLASS"]


def test_materializer_blocks_when_precision_guard_missing_or_blocked() -> None:
    conn = _conn()

    missing = materialize_replacement_forecast_shadow(
        conn,
        _request(openmeteo_precision_guard=None),
    )

    assert missing.ok is False
    assert missing.reason_codes == ("OM9_PRECISION_GUARD_REQUIRED_FOR_MATERIALIZATION",)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0

    blocked = materialize_replacement_forecast_shadow(
        conn,
        _request(openmeteo_precision_guard=_precision_guard(endpoint_mode="daily_vendor_aggregated")),
    )

    assert blocked.ok is False
    assert "OM9_PRECISION_GUARD_BLOCKED_MATERIALIZATION" in blocked.reason_codes
    assert "OM9_ENDPOINT_MUST_BE_HOURLY_ZEUS_AGGREGATED" in blocked.reason_codes
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0


def test_materializer_blocks_future_dependency_before_writing_shadow_rows() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(
        conn,
        _request(aifs_source_available_at=_dt(5)),
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

    result = materialize_replacement_forecast_shadow(
        conn,
        _request(openmeteo_source_run_id=""),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_OPENMETEO_SOURCE_RUN_ID_MISSING",)
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_materializer_posterior_available_at_includes_baseline_dependency() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(
        conn,
        _request(baseline_source_available_at=_dt(3, 30), aifs_source_available_at=_dt(2), openmeteo_source_available_at=_dt(3)),
    )

    assert result.ok is True
    posterior_row = conn.execute("SELECT source_available_at FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    assert posterior_row["source_available_at"] == _dt(3, 30).isoformat()


def test_materializer_blocks_expired_request_before_writing_shadow_rows() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(
        conn,
        _request(expires_at=_dt(4)),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_EXPIRY_NOT_AFTER_COMPUTED_AT",)
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


def test_materialize_script_template_requires_precision_metadata() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/materialize_replacement_forecast_shadow.py", "--print-template"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    template = json.loads(result.stdout)

    assert template["precision_metadata_json"] == "openmeteo_precision_metadata.json"


def test_materialize_script_fails_closed_without_precision_metadata(tmp_path) -> None:
    (tmp_path / "aifs_samples.json").write_text(
        json.dumps(
            {
                "samples": [
                    {"member_id": "pf-001", "valid_time_utc": "2026-06-06T18:00:00+00:00", "temperature": 24.0, "temperature_unit": "C"},
                    {"member_id": "pf-002", "valid_time_utc": "2026-06-06T18:00:00+00:00", "temperature": 25.0, "temperature_unit": "C"},
                ]
            }
        ),
        encoding="utf-8",
    )
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
        "aifs_source_run_id": "aifs-run",
        "aifs_source_available_at": "2026-06-06T02:30:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0, "center_c": 25.0}],
        "aifs_samples_json": "aifs_samples.json",
        "openmeteo_payload_json": "openmeteo_payload.json",
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(request), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/materialize_replacement_forecast_shadow.py", "--input-json", str(input_json)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["status"] == "ERROR"
    assert "precision_metadata_json" in payload["error"]
