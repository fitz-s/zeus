# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-07
# Purpose: Protect automatic replacement seed discovery from DB context plus raw manifests.
# Reuse: Run before enabling daemon-side replacement shadow materialization discovery.
# Authority basis: Simple switch must not depend on hand-authored seeds once raw inputs exist.
"""Replacement forecast materialization seed discovery tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, write_manifest
from src.data.replacement_forecast_readiness import SOURCE_ID as REPLACEMENT_SOURCE_ID
from src.data.replacement_forecast_readiness import STRATEGY_KEY as REPLACEMENT_STRATEGY_KEY
from src.data.replacement_forecast_seed_discovery import (
    _manifest_allows_target_date,
    _latest_manifest,
    discover_replacement_forecast_materialization_seeds,
)


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
    source_cycle_time: str = "2026-06-06T00:00:00+00:00",
    source_available_at: str = "2026-06-06T02:30:00+00:00",
    captured_at: str = "2026-06-06T03:00:00+00:00",
) -> Path:
    artifact = _write_file(raw_dir / f"{name}.json", {"name": name})
    manifest = RawForecastArtifactManifest.from_file(
        artifact,
        source_id=source_id,
        product_id=product_id,
        data_version=data_version,
        source_cycle_time=source_cycle_time,
        source_available_at=source_available_at,
        captured_at=captured_at,
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
            CREATE TABLE forecast_posteriors (
                posterior_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL DEFAULT 'openmeteo_ecmwf_ifs9_bayes_fusion_v1',
                data_version TEXT NOT NULL DEFAULT 'openmeteo_ecmwf_ifs9_bayes_fusion_high_v1',
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                dependency_source_run_ids_json TEXT,
                runtime_layer TEXT NOT NULL DEFAULT 'live',
                trade_authority_status TEXT NOT NULL,
                training_allowed INTEGER NOT NULL
            );
            CREATE TABLE readiness_state (
                readiness_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL,
                dependency_json TEXT NOT NULL,
                provenance_json TEXT NOT NULL
            );
            """
        )
        for label, low, high in (("69°F or below", None, 69.0), ("70-71°F", 70.0, 71.0), ("72°F or above", 72.0, None)):
            conn.execute(
                """
                INSERT INTO market_events
                  (market_slug, city, target_date, temperature_metric, token_id, range_label, range_low, range_high)
                VALUES ('slug', 'NYC', '2026-06-08', 'high', ?, ?, ?, ?)
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
               '2026-06-08', 'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
               'COMPLETE', 'LIVE_ELIGIBLE', '2026-06-06T02:05:00+00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()


def _write_raw_inputs(raw_dir: Path) -> None:
    _write_file(raw_dir / "precision_metadata.json", {"city": "NYC"})
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
            "target_date": "2026-06-08",
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
    assert seed["openmeteo_source_run_id"] == "openmeteo-run"
    assert seed["openmeteo_payload_json"].endswith("raw/openmeteo.json")
    assert seed["openmeteo_manifest_json"].endswith("raw/openmeteo.manifest.json")
    assert seed["precision_metadata_json"].endswith("raw/precision_metadata.json")


def test_single_runs_manifest_horizon_admits_later_target_dates(tmp_path: Path) -> None:
    """A multi-day single-runs payload must not be treated as a one-day manifest.

    Live evidence 2026-06-20: raw_model_forecasts had 18Z rows for day+1 held
    families, but the raw manifest metadata still listed only the artifact
    filename's local start date. Cycle advance then reported NOT_NEEDED while
    held-position belief correctly marked the older posterior stale.
    """

    artifact = _write_file(tmp_path / "openmeteo.json", {"hourly": {}})
    manifest = RawForecastArtifactManifest.from_file(
        artifact,
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time="2026-06-19T18:00:00+00:00",
        source_available_at="2026-06-19T23:30:00+00:00",
        captured_at="2026-06-19T23:31:00+00:00",
        request_url="https://example.invalid/openmeteo",
        request_params={"run": "2026-06-19T18:00", "forecast_hours": 120},
        product_metadata={
            "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
            "openmeteo_endpoint": "single_runs_api",
            "city": "Paris",
            "target_date": "2026-06-19",
            "target_dates": ["2026-06-19"],
            "forecast_hours": 120,
        },
    )

    assert _manifest_allows_target_date(manifest, target_date="2026-06-19")
    assert _manifest_allows_target_date(manifest, target_date="2026-06-21")
    assert not _manifest_allows_target_date(manifest, target_date="2026-06-26")


def test_latest_manifest_rejects_horizon_admitted_payload_without_target_day_samples(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    payload = _write_file(
        raw_dir / "openmeteo.json",
        {
            "hourly": {
                "time": ["2026-06-24T13:00", "2026-06-24T14:00"],
                "temperature_2m": [21.0, 22.0],
            }
        },
    )
    manifest = RawForecastArtifactManifest.from_file(
        payload,
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time="2026-06-24T12:00:00+00:00",
        source_available_at="2026-06-24T19:31:00+00:00",
        captured_at="2026-06-24T19:31:00+00:00",
        request_url="https://example.invalid/openmeteo",
        request_params={"run": "2026-06-24T12:00", "forecast_hours": 120},
        product_metadata={
            "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
            "openmeteo_endpoint": "single_runs_api",
            "city": "London",
            "city_timezone": "Europe/London",
            "target_date": "2026-06-24",
            "forecast_hours": 120,
            "openmeteo_payload_json": str(payload),
        },
    )

    assert _manifest_allows_target_date(manifest, target_date="2026-06-25")
    assert (
        _latest_manifest(
            (manifest,),
            source_id="openmeteo_ecmwf_ifs_9km",
            data_version=OPENMETEO_HIGH_DATA_VERSION,
            city="London",
            target_date="2026-06-25",
            city_timezone="Europe/London",
        )
        is None
    )


def test_seed_discovery_reads_manifests_recursively_and_resolves_relative_to_manifest(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    nested_raw_dir = raw_dir / "20260607T000000Z"
    seed_dir = tmp_path / "seeds"
    _init_db(db_path)
    _write_raw_inputs(nested_raw_dir)

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-07T09:00:00+00:00",
    )

    assert report.status == "DISCOVERED"
    seed = json.loads(Path(report.written_seed_files[0]).read_text(encoding="utf-8"))
    assert seed["openmeteo_payload_json"].endswith("raw/20260607T000000Z/openmeteo.json")
    assert seed["openmeteo_manifest_json"].endswith("raw/20260607T000000Z/openmeteo.manifest.json")
    assert seed["precision_metadata_json"].endswith("raw/20260607T000000Z/precision_metadata.json")


def test_seed_discovery_selects_latest_fusion_materializable_cycle(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    _init_db(db_path)
    _write_file(raw_dir / "precision_metadata.json", {"city": "NYC"})
    _write_manifest(
        raw_dir,
        name="openmeteo-06z",
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time="2026-06-06T06:00:00+00:00",
        source_available_at="2026-06-06T08:30:00+00:00",
        captured_at="2026-06-06T09:00:00+00:00",
        metadata={
            "openmeteo_payload_json": "openmeteo-06z.json",
            "precision_metadata_json": "precision_metadata.json",
            "city": "NYC",
            "target_date": "2026-06-08",
        },
    )
    _write_manifest(
        raw_dir,
        name="openmeteo-12z",
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time="2026-06-06T12:00:00+00:00",
        source_available_at="2026-06-06T12:30:00+00:00",
        captured_at="2026-06-06T12:45:00+00:00",
        metadata={
            "openmeteo_payload_json": "openmeteo-12z.json",
            "precision_metadata_json": "precision_metadata.json",
            "city": "NYC",
            "target_date": "2026-06-08",
        },
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE raw_model_forecasts (
                raw_model_forecast_id INTEGER PRIMARY KEY,
                model TEXT NOT NULL,
                city TEXT NOT NULL,
                metric TEXT NOT NULL,
                target_date TEXT NOT NULL,
                source_cycle_time TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                forecast_value_c REAL NOT NULL,
                lead_days INTEGER,
                captured_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO raw_model_forecasts (
                raw_model_forecast_id, model, city, metric, target_date, source_cycle_time,
                endpoint, forecast_value_c, lead_days, captured_at
            ) VALUES (
                1, 'ifs9', 'NYC', 'high', '2026-06-08', '2026-06-06T06:00:00+00:00',
                'single_runs', 27.0, 2, '2026-06-06T08:00:00+00:00'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-06T13:00:00+00:00",
    )

    assert report.status == "DISCOVERED"
    seed = json.loads(Path(report.written_seed_files[0]).read_text(encoding="utf-8"))
    assert seed["openmeteo_source_run_id"] == "openmeteo-06z-run"
    assert seed["openmeteo_manifest_json"].endswith("openmeteo-06z.manifest.json")


def test_seed_discovery_limit_applies_after_filtering_seedable_targets(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    _init_db(db_path)
    _write_raw_inputs(raw_dir)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO market_events
              (market_slug, city, target_date, temperature_metric, token_id, range_label, range_low, range_high)
            VALUES ('slug-covered', 'NYC', '2026-06-09', 'high', 'covered-token', '70°F', 70.0, 70.0)
            """
        )
        conn.execute(
            """
            INSERT INTO source_run_coverage
              (coverage_id, source_run_id, source_id, city_id, city, city_timezone, target_local_date,
               temperature_metric, data_version, completeness_status, readiness_status, computed_at)
            VALUES
              ('coverage-covered', 'covered-baseline-run', 'ecmwf_open_data', 'NYC', 'NYC', 'America/New_York',
               '2026-06-09', 'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
               'COMPLETE', 'LIVE_ELIGIBLE', '2026-06-07T02:05:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, city, target_date, temperature_metric,
                dependency_source_run_ids_json, trade_authority_status, training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'NYC', '2026-06-09', 'high',
                '{"baseline_b0":"covered-baseline-run"}',
                'LIVE_AUTHORITY', 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO readiness_state (
                readiness_id, strategy_key, dependency_json, provenance_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                "ready-covered",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                json.dumps({"dependencies": [{"role": "baseline_b0", "source_run_id": "covered-baseline-run"}]}),
                json.dumps({"city": "NYC", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-07T09:00:00+00:00",
        limit=1,
    )

    assert report.status == "DISCOVERED"
    seed = json.loads(Path(report.written_seed_files[0]).read_text(encoding="utf-8"))
    assert seed["target_date"] == "2026-06-08"


def test_seed_discovery_does_not_seed_after_local_target_day_starts(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    _init_db(db_path)
    _write_raw_inputs(raw_dir)

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-08T05:00:00+00:00",
    )

    assert report.status == "NO_ELIGIBLE_TARGETS"
    assert report.reason_codes == ("REPLACEMENT_SEED_DISCOVERY_DB_TARGETS_MISSING",)
    assert not list(seed_dir.glob("*.json"))


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


def test_seed_discovery_does_not_skip_current_source_run_because_stale_replacement_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    _init_db(db_path)
    _write_raw_inputs(raw_dir)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE source_run_coverage
            SET source_run_id = 'baseline-current-run',
                computed_at = '2026-06-07T08:00:00+00:00'
            WHERE coverage_id = 'coverage-1'
            """
        )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, city, target_date, temperature_metric,
                dependency_source_run_ids_json, trade_authority_status,
                training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'NYC', '2026-06-08', 'high',
                '{"baseline_b0":"baseline-stale-run"}',
                'LIVE_AUTHORITY', 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO readiness_state (
                readiness_id, strategy_key, dependency_json, provenance_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                "ready-stale",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                json.dumps({"dependencies": [{"role": "baseline_b0", "source_run_id": "baseline-stale-run"}]}),
                json.dumps({"city": "NYC", "target_date": "2026-06-08", "temperature_metric": "high"}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-07T09:00:00+00:00",
    )

    assert report.status == "DISCOVERED"
    seed = json.loads(Path(report.written_seed_files[0]).read_text(encoding="utf-8"))
    assert seed["baseline_source_run_id"] == "baseline-current-run"


def test_seed_discovery_retries_when_current_posterior_exists_but_readiness_is_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    _init_db(db_path)
    _write_raw_inputs(raw_dir)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, city, target_date, temperature_metric,
                dependency_source_run_ids_json, trade_authority_status,
                training_allowed
            ) VALUES (
                ?,
                'NYC', '2026-06-08', 'high',
                '{"baseline_b0":"baseline-run"}',
                'LIVE_AUTHORITY', 0
            )
            """,
            (REPLACEMENT_SOURCE_ID,),
        )
        conn.commit()
    finally:
        conn.close()

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-07T09:00:00+00:00",
    )

    assert report.status == "DISCOVERED"
    seed = json.loads(Path(report.written_seed_files[0]).read_text(encoding="utf-8"))
    assert seed["baseline_source_run_id"] == "baseline-run"


def test_seed_discovery_skips_when_current_posterior_and_readiness_exist(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    _init_db(db_path)
    _write_raw_inputs(raw_dir)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, city, target_date, temperature_metric,
                dependency_source_run_ids_json, trade_authority_status,
                training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'NYC', '2026-06-08', 'high',
                '{"baseline_b0":"baseline-run"}',
                'LIVE_AUTHORITY', 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO readiness_state (
                readiness_id, strategy_key, dependency_json, provenance_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                "ready-current",
                REPLACEMENT_STRATEGY_KEY,
                json.dumps({"dependencies": [{"role": "baseline_b0", "source_run_id": "baseline-run"}]}),
                json.dumps({"city": "NYC", "target_date": "2026-06-08", "temperature_metric": "high"}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-07T09:00:00+00:00",
    )

    assert report.status == "NO_ELIGIBLE_TARGETS"
    assert report.reason_codes == ("REPLACEMENT_SEED_DISCOVERY_DB_TARGETS_MISSING",)
    assert not list(seed_dir.glob("*.json"))


def test_seed_discovery_blocks_when_source_run_coverage_schema_is_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    _write_raw_inputs(raw_dir)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE market_events (
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                token_id TEXT,
                range_label TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-07T09:00:00+00:00",
    )

    assert report.status == "BLOCKED"
    assert report.reason_codes == ("REPLACEMENT_SEED_DISCOVERY_SOURCE_RUN_COVERAGE_SCHEMA_MISSING",)


def test_seed_discovery_blocks_when_replacement_dependency_schema_is_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    _write_raw_inputs(raw_dir)
    conn = sqlite3.connect(db_path)
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
            CREATE TABLE forecast_posteriors (
                posterior_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                runtime_layer TEXT NOT NULL DEFAULT 'live',
                trade_authority_status TEXT NOT NULL,
                training_allowed INTEGER NOT NULL
            );
            CREATE TABLE readiness_state (
                readiness_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL,
                provenance_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO market_events
              (market_slug, city, target_date, temperature_metric, token_id, range_label, range_low, range_high)
            VALUES ('slug', 'NYC', '2026-06-07', 'high', 'token', '70°F', 70.0, 70.0)
            """
        )
        conn.execute(
            "INSERT INTO source_run VALUES ('baseline-current-run', 'ecmwf_open_data', 'mx2t3_high', '2026-06-07T00:00:00+00:00', '2026-06-07T02:00:00+00:00')"
        )
        conn.execute(
            """
            INSERT INTO source_run_coverage
              (coverage_id, source_run_id, source_id, city_id, city, city_timezone, target_local_date,
               temperature_metric, data_version, completeness_status, readiness_status, computed_at)
            VALUES
              ('coverage-1', 'baseline-current-run', 'ecmwf_open_data', 'NYC', 'NYC', 'America/New_York',
               '2026-06-07', 'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
               'COMPLETE', 'LIVE_ELIGIBLE', '2026-06-07T02:05:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, city, target_date, temperature_metric,
                trade_authority_status, training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'NYC', '2026-06-07', 'high', 'LIVE_AUTHORITY', 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO readiness_state (readiness_id, strategy_key, provenance_json)
            VALUES (?, ?, ?)
            """,
            (
                "ready-old-schema",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                json.dumps({"city": "NYC", "target_date": "2026-06-07", "temperature_metric": "high"}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-07T09:00:00+00:00",
    )

    assert report.status == "BLOCKED"
    assert report.reason_codes == (
        "REPLACEMENT_SEED_DISCOVERY_CURRENT_TARGET_PLAN_REPLACEMENT_CURRENT_TARGET_PLAN_POSTERIOR_SCHEMA_MISSING",
    )
