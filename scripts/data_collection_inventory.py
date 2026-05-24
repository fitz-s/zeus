#!/usr/bin/env python3
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Advisory job inventory + --check (registry mirrors scheduler) + executor/scheduler dry-run previews.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR3);
#   operator spec §"Job registry"; src/data/source_job_registry.py.
"""Data-collection job inventory CLI — PR3 (advisory, read-only).

    python3 scripts/data_collection_inventory.py            # render the job matrix
    python3 scripts/data_collection_inventory.py --json
    python3 scripts/data_collection_inventory.py --check     # exit 1 if a scheduled add_job id
                                                             # is missing from the registry

``--check`` greps the two daemon modules for every add_job(id=...) and asserts each is declared
in source_job_registry.JOB_REGISTRY — so the registry can never silently fall out of date.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.source_job_registry import (  # noqa: E402
    JOB_REGISTRY,
    active_opendata_owner,
    assert_opendata_singleton,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
# Strict registry-MIRROR daemons: every scheduled add_job id here MUST be in JOB_REGISTRY.
# (These two daemons are also the ones that build-from-registry in registry mode.)
_DAEMON_FILES = (
    REPO_ROOT / "src" / "ingest_main.py",
    REPO_ROOT / "src" / "ingest" / "forecast_live_daemon.py",
)

# src/main.py is the TRADING daemon: its scheduler mixes data-collection jobs with trading-cycle
# and execution/chain jobs. PR #329 review B requires the registry to COVER its data-collection
# jobs — but the trading/chain jobs must NOT be forced into a data-collection registry. So we
# DECLARE the partition explicitly (advisor point 4): we do not ask the script to infer "is this
# data collection". Every scheduled add_job id in src/main must be in exactly one of these two
# sets; an id in neither fails --check, forcing a conscious classification of any new job.
_SRC_MAIN_FILE = REPO_ROOT / "src" / "main.py"

# Data-collection jobs in src/main — these MUST be registered in JOB_REGISTRY (operator B list).
# (user_ws_ingestor is a long-running thread, not an add_job, so it is registered but never
# appears in the scheduled-id scan.)
_SRC_MAIN_DATA_COLLECTION_JOB_IDS: frozenset[str] = frozenset({
    "market_discovery", "venue_heartbeat", "harvester", "wu_daily",
})

# Explicitly NON-data-collection src/main jobs: trading-cycle modes + execution/chain ops +
# the daemon's own heartbeat. Declared so --check can tell "known non-collection" apart from
# "new unclassified job". update_reaction_* uses an f-string id (not a static literal) so it is
# not extracted; it is a trading-cycle mode and intentionally not covered.
_SRC_MAIN_NON_COLLECTION_JOB_IDS: frozenset[str] = frozenset({
    "opening_hunt", "day0_capture", "imminent_open_capture",  # CycleRunner trading modes
    "heartbeat",                                              # daemon heartbeat (file-only)
    "redeem_submitter", "redeem_reconciler",                  # chain redemption ops
    "wrap_intent_creator", "wrap_submitter", "wrap_reconciler",  # chain wrap ops
    "deployment_freshness",                                   # deploy-freshness gate
})


# APScheduler keyword args that mark a dict as a job-spec (used to tell a real job-spec dict
# apart from any unrelated dict that happens to carry an "id" key). A dict with an "id" key plus
# at least one of these is an add_job(**kwargs) spec.
_SCHEDULER_SPEC_KEYS = frozenset({
    "trigger", "max_instances", "coalesce", "misfire_grace_time", "executor",
    "seconds", "minutes", "hours", "hour", "minute", "run_date", "next_run_time",
})


def _resolve_id(value: ast.expr, consts: dict[str, str]) -> str | None:
    """Resolve an id expression to a string: literal or module-level NAME constant."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.Name) and value.id in consts:
        return consts[value.id]
    return None


def _scheduled_job_ids() -> set[str]:
    """Every scheduled job id in the daemon modules, via AST (PR review #329 I + P1).

    Two real call shapes exist and BOTH must be harvested:

      1. Direct keyword: ``scheduler.add_job(func, trigger, id="...")`` (ingest_main). AST resolves
         the ``id`` keyword whether it is a string literal or a module-level constant; this also
         survives an id= placed after a nested call (the old regex stopped at the first ')').

      2. Unpacked job-spec dict: ``forecast_live_daemon`` builds ``(func, trigger, {"id": CONST,
         "max_instances": 1, ...})`` tuples and schedules them via ``add_job(func, trigger,
         **kwargs)`` (forecast_live_daemon.py:861). Here the id is a *dict key*, never an add_job
         keyword — so case (1) alone was BLIND to all eight forecast-live jobs, letting --check
         report clean coverage even if those ids drifted from JOB_REGISTRY (PR #329 review P1).
         We harvest the id from any dict literal carrying an ``"id"`` key plus ≥1 APScheduler
         spec key (so unrelated dicts with an "id" key are not mistaken for job specs).
    """
    return _scheduled_ids_in(_DAEMON_FILES)


def _scheduled_ids_in(files: tuple[Path, ...]) -> set[str]:
    """Extract scheduled add_job ids from the given files (both call shapes; see _scheduled_job_ids)."""
    ids: set[str] = set()
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        # module-level NAME = "literal" constant map (resolves id=CONSTANT references)
        consts: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        consts[tgt.id] = node.value.value
        for n in ast.walk(tree):
            # case 1: direct add_job(..., id=...)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "add_job":
                for kw in n.keywords:
                    if kw.arg == "id":
                        resolved = _resolve_id(kw.value, consts)
                        if resolved is not None:
                            ids.add(resolved)
            # case 2: job-spec dict LITERAL {"id": ..., <spec key>: ...} unpacked into add_job
            elif isinstance(n, ast.Dict):
                keys = {k.value for k in n.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if "id" not in keys or not (keys & _SCHEDULER_SPEC_KEYS):
                    continue
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value == "id":
                        resolved = _resolve_id(v, consts)
                        if resolved is not None:
                            ids.add(resolved)
            # case 3: job-spec dict CONSTRUCTOR dict(id="...", <spec key>=...) — the spec-list form
            # used by ingest_main's _ingest_main_job_specs (ast.Dict only matches {} literals, so
            # the constructor form must be handled separately or the mirror goes blind to it).
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "dict":
                kwarg_names = {kw.arg for kw in n.keywords if kw.arg}
                if "id" not in kwarg_names or not (kwarg_names & _SCHEDULER_SPEC_KEYS):
                    continue
                for kw in n.keywords:
                    if kw.arg == "id":
                        resolved = _resolve_id(kw.value, consts)
                        if resolved is not None:
                            ids.add(resolved)
    return ids


def _src_main_partition_violations() -> list[str]:
    """PR #329 review B: every scheduled add_job id in src/main.py must be classified as either a
    registered data-collection job or an explicit non-collection job. Returns violation strings:

      * UNCLASSIFIED: a scheduled id in neither set — a new src/main job nobody triaged. Forces a
        conscious decision (add to the registry as data-collection, or to the non-collection set).
      * UNREGISTERED: a declared data-collection id missing from JOB_REGISTRY — coverage gap.

    This is the antibody that keeps the registry honest about src/main once it claims to cover it.
    """
    scheduled = _scheduled_ids_in((_SRC_MAIN_FILE,))
    known = _SRC_MAIN_DATA_COLLECTION_JOB_IDS | _SRC_MAIN_NON_COLLECTION_JOB_IDS
    violations: list[str] = []
    for jid in sorted(scheduled - known):
        violations.append(
            f"UNCLASSIFIED src/main job {jid!r}: not in data-collection nor non-collection set "
            f"(classify it: register in JOB_REGISTRY, or add to _SRC_MAIN_NON_COLLECTION_JOB_IDS)"
        )
    for jid in sorted(_SRC_MAIN_DATA_COLLECTION_JOB_IDS):
        if jid not in JOB_REGISTRY:
            violations.append(f"UNREGISTERED src/main data-collection job {jid!r}: declared collection but missing from JOB_REGISTRY")
    return violations


def _orphan_callable_refs() -> list[str]:
    """Registry jobs whose callable_ref no longer exists as a `def` in its owner daemon.

    Reverse-direction guard: scheduled⊆registered alone would miss a registry job whose
    callable was deleted from the daemon. Jobs without a callable_ref (e.g. forecast_live
    wrapper jobs) are skipped — they have no single resolvable function name.
    """
    daemon_src = {
        "ingest_main": (REPO_ROOT / "src" / "ingest_main.py").read_text(encoding="utf-8"),
        "forecast_live_daemon": (REPO_ROOT / "src" / "ingest" / "forecast_live_daemon.py").read_text(encoding="utf-8"),
        "main": _SRC_MAIN_FILE.read_text(encoding="utf-8"),
    }
    orphans: list[str] = []
    for job in JOB_REGISTRY.values():
        ref = job.callable_ref
        if not ref:
            continue
        src = daemon_src.get(job.owner_daemon, "")
        if not re.search(rf"\bdef {re.escape(ref)}\s*\(", src):
            orphans.append(f"{job.job_id} -> {job.owner_daemon}.{ref} (callable not found)")
    return orphans


def cmd_check() -> int:
    scheduled = _scheduled_job_ids()
    registered = set(JOB_REGISTRY)

    missing = sorted(scheduled - registered)          # scheduled but unregistered (ingest daemons)
    orphans = _orphan_callable_refs()                 # registered but callable deleted
    src_main = _src_main_partition_violations()        # src/main collection/non-collection partition

    if missing or orphans or src_main:
        if missing:
            print(f"data_collection_inventory --check: {len(missing)} scheduled job(s) NOT in registry:")
            for j in missing:
                print(f"  MISSING: {j}")
        if orphans:
            print(f"data_collection_inventory --check: {len(orphans)} registry job(s) with ORPHAN callable_ref:")
            for o in orphans:
                print(f"  ORPHAN: {o}")
        if src_main:
            print(f"data_collection_inventory --check: {len(src_main)} src/main partition violation(s):")
            for v in src_main:
                print(f"  {v}")
        return 1

    extra = len(registered) - len(scheduled)          # startup-catch-up / direct-call / src-main / long-running jobs
    print(
        f"data_collection_inventory --check: OK — {len(scheduled)} ingest-daemon scheduled add_job id(s) "
        f"registered, plus {extra} direct-call/startup/src-main/long-running job(s) ({len(registered)} "
        f"total); src/main partition clean; no orphan callables."
    )
    return 0


def cmd_render(as_json: bool) -> int:
    specs = sorted(JOB_REGISTRY.values(), key=lambda j: (j.owner_daemon, j.role, j.job_id))
    if as_json:
        print(json.dumps([j.__dict__ for j in specs], indent=2))
        return 0
    header = f"{'JOB_ID':44} {'OWNER':22} {'ROLE':11} {'EXEC':8} {'DB':3} {'SOURCE'}"
    print(header)
    print("-" * len(header))
    for j in specs:
        print(f"{j.job_id:44.44} {j.owner_daemon:22.22} {j.role:11} {j.current_executor:8} "
              f"{'yes' if j.writes_db else '—':3} {j.source_id or ''}")
    return 0


def cmd_scheduler_preview(forecast_live_owner: str) -> int:
    """Dry-run: show which jobs would be ACTIVE under a given ZEUS_FORECAST_LIVE_OWNER value.

    owner_gated OpenData jobs are active only on the resolved owner daemon. Read-only.
    """
    owner = active_opendata_owner(forecast_live_owner)
    try:
        assert_opendata_singleton(forecast_live_owner)
        singleton = "OK"
    except RuntimeError as exc:
        singleton = f"VIOLATION: {exc}"
    print(f"ZEUS_FORECAST_LIVE_OWNER={forecast_live_owner!r} -> OpenData owner: {owner}")
    print(f"OpenData singleton: {singleton}")
    print()
    header = f"{'ACTIVE':7} {'JOB_ID':44} {'OWNER':22} {'ROLE'}"
    print(header)
    print("-" * len(header))
    for j in sorted(JOB_REGISTRY.values(), key=lambda x: (x.owner_daemon, x.job_id)):
        # owner_gated jobs are active only on the resolved owner daemon; others always active.
        active = (j.owner_daemon == owner) if j.owner_gated else True
        print(f"{'yes' if active else 'no':7} {j.job_id:44.44} {j.owner_daemon:22.22} {j.role}")
    return 0


def cmd_executor_plan() -> int:
    """Dry-run: show the executor class each job would get under the registry-built scheduler
    (PR6). Read-only; the live daemon is unaffected unless ZEUS_SCHEDULER_REGISTRY_ENABLED=1."""
    from src.data.scheduler_adapter import (
        build_job_specs,
        scheduler_registry_enabled,
        validate_executor_assignment,
    )

    specs = build_job_specs()
    print(f"registry-built scheduler enabled: {scheduler_registry_enabled()} (flag default off)")
    violations = validate_executor_assignment(specs)
    print(f"executor-assignment violations: {len(violations)}")
    for v in violations:
        print(f"  VIOLATION: {v}")
    print()
    header = f"{'JOB_ID':44} {'EXECUTOR':12} {'INST':>4} {'COALESCE':9} OWNER"
    print(header)
    print("-" * len(header))
    for s in sorted(specs, key=lambda x: (x.executor_class, x.job_id)):
        print(f"{s.job_id:44.44} {s.executor_class:12} {s.max_instances:>4} "
              f"{str(s.coalesce):9} {s.owner_daemon}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Data-collection job inventory (advisory).")
    p.add_argument("--check", action="store_true", help="fail if a scheduled job is unregistered")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--scheduler-preview", action="store_true",
                   help="dry-run: show jobs active under --forecast-live-owner")
    p.add_argument("--executor-plan", action="store_true",
                   help="dry-run: show registry-assigned executor class per job (PR6)")
    p.add_argument("--forecast-live-owner", default="ingest_main",
                   help="ZEUS_FORECAST_LIVE_OWNER value to preview (default: ingest_main)")
    args = p.parse_args(argv)
    if args.check:
        return cmd_check()
    if args.scheduler_preview:
        return cmd_scheduler_preview(args.forecast_live_owner)
    if args.executor_plan:
        return cmd_executor_plan()
    return cmd_render(args.json)


if __name__ == "__main__":
    sys.exit(main())
