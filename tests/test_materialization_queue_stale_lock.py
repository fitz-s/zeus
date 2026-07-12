# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator directive 2026-06-09 — the orphaned materialization-queue lock
#   ("your regression") stalled live ~12h. A holder SIGKILL'd mid-run never ran its
#   finally-unlink, so its lock blocked the queue forever -> materializer dark -> readiness
#   (3h TTL) expired -> reactor READINESS_EXPIRED -> zero trades. Relationship tests for the
#   stale-lock self-heal antibody (dead-PID detection + archive + steal) in
#   src/data/replacement_forecast_live_materialization_queue.py.
"""Relationship tests: an orphaned queue lock from a DEAD holder cannot block the queue.

The category (Fitz #5 make-it-unconstructable + #3 immune system): the queue's exclusive lock
is released only by the holder's ``finally`` unlink. A holder killed with SIGKILL skips
``finally`` entirely, orphaning the lock; every later acquirer then sees ``FileExistsError`` and
gives up -> the materializer goes dark and readiness silently expires. These tests pin the
boundary between *holder liveness* and *queue acquirability*: a dead holder's lock is
auto-archived and stolen, a live holder still blocks, and an unparseable lock never wedges
the queue forever.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.data.replacement_forecast_live_materialization_queue import _queue_lock


def _dead_pid() -> int:
    """A PID that was a real process and is now guaranteed dead (deterministic)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _write_lock(path: Path, pid: int) -> None:
    path.write_text(
        f"pid={pid} acquired_at=2026-06-08T21:24:32.884541+00:00\n",
        encoding="utf-8",
    )


def test_dead_holder_lock_is_stolen_not_blocking(tmp_path):
    lock = tmp_path / ".materialization_queue.lock"
    _write_lock(lock, _dead_pid())
    with _queue_lock(lock) as acquired:
        assert acquired is True, "a lock held by a DEAD pid must be stealable, not block the queue"
    assert not lock.exists(), "the stealing acquirer releases its lock on exit"


def test_dead_holder_lock_is_archived_for_audit(tmp_path):
    lock = tmp_path / ".materialization_queue.lock"
    dead = _dead_pid()
    _write_lock(lock, dead)
    with _queue_lock(lock) as acquired:
        assert acquired is True
    qdir = tmp_path / "archived_stale_locks"
    archived = list(qdir.glob(f"*pid{dead}*")) if qdir.exists() else []
    assert archived, "a stolen stale lock must be archived (audit trail), never silently deleted"


def test_live_holder_lock_still_blocks(tmp_path):
    lock = tmp_path / ".materialization_queue.lock"
    _write_lock(lock, os.getpid())  # this test process is definitely alive
    with _queue_lock(lock) as acquired:
        assert acquired is False, "a lock held by a LIVE pid must still block (no concurrent double-run)"
    assert lock.exists(), "a blocked acquirer must NOT remove the live holder's lock"


def test_normal_roundtrip_leaves_no_lock(tmp_path):
    lock = tmp_path / ".materialization_queue.lock"
    with _queue_lock(lock) as acquired:
        assert acquired is True
        assert lock.exists(), "the lock is held for the duration of the body"
    assert not lock.exists(), "the lock is released after the body"


def test_garbled_lock_is_recovered(tmp_path):
    """A lock file with no parseable ``pid=`` (truncated/garbled write) is treated as orphaned
    and recovered, so a corrupt lock never wedges the queue forever."""
    lock = tmp_path / ".materialization_queue.lock"
    lock.write_text("corrupt-no-pid-line\n", encoding="utf-8")
    with _queue_lock(lock) as acquired:
        assert acquired is True, "an unparseable lock must not block the queue indefinitely"
    assert not lock.exists()
