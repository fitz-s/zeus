# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §6 antibody #11
"""Antibody #11 — Dual-run file lock race test.

Simulates two processes both attempting to acquire the advisory lock for
the 'hourly_instants' table and write a fake row. Asserts:
- Only ONE process successfully acquires the lock and writes.
- The other process returns 'skipped_lock_held' status.

Uses multiprocessing to spawn real OS processes so fcntl.flock semantics
are exercised correctly (flock is per open-file-description, not per thread).

tmp_path fixture isolates the lock directory so this test never touches
the production state/locks/ directory.
"""

from __future__ import annotations

import multiprocessing
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Worker functions (must be defined at module level for pickle-ability)
# ---------------------------------------------------------------------------

def _worker_try_write(
    lock_dir: str,
    output_file: str,
    hold_seconds: float,
    result_queue: "multiprocessing.Queue[str]",
) -> None:
    """Try to acquire the lock, write to output_file if acquired, hold for hold_seconds."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.data.dual_run_lock import acquire_lock

    lock_dir_path = Path(lock_dir)
    output_path = Path(output_file)

    with acquire_lock("hourly_instants", _locks_dir_override=lock_dir_path) as acquired:
        if not acquired:
            result_queue.put("skipped_lock_held")
            return
        # Simulate a tick: write a fake row marker file.
        output_path.write_text("written_by_pid=" + str(multiprocessing.current_process().pid))
        # Hold the lock briefly to ensure the second process sees it contended.
        time.sleep(hold_seconds)
        result_queue.put("acquired_and_wrote")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_only_one_process_writes_under_lock(tmp_path):
    """Two processes contend on the hourly_instants lock.

    Expected outcome:
    - Exactly one process reports 'acquired_and_wrote'.
    - Exactly one process reports 'skipped_lock_held'.
    - The output file exists (the winning process wrote it).
    - The output file is written only once (no double-write).
    """
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    output_file = tmp_path / "fake_row.txt"

    ctx = multiprocessing.get_context("fork")
    result_queue: multiprocessing.Queue = ctx.Queue()

    # Process 1: acquires lock, holds for 0.5s to give process 2 time to try.
    p1 = ctx.Process(
        target=_worker_try_write,
        args=(str(lock_dir), str(output_file), 0.5, result_queue),
    )
    # Process 2: starts 0.1s later, should find lock held.
    p2 = ctx.Process(
        target=_worker_try_write,
        args=(str(lock_dir), str(output_file), 0.0, result_queue),
    )

    p1.start()
    time.sleep(0.1)  # Give p1 time to acquire before p2 starts.
    p2.start()

    p1.join(timeout=5)
    p2.join(timeout=5)

    assert p1.exitcode == 0, f"Process 1 crashed (exitcode={p1.exitcode})"
    assert p2.exitcode == 0, f"Process 2 crashed (exitcode={p2.exitcode})"

    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    assert len(results) == 2, (
        f"Expected 2 results from the two processes, got {len(results)}: {results}"
    )
    assert results.count("acquired_and_wrote") == 1, (
        f"Expected exactly 1 process to acquire and write, results: {results}"
    )
    assert results.count("skipped_lock_held") == 1, (
        f"Expected exactly 1 process to be skipped_lock_held, results: {results}"
    )

    # The output file must exist exactly once (no empty/double write).
    assert output_file.exists(), "Winning process should have written the output file"
    content = output_file.read_text()
    assert content.startswith("written_by_pid="), (
        f"Output file content malformed: {content!r}"
    )


def test_lock_releases_after_context_exit(tmp_path):
    """After the first process's lock context exits, a second acquire succeeds."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()

    from src.data.dual_run_lock import acquire_lock

    # First context: acquire and release.
    with acquire_lock("hourly_instants", _locks_dir_override=lock_dir) as a1:
        assert a1 is True, "First acquire should succeed"

    # Second context (same process, same thread): should succeed after release.
    with acquire_lock("hourly_instants", _locks_dir_override=lock_dir) as a2:
        assert a2 is True, "Second acquire after release should succeed"


def test_unknown_table_name_still_works(tmp_path):
    """acquire_lock works for any string table name, not just the six known ones."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()

    from src.data.dual_run_lock import acquire_lock

    with acquire_lock("custom_test_table", _locks_dir_override=lock_dir) as acquired:
        assert acquired is True

    # Lock file was created.
    assert (lock_dir / "k2_custom_test_table.lock").exists()
