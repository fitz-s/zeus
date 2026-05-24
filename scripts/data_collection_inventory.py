#!/usr/bin/env python3
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
_DAEMON_FILES = (
    REPO_ROOT / "src" / "ingest_main.py",
    REPO_ROOT / "src" / "ingest" / "forecast_live_daemon.py",
)


def _scheduled_job_ids() -> set[str]:
    """Every id=... passed to .add_job(...) in the daemon modules (incl. ID constants resolved)."""
    ids: set[str] = set()
    for f in _DAEMON_FILES:
        text = f.read_text(encoding="utf-8")
        # direct string ids on add_job
        for m in re.finditer(r"\.add_job\([^)]*?id\s*=\s*[\"']([^\"']+)[\"']", text, re.DOTALL):
            ids.add(m.group(1))
        # forecast_live uses ID constants — resolve `NAME = "forecast_live_..."`
        for m in re.finditer(r'^[A-Z0-9_]*JOB_ID\s*=\s*"([^"]+)"', text, re.MULTILINE):
            ids.add(m.group(1))
    return ids


def cmd_check() -> int:
    scheduled = _scheduled_job_ids()
    registered = set(JOB_REGISTRY)
    missing = sorted(scheduled - registered)
    if missing:
        print(f"data_collection_inventory --check: {len(missing)} scheduled job(s) NOT in registry:")
        for j in missing:
            print(f"  MISSING: {j}")
        return 1
    print(f"data_collection_inventory --check: OK — all {len(scheduled)} scheduled job ids registered.")
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
