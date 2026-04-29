"""Compiled closeout lane for topology_doctor."""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-16; last_reused=2026-04-16
# Purpose: Combine changed-file topology lanes into one closeout result.
# Reuse: Keep lane additions scoped and parity-tested through tests/test_topology_doctor.py.

from __future__ import annotations

import json
import subprocess
from datetime import date
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


def lane_summary(api: Any, result: Any, *, issue_schema_version: str = "1") -> dict[str, Any]:
    return {
        "ok": result.ok,
        "issue_count": len(result.issues),
        "blocking_count": len([issue for issue in result.issues if issue.severity == "error"]),
        "warning_count": len([issue for issue in result.issues if issue.severity == "warning"]),
        "issues": [api._issue_to_json(issue, issue_schema_version) for issue in result.issues],
    }


def global_health_summary(result: Any) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "issue_count": len(result.issues),
        "blocking_count": len([issue for issue in result.issues if issue.severity == "error"]),
        "warning_count": len([issue for issue in result.issues if issue.severity == "warning"]),
    }


def normalized_issue_path(path: str) -> str:
    if path.startswith("<"):
        return path
    return path.split(":", 1)[0].rstrip("/")


def issue_in_scope(issue_path: str, changed_files: list[str]) -> bool:
    normalized = normalized_issue_path(issue_path)
    if normalized.startswith("<"):
        return False
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


def receipt_payload(api: Any, receipt_path: str | None) -> dict[str, Any]:
    if not receipt_path:
        return {}
    target = Path(receipt_path)
    if not target.is_absolute():
        target = api.ROOT / receipt_path
    if not target.exists() or not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_iso_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _warning_deferral_matches_issue(deferral: dict[str, str], issue: dict[str, Any]) -> bool:
    if deferral["code"] != str(issue.get("code") or ""):
        return False
    issue_path = normalized_issue_path(str(issue.get("path") or ""))
    deferral_path = normalized_issue_path(deferral["path"])
    return issue_path == deferral_path or issue_in_scope(issue_path, [deferral_path])


def warning_lifecycle(
    api: Any,
    *,
    receipt_path: str | None,
    changed_files: list[str],
    claims: list[str],
    scoped_warning_issues: list[dict[str, Any]],
    global_warning_issues: list[dict[str, Any]],
    issue_schema_version: str = "1",
) -> dict[str, Any]:
    payload = receipt_payload(api, receipt_path)
    raw_deferrals = payload.get("warning_deferrals") or []
    result: dict[str, Any] = {
        "authority_status": "packet_local_warning_lifecycle_not_authority",
        "receipt_path": receipt_path,
        "deferrals": [],
        "expired_promotions": [],
        "invalid_deferrals": [],
        "blocking_issues": [],
    }
    if not raw_deferrals:
        return result
    if not isinstance(raw_deferrals, list):
        issue = api.blocking(
            "warning_deferral_invalid",
            receipt_path or "<receipt>",
            "warning_deferrals must be a list",
            lane="warning_lifecycle",
            scope="changed_file",
            owner_manifest=receipt_path,
            repair_kind="none",
            authority_status="evidence",
        )
        result["invalid_deferrals"].append({"index": None, "reason": issue.message})
        result["blocking_issues"].append({"lane": "warning_lifecycle", **api._issue_to_json(issue, issue_schema_version)})
        return result

    today = date.today()
    claim_set = set(claims or [])
    for index, entry in enumerate(raw_deferrals):
        if not isinstance(entry, dict):
            issue = api.blocking(
                "warning_deferral_invalid",
                receipt_path or "<receipt>",
                f"warning_deferrals[{index}] must be an object",
                lane="warning_lifecycle",
                scope="changed_file",
                owner_manifest=receipt_path,
                repair_kind="none",
                authority_status="evidence",
            )
            result["invalid_deferrals"].append({"index": index, "reason": issue.message})
            result["blocking_issues"].append({"lane": "warning_lifecycle", **api._issue_to_json(issue, issue_schema_version)})
            continue

        deferral = {
            "code": str(entry.get("code") or ""),
            "path": str(entry.get("path") or ""),
            "state": str(entry.get("state") or "acknowledged"),
            "scope": str(entry.get("scope") or "changed_file"),
            "claim": str(entry.get("claim") or ""),
            "owner": str(entry.get("owner") or ""),
            "invalidation_condition": str(entry.get("invalidation_condition") or ""),
            "deferred_until": str(entry.get("deferred_until") or ""),
            "expires_at": str(entry.get("expires_at") or ""),
        }
        result["deferrals"].append(deferral)

        invalid_reasons = []
        for field in ("code", "path", "owner", "invalidation_condition"):
            if not deferral[field]:
                invalid_reasons.append(f"missing {field}")
        if deferral["state"] not in api.ISSUE_LIFECYCLE_STATES:
            invalid_reasons.append(f"invalid state {deferral['state']!r}")
        if deferral["scope"] not in {"changed_file", "global", "claim"}:
            invalid_reasons.append(f"invalid scope {deferral['scope']!r}")
        if deferral["state"] != "retired" and not (deferral["expires_at"] or deferral["deferred_until"]):
            invalid_reasons.append("missing expires_at or deferred_until")
        expiry = _parse_iso_date(deferral["expires_at"] or deferral["deferred_until"])
        if (deferral["expires_at"] or deferral["deferred_until"]) and expiry is None:
            invalid_reasons.append("invalid ISO date")
        if invalid_reasons:
            issue = api.blocking(
                "warning_deferral_invalid",
                receipt_path or "<receipt>",
                f"warning_deferrals[{index}] invalid: {', '.join(invalid_reasons)}",
                lane="warning_lifecycle",
                scope=deferral["scope"] if deferral["scope"] in {"changed_file", "global", "claim"} else "changed_file",
                owner_manifest=receipt_path,
                repair_kind="none",
                authority_status="evidence",
            )
            result["invalid_deferrals"].append({"index": index, "reason": issue.message})
            result["blocking_issues"].append({"lane": "warning_lifecycle", **api._issue_to_json(issue, issue_schema_version)})
            continue
        if deferral["state"] == "retired" or expiry is None or today <= expiry:
            continue
        if deferral["claim"] and deferral["claim"] not in claim_set:
            continue
        if deferral["scope"] in {"global", "claim"} and not deferral["claim"]:
            continue

        issue_pool = global_warning_issues if deferral["scope"] in {"global", "claim"} else scoped_warning_issues
        if not any(_warning_deferral_matches_issue(deferral, issue) for issue in issue_pool):
            continue
        if deferral["scope"] == "changed_file" and not issue_in_scope(deferral["path"], changed_files):
            continue

        promoted = api.blocking(
            "warning_deferral_expired",
            deferral["path"],
            f"expired warning deferral for {deferral['code']} owned by {deferral['owner']}",
            lane="warning_lifecycle",
            scope=deferral["scope"],
            owner_manifest=receipt_path,
            repair_kind="none",
            lifecycle_state="promoted_to_blocker",
            lifecycle_owner=deferral["owner"],
            deferred_until=deferral["deferred_until"] or None,
            expires_at=deferral["expires_at"] or None,
            invalidation_condition=deferral["invalidation_condition"],
            authority_status="evidence",
        )
        promoted_payload = {"lane": "warning_lifecycle", **api._issue_to_json(promoted, "2")}
        result["expired_promotions"].append(promoted_payload)
        result["blocking_issues"].append(
            {"lane": "warning_lifecycle", **api._issue_to_json(promoted, issue_schema_version)}
        )
    return result


def scoped_result(api: Any, result: Any, changed_files: list[str]) -> Any:
    issues = [issue for issue in result.issues if issue_in_scope(issue.path, changed_files)]
    blocking = [
        issue
        for issue in issues
        if issue.severity == "error" and api._issue_blocks_mode(api._issue_to_json(issue, "2"), "closeout")
    ]
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


def ensure_global_health_lanes(api: Any, unscoped_lanes: dict[str, Any]) -> None:
    runners = {
        "docs": api.run_docs,
        "source": api.run_source,
        "tests": api.run_tests,
        "scripts": api.run_scripts,
        "data_rebuild": api.run_data_rebuild,
        "context_budget": api.run_context_budget,
    }
    for lane, runner in runners.items():
        if lane not in unscoped_lanes:
            unscoped_lanes[lane] = runner()


def run_closeout(
    api: Any,
    *,
    changed_files: list[str] | None = None,
    plan_evidence: str | None = None,
    work_record_path: str | None = None,
    receipt_path: str | None = None,
    claims: list[str] | None = None,
    issue_schema_version: str = "1",
) -> dict[str, Any]:
    actual_changed = effective_changed_files(api, changed_files, receipt_path=receipt_path)
    risk_tier = api._runtime_risk_tier(actual_changed, task="closeout", write_intent="edit")
    selected = selected_lanes(api, actual_changed)
    unscoped_lanes: dict[str, Any] = {
        "planning_lock": api.run_planning_lock(actual_changed, plan_evidence),
        "work_record": api.run_work_record(actual_changed, work_record_path),
        "change_receipts": api.run_change_receipts(actual_changed, receipt_path),
        "map_maintenance": api.run_map_maintenance(actual_changed, mode="closeout"),
        "artifact_lifecycle": api.run_artifact_lifecycle(),
        "naming_conventions": api.run_naming_conventions(),
        "freshness_metadata": api.run_freshness_metadata(actual_changed),
        "code_review_graph": api.run_code_review_graph_status(actual_changed),
    }
    lanes: dict[str, Any] = {
        "planning_lock": unscoped_lanes["planning_lock"],
        "work_record": unscoped_lanes["work_record"],
        "change_receipts": unscoped_lanes["change_receipts"],
        "map_maintenance": unscoped_lanes["map_maintenance"],
        "artifact_lifecycle": scoped_result(api, unscoped_lanes["artifact_lifecycle"], actual_changed),
        "naming_conventions": scoped_result(api, unscoped_lanes["naming_conventions"], actual_changed),
        "freshness_metadata": unscoped_lanes["freshness_metadata"],
        "code_review_graph": scoped_result(api, unscoped_lanes["code_review_graph"], actual_changed),
    }
    if selected["docs"]:
        unscoped_lanes["docs"] = api.run_docs()
        lanes["docs"] = scoped_result(api, unscoped_lanes["docs"], actual_changed)
    if selected["source"]:
        unscoped_lanes["source"] = api.run_source()
        lanes["source"] = scoped_result(api, unscoped_lanes["source"], actual_changed)
    if selected["tests"]:
        unscoped_lanes["tests"] = api.run_tests()
        lanes["tests"] = scoped_result(api, unscoped_lanes["tests"], actual_changed)
    if selected["scripts"]:
        unscoped_lanes["scripts"] = api.run_scripts()
        lanes["scripts"] = scoped_result(api, unscoped_lanes["scripts"], actual_changed)
    if selected["data_rebuild"]:
        unscoped_lanes["data_rebuild"] = api.run_data_rebuild()
        lanes["data_rebuild"] = scoped_result(api, unscoped_lanes["data_rebuild"], actual_changed)
    if selected["context_budget"]:
        unscoped_lanes["context_budget"] = api.run_context_budget()
        if "architecture/context_budget.yaml" in actual_changed:
            lanes["context_budget"] = unscoped_lanes["context_budget"]
        else:
            lanes["context_budget"] = scoped_result(api, unscoped_lanes["context_budget"], actual_changed)
    ensure_global_health_lanes(api, unscoped_lanes)

    blocking_issues = [
        {"lane": lane, **api._issue_to_json(issue, issue_schema_version)}
        for lane, result in lanes.items()
        for issue in result.issues
        if issue.severity == "error" and api._issue_blocks_mode(api._issue_to_json(issue, "2"), "closeout")
    ]
    warning_issues = [
        {"lane": lane, **api._issue_to_json(issue, issue_schema_version)}
        for lane, result in lanes.items()
        for issue in result.issues
        if issue.severity == "warning"
    ]
    global_warning_issues = [
        {"lane": lane, **api._issue_to_json(issue, issue_schema_version)}
        for lane, result in unscoped_lanes.items()
        for issue in result.issues
        if issue.severity == "warning"
    ]
    claim_evaluation = api.build_runtime_claim_evaluation(
        claims,
        graph_result=unscoped_lanes["code_review_graph"],
        global_health={lane: global_health_summary(result) for lane, result in unscoped_lanes.items()},
        closeout_ok=not blocking_issues,
    )
    claim_blocking_issues = api.runtime_claim_issues(claim_evaluation)
    blocking_issues = blocking_issues + claim_blocking_issues
    warning_lifecycle_result = warning_lifecycle(
        api,
        receipt_path=receipt_path,
        changed_files=actual_changed,
        claims=claims or [],
        scoped_warning_issues=warning_issues,
        global_warning_issues=global_warning_issues,
        issue_schema_version=issue_schema_version,
    )
    blocking_issues = blocking_issues + warning_lifecycle_result["blocking_issues"]
    compiled = api.build_compiled_topology()
    telemetry = compiled.get("telemetry") or {}
    return {
        "ok": not blocking_issues,
        "authority_status": "generated_closeout_not_authority",
        "changed_files": actual_changed,
        "risk_tier": risk_tier,
        "gate_budget": api.RUNTIME_RISK_GATE_BUDGETS[risk_tier],
        "claim_scope": {
            "graph": "code_review_graph lane blocks closeout only for changed graph paths or explicit graph-impact claims",
            "repo_health": "global_health is reported separately from changed-file closeout",
            "receipts": "receipt/work-record/planning-lock remain blocking for governed changed files",
        },
        "migration_notes": {
            "runtime_path_first": "python scripts/topology_doctor.py runtime --task '<task>' --files <files> --intent '<intent>' --task-class <class> --write-intent <intent>",
            "legacy_commands_supported": ["--navigation", "digest", "closeout"],
            "deprecation_policy": "do not emit deprecation warnings until two packet receipts record successful runtime-command usage",
            "default_read_docs": "no new default-read docs are required for normal future tasks",
        },
        "claim_evaluation": claim_evaluation,
        "claims_evaluated": claim_evaluation["evaluated"],
        "claims_blocked": claim_evaluation["blocked"],
        "claims_advisory": claim_evaluation["advisory"],
        "warning_lifecycle": warning_lifecycle_result,
        "selected_lanes": selected,
        "lanes": {lane: lane_summary(api, result, issue_schema_version=issue_schema_version) for lane, result in lanes.items()},
        "global_health": {lane: global_health_summary(result) for lane, result in unscoped_lanes.items()},
        "telemetry": telemetry,
        "blocking_issues": blocking_issues,
        "warning_issues": warning_issues,
    }
