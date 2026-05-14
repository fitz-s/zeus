# Created: 2026-05-14
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md Phase 8 staged end-to-end verification.
"""Relationship tests for the forecast-live readiness verifier."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts.check_forecast_live_ready import CompletionState, evaluate_forecast_live_ready
from scripts import check_forecast_live_ready
from scripts.check_forecast_live_e2e import run_smoke
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.db import init_schema_forecasts
from src.state.job_run_repo import write_job_run
from src.state.readiness_repo import write_readiness_state
from src.state.source_run_coverage_repo import write_source_run_coverage
from src.state.source_run_repo import write_source_run

UTC = timezone.utc
NOW = datetime(2026, 5, 14, 9, 30, tzinfo=UTC)

TRACKS = {
    "HIGH": {
        "job_name": "forecast_live_opendata_mx2t6_high",
        "job_track": "mx2t6_high",
        "source_run_track": "mx2t6_high_full_horizon",
        "temperature_metric": "high",
        "physical_quantity": "mx2t3_local_calendar_day_max",
        "observation_field": "high_temp",
        "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
    },
    "LOW": {
        "job_name": "forecast_live_opendata_mn2t6_low",
        "job_track": "mn2t6_low",
        "source_run_track": "mn2t6_low_full_horizon",
        "temperature_metric": "low",
        "physical_quantity": "mn2t3_local_calendar_day_min",
        "observation_field": "low_temp",
        "data_version": ECMWF_OPENDATA_LOW_DATA_VERSION,
    },
}


def _source_run_id(label: str) -> str:
    return f"source-run-{label.lower()}-20260514-00z"


def _write_source_health(tmp_path: Path, *, error: str | None = None) -> Path:
    payload = {
        "written_at": NOW.isoformat(),
        "sources": {
            "ecmwf_open_data": {
                "last_success_at": None if error else NOW.isoformat(),
                "last_failure_at": NOW.isoformat() if error else None,
                "consecutive_failures": 1 if error else 0,
                "degraded_since": NOW.isoformat() if error else None,
                "latency_ms": 42,
                "error": error,
            }
        },
    }
    path = tmp_path / "source_health.json"
    path.write_text(json.dumps(payload))
    return path


def _write_track(
    conn: sqlite3.Connection,
    label: str,
    *,
    source_status: str = "SUCCESS",
    completeness_status: str = "COMPLETE",
    partial_run: bool = False,
    readiness_dependency_source_run_id: str | None = None,
    job_source_run_id: str | None = None,
) -> None:
    cfg = TRACKS[label]
    source_run_id = _source_run_id(label)
    release_key = f"ecmwf_open_data:{cfg['job_track']}:full"
    write_job_run(
        conn,
        job_run_id=f"job-{label.lower()}",
        job_name=cfg["job_name"],
        plane="forecast",
        scheduled_for=NOW.replace(minute=0),
        status="SUCCESS" if source_status == "SUCCESS" else "PARTIAL",
        source_id="ecmwf_open_data",
        track=cfg["job_track"],
        release_calendar_key=release_key,
        source_run_id=job_source_run_id or source_run_id,
    )
    write_source_run(
        conn,
        source_run_id=source_run_id,
        source_id="ecmwf_open_data",
        track=cfg["source_run_track"],
        release_calendar_key=release_key,
        source_cycle_time=NOW.replace(hour=0, minute=0),
        status=source_status,
        completeness_status=completeness_status,
        partial_run=partial_run,
        expected_members=51,
        observed_members=51 if completeness_status == "COMPLETE" else 37,
        expected_steps_json=[126, 132, 138, 144],
        observed_steps_json=[126, 132, 138, 144] if completeness_status == "COMPLETE" else [126, 132],
    )
    write_source_run_coverage(
        conn,
        coverage_id=f"coverage-{label.lower()}",
        source_run_id=source_run_id,
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        release_calendar_key=release_key,
        track=cfg["source_run_track"],
        city_id="LONDON",
        city="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 15),
        temperature_metric=cfg["temperature_metric"],
        physical_quantity=cfg["physical_quantity"],
        observation_field=cfg["observation_field"],
        data_version=cfg["data_version"],
        expected_members=51,
        observed_members=51,
        expected_steps_json=[126, 132, 138, 144],
        observed_steps_json=[126, 132, 138, 144],
        snapshot_ids_json=[1, 2, 3],
        target_window_start_utc=NOW + timedelta(hours=14),
        target_window_end_utc=NOW + timedelta(hours=38),
        completeness_status="COMPLETE",
        readiness_status="LIVE_ELIGIBLE",
        computed_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    dependency_source_run_id = readiness_dependency_source_run_id
    if dependency_source_run_id is None:
        dependency_source_run_id = source_run_id
    write_readiness_state(
        conn,
        readiness_id=f"readiness-{label.lower()}",
        scope_type="city_metric",
        status="LIVE_ELIGIBLE",
        computed_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        city_id="LONDON",
        city="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 15),
        temperature_metric=cfg["temperature_metric"],
        physical_quantity=cfg["physical_quantity"],
        observation_field=cfg["observation_field"],
        data_version=cfg["data_version"],
        source_id="ecmwf_open_data",
        track=cfg["source_run_track"],
        source_run_id=source_run_id,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        reason_codes_json=["PRODUCER_COVERAGE_READY"],
        dependency_json={"source_run_id": dependency_source_run_id},
        provenance_json={"source_run_id": source_run_id},
    )


def _forecast_db(tmp_path: Path, **track_overrides: object) -> Path:
    path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    for label in ("HIGH", "LOW"):
        overrides = track_overrides.get(label, {})
        assert isinstance(overrides, dict)
        _write_track(conn, label, **overrides)
    conn.commit()
    conn.close()
    return path


def _report(db_path: Path, source_health_path: Path):
    return evaluate_forecast_live_ready(
        forecasts_db_path=db_path,
        source_health_path=source_health_path,
        now_utc=NOW,
        claim_mode="staged",
        require_process=False,
        require_heartbeat=False,
    )


def test_staged_complete_chain_reports_producer_ready(tmp_path: Path) -> None:
    report = _report(_forecast_db(tmp_path), _write_source_health(tmp_path))

    assert report.highest_completion_state == CompletionState.PRODUCER_READY.value
    assert report.producer_ready is True
    assert report.runtime_ready is False
    assert report.blockers == []
    assert any(check.name == "runtime_evidence" and check.status == "SKIPPED" for check in report.checks)
    assert {track.label for track in report.tracks if track.ready} == {"HIGH", "LOW"}


def test_partial_source_run_blocks_producer_ready(tmp_path: Path) -> None:
    report = _report(
        _forecast_db(
            tmp_path,
            HIGH={
                "source_status": "PARTIAL",
                "completeness_status": "PARTIAL",
                "partial_run": True,
            },
        ),
        _write_source_health(tmp_path),
    )

    assert report.highest_completion_state == CompletionState.CODE_READY_ON_HEAD.value
    assert report.producer_ready is False
    assert "HIGH_SOURCE_RUN_NOT_SUCCESS:PARTIAL" in report.blockers
    assert "HIGH_SOURCE_RUN_NOT_COMPLETE:PARTIAL" in report.blockers


def test_http_429_source_health_blocks_producer_ready(tmp_path: Path) -> None:
    report = _report(
        _forecast_db(tmp_path),
        _write_source_health(tmp_path, error="THROTTLED HTTP 429"),
    )

    assert report.highest_completion_state == CompletionState.CODE_READY_ON_HEAD.value
    assert report.producer_ready is False
    assert "SOURCE_HEALTH_THROTTLED_HTTP_429" in report.blockers


def test_missing_readiness_dependency_blocks_producer_ready(tmp_path: Path) -> None:
    report = _report(
        _forecast_db(tmp_path, HIGH={"readiness_dependency_source_run_id": "missing-source-run"}),
        _write_source_health(tmp_path),
    )

    assert report.highest_completion_state == CompletionState.CODE_READY_ON_HEAD.value
    assert report.producer_ready is False
    assert "HIGH_READINESS_DEPENDENCY_MISMATCH" in report.blockers


def test_job_run_source_run_mismatch_blocks_producer_ready(tmp_path: Path) -> None:
    report = _report(
        _forecast_db(tmp_path, HIGH={"job_source_run_id": "stale-source-run"}),
        _write_source_health(tmp_path),
    )

    assert report.highest_completion_state == CompletionState.CODE_READY_ON_HEAD.value
    assert report.producer_ready is False
    assert "HIGH_JOB_RUN_SOURCE_RUN_MISMATCH" in report.blockers


def test_no_runtime_required_is_rejected_for_post_launch_claim() -> None:
    assert check_forecast_live_ready.main(["--no-runtime-required"]) == 2


def test_forecast_live_heartbeat_satisfies_runtime_evidence(tmp_path: Path) -> None:
    from src.ingest.forecast_live_daemon import _write_forecast_live_heartbeat

    heartbeat_path = tmp_path / "forecast-live-heartbeat.json"
    _write_forecast_live_heartbeat(
        heartbeat_path=heartbeat_path,
        status="scheduler_ready",
        now_utc=NOW,
    )

    report = evaluate_forecast_live_ready(
        forecasts_db_path=_forecast_db(tmp_path),
        source_health_path=_write_source_health(tmp_path),
        now_utc=NOW,
        claim_mode="post-launch",
        require_process=False,
        require_heartbeat=True,
        heartbeat_path=heartbeat_path,
    )

    assert report.producer_ready is True
    assert report.runtime_ready is True
    assert report.highest_completion_state == CompletionState.PRODUCER_READY.value
    assert report.blockers == []


def test_temp_only_smoke_proves_daemon_to_live_reader_chain(tmp_path: Path) -> None:
    report = run_smoke(work_dir=tmp_path / "forecast-live-smoke", keep_artifacts=True)

    assert report.status == "PASS"
    assert report.external_fetch is False
    assert report.production_db_write is False
    assert report.verifier["highest_completion_state"] == CompletionState.PRODUCER_READY.value
    assert report.verifier["producer_ready"] is True
    assert {track.label for track in report.tracks if track.reader_ok} == {"HIGH", "LOW"}
    assert all(track.n_members == 51 for track in report.tracks)
    assert all(track.members_match for track in report.tracks)
    assert all(track.members_max_abs_error == 0.0 for track in report.tracks)
    assert report.timings_seconds["total"] > 0
