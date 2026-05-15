# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=2026-05-14
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §5 Phase 1 + docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md §6.2.
"""Advisory per-table file lock for dual-run Phase 1.

During Phase 1 both the monolith (src.main) and the ingest daemon
(src.ingest_main) schedule the same K2 jobs. This module provides an
advisory lock so only ONE process runs a given table tick at a time.

Usage::

    from src.data.dual_run_lock import acquire_lock

    with acquire_lock("hourly_instants") as acquired:
        if not acquired:
            logger.info("skipped_lock_held: hourly_instants")
            return
        # ... do the tick work ...

Contract:
- Lock file: ``state/locks/k2_<table_name>.lock``
- ``state/locks/`` is created on first call if missing.
- Uses POSIX ``fcntl.flock(LOCK_EX | LOCK_NB)``.
- Returns ``True`` if lock acquired, ``False`` if held by another process.
- Releases on context exit, or on process death (OS auto-release).
- Known tables: daily_obs, hourly_instants, solar_daily, forecasts_daily,
  hole_scanner, etl_recalibrate.
"""

from __future__ import annotations

import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

OPENDATA_DAEMON_LOCK_KEY = "opendata_live_forecast"

_KNOWN_TABLES = frozenset(
    {
        "daily_obs",
        "hourly_instants",
        "solar_daily",
        "forecasts_daily",
        "hole_scanner",
        "etl_recalibrate",
        # Phase 1.5: harvester split — ingest-side truth writer + trading-side P&L resolver
        "harvester_truth",
        "harvester_pnl",
        # Phase 2: source health probe, drift detector, ingest status rollup
        "source_health",
        "drift_detector",
        "ingest_status",
        # Data-daemon live-efficiency refactor: mutual exclusion between
        # legacy ingest_main and dedicated forecast-live OpenData owners.
        OPENDATA_DAEMON_LOCK_KEY,
    }
)

# Resolved at import time so it always matches state_path("locks/<name>.lock").
_LOCKS_DIR: Path | None = None


def _locks_dir() -> Path:
    global _LOCKS_DIR
    if _LOCKS_DIR is None:
        try:
            from src.config import state_path
            _LOCKS_DIR = state_path("locks")
        except Exception:
            # Fallback for tests that patch state differently.
            _LOCKS_DIR = Path(__file__).resolve().parents[2] / "state" / "locks"
    _LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    return _LOCKS_DIR


@contextmanager
def acquire_lock(
    table_name: str,
    *,
    _locks_dir_override: Path | None = None,
) -> Generator[bool, None, None]:
    """Context manager that attempts to acquire an exclusive advisory file lock.

    Parameters
    ----------
    table_name:
        One of the six known ingest tables (or any string for testing).
    _locks_dir_override:
        For tests: use this directory instead of the canonical state/locks/.

    Yields
    ------
    True
        Lock was acquired; caller should proceed.
    False
        Lock is held by another process; caller should skip and log
        ``"skipped_lock_held"``.
    """
    lock_dir = _locks_dir_override if _locks_dir_override is not None else _locks_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"k2_{table_name}.lock"

    lock_file = lock_path.open("w")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another process holds the lock.
            lock_file.close()
            yield False
            return
        # We hold the lock.
        try:
            yield True
        finally:
            # Release explicitly; also released on process death.
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
    finally:
        try:
            lock_file.close()
        except Exception:
            pass
