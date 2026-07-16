# Created: 2026-06-09
# Last reused or audited: 2026-07-13
# Authority basis: 2026-06-09 anchor-lag root cause (/tmp/anchor_lag_report.md, verified against
#   src/data/replacement_forecast_production.py + replacement_forecast_current_target_plan.py):
#   the ALREADY_COVERED / HAVE_RAW_MANIFESTS short-circuits contained NO cycle comparison, so once
#   any cycle fully materialized the download cron could never advance the anchor again —
#   deterministic_forecast_anchors froze at 2026-06-08T18 for ~24h while Open-Meteo served
#   2026-06-09T00 (httpx 200 OK on the BAYES_PRECISION_FUSION leg of the SAME job run).
"""RELATIONSHIP antibody: current-target COVERAGE never implies CYCLE CURRENCY.

Cross-module invariant (plan coverage -> download gate boundary):
  plan.ready means "a posterior exists for every current target". It says NOTHING about which
  IFS cycle that posterior was built from. The download gate may skip ONLY when the
  currently-available cycle's raw inputs (BOTH ecmwf_aifs_ens AND openmeteo_ecmwf_ifs_9km
  artifacts) are already downloaded. available_cycle > downloaded high-water mark => the
  download MUST fire regardless of posterior coverage.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.data.replacement_forecast_production import (
    _download_replacement_forecast_current_targets_if_needed,
)

AVAILABLE_CYCLE = datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)
STALE_CYCLE_ISO = "2026-06-08T18:00:00+00:00"
CURRENT_CYCLE_ISO = "2026-06-09T00:00:00+00:00"

_ARTIFACTS_DDL = """
CREATE TABLE raw_forecast_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    data_version TEXT NOT NULL,
    source_cycle_time TEXT NOT NULL,
    source_available_at TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    request_url TEXT,
    request_params_json TEXT NOT NULL DEFAULT '{}',
    artifact_metadata_json TEXT NOT NULL DEFAULT '{}',
    trade_authority_status TEXT NOT NULL DEFAULT 'SHADOW_ONLY',
    training_allowed INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@dataclass
class _PlanStub:
    ready: bool = True
    missing_aifs_manifest_count: int = 0
    missing_openmeteo_manifest_count: int = 0
    rows: tuple = ()
    payload: dict = field(default_factory=lambda: {"status": "CURRENT_TARGETS_COVERED"})

    def as_dict(self) -> dict:
        return dict(self.payload)


@dataclass(frozen=True)
class _TargetRow:
    city: str
    target_date: str
    temperature_metric: str
    covered: bool
    missing_openmeteo_manifest: bool


def _make_db(tmp_path: Path, cycles_by_source: dict[str, str]) -> Path:
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    conn.execute(_ARTIFACTS_DDL)
    for sid, cyc in cycles_by_source.items():
        conn.execute(
            "INSERT INTO raw_forecast_artifacts (source_id, product_id, data_version,"
            " source_cycle_time, source_available_at, captured_at, artifact_path, sha256,"
            " byte_size) VALUES (?, 'p', 'v1', ?, ?, ?, '/tmp/x', 'h', 1)",
            (sid, cyc, cyc, cyc),
        )
    conn.commit()
    conn.close()
    return db


def test_anchor_ladder_skips_single_runs_when_source_clock_says_not_public(monkeypatch) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import build_anchor_request

    request = build_anchor_request(
        latitude=33.63,
        longitude=-84.44,
        run="2026-06-25T12:00:00+00:00",
        timezone_name="UTC",
    )

    monkeypatch.setattr(dl, "_single_runs_public_for_request", lambda _request: False)

    def _single_runs_should_not_be_called(*_args, **_kwargs):
        raise AssertionError("single-runs fetch should be skipped before publication")

    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        _single_runs_should_not_be_called,
    )

    def _meta_refuses(*_args, **_kwargs):
        raise ValueError("provider declares an older run")

    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        _meta_refuses,
    )
    monkeypatch.setattr(
        dl,
        "_try_bucket_rung_three",
        lambda **_kwargs: (
            {"hourly": {"time": [], "temperature_2m": []}},
            {"run_authority": "bucket_partial_run_test"},
        ),
    )

    payload, provenance = dl._resolve_anchor_payload(
        request=request,
        city="Atlanta",
        target_date="2026-06-25",
        timezone_name="UTC",
    )

    assert payload == {"hourly": {"time": [], "temperature_2m": []}}
    assert provenance["run_authority"] == "bucket_partial_run_test"


def _wire(monkeypatch, *, plan: _PlanStub, calls: list):
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.replacement_forecast_current_target_plan as plan_mod

    def _plan_builder(db, *args, **kwargs):
        required_cycle = kwargs.get("required_openmeteo_source_cycle_time")
        if required_cycle is None:
            return plan
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts "
                "WHERE source_id = 'openmeteo_ecmwf_ifs_9km'"
            ).fetchone()
        finally:
            conn.close()
        max_cycle = None if row is None else row[0]
        required_iso = required_cycle.isoformat() if hasattr(required_cycle, "isoformat") else str(required_cycle)
        if max_cycle is None or str(max_cycle) < required_iso:
            return _PlanStub(
                ready=False,
                missing_openmeteo_manifest_count=1,
                payload={"status": "CURRENT_TARGETS_MISSING_CURRENT_CYCLE_MANIFESTS"},
            )
        return plan

    monkeypatch.setattr(
        plan_mod,
        "build_replacement_forecast_current_target_plan",
        _plan_builder,
    )
    # Run-selection single authority (2026-06-11): the production job resolves the
    # available cycle via provider probes, never the dead now-minus-lag guess.
    import src.data.replacement_forecast_production as production

    monkeypatch.setattr(
        production, "_probe_resolved_available_cycle", lambda: AVAILABLE_CYCLE
    )

    def _fake_download(**kwargs):
        calls.append(kwargs)
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
            "cycle": kwargs["cycle"].isoformat(),
        }

    monkeypatch.setattr(dl, "download_current_target_raw_inputs", _fake_download)


def _cfg(db: Path, tmp_path: Path) -> dict:
    return {
        "download_current_targets_enabled": True,
        "forecast_db": db,
        "download_output_dir": tmp_path / "manifests",
        "download_release_lag_hours": 14.0,
        "download_limit": 10,
        "download_anchor_sigma_c": 3.0,
        "download_aifs_retries": 1,
    }


def test_ready_plan_with_stale_artifacts_still_downloads_new_cycle(tmp_path, monkeypatch) -> None:
    # THE 2026-06-09 incident shape: full posterior coverage + artifacts one cycle behind.
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report is not None
    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED", (
        "plan.ready (posterior coverage) must NOT suppress the download of a newer available "
        "cycle — this is the gate that froze deterministic_forecast_anchors at 06-08T18"
    )
    assert len(calls) == 1
    assert calls[0]["cycle"] == AVAILABLE_CYCLE


def test_ready_plan_with_current_artifacts_skips_without_download(tmp_path, monkeypatch) -> None:
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": CURRENT_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": CURRENT_CYCLE_ISO,
    })
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report is not None
    assert report["status"] == "CURRENT_TARGETS_ALREADY_COVERED"
    assert calls == []
    # The skip must be self-explaining (anti-silent-skip class): cycle facts in the report.
    assert report["available_cycle"] == AVAILABLE_CYCLE.isoformat()
    assert report["downloaded_cycle"] == CURRENT_CYCLE_ISO


def test_one_source_lagging_fires_download(tmp_path, monkeypatch) -> None:
    # AIFS current but the OpenMeteo ifs9 anchor lagging: the high-water mark is the MIN over
    # BOTH sources — a half-downloaded cycle is NOT current.
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": CURRENT_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1


def test_no_artifacts_at_all_fires_download(tmp_path, monkeypatch) -> None:
    db = _make_db(tmp_path, {})
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1


def test_have_raw_manifests_gate_is_also_cycle_aware(tmp_path, monkeypatch) -> None:
    # plan NOT ready but zero missing manifests (the second short-circuit) + stale artifacts:
    # the download must still fire — BOTH early returns carry the cycle-currency requirement.
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    plan = _PlanStub(ready=False, missing_aifs_manifest_count=0, missing_openmeteo_manifest_count=0)
    _wire(monkeypatch, plan=plan, calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1


def test_partial_current_cycle_manifests_do_not_skip_download(tmp_path, monkeypatch) -> None:
    # Live 2026-06-24 shape: the artifact high-water mark reached the available
    # cycle because a few targets wrote 12Z manifests, while most current targets
    # still only had older-cycle manifests. The skip gate must ask the plan for
    # current-cycle coverage, not use a non-cycle-aware manifest count.
    db = _make_db(tmp_path, {"openmeteo_ecmwf_ifs_9km": CURRENT_CYCLE_ISO})
    calls: list = []
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.replacement_forecast_current_target_plan as plan_mod
    import src.data.replacement_forecast_production as production

    stale_non_cycle_plan = _PlanStub(
        ready=False,
        missing_openmeteo_manifest_count=0,
        payload={"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS_STALE"},
    )
    current_cycle_plan = _PlanStub(
        ready=False,
        missing_openmeteo_manifest_count=1,
        payload={"status": "CURRENT_TARGETS_MISSING_CURRENT_CYCLE_MANIFESTS"},
    )

    def _plan_builder(_db, *args, **kwargs):
        if kwargs.get("required_openmeteo_source_cycle_time") is not None:
            return current_cycle_plan
        return stale_non_cycle_plan

    monkeypatch.setattr(plan_mod, "build_replacement_forecast_current_target_plan", _plan_builder)
    monkeypatch.setattr(production, "_probe_resolved_available_cycle", lambda: AVAILABLE_CYCLE)

    def _fake_download(**kwargs):
        calls.append(kwargs)
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
            "cycle": kwargs["cycle"].isoformat(),
        }

    monkeypatch.setattr(dl, "download_current_target_raw_inputs", _fake_download)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))

    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1
    assert calls[0].get("include_covered") is True


def test_direct_current_target_downloader_scopes_plan_to_requested_cycle(tmp_path, monkeypatch) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    seen: list[dict] = []

    def _plan_builder(_db, *args, **kwargs):
        seen.append(dict(kwargs))
        return _PlanStub(ready=False, rows=())

    monkeypatch.setattr(dl, "build_replacement_forecast_current_target_plan", _plan_builder)

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
    )

    assert report["target_count"] == 0
    assert seen[0]["required_openmeteo_source_cycle_time"] == AVAILABLE_CYCLE


def test_direct_current_target_downloader_prioritizes_missing_cycle_manifest_before_limit(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = (
        _TargetRow(
            city="Amsterdam",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=True,
            missing_openmeteo_manifest=False,
        ),
        _TargetRow(
            city="London",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        ),
    )
    monkeypatch.setattr(
        dl,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: _PlanStub(ready=False, rows=rows),
    )
    monkeypatch.setattr(
        dl,
        "_resolve_anchor_payload",
        lambda **_kwargs: (
            {"hourly": {"time": [], "temperature_2m": []}},
            {"openmeteo_endpoint": "single_runs_api", "run_authority": "test"},
        ),
    )

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=1,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
    )

    assert report["target_count"] == 1
    assert report["manifest_count"] == 1
    assert "London" in report["written_manifests"][0]


def test_direct_downloader_reuses_plan_and_city_date_payload_across_metrics(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = (
        _TargetRow(
            city="London",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        ),
        _TargetRow(
            city="London",
            target_date="2026-06-10",
            temperature_metric="low",
            covered=False,
            missing_openmeteo_manifest=True,
        ),
    )
    plan = _PlanStub(ready=False, rows=rows)
    monkeypatch.setattr(
        dl,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("precomputed plan must be reused")
        ),
    )
    calls: list[dict[str, object]] = []

    def _resolve(**kwargs):
        calls.append(kwargs)
        return (
            {"hourly": {"time": [], "temperature_2m": []}},
            {"openmeteo_endpoint": "bucket", "run_authority": "bucket_partial_run_test"},
        )

    monkeypatch.setattr(dl, "_resolve_anchor_payload", _resolve)

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=plan,
        max_wall_clock_seconds=5.0,
    )

    assert report["manifest_count"] == 2
    assert report["downloaded"]["openmeteo_transport_fetch_count"] == 1
    assert len(calls) == 1


def test_disabled_flag_still_short_circuits(tmp_path, monkeypatch) -> None:
    db = _make_db(tmp_path, {})
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    cfg = _cfg(db, tmp_path)
    cfg["download_current_targets_enabled"] = False
    assert _download_replacement_forecast_current_targets_if_needed(cfg) is None
    assert calls == []


def test_stale_cycle_download_includes_covered_targets(tmp_path, monkeypatch) -> None:
    # K-ROOT INSTANCE #3 (2026-06-09): the downloader filtered its target list to
    # NOT-covered rows, so a fully-covered window never received the NEW cycle's raw
    # inputs — re-materialization at the fresh cycle could only bind OLD manifests
    # (observed live: 06-11 targets re-pinned to 06-08T18 manifests). When the download
    # fires because the cycle is stale, it must pass include_covered=True.
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert len(calls) == 1
    assert calls[0].get("include_covered") is True, (
        "a stale-cycle download must fetch raw inputs for ALL current targets — filtering "
        "to uncovered rows self-perpetuates staleness (coverage never implies currency)"
    )


def test_existing_corrupt_openmeteo_payload_is_not_reused(tmp_path: Path) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    payload = tmp_path / "openmeteo_Buenos_Aires_2026-06-24_high_20260624T000000Z.json"
    payload.write_text('{"hourly": {}}\n}\n', encoding="utf-8")

    assert dl._json_file_valid(payload) is False

    dl._write_json(payload, {"hourly": {"time": [], "temperature_2m": []}})

    assert dl._json_file_valid(payload) is True


def test_concurrent_payload_publishers_use_distinct_temp_files(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import scripts.download_replacement_forecast_current_targets as dl

    target = tmp_path / "openmeteo_Seoul_2026-07-14_high.json"
    payloads = ({"writer": 1}, {"writer": 2})
    barrier = threading.Barrier(2)
    real_replace = os.replace

    def synchronized_replace(source, destination):
        barrier.wait(timeout=5)
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dl._write_json, target, payload) for payload in payloads]
        for future in futures:
            future.result(timeout=10)

    assert json.loads(target.read_text(encoding="utf-8")) in payloads
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []
