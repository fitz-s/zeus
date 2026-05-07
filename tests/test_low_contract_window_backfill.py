# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Authority basis: docs/operations/task_2026-05-06_calibration_quality_blockers/PLAN.md Slice C
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.backfill_low_contract_window_evidence import (
    LOW_RECOVERY_SOURCES,
    _default_fifty_one_raw_root,
    run_backfill,
)
import scripts.backfill_low_contract_window_evidence as backfill_module
from scripts.ingest_grib_to_snapshots import _contract_evidence_fields
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import LOW_LOCALDAY_MIN


def _payload(
    *,
    boundary_ambiguous: bool = False,
    data_version: str | None = None,
) -> dict:
    members = [
        {
            "member": idx,
            "value_native_unit": None if boundary_ambiguous else 60.0 + idx * 0.01,
            "inner_min_native_unit": 60.0 + idx * 0.01,
            "boundary_min_native_unit": 58.0 if boundary_ambiguous else 65.0,
            "boundary_ambiguous": boundary_ambiguous,
        }
        for idx in range(51)
    ]
    return {
        "generated_at": "2026-05-07T00:00:00+00:00",
        "data_version": data_version or LOW_LOCALDAY_MIN.data_version,
        "physical_quantity": LOW_LOCALDAY_MIN.physical_quantity,
        "param": "122.128",
        "paramId": 122,
        "short_name": "mn2t6",
        "step_type": "min",
        "aggregation_window_hours": 6,
        "city": "Chicago",
        "lat": 41.8781,
        "lon": -87.6298,
        "unit": "F",
        "timezone": "America/Chicago",
        "manifest_sha256": "sha256:test",
        "manifest_hash": "hash:test",
        "issue_time_utc": "2026-05-30T00:00:00+00:00",
        "target_date_local": "2026-06-01",
        "lead_day": 2,
        "lead_day_anchor": "target_local_date",
        "local_day_start_utc": "2026-06-01T05:00:00+00:00",
        "local_day_end_utc": "2026-06-02T05:00:00+00:00",
        "local_day_window": {
            "start_utc": "2026-06-01T05:00:00+00:00",
            "end_utc": "2026-06-02T05:00:00+00:00",
        },
        "step_horizon_hours": 60,
        "step_horizon_deficit_hours": 0,
        "causality": {"status": "OK"},
        "boundary_ambiguous": boundary_ambiguous,
        "boundary_policy": {
            "training_rule": "drop_ambiguous_members",
            "boundary_ambiguous": boundary_ambiguous,
            "ambiguous_member_count": 51 if boundary_ambiguous else 0,
        },
        "nearest_grid_lat": 41.875,
        "nearest_grid_lon": -87.625,
        "nearest_grid_distance_km": 0.5,
        "member_count": 51,
        "missing_members": [],
        "training_allowed": not boundary_ambiguous,
        "temperature_metric": "low",
        "members_unit": "degF",
        "selected_step_ranges_inner": ["54-60"],
        "selected_step_ranges_boundary": ["48-54", "72-78"],
        "members": members,
    }


def _write_payload(root: Path, payload: dict) -> Path:
    path = (
        root
        / "tigge_ecmwf_ens_mn2t6_localday_min"
        / "chicago"
        / "20260530"
        / "tigge_ecmwf_ens_mn2t6_localday_min_target_2026-06-01_lead_2.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours,
            members_json, model_version, data_version, training_allowed,
            causality_status, authority, members_unit, provenance_json
        ) VALUES (?, ?, 'low', ?, 'low_temp', ?, ?, ?, ?, ?, ?, 'ENS', ?, ?, ?, 'VERIFIED', 'degF', ?)
        """,
        (
            "Chicago",
            "2026-06-01",
            LOW_LOCALDAY_MIN.physical_quantity,
            "2026-05-30T00:00:00+00:00",
            "2026-06-01",
            "2026-05-30T08:00:00+00:00",
            "2026-05-30T08:05:00+00:00",
            48.0,
            json.dumps([60.0 + idx * 0.01 for idx in range(51)]),
            LOW_LOCALDAY_MIN.data_version,
            0,
            "REJECTED_BOUNDARY_AMBIGUOUS",
            json.dumps({"legacy": True}),
        ),
    )
    conn.commit()
    return conn


def _recovery_count(conn: sqlite3.Connection) -> int:
    source = LOW_RECOVERY_SOURCES["tigge_mars"]
    return conn.execute(
        "SELECT COUNT(*) FROM ensemble_snapshots_v2 WHERE data_version = ?",
        (source.recovery_data_version,),
    ).fetchone()[0]


def test_low_contract_window_backfill_dry_run_does_not_write(tmp_path: Path):
    conn = _make_db()
    _write_payload(tmp_path, _payload())

    reports = run_backfill(
        conn=conn,
        json_root=tmp_path,
        sources=[LOW_RECOVERY_SOURCES["tigge_mars"]],
        dry_run=True,
        force=False,
    )

    stats = reports["tigge_mars"]
    assert stats.would_insert == 1
    assert stats.training_candidates == 1
    assert stats.inserted == 0
    assert _recovery_count(conn) == 0


def test_low_contract_window_backfill_default_raw_root_handles_linked_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    worktree_project = tmp_path / "worktrees" / "zeus-low-high-branch"
    raw_root = tmp_path / "workspace-venus" / "51 source data" / "raw"
    worktree_project.mkdir(parents=True)
    raw_root.mkdir(parents=True)
    monkeypatch.setattr(backfill_module, "PROJECT_ROOT", worktree_project)

    assert _default_fifty_one_raw_root() == raw_root


def test_low_contract_window_backfill_apply_requires_force(tmp_path: Path):
    conn = _make_db()
    _write_payload(tmp_path, _payload())

    with pytest.raises(RuntimeError, match="--apply requires --force"):
        run_backfill(
            conn=conn,
            json_root=tmp_path,
            sources=[LOW_RECOVERY_SOURCES["tigge_mars"]],
            dry_run=False,
            force=False,
        )


def test_low_contract_window_backfill_apply_inserts_recovery_row(tmp_path: Path):
    conn = _make_db()
    _write_payload(tmp_path, _payload())
    source = LOW_RECOVERY_SOURCES["tigge_mars"]

    reports = run_backfill(
        conn=conn,
        json_root=tmp_path,
        sources=[source],
        dry_run=False,
        force=True,
    )

    assert reports["tigge_mars"].inserted == 1
    row = conn.execute(
        """
        SELECT data_version, training_allowed, causality_status,
               forecast_window_attribution_status, contributes_to_target_extrema,
               forecast_window_block_reasons_json, provenance_json
        FROM ensemble_snapshots_v2
        WHERE data_version = ?
        """,
        (source.recovery_data_version,),
    ).fetchone()
    assert row["training_allowed"] == 1
    assert row["causality_status"] == "OK"
    assert row["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert row["contributes_to_target_extrema"] == 1
    assert json.loads(row["forecast_window_block_reasons_json"]) == []
    assert json.loads(row["provenance_json"])["low_contract_window_backfill"][
        "live_promotion_authorized"
    ] is False


def test_low_contract_evidence_uses_inner_ranges_not_boundary_envelope():
    payload = _payload()
    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence["contributes_to_target_extrema"] == 1
    assert json.loads(evidence["forecast_window_block_reasons_json"]) == []
    assert evidence["forecast_window_start_local"].startswith("2026-06-01T01:00:00")
    assert evidence["forecast_window_end_local"].startswith("2026-06-01T07:00:00")


def test_low_contract_evidence_falls_back_to_selected_step_ranges_when_inner_missing():
    payload = _payload()
    payload["selected_step_ranges"] = ["54-60"]
    payload["selected_step_ranges_inner"] = []

    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence["contributes_to_target_extrema"] == 1
    assert json.loads(evidence["forecast_window_block_reasons_json"]) == []
    assert evidence["forecast_window_start_local"].startswith("2026-06-01T01:00:00")
    assert evidence["forecast_window_end_local"].startswith("2026-06-01T07:00:00")


def test_low_contract_evidence_matches_paris_inner_min_physical_semantics():
    payload = _payload()
    payload.update({
        "city": "Paris",
        "lat": 48.8566,
        "lon": 2.3522,
        "timezone": "Europe/Paris",
        "unit": "C",
        "members_unit": "degC",
        "issue_time_utc": "2025-07-09T00:00:00+00:00",
        "target_date_local": "2025-07-13",
        "lead_day": 4,
        "local_day_start_utc": "2025-07-12T22:00:00+00:00",
        "local_day_end_utc": "2025-07-13T22:00:00+00:00",
        "selected_step_ranges_inner": ["102-108", "108-114", "96-102"],
        "selected_step_ranges_boundary": ["114-120", "90-96"],
    })

    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence["contributes_to_target_extrema"] == 1
    assert json.loads(evidence["forecast_window_block_reasons_json"]) == []
    assert evidence["forecast_window_start_local"] == "2025-07-13T02:00:00+02:00"
    assert evidence["forecast_window_end_local"] == "2025-07-13T20:00:00+02:00"


def test_low_contract_evidence_missing_member_value_blocks_training_authority():
    payload = _payload(boundary_ambiguous=False)
    payload["members"][7]["value_native_unit"] = None

    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence["contributes_to_target_extrema"] == 0
    assert "missing_member_value_for_contract_extrema" in json.loads(
        evidence["forecast_window_block_reasons_json"]
    )


def test_low_contract_evidence_invalid_timezone_blocks_without_raising():
    payload = _payload()
    payload["timezone"] = "Not/A_Real_Timezone"

    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["forecast_window_attribution_status"] == "UNKNOWN"
    assert evidence["contributes_to_target_extrema"] == 0
    assert "invalid_city_timezone" in json.loads(
        evidence["forecast_window_block_reasons_json"]
    )


def test_low_contract_evidence_nondict_boundary_policy_does_not_raise():
    payload = _payload()
    payload["boundary_policy"] = "malformed"

    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence["contributes_to_target_extrema"] == 1
    assert json.loads(evidence["forecast_window_block_reasons_json"]) == []


def test_low_contract_window_backfill_preserves_non_boundary_block_reason(tmp_path: Path):
    conn = _make_db()
    payload = _payload(boundary_ambiguous=False)
    payload["members"][3]["value_native_unit"] = None
    _write_payload(tmp_path, payload)
    source = LOW_RECOVERY_SOURCES["tigge_mars"]

    reports = run_backfill(
        conn=conn,
        json_root=tmp_path,
        sources=[source],
        dry_run=False,
        force=True,
    )

    assert reports["tigge_mars"].blocked_candidates == 1
    row = conn.execute(
        """
        SELECT training_allowed, causality_status, boundary_ambiguous,
               forecast_window_attribution_status, forecast_window_block_reasons_json
        FROM ensemble_snapshots_v2
        WHERE data_version = ?
        """,
        (source.recovery_data_version,),
    ).fetchone()
    assert row["training_allowed"] == 0
    assert row["causality_status"] == "UNKNOWN"
    assert row["boundary_ambiguous"] == 0
    assert row["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert "missing_member_value_for_contract_extrema" in json.loads(
        row["forecast_window_block_reasons_json"]
    )


def test_low_contract_window_backfill_ambiguous_window_stays_blocked(tmp_path: Path):
    conn = _make_db()
    _write_payload(tmp_path, _payload(boundary_ambiguous=True))
    source = LOW_RECOVERY_SOURCES["tigge_mars"]

    reports = run_backfill(
        conn=conn,
        json_root=tmp_path,
        sources=[source],
        dry_run=False,
        force=True,
    )

    assert reports["tigge_mars"].blocked_candidates == 1
    row = conn.execute(
        """
        SELECT training_allowed, causality_status, forecast_window_attribution_status,
               forecast_window_block_reasons_json
        FROM ensemble_snapshots_v2
        WHERE data_version = ?
        """,
        (source.recovery_data_version,),
    ).fetchone()
    assert row["training_allowed"] == 0
    assert row["causality_status"] == "REJECTED_BOUNDARY_AMBIGUOUS"
    assert row["forecast_window_attribution_status"] == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    assert "boundary_ambiguous" in json.loads(row["forecast_window_block_reasons_json"])
