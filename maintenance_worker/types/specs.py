# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.0a)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Allowed Targets"
"""
Spec dataclasses — TaskSpec, EngineConfig, TickContext, ProposalManifest,
AckState.

All frozen=True following scripts/topology_v_next/dataclasses.py P1.1 pattern.
Collections use tuple[...] to remain hashable/frozen.
Stdlib only (pathlib, dataclasses, datetime).

Note: InstallMetadata, RollbackRecipe, GuardResult, TickResult, AckStatus,
ScheduleKind are SCAFFOLD §3-only symbols deferred to the sub-packets that
need them (P5.1 / P5.3). Logged as deviation in P5.0a BATCH_DONE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class TaskSpec:
    """
    A single hygiene task as loaded from the task catalog.

    task_id must be unique within a catalog. schedule names which
    ScheduleKind runs this task (defined in P5.3 task_registry.py).
    dry_run_floor_exempt maps to the hardcoded FLOOR_EXEMPT_TASK_IDS
    frozenset in P5.2 validator.py; TaskRegistry cross-checks at load.
    """

    task_id: str
    description: str
    schedule: str
    dry_run_floor_exempt: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EngineConfig:
    """
    Fully-resolved engine configuration for one tick.

    All paths are absolute. env_vars holds ${VAR}-expanded values resolved
    by ConfigLoader. live_default=True is overridden to DRY_RUN_ONLY by
    InvocationMode.MANUAL_CLI and by enforce_dry_run_floor().
    """

    repo_root: Path
    state_dir: Path
    evidence_dir: Path
    task_catalog_path: Path
    safety_contract_path: Path
    live_default: bool
    scheduler: str
    notification_channel: str
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TickContext:
    """
    Immutable snapshot of one tick's execution context.

    Passed through the engine state machine from START to SUMMARY_REPORT.
    run_id is a UUID4 string (set by provenance.make_run_id() in P5.5).
    started_at is UTC.
    """

    run_id: str
    started_at: datetime
    config: EngineConfig
    invocation_mode: str  # InvocationMode.value; avoids cross-import


@dataclass(frozen=True)
class ProposalManifest:
    """
    Dry-run proposal emitted before any filesystem mutation.

    Consumed by AckManager.check_ack() and by the post-mutation detector
    (kill_switch.post_mutation_detector) to compare allowed vs applied sets.
    proposed_moves: (source, destination) pairs.
    proposed_deletes: zero-byte files eligible for removal.
    proposal_hash: SHA-256 computed by AckManager.compute_proposal_hash().
    """

    task_id: str
    proposed_moves: tuple[tuple[Path, Path], ...] = field(default_factory=tuple)
    proposed_deletes: tuple[Path, ...] = field(default_factory=tuple)
    proposed_creates: tuple[Path, ...] = field(default_factory=tuple)
    proposed_modifies: tuple[Path, ...] = field(default_factory=tuple)
    proposal_hash: str = ""


@dataclass(frozen=True)
class AckState:
    """
    Acknowledgement state for a proposal, as read from state_dir.

    acked=True: human has explicitly acked this proposal_hash.
    auto_ack_remaining: remaining count for AUTO_ACK_NEXT_N bulk-ack mode.
    Zero means no bulk-ack active.
    """

    task_id: str
    proposal_hash: str
    acked: bool = False
    auto_ack_remaining: int = 0
    acked_at: datetime | None = None
