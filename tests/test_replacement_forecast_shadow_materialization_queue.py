# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect replacement forecast shadow materialization queue wiring.
# Reuse: Run before changing daemon-side replacement posterior generation.
# Authority basis: Simple switch must create shadow/veto rows without trade or settlement writes.
"""Replacement forecast shadow materialization queue tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import src.main as main_module
from src.config import PROJECT_ROOT
from src.data.replacement_forecast_runtime_policy import SHADOW_FLAG
from src.data.replacement_forecast_shadow_materialization_queue import (
    process_replacement_forecast_shadow_materialization_queue,
)


def _completed(returncode: int, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["materialize"], returncode=returncode, stdout=stdout, stderr=stderr)


def _minimal_valid_request() -> str:
    """A request payload carrying the minimal keys the poison-pill gate requires.

    These dispatch-plumbing tests use a stub runner, so the payload need only satisfy
    _validate_request_payload (temperature_metric, target_date, source_cycle_time, and an
    AIFS input selector) — the stub runner never reads the file. A bare ``{}`` would now
    be rejected pre-spawn by the antibody, so dispatch tests must use a valid-shaped stub.
    """
    return json.dumps(
        {
            "temperature_metric": "high",
            "target_date": "2026-06-07",
            "source_cycle_time": "2026-06-06T00:00:00+00:00",
            "aifs_samples_json": "aifs_samples.json",
        }
    )


def _write_seed_inputs(tmp_path: Path, *, future_dependency: bool = False) -> dict[str, object]:
    (tmp_path / "aifs_samples.json").write_text(
        json.dumps(
            {
                "samples": [
                    {"member_id": "pf-001", "valid_time_utc": "2026-06-06T18:00:00+00:00", "temperature": 18.0, "temperature_unit": "C"},
                    {"member_id": "pf-002", "valid_time_utc": "2026-06-06T18:00:00+00:00", "temperature": 25.0, "temperature_unit": "C"},
                    {"member_id": "pf-003", "valid_time_utc": "2026-06-06T18:00:00+00:00", "temperature": 32.0, "temperature_unit": "C"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "openmeteo_payload.json").write_text(
        json.dumps(
            {
                "hourly_units": {"temperature_2m": "C"},
                "hourly": {"time": ["2026-06-07T00:00"], "temperature_2m": [20.0]},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "precision_metadata.json").write_text(
        json.dumps(
            {
                "city": "Shanghai",
                "station_id": "ZSSS",
                "city_lat": 31.2304,
                "city_lon": 121.4737,
                "station_lat": 31.1979,
                "station_lon": 121.3363,
                "requested_lat": 31.1979,
                "requested_lon": 121.3363,
                "requested_coordinate_precision_decimals": 4,
                "nearest_grid_lat": 31.2,
                "nearest_grid_lon": 121.3,
                "nearest_grid_distance_km": 3.5,
                "native_grid": "openmeteo_ecmwf_ifs_9km",
                "delivery_grid_resolution": "0p1",
                "interpolation_method": "nearest_gridpoint",
                "endpoint_mode": "hourly_zeus_aggregated",
                "local_day_start_utc": "2026-06-06T16:00:00+00:00",
                "local_day_end_utc": "2026-06-07T16:00:00+00:00",
                "timezone_name": "Asia/Shanghai",
                "target_local_date": "2026-06-07",
                "temperature_unit": "C",
                "anchor_sigma_c": 3.0,
                "grid_elevation_m": 4.0,
                "station_elevation_m": 3.0,
                "land_sea_mask": "land",
                "city_class": "flat_inland",
                "station_mapping_policy": "settlement_station",
            }
        ),
        encoding="utf-8",
    )
    return {
        "city": "Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "aifs_source_run_id": "aifs-run",
        "aifs_source_available_at": "2026-06-06T05:00:00+00:00" if future_dependency else "2026-06-06T02:30:00+00:00",
        "openmeteo_source_run_id": "openmeteo-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "aifs_samples_json": "aifs_samples.json",
        "openmeteo_payload_json": "openmeteo_payload.json",
        "precision_metadata_json": "precision_metadata.json",
        "bins": [
            {"bin_id": "cool", "lower_c": None, "upper_c": 20.0, "center_c": 19.0},
            {"bin_id": "warm", "lower_c": 21.0, "upper_c": 30.0, "center_c": 25.0},
            {"bin_id": "hot", "lower_c": 31.0, "upper_c": None, "center_c": 32.0},
        ],
    }


def test_materialization_queue_absent_or_empty_is_noop(tmp_path) -> None:
    absent = process_replacement_forecast_shadow_materialization_queue(
        request_dir=tmp_path / "missing",
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
    )

    assert absent.status == "NO_REQUESTS"
    assert absent.processed_count == 0
    assert absent.failed_count == 0

    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    empty = process_replacement_forecast_shadow_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
    )

    assert empty.status == "NO_REQUESTS"
    assert empty.reason_codes == ("REPLACEMENT_SHADOW_MATERIALIZATION_QUEUE_EMPTY",)


def test_materialization_queue_processes_success_and_failure_with_receipts(tmp_path) -> None:
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    (request_dir / "a.json").write_text(_minimal_valid_request(), encoding="utf-8")
    (request_dir / "b.json").write_text(_minimal_valid_request(), encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def runner(argv):
        calls.append(tuple(argv))
        if any(str(part).endswith("a.json") for part in argv):
            return _completed(0, stdout='{"status":"SHADOW_ONLY"}')
        return _completed(2, stderr='{"status":"ERROR"}')

    report = process_replacement_forecast_shadow_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        runner=runner,
    )

    assert report.status == "FAILED"
    assert report.processed_count == 1
    assert report.failed_count == 1
    assert not list(request_dir.glob("*.json"))
    processed = [path for path in (tmp_path / "processed").glob("*.json") if not path.name.endswith(".receipt.json")]
    failed = [path for path in (tmp_path / "failed").glob("*.json") if not path.name.endswith(".receipt.json")]
    assert len(processed) == 1
    assert len(failed) == 1
    assert json.loads(processed[0].with_suffix(processed[0].suffix + ".receipt.json").read_text())["returncode"] == 0
    assert json.loads(failed[0].with_suffix(failed[0].suffix + ".receipt.json").read_text())["returncode"] == 2
    assert all("--commit" in call and "--init-schema" in call for call in calls)


def test_materialization_queue_respects_per_cycle_limit(tmp_path) -> None:
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    for index in range(3):
        (request_dir / f"{index}.json").write_text(_minimal_valid_request(), encoding="utf-8")

    report = process_replacement_forecast_shadow_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        limit=2,
        runner=lambda argv: _completed(0),
    )

    assert report.status == "PROCESSED"
    assert report.processed_count == 2
    assert report.skipped_count == 1
    assert "REPLACEMENT_SHADOW_MATERIALIZATION_QUEUE_LIMIT_REACHED" in report.reason_codes
    assert len(list(request_dir.glob("*.json"))) == 1


def test_materialization_queue_lock_blocks_parallel_processor(tmp_path) -> None:
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    (request_dir / "a.json").write_text(_minimal_valid_request(), encoding="utf-8")
    # Use the current process PID — the stale-lock self-heal only steals a lock whose holder
    # is dead.  A LIVE pid (this test process) keeps the lock live and must trigger LOCKED.
    import os as _os
    (tmp_path / ".materialization_queue.lock").write_text(f"pid={_os.getpid()} acquired_at=fake", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    report = process_replacement_forecast_shadow_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        runner=lambda argv: calls.append(tuple(argv)) or _completed(0),
    )

    assert report.status == "LOCKED"
    assert report.ok is True
    assert report.reason_codes == ("REPLACEMENT_SHADOW_MATERIALIZATION_QUEUE_LOCKED",)
    assert calls == []
    assert (request_dir / "a.json").exists()


def test_materialization_queue_prepares_seed_before_materializing(tmp_path) -> None:
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    (seed_dir / "seed.json").write_text(json.dumps(_write_seed_inputs(seed_dir)), encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def runner(argv):
        calls.append(tuple(argv))
        input_index = list(argv).index("--input-json") + 1
        request_payload = json.loads(Path(argv[input_index]).read_text(encoding="utf-8"))
        assert request_payload["baseline_source_run_id"] == "b0-run"
        assert request_payload["precision_metadata_json"] == str(seed_dir / "precision_metadata.json")
        return _completed(0, stdout='{"status":"SHADOW_ONLY"}')

    report = process_replacement_forecast_shadow_materialization_queue(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        runner=runner,
    )

    assert report.status == "PROCESSED"
    assert report.seed_processed_count == 1
    assert report.seed_failed_count == 0
    assert report.processed_count == 1
    assert not (seed_dir / "seed.json").exists()
    assert not list((tmp_path / "requests").glob("*.json"))
    assert calls and "--commit" in calls[0]


def test_materialization_queue_skips_seed_that_is_already_covered(tmp_path) -> None:
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    seed = _write_seed_inputs(seed_dir)
    (seed_dir / "seed.json").write_text(json.dumps(seed), encoding="utf-8")
    forecast_db = tmp_path / "forecast.db"
    conn = sqlite3.connect(forecast_db)
    try:
        conn.executescript(
            """
            CREATE TABLE forecast_posteriors (
                source_id TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                dependency_source_run_ids_json TEXT
            );
            CREATE TABLE readiness_state (
                strategy_key TEXT,
                status TEXT DEFAULT 'SHADOW_ONLY',
                provenance_json TEXT,
                dependency_json TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?)",
            (
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                "Shanghai",
                "2026-06-07",
                "high",
                json.dumps({"baseline_b0": "b0-run"}),
            ),
        )
        conn.execute(
            "INSERT INTO readiness_state VALUES (?, ?, ?, ?)",
            (
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                "SHADOW_ONLY",
                json.dumps({"city": "Shanghai", "target_date": "2026-06-07", "temperature_metric": "high"}),
                json.dumps({"dependencies": [{"role": "baseline_b0", "source_run_id": "b0-run"}]}),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    calls: list[tuple[str, ...]] = []

    report = process_replacement_forecast_shadow_materialization_queue(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        forecast_db=forecast_db,
        runner=lambda argv: calls.append(tuple(argv)) or _completed(0),
    )

    assert report.status == "NO_REQUESTS"
    assert report.seed_processed_count == 1
    assert report.seed_failed_count == 0
    assert report.processed_count == 0
    assert calls == []
    moved = next(path for path in (tmp_path / "seed_processed").glob("*.json") if not path.name.endswith(".receipt.json"))
    receipt = json.loads(moved.with_suffix(moved.suffix + ".receipt.json").read_text())
    assert receipt["reason_codes"] == ["REPLACEMENT_MATERIALIZATION_SEED_ALREADY_COVERED"]


def test_materialization_queue_does_not_skip_seed_for_blocked_readiness(tmp_path) -> None:
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    seed = _write_seed_inputs(seed_dir)
    (seed_dir / "seed.json").write_text(json.dumps(seed), encoding="utf-8")
    forecast_db = tmp_path / "forecast.db"
    conn = sqlite3.connect(forecast_db)
    try:
        conn.executescript(
            """
            CREATE TABLE forecast_posteriors (
                source_id TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                dependency_source_run_ids_json TEXT
            );
            CREATE TABLE readiness_state (
                strategy_key TEXT,
                status TEXT,
                provenance_json TEXT,
                dependency_json TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?)",
            (
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                "Shanghai",
                "2026-06-07",
                "high",
                json.dumps({"baseline_b0": "b0-run"}),
            ),
        )
        conn.execute(
            "INSERT INTO readiness_state VALUES (?, ?, ?, ?)",
            (
                "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
                "BLOCKED",
                json.dumps({"city": "Shanghai", "target_date": "2026-06-07", "temperature_metric": "high"}),
                json.dumps({"dependencies": [{"role": "baseline_b0", "source_run_id": "b0-run"}]}),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    calls: list[tuple[str, ...]] = []

    report = process_replacement_forecast_shadow_materialization_queue(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        forecast_db=forecast_db,
        runner=lambda argv: calls.append(tuple(argv)) or _completed(0, stdout='{"status":"SHADOW_ONLY"}'),
    )

    assert report.status == "PROCESSED"
    assert report.seed_processed_count == 1
    assert report.processed_count == 1
    assert calls


def test_materialization_queue_blocks_bad_seed_before_materializer(tmp_path) -> None:
    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    (seed_dir / "seed.json").write_text(json.dumps(_write_seed_inputs(seed_dir, future_dependency=True)), encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    report = process_replacement_forecast_shadow_materialization_queue(
        seed_dir=seed_dir,
        seed_processed_dir=tmp_path / "seed_processed",
        seed_failed_dir=tmp_path / "seed_failed",
        request_dir=tmp_path / "requests",
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        runner=lambda argv: calls.append(tuple(argv)) or _completed(0),
    )

    assert report.status == "NO_REQUESTS"
    assert report.seed_processed_count == 0
    assert report.seed_failed_count == 1
    assert report.processed_count == 0
    assert calls == []
    failed_seed = next(path for path in (tmp_path / "seed_failed").glob("*.json") if not path.name.endswith(".receipt.json"))
    receipt = json.loads(failed_seed.with_suffix(failed_seed.suffix + ".receipt.json").read_text())
    assert receipt["reason_codes"] == ["REPLACEMENT_MATERIALIZATION_REQUEST_HAS_FUTURE_DEPENDENCY"]


def test_main_shadow_materialization_cycle_is_flag_gated(monkeypatch, tmp_path) -> None:
    flags = dict(main_module.settings["feature_flags"])
    flags[SHADOW_FLAG] = False
    monkeypatch.setitem(main_module.settings._data, "feature_flags", flags)
    monkeypatch.setitem(
        main_module.settings._data,
        "replacement_forecast_shadow",
        {
            "request_dir": str(tmp_path / "requests"),
            "processed_dir": str(tmp_path / "processed"),
            "failed_dir": str(tmp_path / "failed"),
            "materialization_limit_per_cycle": 1,
        },
    )
    calls: list[dict[str, object]] = []

    def fake_process(**kwargs):
        calls.append(kwargs)
        raise AssertionError("queue must not run when shadow flag is off")

    monkeypatch.setattr(
        "src.data.replacement_forecast_shadow_materialization_queue.process_replacement_forecast_shadow_materialization_queue",
        fake_process,
    )

    main_module._replacement_forecast_shadow_materialize_cycle.__wrapped__()

    assert calls == []


def test_main_shadow_materialization_cycle_processes_configured_queue_when_enabled(monkeypatch, tmp_path) -> None:
    flags = dict(main_module.settings["feature_flags"])
    flags[SHADOW_FLAG] = True
    monkeypatch.setitem(main_module.settings._data, "feature_flags", flags)
    monkeypatch.setitem(
        main_module.settings._data,
        "replacement_forecast_shadow",
        {
            "request_dir": str(tmp_path / "requests"),
            "processed_dir": str(tmp_path / "processed"),
            "failed_dir": str(tmp_path / "failed"),
            "seed_dir": str(tmp_path / "seeds"),
            "seed_processed_dir": str(tmp_path / "seed_processed"),
            "seed_failed_dir": str(tmp_path / "seed_failed"),
            "forecast_db": str(tmp_path / "forecast.db"),
            "raw_manifest_dir": str(tmp_path / "raw"),
            "seed_discovery_limit_per_cycle": 4,
            "seed_limit_per_cycle": 2,
            "materialization_limit_per_cycle": 3,
        },
    )
    captured: dict[str, object] = {}

    class _Report:
        failed_count = 0
        processed_count = 0

        def as_dict(self):
            return {"status": "NO_REQUESTS"}

    def fake_process(**kwargs):
        captured.update(kwargs)
        return _Report()

    monkeypatch.setattr(
        "src.data.replacement_forecast_shadow_materialization_queue.process_replacement_forecast_shadow_materialization_queue",
        fake_process,
    )

    main_module._replacement_forecast_shadow_materialize_cycle.__wrapped__()

    assert captured["request_dir"] == tmp_path / "requests"
    assert captured["processed_dir"] == tmp_path / "processed"
    assert captured["failed_dir"] == tmp_path / "failed"
    assert captured["seed_dir"] == tmp_path / "seeds"
    assert captured["seed_processed_dir"] == tmp_path / "seed_processed"
    assert captured["seed_failed_dir"] == tmp_path / "seed_failed"
    assert captured["forecast_db"] == tmp_path / "forecast.db"
    assert captured["raw_manifest_dir"] == tmp_path / "raw"
    assert captured["seed_discovery_limit"] == 4
    assert captured["seed_limit"] == 2
    assert captured["limit"] == 3


def test_main_shadow_materialization_cycle_downloads_missing_current_targets(monkeypatch, tmp_path) -> None:
    """Operator directive 2026-06-08/2026-06-11: downloads are a SEPARATE job
    (_replacement_forecast_download_cycle on the download lane in forecast_live_daemon).
    The materialize cycle (_replacement_forecast_shadow_materialize_cycle) only runs
    seed_discovery→seed→materialize on already-downloaded manifests — it NEVER triggers
    a download itself.  This test verifies that the queue is invoked with the correct
    config kwargs including forecast_db and raw_manifest_dir, which proves the cycle
    passes those parameters through even when manifests are missing (the download job
    handles the missing-manifest case independently)."""
    flags = dict(main_module.settings["feature_flags"])
    flags[SHADOW_FLAG] = True
    monkeypatch.setitem(main_module.settings._data, "feature_flags", flags)
    forecast_db = tmp_path / "forecast.db"
    raw_dir = tmp_path / "raw"
    monkeypatch.setitem(
        main_module.settings._data,
        "replacement_forecast_shadow",
        {
            "request_dir": str(tmp_path / "requests"),
            "processed_dir": str(tmp_path / "processed"),
            "failed_dir": str(tmp_path / "failed"),
            "seed_dir": str(tmp_path / "seeds"),
            "seed_processed_dir": str(tmp_path / "seed_processed"),
            "seed_failed_dir": str(tmp_path / "seed_failed"),
            "forecast_db": str(forecast_db),
            "raw_manifest_dir": str(raw_dir),
            "download_current_targets_enabled": True,
            "download_limit_per_cycle": 7,
            "download_release_lag_hours": 11.0,
            "download_anchor_sigma_c": 2.5,
            "download_aifs_retries": 2,
            "materialization_limit_per_cycle": 3,
        },
    )
    captured_queue: dict[str, object] = {}

    class _Report:
        failed_count = 0
        processed_count = 0

        def as_dict(self):
            return {"status": "NO_REQUESTS"}

    def fake_process(**kwargs):
        captured_queue.update(kwargs)
        return _Report()

    monkeypatch.setattr(
        "src.data.replacement_forecast_shadow_materialization_queue.process_replacement_forecast_shadow_materialization_queue",
        fake_process,
    )

    main_module._replacement_forecast_shadow_materialize_cycle.__wrapped__()

    # The materialize cycle must pass through the forecast_db and raw_manifest_dir config
    # so seed_discovery can locate manifests, even when downloads are outstanding.
    assert captured_queue["forecast_db"] == forecast_db
    assert captured_queue["raw_manifest_dir"] == raw_dir
    assert captured_queue["limit"] == 3


def test_main_shadow_materialization_cycle_roots_relative_config_paths(monkeypatch) -> None:
    flags = dict(main_module.settings["feature_flags"])
    flags[SHADOW_FLAG] = True
    monkeypatch.setitem(main_module.settings._data, "feature_flags", flags)
    monkeypatch.setitem(
        main_module.settings._data,
        "replacement_forecast_shadow",
        {
            "request_dir": "state/replacement_forecast_shadow/requests",
            "processed_dir": "state/replacement_forecast_shadow/processed",
            "failed_dir": "state/replacement_forecast_shadow/failed",
            "seed_dir": "state/replacement_forecast_shadow/seeds",
            "seed_processed_dir": "state/replacement_forecast_shadow/seed_processed",
            "seed_failed_dir": "state/replacement_forecast_shadow/seed_failed",
            "forecast_db": "state/zeus-forecasts.db",
            "raw_manifest_dir": "state/replacement_forecast_shadow/raw_manifests",
            "materialization_limit_per_cycle": 3,
        },
    )
    captured: dict[str, object] = {}

    class _Report:
        failed_count = 0
        processed_count = 0

        def as_dict(self):
            return {"status": "NO_REQUESTS"}

    def fake_process(**kwargs):
        captured.update(kwargs)
        return _Report()

    monkeypatch.setattr(
        "src.data.replacement_forecast_shadow_materialization_queue.process_replacement_forecast_shadow_materialization_queue",
        fake_process,
    )

    main_module._replacement_forecast_shadow_materialize_cycle.__wrapped__()

    root = Path(PROJECT_ROOT)
    assert captured["request_dir"] == root / "state/replacement_forecast_shadow/requests"
    assert captured["processed_dir"] == root / "state/replacement_forecast_shadow/processed"
    assert captured["failed_dir"] == root / "state/replacement_forecast_shadow/failed"
    assert captured["seed_dir"] == root / "state/replacement_forecast_shadow/seeds"
    assert captured["seed_processed_dir"] == root / "state/replacement_forecast_shadow/seed_processed"
    assert captured["seed_failed_dir"] == root / "state/replacement_forecast_shadow/seed_failed"
    assert captured["forecast_db"] == root / "state/zeus-forecasts.db"
    assert captured["raw_manifest_dir"] == root / "state/replacement_forecast_shadow/raw_manifests"
    assert captured["limit"] == 3


# ---------------------------------------------------------------------------
# POISON-PILL IMMUNITY relationship tests
# Created: 2026-06-10
# Last reused/audited: 2026-06-10
# Authority basis: materializer queue starvation incident 2026-06-10,
#   report /tmp/materializer_collapse_report.md
#
# RELATIONSHIP under test (cross-module invariant): when new_listing_scout's output
# (a condition_id-only intent stub) flows into the materializer queue's input contract
# (fully-resolved request payload), the queue must NEVER let the malformed file crash-and-
# stay. It is rejected pre-spawn, consumes its slot at most once, and a co-located valid
# request in the same cycle is still processed. This is the antibody that makes the
# "772 stubs starve all production" CATEGORY unconstructable.
# ---------------------------------------------------------------------------


def _scout_stub() -> str:
    """The exact shape new_listing_scout writes — a condition_id intent, NOT a request."""
    return json.dumps(
        {
            "source": "new_listing_scout",
            "condition_id": "0xdeadbeef",
            "enqueued_at": "2026-06-10T02:54:00+00:00",
            "reason": "NEW_LISTING_FAST_LANE",
        }
    )


def test_scout_stub_request_is_failed_pre_spawn_without_crash(tmp_path) -> None:
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    (request_dir / "new_listing_scout_0xdeadbeef.json").write_text(_scout_stub(), encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    report = process_replacement_forecast_shadow_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        runner=lambda argv: calls.append(tuple(argv)) or _completed(0),
    )

    # The materializer subprocess is NEVER spawned for the stub (no KeyError crash path).
    assert calls == []
    # The stub leaves requests/ exactly once, into failed/.
    assert report.failed_count == 1
    assert not list(request_dir.glob("*.json"))
    failed = [p for p in (tmp_path / "failed").glob("*.json") if not p.name.endswith(".receipt.json")]
    assert len(failed) == 1
    receipt = json.loads(failed[0].with_suffix(failed[0].suffix + ".receipt.json").read_text())
    assert receipt["subprocess_spawned"] is False
    assert receipt["returncode"] is None
    assert receipt["reason_codes"] == ["REPLACEMENT_SHADOW_MATERIALIZATION_REQUEST_MISSING_REQUIRED_KEYS"]


def test_scout_stub_does_not_starve_a_valid_request_in_same_cycle(tmp_path) -> None:
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    # Stub sorts BEFORE the valid file by filename; without the antibody the stub would
    # crash and remain, re-failing every cycle and starving the valid seed forever.
    (request_dir / "00_new_listing_scout_0xdeadbeef.json").write_text(_scout_stub(), encoding="utf-8")
    (request_dir / "99_valid.json").write_text(_minimal_valid_request(), encoding="utf-8")
    spawned: list[str] = []

    def runner(argv):
        spawned.append(next(str(p) for p in argv if str(p).endswith(".json")))
        return _completed(0, stdout='{"status":"SHADOW_ONLY"}')

    report = process_replacement_forecast_shadow_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        runner=runner,
    )

    # Exactly one subprocess spawned — for the VALID file, not the stub.
    assert len(spawned) == 1
    assert spawned[0].endswith("99_valid.json")
    assert report.processed_count == 1
    assert report.failed_count == 1
    assert not list(request_dir.glob("*.json"))
    processed = [p for p in (tmp_path / "processed").glob("*.json") if not p.name.endswith(".receipt.json")]
    assert len(processed) == 1 and processed[0].name.startswith("99_valid")


def test_valid_request_still_processed_normally_regression(tmp_path) -> None:
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    (request_dir / "valid.json").write_text(_minimal_valid_request(), encoding="utf-8")
    spawned: list[tuple[str, ...]] = []

    report = process_replacement_forecast_shadow_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        runner=lambda argv: spawned.append(tuple(argv)) or _completed(0, stdout='{"status":"SHADOW_ONLY"}'),
    )

    assert len(spawned) == 1
    assert report.processed_count == 1
    assert report.failed_count == 0
    assert not list(request_dir.glob("*.json"))


# ---------------------------------------------------------------------------
# SCOUT INTENT-STAGING relationship test
# Created: 2026-06-10
# Last reused/audited: 2026-06-10
# Authority basis: materializer queue starvation incident 2026-06-10,
#   report /tmp/materializer_collapse_report.md
#
# RELATIONSHIP under test: new_listing_scout's enqueue write must land in the non-queue
# scout_intents/ staging dir, NEVER in the materializer requests/ dir. requests/ is the
# fully-resolved-payload contract; an intent stub there is poison (see poison-pill tests).
# ---------------------------------------------------------------------------


class _FakeGammaResp:
    status_code = 200

    def __init__(self, events):
        self._events = events

    def json(self):
        return self._events


def test_new_listing_scout_writes_intent_not_request(tmp_path, monkeypatch) -> None:
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    intents_dir = tmp_path / "scout_intents"
    new_cid = "0xnewlisting000000000000000000000000000000000000000000000000000001"

    # Enable the EDLI gate so the scout body runs.
    edli_cfg = dict(main_module._settings_section("edli", {}) or {})
    edli_cfg["enabled"] = True
    monkeypatch.setitem(main_module.settings._data, "edli", edli_cfg)

    # Pre-seed the known-set with a DIFFERENT cid so the DB-init branch is skipped and
    # new_cid is detected as brand-new.
    monkeypatch.setattr(main_module, "_SCOUT_KNOWN_CONDITION_IDS", {"0xexisting"})
    monkeypatch.setattr(main_module, "_NEW_FAMILY_CONDITION_IDS", set())

    monkeypatch.setattr(
        "src.data.market_scanner._gamma_get",
        lambda *a, **k: _FakeGammaResp([{"markets": [{"conditionId": new_cid}]}]),
    )
    # Persist path touches the DB / network — stub it out; not under test here.
    monkeypatch.setattr("src.data.market_scanner.find_weather_markets_or_raise", lambda **k: [])
    monkeypatch.setattr("src.data.market_scanner._persist_market_events_to_db", lambda *a, **k: None)

    # Point the queue config's request_dir at our tmp requests/ so scout_intents/ is its sibling.
    monkeypatch.setattr(
        "src.data.replacement_forecast_production._replacement_forecast_shadow_materialization_queue_config",
        lambda: {"request_dir": request_dir},
    )

    main_module._new_listing_scout_cycle()

    # The intent stub lands in scout_intents/, NEVER in the queue's requests/ dir.
    assert not list(request_dir.glob("*.json")), "scout must NOT write into the materializer requests/ dir"
    staged = list(intents_dir.glob("new_listing_scout_*.json"))
    assert len(staged) == 1
    payload = json.loads(staged[0].read_text())
    assert payload["condition_id"] == new_cid
    assert payload["source"] == "new_listing_scout"
    assert payload["reason"] == "NEW_LISTING_FAST_LANE"
