# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.0a)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Validator Semantics"
"""
Operation enum — exhaustive set of filesystem/process operations the agent
may attempt. READ is not exempt per SAFETY_CONTRACT.md §147-149.
MKDIR is a distinct value per SAFETY_CONTRACT.md line 100.

No logic. Stdlib only.
"""
from __future__ import annotations

from enum import Enum


class Operation(str, Enum):
    """
    Every operation the agent may attempt, pre-validated via validate_action().

    Members are string-valued for logging and serialization transparency.

    READ is not exempt: reads of credential files, state/*.db*, and all
    Forbidden Target paths return FORBIDDEN_PATH, not ALLOWED.
    SAFETY_CONTRACT.md §(a): "READ is not exempt."

    MKDIR is explicit (not subsumed under WRITE) per SAFETY_CONTRACT.md
    line 100, which lists directory creation as an allowed-write operation
    requiring its own path-pattern check.
    """

    READ = "READ"
    WRITE = "WRITE"
    MKDIR = "MKDIR"
    MOVE = "MOVE"
    DELETE = "DELETE"
    GIT_EXEC = "GIT_EXEC"
    GH_EXEC = "GH_EXEC"
    SUBPROCESS_EXEC = "SUBPROCESS_EXEC"
