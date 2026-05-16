# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/PLAN.md §WAVE 1.5
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md
"""
types/candidates — Candidate dataclass.

A Candidate represents one discovered item returned by a task handler's
enumerate() function. The engine collects these, emits them in the dry-run
proposal, and passes them to apply() to produce an ApplyResult.

Stdlib only. No imports from maintenance_worker.core.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Candidate:
    """
    One enumerated item for a maintenance task.

    task_id: which TaskSpec produced this candidate.
    path:    primary filesystem path this candidate refers to.
    verdict: handler-specific verdict string, e.g. ARCHIVABLE,
             LOAD_BEARING, STALE_QUARANTINE_CANDIDATE,
             SKIP (forbidden), ARCHIVE_CANDIDATE.
    reason:  human-readable rationale for the verdict.
    evidence: structured diagnostic data (check results, matched
              patterns, ages, etc.) — stored as dict so handlers can
              include arbitrary check outputs without widening the type.
    """

    task_id: str
    path: Path
    verdict: str
    reason: str
    evidence: dict[str, object] = field(default_factory=dict)
