# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect DB materialization for Open-Meteo ECMWF IFS 9km + AIFS sampled-2t replacement shadow posterior.
# Reuse: Run before changing replacement forecast live shadow write path.
# Authority basis: Operator-directed replacement forecast simple-switch readiness.
"""Replacement forecast materializer tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
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
    ReplacementForecastMaterializeRequest,
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


def _aifs_extraction() -> AifsSampledLocalDayExtraction:
    return AifsSampledLocalDayExtraction(
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 7),
        source_cycle_time=_dt(0),
        target_window_start_utc=_dt(16),
        target_window_end_utc=datetime(2026, 6, 7, 16, tzinfo=UTC),
        members=(
            AifsMemberLocalDayExtrema("pf-001", high_c=24.0, low_c=18.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-002", high_c=26.0, low_c=19.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
            AifsMemberLocalDayExtrema("pf-003", high_c=32.0, low_c=21.0, sample_count=4, contributing_valid_times_utc=(_dt(18), _dt(0), _dt(6), _dt(12))),
        ),
    )


def _anchor() -> OpenMeteoIfs9LocalDayAnchor:
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
        source_cycle_time=_dt(0),
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
        aifs_extraction=_aifs_extraction(),
        aifs_source_run_id=aifs_source_run_id,
        aifs_source_available_at=aifs_source_available_at or _dt(2, 30),
        openmeteo_anchor=_anchor(),
        openmeteo_source_run_id=openmeteo_source_run_id,
        openmeteo_source_available_at=openmeteo_source_available_at or _dt(3),
        bins=_bins(),
        source_cycle_time=_dt(0),
        computed_at=_dt(4),
        expires_at=expires_at or _dt(6),
        anchor_artifact_id=anchor_artifact_id,
        aifs_artifact_id=aifs_artifact_id,
        openmeteo_precision_guard=guard,
    )


def test_materializer_writes_posterior_and_readiness_readable_by_switch_reader() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_shadow(conn, _request())

    assert result.ok is True
    assert result.posterior_id is not None
    assert result.anchor_id is not None
    readiness_row = conn.execute("SELECT * FROM readiness_state WHERE readiness_id = ?", (result.readiness_id,)).fetchone()
    assert readiness_row is not None
    assert readiness_row["status"] == "SHADOW_ONLY"
    posterior_row = conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    assert posterior_row is not None
    assert posterior_row["trade_authority_status"] == "SHADOW_VETO_ONLY"
    assert posterior_row["training_allowed"] == 0

    from src.engine.replacement_forecast_hook_factory import _latest_replacement_readiness

    readiness = _latest_replacement_readiness(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
    )
    assert readiness is not None
    bundle = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4, 30),
    )
    assert bundle.ok is True
    assert bundle.bundle is not None
    assert bundle.bundle.posterior_id == result.posterior_id
    assert set(bundle.bundle.q) == {"cool", "warm", "hot"}


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
    assert anchor_provenance["precision_guard"]["status"] == "SHADOW_ONLY"
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
