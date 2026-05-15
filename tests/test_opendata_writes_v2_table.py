# Created: 2026-05-01
# Last reused/audited: 2026-05-15
# Authority basis: Operator directive 2026-05-01 — antibody for Invariant A;
#   docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md
#   Phase 5 forecast authority chain ownership.
"""Antibody for Invariant A — Open Data writes target ensemble_snapshots_v2.

The ingest cycle skips download/extract via test seams and runs the in-process
ingester against a temporary opendata-shaped JSON file. We then assert:

  1. ``ensemble_snapshots_v2`` got the row (not ``ensemble_snapshots``).
  2. The row's ``data_version`` is one of the canonical opendata constants.
  3. ``CANONICAL_ENSEMBLE_DATA_VERSIONS`` contains both opendata constants.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.contracts.ensemble_snapshot_provenance import (
    CANONICAL_ENSEMBLE_DATA_VERSIONS,
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.data.executable_forecast_reader import read_executable_forecast
from src.state.readiness_repo import write_readiness_state
from src.state.db import init_schema, init_schema_forecasts
from src.state.source_run_repo import get_source_run
from src.state.schema.v2_schema import apply_v2_schema


def test_canonical_allowlist_includes_opendata():
    assert ECMWF_OPENDATA_HIGH_DATA_VERSION in CANONICAL_ENSEMBLE_DATA_VERSIONS
    assert ECMWF_OPENDATA_LOW_DATA_VERSION in CANONICAL_ENSEMBLE_DATA_VERSIONS
    # Both TIGGE archive data_versions remain valid (back-compat).
    assert "tigge_mx2t6_local_calendar_day_max_v1" in CANONICAL_ENSEMBLE_DATA_VERSIONS
    assert "tigge_mn2t6_local_calendar_day_min_v1" in CANONICAL_ENSEMBLE_DATA_VERSIONS


def _make_opendata_high_payload(
    target_date: str,
    issue_iso: str,
    *,
    local_day_start_iso: str | None = None,
    local_day_end_iso: str | None = None,
    missing_member_ids: tuple[int, ...] = (),
) -> dict:
    local_day_start_iso = local_day_start_iso or f"{target_date}T00:00:00+00:00"
    local_day_end_iso = local_day_end_iso or f"{target_date}T23:59:59+00:00"
    missing_members = sorted(set(missing_member_ids))
    return {
        "generated_at": "2026-05-01T08:00:00+00:00",
        "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
        # 2026-05-07 mx2t3 cutover: payload now reports the 3h native physical
        # identity. Snapshot ingest contract maps data_version → MetricIdentity
        # whose physical_quantity matches; mismatched payloads are rejected.
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
        "manifest_sha256": "0" * 64,
        "manifest_hash": "0" * 64,
        "issue_time_utc": issue_iso,
        "target_date_local": target_date,
        "lead_day": 1,
        "lead_day_anchor": "issue_utc.date()",
        "timezone": "Europe/London",
        "local_day_window": {
            "start": local_day_start_iso,
            "end": local_day_end_iso,
        },
        "local_day_start_utc": local_day_start_iso,
        "local_day_end_utc": local_day_end_iso,
        "step_horizon_hours": 240.0,
        "step_horizon_deficit_hours": 0.0,
        "causality": {"status": "OK"},
        "boundary_ambiguous": False,
        "nearest_grid_lat": 51.5,
        "nearest_grid_lon": -0.5,
        "nearest_grid_distance_km": 5.0,
        "selected_step_ranges": ["18-24", "24-30"],
        "member_count": 51,
        "missing_members": missing_members,
        "training_allowed": not missing_members,
        "members": [
            {
                "member": i,
                "value_native_unit": None if i in missing_members else 18.0 + 0.1 * i,
            }
            for i in range(51)
        ],
    }


def test_opendata_high_payload_lands_in_v2(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)

    # Build the JSON file at the location the ingester would scan.
    fifty_one_root = tmp_path / "51 source data"
    extract_subdir = "open_ens_mx2t6_localday_max"
    target = "2026-05-02"
    issue = "2026-05-01T00:00:00+00:00"
    payload = _make_opendata_high_payload(target, issue)
    json_dir = fifty_one_root / "raw" / extract_subdir / "london" / "20260501"
    json_dir.mkdir(parents=True)
    json_path = json_dir / f"{extract_subdir}_target_{target}_lead_1.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    # Reach into ingest_grib_to_snapshots.ingest_track via the rebound subdir
    # path (mirroring src/data/ecmwf_open_data.py's in-process invocation).
    import sys
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import ingest_grib_to_snapshots as _ingmod  # type: ignore

    original = _ingmod._TRACK_CONFIGS["mx2t6_high"]["json_subdir"]
    _ingmod._TRACK_CONFIGS["mx2t6_high"]["json_subdir"] = extract_subdir
    try:
        summary = _ingmod.ingest_track(
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
        _ingmod._TRACK_CONFIGS["mx2t6_high"]["json_subdir"] = original

    assert summary["written"] == 1, summary
    rows = conn.execute(
        "SELECT data_version, temperature_metric, city, target_date "
        "FROM ensemble_snapshots_v2"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["data_version"] == ECMWF_OPENDATA_HIGH_DATA_VERSION
    assert row["temperature_metric"] == "high"
    assert row["city"] == "London"
    assert row["target_date"] == target
    # Legacy v1 table receives nothing.
    legacy = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()
    assert legacy[0] == 0


def test_collect_open_ens_cycle_writes_authority_chain_readable_by_live_reader(tmp_path: Path, monkeypatch):
    from src.data import ecmwf_open_data

    forecasts_db_path = tmp_path / "forecasts.db"
    forecasts_conn = sqlite3.connect(str(forecasts_db_path))
    forecasts_conn.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts_conn)

    fifty_one_root = tmp_path / "51 source data"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", fifty_one_root)
    extract_subdir = "open_ens_mx2t6_localday_max"
    target = "2026-05-02"
    issue = "2026-05-01T00:00:00+00:00"
    payload = _make_opendata_high_payload(
        target,
        issue,
        local_day_start_iso="2026-05-01T23:00:00+00:00",
        local_day_end_iso="2026-05-02T23:00:00+00:00",
    )
    json_dir = fifty_one_root / "raw" / extract_subdir / "london" / "20260501"
    json_dir.mkdir(parents=True)
    json_path = json_dir / f"{extract_subdir}_target_{target}_lead_1.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    now = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 1),
        run_hour=0,
        skip_download=True,
        skip_extract=True,
        conn=forecasts_conn,
        now_utc=now,
    )

    assert result["status"] == "ok"
    assert result["forecast_track"] == "mx2t6_high_full_horizon"
    assert result["coverage_written"] == 1
    assert result["producer_readiness_written"] == 1
    source_run = get_source_run(forecasts_conn, result["source_run_id"])
    assert source_run is not None
    assert source_run["status"] == "SUCCESS"
    assert source_run["track"] == "mx2t6_high_full_horizon"
    coverage = forecasts_conn.execute("SELECT * FROM source_run_coverage").fetchone()
    assert coverage["readiness_status"] == "LIVE_ELIGIBLE"
    assert coverage["target_window_start_utc"] == "2026-05-01T23:00:00+00:00"
    producer = forecasts_conn.execute(
        "SELECT * FROM readiness_state WHERE strategy_key = 'producer_readiness'"
    ).fetchone()
    assert producer is not None
    assert producer["status"] == "LIVE_ELIGIBLE"

    trade_db_path = tmp_path / "trade.db"
    trade_conn = sqlite3.connect(str(trade_db_path))
    trade_conn.row_factory = sqlite3.Row
    init_schema(trade_conn)
    apply_v2_schema(trade_conn)
    write_readiness_state(
        trade_conn,
        readiness_id="entry-ready-london-2026-05-02",
        scope_type="city_metric",
        status="LIVE_ELIGIBLE",
        computed_at=now + timedelta(minutes=1),
        expires_at=now + timedelta(hours=2),
        city_id="LONDON",
        city="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 2),
        temperature_metric="high",
        physical_quantity="mx2t3_local_calendar_day_max",
        observation_field="high_temp",
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        source_id="ecmwf_open_data",
        track="mx2t6_high_full_horizon",
        source_run_id=result["source_run_id"],
        strategy_key="entry_forecast",
        market_family="london-2026-05-02-high",
        condition_id="condition-london-high",
        reason_codes_json=["ENTRY_READY"],
        dependency_json={"producer_readiness_id": producer["readiness_id"]},
    )
    forecasts_conn.commit()
    forecasts_conn.close()
    trade_conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db_path),))

    reader_result = read_executable_forecast(
        trade_conn,
        city_id="LONDON",
        city_name="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 2),
        temperature_metric="high",
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        track="mx2t6_high_full_horizon",
        strategy_key="entry_forecast",
        market_family="london-2026-05-02-high",
        condition_id="condition-london-high",
        decision_time=now + timedelta(minutes=2),
    )

    assert reader_result.ok, reader_result.reason_code
    assert reader_result.reason_code == "EXECUTABLE_FORECAST_READY"


def test_collect_open_ens_cycle_blocks_live_when_member_value_missing(tmp_path: Path, monkeypatch):
    from src.data import ecmwf_open_data

    forecasts_conn = sqlite3.connect(str(tmp_path / "forecasts.db"))
    forecasts_conn.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts_conn)

    fifty_one_root = tmp_path / "51 source data"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", fifty_one_root)
    extract_subdir = "open_ens_mx2t6_localday_max"
    target = "2026-05-02"
    payload = _make_opendata_high_payload(
        target,
        "2026-05-01T00:00:00+00:00",
        local_day_start_iso="2026-05-01T23:00:00+00:00",
        local_day_end_iso="2026-05-02T23:00:00+00:00",
        missing_member_ids=(0,),
    )
    json_dir = fifty_one_root / "raw" / extract_subdir / "london" / "20260501"
    json_dir.mkdir(parents=True)
    (json_dir / f"{extract_subdir}_target_{target}_lead_1.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 1),
        run_hour=0,
        skip_download=True,
        skip_extract=True,
        conn=forecasts_conn,
        now_utc=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    source_run = get_source_run(forecasts_conn, result["source_run_id"])
    assert source_run is not None
    assert source_run["status"] == "PARTIAL"
    assert source_run["completeness_status"] == "PARTIAL"
    assert source_run["reason_code"] == "MISSING_EXPECTED_MEMBERS"
    assert source_run["observed_members"] == 50
    coverage = forecasts_conn.execute("SELECT * FROM source_run_coverage").fetchone()
    assert coverage["observed_members"] == 50
    assert coverage["readiness_status"] == "BLOCKED"
    assert coverage["reason_code"] == "MISSING_EXPECTED_MEMBERS"
    producer = forecasts_conn.execute(
        "SELECT * FROM readiness_state WHERE strategy_key = 'producer_readiness'"
    ).fetchone()
    assert producer["status"] == "BLOCKED"
    assert json.loads(producer["reason_codes_json"]) == ["MISSING_EXPECTED_MEMBERS"]


def test_collect_open_ens_cycle_partial_global_run_allows_covered_target(tmp_path: Path, monkeypatch):
    from src.data import ecmwf_open_data

    forecasts_db_path = tmp_path / "forecasts.db"
    forecasts_conn = sqlite3.connect(str(forecasts_db_path))
    forecasts_conn.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts_conn)

    fifty_one_root = tmp_path / "51 source data"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", fifty_one_root)
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", [3, 6, 9, 12, 15, 18, 21, 24, 150])
    extract_subdir = "open_ens_mx2t6_localday_max"
    payload = _make_opendata_high_payload(
        "2026-05-02",
        "2026-05-01T00:00:00+00:00",
        local_day_start_iso="2026-05-01T23:00:00+00:00",
        local_day_end_iso="2026-05-02T23:00:00+00:00",
    )
    json_dir = fifty_one_root / "raw" / extract_subdir / "london" / "20260501"
    json_dir.mkdir(parents=True)
    (json_dir / f"{extract_subdir}_target_2026-05-02_lead_1.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    def fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del cycle_date, cycle_hour, mirrors
        if step == 150:
            return ("NOT_RELEASED", "not released")
        ecmwf_open_data._step_cache_path(output_dir, step=step, param=param).write_bytes(b"x")
        return ("OK", None)

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 1),
        run_hour=0,
        skip_extract=True,
        conn=forecasts_conn,
        _fetch_impl=fetch_impl,
        now_utc=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    source_run = get_source_run(forecasts_conn, result["source_run_id"])
    assert source_run is not None
    assert source_run["status"] == "PARTIAL"
    assert source_run["completeness_status"] == "PARTIAL"
    coverage = forecasts_conn.execute("SELECT * FROM source_run_coverage").fetchone()
    assert coverage["readiness_status"] == "LIVE_ELIGIBLE"
    producer = forecasts_conn.execute(
        "SELECT * FROM readiness_state WHERE strategy_key = 'producer_readiness'"
    ).fetchone()
    assert producer["status"] == "LIVE_ELIGIBLE"


def test_collect_open_ens_cycle_scopes_ingest_to_selected_cycle(tmp_path: Path, monkeypatch):
    from src.data import ecmwf_open_data

    forecasts_db_path = tmp_path / "forecasts.db"
    forecasts_conn = sqlite3.connect(str(forecasts_db_path))
    forecasts_conn.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts_conn)

    fifty_one_root = tmp_path / "51 source data"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", fifty_one_root)
    extract_subdir = "open_ens_mx2t6_localday_max"

    stale_dir = fifty_one_root / "raw" / extract_subdir / "london" / "20260430"
    stale_dir.mkdir(parents=True)
    stale_payload = _make_opendata_high_payload(
        "2026-05-01",
        "2026-04-30T00:00:00+00:00",
        local_day_start_iso="2026-04-30T23:00:00+00:00",
        local_day_end_iso="2026-05-01T23:00:00+00:00",
    )
    (stale_dir / f"{extract_subdir}_target_2026-05-01_lead_1.json").write_text(
        json.dumps(stale_payload),
        encoding="utf-8",
    )

    selected_dir = fifty_one_root / "raw" / extract_subdir / "london" / "20260501"
    selected_dir.mkdir(parents=True)
    selected_payload = _make_opendata_high_payload(
        "2026-05-02",
        "2026-05-01T00:00:00+00:00",
        local_day_start_iso="2026-05-01T23:00:00+00:00",
        local_day_end_iso="2026-05-02T23:00:00+00:00",
    )
    (selected_dir / f"{extract_subdir}_target_2026-05-02_lead_1.json").write_text(
        json.dumps(selected_payload),
        encoding="utf-8",
    )

    other_cycle_dir = fifty_one_root / "raw" / extract_subdir / "london" / "20260501_cycle12z"
    other_cycle_dir.mkdir(parents=True)
    other_payload = _make_opendata_high_payload(
        "2026-05-03",
        "2026-05-01T12:00:00+00:00",
        local_day_start_iso="2026-05-02T23:00:00+00:00",
        local_day_end_iso="2026-05-03T23:00:00+00:00",
    )
    (other_cycle_dir / f"{extract_subdir}_target_2026-05-03_lead_1.json").write_text(
        json.dumps(other_payload),
        encoding="utf-8",
    )

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 1),
        run_hour=0,
        skip_download=True,
        skip_extract=True,
        conn=forecasts_conn,
        now_utc=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    assert result["cycle_extract_dir"] == "20260501"
    assert result["cycle_json_files"] == 1
    assert result["snapshots_inserted"] == 1
    rows = forecasts_conn.execute(
        """
        SELECT target_date, source_run_id, source_cycle_time
        FROM ensemble_snapshots_v2
        ORDER BY target_date
        """
    ).fetchall()
    assert [(row["target_date"], row["source_run_id"]) for row in rows] == [
        ("2026-05-02", result["source_run_id"])
    ]
    assert rows[0]["source_cycle_time"] == "2026-05-01T00:00:00+00:00"


def test_collect_open_ens_cycle_clears_prior_same_source_run_rows(tmp_path: Path, monkeypatch):
    from src.data import ecmwf_open_data

    forecasts_conn = sqlite3.connect(str(tmp_path / "forecasts.db"))
    forecasts_conn.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts_conn)

    fifty_one_root = tmp_path / "51 source data"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", fifty_one_root)
    extract_subdir = "open_ens_mx2t6_localday_max"
    source_run_id = "ecmwf_open_data:mx2t6_high:2026-05-01T00Z"
    forecasts_conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
            model_version, data_version, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            city_timezone, settlement_unit, manifest_hash, provenance_json, members_unit,
            local_day_start_utc, step_horizon_hours, unit
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "London",
            "2026-05-09",
            "high",
            "mx2t3_local_calendar_day_max",
            "high_temp",
            "2026-05-01T00:00:00+00:00",
            "2026-05-09T00:00:00+00:00",
            "2026-05-01T09:00:00+00:00",
            "2026-05-01T09:00:00+00:00",
            192.0,
            json.dumps([18.0] * 51),
            "ecmwf_open_data",
            ECMWF_OPENDATA_HIGH_DATA_VERSION,
            "ecmwf_open_data",
            "ensemble_snapshots_v2_db_reader",
            source_run_id,
            "ecmwf_open_data:mx2t6_high:full",
            "2026-05-01T00:00:00+00:00",
            "2026-05-01T00:00:00+00:00",
            "2026-05-01T00:00:00+00:00",
            "Europe/London",
            "C",
            "stale",
            "{}",
            "degC",
            "2026-05-08T23:00:00+00:00",
            240.0,
            "C",
        ),
    )
    forecasts_conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
            model_version, data_version, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            city_timezone, settlement_unit, manifest_hash, provenance_json, members_unit,
            local_day_start_utc, step_horizon_hours, unit
        )
        SELECT
            city, '2026-05-08', temperature_metric, 'mx2t6_local_calendar_day_max', observation_field,
            issue_time, '2026-05-08T00:00:00+00:00', available_at, fetch_time, lead_hours, members_json,
            model_version, 'ecmwf_opendata_mx2t6_local_calendar_day_max_v1', source_id, source_transport,
            source_run_id, release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            city_timezone, settlement_unit, manifest_hash, provenance_json, members_unit,
            '2026-05-07T23:00:00+00:00', step_horizon_hours, unit
        FROM ensemble_snapshots_v2
        WHERE source_run_id = ? AND target_date = '2026-05-09'
        """,
        (source_run_id,),
    )
    write_readiness_state(
        forecasts_conn,
        readiness_id="producer_readiness:stale",
        scope_type="city_metric",
        status="LIVE_ELIGIBLE",
        computed_at=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 5, 2, 9, 0, tzinfo=timezone.utc),
        city_id="LONDON",
        city="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 9),
        temperature_metric="high",
        physical_quantity="mx2t3_local_calendar_day_max",
        observation_field="high_temp",
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        source_id="ecmwf_open_data",
        track="mx2t6_high_full_horizon",
        source_run_id=source_run_id,
        strategy_key="producer_readiness",
        reason_codes_json=["STALE"],
    )

    payload = _make_opendata_high_payload(
        "2026-05-02",
        "2026-05-01T00:00:00+00:00",
        local_day_start_iso="2026-05-01T23:00:00+00:00",
        local_day_end_iso="2026-05-02T23:00:00+00:00",
    )
    json_dir = fifty_one_root / "raw" / extract_subdir / "london" / "20260501"
    json_dir.mkdir(parents=True)
    (json_dir / f"{extract_subdir}_target_2026-05-02_lead_1.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 1),
        run_hour=0,
        skip_download=True,
        skip_extract=True,
        conn=forecasts_conn,
        now_utc=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )

    assert result["cleared_authority"]["snapshots_deleted"] == 2
    assert result["cleared_authority"]["producer_readiness_deleted"] == 1
    rows = forecasts_conn.execute(
        "SELECT target_date FROM ensemble_snapshots_v2 WHERE source_run_id = ? ORDER BY target_date",
        (source_run_id,),
    ).fetchall()
    assert [row["target_date"] for row in rows] == ["2026-05-02"]
    readiness_targets = forecasts_conn.execute(
        """
        SELECT target_local_date
        FROM readiness_state
        WHERE strategy_key = 'producer_readiness' AND source_run_id = ?
        ORDER BY target_local_date
        """,
        (source_run_id,),
    ).fetchall()
    assert [row["target_local_date"] for row in readiness_targets] == ["2026-05-02"]


def test_collect_open_ens_cycle_overwrites_existing_snapshot_in_place(tmp_path: Path, monkeypatch):
    from src.data import ecmwf_open_data

    forecasts_conn = sqlite3.connect(str(tmp_path / "forecasts.db"))
    forecasts_conn.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts_conn)

    fifty_one_root = tmp_path / "51 source data"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", fifty_one_root)
    extract_subdir = "open_ens_mx2t6_localday_max"
    source_run_id = "ecmwf_open_data:mx2t6_high:2026-05-01T00Z"
    issue_iso = "2026-05-01T00:00:00+00:00"
    forecasts_conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
            model_version, data_version, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            city_timezone, settlement_unit, manifest_hash, provenance_json, members_unit,
            local_day_start_utc, step_horizon_hours, unit
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "London",
            "2026-05-02",
            "high",
            "mx2t3_local_calendar_day_max",
            "high_temp",
            issue_iso,
            "2026-05-02T00:00:00+00:00",
            "2026-05-01T09:00:00+00:00",
            "2026-05-01T09:00:00+00:00",
            48.0,
            json.dumps([None] * 51),
            "ecmwf_open_data",
            ECMWF_OPENDATA_HIGH_DATA_VERSION,
            "ecmwf_open_data",
            "ensemble_snapshots_v2_db_reader",
            source_run_id,
            "ecmwf_open_data:mx2t6_high:full",
            issue_iso,
            issue_iso,
            issue_iso,
            "Europe/London",
            "C",
            "stale",
            "{}",
            "degC",
            "2026-05-01T23:00:00+00:00",
            240.0,
            "C",
        ),
    )
    before_id = forecasts_conn.execute(
        "SELECT snapshot_id FROM ensemble_snapshots_v2 WHERE source_run_id = ?",
        (source_run_id,),
    ).fetchone()["snapshot_id"]

    payload = _make_opendata_high_payload(
        "2026-05-02",
        issue_iso,
        local_day_start_iso="2026-05-01T23:00:00+00:00",
        local_day_end_iso="2026-05-02T23:00:00+00:00",
    )
    json_dir = fifty_one_root / "raw" / extract_subdir / "london" / "20260501"
    json_dir.mkdir(parents=True)
    (json_dir / f"{extract_subdir}_target_2026-05-02_lead_1.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 1),
        run_hour=0,
        skip_download=True,
        skip_extract=True,
        conn=forecasts_conn,
        now_utc=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )

    assert result["cleared_authority"]["snapshots_deleted"] == 0
    row = forecasts_conn.execute(
        "SELECT snapshot_id, members_json FROM ensemble_snapshots_v2 WHERE source_run_id = ?",
        (source_run_id,),
    ).fetchone()
    assert row["snapshot_id"] == before_id
    assert json.loads(row["members_json"])[0] == pytest.approx(18.0)


def test_collect_open_ens_cycle_default_extract_timeout_is_live_sized(tmp_path: Path, monkeypatch):
    from src.data import ecmwf_open_data

    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    fifty_one_root = tmp_path / "51 source data"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", fifty_one_root)

    calls: list[dict[str, object]] = []

    def runner(args, *, label: str, timeout: int):
        calls.append({"label": label, "timeout": timeout})
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 1),
        run_hour=0,
        skip_download=True,
        conn=conn,
        _runner=runner,
        now_utc=datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "empty_ingest"
    assert calls == [
        {"label": "extract_mx2t6_high", "timeout": 900},
    ]
