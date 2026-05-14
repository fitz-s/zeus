# Created: 2026-05-14
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md section 6.1, section 6.2, and section 8 Phase 4.
"""Relationship tests for the dedicated forecast-live daemon boundary."""

from __future__ import annotations

import ast
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

from src.data.dual_run_lock import OPENDATA_DAEMON_LOCK_KEY, acquire_lock


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
