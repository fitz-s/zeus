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
from src.state.schema.v2_schema import apply_canonical_schema
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


def _payload_mixed_members(
    *,
    quarantined_count: int,
    genuinely_missing_count: int = 0,
    total: int = 51,
) -> dict:
    """Minority/majority-ambiguous payload with per-member boundary_ambiguous mix.

    ``quarantined_count`` members are individually boundary-ambiguous (nulled by
    the boundary rule, per extract_open_ens_localday.py:574-580 -- a lawful
    exclusion, not missing data). ``genuinely_missing_count`` members have no
    inner or boundary data at all (a real ingest gap). The snapshot-level
    ``boundary_ambiguous`` flag follows the majority threshold (>=26/51), same
    as extract_open_ens_localday.py:596-598.
    """
    majority_threshold = max(1, total // 2 + 1)
    snapshot_boundary_ambiguous = quarantined_count >= majority_threshold
    members = []
    missing_members: list[int] = []
    for idx in range(total):
        if idx < genuinely_missing_count:
            members.append({
                "member": idx,
                "value_native_unit": None,
                "inner_min_native_unit": None,
                "boundary_min_native_unit": None,
                "boundary_ambiguous": False,
            })
            missing_members.append(idx)
        elif idx < genuinely_missing_count + quarantined_count:
            members.append({
                "member": idx,
                "value_native_unit": None,
                "inner_min_native_unit": 60.0 + idx * 0.01,
                "boundary_min_native_unit": 58.0,
                "boundary_ambiguous": True,
            })
        else:
            members.append({
                "member": idx,
                "value_native_unit": 60.0 + idx * 0.01,
                "inner_min_native_unit": 60.0 + idx * 0.01,
                "boundary_min_native_unit": 65.0,
                "boundary_ambiguous": False,
            })
    return {
        "generated_at": "2026-05-07T00:00:00+00:00",
        "data_version": LOW_LOCALDAY_MIN.data_version,
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
        "boundary_ambiguous": snapshot_boundary_ambiguous,
        "boundary_policy": {
            "training_rule": "drop_ambiguous_members",
            "boundary_ambiguous": snapshot_boundary_ambiguous,
            "ambiguous_member_count": quarantined_count,
        },
        "nearest_grid_lat": 41.875,
        "nearest_grid_lon": -87.625,
        "nearest_grid_distance_km": 0.5,
        "member_count": total,
        "missing_members": missing_members,
        "training_allowed": snapshot_boundary_ambiguous is False and not missing_members,
        "temperature_metric": "low",
        "members_unit": "degF",
        "selected_step_ranges_inner": ["54-60"],
        "selected_step_ranges_boundary": ["48-54", "72-78"],
        "members": members,
    }


def _write_payload(root: Path, payload: dict, *, suffix: str = "") -> Path:
    path = (
        root
        / "tigge_ecmwf_ens_mn2t6_localday_min"
        / "chicago"
        / "20260530"
        / f"tigge_ecmwf_ens_mn2t6_localday_min_target_2026-06-01_lead_2{suffix}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours,
            members_json, model_version, dataset_id, training_allowed,
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
        "SELECT COUNT(*) FROM ensemble_snapshots WHERE dataset_id = ?",
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


def test_low_contract_window_backfill_cycle_filter_counts_and_skips(tmp_path: Path):
    conn = _make_db()
    _write_payload(tmp_path, _payload(), suffix="_00")
    payload_12 = _payload()
    payload_12["issue_time_utc"] = "2026-05-30T12:00:00+00:00"
    _write_payload(tmp_path, payload_12, suffix="_12")

    reports = run_backfill(
        conn=conn,
        json_root=tmp_path,
        sources=[LOW_RECOVERY_SOURCES["tigge_mars"]],
        dry_run=True,
        force=False,
        cycle="00",
    )

    stats = reports["tigge_mars"]
    assert stats.files_scanned == 2
    assert stats.by_cycle == {"00": 1, "12": 1}
    assert stats.cycle_filtered == 1
    assert stats.would_insert == 1
    assert stats.no_matching_snapshot == 0


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
        SELECT dataset_id, training_allowed, causality_status,
               forecast_window_attribution_status, contributes_to_target_extrema,
               forecast_window_block_reasons_json, provenance_json
        FROM ensemble_snapshots
        WHERE dataset_id = ?
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
        FROM ensemble_snapshots
        WHERE dataset_id = ?
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
        FROM ensemble_snapshots
        WHERE dataset_id = ?
        """,
        (source.recovery_data_version,),
    ).fetchone()
    assert row["training_allowed"] == 0
    assert row["causality_status"] == "REJECTED_BOUNDARY_AMBIGUOUS"
    assert row["forecast_window_attribution_status"] == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    assert "boundary_ambiguous" in json.loads(row["forecast_window_block_reasons_json"])


# ---------------------------------------------------------------------------
# Boundary-quarantine vs. genuinely-missing (P0 fix regression).
#
# Root cause: extract_open_ens_localday.py nulls a member's value when ITS OWN
# boundary_ambiguous flag is True (leakage law: a boundary-crossing value must
# never enter the local-day minimum). The snapshot-level majority rule
# (extract_open_ens_localday.py:596-598) separately decides whether the WHOLE
# snapshot is too ambiguous to use (>=26/51 quarantined). Before this fix,
# _missing_contract_extrema_member_reasons treated ANY null member value as
# "missing" regardless of the majority verdict, so a minority-ambiguous day
# (e.g. 10/51 lawfully quarantined, snapshot-level boundary_ambiguous=False)
# still got contributes_to_target_extrema=0 for the WHOLE snapshot -- the
# materializer's ENS query (replacement_forecast_materializer.py) then walked
# back to a stale prior-day snapshot. Quarantine is a lawful exclusion, not a
# missing value; only the classification of the block reason changes here --
# the quarantined member's boundary value never enters members_json as
# anything but null (leakage law preserved).
# ---------------------------------------------------------------------------


def test_low_contract_evidence_minority_quarantine_contributes():
    """10/51 quarantined, snapshot-level boundary_ambiguous=False -> contributes=1."""
    payload = _payload_mixed_members(quarantined_count=10)
    assert payload["boundary_ambiguous"] is False, "10/51 is below the 26/51 majority threshold"

    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence["contributes_to_target_extrema"] == 1
    assert json.loads(evidence["forecast_window_block_reasons_json"]) == []

    # Leakage law: quarantined members' values are never populated, whatever
    # the block-reason classification. Only the 41 non-quarantined members are
    # finite; the local-day minimum must be computed from exactly those.
    finite_values = [
        m["value_native_unit"] for m in payload["members"] if m["value_native_unit"] is not None
    ]
    assert len(finite_values) == 41
    quarantined_values = [
        m["value_native_unit"] for m in payload["members"] if m["boundary_ambiguous"]
    ]
    assert all(v is None for v in quarantined_values), (
        "a quarantined member's boundary value must never enter the extrema computation"
    )


def test_low_contract_evidence_majority_quarantine_still_blocks():
    """26/51 quarantined, snapshot-level boundary_ambiguous=True -> unchanged (blocked)."""
    payload = _payload_mixed_members(quarantined_count=26)
    assert payload["boundary_ambiguous"] is True, "26/51 meets the majority threshold"

    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["contributes_to_target_extrema"] == 0
    assert evidence["forecast_window_attribution_status"] == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    assert "boundary_ambiguous" in json.loads(evidence["forecast_window_block_reasons_json"])


def test_low_contract_evidence_genuine_missing_member_still_blocks():
    """A real ingest gap (no inner/boundary data at all) still blocks contributes.

    Distinguishes the fix from a blanket pass: even with 0 boundary-quarantined
    members, a snapshot with a genuinely missing member (present in
    missing_members, not merely boundary-nulled) must still fail closed.
    """
    payload = _payload_mixed_members(quarantined_count=0, genuinely_missing_count=2)
    assert payload["boundary_ambiguous"] is False
    assert payload["missing_members"] == [0, 1]

    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["contributes_to_target_extrema"] == 0
    reasons = json.loads(evidence["forecast_window_block_reasons_json"])
    assert "missing_forecast_members_for_contract_extrema" in reasons
    assert "missing_member_value_for_contract_extrema" in reasons


def test_low_contract_evidence_minority_quarantine_plus_genuine_gap_still_blocks():
    """Mixed case: minority quarantine (lawful) alongside a genuine gap (unlawful).

    The genuine gap must still surface as a block reason even though the
    quarantined members no longer do.
    """
    payload = _payload_mixed_members(quarantined_count=10, genuinely_missing_count=1)
    assert payload["boundary_ambiguous"] is False

    evidence = _contract_evidence_fields(
        payload,
        LOW_LOCALDAY_MIN,
        source_id="tigge_mars",
    )

    assert evidence["contributes_to_target_extrema"] == 0
    reasons = json.loads(evidence["forecast_window_block_reasons_json"])
    assert "missing_forecast_members_for_contract_extrema" in reasons
    assert "missing_member_value_for_contract_extrema" in reasons
