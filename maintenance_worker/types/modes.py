# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.0a)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Accidental-Trigger Containment"
"""
Mode enums — invocation mode and refusal reason.

RefusalReason placed here (modes.py) per SCAFFOLD §3; note: brief smoke test
imports RefusalReason from results.py — the types/__init__.py re-exports both,
so both import paths work. Authoritative definition is this file.

No logic. Stdlib only.
"""
from __future__ import annotations

from enum import Enum


class InvocationMode(str, Enum):
    """
    How the agent was invoked. Drives DRY_RUN_ONLY enforcement.

    MANUAL_CLI forces DRY_RUN_ONLY regardless of live_default per
    SAFETY_CONTRACT.md "Accidental-Trigger Containment".
    """

    SCHEDULED = "SCHEDULED"
    MANUAL_CLI = "MANUAL_CLI"
    IN_PROCESS = "IN_PROCESS"


class RefusalReason(str, Enum):
    """
    Named reasons for a tick refusal. Each maps to a unique exit code.

    Hard guards (refuse_fatal, non-zero exit, human must intervene):
      KILL_SWITCH, DIRTY_REPO, ACTIVE_REBASE, LOW_DISK, INFLIGHT_PR,
      SELF_QUARANTINED, FORBIDDEN_PATH_VIOLATION, FORBIDDEN_OPERATION_VIOLATION

    Soft guards (skip_tick, next tick retries automatically):
      MAINTENANCE_PAUSED, ONCALL_QUIET

    Per SCAFFOLD §6: 6 refuse_fatal hard guards + 2 skip_tick soft guards.
    The FORBIDDEN_* pair covers validator gate failures (path and operation).
    """

    # Hard guards — refuse_fatal
    KILL_SWITCH = "KILL_SWITCH"
    DIRTY_REPO = "DIRTY_REPO"
    ACTIVE_REBASE = "ACTIVE_REBASE"
    LOW_DISK = "LOW_DISK"
    INFLIGHT_PR = "INFLIGHT_PR"
    SELF_QUARANTINED = "SELF_QUARANTINED"
    FORBIDDEN_PATH_VIOLATION = "FORBIDDEN_PATH_VIOLATION"
    FORBIDDEN_OPERATION_VIOLATION = "FORBIDDEN_OPERATION_VIOLATION"

    # Soft guards — skip_tick
    MAINTENANCE_PAUSED = "MAINTENANCE_PAUSED"
    ONCALL_QUIET = "ONCALL_QUIET"
