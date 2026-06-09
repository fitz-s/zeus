# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: 2026-06-09 anchor-lag root cause (/tmp/anchor_lag_report.md, verified against
#   src/data/replacement_forecast_production.py + replacement_forecast_current_target_plan.py):
#   the ALREADY_COVERED / HAVE_RAW_MANIFESTS short-circuits contained NO cycle comparison, so once
#   any cycle fully materialized the download cron could never advance the anchor again —
#   deterministic_forecast_anchors froze at 2026-06-08T18 for ~24h while Open-Meteo served
#   2026-06-09T00 (httpx 200 OK on the U0R leg of the SAME job run).
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
    payload: dict = field(default_factory=lambda: {"status": "CURRENT_TARGETS_COVERED"})

    def as_dict(self) -> dict:
        return dict(self.payload)


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


def _wire(monkeypatch, *, plan: _PlanStub, calls: list):
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.replacement_forecast_current_target_plan as plan_mod

    monkeypatch.setattr(
        plan_mod, "build_replacement_forecast_current_target_plan", lambda _db: plan
    )
    monkeypatch.setattr(
        dl, "_parse_cycle",
        lambda value, *, now, release_lag_hours: AVAILABLE_CYCLE if value is None else value,
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
