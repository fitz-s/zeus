# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect raw-artifact to replacement posterior materialization preflight.
# Reuse: Run before claiming downloaded replacement weather data can feed live shadow/veto.
# Authority basis: Operator requires full E2E validation from download to pre-order path.
"""Replacement forecast materialization preflight tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

# ecmwf_aifs_ens is a deleted, BLOCKED-forever source (banned-source deletion order,
# docs/evidence/capital_efficiency_2026_07_19/banned_source_deletion_audit.md); this fixture
# label is retained verbatim (not imported from the now-deleted module) purely to exercise
# generic multi-source manifest discovery below, not AIFS-specific behavior.
AIFS_HIGH_DATA_VERSION = "ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max"
from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, read_manifest, write_manifest
from src.data.replacement_forecast_materialization_preflight import build_replacement_forecast_materialization_preflight
from scripts.stage_replacement_forecast_raw_manifests import stage_replacement_forecast_raw_manifests


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_manifest(
    raw_dir: Path,
    *,
    name: str,
    source_id: str,
    product_id: str,
    data_version: str,
    metadata: dict[str, object],
) -> Path:
    artifact = _write_json(raw_dir / f"{name}.json", {"artifact": name})
    manifest = RawForecastArtifactManifest.from_file(
        artifact,
        source_id=source_id,
        product_id=product_id,
        data_version=data_version,
        source_cycle_time="2026-06-06T00:00:00+00:00",
        source_available_at="2026-06-06T02:30:00+00:00",
        captured_at="2026-06-06T03:00:00+00:00",
        request_url=f"https://example.invalid/{name}",
        request_params={"name": name},
        product_metadata={"source_run_id": f"{name}-run", **metadata},
    )
    manifest_path = raw_dir / f"{name}.manifest.json"
    write_manifest(manifest, manifest_path)
    return manifest_path


def _init_forecast_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE market_events (
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                token_id TEXT,
                range_label TEXT,
                range_low REAL,
                range_high REAL
            );
            CREATE TABLE source_run (
                source_run_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                track TEXT NOT NULL,
                source_cycle_time TEXT,
                source_available_at TEXT
            );
            CREATE TABLE source_run_coverage (
                coverage_id TEXT PRIMARY KEY,
                source_run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                city_id TEXT NOT NULL,
                city TEXT NOT NULL,
                city_timezone TEXT NOT NULL,
                target_local_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                data_version TEXT NOT NULL,
                completeness_status TEXT NOT NULL,
                readiness_status TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        for label, low, high in (("69°F or below", None, 69.0), ("70-71°F", 70.0, 71.0), ("72°F or above", 72.0, None)):
            conn.execute(
                """
                INSERT INTO market_events
                  (market_slug, city, target_date, temperature_metric, token_id, range_label, range_low, range_high)
                VALUES ('slug', 'NYC', '2026-06-07', 'high', ?, ?, ?, ?)
                """,
                (label, label, low, high),
            )
        conn.execute(
            "INSERT INTO source_run VALUES ('baseline-run', 'ecmwf_open_data', 'mx2t3_high', '2026-06-06T00:00:00+00:00', '2026-06-06T02:00:00+00:00')"
        )
        conn.execute(
            """
            INSERT INTO source_run_coverage
              (coverage_id, source_run_id, source_id, city_id, city, city_timezone, target_local_date,
               temperature_metric, data_version, completeness_status, readiness_status, computed_at)
            VALUES
              ('coverage-1', 'baseline-run', 'ecmwf_open_data', 'NYC', 'NYC', 'America/New_York',
               '2026-06-07', 'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
               'COMPLETE', 'LIVE_ELIGIBLE', '2026-06-06T02:05:00+00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()


def _write_raw_inputs(raw_dir: Path) -> None:
    _write_json(raw_dir / "aifs_samples.json", {"samples": []})
    _write_json(raw_dir / "openmeteo.json", {"hourly": {"time": [], "temperature_2m": []}})
    _write_json(
        raw_dir / "precision_metadata.json",
        {
            "city": "NYC",
            "station_id": "KNYC",
            "city_lat": 40.7128,
            "city_lon": -74.006,
            "station_lat": 40.7789,
            "station_lon": -73.9692,
            "requested_lat": 40.7789,
            "requested_lon": -73.9692,
            "requested_coordinate_precision_decimals": 4,
            "nearest_grid_lat": 40.8,
            "nearest_grid_lon": -74.0,
            "nearest_grid_distance_km": 3.0,
            "native_grid": "openmeteo_ecmwf_ifs_9km",
            "delivery_grid_resolution": "0p1",
            "interpolation_method": "nearest_gridpoint",
            "endpoint_mode": "hourly_zeus_aggregated",
            "local_day_start_utc": "2026-06-06T04:00:00+00:00",
            "local_day_end_utc": "2026-06-07T04:00:00+00:00",
            "timezone_name": "America/New_York",
            "target_local_date": "2026-06-07",
            "temperature_unit": "C",
            "anchor_sigma_c": 3.0,
            "grid_elevation_m": 10.0,
            "station_elevation_m": 12.0,
            "land_sea_mask": "land",
            "city_class": "flat_inland",
            "station_mapping_policy": "settlement_station",
        },
    )
    _write_manifest(
        raw_dir,
        name="aifs",
        source_id="ecmwf_aifs_ens",
        product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
        data_version=AIFS_HIGH_DATA_VERSION,
        metadata={
            "aifs_samples_json": "aifs_samples.json",
            "city": "NYC",
            "target_date": "2026-06-07",
        },
    )
    _write_manifest(
        raw_dir,
        name="openmeteo",
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        metadata={
            "openmeteo_payload_json": "openmeteo.json",
            "precision_metadata_json": "precision_metadata.json",
            "city": "NYC",
            "target_date": "2026-06-07",
        },
    )


def test_materialization_preflight_blocks_when_manifests_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    _init_forecast_db(db_path)

    report = build_replacement_forecast_materialization_preflight(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        scratch_seed_dir=tmp_path / "scratch",
        computed_at="2026-06-06T04:00:00+00:00",
    )

    assert report.status == "MATERIALIZATION_PREFLIGHT_BLOCKED"
    assert "REPLACEMENT_MATERIALIZATION_PREFLIGHT_RAW_MANIFESTS_MISSING" in report.reason_codes
    assert report.raw_candidate_counts["manifest_json"] == 0


def test_materialization_preflight_discovers_seed_from_manifests(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "scratch"
    _init_forecast_db(db_path)
    _write_raw_inputs(raw_dir)

    report = build_replacement_forecast_materialization_preflight(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        scratch_seed_dir=seed_dir,
        computed_at="2026-06-06T04:00:00+00:00",
    )

    assert report.status == "MATERIALIZATION_PREFLIGHT_READY", report.as_dict()
    assert report.manifest_count == 2
    assert report.inventory_status == "PASS"
    assert report.seed_discovery_status == "DISCOVERED"
    assert report.discovered_seed_count == 1
    assert len(report.written_seed_files) == 1
    assert Path(report.written_seed_files[0]).exists()


def test_materialization_preflight_cli_writes_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    receipt = tmp_path / "receipt.json"
    _init_forecast_db(db_path)
    _write_raw_inputs(raw_dir)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/audit_replacement_forecast_materialization_preflight.py",
            "--forecast-db",
            str(db_path),
            "--raw-manifest-dir",
            str(raw_dir),
            "--scratch-seed-dir",
            str(tmp_path / "scratch"),
            "--computed-at",
            "2026-06-06T04:00:00+00:00",
            "--receipt-json",
            str(receipt),
            "--stdout",
        ],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "MATERIALIZATION_PREFLIGHT_READY"
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "MATERIALIZATION_PREFLIGHT_READY"


def test_stage_raw_manifests_writes_replay_target_identity(tmp_path: Path) -> None:
    source_raw_dir = tmp_path / "source_raw"
    live_raw_dir = tmp_path / "live_raw"
    _write_json(
        source_raw_dir / "openmeteo_jun5_jun6" / "Shanghai_20260605T00Z.json",
        {"hourly": {"time": ["2026-06-05T00:00"], "temperature_2m": [28.0]}},
    )

    receipt = stage_replacement_forecast_raw_manifests(
        source_raw_dir=source_raw_dir,
        live_raw_manifest_dir=live_raw_dir,
        captured_at="2026-06-07T04:30:30+00:00",
    )

    assert receipt["status"] == "RAW_MANIFESTS_STAGED"
    manifests = [read_manifest(Path(path)) for path in receipt["written_manifests"]]
    by_source = {manifest.source_id: manifest.product_metadata for manifest in manifests}
    openmeteo_metadata = by_source["openmeteo_ecmwf_ifs_9km"]
    assert openmeteo_metadata["city"] == "Shanghai"
    assert openmeteo_metadata["cities"] == ["Shanghai"]
    assert openmeteo_metadata["target_date"] == "2026-06-05"
    assert openmeteo_metadata["target_dates"] == ["2026-06-05"]
