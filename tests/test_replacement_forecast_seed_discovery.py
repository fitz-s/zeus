# Created: 2026-06-06
# Last reused/audited: 2026-07-18
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-07; last_reused=2026-07-18
# Purpose: Protect automatic replacement seed discovery from DB context plus raw manifests.
# Reuse: Run before enabling daemon-side replacement shadow materialization discovery.
# Authority basis: Simple switch must not depend on hand-authored seeds once raw inputs exist.
"""Replacement forecast materialization seed discovery tests."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, write_manifest
from src.data.replacement_forecast_readiness import SOURCE_ID as REPLACEMENT_SOURCE_ID
from src.data.replacement_forecast_readiness import STRATEGY_KEY as REPLACEMENT_STRATEGY_KEY
from src.data.replacement_forecast_seed_discovery import (
    _load_manifest_files,
    _load_manifests,
    _manifest_allows_target_date,
    _latest_manifest,
    _seed_target_sort_key,
    discover_replacement_forecast_materialization_seeds,
)


def test_seed_target_sort_keeps_day0_retries_from_starving_pre_settlement_q() -> None:
    day0_held = SimpleNamespace(
        city="Manila",
        target_date="2026-07-18",
        temperature_metric="high",
        day0_observed_extreme_required=True,
    )
    future = SimpleNamespace(
        city="Paris",
        target_date="2026-07-19",
        temperature_metric="high",
        day0_observed_extreme_required=False,
    )
    held = {("Manila", "2026-07-18", "high"): 0}

    ordered = sorted(
        (day0_held, future),
        key=lambda row: _seed_target_sort_key(row, held),
    )

    assert ordered == [future, day0_held]


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
    payload_name = metadata.get("openmeteo_payload_json")
    if isinstance(payload_name, str) and payload_name.strip():
        payload_path = Path(payload_name)
        if not payload_path.is_absolute():
            payload_path = raw_dir / payload_path
        if not payload_path.exists() or payload_path == artifact:
            _write_file(
                payload_path,
                {
                    "hourly": {
                        "time": ["2026-06-08T00:00", "2026-06-08T12:00"],
                        "temperature_2m": [20.0, 24.0],
                    }
                },
            )
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


def test_load_manifests_reuses_unchanged_files_but_rechecks_availability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import src.data.replacement_forecast_seed_discovery as discovery

    raw_dir = tmp_path / "raw"
    manifest_path = _write_manifest(
        raw_dir,
        name="future",
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        metadata={},
        source_available_at="2026-06-07T00:00:00+00:00",
        captured_at="2026-06-07T00:01:00+00:00",
    )
    discovery._MANIFEST_CACHE.pop(raw_dir.resolve(), None)
    real_read = discovery.read_manifest
    reads: list[Path] = []

    def _read(path: Path):
        reads.append(path)
        return real_read(path)

    monkeypatch.setattr(discovery, "read_manifest", _read)

    before = _load_manifests(
        raw_dir, computed_at=discovery._dt("2026-06-06T23:59:00+00:00", field_name="computed_at")
    )
    after = _load_manifests(
        raw_dir, computed_at=discovery._dt("2026-06-07T00:02:00+00:00", field_name="computed_at")
    )
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    changed = _load_manifests(
        raw_dir, computed_at=discovery._dt("2026-06-07T00:02:00+00:00", field_name="computed_at")
    )

    assert before == ()
    assert len(after) == len(changed) == 1
    assert reads == [manifest_path.resolve(), manifest_path.resolve()]


def test_load_manifests_singleflights_concurrent_inventory_scans(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import src.data.replacement_forecast_seed_discovery as discovery

    raw_dir = tmp_path / "raw"
    _write_manifest(
        raw_dir,
        name="selected",
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        metadata={},
    )
    root = raw_dir.resolve()
    discovery._MANIFEST_CACHE.pop(root, None)
    discovery._MANIFEST_LOADS.pop(root, None)
    discovery._MANIFEST_CACHE_VERSIONS.pop(root, None)
    first_scan_started = threading.Event()
    release_first_scan = threading.Event()
    real_rglob = Path.rglob
    scans = 0

    def blocked_rglob(path: Path, pattern: str):
        nonlocal scans
        scans += 1
        first_scan_started.set()
        assert release_first_scan.wait(1.0)
        return real_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", blocked_rglob)
    results: list[tuple[RawForecastArtifactManifest, ...]] = []

    def load() -> None:
        results.append(
            _load_manifests(
                raw_dir,
                computed_at=datetime.fromisoformat("2026-06-06T04:00:00+00:00"),
            )
        )

    first = threading.Thread(target=load)
    second = threading.Thread(target=load)
    first.start()
    assert first_scan_started.wait(0.5)
    second.start()
    release_first_scan.set()
    first.join(1.0)
    second.join(1.0)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert scans == 1
    assert [len(result) for result in results] == [1, 1]


def test_load_manifests_waiter_uses_completed_generation_cache(
    tmp_path: Path,
) -> None:
    import time

    import src.data.replacement_forecast_seed_discovery as discovery

    raw_dir = tmp_path / "raw"
    manifest_path = _write_manifest(
        raw_dir,
        name="selected",
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        metadata={},
    ).resolve()
    root = raw_dir.resolve()
    manifest = discovery._read_manifest_with_path(manifest_path)
    stat = manifest_path.stat()
    signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)

    with discovery._MANIFEST_CACHE_LOCK:
        prior = threading.Condition(discovery._MANIFEST_CACHE_LOCK)
        discovery._MANIFEST_CACHE.pop(root, None)
        discovery._MANIFEST_CACHE_VERSIONS[root] = 0
        discovery._MANIFEST_LOADS[root] = prior

    results: list[tuple[RawForecastArtifactManifest, ...]] = []
    waiter = threading.Thread(
        target=lambda: results.append(
            _load_manifests(
                raw_dir,
                computed_at=datetime.fromisoformat("2026-06-06T04:00:00+00:00"),
            )
        )
    )
    waiter.start()
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and not prior._waiters:  # noqa: SLF001
        time.sleep(0.005)
    assert prior._waiters  # noqa: SLF001

    with discovery._MANIFEST_CACHE_LOCK:
        discovery._MANIFEST_CACHE[root] = {manifest_path: (signature, manifest)}
        discovery._MANIFEST_CACHE_VERSIONS[root] = 1
        discovery._MANIFEST_LOADS.pop(root)
        prior.notify_all()
        # A new loader generation wins the lock before the old waiter. The
        # waiter must consume generation 1 instead of waiting on generation 2.
        replacement = threading.Condition(discovery._MANIFEST_CACHE_LOCK)
        discovery._MANIFEST_LOADS[root] = replacement

    waiter.join(0.5)
    returned_without_replacement_notify = not waiter.is_alive()
    with discovery._MANIFEST_CACHE_LOCK:
        discovery._MANIFEST_LOADS.pop(root, None)
        replacement.notify_all()
    waiter.join(0.5)

    assert returned_without_replacement_notify is True
    assert [len(result) for result in results] == [1]


def test_load_manifest_files_reads_only_producer_committed_paths(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    selected = _write_manifest(
        raw_dir,
        name="selected",
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        metadata={},
    )
    _write_manifest(
        raw_dir,
        name="historical",
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        metadata={},
    )

    manifests = _load_manifest_files(
        (selected,),
        computed_at=datetime.fromisoformat("2026-06-06T04:00:00+00:00"),
    )

    assert len(manifests) == 1
    assert manifests[0].product_metadata["manifest_json"] == str(selected.resolve())


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


def _write_world_day0_observation(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE observation_instants (
                city TEXT,
                target_date TEXT,
                local_timestamp TEXT,
                utc_timestamp TEXT,
                causality_status TEXT,
                authority TEXT,
                source_role TEXT,
                training_allowed INTEGER,
                source TEXT,
                station_id TEXT,
                temp_unit TEXT,
                imported_at TEXT,
                running_max REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO observation_instants VALUES (
                'NYC',
                '2026-06-08',
                '2026-06-08T01:00:00-04:00',
                '2026-06-08T05:00:00+00:00',
                'OK',
                'VERIFIED',
                'historical_hourly',
                1,
                'wu_icao_history',
                'KLGA',
                'F',
                '2026-06-08T05:05:00+00:00',
                77.0
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


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


def test_legacy_single_runs_manifest_horizon_admits_later_target_dates(tmp_path: Path) -> None:
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
            "forecast_hours": 120,
        },
    )

    assert _manifest_allows_target_date(manifest, target_date="2026-06-19")
    assert _manifest_allows_target_date(manifest, target_date="2026-06-21")
    assert not _manifest_allows_target_date(manifest, target_date="2026-06-26")


def test_exact_target_dates_do_not_horizon_admit_wrong_daily_payload(tmp_path: Path) -> None:
    """New live manifests are target-day scoped and must not bind the wrong payload."""

    artifact = _write_file(tmp_path / "openmeteo_Paris_2026-06-19_high.json", {"hourly": {}})
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
            "openmeteo_payload_json": str(artifact),
        },
    )

    assert _manifest_allows_target_date(manifest, target_date="2026-06-19")
    assert not _manifest_allows_target_date(manifest, target_date="2026-06-20")


def test_meta_stamped_current_target_horizon_admits_covered_later_day(tmp_path: Path) -> None:
    """Meta-stamped current-target payloads are multi-day live inputs.

    Live evidence 2026-07-03: 12Z Open-Meteo payloads physically covered day+1,
    but manifests carried target_dates=[start_day]. Seed discovery then selected
    the older 00Z day+1 artifact and cycle-advance froze posteriors at 00Z.
    """

    raw_dir = tmp_path / "raw"
    precision = _write_file(raw_dir / "precision_metadata.json", {"city": "Paris"})
    old_payload = _write_file(
        raw_dir / "old_openmeteo.json",
        {
            "hourly": {
                "time": ["2026-06-20T00:00", "2026-06-20T12:00"],
                "temperature_2m": [19.0, 24.0],
            }
        },
    )
    fresh_payload = _write_file(
        raw_dir / "fresh_openmeteo.json",
        {
            "hourly": {
                "time": ["2026-06-20T12:00", "2026-06-21T00:00", "2026-06-21T12:00"],
                "temperature_2m": [18.0, 20.0, 25.0],
            }
        },
    )
    old_manifest = RawForecastArtifactManifest.from_file(
        old_payload,
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time="2026-06-20T00:00:00+00:00",
        source_available_at="2026-06-20T06:30:00+00:00",
        captured_at="2026-06-20T06:31:00+00:00",
        request_url="https://example.invalid/old",
        request_params={"run": "2026-06-20T00:00", "forecast_hours": 120},
        product_metadata={
            "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
            "openmeteo_endpoint": "single_runs_api",
            "city": "Paris",
            "city_timezone": "Europe/Paris",
            "target_date": "2026-06-20",
            "target_dates": ["2026-06-20"],
            "forecast_hours": 120,
            "openmeteo_payload_json": str(old_payload),
            "precision_metadata_json": str(precision),
        },
    )
    fresh_manifest = RawForecastArtifactManifest.from_file(
        fresh_payload,
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time="2026-06-20T12:00:00+00:00",
        source_available_at="2026-06-20T18:30:00+00:00",
        captured_at="2026-06-20T18:31:00+00:00",
        request_url="https://example.invalid/fresh",
        request_params={"run": "2026-06-20T12:00", "forecast_hours": 120},
        product_metadata={
            "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
            "openmeteo_endpoint": "standard_api_meta_stamped",
            "city": "Paris",
            "city_timezone": "Europe/Paris",
            "target_date": "2026-06-20",
            "target_dates": ["2026-06-20"],
            "forecast_hours": 120,
            "openmeteo_payload_json": str(fresh_payload),
            "precision_metadata_json": str(precision),
        },
    )

    assert _manifest_allows_target_date(fresh_manifest, target_date="2026-06-21")
    selected = _latest_manifest(
        (old_manifest, fresh_manifest),
        source_id="openmeteo_ecmwf_ifs_9km",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        city="Paris",
        target_date="2026-06-21",
        city_timezone="Europe/Paris",
    )

    assert selected is fresh_manifest


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


def test_seed_discovery_selects_latest_anchor_even_when_fusion_current_missing(tmp_path: Path) -> None:
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
            UPDATE source_run
            SET source_cycle_time = '2026-06-06T12:00:00+00:00',
                source_available_at = '2026-06-06T12:30:00+00:00'
            WHERE source_run_id = 'baseline-run'
            """
        )
        conn.execute(
            """
            UPDATE source_run_coverage
            SET computed_at = '2026-06-06T12:35:00+00:00'
            WHERE source_run_id = 'baseline-run'
            """
        )
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
    assert seed["openmeteo_source_run_id"] == "openmeteo-12z-run"
    assert seed["openmeteo_manifest_json"].endswith("openmeteo-12z.manifest.json")
    assert (
        "REPLACEMENT_SEED_DISCOVERY_FUSION_CURRENT_VALUES_MISSING_NON_BLOCKING"
        in report.reason_codes
    )


def test_seed_discovery_does_not_write_mixed_baseline_anchor_cycle_seed(tmp_path: Path) -> None:
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
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE source_run
            SET source_cycle_time = '2026-06-06T12:00:00+00:00',
                source_available_at = '2026-06-06T12:30:00+00:00'
            WHERE source_run_id = 'baseline-run'
            """
        )
        conn.execute(
            """
            UPDATE source_run_coverage
            SET computed_at = '2026-06-06T12:35:00+00:00'
            WHERE source_run_id = 'baseline-run'
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

    assert report.status == "NO_ELIGIBLE_TARGETS"
    assert report.discovered_count == 0
    assert report.failed_count == 1
    assert report.written_seed_files == ()
    assert "REPLACEMENT_MATERIALIZATION_SEED_OM9_CYCLE_REGRESSES_BASELINE" in report.reason_codes


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


def test_seed_discovery_prioritizes_held_family_and_skips_unchanged_blocked_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    trade_db = tmp_path / "zeus_trades.db"
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM market_events")
        conn.execute("DELETE FROM source_run_coverage")
        for city, run_id, tz in (
            ("Amsterdam", "amsterdam-baseline-run", "Europe/Amsterdam"),
            ("Tokyo", "tokyo-baseline-run", "Asia/Tokyo"),
        ):
            conn.execute(
                "INSERT INTO source_run VALUES (?, 'ecmwf_open_data', 'mx2t3_high', "
                "'2026-06-06T00:00:00+00:00', '2026-06-06T02:00:00+00:00')",
                (run_id,),
            )
            for label, low, high in (("69°F or below", None, 69.0), ("70-71°F", 70.0, 71.0)):
                conn.execute(
                    """
                    INSERT INTO market_events
                      (market_slug, city, target_date, temperature_metric, token_id, range_label, range_low, range_high)
                    VALUES (?, ?, '2026-06-08', 'high', ?, ?, ?, ?)
                    """,
                    (f"slug-{city}", city, f"{city}-{label}", label, low, high),
                )
            conn.execute(
                """
                INSERT INTO source_run_coverage
                  (coverage_id, source_run_id, source_id, city_id, city, city_timezone, target_local_date,
                   temperature_metric, data_version, completeness_status, readiness_status, computed_at)
                VALUES (?, ?, 'ecmwf_open_data', ?, ?, ?,
                   '2026-06-08', 'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
                   'COMPLETE', 'LIVE_ELIGIBLE', '2026-06-06T02:05:00+00:00')
                """,
                (f"coverage-{city}", run_id, city, city, tz),
            )
        conn.commit()
    finally:
        conn.close()
    for city in ("Amsterdam", "Tokyo"):
        _write_file(raw_dir / f"precision_{city}.json", {"city": city})
        _write_manifest(
            raw_dir,
            name=f"openmeteo_{city}",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version=OPENMETEO_HIGH_DATA_VERSION,
            metadata={
                "openmeteo_payload_json": f"openmeteo_{city}.json",
                "precision_metadata_json": f"precision_{city}.json",
                "city": city,
                "target_date": "2026-06-08",
            },
        )
    trade_conn = sqlite3.connect(trade_db)
    try:
        trade_conn.execute(
            """
            CREATE TABLE position_current (
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                phase TEXT
            )
            """
        )
        trade_conn.execute(
            """
            INSERT INTO position_current
              (city, target_date, temperature_metric, phase)
        VALUES ('Tokyo', '2026-06-08', 'high', 'active')
            """
        )
        trade_conn.commit()
    finally:
        trade_conn.close()
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery._zeus_trade_db_path",
        lambda: trade_db,
    )

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-06T04:00:00+00:00",
        limit=1,
    )

    assert report.status == "DISCOVERED"
    seed = json.loads(Path(report.written_seed_files[0]).read_text(encoding="utf-8"))
    assert seed["city"] == "Tokyo"

    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {},
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery._unchanged_blocked_seed_attempt",
        lambda **kwargs: kwargs["seed"]["city"] == "Amsterdam",
    )
    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-06T04:01:00+00:00",
        limit=1,
    )

    assert report.status == "DISCOVERED"
    assert "REPLACEMENT_SEED_DISCOVERY_UNCHANGED_BLOCKED_INPUT_SKIPPED" in report.reason_codes
    seed = json.loads(Path(report.written_seed_files[0]).read_text(encoding="utf-8"))
    assert seed["city"] == "Tokyo"


def test_seed_discovery_does_not_seed_after_local_target_day_starts_without_observed_extreme(
    tmp_path: Path,
) -> None:
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
    assert report.reason_codes == ("REPLACEMENT_SEED_DISCOVERY_DAY0_OBSERVED_EXTREME_MISSING",)
    assert not list(seed_dir.glob("*.json"))


def test_seed_discovery_seeds_day0_when_canonical_observed_extreme_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    seed_dir = tmp_path / "seeds"
    world_path = tmp_path / "world.db"
    _init_db(db_path)
    _write_raw_inputs(raw_dir)
    _write_world_day0_observation(world_path)

    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.get_world_connection_read_only",
        lambda: sqlite3.connect(world_path),
    )

    report = discover_replacement_forecast_materialization_seeds(
        forecast_db=db_path,
        raw_manifest_dir=raw_dir,
        seed_dir=seed_dir,
        computed_at="2026-06-08T05:30:00+00:00",
    )

    assert report.status == "DISCOVERED"
    seed = json.loads(Path(report.written_seed_files[0]).read_text(encoding="utf-8"))
    assert seed["city"] == "NYC"
    assert seed["day0_observed_extreme_c"] == (77.0 - 32.0) * 5.0 / 9.0
    assert seed["day0_observed_extreme_source"] == "durable_observation_instants"
    assert seed["day0_observed_extreme_observation_time"] == "2026-06-08T05:00:00+00:00"
    assert seed["day0_observed_extreme_sample_count"] == 1
    assert seed["day0_observed_extreme_unit"] == "F"


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
            "INSERT INTO source_run VALUES ("
            "'baseline-current-run', 'ecmwf_open_data', 'mx2t3_high', "
            "'2026-06-06T00:00:00+00:00', '2026-06-06T02:00:00+00:00')"
        )
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

    assert report.status == "DISCOVERED", report.reason_codes
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
