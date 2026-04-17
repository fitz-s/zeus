"""Compiled closeout lane for topology_doctor."""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-16; last_reused=2026-04-16
# Purpose: Combine changed-file topology lanes into one closeout result.
# Reuse: Keep lane additions scoped and parity-tested through tests/test_topology_doctor.py.

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def staged_changed_files(api: Any) -> list[str]:
    proc = api.subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACDMR"],
        cwd=api.ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(line for line in proc.stdout.splitlines() if line)


def receipt_changed_files(receipt_path: str | None) -> list[str]:
    if not receipt_path:
        return []
    target = Path(receipt_path)
    if not target.exists() or not target.is_file():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return sorted(str(path) for path in (payload.get("changed_files") or []))


def effective_changed_files(
    api: Any,
    changed_files: list[str] | None = None,
    *,
    receipt_path: str | None = None,
) -> list[str]:
    if changed_files:
        return sorted(api._map_maintenance_changes(changed_files))
    try:
        staged = staged_changed_files(api)
    except subprocess.CalledProcessError:
        staged = []
    if staged:
        return sorted(api._map_maintenance_changes(staged))
    git_changes = sorted(api._map_maintenance_changes([]))
    if git_changes:
        return git_changes
    return receipt_changed_files(receipt_path)


def changed_files_touch(changed_files: list[str], patterns: tuple[str, ...]) -> bool:
    return any(path.startswith(pattern) for path in changed_files for pattern in patterns)


def lane_summary(api: Any, result: Any) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "issue_count": len(result.issues),
        "blocking_count": len([issue for issue in result.issues if issue.severity == "error"]),
        "warning_count": len([issue for issue in result.issues if issue.severity == "warning"]),
        "issues": [api.asdict(issue) for issue in result.issues],
    }


def normalized_issue_path(path: str) -> str:
    if path.startswith("<"):
        return path
    return path.split(":", 1)[0].rstrip("/")


def issue_in_scope(issue_path: str, changed_files: list[str]) -> bool:
    normalized = normalized_issue_path(issue_path)
    if normalized.startswith("<"):
        return True
    for path in changed_files:
        scoped = path.rstrip("/")
        if normalized == scoped:
            return True
        if issue_path.startswith(f"{scoped}:"):
            return True
        if scoped.startswith(f"{normalized}/"):
            return True
        if normalized.startswith(f"{scoped}/"):
            return True
    return False


def scoped_result(api: Any, result: Any, changed_files: list[str]) -> Any:
    issues = [issue for issue in result.issues if issue_in_scope(issue.path, changed_files)]
    blocking = [issue for issue in issues if issue.severity == "error"]
    return api.StrictResult(ok=not blocking, issues=issues)


def selected_lanes(api: Any, changed_files: list[str]) -> dict[str, Any]:
    docs_related = (
        changed_files_touch(changed_files, ("docs/",))
        or any(
            path in changed_files
            for path in (
                "AGENTS.md",
                "workspace_map.md",
                "architecture/topology.yaml",
                "architecture/artifact_lifecycle.yaml",
                "architecture/map_maintenance.yaml",
                "architecture/change_receipt_schema.yaml",
                "architecture/context_budget.yaml",
            )
        )
    )
    source_related = changed_files_touch(changed_files, ("src/",)) or "architecture/source_rationale.yaml" in changed_files
    tests_related = changed_files_touch(changed_files, ("tests/",)) or any(
        path in changed_files for path in ("architecture/test_topology.yaml", "pytest.ini")
    )
    scripts_related = changed_files_touch(changed_files, ("scripts/",)) or "architecture/script_manifest.yaml" in changed_files
    data_rebuild_related = any(
        path in changed_files
        for path in (
            "architecture/data_rebuild_topology.yaml",
            "scripts/rebuild_calibration_pairs_canonical.py",
            "scripts/rebuild_settlements.py",
            "scripts/refit_platt.py",
        )
    )
    context_budget_related = docs_related or any(
        path in changed_files
        for path in (
            "architecture/context_budget.yaml",
            "docs/README.md",
            "docs/AGENTS.md",
        )
    )
    return {
        "docs": docs_related,
        "source": source_related,
        "tests": tests_related,
        "scripts": scripts_related,
        "data_rebuild": data_rebuild_related,
        "context_budget": context_budget_related,
    }


def run_closeout(
    api: Any,
    *,
    changed_files: list[str] | None = None,
    plan_evidence: str | None = None,
    work_record_path: str | None = None,
    receipt_path: str | None = None,
) -> dict[str, Any]:
    actual_changed = effective_changed_files(api, changed_files, receipt_path=receipt_path)
    selected = selected_lanes(api, actual_changed)
    lanes: dict[str, Any] = {
        "planning_lock": api.run_planning_lock(actual_changed, plan_evidence),
        "work_record": api.run_work_record(actual_changed, work_record_path),
        "change_receipts": api.run_change_receipts(actual_changed, receipt_path),
        "map_maintenance": api.run_map_maintenance(actual_changed, mode="closeout"),
        "artifact_lifecycle": api.run_artifact_lifecycle(),
        "freshness_metadata": api.run_freshness_metadata(actual_changed),
    }
    if selected["docs"]:
        lanes["docs"] = scoped_result(api, api.run_docs(), actual_changed)
    if selected["source"]:
        lanes["source"] = scoped_result(api, api.run_source(), actual_changed)
    if selected["tests"]:
        lanes["tests"] = scoped_result(api, api.run_tests(), actual_changed)
    if selected["scripts"]:
        lanes["scripts"] = scoped_result(api, api.run_scripts(), actual_changed)
    if selected["data_rebuild"]:
        lanes["data_rebuild"] = scoped_result(api, api.run_data_rebuild(), actual_changed)
    if selected["context_budget"]:
        if "architecture/context_budget.yaml" in actual_changed:
            lanes["context_budget"] = api.run_context_budget()
        else:
            lanes["context_budget"] = scoped_result(api, api.run_context_budget(), actual_changed)

    blocking_issues = [
        {"lane": lane, **api.asdict(issue)}
        for lane, result in lanes.items()
        for issue in result.issues
        if issue.severity == "error"
    ]
    warning_issues = [
        {"lane": lane, **api.asdict(issue)}
        for lane, result in lanes.items()
        for issue in result.issues
        if issue.severity == "warning"
    ]
    compiled = api.build_compiled_topology()
    telemetry = compiled.get("telemetry") or {}
    return {
        "ok": not blocking_issues,
        "authority_status": "generated_closeout_not_authority",
        "changed_files": actual_changed,
        "selected_lanes": selected,
        "lanes": {lane: lane_summary(api, result) for lane, result in lanes.items()},
        "telemetry": telemetry,
        "blocking_issues": blocking_issues,
        "warning_issues": warning_issues,
    }
