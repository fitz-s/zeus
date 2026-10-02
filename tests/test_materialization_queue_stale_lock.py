# Created: 2026-06-09
# Last reused/audited: 2026-09-30
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


@pytest.mark.parametrize("limit", (2, 3))
@pytest.mark.parametrize("third_slot", ("none", "expansion", "global"))
def test_priority_fallback_reserves_each_request_only_once(tmp_path, limit, third_slot):
    import src.data.replacement_forecast_live_materialization_queue as queue

    requests = tmp_path / "requests"
    requests.mkdir()
    held = requests / "London.high.json"
    expansion = requests / "Hong_Kong.high.json"
    payloads = {
        held: _materialization_request(),
        expansion: dict(_materialization_request(), city="Hong Kong"),
    }
    third = requests / "Paris.high.json"
    if third_slot != "none":
        payloads[third] = dict(_materialization_request(), city="Paris")
    for path, payload in payloads.items():
        path.write_text(json.dumps(payload), encoding="utf-8")
    before = {path: (path.stat().st_ino, path.read_bytes()) for path in payloads}
    selected = queue._interleave_current_priority_request_files(
        tuple(payloads), payloads,
        current_money_risk=frozenset({("London", "2026-08-25", "high")}),
        current_global_scope=(frozenset({("Paris", "2026-08-25", "high")})
                              if third_slot == "global" else frozenset()),
        limit=limit,
    )[:limit]
    # The ordinary flocked claim must not move a fallback/global reservation
    # twice. The old three-slot head fails here with the actual rename ENOENT.
    with queue._queue_lock(tmp_path / ".materialization_queue.lock") as acquired:
        assert acquired
        batch = queue._new_claim_batch(tmp_path / queue.MATERIALIZATION_INFLIGHT_DIR_NAME, selected)
    expected = ((held, third, expansion) if third_slot == "global"
                else (held, expansion, third) if third_slot == "expansion"
                else (held, expansion))[:limit]
    assert selected == expected
    assert len(queue._claim_request_files(batch)) == len(expected)
    for path in selected:
        inode, body = before[path]
        claimed = batch / path.name
        assert not path.exists()
        assert (claimed.stat().st_ino, claimed.read_bytes()) == (inode, body)
    keys, recovered, unknown = queue._recover_stale_claims(
        request_path=requests, inflight_path=batch.parent,
    )
    assert recovered == 0 and not unknown and len(keys) == len(expected)
    assert len(queue._claim_request_files(batch)) == len(expected)
    for path in set(payloads) - set(selected):
        assert (path.stat().st_ino, path.read_bytes()) == before[path]


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


def _priority_claim_plan(tmp_path, monkeypatch):
    import src.data.replacement_forecast_live_materialization_queue as queue

    requests = tmp_path / "requests"
    requests.mkdir()
    selected = requests / "London.2026-08-25.high.json"
    selected.write_text(json.dumps(_materialization_request()), encoding="utf-8")
    revision = [1]
    monkeypatch.setattr(queue, "_claim_db_fingerprint", lambda _db: revision[0])
    monkeypatch.setattr(queue, "_current_money_risk_families", lambda *_a, **_kw: frozenset())
    monkeypatch.setattr(queue, "_current_global_auction_scope_families", lambda *_a, **_kw: frozenset())

    def priority(_db, files, _payloads, **_kwargs):
        return ({p.name: (-10 if p.name.startswith("Held") else -1, p.name) for p in files},
                {p.name for p in files})

    monkeypatch.setattr(queue, "_priority_map_with_names", priority)
    def plan():
        return queue._build_request_claim_read_plan(
            request_path=requests, processed_path=tmp_path / "processed",
            failed_path=tmp_path / "failed", forecast_db=tmp_path / "forecasts.db",
            limit=3, lane=queue.MATERIALIZATION_LANE_PRIORITY,
        )
    return queue, requests, selected, revision, plan


@pytest.mark.parametrize("unrelated_request", (False, True))
def test_priority_claim_replans_unrelated_queue_or_db_churn(tmp_path, monkeypatch, unrelated_request):
    queue, requests, selected, revision, plan = _priority_claim_plan(tmp_path, monkeypatch)
    prior = plan()
    if unrelated_request:
        other = dict(_materialization_request(), city="Paris")
        (requests / "Paris.2026-08-25.high.json").write_text(json.dumps(other), encoding="utf-8")
    revision[0] = 2  # Unrelated forecast WAL commit.
    claimed, deferral = queue._try_claim_priority_request(prior)
    assert deferral == ()
    assert claimed is not None and claimed.claimed_count == 1
    assert not selected.exists()
    assert (claimed.batch_path / selected.name).exists()


def test_priority_claim_db_fence_drift_after_rerank_still_claims(tmp_path, monkeypatch):
    """Forecast writes during the re-rank itself never revoke an unchanged request."""
    queue, _requests, selected, revision, plan = _priority_claim_plan(tmp_path, monkeypatch)
    prior = plan()
    reads = iter(range(2, 100))
    monkeypatch.setattr(queue, "_claim_db_fingerprint", lambda _db: next(reads))
    claimed, deferral = queue._try_claim_priority_request(prior)
    assert deferral == ()
    assert claimed is not None and not selected.exists()
    del revision


@pytest.mark.parametrize("observation", ("matched", "none", "unknown"))
def test_priority_claim_names_owner_observation_distinctly(tmp_path, monkeypatch, observation):
    """RACED_OWNER only for a positively matched owner; unknown identity has its own reason."""
    queue, requests, selected, _revision, plan = _priority_claim_plan(tmp_path, monkeypatch)
    prior = plan()
    inflight = requests.parent / queue.MATERIALIZATION_INFLIGHT_DIR_NAME
    if observation == "matched":
        duplicate = requests / "London.duplicate.json"
        duplicate.write_text(selected.read_text(), encoding="utf-8")
        # The owner of the same identity appears after the plan; the request
        # snapshot is unchanged, so only the inflight scan can see it.
        queue._new_claim_batch(inflight, (duplicate,))
    elif observation == "unknown":
        batch = inflight / "legacy-unkeyed"
        batch.mkdir(parents=True)
        (batch / "unreadable.json").write_text("{", encoding="utf-8")
    claimed, deferral = queue._try_claim_priority_request(prior)
    raced = "REPLACEMENT_LIVE_MATERIALIZATION_PRIORITY_CLAIM_DEFERRED_RACED_OWNER"
    if observation == "matched":
        assert claimed is None and raced in deferral
    elif observation == "unknown":
        assert claimed is None
        assert deferral == (queue._PRIORITY_CLAIM_UNKNOWN_OWNER_REASON,)
        assert raced not in deferral
    else:
        assert claimed is not None and deferral == ()


@pytest.mark.parametrize("preemption", ("held", "superseder", "owner", "changed_selected"))
def test_priority_claim_replan_preserves_preemption_and_identity(tmp_path, monkeypatch, preemption):
    queue, requests, selected, revision, plan = _priority_claim_plan(tmp_path, monkeypatch)
    prior = plan()
    if preemption == "held":
        (requests / "Held.2026-08-25.high.json").write_text(
            json.dumps(dict(_materialization_request(), city="Held")), encoding="utf-8",
        )
    elif preemption == "superseder":
        (requests / "London.newer.json").write_text(
            json.dumps(dict(_materialization_request(), computed_at="2026-08-24T09:00:00+00:00")),
            encoding="utf-8",
        )
    elif preemption == "owner":
        duplicate = requests / "London.duplicate.json"
        duplicate.write_text(selected.read_text(), encoding="utf-8")
        queue._new_claim_batch(requests.parent / queue.MATERIALIZATION_INFLIGHT_DIR_NAME, (duplicate,))
    else:
        selected.write_text(json.dumps(dict(_materialization_request(), computed_at="2026-08-24T10:00:00+00:00")), encoding="utf-8")
    revision[0] = 2
    assert queue._try_claim_priority_request(prior)[0] is None
    assert selected.exists()


@pytest.mark.parametrize("during_replan", ("higher_held", "same_identity_owner"))
def test_priority_replan_rejects_owner_or_held_arriving_after_sort_before_snapshot(
    tmp_path, monkeypatch, during_replan,
):
    queue, requests, selected, revision, plan = _priority_claim_plan(tmp_path, monkeypatch)
    prior = plan()
    (requests / "Paris.unrelated.json").write_text(
        json.dumps(dict(_materialization_request(), city="Paris")), encoding="utf-8",
    )
    revision[0] = 2
    original_snapshot = queue._queue_files_snapshot
    calls = 0

    def publish_before_builder_snapshot(directory):
        nonlocal calls
        calls += 1
        if calls == 2:
            if during_replan == "higher_held":
                (requests / "Held.2026-08-25.high.json").write_text(
                    json.dumps(dict(_materialization_request(), city="Held")), encoding="utf-8",
                )
            else:
                duplicate = requests / "London.duplicate.json"
                duplicate.write_text(selected.read_text(), encoding="utf-8")
                queue._new_claim_batch(
                    requests.parent / queue.MATERIALIZATION_INFLIGHT_DIR_NAME,
                    (duplicate,),
                )
        return original_snapshot(directory)

    monkeypatch.setattr(queue, "_queue_files_snapshot", publish_before_builder_snapshot)
    assert queue._try_claim_priority_request(prior)[0] is None
    assert selected.exists()


# The forecast-DB fingerprint is no longer re-read after the re-rank, so only
# the re-rank's own read can fail.
@pytest.mark.parametrize("failure_step", ("replan", "replan_deadline"))
def test_priority_revalidation_read_failure_defers_without_moving_request(
    tmp_path, monkeypatch, failure_step,
):
    queue, requests, selected, _revision, _plan = _priority_claim_plan(tmp_path, monkeypatch)
    calls = 0

    def fingerprint(_db):
        nonlocal calls
        calls += 1
        if failure_step == "replan" and calls == 3:
            raise sqlite3.OperationalError("DB_CONNECTION_DEADLINE_EXPIRED")
        if failure_step == "replan_deadline" and calls == 3:
            raise queue._ClaimReadDeadlineExceeded()
        if failure_step == "final_fingerprint" and calls == 3:
            raise sqlite3.OperationalError("DB_CONNECTION_DEADLINE_EXPIRED")
        return 1 if calls == 1 else 2 if failure_step != "final_fingerprint" else 1

    monkeypatch.setattr(queue, "_claim_db_fingerprint", fingerprint)
    report = queue.process_replacement_forecast_live_materialization_queue(
        request_dir=requests, processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed", forecast_db=tmp_path / "forecasts.db",
        seed_limit=0, limit=3, lane=queue.MATERIALIZATION_LANE_PRIORITY,
        runner=lambda _argv: pytest.fail("no child may spawn on failed revalidation"),
    )
    assert report.status == "DEFERRED"
    assert "REPLACEMENT_LIVE_MATERIALIZATION_CLAIM_DEFERRED_REVALIDATION" in report.reason_codes
    assert selected.exists()
    assert not list((requests.parent / queue.MATERIALIZATION_INFLIGHT_DIR_NAME).glob("priority.*"))



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



def test_priority_non_priority_request_keeps_locked_path(tmp_path, monkeypatch):
    import src.data.replacement_forecast_live_materialization_queue as queue

    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    request_path = request_dir / "London.2026-08-25.high.json"
    request_path.write_text(json.dumps(_materialization_request()), encoding="utf-8")
    processed_dir = tmp_path / "processed"
    failed_dir = tmp_path / "failed"
    locked_calls: list[dict[str, object]] = []
    sentinel = queue._MaterializationQueueClaim(
        request_path=request_dir,
        batch_path=None,
        processed_path=processed_dir,
        failed_path=failed_dir,
        claimed_count=0,
        skipped_count=0,
        inflight_deferred_count=0,
        timeout_retry_deferred_count=0,
        processed_files=(),
        failed_files=(),
        seed_processed_files=(),
        seed_failed_files=(),
        seed_reasons=(),
        discovery_report=None,
    )

    def locked_claim(**kwargs):
        locked_calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(
        queue,
        "_claim_replacement_forecast_live_materialization_queue_locked",
        locked_claim,
    )
    report = queue.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=processed_dir,
        failed_dir=failed_dir,
        forecast_db=None,
        seed_limit=0,
        limit=1,
        runner=lambda _argv: pytest.fail("locked sentinel must not run a child"),
        lane=queue.MATERIALIZATION_LANE_PRIORITY,
    )

    assert locked_calls
    assert report.status == "NO_REQUESTS"
    assert request_path.exists()



def test_priority_empty_request_returns_before_lock_or_discovery(tmp_path, monkeypatch):
    import src.data.replacement_forecast_live_materialization_queue as queue

    request_dir = tmp_path / "requests"
    request_dir.mkdir()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("empty priority queue must not lock, discover, or read DB")

    monkeypatch.setattr(queue, "_queue_lock", forbidden)
    monkeypatch.setattr(
        queue,
        "_claim_replacement_forecast_live_materialization_queue_locked",
        forbidden,
    )
    monkeypatch.setattr(queue, "_priority_map_with_names", forbidden)
    monkeypatch.setattr(queue, "_claim_db_fingerprint", forbidden)

    report = queue.process_replacement_forecast_live_materialization_queue(
        request_dir=request_dir,
        processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed",
        forecast_db=tmp_path / "forecasts.db",
        seed_limit=0,
        limit=1,
        runner=lambda _argv: pytest.fail("empty priority queue must not run a child"),
        lane=queue.MATERIALIZATION_LANE_PRIORITY,
    )

    assert report.status == "NO_REQUESTS"
    assert "REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_EMPTY" in report.reason_codes
