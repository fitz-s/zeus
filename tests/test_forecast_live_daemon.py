# Created: 2026-05-14
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md section 6.1, section 6.2, section 8 Phase 4, and Phase 6 durable work journaling.
"""Relationship tests for the dedicated forecast-live daemon boundary."""

from __future__ import annotations

import ast
import sqlite3
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.data.dual_run_lock import OPENDATA_DAEMON_LOCK_KEY, acquire_lock
from src.state.db import init_schema_forecasts


REPO_ROOT = Path(__file__).resolve().parents[1]
FORECAST_LIVE_DAEMON = REPO_ROOT / "src" / "ingest" / "forecast_live_daemon.py"


class _BlockingSchedulerStub:
    pass


class _ThreadPoolExecutorStub:
    pass


_apscheduler = types.ModuleType("apscheduler")
_apscheduler_schedulers = types.ModuleType("apscheduler.schedulers")
_apscheduler_schedulers_blocking = types.ModuleType("apscheduler.schedulers.blocking")
_apscheduler_executors = types.ModuleType("apscheduler.executors")
_apscheduler_executors_pool = types.ModuleType("apscheduler.executors.pool")
_apscheduler_schedulers_blocking.BlockingScheduler = _BlockingSchedulerStub
_apscheduler_executors_pool.ThreadPoolExecutor = _ThreadPoolExecutorStub
sys.modules.setdefault("apscheduler", _apscheduler)
sys.modules.setdefault("apscheduler.schedulers", _apscheduler_schedulers)
sys.modules.setdefault("apscheduler.schedulers.blocking", _apscheduler_schedulers_blocking)
sys.modules.setdefault("apscheduler.executors", _apscheduler_executors)
sys.modules.setdefault("apscheduler.executors.pool", _apscheduler_executors_pool)


def test_forecast_live_scheduler_only_registers_opendata_jobs() -> None:
    from src.ingest.forecast_live_daemon import FORECAST_LIVE_JOB_IDS, forecast_live_job_specs

    specs = forecast_live_job_specs(
        startup_run_date=datetime(2026, 5, 14, 8, 0, tzinfo=timezone.utc)
    )
    job_ids = {kwargs["id"] for _, _, kwargs in specs}

    assert job_ids == FORECAST_LIVE_JOB_IDS
    assert job_ids == {
        "forecast_live_opendata_daily_mx2t6",
        "forecast_live_opendata_daily_mn2t6",
        "forecast_live_opendata_startup_catch_up",
    }
    assert not any("tigge" in job_id for job_id in job_ids)
    assert not any("calibrat" in job_id or "refit" in job_id for job_id in job_ids)
    assert not any("market" in job_id or "venue" in job_id for job_id in job_ids)


def test_forecast_live_track_runner_uses_shared_opendata_lock(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track

    def collector(*, track: str) -> dict:
        raise AssertionError(f"collector must not run while {OPENDATA_DAEMON_LOCK_KEY} is held")

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
    import src.ingest_main as ingest_main

    def collector(*, track: str) -> dict:
        raise AssertionError(f"legacy collector must not run while {OPENDATA_DAEMON_LOCK_KEY} is held")

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


def test_forecast_live_track_runner_journals_success_in_forecasts_job_run(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str) -> dict:
        return {
            "status": "ok",
            "track": track,
            "source_run_id": "ecmwf_open_data:mx2t6_high:2026-05-14T00Z",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "forecast_track": "mx2t6_high_full_horizon",
            "snapshots_inserted": 7,
            "coverage_written": 1,
            "producer_readiness_written": 1,
        }

    result = run_opendata_track(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _job_conn=conn,
        _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "SUCCESS"
    assert row["scheduled_for"] == "2026-05-14T00:00:00+00:00"
    assert row["source_id"] == "ecmwf_open_data"
    assert row["track"] == "mx2t6_high"
    assert row["source_run_id"] == "ecmwf_open_data:mx2t6_high:2026-05-14T00Z"
    assert row["release_calendar_key"] == "ecmwf_open_data:mx2t6_high:full"
    assert row["rows_written"] == 7


def test_forecast_live_track_runner_upserts_same_source_cycle_job_run(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str) -> dict:
        return {
            "status": "ok",
            "track": track,
            "source_run_id": "source-run-repeat",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "snapshots_inserted": 1,
        }

    for _ in range(2):
        run_opendata_track(
            "mx2t6_high",
            _locks_dir_override=tmp_path,
            _collector=collector,
            _source_paused=lambda source_id: False,
            _job_conn=conn,
            _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
        )

    rows = conn.execute(
        "SELECT * FROM job_run WHERE job_name = ?",
        ("forecast_live_opendata_mx2t6_high",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "SUCCESS"


def test_forecast_live_track_runner_journals_partial_result(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str) -> dict:
        return {
            "status": "ok",
            "source_run_status": "PARTIAL",
            "source_run_completeness": "PARTIAL",
            "reason_code": "coverage_short",
            "track": track,
            "snapshots_inserted": 3,
        }

    result = run_opendata_track(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _job_conn=conn,
        _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "PARTIAL"
    assert row["reason_code"] == "coverage_short"
    assert row["rows_written"] == 3
    assert row["rows_failed"] == 0


def test_forecast_live_track_runner_journals_lock_held(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str) -> dict:
        raise AssertionError("collector must not run when lock is held")

    with acquire_lock(OPENDATA_DAEMON_LOCK_KEY, _locks_dir_override=tmp_path) as acquired:
        assert acquired
        result = run_opendata_track(
            "mx2t6_high",
            _locks_dir_override=tmp_path,
            _collector=collector,
            _source_paused=lambda source_id: False,
            _job_conn=conn,
            _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
        )

    assert result["status"] == "skipped_lock_held"
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "SKIPPED_LOCK_HELD"
    assert row["reason_code"] == "SKIPPED_LOCK_HELD"


def test_forecast_live_track_runner_journals_collector_exception(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str) -> dict:
        raise RuntimeError(f"failed {track}")

    with pytest.raises(RuntimeError, match="failed mx2t6_high"):
        run_opendata_track(
            "mx2t6_high",
            _locks_dir_override=tmp_path,
            _collector=collector,
            _source_paused=lambda source_id: False,
            _job_conn=conn,
            _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
        )

    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["reason_code"] == "EXCEPTION:RuntimeError:failed mx2t6_high"
    assert row["rows_failed"] == 1


def test_journaled_wrapper_commits_failed_job_before_reraising(tmp_path, monkeypatch) -> None:
    from src.data.release_calendar import FetchDecision
    import src.data.ecmwf_open_data as ecmwf_open_data
    import src.ingest.forecast_live_daemon as forecast_live_daemon
    import src.state.db as state_db
    from src.state.job_run_repo import get_latest_job_run

    db_path = tmp_path / "forecasts.db"
    setup_conn = sqlite3.connect(db_path)
    setup_conn.row_factory = sqlite3.Row
    init_schema_forecasts(setup_conn)
    setup_conn.commit()
    setup_conn.close()

    def get_test_conn(*, write_class: str):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(state_db, "get_forecasts_connection", get_test_conn)
    monkeypatch.setattr(
        forecast_live_daemon,
        "_forecast_work_identity",
        lambda track, now_utc: {
            "decision": FetchDecision.FETCH_ALLOWED,
            "metadata": {"selected_cycle_time": datetime(2026, 5, 14, 0, tzinfo=timezone.utc)},
            "job_name": "forecast_live_opendata_mx2t6_high",
            "source_id": "ecmwf_open_data",
            "track": track,
            "scheduled_for": datetime(2026, 5, 14, 0, tzinfo=timezone.utc),
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "safe_fetch_not_before": None,
        },
    )

    def collector(*, track: str) -> dict:
        raise RuntimeError(f"failed {track}")

    monkeypatch.setattr(ecmwf_open_data, "collect_open_ens_cycle", collector)

    with pytest.raises(RuntimeError, match="failed mx2t6_high"):
        forecast_live_daemon._run_journaled_opendata_track("mx2t6_high")

    verify_conn = sqlite3.connect(db_path)
    verify_conn.row_factory = sqlite3.Row
    row = get_latest_job_run(verify_conn, "forecast_live_opendata_mx2t6_high")
    verify_conn.close()

    assert row is not None
    assert row["status"] == "FAILED"
    assert row["reason_code"] == "EXCEPTION:RuntimeError:failed mx2t6_high"


def test_forecast_live_track_runner_journals_not_released_without_collector(tmp_path, monkeypatch) -> None:
    from src.data.release_calendar import FetchDecision
    import src.data.release_calendar as release_calendar
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    selected_cycle = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    safe_fetch = datetime(2026, 5, 14, 8, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(
        release_calendar,
        "select_source_run_for_target_horizon",
        lambda **kwargs: (
            FetchDecision.SKIPPED_NOT_RELEASED,
            {
                "selected_cycle_time": selected_cycle,
                "next_safe_fetch_at": safe_fetch,
            },
        ),
    )

    def collector(*, track: str) -> dict:
        raise AssertionError("collector must not run before safe fetch")

    result = run_opendata_track(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _job_conn=conn,
        _now_utc=datetime(2026, 5, 14, 7, 30, tzinfo=timezone.utc),
    )

    assert result["status"] == "skipped_not_released"
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "SKIPPED_NOT_RELEASED"
    assert row["scheduled_for"] == selected_cycle.isoformat()
    assert row["release_calendar_key"] == "ecmwf_open_data:mx2t6_high:full"
    assert row["safe_fetch_not_before"] == safe_fetch.isoformat()


def test_forecast_live_daemon_has_no_trading_imports() -> None:
    tree = ast.parse(FORECAST_LIVE_DAEMON.read_text(encoding="utf-8"))
    banned_prefixes = (
        "src.engine",
        "src.execution",
        "src.riskguard",
        "src.risk_allocator",
        "src.strategy",
        "src.signal",
        "src.venue",
    )
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    assert not [
        module for module in imports
        if module == "src.main" or module.startswith(banned_prefixes)
    ]
