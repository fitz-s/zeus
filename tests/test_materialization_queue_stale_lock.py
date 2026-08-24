# Created: 2026-06-09
# Last reused or audited: 2026-08-23
"""Relationship tests for the persistent flock-backed materialization lock."""
from __future__ import annotations

import os
import subprocess
import sys
import threading

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
