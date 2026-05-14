# Created: 2026-05-14
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md section 6.2 and section 8 Phase 2/4.
"""Relationship tests for OpenData scheduler ownership.

These tests lock the boundary between the legacy ingest daemon and the new
forecast-live owner. Only one process may schedule live OpenData collection.
"""

from __future__ import annotations

import sys
import types


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

import src.ingest_main as ingest_main


class _RecordingScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, object]] = []

    def add_job(self, func, trigger, **kwargs) -> None:
        self.jobs.append({"func": func, "trigger": trigger, **kwargs})


def _jobs_by_id(scheduler: _RecordingScheduler) -> dict[str, dict[str, object]]:
    return {str(job["id"]): job for job in scheduler.jobs}


def test_legacy_ingest_main_registers_opendata_jobs_by_default(monkeypatch):
    monkeypatch.delenv("ZEUS_FORECAST_LIVE_OWNER", raising=False)
    scheduler = _RecordingScheduler()

    registered = ingest_main._register_opendata_jobs(scheduler)
    jobs = _jobs_by_id(scheduler)

    assert registered == [
        "ingest_opendata_daily_mx2t6",
        "ingest_opendata_daily_mn2t6",
        "ingest_opendata_startup_catch_up",
    ]
    assert set(jobs) == set(registered)
    assert jobs["ingest_opendata_daily_mx2t6"]["hour"] == 7
    assert jobs["ingest_opendata_daily_mx2t6"]["minute"] == 30
    assert jobs["ingest_opendata_daily_mn2t6"]["hour"] == 7
    assert jobs["ingest_opendata_daily_mn2t6"]["minute"] == 35
    assert jobs["ingest_opendata_startup_catch_up"]["trigger"] == "date"
    assert jobs["ingest_opendata_startup_catch_up"]["executor"] == "fast"


def test_forecast_live_owner_suppresses_legacy_opendata_jobs(monkeypatch):
    monkeypatch.setenv("ZEUS_FORECAST_LIVE_OWNER", "forecast_live")
    scheduler = _RecordingScheduler()

    registered = ingest_main._register_opendata_jobs(scheduler)

    assert registered == []
    assert scheduler.jobs == []
