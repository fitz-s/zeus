# Created: 2026-05-03
# Last reused/audited: 2026-08-03
# Authority basis: LOW local-day-min interval provenance contract plus the original SourceRunContext contract.
"""GRIB ingester source-run context linkage tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.contracts.snapshot_ingest_contract import (
    LOW_BOUNDARY_SEMANTICS_REVISION,
    normalize_low_boundary_evidence,
)
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_canonical_schema
from src.types.metric_identity import LOW_LOCALDAY_MIN

UTC = timezone.utc
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from ingest_grib_to_snapshots import (  # type: ignore  # noqa: E402
    LOW_LOCAL_DAY_MIN_INTERVAL_EVIDENCE_REVISION,
    SourceRunContext,
    _low_local_day_min_interval_evidence,
    ingest_json_file,
    ingest_track,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


def _payload(target_date: str, issue_iso: str) -> dict:
    return {
        "generated_at": "2026-05-03T08:00:00+00:00",
        "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
        "physical_quantity": "mx2t3_local_calendar_day_max",
        "param": "mx2t3",
        "paramId": 121,
        "short_name": "mx2t3",
        "step_type": "max",
        "aggregation_window_hours": 3,
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
        source_transport="ensemble_snapshots_db_reader",
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
    row = conn.execute("SELECT * FROM ensemble_snapshots").fetchone()
    assert row["source_id"] == "ecmwf_open_data"
    assert row["source_transport"] == "ensemble_snapshots_db_reader"
    assert row["source_run_id"] == "ecmwf_open_data:mx2t6_high:2026-05-03T00Z"
    assert row["release_calendar_key"] == "ecmwf_open_data:mx2t6_high:full"
    assert row["source_cycle_time"] == "2026-05-03T00:00:00+00:00"
    assert row["source_release_time"] == "2026-05-03T08:05:00+00:00"
    assert row["source_available_at"] == "2026-05-03T08:10:00+00:00"
    assert row["available_at"] == "2026-05-03T08:10:00+00:00"
    assert "low_local_day_min_interval_evidence" not in json.loads(
        row["provenance_json"]
    )


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
    row = conn.execute("SELECT * FROM ensemble_snapshots").fetchone()
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
    row = conn.execute("SELECT * FROM ensemble_snapshots").fetchone()
    assert row["city_timezone"] == "Europe/London"
    assert row["settlement_source_type"] == "wu_icao"
    assert row["settlement_station_id"] == "EGLC"
    assert row["settlement_unit"] == "C"
    assert row["settlement_rounding_policy"] == "wmo_half_up"
    assert row["bin_grid_id"] == "C_canonical_v1"
    assert row["bin_schema_id"] == "canonical_bin_grid_v1"
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
        "physical_quantity": "mn2t3_local_calendar_day_min",
        "param": "mn2t3",
        "paramId": 122,
        "short_name": "mn2t3",
        "step_type": "min",
        "aggregation_window_hours": 3,
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
    row = conn.execute("SELECT * FROM ensemble_snapshots").fetchone()
    assert row["temperature_metric"] == "low"
    assert row["observation_field"] == "low_temp"
    assert row["training_allowed"] == 0
    assert row["causality_status"] == "REJECTED_BOUNDARY_AMBIGUOUS"
    assert row["forecast_window_attribution_status"] == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    assert row["contributes_to_target_extrema"] == 0
    assert "boundary_ambiguous" in json.loads(row["forecast_window_block_reasons_json"])


def _low_boundary_payload(
    *,
    ambiguous_count: int,
    invalid_inner_member_id: int | None = None,
    invalid_boundary_member_id: int | None = None,
    missing_boundary_member_id: int | None = None,
) -> dict:
    issue = datetime(2026, 7, 30, 12, tzinfo=UTC)
    available = datetime(2026, 7, 30, 20, 6, tzinfo=UTC)
    target = date(2026, 8, 1)
    members = []
    for member_id in range(51):
        inner = 28.0 + member_id / 100.0
        ambiguous = member_id < ambiguous_count
        members.append(
            {
                "member": member_id,
                "value_native_unit": None if ambiguous else inner + 5.0,
                "inner_min_native_unit": (
                    float("nan")
                    if member_id == invalid_inner_member_id
                    else inner
                ),
                "boundary_min_native_unit": (
                    None
                    if member_id == missing_boundary_member_id
                    else float("inf")
                    if member_id == invalid_boundary_member_id
                    else inner - 1.0
                    if ambiguous
                    else inner + 1.0
                ),
                "boundary_ambiguous": ambiguous,
            }
        )
    return {
        "generated_at": available.isoformat(),
        "data_version": ECMWF_OPENDATA_LOW_DATA_VERSION,
        "physical_quantity": "mn2t3_local_calendar_day_min",
        "param": "mn2t3",
        "paramId": 122,
        "short_name": "mn2t3",
        "step_type": "min",
        "aggregation_window_hours": 3,
        "temperature_metric": "low",
        "members_unit": "C",
        "city": "Shanghai",
        "lat": 31.25,
        "lon": 121.75,
        "unit": "C",
        "manifest_sha256": "2" * 64,
        "manifest_hash": "2" * 64,
        "issue_time_utc": issue.isoformat(),
        "target_date_local": target.isoformat(),
        "lead_day": 2,
        "lead_day_anchor": "issue_utc.date()",
        "timezone": "Asia/Shanghai",
        "local_day_window": {
            "start": "2026-07-31T16:00:00+00:00",
            "end": "2026-08-01T16:00:00+00:00",
        },
        "local_day_start_utc": "2026-07-31T16:00:00+00:00",
        "local_day_end_utc": "2026-08-01T16:00:00+00:00",
        "forecast_window_start_utc": "2026-07-31T18:00:00+00:00",
        "forecast_window_end_utc": "2026-08-01T15:00:00+00:00",
        "step_horizon_hours": 144.0,
        "step_horizon_deficit_hours": 0.0,
        "causality": {"status": "OK"},
        # Exact stale external-producer shape: any-member veto despite only 2/51.
        "boundary_ambiguous": True,
        "boundary_policy": {
            "boundary_ambiguous": True,
            "ambiguous_member_count": ambiguous_count,
            "training_rule": "drop_ambiguous_members",
        },
        "nearest_grid_lat": 31.25,
        "nearest_grid_lon": 121.75,
        "nearest_grid_distance_km": 13.0,
        "selected_step_ranges_inner": ["30-33", "33-36"],
        "selected_step_ranges_boundary": ["27-30", "123-126"],
        "member_count": 51,
        "missing_members": [],
        "training_allowed": False,
        "members": members,
    }


def _low_source_context() -> SourceRunContext:
    issue = datetime(2026, 7, 30, 12, tzinfo=UTC)
    return SourceRunContext(
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_db_reader",
        source_run_id="ecmwf_open_data:mn2t6_low:2026-07-30T12Z",
        release_calendar_key="ecmwf_open_data:mn2t6_low:full",
        source_cycle_time=issue,
        source_release_time=issue,
        source_available_at=datetime(2026, 7, 30, 20, 6, tzinfo=UTC),
    )


def test_minority_low_boundary_normalizes_into_current_evidence_shape(tmp_path: Path) -> None:
    """A stale producer veto cannot hide a usable minority-quarantined ENS shape."""

    conn = _conn()
    payload = _low_boundary_payload(ambiguous_count=2)
    path = tmp_path / "minority_low_snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = ingest_json_file(
        conn,
        path,
        metric=LOW_LOCALDAY_MIN,
        model_version="ecmwf_ens",
        overwrite=True,
        source_run_context=_low_source_context(),
    )

    assert status == "written"
    row = conn.execute("SELECT * FROM ensemble_snapshots").fetchone()
    assert row["boundary_ambiguous"] == 0
    assert row["ambiguous_member_count"] == 2
    assert row["training_allowed"] == 1
    assert row["causality_status"] == "OK"
    assert row["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert row["contributes_to_target_extrema"] == 1
    persisted_members = json.loads(row["members_json"])
    assert sum(value is None for value in persisted_members) == 2
    assert persisted_members[2] == 28.02
    provenance = json.loads(row["provenance_json"])
    normalization = provenance["boundary_normalization"]
    assert normalization["semantics_revision"] == LOW_BOUNDARY_SEMANTICS_REVISION
    assert normalization["artifact_manifest_sha256"] == "2" * 64
    assert len(normalization["raw_evidence_sha256"]) == 64
    assert normalization["raw_boundary_ambiguous"] is True
    assert normalization["raw_ambiguous_member_count"] == 2
    assert normalization["canonical_boundary_ambiguous"] is False
    assert normalization["canonical_ambiguous_member_count"] == 2
    assert normalization["quarantined_member_ids"] == [0, 1]
    assert normalization["invalid_member_ids"] == []
    assert normalization["member_decisions"][0] == {
        "member": 0,
        "decision": "quarantined",
        "reason": "quarantined_boundary_strictly_lower",
    }
    interval_evidence = provenance["low_local_day_min_interval_evidence"]
    assert interval_evidence["semantics_revision"] == (
        LOW_LOCAL_DAY_MIN_INTERVAL_EVIDENCE_REVISION
    )
    assert interval_evidence["members_unit"] == "C"
    assert interval_evidence["native_unit"] == "C"
    assert interval_evidence["selected_step_ranges_inner"] == ["30-33", "33-36"]
    assert interval_evidence["selected_step_ranges_boundary"] == ["27-30", "123-126"]
    assert interval_evidence["member_count"] == 51
    assert len(interval_evidence["member_records"]) == 51
    assert interval_evidence["member_records"][0] == {
        "member": 0,
        "raw_endpoints": {
            "inner_min_native_unit": 28.0,
            "boundary_min_native_unit": 27.0,
        },
        "lower_native_unit": 27.0,
        "upper_native_unit": 28.0,
        "exact": False,
        "status": "INTERVAL",
        "reason": "quarantined_boundary_strictly_lower",
    }
    assert interval_evidence["member_records"][2]["exact"] is True
    assert interval_evidence["member_records"][2]["lower_native_unit"] == 28.02
    assert interval_evidence["member_records"][2]["upper_native_unit"] == 28.02
    assert len(interval_evidence["identity_sha256"]) == 64

    from src.data.replacement_forecast_materializer import _read_current_evidence_shape

    target = date(2026, 8, 1)
    carrier = datetime(2026, 7, 30, 18, tzinfo=UTC)
    request = SimpleNamespace(
        city="Shanghai",
        target_date=target,
        source_cycle_time=carrier,
        computed_at=datetime(2026, 7, 31, 4, 16, 50, tzinfo=UTC),
    )
    shape = _read_current_evidence_shape(
        conn,
        request,
        metric="low",
        provider_values_c={"ecmwf_ifs": 28.2, "icon_global": 28.4},
        provider_weights={"ecmwf_ifs": 0.6, "icon_global": 0.4},
        center_c=28.28,
    )

    assert shape is not None
    assert shape.snapshot_id == row["snapshot_id"]
    assert shape.shape_lag_hours == 6.0
    assert shape.stale_shape_reused is True
    assert len(shape.members_c) == 49


def test_low_boundary_tie_restores_fully_inside_member_value() -> None:
    """A producer's retired <= comparison cannot quarantine an exact tie."""

    members = [
        {
            "member": member_id,
            "value_native_unit": (
                None if member_id == 0 else 99.0 if member_id == 1 else 28.0
            ),
            "inner_min_native_unit": 28.0,
            "boundary_min_native_unit": 28.0 if member_id == 0 else 29.0,
            "boundary_ambiguous": member_id == 0,
        }
        for member_id in range(51)
    ]
    normalized = normalize_low_boundary_evidence(
        {
            "temperature_metric": "low",
            "boundary_ambiguous": True,
            "boundary_policy": {
                "boundary_ambiguous": True,
                "ambiguous_member_count": 1,
            },
            "members": members,
        }
    )

    assert normalized["boundary_ambiguous"] is False
    assert normalized["boundary_policy"]["ambiguous_member_count"] == 0
    assert normalized["members"][0]["boundary_ambiguous"] is False
    assert normalized["members"][0]["value_native_unit"] == 28.0
    assert normalized["members"][1]["value_native_unit"] == 28.0


def test_low_without_boundary_window_accepts_null_boundary_extrema() -> None:
    """Null boundary evidence is valid only when no boundary bucket exists."""

    normalized = normalize_low_boundary_evidence(
        {
            "temperature_metric": "low",
            "selected_step_ranges_boundary": [],
            "members": [
                {
                    "member": member_id,
                    "value_native_unit": 99.0,
                    "inner_min_native_unit": 28.0,
                    "boundary_min_native_unit": None,
                    "boundary_ambiguous": False,
                }
                for member_id in range(51)
            ],
        }
    )

    evidence = normalized["boundary_normalization"]
    assert evidence["invalid_member_ids"] == []
    assert evidence["quarantined_member_ids"] == []
    assert {
        decision["reason"] for decision in evidence["member_decisions"]
    } == {"accepted_no_boundary_window"}
    assert all(
        member["value_native_unit"] == 28.0
        for member in normalized["members"]
    )


def test_invalid_low_boundary_member_fails_closed_end_to_end(tmp_path: Path) -> None:
    """NaN extrema are missing evidence, never a lawful minority quarantine."""

    conn = _conn()
    payload = _low_boundary_payload(
        ambiguous_count=2,
        invalid_inner_member_id=50,
        invalid_boundary_member_id=49,
        missing_boundary_member_id=48,
    )
    path = tmp_path / "invalid_low_snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = ingest_json_file(
        conn,
        path,
        metric=LOW_LOCALDAY_MIN,
        model_version="ecmwf_ens",
        overwrite=True,
        source_run_context=_low_source_context(),
    )

    assert status == "written"
    row = conn.execute("SELECT * FROM ensemble_snapshots").fetchone()
    assert row["boundary_ambiguous"] == 0
    assert row["ambiguous_member_count"] == 2
    assert row["training_allowed"] == 0
    assert row["causality_status"] == "UNKNOWN"
    assert row["contributes_to_target_extrema"] == 0
    assert "missing_forecast_members_for_contract_extrema" in json.loads(
        row["forecast_window_block_reasons_json"]
    )
    persisted_members = json.loads(row["members_json"])
    assert persisted_members[48] is None
    assert persisted_members[49] is None
    assert persisted_members[50] is None
    normalization = json.loads(row["provenance_json"])["boundary_normalization"]
    assert normalization["invalid_member_ids"] == [48, 49, 50]
    assert normalization["member_decisions"][48] == {
        "member": 48,
        "decision": "invalid",
        "reason": "invalid_missing_boundary_extrema",
    }
    assert normalization["member_decisions"][49] == {
        "member": 49,
        "decision": "invalid",
        "reason": "invalid_nonfinite_boundary_min",
    }
    assert normalization["member_decisions"][50] == {
        "member": 50,
        "decision": "invalid",
        "reason": "invalid_nonfinite_inner_min",
    }
    interval_evidence = json.loads(row["provenance_json"])[
        "low_local_day_min_interval_evidence"
    ]
    for member_id in (48, 49, 50):
        record = interval_evidence["member_records"][member_id]
        assert record["lower_native_unit"] is None
        assert record["upper_native_unit"] is None
        assert record["exact"] is None
        assert record["status"] == "INVALID"
    assert interval_evidence["member_records"][48]["reason"] == (
        "invalid_missing_boundary_extrema"
    )
    assert interval_evidence["member_records"][49]["reason"] == (
        "invalid_nonfinite_boundary_min"
    )
    assert interval_evidence["member_records"][50]["reason"] == (
        "invalid_nonfinite_inner_min"
    )

    from src.data.replacement_forecast_materializer import _read_current_evidence_shape

    request = SimpleNamespace(
        city="Shanghai",
        target_date=date(2026, 8, 1),
        source_cycle_time=datetime(2026, 7, 30, 18, tzinfo=UTC),
        computed_at=datetime(2026, 7, 31, 4, 16, 50, tzinfo=UTC),
    )
    assert (
        _read_current_evidence_shape(
            conn,
            request,
            metric="low",
            provider_values_c={"ecmwf_ifs": 28.2, "icon_global": 28.4},
            provider_weights={"ecmwf_ifs": 0.6, "icon_global": 0.4},
            center_c=28.28,
        )
        is None
    )


def test_exact_low_boundary_majority_fails_closed_end_to_end(tmp_path: Path) -> None:
    """The canonical 26/51 threshold blocks DB contribution and selection."""

    conn = _conn()
    path = tmp_path / "majority_low_snapshot.json"
    path.write_text(
        json.dumps(_low_boundary_payload(ambiguous_count=26)),
        encoding="utf-8",
    )

    status = ingest_json_file(
        conn,
        path,
        metric=LOW_LOCALDAY_MIN,
        model_version="ecmwf_ens",
        overwrite=True,
        source_run_context=_low_source_context(),
    )

    assert status == "written"
    row = conn.execute("SELECT * FROM ensemble_snapshots").fetchone()
    assert row["boundary_ambiguous"] == 1
    assert row["ambiguous_member_count"] == 26
    assert row["training_allowed"] == 0
    assert row["causality_status"] == "REJECTED_BOUNDARY_AMBIGUOUS"
    assert row["forecast_window_attribution_status"] == (
        "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    )
    assert row["contributes_to_target_extrema"] == 0
    persisted_members = json.loads(row["members_json"])
    assert sum(value is None for value in persisted_members) == 26
    interval_evidence = json.loads(row["provenance_json"])[
        "low_local_day_min_interval_evidence"
    ]
    assert len(interval_evidence["member_records"]) == 51
    assert sum(record["status"] == "INTERVAL" for record in interval_evidence["member_records"]) == 26
    assert row["training_allowed"] == 0
    assert row["causality_status"] == "REJECTED_BOUNDARY_AMBIGUOUS"

    from src.data.replacement_forecast_materializer import _read_current_evidence_shape

    request = SimpleNamespace(
        city="Shanghai",
        target_date=date(2026, 8, 1),
        source_cycle_time=datetime(2026, 7, 30, 18, tzinfo=UTC),
        computed_at=datetime(2026, 7, 31, 4, 16, 50, tzinfo=UTC),
    )
    assert (
        _read_current_evidence_shape(
            conn,
            request,
            metric="low",
            provider_values_c={"ecmwf_ifs": 28.2, "icon_global": 28.4},
            provider_weights={"ecmwf_ifs": 0.6, "icon_global": 0.4},
            center_c=28.28,
        )
        is None
    )


def test_low_no_boundary_window_persists_exact_inner_interval() -> None:
    payload = normalize_low_boundary_evidence(
        {
            "temperature_metric": "low",
            "unit": "C",
            "members_unit": "C",
            "selected_step_ranges_inner": ["12-18"],
            "selected_step_ranges_boundary": [],
            "members": [
                {
                    "member": member_id,
                    "inner_min_native_unit": 28.0 + member_id / 100.0,
                    "boundary_min_native_unit": None,
                }
                for member_id in range(51)
            ],
        }
    )
    evidence = _low_local_day_min_interval_evidence(payload)
    assert evidence is not None
    assert all(record["exact"] is True for record in evidence["member_records"])
    assert all(
        record["lower_native_unit"] == record["upper_native_unit"]
        for record in evidence["member_records"]
    )
    assert {
        record["reason"] for record in evidence["member_records"]
    } == {"accepted_no_boundary_window"}


def test_low_ingest_uses_metric_and_selected_range_fallback_for_interval_evidence(
    tmp_path: Path,
) -> None:
    payload = _low_boundary_payload(ambiguous_count=2)
    payload.pop("temperature_metric")
    payload["selected_step_ranges_inner"] = ["not-a-range"]
    payload["selected_step_ranges"] = ["54-60"]
    path = tmp_path / "low_metric_and_range_fallback.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    conn = _conn()

    status = ingest_json_file(
        conn,
        path,
        metric=LOW_LOCALDAY_MIN,
        model_version="ecmwf_ens",
        overwrite=True,
        source_run_context=_low_source_context(),
    )

    assert status == "written"
    row = conn.execute("SELECT provenance_json FROM ensemble_snapshots").fetchone()
    evidence = json.loads(row["provenance_json"])[
        "low_local_day_min_interval_evidence"
    ]
    assert evidence["selected_step_ranges_inner"] == ["54-60"]
    assert evidence["member_count"] == 51


def test_low_ingest_canonicalizes_missing_metric_before_no_boundary_normalization(
    tmp_path: Path,
) -> None:
    payload = _low_boundary_payload(ambiguous_count=0)
    payload.pop("temperature_metric")
    payload["selected_step_ranges_boundary"] = []
    payload["boundary_ambiguous"] = False
    payload["boundary_policy"] = {
        "boundary_ambiguous": False,
        "ambiguous_member_count": 0,
        "training_rule": "drop_ambiguous_members",
    }
    for member in payload["members"]:
        member["boundary_min_native_unit"] = None
        member["boundary_ambiguous"] = False
    path = tmp_path / "low_missing_metric_no_boundary.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    conn = _conn()

    status = ingest_json_file(
        conn,
        path,
        metric=LOW_LOCALDAY_MIN,
        model_version="ecmwf_ens",
        overwrite=True,
        source_run_context=_low_source_context(),
    )

    assert status == "written"
    row = conn.execute("SELECT provenance_json FROM ensemble_snapshots").fetchone()
    records = json.loads(row["provenance_json"])[
        "low_local_day_min_interval_evidence"
    ]["member_records"]
    assert len(records) == 51
    assert {record["status"] for record in records} == {"EXACT"}
    assert {record["reason"] for record in records} == {
        "accepted_no_boundary_window"
    }


def test_low_interval_provenance_identity_is_json_deterministic() -> None:
    normalized = normalize_low_boundary_evidence(_low_boundary_payload(ambiguous_count=2))
    reordered = {
        **normalized,
        "members": [
            {key: member[key] for key in reversed(list(member))}
            for member in normalized["members"]
        ],
    }
    first = _low_local_day_min_interval_evidence(normalized)
    second = _low_local_day_min_interval_evidence(reordered)
    assert first == second
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
    assert first is not None
    identity_keys = {
        "semantics_revision",
        "members_unit",
        "native_unit",
        "selected_step_ranges_inner",
        "selected_step_ranges_boundary",
        "member_records",
    }
    identity_material = {key: first[key] for key in identity_keys}
    expected_hash = hashlib.sha256(
        json.dumps(
            identity_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert first["identity_sha256"] == expected_hash

    mutated = json.loads(json.dumps(normalized))
    mutated["members"][0]["inner_min_native_unit"] += 0.25
    changed = _low_local_day_min_interval_evidence(mutated)
    assert changed is not None
    assert changed["identity_sha256"] != first["identity_sha256"]
