# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/scheduler_bindings/
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Scheduler Bindings"
"""
in_process_scheduler — InProcessScheduler for programmatic tick invocation.

Provides a simple blocking scheduler that fires run_tick() at a fixed
interval. Intended for testing, CI, and embedding the maintenance worker
into a parent process without a system scheduler (launchd/cron).

InvocationMode is set to IN_PROCESS; the engine respects dry-run floors
regardless of invocation mode, so this is safe to use in production
contexts that don't have launchd/cron configured.

Public API:
  scheduler = InProcessScheduler(config, interval_seconds=3600)
  scheduler.run_once()           # fire one tick immediately
  scheduler.run_forever()        # blocking loop; Ctrl-C to stop

Stdlib only. Zero Zeus identifiers.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from maintenance_worker.core.engine import TickResult, run_tick
from maintenance_worker.types.specs import EngineConfig


# ---------------------------------------------------------------------------
# InProcessScheduler
# ---------------------------------------------------------------------------


class InProcessScheduler:
    """
    Simple blocking scheduler that fires run_tick() at a fixed interval.

    Sets MAINTENANCE_IN_PROCESS=1 in the environment so that
    scheduler_detect.detect() returns InvocationMode.IN_PROCESS.

    Usage:
        scheduler = InProcessScheduler(config, interval_seconds=3600)
        scheduler.run_once()
        # or
        scheduler.run_forever()  # blocks until KeyboardInterrupt

    on_tick_complete: optional callback called after each tick with the
    TickResult. Useful for notification/logging in parent processes.
    """

    # Environment variable that signals IN_PROCESS invocation mode.
    _ENV_FLAG = "MAINTENANCE_IN_PROCESS"

    def __init__(
        self,
        config: EngineConfig,
        interval_seconds: int = 3600,
        on_tick_complete: Optional[Callable[[TickResult], None]] = None,
    ) -> None:
        """
        config: EngineConfig for the maintenance worker.
        interval_seconds: how often to fire ticks (default 3600 = hourly).
        on_tick_complete: called after each tick with its TickResult.
        """
        if interval_seconds < 1:
            raise ValueError(f"interval_seconds must be >= 1, got {interval_seconds}")
        self._config = config
        self._interval_seconds = interval_seconds
        self._on_tick_complete = on_tick_complete

    def run_once(self) -> TickResult:
        """
        Fire one tick immediately and return its TickResult.

        Sets MAINTENANCE_IN_PROCESS=1 for the duration of the call,
        then restores the original value.
        """
        prior = os.environ.get(self._ENV_FLAG)
        os.environ[self._ENV_FLAG] = "1"
        try:
            result = run_tick(self._config)
            if self._on_tick_complete is not None:
                self._on_tick_complete(result)
            return result
        finally:
            if prior is None:
                os.environ.pop(self._ENV_FLAG, None)
            else:
                os.environ[self._ENV_FLAG] = prior

    def run_forever(self) -> None:
        """
        Blocking tick loop. Fires run_once() at interval_seconds cadence.

        Catches KeyboardInterrupt cleanly (logs stop message and returns).
        All other exceptions from run_tick are caught per-tick so the
        scheduler survives individual tick failures (fail-open loop).

        Loop: fire → sleep(interval) → fire → sleep → ...
        The first tick fires immediately on entry.
        """
        try:
            while True:
                try:
                    self.run_once()
                except SystemExit:
                    # run_tick() calls sys.exit() on fatal guard failure.
                    # Propagate to let the parent process handle it.
                    raise
                except Exception:
                    # Per-tick failure: log (best-effort) and continue.
                    import traceback
                    traceback.print_exc()

                time.sleep(self._interval_seconds)
        except KeyboardInterrupt:
            pass
