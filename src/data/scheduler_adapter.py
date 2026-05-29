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

SCHEDULER_REGISTRY_FLAG = "ZEUS_SCHEDULER_REGISTRY_ENABLED"  # legacy flag (back-compat, see below)

# PR #329 review A/F: ONE data-collection mode flag, two values. The replacement (registry-built
# scheduler) is the DEFAULT; legacy (hand-coded add_job) is the rollback escape hatch (F). A
# single flag makes the two modes structurally mutually exclusive — you cannot be both.
DATA_COLLECTION_MODE_FLAG = "ZEUS_DATA_COLLECTION_MODE"     # "registry" (default) | "legacy"
LEGACY_DATA_COLLECTION_FLAG = "ZEUS_USE_LEGACY_DATA_COLLECTION"  # "1" -> force legacy (F rollback)
REGISTRY_MODE = "registry"
LEGACY_MODE = "legacy"


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes")


def assert_single_collection_mode() -> None:
    """F: registry and legacy modes are mutually exclusive — contradictory env fails fast at boot.

    Forbidden combinations (the operator must pick exactly one mode, not half-set two switches):
      * ZEUS_DATA_COLLECTION_MODE=registry  AND  ZEUS_USE_LEGACY_DATA_COLLECTION=1
      * ZEUS_DATA_COLLECTION_MODE=legacy    AND  ZEUS_SCHEDULER_REGISTRY_ENABLED=1
      * ZEUS_DATA_COLLECTION_MODE set to anything other than registry/legacy
    """
    raw_mode = os.environ.get(DATA_COLLECTION_MODE_FLAG)
    legacy_force = _truthy(os.environ.get(LEGACY_DATA_COLLECTION_FLAG, "0"))
    registry_legacy_flag = _truthy(os.environ.get(SCHEDULER_REGISTRY_FLAG, "0"))

    explicit_mode = raw_mode.strip().lower() if raw_mode is not None else None
    if explicit_mode is not None and explicit_mode not in (REGISTRY_MODE, LEGACY_MODE):
        raise RuntimeError(
            f"{DATA_COLLECTION_MODE_FLAG}={raw_mode!r} invalid; must be "
            f"{REGISTRY_MODE!r} or {LEGACY_MODE!r}."
        )
    # Contradiction only when the mode is EXPLICITLY set against an opposing flag — the rollback
    # flag ALONE (mode unset) is a valid way to select legacy, not a conflict.
    if explicit_mode == REGISTRY_MODE and legacy_force:
        raise RuntimeError(
            f"{DATA_COLLECTION_MODE_FLAG}=registry contradicts {LEGACY_DATA_COLLECTION_FLAG}=1 — "
            "registry and legacy schedulers are mutually exclusive; set exactly one."
        )
    if explicit_mode == LEGACY_MODE and registry_legacy_flag:
        raise RuntimeError(
            f"{DATA_COLLECTION_MODE_FLAG}=legacy contradicts {SCHEDULER_REGISTRY_FLAG}=1 — "
            "registry and legacy schedulers are mutually exclusive; set exactly one."
        )


def data_collection_mode() -> str:
    """The active data-collection mode: 'registry' (default) | 'legacy'.

    Default REGISTRY (PR #329 review A: the replacement is real + active, not deferred). The
    legacy hand-coded path is reachable only via an explicit opt-out (ZEUS_USE_LEGACY_DATA_
    COLLECTION=1 or ZEUS_DATA_COLLECTION_MODE=legacy). Raises on a contradictory combination."""
    assert_single_collection_mode()
    if _truthy(os.environ.get(LEGACY_DATA_COLLECTION_FLAG, "0")):
        return LEGACY_MODE
    return (os.environ.get(DATA_COLLECTION_MODE_FLAG, REGISTRY_MODE)).strip().lower()


def registry_scheduler_active() -> bool:
    """True when the registry-built scheduler is the active path (the new default)."""
    return data_collection_mode() == REGISTRY_MODE


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
        # COVER vs BUILD (PR #329 review A): the registry-built scheduler is for the two INGEST
        # daemons only. src/main (the trading daemon) is COVERED in the registry for inventory /
        # frontier / singleton, but its hand-coded scheduler is never rebuilt — so its jobs must
        # not be emitted as build specs. Long-running jobs (the user-WS thread) are not add_job'able
        # at all. Either would, if built, double-schedule a live producer.
        if j.owner_daemon == "main" or j.dispatch_kind == "long_running":
            continue
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
    fault). Returns a list of violation messages (empty = clean).

    PR #329 review (P2): compare the REGISTRY ``writes_db`` truth against the assigned executor
    class — NOT ``s.is_db_writer`` (which is ``executor_class.endswith('_db')``, so the prior
    ``is_db_writer and class in (io,heartbeat)`` was unreachable: a tautology that could never
    flag a mis-assignment). Now a registry job with ``writes_db=True`` that executor_class_for()
    wrongly routes to io/heartbeat is caught. Falls back to the class-derived flag only for an
    unknown job_id (no registry row to consult)."""
    from src.data.source_job_registry import JOB_REGISTRY

    specs = specs or build_job_specs()
    violations: list[str] = []
    for s in specs:
        job = JOB_REGISTRY.get(s.job_id)
        writes_db = job.writes_db if job is not None else s.is_db_writer
        if writes_db and s.executor_class in ("io", "heartbeat"):
            violations.append(
                f"{s.job_id}: writes_db job assigned file-only executor {s.executor_class!r}"
            )
    return violations


# Kwargs the registry build spec OWNS (executor lane + concurrency + id); stripped from a daemon
# job-spec dict so only the TRIGGER params remain for build_registry_scheduler (PR #329 A). Shared
# by both ingest daemons so the spec->job_defs derivation can never diverge between them.
REGISTRY_OWNED_KWARGS = frozenset({"id", "executor", "max_instances", "coalesce", "misfire_grace_time"})


def job_defs_from_specs(
    specs: "list[tuple] | tuple[tuple, ...]",
) -> dict[str, tuple]:
    """Derive registry job_defs (id -> (callable, trigger, trigger_kwargs)) from a daemon's
    (callable, trigger, kwargs) spec list — the SAME list its legacy add_job loop consumes, so
    trigger params can never diverge between the legacy and registry paths (one source, two
    consumers)."""
    out: dict[str, tuple] = {}
    for fn, trigger, kwargs in specs:
        job_id = str(kwargs["id"])
        trigger_kwargs = {k: v for k, v in kwargs.items() if k not in REGISTRY_OWNED_KWARGS}
        out[job_id] = (fn, trigger, trigger_kwargs)
    return out


def expected_registry_job_ids(owner_daemon: str, forecast_live_owner_env: str) -> set[str]:
    """The job ids a daemon MUST build from the registry (PR #329 A) — owner-filtered, with the
    OpenData ownership singleton resolved + long-running (non-add_job) jobs excluded.

    This is the contract the boot assertion checks the daemon's actual job_defs against: build the
    exact registry set, no more, no less. owner_gated OpenData jobs are included only on the daemon
    that currently owns OpenData (active_opendata_owner) — so the two ingest daemons never both
    schedule OpenData."""
    from src.data.source_job_registry import JOB_REGISTRY, active_opendata_owner

    opendata_owner = active_opendata_owner(forecast_live_owner_env)
    ids: set[str] = set()
    for j in JOB_REGISTRY.values():
        if j.owner_daemon != owner_daemon:
            continue
        if j.dispatch_kind == "long_running":   # threads are not add_job'able
            continue
        if j.owner_gated:
            # OpenData ownership asymmetry (PR #329 review #2): ingest_main runs REGARDLESS of
            # OpenData ownership (it does obs/market/settlement ingest always), so its OpenData
            # jobs are gated on being the active owner. forecast_live_daemon, by deployment, only
            # runs WHEN it owns OpenData (it is the dedicated owner daemon) — so its owner-gated
            # jobs are always part of its expected set. Gating forecast_live on the env var would
            # crash its boot assert (8 jobs built vs 2 expected) if ZEUS_FORECAST_LIVE_OWNER were
            # ever unset — a total forecast-collection outage one env var away. Encode the invariant
            # here instead of relying on the plist.
            if owner_daemon == "ingest_main" and opendata_owner != "ingest_main":
                continue                        # ingest_main is not the active OpenData owner
        ids.add(j.job_id)
    return ids


def registry_executor_pools() -> dict[str, object]:
    """The APScheduler executor pools for registry mode — one serial pool per lane (PR8 lane
    separation). live_db is serial (preserve the single-writer invariant) but separated from
    derived_db/backfill_db so an ETL/calibration job can never starve live ingest behind the lock.
    """
    from apscheduler.executors.pool import ThreadPoolExecutor

    return {
        "live_db": ThreadPoolExecutor(max_workers=1),
        "backfill_db": ThreadPoolExecutor(max_workers=1),
        "derived_db": ThreadPoolExecutor(max_workers=1),
        "io": ThreadPoolExecutor(max_workers=2),
        # heartbeat lane carries the 60s liveness file write AND the file-only diagnostics
        # (source_health probe ~minutes/network-bound, status rollup ~DB-reader). PR #329 review
        # #1: a single worker would let a slow probe delay the 60s heartbeat past the supervisor's
        # 30s restart-seed threshold -> false daemon-dead restart (the exact Fix #4 regression the
        # legacy 4-worker 'fast' pool prevented). Multi-worker so the liveness heartbeat always has
        # a free slot. These are file-only writers (no DB lock contention), so parallelism is safe.
        "heartbeat": ThreadPoolExecutor(max_workers=3),
    }


def build_registry_scheduler(
    scheduler: object,
    owner_daemon: str,
    job_defs: dict[str, tuple],
    *,
    forecast_live_owner_env: str,
    logger: object = None,
) -> list[str]:
    """Build a daemon's APScheduler jobs FROM the registry (PR #329 A). The daemon supplies only
    the parts a data-registry cannot hold — ``job_defs[job_id] = (callable, trigger, trigger_kwargs)``
    — and this routes the executor class + concurrency from the registry build spec.

    FAIL-FAST BOOT ASSERT (the safety net that makes registry-default safe): job_defs must cover
    EXACTLY the registry's expected set for this daemon. A missing or extra id raises, so the
    daemon refuses to boot a schedule that diverges from the registry rather than silently running
    the wrong job set. Returns the built job ids (also logged)."""
    expected = expected_registry_job_ids(owner_daemon, forecast_live_owner_env)
    have = set(job_defs)
    missing, extra = expected - have, have - expected
    if missing or extra:
        raise RuntimeError(
            f"registry scheduler job-set mismatch for {owner_daemon!r}: "
            f"missing_from_daemon={sorted(missing)} not_in_registry={sorted(extra)}. "
            "The daemon's job_defs must match the registry exactly (PR #329 A boot assert)."
        )
    specs = {s.job_id: s for s in build_job_specs(owner_daemon=owner_daemon)}
    built: list[str] = []
    for job_id in sorted(expected):
        spec = specs[job_id]
        fn, trigger, trigger_kwargs = job_defs[job_id]
        scheduler.add_job(  # type: ignore[attr-defined]
            fn, trigger, id=job_id, executor=spec.executor_class,
            max_instances=spec.max_instances, coalesce=spec.coalesce,
            misfire_grace_time=spec.misfire_grace_time, **dict(trigger_kwargs),
        )
        built.append(job_id)
    if logger is not None:
        logger.info(  # type: ignore[attr-defined]
            "registry scheduler built %d jobs for %s (executors=%s): %s",
            len(built), owner_daemon, sorted({specs[j].executor_class for j in built}), built,
        )
    return built


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
