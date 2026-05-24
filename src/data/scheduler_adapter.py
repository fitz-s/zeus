# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §7
#   (Scheduler adapter / executor classes) + §"Scheduler/concurrency efficiency";
#   docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR6); src/data/source_job_registry.py.
"""Scheduler adapter — PR6 (pure planner; daemon wiring is operator-gated).

Turns the job registry (src/data/source_job_registry) into per-job APScheduler build specs,
assigning each job an EXECUTOR CLASS by intent so the single-writer SQLite lock no longer lets
DB-heavy jobs starve heartbeats:

    live_db      — live DB writers (forecast/observation/market live ingest)
    backfill_db  — backfill / historical DB writers (incl. UMA historical settlement)
    derived_db   — derived/diagnostic DB writers (calibration, skill, drift, recalibrate)
    io           — non-DB IO jobs
    heartbeat    — heartbeat / health / status (file-only, must never block on the DB lock)

This module is the structural home of the "UMA must not write the DB on the file-only fast
executor" fix: by construction a writes_db job is assigned a *_db class, never io/heartbeat.

PR6 ships this planner + a dry-run preview ONLY. Flipping the live daemon to build its scheduler
from these specs is gated behind ZEUS_SCHEDULER_REGISTRY_ENABLED (default off) and is an
operator-go activation step — this module changes NO runtime behavior on its own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

from src.data.source_job_registry import JOB_REGISTRY, SourceJobSpec

ExecutorClass = Literal["live_db", "backfill_db", "derived_db", "io", "heartbeat"]

SCHEDULER_REGISTRY_FLAG = "ZEUS_SCHEDULER_REGISTRY_ENABLED"


def scheduler_registry_enabled() -> bool:
    """True only when the operator has explicitly enabled registry-built scheduling.

    Default OFF — the live daemons keep their hand-coded add_job() blocks until activation.
    """
    return os.environ.get(SCHEDULER_REGISTRY_FLAG, "0").strip().lower() in ("1", "true", "yes")


def executor_class_for(spec: SourceJobSpec) -> ExecutorClass:
    """Assign an executor class by job intent. writes_db jobs ALWAYS get a *_db class."""
    if not spec.writes_db:
        # file-only / non-DB jobs: heartbeat-class for diagnostics, io otherwise.
        return "heartbeat" if spec.role == "diagnostic" else "io"
    if spec.role == "live" or spec.role == "settlement":
        # Settlement is live-critical EXCEPT historical UMA, which is a backfill concern.
        if spec.source_id == "polymarket_uma_oo_v2":
            return "backfill_db"
        return "live_db"
    if spec.role == "backfill":
        return "backfill_db"
    return "derived_db"  # derived / diagnostic DB writers


@dataclass(frozen=True)
class JobBuildSpec:
    """A resolved APScheduler build descriptor for one job (consumed at activation)."""

    job_id: str
    owner_daemon: str
    executor_class: ExecutorClass
    max_instances: int
    coalesce: bool
    misfire_grace_time: int  # seconds

    @property
    def is_db_writer(self) -> bool:
        return self.executor_class.endswith("_db")


def build_job_specs(owner_daemon: Optional[str] = None) -> list[JobBuildSpec]:
    """Resolve registry jobs into JobBuildSpecs (pure; adds nothing to a live scheduler).

    ``owner_daemon`` (PR review #329 F9): when given, return ONLY that daemon's jobs. Activation
    MUST pass it — otherwise a single daemon would build BOTH daemons' jobs (cross-daemon
    scheduling + bypass of the OpenData singleton). Default None = full inventory (preview only).

    Concurrency (PR review #329 F10): every job is single-instance + coalesce, matching the
    current ingest_main scheduler (the fast-executor file-only jobs already use
    max_instances=1/coalesce=True to prevent overlapping JSON writers; the default executor is
    single-worker). The prior role-derived 3/coalesce=False would have made heartbeats/health
    overlap on activation — NOT behavior-preserving.
    """
    specs: list[JobBuildSpec] = []
    for j in JOB_REGISTRY.values():
        if owner_daemon is not None and j.owner_daemon != owner_daemon:
            continue
        ec = executor_class_for(j)
        # Preserve the job's REAL misfire grace where declared (PR review #329 R3 F9): OpenData /
        # TIGGE daily jobs use 3600s; clobbering them with a 300/60 default changes catch-up
        # semantics on activation. Fall back to a class default only when the registry is silent.
        misfire = j.misfire_grace_time if j.misfire_grace_time is not None \
            else (300 if ec.endswith("_db") else 60)
        specs.append(JobBuildSpec(
            job_id=j.job_id,
            owner_daemon=j.owner_daemon,
            executor_class=ec,
            max_instances=1,                       # serial — preserve current anti-overlap
            coalesce=True,                          # merge missed runs, never stack
            misfire_grace_time=misfire,
        ))
    return specs


def validate_executor_assignment(specs: list[JobBuildSpec] | None = None) -> list[str]:
    """Fail-closed structural check: no DB writer may land on io/heartbeat (the lock-starvation
    fault). Returns a list of violation messages (empty = clean)."""
    specs = specs or build_job_specs()
    violations: list[str] = []
    for s in specs:
        if s.is_db_writer and s.executor_class in ("io", "heartbeat"):
            violations.append(f"{s.job_id}: DB writer on file-only executor {s.executor_class!r}")
    return violations


def validate_lane_separation(specs: list[JobBuildSpec] | None = None) -> list[str]:
    """PR8: derived/diagnostic/backfill DB writers must NOT share the live_db lane — so a
    calibration/skill/drift ETL can never starve live forecast/observation/market ingest behind
    the serial writer. Returns violations (empty = clean).

    The live_db lane is reserved for role in {live, settlement(non-UMA)}; everything else gets
    backfill_db / derived_db. This is enforced by executor_class_for(); this validator proves it.
    """
    from src.data.source_job_registry import JOB_REGISTRY

    specs = specs if specs is not None else build_job_specs()
    violations: list[str] = []
    # Iterate the PASSED specs (which may be owner-filtered), not the global registry —
    # otherwise an owner-filtered spec list KeyErrors on the other daemon's jobs (PR review #329 D).
    for s in specs:
        job = JOB_REGISTRY.get(s.job_id)
        if job is None:
            continue
        if s.executor_class == "live_db" and job.role in ("derived", "diagnostic", "backfill"):
            violations.append(
                f"{s.job_id}: role={job.role} on live_db lane — would starve live ingest behind ETL"
            )
    return violations
