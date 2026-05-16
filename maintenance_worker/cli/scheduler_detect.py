# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/scheduler_detect.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"InvocationMode"
"""
cli/scheduler_detect — detect() -> InvocationMode

Detects how the maintenance worker was invoked:
  SCHEDULED   — launched by launchd or cron (MAINTENANCE_SCHEDULER=1 env var,
                or parent is launchd, or MAINTENANCE_IN_PROCESS not set + no TTY)
  MANUAL_CLI  — interactive terminal (sys.stdin.isatty() and no scheduler env)
  IN_PROCESS  — programmatic invocation (MAINTENANCE_IN_PROCESS=1 env var)

Detection priority (highest to lowest):
  1. MAINTENANCE_IN_PROCESS=1  → IN_PROCESS
  2. MAINTENANCE_SCHEDULER=1   → SCHEDULED
  3. sys.stdin.isatty()        → MANUAL_CLI
  4. default                   → SCHEDULED (launchd/cron with no TTY)

Stdlib only. Zero Zeus identifiers.
"""
from __future__ import annotations

import os
import sys

from maintenance_worker.types.modes import InvocationMode


def detect() -> InvocationMode:
    """
    Detect the current invocation mode.

    Priority (highest to lowest):
      1. MAINTENANCE_IN_PROCESS=1 → IN_PROCESS
      2. MAINTENANCE_SCHEDULER=1  → SCHEDULED
      3. sys.stdin.isatty()       → MANUAL_CLI
      4. default                  → SCHEDULED

    This function is pure (reads env + tty state; no side effects).
    """
    # 1. Programmatic in-process embedding
    if os.environ.get("MAINTENANCE_IN_PROCESS") == "1":
        return InvocationMode.IN_PROCESS

    # 2. Explicit scheduler signal (launchd plist or cron_wrapper.sh)
    if os.environ.get("MAINTENANCE_SCHEDULER") == "1":
        return InvocationMode.SCHEDULED

    # 3. Interactive terminal → user invoked the CLI directly
    try:
        if sys.stdin.isatty():
            return InvocationMode.MANUAL_CLI
    except AttributeError:
        # stdin may be None in some embedding contexts
        pass

    # 4. No TTY, no env signal → assume scheduled (launchd default)
    return InvocationMode.SCHEDULED
