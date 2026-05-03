# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — antibody for Invariant A
#   (Open Data ENS rows land in ensemble_snapshots_v2 with the canonical
#   ecmwf_opendata_*_v1 data_versions; never in legacy ensemble_snapshots).
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
from src.state.db import init_schema
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
) -> dict:
    local_day_start_iso = local_day_start_iso or f"{target_date}T00:00:00+00:00"
    local_day_end_iso = local_day_end_iso or f"{target_date}T23:59:59+00:00"
    return {
        "generated_at": "2026-05-01T08:00:00+00:00",
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
        "missing_members": [],
        "training_allowed": True,
        "members": [
            {"member": i, "value_native_unit": 18.0 + 0.1 * i} for i in range(51)
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

    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)

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
        conn=conn,
        now_utc=now,
    )

    assert result["status"] == "ok"
    assert result["forecast_track"] == "mx2t6_high_full_horizon"
    assert result["coverage_written"] == 1
    assert result["producer_readiness_written"] == 1
    source_run = get_source_run(conn, result["source_run_id"])
    assert source_run is not None
    assert source_run["status"] == "SUCCESS"
    assert source_run["track"] == "mx2t6_high_full_horizon"
    coverage = conn.execute("SELECT * FROM source_run_coverage").fetchone()
    assert coverage["readiness_status"] == "LIVE_ELIGIBLE"
    assert coverage["target_window_start_utc"] == "2026-05-01T23:00:00+00:00"
    producer = conn.execute(
        "SELECT * FROM readiness_state WHERE strategy_key = 'producer_readiness'"
    ).fetchone()
    assert producer is not None
    assert producer["status"] == "LIVE_ELIGIBLE"

    write_readiness_state(
        conn,
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
        physical_quantity="mx2t6_local_calendar_day_max",
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

    reader_result = read_executable_forecast(
        conn,
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
