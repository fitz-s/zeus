# Created: 2026-06-09
# Last reused or audited: 2026-08-24
# Authority basis: materialization pre-claim deadline hotfix (2026-08-24)
"""Relationship tests for the persistent flock-backed materialization lock."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading

import pytest

from src.data.replacement_forecast_live_materialization_queue import _queue_lock


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_dead_holder_metadata_is_recovered_and_path_persists(tmp_path):
    lock = tmp_path / ".materialization_queue.lock"
    lock.write_text(f"pid={_dead_pid()}\n", encoding="utf-8")
    with _queue_lock(lock) as acquired:
        assert acquired
        assert f"pid={os.getpid()}" in lock.read_text(encoding="utf-8")
    assert lock.exists()


def test_live_holder_flock_blocks_third_contender(tmp_path):
    lock = tmp_path / ".materialization_queue.lock"
    entered = threading.Event()
    release = threading.Event()

    def owner() -> None:
        with _queue_lock(lock) as acquired:
            assert acquired
            entered.set()
            assert release.wait(1.0)

    thread = threading.Thread(target=owner)
    thread.start()
    assert entered.wait(1.0)
    with _queue_lock(lock) as acquired:
        assert acquired is False
    release.set()
    thread.join(1.0)
    assert not thread.is_alive()
    assert lock.exists()


def test_normal_roundtrip_keeps_persistent_path(tmp_path):
    lock = tmp_path / ".materialization_queue.lock"
    with _queue_lock(lock) as acquired:
        assert acquired
    assert lock.exists()


def test_malformed_unlocked_metadata_is_overwritten(tmp_path):
    lock = tmp_path / ".materialization_queue.lock"
    lock.write_text("corrupt-no-pid-line\n", encoding="utf-8")
    with _queue_lock(lock) as acquired:
        assert acquired
        assert f"pid={os.getpid()}" in lock.read_text(encoding="utf-8")
    assert lock.exists()


def test_metadata_write_failure_leaves_path_and_next_owner_can_recover(
    tmp_path, monkeypatch
):
    lock = tmp_path / ".materialization_queue.lock"
    import src.data.replacement_forecast_live_materialization_queue as queue

    original_write = queue.os.write

    def fail_write(_fd, _payload):
        raise OSError("metadata write failed")

    monkeypatch.setattr(queue.os, "write", fail_write)
    try:
        with _queue_lock(lock):
            raise AssertionError("metadata failure must not yield ownership")
    except OSError:
        pass
    assert lock.exists()
    monkeypatch.setattr(queue.os, "write", original_write)
    with _queue_lock(lock) as acquired:
        assert acquired
        with _queue_lock(lock) as third:
            assert third is False



def _materialization_request() -> dict[str, object]:
    return {
        "city": "London",
        "target_date": "2026-08-25",
        "temperature_metric": "high",
        "source_cycle_time": "2026-08-24T00:00:00+00:00",
        "computed_at": "2026-08-24T08:00:00+00:00",
        "baseline_source_run_id": "baseline-run",
        "openmeteo_source_run_id": "openmeteo-run",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }


def test_empty_request_plan_skips_forecast_db_reads(tmp_path, monkeypatch):
    import src.data.replacement_forecast_live_materialization_queue as queue

    request_dir = tmp_path / "requests"
    request_dir.mkdir()

    def no_db_read(*_args, **_kwargs):
        raise AssertionError("empty request queue must not inspect the forecast DB")

    monkeypatch.setattr(queue, "_claim_db_fingerprint", no_db_read)
    monkeypatch.setattr(queue, "_priority_map_with_names", no_db_read)
    plan = queue._build_request_claim_read_plan(
        request_path=request_dir,
        processed_path=tmp_path / "processed",
        failed_path=tmp_path / "failed",
        forecast_db=tmp_path / "forecasts.db",
        limit=1,
        lane=queue.MATERIALIZATION_LANE_ALL,
    )

    assert plan.claim.selected_files == ()
    assert plan.claim.forecast_db_fingerprint is None
    assert plan.superseded == ()


def test_exact_preclaim_db_deadline_defers_and_retries_next_tick(tmp_path, monkeypatch):
    import src.data.replacement_forecast_live_materialization_queue as queue

    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    request_path = request_dir / "London.2026-08-25.high.json"
    request_path.write_text(json.dumps(_materialization_request()), encoding="utf-8")
    spawned: list[list[str]] = []

    def runner(argv):
        spawned.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, stdout="", stderr="")

    first = True

    def deadline_once(_db_path):
        nonlocal first
        if first:
            first = False
            raise sqlite3.OperationalError("DB_CONNECTION_DEADLINE_EXPIRED")
        return None

    monkeypatch.setattr(queue, "_claim_db_fingerprint", deadline_once)
    kwargs = {
        "request_dir": request_dir,
        "processed_dir": tmp_path / "processed",
        "failed_dir": tmp_path / "failed",
        "forecast_db": tmp_path / "forecasts.db",
        "seed_limit": 0,
        "limit": 1,
        "runner": runner,
    }
    deferred = queue.process_replacement_forecast_live_materialization_queue(**kwargs)

    assert deferred.status == "DEFERRED"
    assert deferred.reason_codes == (queue._CLAIM_READ_DEFERRED_REASON,)
    assert request_path.exists()
    assert not list((request_dir.parent / queue.MATERIALIZATION_INFLIGHT_DIR_NAME).glob("*.json"))
    assert spawned == []

    retried = queue.process_replacement_forecast_live_materialization_queue(**kwargs)
    assert retried.status == "PROCESSED"
    assert retried.failed_count == 0
    assert len(spawned) == 1


def test_non_deadline_preclaim_sqlite_error_is_not_swallowed(tmp_path, monkeypatch):
    import src.data.replacement_forecast_live_materialization_queue as queue

    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    request_path = request_dir / "London.2026-08-25.high.json"
    request_path.write_text(json.dumps(_materialization_request()), encoding="utf-8")

    def other_sqlite_error(_db_path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(queue, "_claim_db_fingerprint", other_sqlite_error)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        queue.process_replacement_forecast_live_materialization_queue(
            request_dir=request_dir,
            processed_dir=tmp_path / "processed",
            failed_dir=tmp_path / "failed",
            forecast_db=tmp_path / "forecasts.db",
            seed_limit=0,
            limit=1,
            runner=lambda _argv: pytest.fail("runner must not be called"),
        )
    assert request_path.exists()
    assert not list((request_dir.parent / queue.MATERIALIZATION_INFLIGHT_DIR_NAME).glob("*.json"))


def test_normal_preclaim_success_still_runs_runner(tmp_path):
    import src.data.replacement_forecast_live_materialization_queue as queue

    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    request_path = request_dir / "London.2026-08-25.high.json"
    request_path.write_text(json.dumps(_materialization_request()), encoding="utf-8")
    spawned: list[list[str]] = []

    def runner(argv):
        spawned.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, stdout="", stderr="")

    report = queue.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        forecast_db=None,
        seed_limit=0,
        limit=1,
        runner=runner,
    )

    assert report.status == "PROCESSED"
    assert report.processed_count == 1
    assert report.failed_count == 0
    assert len(spawned) == 1



def test_empty_request_priority_preserves_unknown_inflight_deferred(tmp_path):
    import src.data.replacement_forecast_live_materialization_queue as queue

    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    batch = request_dir.parent / queue.MATERIALIZATION_INFLIGHT_DIR_NAME / "legacy-owner"
    batch.mkdir(parents=True)
    claimed = batch / "unknown.json"
    claimed.write_text("{}", encoding="utf-8")

    report = queue.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        forecast_db=tmp_path / "forecasts.db",
        seed_limit=0,
        limit=1,
        runner=lambda _argv: pytest.fail("unknown inflight owner must not run a child"),
        lane=queue.MATERIALIZATION_LANE_PRIORITY,
    )

    assert report.status == "DEFERRED"
    assert queue._CLAIM_UNKNOWN_INFLIGHT_DEFERRED_REASON in report.reason_codes
    assert claimed.exists()
    assert batch.exists()
