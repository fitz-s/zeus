"""Process-level lock to prevent double-daemon launches. P12 finding #3.

Uses fcntl.flock() with a mode-qualified lock file so two live daemons cannot
run concurrently.

Stale lock detection: if the PID in the lock file is dead, reclaim the lock.
The caller must keep the returned fd alive (store it) to hold the lock.
"""

from __future__ import annotations

import fcntl
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def acquire_process_lock(state_dir: Path) -> "int | None":
    """Acquire an exclusive process lock for the live daemon.

    Args:
        state_dir: Directory where lock files are stored (e.g. zeus/state/).

    Returns:
        Open file descriptor holding the lock. Caller MUST keep this reference
        alive (e.g. store as module-level or instance variable) — GC closing
        the fd releases the lock.

    Raises:
        SystemExit: if another instance is already running.
    """
    mode = "live"
    lock_path = state_dir / f"zeus-{mode}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    lock_fd = None

    def _try_lock():
        nonlocal lock_fd
        lock_fd = open(lock_path, "r+")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.seek(0)
        lock_fd.truncate()
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()

    # Fast path: lock is free
    try:
        _try_lock()
        logger.info("Process lock acquired: %s (PID %d)", lock_path, os.getpid())
        return lock_fd
    except (IOError, OSError):
        pass

    # Lock is held — check if the holder is still alive
    try:
        old_pid_str = lock_path.read_text().strip()
        old_pid = int(old_pid_str)
        os.kill(old_pid, 0)  # signal 0 = probe, no kill
        # Process is alive — genuine conflict
        logger.error(
            "Another Zeus %s instance (PID %d) is already running. "
            "Kill it first or run: kill %d",
            mode, old_pid, old_pid,
        )
        sys.exit(1)
    except PermissionError:
        logger.error(
            "Another Zeus %s instance (PID %s) appears running "
            "(permission denied when probing).",
            mode, old_pid_str,
        )
        sys.exit(1)
    except (ValueError, ProcessLookupError):
        # PID is dead or unparseable — stale lock, reclaim
        logger.warning("Stale lock from dead PID %s. Reclaiming.", old_pid_str)
        if lock_fd is not None:
            lock_fd.close()
        for _ in range(5):
            try:
                _try_lock()
                logger.info("Process lock reclaimed: %s (PID %d)", lock_path, os.getpid())
                return lock_fd
            except (IOError, OSError):
                time.sleep(0.2)
        logger.error("Failed to reclaim process lock after stale PID cleanup.")
        sys.exit(1)
