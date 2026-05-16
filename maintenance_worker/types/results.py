# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.0a)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Pre-Action Validator"
"""
Result enums and dataclasses — ValidatorResult, RefusalReason (re-exported),
ApplyResult, CheckResult.

ValidatorResult has exactly 5 values per SAFETY_CONTRACT.md §128-136.
ApplyResult carries staged filesystem mutations; does NOT commit to git
(P5.5 ApplyPublisher owns commit/PR/provenance per SCAFFOLD §3.5).
CheckResult models a single guard check outcome; shape inferred from
SCAFFOLD §3 guard contract (exact fields not specified; deviation logged).

No logic. Stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Re-export RefusalReason here for brief smoke-test compatibility.
# Authoritative definition is maintenance_worker.types.modes.
from maintenance_worker.types.modes import RefusalReason  # noqa: F401


class ValidatorResult(str, Enum):
    """
    Return enum for validate_action(path, operation).

    Exactly 5 values per SAFETY_CONTRACT.md §128-136.
    ALLOWED_BUT_DRY_RUN_ONLY prevents APPLY_DECISIONS from executing live;
    used by enforce_dry_run_floor() and accidental-trigger containment.
    """

    ALLOWED = "ALLOWED"
    FORBIDDEN_PATH = "FORBIDDEN_PATH"
    FORBIDDEN_OPERATION = "FORBIDDEN_OPERATION"
    MISSING_PRECHECK = "MISSING_PRECHECK"
    ALLOWED_BUT_DRY_RUN_ONLY = "ALLOWED_BUT_DRY_RUN_ONLY"


@dataclass(frozen=True)
class CheckResult:
    """
    Outcome of a single guard check (pre-tick guard evaluation).

    ok=True: guard passed, tick may continue.
    ok=False: guard failed; reason names the RefusalReason, details holds
    diagnostic context for logging.

    Shape inferred from SCAFFOLD §3 guard contract; not fully specified
    upstream (logged as deviation in P5.0a BATCH_DONE).
    """

    ok: bool
    reason: str = ""
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ApplyResult:
    """
    Staged filesystem mutations from MaintenanceEngine._apply_decisions().

    Does NOT represent a git commit — P5.5 ApplyPublisher.publish() owns
    commit/PR/provenance. Post-mutation detector in kill_switch.py reads
    this to compare applied diff against the allowed-write manifest.

    moved: mapping of source → destination paths (git mv operations).
    deleted: zero-byte files removed via os.unlink (path-validated).
    created: new files created (stub manifests, evidence artifacts).
    requires_pr: True if the staged changes warrant a maintenance PR.
    task_id: which TaskSpec produced this result.
    """

    task_id: str
    moved: tuple[tuple[Path, Path], ...] = field(default_factory=tuple)
    deleted: tuple[Path, ...] = field(default_factory=tuple)
    created: tuple[Path, ...] = field(default_factory=tuple)
    requires_pr: bool = False
    dry_run_only: bool = False
