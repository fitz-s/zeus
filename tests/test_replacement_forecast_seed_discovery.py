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


def _hourly_localday_payload(*, local_date: str, n_days: int, tz_offset_hours: int) -> dict:
    """A single-runs-shaped hourly payload starting at 00:00 LOCAL on ``local_date``.

    The provider serves UTC-naive times already in the requested timezone, so the
    extractor reads ``hourly.time`` as local wall-clock. ``n_days`` controls how many
    local days the file covers from ``local_date`` (1 = the broken single-day partial
    capture; >=2 = a healthy multi-day horizon). ``tz_offset_hours`` is unused in the
    bytes (times are local) but documents the city family the payload belongs to.
    """
    from datetime import date as _date, datetime as _dt, timedelta as _td

    del tz_offset_hours
    start = _dt.fromisoformat(f"{local_date}T00:00")
    times: list[str] = []
    temps: list[float] = []
    for hour in range(24 * n_days):
        ts = start + _td(hours=hour)
        times.append(ts.strftime("%Y-%m-%dT%H:%M"))
        temps.append(20.0 + (hour % 24) * 0.1)
    return {
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
        "hourly": {"time": times, "temperature_2m": temps},
    }


def _write_anchor_manifest_with_payload(
    raw_dir: Path,
    *,
    name: str,
    declared_target_date: str,
    payload: dict,
    source_available_at: str,
    captured_at: str,
) -> RawForecastArtifactManifest:
    """Write a single-runs anchor manifest whose payload bytes are ``payload``.

    The manifest declares ``forecast_hours=120`` (as the live downloader always does),
    so horizon-admission trusts a 120h coverage even when the bytes are a 24h partial.
    Returns the manifest as it would be loaded by ``_load_manifests`` (with the
    ``manifest_json`` + ``openmeteo_payload_json`` paths threaded through metadata).
    """
    payload_path = _write_file(raw_dir / f"{name}.json", payload)
    precision_path = _write_file(raw_dir / f"precision_{name}.json", {"ok": True})
    manifest_path = raw_dir / f"{name}.manifest.json"
    manifest = RawForecastArtifactManifest.from_file(
        payload_path,
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time="2026-06-22T12:00:00+00:00",
        source_available_at=source_available_at,
        captured_at=captured_at,
        request_url="https://example.invalid/openmeteo",
        request_params={"run": "2026-06-22T12:00", "forecast_hours": 120},
        product_metadata={
            "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
            "openmeteo_endpoint": "single_runs_api",
            "city": "Chengdu",
            "cities": ["Chengdu"],
            "target_date": declared_target_date,
            "target_dates": [declared_target_date],
            "forecast_hours": 120,
            "openmeteo_payload_json": str(payload_path),
            "precision_metadata_json": str(precision_path),
            "manifest_json": str(manifest_path),
        },
    )
    write_manifest(manifest, manifest_path)
    # Mirror _load_manifests: thread manifest_json so the base-dir/payload resolves.
    return RawForecastArtifactManifest(
        **{
            **manifest.to_dict(),
            "product_metadata": {
                **dict(manifest.product_metadata),
                "manifest_json": str(manifest_path),
            },
        }
    )


def test_latest_manifest_skips_partial_horizon_payload_for_eastward_target(tmp_path: Path) -> None:
    """Live eastward blackout 2026-06-23: a partial-horizon single-runs capture (24h,
    only the launch local day) is mislabeled forecast_hours=120 in its manifest, so
    horizon-admission admits it for a LATER target date it cannot serve. When a healthy
    sibling manifest at the SAME cycle/availability/captured stamps DOES cover the wanted
    day, _latest_manifest must select the COVERING manifest, not the partial one whose
    extraction raises 'insufficient Open-Meteo hourly samples inside target local day'.

    Both manifests carry identical (source_cycle_time, source_available_at, captured_at),
    so the pre-fix max() tiebreak is order-dependent and can return the broken partial.
    """
    from src.data.replacement_forecast_seed_discovery import _latest_manifest

    raw_dir = tmp_path / "raw" / "20260622T120000Z"

    # BROKEN: declared target_date 2026-06-23, 24h payload covering ONLY 2026-06-23.
    partial = _write_anchor_manifest_with_payload(
        raw_dir,
        name="openmeteo_Chengdu_2026-06-23_high_20260622T120000Z",
        declared_target_date="2026-06-23",
        payload=_hourly_localday_payload(local_date="2026-06-23", n_days=1, tz_offset_hours=8),
        source_available_at="2026-06-23T08:10:15+00:00",
        captured_at="2026-06-23T08:10:15+00:00",
    )
    # HEALTHY: declared target_date 2026-06-24, multi-day payload covering 2026-06-24.
    covering = _write_anchor_manifest_with_payload(
        raw_dir,
        name="openmeteo_Chengdu_2026-06-24_high_20260622T120000Z",
        declared_target_date="2026-06-24",
        payload=_hourly_localday_payload(local_date="2026-06-23", n_days=3, tz_offset_hours=8),
        source_available_at="2026-06-23T08:10:15+00:00",
        captured_at="2026-06-23T08:10:15+00:00",
    )

    # Order the partial FIRST so an order-dependent max() tiebreak would return it.
    chosen = _latest_manifest(
        (partial, covering),
        source_id="openmeteo_ecmwf_ifs_9km",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        city="Chengdu",
        target_date="2026-06-24",
        city_timezone="Asia/Shanghai",
    )
    assert chosen is not None
    chosen_payload = chosen.product_metadata.get("openmeteo_payload_json")
    assert chosen_payload == covering.product_metadata.get("openmeteo_payload_json"), (
        "must select the manifest whose payload covers 2026-06-24, not the 24h partial"
    )


def test_latest_manifest_unchanged_when_westward_payload_already_covers(tmp_path: Path) -> None:
    """No regression for the working (westward) cities: when the freshest manifest's payload
    already covers the wanted local day, the coverage-aware selector returns the SAME manifest
    the recency tiebreak would have — the freshest covering one — never a staler sibling."""
    from src.data.replacement_forecast_seed_discovery import _latest_manifest

    raw_dir = tmp_path / "raw" / "20260622T120000Z"

    older = _write_anchor_manifest_with_payload(
        raw_dir,
        name="openmeteo_Chengdu_2026-06-24_high_older",
        declared_target_date="2026-06-24",
        payload=_hourly_localday_payload(local_date="2026-06-23", n_days=3, tz_offset_hours=8),
        source_available_at="2026-06-23T06:00:00+00:00",
        captured_at="2026-06-23T06:00:00+00:00",
    )
    fresher = _write_anchor_manifest_with_payload(
        raw_dir,
        name="openmeteo_Chengdu_2026-06-24_high_fresher",
        declared_target_date="2026-06-24",
        payload=_hourly_localday_payload(local_date="2026-06-23", n_days=3, tz_offset_hours=8),
        source_available_at="2026-06-23T08:10:15+00:00",
        captured_at="2026-06-23T08:10:15+00:00",
    )

    chosen = _latest_manifest(
        (older, fresher),
        source_id="openmeteo_ecmwf_ifs_9km",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        city="Chengdu",
        target_date="2026-06-24",
        city_timezone="Asia/Shanghai",
    )
    assert chosen is not None
    assert chosen.product_metadata.get("openmeteo_payload_json") == fresher.product_metadata.get(
        "openmeteo_payload_json"
    ), "both cover the day; the fresher (recency) manifest must still win"


def test_latest_manifest_falls_back_to_recency_when_no_candidate_covers(tmp_path: Path) -> None:
    """If NO admitted manifest covers the wanted day (e.g. all are partial captures), the
    selector falls back to the prior recency tiebreak and returns a candidate (the extractor
    remains the fail-closed backstop) rather than returning None and starving the target."""
    from src.data.replacement_forecast_seed_discovery import _latest_manifest

    raw_dir = tmp_path / "raw" / "20260622T120000Z"

    partial_old = _write_anchor_manifest_with_payload(
        raw_dir,
        name="openmeteo_Chengdu_2026-06-23_high_old",
        declared_target_date="2026-06-23",
        payload=_hourly_localday_payload(local_date="2026-06-23", n_days=1, tz_offset_hours=8),
        source_available_at="2026-06-23T06:00:00+00:00",
        captured_at="2026-06-23T06:00:00+00:00",
    )
    partial_new = _write_anchor_manifest_with_payload(
        raw_dir,
        name="openmeteo_Chengdu_2026-06-23_high_new",
        declared_target_date="2026-06-23",
        payload=_hourly_localday_payload(local_date="2026-06-23", n_days=1, tz_offset_hours=8),
        source_available_at="2026-06-23T08:10:15+00:00",
        captured_at="2026-06-23T08:10:15+00:00",
    )

    chosen = _latest_manifest(
        (partial_old, partial_new),
        source_id="openmeteo_ecmwf_ifs_9km",
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        city="Chengdu",
        target_date="2026-06-24",
        city_timezone="Asia/Shanghai",
    )
    # Neither covers 2026-06-24; recency decides — the fresher one is returned (not None).
    assert chosen is not None
    assert chosen.product_metadata.get("openmeteo_payload_json") == partial_new.product_metadata.get(
        "openmeteo_payload_json"
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
