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
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema


def test_canonical_allowlist_includes_opendata():
    assert ECMWF_OPENDATA_HIGH_DATA_VERSION in CANONICAL_ENSEMBLE_DATA_VERSIONS
    assert ECMWF_OPENDATA_LOW_DATA_VERSION in CANONICAL_ENSEMBLE_DATA_VERSIONS
    # Both TIGGE archive data_versions remain valid (back-compat).
    assert "tigge_mx2t6_local_calendar_day_max_v1" in CANONICAL_ENSEMBLE_DATA_VERSIONS
    assert "tigge_mn2t6_local_calendar_day_min_v1" in CANONICAL_ENSEMBLE_DATA_VERSIONS


def _make_opendata_high_payload(target_date: str, issue_iso: str) -> dict:
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
            "start": f"{target_date}T00:00:00+00:00",
            "end": f"{target_date}T23:59:59+00:00",
        },
        "local_day_start_utc": f"{target_date}T00:00:00+00:00",
        "local_day_end_utc": f"{target_date}T23:59:59+00:00",
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
