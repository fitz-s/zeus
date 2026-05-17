# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.0a)
"""
maintenance_worker.types — public surface for P5.0a shared types.

All sub-packets import from here; this is the build-chain anchor.
P5.0a -> P5.1 -> P5.2 -> P5.3 -> P5.4 -> P5.5.
"""
from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.modes import InvocationMode, RefusalReason
from maintenance_worker.types.operations import Operation
from maintenance_worker.types.results import ApplyResult, CheckResult, ValidatorResult
from maintenance_worker.types.specs import (
    AckState,
    EngineConfig,
    ProposalManifest,
    TaskSpec,
    TickContext,
)

__all__ = [
    "AckState",
    "ApplyResult",
    "Candidate",
    "CheckResult",
    "EngineConfig",
    "InvocationMode",
    "Operation",
    "ProposalManifest",
    "RefusalReason",
    "TaskSpec",
    "TickContext",
    "ValidatorResult",
]
