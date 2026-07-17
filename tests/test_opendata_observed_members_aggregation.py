# Created: 2026-05-30
# Last reused/audited: 2026-05-30
# Authority basis: Live shadow seamhunt 2026-05-30 — source_run.observed_members
#   was min()-poisoned to 0 by boundary-ambiguous all-null snapshot rows, which
#   set source_run_completeness_status=PARTIAL and vetoed every positive-edge
#   certificate at decision_kernel/compiler._validate_forecast_authority_payload.
#   This is the antibody: the run-level member count must aggregate over only the
#   snapshots that contribute to a target extrema window.
"""Relationship antibody: source_run.observed_members ignores non-contributing rows.

Cross-module invariant under test:

  ensemble_snapshots (per-target window completeness, with
  contributes_to_target_extrema attribution)
        --> _write_source_authority_chain (run-level member aggregation)
        --> source_run.completeness_status
        --> decision certificate authority gate

A boundary-ambiguous / far-horizon-overflow snapshot is written as an all-null
placeholder with contributes_to_target_extrema=0. It must NOT drag the run-level
observed_members down: the certificate gates a *contributing* window, and the
contributing windows here each carry the full 51-member ensemble.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.data.ecmwf_open_data import SOURCE_ID, _write_source_authority_chain
from src.state.db import init_schema_forecasts
from src.state.source_run_repo import get_source_run

_SOURCE_TRANSPORT = "ensemble_snapshots_db_reader"
_DATA_VERSION = "ecmwf_opendata_mx2t3_local_calendar_day_max"


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    city: str,
    target_date: str,
    source_run_id: str,
    members_json: str,
    contributes: int,
    attribution_status: str,
    manifest_hash: str = "a" * 64,
    coordinate_manifest_sha: str = "m" * 64,
    boundary_ambiguous: int = 0,
    ambiguous_member_count: int = 0,
    local_day_start_utc: str = "2026-05-30T00:00:00+00:00",
) -> None:
    provenance_json = json.dumps({"manifest_sha256": coordinate_manifest_sha})
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity,
            observation_field, available_at, fetch_time, lead_hours, members_json,
            model_version, dataset_id, training_allowed, causality_status,
            boundary_ambiguous, ambiguous_member_count, manifest_hash, provenance_json, authority,
            recorded_at, members_unit, step_horizon_hours, local_day_start_utc,
            source_id, source_transport, source_run_id, release_calendar_key,
            source_cycle_time, source_release_time, source_available_at,
            contributes_to_target_extrema, forecast_window_attribution_status
        ) VALUES (
            :snapshot_id, :city, :target_date, 'high', 'mx2t3_local_calendar_day_max',
            'high_temp', '2026-05-30T12:00:00+00:00', '2026-05-30T12:00:00+00:00', 120.0, :members_json,
            'ecmwf', :dataset_id, 0, 'OK',
            :boundary_ambiguous, :ambiguous_member_count, :manifest_hash, :provenance_json, 'VERIFIED',
            '2026-05-30T12:00:00+00:00', 'degC', 120.0, :local_day_start_utc,
            :source_id, :source_transport, :source_run_id, '2026-05-30T12Z',
            '2026-05-30T12:00:00+00:00', '2026-05-30T12:00:00+00:00', '2026-05-30T12:00:00+00:00',
            :contributes, :attribution_status
        )
        """,
        {
            "snapshot_id": snapshot_id,
            "city": city,
            "target_date": target_date,
            "members_json": members_json,
            "dataset_id": _DATA_VERSION,
            "source_id": SOURCE_ID,
            "source_transport": _SOURCE_TRANSPORT,
            "source_run_id": source_run_id,
            "contributes": contributes,
            "attribution_status": attribution_status,
            "manifest_hash": manifest_hash,
            "provenance_json": provenance_json,
            "boundary_ambiguous": boundary_ambiguous,
            "ambiguous_member_count": ambiguous_member_count,
            "local_day_start_utc": local_day_start_utc,
        },
    )


@pytest.fixture()
def forecasts_conn():
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "forecasts.db")
        conn.row_factory = sqlite3.Row
        init_schema_forecasts(conn)
        yield conn
        conn.close()


def test_observed_members_ignores_noncontributing_null_rows(forecasts_conn):
    """RED before fix: min() over ALL rows -> observed_members=0 -> PARTIAL.

    GREEN after fix: aggregation over contributing rows -> observed_members=51 -> COMPLETE.
    """
    conn = forecasts_conn
    source_run_id = "ecmwf_open_data:mx2t6_high:2026-05-30T12Z"
    full_members = json.dumps([20.0 + i * 0.01 for i in range(51)])
    null_members = json.dumps([None] * 51)

    # Contributing window: full 51-member ensemble.
    _insert_snapshot(
        conn,
        snapshot_id=1,
        city="London",
        target_date="2026-05-31",
        source_run_id=source_run_id,
        members_json=full_members,
        contributes=1,
        attribution_status="FULLY_INSIDE_TARGET_LOCAL_DAY",
    )
    # Non-contributing boundary-ambiguous window: all-null placeholder.
    _insert_snapshot(
        conn,
        snapshot_id=2,
        city="Auckland",
        target_date="2026-05-30",
        source_run_id=source_run_id,
        members_json=null_members,
        contributes=0,
        attribution_status="AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY",
    )
    conn.commit()

    from datetime import datetime, timezone

    cycle = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
    _write_source_authority_chain(
        conn,
        summary={"written": 2, "errors": 0},
        status="ok",
        source_run_id=source_run_id,
        source_cycle_time=cycle,
        source_release_time=cycle,
        release_calendar_key="2026-05-30T12Z",
        forecast_track="mx2t6_high",
        data_version=_DATA_VERSION,
        computed_at=cycle,
    )
    conn.commit()

    row = get_source_run(conn, source_run_id)
    assert row is not None, "source_run row must be written"
    assert (
        int(row["observed_members"]) == 51
    ), f"contributing window carried 51 members; got observed_members={row['observed_members']}"
    assert (
        row["completeness_status"] == "COMPLETE"
    ), f"all contributing windows full -> COMPLETE; got {row['completeness_status']} ({row['reason_code']})"
    assert row["manifest_hash"] == "m" * 64


def test_genuine_partial_contributing_window_still_drops(forecasts_conn):
    """Fail-closed guard: a contributing window short of 51 members stays PARTIAL.

    Proves the fix did not become a blanket pass — a real ingest gap on a
    target-contributing window is still demoted to PARTIAL/MISSING_EXPECTED_MEMBERS.
    """
    conn = forecasts_conn
    source_run_id = "ecmwf_open_data:mx2t6_high:2026-05-30T00Z"
    partial_members = json.dumps([20.0 + i * 0.01 for i in range(40)] + [None] * 11)

    _insert_snapshot(
        conn,
        snapshot_id=10,
        city="London",
        target_date="2026-05-31",
        source_run_id=source_run_id,
        members_json=partial_members,
        contributes=1,
        attribution_status="FULLY_INSIDE_TARGET_LOCAL_DAY",
    )
    conn.commit()

    from datetime import datetime, timezone

    cycle = datetime(2026, 5, 30, 0, tzinfo=timezone.utc)
    authority = _write_source_authority_chain(
        conn,
        summary={"written": 1, "errors": 0},
        status="ok",
        source_run_id=source_run_id,
        source_cycle_time=cycle,
        source_release_time=cycle,
        release_calendar_key="2026-05-30T00Z",
        forecast_track="mx2t6_high",
        data_version=_DATA_VERSION,
        computed_at=cycle,
        download_observed_steps=[3],
        download_partial_run=False,
    )
    conn.commit()

    row = get_source_run(conn, source_run_id)
    assert row is not None
    assert int(row["observed_members"]) == 40
    assert row["completeness_status"] == "PARTIAL"
    assert row["reason_code"] == "MISSING_EXPECTED_MEMBERS"
    assert int(row["partial_run"]) == 1
    assert not authority["run_complete_time"]


def test_mixed_snapshot_coordinate_manifest_shas_fail_closed(forecasts_conn):
    """A source_run cannot be LIVE authority if its snapshots came from mixed coordinate manifests."""
    conn = forecasts_conn
    source_run_id = "ecmwf_open_data:mx2t6_high:2026-05-30T06Z"
    full_members = json.dumps([20.0 + i * 0.01 for i in range(51)])

    _insert_snapshot(
        conn,
        snapshot_id=20,
        city="London",
        target_date="2026-05-31",
        source_run_id=source_run_id,
        members_json=full_members,
        contributes=1,
        attribution_status="FULLY_INSIDE_TARGET_LOCAL_DAY",
        manifest_hash="a" * 64,
        coordinate_manifest_sha="m" * 64,
    )
    _insert_snapshot(
        conn,
        snapshot_id=21,
        city="Auckland",
        target_date="2026-05-31",
        source_run_id=source_run_id,
        members_json=full_members,
        contributes=1,
        attribution_status="FULLY_INSIDE_TARGET_LOCAL_DAY",
        manifest_hash="b" * 64,
        coordinate_manifest_sha="n" * 64,
    )
    conn.commit()

    from datetime import datetime, timezone

    cycle = datetime(2026, 5, 30, 6, tzinfo=timezone.utc)
    _write_source_authority_chain(
        conn,
        summary={"written": 2, "errors": 0},
        status="ok",
        source_run_id=source_run_id,
        source_cycle_time=cycle,
        source_release_time=cycle,
        release_calendar_key="2026-05-30T06Z",
        forecast_track="mx2t6_high",
        data_version=_DATA_VERSION,
        computed_at=cycle,
    )
    conn.commit()

    row = get_source_run(conn, source_run_id)
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["completeness_status"] == "MISSING"
    assert row["manifest_hash"] is None
    assert row["reason_code"] == "SNAPSHOT_COORDINATE_MANIFEST_SHA_MISMATCH"


def test_minority_boundary_quarantine_coverage_not_blocked_on_missing_members(forecasts_conn):
    """P0 fix regression: minority-quarantined members must not read as MISSING_EXPECTED_MEMBERS.

    10/51 members are lawfully boundary-quarantined (nulled by the extractor's
    per-member leakage-law rule) while the snapshot-level majority verdict says
    the day IS usable (boundary_ambiguous=0, ambiguous_member_count=10 -- below
    the 26/51 threshold). Before this fix, source_run_coverage compared the raw
    41-finite-member count against a fixed expected_members=51 and blocked with
    MISSING_EXPECTED_MEMBERS/BLOCKED even though contributes_to_target_extrema=1.
    After the fix, expected_members is lowered to 41 (51 - ambiguous_member_count)
    for this row only, since boundary_ambiguous=0 (minority, lawful exclusion).
    """
    conn = forecasts_conn
    source_run_id = "ecmwf_open_data:mx2t6_high:2026-05-30T00Z:minority"
    quarantined = 10
    mixed_members = json.dumps([20.0 + i * 0.01 for i in range(51 - quarantined)] + [None] * quarantined)

    _insert_snapshot(
        conn,
        snapshot_id=30,
        city="London",
        target_date="2026-05-31",
        source_run_id=source_run_id,
        members_json=mixed_members,
        contributes=1,
        attribution_status="FULLY_INSIDE_TARGET_LOCAL_DAY",
        boundary_ambiguous=0,
        ambiguous_member_count=quarantined,
        # London 2026-05-31 local midnight is BST (UTC+1) -> 2026-05-30T23:00 UTC.
        # Must match compute_target_local_day_window_utc's own answer, or the
        # per-scope coverage decision reads SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH
        # (a fixture-alignment concern, unrelated to this fix).
        local_day_start_utc="2026-05-30T23:00:00+00:00",
    )
    conn.commit()

    from datetime import datetime, timezone

    cycle = datetime(2026, 5, 30, 0, tzinfo=timezone.utc)
    _write_source_authority_chain(
        conn,
        summary={"written": 1, "errors": 0},
        status="ok",
        source_run_id=source_run_id,
        source_cycle_time=cycle,
        source_release_time=cycle,
        release_calendar_key="2026-05-30T00Z",
        forecast_track="mx2t6_high",
        data_version=_DATA_VERSION,
        computed_at=cycle,
    )
    conn.commit()

    source_run = get_source_run(conn, source_run_id)
    assert source_run is not None
    assert source_run["status"] == "SUCCESS"
    assert source_run["completeness_status"] == "COMPLETE"
    assert int(source_run["observed_members"]) == 51

    coverage_row = conn.execute(
        """
        SELECT expected_members, observed_members, completeness_status,
               readiness_status, reason_code
        FROM source_run_coverage
        WHERE source_run_id = ? AND city = ?
        """,
        (source_run_id, "London"),
    ).fetchone()
    assert coverage_row is not None, "source_run_coverage row must be written for London"
    assert int(coverage_row["observed_members"]) == 41
    assert int(coverage_row["expected_members"]) == 41, (
        "expected_members must be lowered by the lawfully-quarantined count, not left at 51"
    )
    assert coverage_row["reason_code"] != "MISSING_EXPECTED_MEMBERS"
    assert coverage_row["completeness_status"] == "COMPLETE"
    assert coverage_row["readiness_status"] == "LIVE_ELIGIBLE"


def test_majority_boundary_quarantine_coverage_still_blocked(forecasts_conn):
    """Majority-ambiguous (boundary_ambiguous=1) rows keep the full 51 expectation."""
    conn = forecasts_conn
    source_run_id = "ecmwf_open_data:mx2t6_high:2026-05-30T00Z:majority"
    quarantined = 30
    mixed_members = json.dumps([20.0 + i * 0.01 for i in range(51 - quarantined)] + [None] * quarantined)

    _insert_snapshot(
        conn,
        snapshot_id=31,
        city="London",
        target_date="2026-05-31",
        source_run_id=source_run_id,
        members_json=mixed_members,
        contributes=0,
        attribution_status="AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY",
        boundary_ambiguous=1,
        ambiguous_member_count=quarantined,
        local_day_start_utc="2026-05-30T23:00:00+00:00",
    )
    conn.commit()

    from datetime import datetime, timezone

    cycle = datetime(2026, 5, 30, 0, tzinfo=timezone.utc)
    _write_source_authority_chain(
        conn,
        summary={"written": 1, "errors": 0},
        status="ok",
        source_run_id=source_run_id,
        source_cycle_time=cycle,
        source_release_time=cycle,
        release_calendar_key="2026-05-30T00Z",
        forecast_track="mx2t6_high",
        data_version=_DATA_VERSION,
        computed_at=cycle,
    )
    conn.commit()

    source_run = get_source_run(conn, source_run_id)
    assert source_run is not None
    assert source_run["status"] == "PARTIAL"
    assert int(source_run["observed_members"]) == 21

    coverage_row = conn.execute(
        """
        SELECT expected_members, observed_members, completeness_status, readiness_status
        FROM source_run_coverage
        WHERE source_run_id = ? AND city = ?
        """,
        (source_run_id, "London"),
    ).fetchone()
    assert coverage_row is not None
    assert int(coverage_row["expected_members"]) == 51, "majority-ambiguous keeps the full expectation"
    assert int(coverage_row["observed_members"]) == 21
    assert coverage_row["completeness_status"] != "COMPLETE"
    assert coverage_row["readiness_status"] == "BLOCKED"
