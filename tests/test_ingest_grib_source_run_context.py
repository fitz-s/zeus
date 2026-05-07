# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 6 SourceRunContext linkage contract.
"""GRIB ingester source-run context linkage tests."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import LOW_LOCALDAY_MIN

UTC = timezone.utc
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ingest_grib_to_snapshots import SourceRunContext, ingest_json_file, ingest_track  # type: ignore  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _payload(target_date: str, issue_iso: str) -> dict:
    return {
        "generated_at": "2026-05-03T08:00:00+00:00",
        "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
        "physical_quantity": "mx2t6_local_calendar_day_max",
        "param": "mx2t6",
        "paramId": 121,
        "short_name": "mx2t6",
        "step_type": "max",
        "aggregation_window_hours": 6,
        "city": "London",
        "lat": 51.4775,
        "lon": -0.4614,
        "unit": "C",
        "manifest_sha256": "1" * 64,
        "manifest_hash": "1" * 64,
        "issue_time_utc": issue_iso,
        "target_date_local": target_date,
        "lead_day": 5,
        "lead_day_anchor": "issue_utc.date()",
        "timezone": "Europe/London",
        "local_day_window": {
            "start": f"{target_date}T00:00:00+00:00",
            "end": f"{target_date}T23:59:59+00:00",
        },
        "local_day_start_utc": f"{target_date}T00:00:00+00:00",
        "local_day_end_utc": f"{target_date}T23:59:59+00:00",
        "step_horizon_hours": 144.0,
        "step_horizon_deficit_hours": 0.0,
        "causality": {"status": "OK"},
        "boundary_ambiguous": False,
        "nearest_grid_lat": 51.5,
        "nearest_grid_lon": -0.5,
        "nearest_grid_distance_km": 5.0,
        "selected_step_ranges": ["120-126", "126-132", "132-138", "138-144"],
        "member_count": 51,
        "missing_members": [],
        "training_allowed": True,
        "members": [
            {"member": member, "value_native_unit": 18.0 + 0.1 * member}
            for member in range(51)
        ],
    }


def _write_payload(root: Path, payload: dict) -> None:
    extract_subdir = "open_ens_mx2t6_localday_max"
    target = payload["target_date_local"]
    json_dir = root / "raw" / extract_subdir / "london" / "20260503"
    json_dir.mkdir(parents=True)
    json_path = json_dir / f"{extract_subdir}_target_{target}_lead_5.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")


def test_source_run_context_writes_executable_v2_linkage(tmp_path: Path) -> None:
    conn = _conn()
    fifty_one_root = tmp_path / "51 source data"
    _write_payload(
        fifty_one_root,
        _payload("2026-05-08", "2026-05-03T00:00:00+00:00"),
    )
    context = SourceRunContext(
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        source_run_id="ecmwf_open_data:mx2t6_high:2026-05-03T00Z",
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
        source_cycle_time=datetime(2026, 5, 3, tzinfo=UTC),
        source_release_time=datetime(2026, 5, 3, 8, 5, tzinfo=UTC),
        source_available_at=datetime(2026, 5, 3, 8, 10, tzinfo=UTC),
    )

    import ingest_grib_to_snapshots as ingest_module  # type: ignore

    original = ingest_module._TRACK_CONFIGS["mx2t6_high"]["json_subdir"]
    ingest_module._TRACK_CONFIGS["mx2t6_high"]["json_subdir"] = "open_ens_mx2t6_localday_max"
    try:
        summary = ingest_track(
            track="mx2t6_high",
            json_root=fifty_one_root / "raw",
            conn=conn,
            date_from=None,
            date_to=None,
            cities={"London"},
            overwrite=False,
            require_files=False,
            source_run_context=context,
        )
    finally:
        ingest_module._TRACK_CONFIGS["mx2t6_high"]["json_subdir"] = original

    assert summary["written"] == 1
    row = conn.execute("SELECT * FROM ensemble_snapshots_v2").fetchone()
    assert row["source_id"] == "ecmwf_open_data"
    assert row["source_transport"] == "ensemble_snapshots_v2_db_reader"
    assert row["source_run_id"] == "ecmwf_open_data:mx2t6_high:2026-05-03T00Z"
    assert row["release_calendar_key"] == "ecmwf_open_data:mx2t6_high:full"
    assert row["source_cycle_time"] == "2026-05-03T00:00:00+00:00"
    assert row["source_release_time"] == "2026-05-03T08:05:00+00:00"
    assert row["source_available_at"] == "2026-05-03T08:10:00+00:00"
    assert row["available_at"] == "2026-05-03T08:10:00+00:00"


def test_missing_source_run_context_leaves_v2_row_non_executable(tmp_path: Path) -> None:
    conn = _conn()
    fifty_one_root = tmp_path / "51 source data"
    issue = "2026-05-03T00:00:00+00:00"
    _write_payload(fifty_one_root, _payload("2026-05-08", issue))

    import ingest_grib_to_snapshots as ingest_module  # type: ignore

    original = ingest_module._TRACK_CONFIGS["mx2t6_high"]["json_subdir"]
    ingest_module._TRACK_CONFIGS["mx2t6_high"]["json_subdir"] = "open_ens_mx2t6_localday_max"
    try:
        summary = ingest_track(
            track="mx2t6_high",
            json_root=fifty_one_root / "raw",
            conn=conn,
            date_from=None,
            date_to=None,
            cities={"London"},
            overwrite=False,
            require_files=False,
        )
    finally:
        ingest_module._TRACK_CONFIGS["mx2t6_high"]["json_subdir"] = original

    assert summary["written"] == 1
    row = conn.execute("SELECT * FROM ensemble_snapshots_v2").fetchone()
    assert row["source_id"] is None
    assert row["source_transport"] is None
    assert row["source_run_id"] is None
    assert row["release_calendar_key"] is None
    assert row["available_at"] == issue


def test_ingest_persists_contract_outcome_and_forecast_window_evidence(tmp_path: Path) -> None:
    conn = _conn()
    fifty_one_root = tmp_path / "51 source data"
    _write_payload(
        fifty_one_root,
        _payload("2026-05-08", "2026-05-03T00:00:00+00:00"),
    )

    import ingest_grib_to_snapshots as ingest_module  # type: ignore

    original = ingest_module._TRACK_CONFIGS["mx2t6_high"]["json_subdir"]
    ingest_module._TRACK_CONFIGS["mx2t6_high"]["json_subdir"] = "open_ens_mx2t6_localday_max"
    try:
        summary = ingest_track(
            track="mx2t6_high",
            json_root=fifty_one_root / "raw",
            conn=conn,
            date_from=None,
            date_to=None,
            cities={"London"},
            overwrite=False,
            require_files=False,
        )
    finally:
        ingest_module._TRACK_CONFIGS["mx2t6_high"]["json_subdir"] = original

    assert summary["written"] == 1
    row = conn.execute("SELECT * FROM ensemble_snapshots_v2").fetchone()
    assert row["city_timezone"] == "Europe/London"
    assert row["settlement_source_type"] == "wu_icao"
    assert row["settlement_station_id"] == "EGLC"
    assert row["settlement_unit"] == "C"
    assert row["settlement_rounding_policy"] == "wmo_half_up"
    assert row["bin_grid_id"] == "C_canonical_v1"
    assert row["bin_schema_version"] == "canonical_bin_grid_v1"
    assert row["forecast_window_start_utc"] == "2026-05-08T00:00:00+00:00"
    assert row["forecast_window_end_utc"] == "2026-05-09T00:00:00+00:00"
    assert row["forecast_window_start_local"] == "2026-05-08T01:00:00+01:00"
    assert row["forecast_window_end_local"] == "2026-05-09T01:00:00+01:00"
    assert row["forecast_window_attribution_status"] == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    assert row["contributes_to_target_extrema"] == 0
    block_reasons = json.loads(row["forecast_window_block_reasons_json"])
    assert "ambiguous_crosses_local_day_boundary" in block_reasons


def test_low_boundary_ambiguous_persists_block_evidence_without_relaxing_law1(tmp_path: Path) -> None:
    conn = _conn()
    payload = {
        **_payload("2026-05-08", "2026-05-03T00:00:00+00:00"),
        "data_version": ECMWF_OPENDATA_LOW_DATA_VERSION,
        "physical_quantity": LOW_LOCALDAY_MIN.physical_quantity,
        "param": "mn2t6",
        "paramId": 122,
        "short_name": "mn2t6",
        "step_type": "min",
        "temperature_metric": "low",
        "boundary_ambiguous": True,
        "boundary_policy": {
            "boundary_ambiguous": True,
            "ambiguous_member_count": 51,
            "training_rule": "reject_if_ambiguous",
        },
        "selected_step_ranges": None,
        "selected_step_ranges_inner": [],
        "selected_step_ranges_boundary": ["120-126"],
    }
    path = tmp_path / "low_snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = ingest_json_file(
        conn,
        path,
        metric=LOW_LOCALDAY_MIN,
        model_version="ecmwf_ens",
        overwrite=True,
    )

    assert status == "written"
    row = conn.execute("SELECT * FROM ensemble_snapshots_v2").fetchone()
    assert row["temperature_metric"] == "low"
    assert row["observation_field"] == "low_temp"
    assert row["training_allowed"] == 0
    assert row["causality_status"] == "REJECTED_BOUNDARY_AMBIGUOUS"
    assert row["forecast_window_attribution_status"] == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    assert row["contributes_to_target_extrema"] == 0
    assert "boundary_ambiguous" in json.loads(row["forecast_window_block_reasons_json"])
