# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect automatic replacement seed discovery from DB context plus raw manifests.
# Reuse: Run before enabling daemon-side replacement shadow materialization discovery.
# Authority basis: Simple switch must not depend on hand-authored seeds once raw inputs exist.
"""Replacement forecast materialization seed discovery tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.data.ecmwf_aifs_sampled_2t_localday import HIGH_DATA_VERSION as AIFS_HIGH_DATA_VERSION
from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, write_manifest
from src.data.replacement_forecast_seed_discovery import discover_replacement_forecast_materialization_seeds


def _write_file(path: Path, payload: object) -> Path:
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
    artifact = _write_file(raw_dir / f"{name}.json", {"name": name})
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


def _init_db(path: Path) -> None:
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
    _write_file(raw_dir / "aifs_samples.json", {"samples": []})
    _write_file(raw_dir / "precision_metadata.json", {"city": "NYC"})
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


def test_seed_discovery_writes_seed_from_db_target_and_raw_manifests(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    _init_db(db_path)
    _write_raw_inputs(raw_dir)

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-06T04:00:00+00:00",
    )

    assert report.status == "DISCOVERED"
    assert report.discovered_count == 1
    seed_path = Path(report.written_seed_files[0])
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["city"] == "NYC"
    assert seed["baseline_source_run_id"] == "baseline-run"
    assert seed["aifs_source_run_id"] == "aifs-run"
    assert seed["openmeteo_source_run_id"] == "openmeteo-run"
    assert seed["aifs_samples_json"].endswith("raw/aifs_samples.json")
    assert seed["precision_metadata_json"].endswith("raw/precision_metadata.json")


def test_seed_discovery_reports_noop_when_required_manifests_are_absent(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    _init_db(db_path)

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=tmp_path / "raw",
        seed_dir=tmp_path / "seeds",
        computed_at="2026-06-06T04:00:00+00:00",
    )

    assert report.status == "NO_ELIGIBLE_TARGETS"
    assert report.reason_codes == ("REPLACEMENT_SEED_DISCOVERY_RAW_MANIFESTS_MISSING",)
    assert report.discovered_count == 0
