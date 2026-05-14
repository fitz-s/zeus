# Created: 2026-05-14
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_data_daemon_live_efficiency/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md
#   Phase 2 forecast-live-daemon ownership and mutual-exclusion contract.
"""Relationship tests for the dedicated forecast-live daemon."""

from __future__ import annotations

from datetime import datetime, timezone

from src.data.dual_run_lock import acquire_lock
from src.data.ecmwf_open_data import OPENDATA_DAEMON_LOCK_KEY


def test_forecast_live_scheduler_only_registers_opendata_forecast_jobs() -> None:
    from src.ingest.forecast_live_daemon import FORECAST_LIVE_JOB_IDS, forecast_live_job_specs

    specs = forecast_live_job_specs(
        startup_run_date=datetime(2026, 5, 14, 8, 0, tzinfo=timezone.utc)
    )
    job_ids = {kwargs["id"] for _, _, kwargs in specs}

    assert job_ids == FORECAST_LIVE_JOB_IDS
    assert not any("tigge" in job_id for job_id in job_ids)
    assert not any("calibrat" in job_id or "refit" in job_id for job_id in job_ids)
    assert not any("market" in job_id or "venue" in job_id for job_id in job_ids)


def test_forecast_live_track_runner_uses_shared_opendata_lock(tmp_path) -> None:
    from src.data import dual_run_lock
    from src.ingest.forecast_live_daemon import run_opendata_track

    assert OPENDATA_DAEMON_LOCK_KEY in dual_run_lock._KNOWN_TABLES

    def collector(*, track: str) -> dict:
        raise AssertionError(f"collector must not run while {OPENDATA_DAEMON_LOCK_KEY} lock is held")

    with acquire_lock(OPENDATA_DAEMON_LOCK_KEY, _locks_dir_override=tmp_path) as acquired:
        assert acquired
        result = run_opendata_track(
            "mx2t6_high",
            _locks_dir_override=tmp_path,
            _collector=collector,
            _source_paused=lambda source_id: False,
        )

    assert result == {
        "status": "skipped_lock_held",
        "source": "ecmwf_open_data",
        "track": "mx2t6_high",
    }


def test_legacy_ingest_opendata_runner_uses_same_shared_lock(tmp_path, monkeypatch) -> None:
    from src import ingest_main

    def collector(*, track: str) -> dict:
        raise AssertionError(f"legacy collector must not run while {OPENDATA_DAEMON_LOCK_KEY} lock is held")

    monkeypatch.setattr(ingest_main, "_is_source_paused", lambda source_id: False)
    with acquire_lock(OPENDATA_DAEMON_LOCK_KEY, _locks_dir_override=tmp_path) as acquired:
        assert acquired
        result = ingest_main._run_opendata_track(
            "mn2t6_low",
            _locks_dir_override=tmp_path,
            _collector=collector,
        )

    assert result == {
        "status": "skipped_lock_held",
        "source": "ecmwf_open_data",
        "track": "mn2t6_low",
    }


def test_forecast_live_track_runner_calls_collector_once_under_lock(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track

    calls: list[str] = []

    def collector(*, track: str) -> dict:
        calls.append(track)
        return {"status": "ok", "track": track}

    result = run_opendata_track(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
    )

    assert result == {"status": "ok", "track": "mx2t6_high"}
    assert calls == ["mx2t6_high"]
