# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-07
# Purpose: Protect automatic replacement materialization seed generation from market/source context.
# Reuse: Run before changing replacement shadow queue input generation.
# Authority basis: Replacement materialization must be grounded in real market bins and source-run coverage, not hand-built seed JSON.
"""Replacement forecast materialization seed builder tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.data.ecmwf_aifs_sampled_2t_localday import HIGH_DATA_VERSION as AIFS_HIGH_DATA_VERSION
from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, write_manifest
from src.data.replacement_forecast_materialization_seed_builder import (
    build_replacement_forecast_materialization_seed,
    latest_baseline_coverage_for_replacement_seed,
    load_manifest_with_path,
    market_bins_for_replacement_seed,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _artifact(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text(f"{name}\n", encoding="utf-8")
    return path


def _manifest(
    tmp_path: Path,
    *,
    source_id: str,
    product_id: str,
    data_version: str,
    name: str,
    available_at: str = "2026-06-06T03:00:00+00:00",
    captured_at: str = "2026-06-06T03:05:00+00:00",
) -> Path:
    artifact_path = _artifact(tmp_path, f"{name}.json")
    manifest = RawForecastArtifactManifest.from_file(
        artifact_path,
        source_id=source_id,
        product_id=product_id,
        data_version=data_version,
        source_cycle_time="2026-06-06T00:00:00+00:00",
        source_available_at=available_at,
        captured_at=captured_at,
        request_url=f"https://example.invalid/{name}",
        request_params={"name": name},
        product_metadata={"source_run_id": f"{name}-source-run"},
    )
    manifest_path = tmp_path / f"{name}.manifest.json"
    write_manifest(manifest, manifest_path)
    return manifest_path


def _baseline_coverage() -> dict[str, object]:
    return {
        "source_run_id": "baseline-source-run",
        "source_id": "ecmwf_open_data",
        "city_id": "NYC",
        "city": "NYC",
        "city_timezone": "America/New_York",
        "target_local_date": "2026-06-07",
        "temperature_metric": "high",
        "data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "completeness_status": "COMPLETE",
        "readiness_status": "LIVE_ELIGIBLE",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "source_available_at": "2026-06-06T02:00:00+00:00",
        "computed_at": "2026-06-06T02:00:00+00:00",
    }


def _market_bins_f() -> list[dict[str, object]]:
    return [
        {"range_label": "69°F or below", "range_low": None, "range_high": 69.0, "token_id": "tok-cool"},
        {"range_label": "70-71°F", "range_low": 70.0, "range_high": 71.0, "token_id": "tok-mid"},
        {"range_label": "72°F or above", "range_low": 72.0, "range_high": None, "token_id": "tok-hot"},
    ]


def test_seed_builder_uses_real_market_bins_and_fahrenheit_step(tmp_path: Path) -> None:
    aifs_manifest = load_manifest_with_path(
        _manifest(
            tmp_path,
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version=AIFS_HIGH_DATA_VERSION,
            name="aifs",
        )
    )
    openmeteo_manifest = load_manifest_with_path(
        _manifest(
            tmp_path,
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version=OPENMETEO_HIGH_DATA_VERSION,
            name="openmeteo",
        )
    )

    result = build_replacement_forecast_materialization_seed(
        city="NYC",
        target_date="2026-06-07",
        temperature_metric="high",
        market_bins=_market_bins_f(),
        baseline_coverage=_baseline_coverage(),
        aifs_manifest=aifs_manifest,
        openmeteo_manifest=openmeteo_manifest,
        aifs_samples_json=tmp_path / "aifs_samples.json",
        openmeteo_payload_json=tmp_path / "openmeteo_payload.json",
        precision_metadata_json=tmp_path / "precision_metadata.json",
        computed_at="2026-06-06T04:00:00+00:00",
        base_dir=tmp_path,
    )

    assert result.ok is True
    seed = result.seed
    assert seed is not None
    assert seed["settlement_step_c"] == 5.0 / 9.0
    assert seed["baseline_source_run_id"] == "baseline-source-run"
    assert seed["aifs_source_run_id"] == "aifs-source-run"
    assert seed["openmeteo_source_run_id"] == "openmeteo-source-run"
    assert [row["bin_id"] for row in seed["bins"]] == ["69°F or below", "70-71°F", "72°F or above"]
    middle = seed["bins"][1]
    assert round(middle["lower_c"], 6) == round((70.0 - 32.0) * 5.0 / 9.0, 6)
    assert round(middle["upper_c"], 6) == round((71.0 - 32.0) * 5.0 / 9.0, 6)


def test_seed_builder_writes_sibling_raw_paths_relative_to_seed_dir(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw_manifests"
    seed_dir = tmp_path / "seeds"
    raw_dir.mkdir()
    seed_dir.mkdir()
    aifs_manifest = load_manifest_with_path(
        _manifest(
            raw_dir,
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version=AIFS_HIGH_DATA_VERSION,
            name="aifs",
        )
    )
    openmeteo_manifest = load_manifest_with_path(
        _manifest(
            raw_dir,
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version=OPENMETEO_HIGH_DATA_VERSION,
            name="openmeteo",
        )
    )

    result = build_replacement_forecast_materialization_seed(
        city="NYC",
        target_date="2026-06-07",
        temperature_metric="high",
        market_bins=_market_bins_f(),
        baseline_coverage=_baseline_coverage(),
        aifs_manifest=aifs_manifest,
        openmeteo_manifest=openmeteo_manifest,
        aifs_samples_json=raw_dir / "aifs_samples.json",
        openmeteo_payload_json=raw_dir / "openmeteo_payload.json",
        precision_metadata_json=raw_dir / "precision_metadata.json",
        computed_at="2026-06-06T04:00:00+00:00",
        base_dir=seed_dir,
    )

    assert result.ok is True
    seed = result.seed
    assert seed is not None
    assert seed["aifs_samples_json"] == "../raw_manifests/aifs_samples.json"
    assert seed["openmeteo_payload_json"] == "../raw_manifests/openmeteo_payload.json"
    assert seed["precision_metadata_json"] == "../raw_manifests/precision_metadata.json"


def test_seed_builder_blocks_future_dependency(tmp_path: Path) -> None:
    aifs_manifest = load_manifest_with_path(
        _manifest(
            tmp_path,
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version=AIFS_HIGH_DATA_VERSION,
            name="aifs",
            available_at="2026-06-06T05:00:00+00:00",
            captured_at="2026-06-06T05:05:00+00:00",
        )
    )
    openmeteo_manifest = load_manifest_with_path(
        _manifest(
            tmp_path,
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version=OPENMETEO_HIGH_DATA_VERSION,
            name="openmeteo",
        )
    )

    result = build_replacement_forecast_materialization_seed(
        city="NYC",
        target_date="2026-06-07",
        temperature_metric="high",
        market_bins=_market_bins_f(),
        baseline_coverage=_baseline_coverage(),
        aifs_manifest=aifs_manifest,
        openmeteo_manifest=openmeteo_manifest,
        aifs_samples_json=tmp_path / "aifs_samples.json",
        openmeteo_payload_json=tmp_path / "openmeteo_payload.json",
        precision_metadata_json=tmp_path / "precision_metadata.json",
        computed_at="2026-06-06T04:00:00+00:00",
        base_dir=tmp_path,
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_SEED_HAS_FUTURE_DEPENDENCY",)


def _init_context_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE market_events (
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                condition_id TEXT,
                token_id TEXT,
                range_label TEXT,
                range_low REAL,
                range_high REAL,
                outcome TEXT,
                created_at TEXT,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        for idx, row in enumerate(_market_bins_f()):
            conn.execute(
                """
                INSERT INTO market_events
                  (market_slug, city, target_date, temperature_metric, token_id, range_label, range_low, range_high)
                VALUES (?, 'NYC', '2026-06-07', 'high', ?, ?, ?, ?)
                """,
                (f"slug-{idx}", row["token_id"], row["range_label"], row["range_low"], row["range_high"]),
            )
        conn.execute(
            "INSERT INTO source_run VALUES ('baseline-source-run', 'ecmwf_open_data', 'mx2t3_high', '2026-06-06T00:00:00+00:00', '2026-06-06T02:00:00+00:00')"
        )
        conn.execute(
            """
            INSERT INTO source_run_coverage
              (coverage_id, source_run_id, source_id, city_id, city, city_timezone, target_local_date,
               temperature_metric, data_version, completeness_status, readiness_status, computed_at)
            VALUES
              ('coverage-1', 'baseline-source-run', 'ecmwf_open_data', 'NYC', 'NYC', 'America/New_York',
               '2026-06-07', 'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
               'COMPLETE', 'LIVE_ELIGIBLE', '2026-06-06T02:05:00+00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_seed_builder_db_adapters_and_cli(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    _init_context_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        coverage = latest_baseline_coverage_for_replacement_seed(conn, city="NYC", target_date="2026-06-07", temperature_metric="high")
        bins = market_bins_for_replacement_seed(conn, city="NYC", target_date="2026-06-07", temperature_metric="high")
    finally:
        conn.close()
    assert coverage is not None
    assert coverage["source_run_id"] == "baseline-source-run"
    assert len(bins) == 3

    aifs_manifest = _manifest(
        tmp_path,
        source_id="ecmwf_aifs_ens",
        product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
        data_version=AIFS_HIGH_DATA_VERSION,
        name="aifs",
    )
    openmeteo_manifest = _manifest(
        tmp_path,
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        name="openmeteo",
    )
    output_json = tmp_path / "seed.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_replacement_forecast_materialization_seed.py",
            "--forecast-db",
            str(db_path),
            "--city",
            "NYC",
            "--target-date",
            "2026-06-07",
            "--temperature-metric",
            "high",
            "--aifs-manifest-json",
            str(aifs_manifest),
            "--openmeteo-manifest-json",
            str(openmeteo_manifest),
            "--aifs-samples-json",
            str(tmp_path / "aifs_samples.json"),
            "--openmeteo-payload-json",
            str(tmp_path / "openmeteo_payload.json"),
            "--precision-metadata-json",
            str(tmp_path / "precision_metadata.json"),
            "--computed-at",
            "2026-06-06T04:00:00+00:00",
            "--output-json",
            str(output_json),
            "--stdout",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    seed = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["status"] == "READY"
    assert seed["baseline_source_run_id"] == "baseline-source-run"
    assert seed["aifs_manifest_json"] == "aifs.manifest.json"
