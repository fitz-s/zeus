"""Artifact lifecycle and work-record checker family for topology_doctor."""

from __future__ import annotations

import re
import subprocess
from fnmatch import fnmatch
from typing import Any


def path_matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def artifact_record_contract(api: Any) -> dict[str, Any]:
    return api.load_artifact_lifecycle().get("record_contract") or {}


def approved_work_record_path(api: Any, path: str, contract: dict[str, Any] | None = None) -> bool:
    contract = contract or artifact_record_contract(api)
    return path_matches_any(path, [str(pattern) for pattern in contract.get("approved_record_globs") or []])


def record_exempt_path(api: Any, path: str, contract: dict[str, Any] | None = None) -> bool:
    contract = contract or artifact_record_contract(api)
    if approved_work_record_path(api, path, contract):
        return True
    return path_matches_any(path, [str(pattern) for pattern in contract.get("exempt_path_globs") or []])


def run_artifact_lifecycle(api: Any) -> Any:
    if not api.ARTIFACT_LIFECYCLE_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "artifact_lifecycle_manifest_missing",
                    "architecture/artifact_lifecycle.yaml",
                    "artifact lifecycle manifest is missing",
                )
            ],
        )
    manifest = api.load_artifact_lifecycle()
    issues: list[Any] = []
    required = manifest.get("required_artifact_class_fields") or []
    required_liminal = manifest.get("required_liminal_artifact_fields") or []
    liminal_roles = set(manifest.get("allowed_liminal_artifact_roles") or [])
    route_classes = set(manifest.get("allowed_route_classes") or [])
    contract = manifest.get("record_contract") or {}
    for field in ("minimum_fields", "approved_record_globs", "exempt_path_globs"):
        if api._metadata_missing(contract.get(field)):
            issues.append(
                api._issue(
                    "artifact_lifecycle_record_contract_missing",
                    "architecture/artifact_lifecycle.yaml:record_contract",
                    f"missing {field}",
                )
            )
    classes: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(manifest.get("artifact_classes") or []):
        class_id = str(item.get("id") or f"artifact_class[{idx}]")
        path = f"architecture/artifact_lifecycle.yaml:{class_id}"
        if class_id in classes:
            issues.append(api._issue("artifact_lifecycle_duplicate_class", path, "duplicate artifact class id"))
        classes[class_id] = item
        for field in required:
            if api._metadata_missing(item.get(field)):
                issues.append(api._issue("artifact_lifecycle_required_field_missing", path, f"missing {field}"))

    for idx, rule in enumerate(manifest.get("classification_rules") or []):
        rule_id = str(rule.get("id") or f"classification_rule[{idx}]")
        path = f"architecture/artifact_lifecycle.yaml:{rule_id}"
        if api._metadata_missing(rule.get("path_globs")):
            issues.append(api._issue("artifact_lifecycle_rule_invalid", path, "missing path_globs"))
        artifact_class = str(rule.get("artifact_class") or "")
        if artifact_class not in classes:
            issues.append(
                api._issue(
                    "artifact_lifecycle_unknown_class",
                    path,
                    f"unknown artifact_class {artifact_class!r}",
                )
            )
        if api._metadata_missing(rule.get("lifecycle")):
            issues.append(api._issue("artifact_lifecycle_rule_invalid", path, "missing lifecycle"))

    for idx, item in enumerate(manifest.get("liminal_artifacts") or []):
        item_path = str(item.get("path") or f"liminal_artifact[{idx}]")
        path = f"architecture/artifact_lifecycle.yaml:{item_path}"
        for field in required_liminal:
            if api._metadata_missing(item.get(field)):
                issues.append(api._issue("artifact_lifecycle_liminal_field_missing", path, f"missing {field}"))
        if item.get("artifact_role") not in liminal_roles:
            issues.append(
                api._issue(
                    "artifact_lifecycle_liminal_role_invalid",
                    path,
                    f"invalid artifact_role {item.get('artifact_role')!r}",
                )
            )
        if item.get("route_class") not in route_classes:
            issues.append(
                api._issue(
                    "artifact_lifecycle_liminal_route_invalid",
                    path,
                    f"invalid route_class {item.get('route_class')!r}",
                )
            )
        if item.get("path") and not (api.ROOT / item_path).exists():
            issues.append(
                api._issue(
                    "artifact_lifecycle_liminal_path_missing",
                    path,
                    "liminal artifact path does not exist",
                )
            )
    return api.StrictResult(ok=not issues, issues=issues)


def record_text_has_field(text: str, field: str) -> bool:
    return re.search(rf"^{re.escape(field)}:\s*\S", text, re.MULTILINE) is not None


def run_work_record(api: Any, changed_files: list[str] | None = None, record_path: str | None = None) -> Any:
    if not api.ARTIFACT_LIFECYCLE_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "artifact_lifecycle_manifest_missing",
                    "architecture/artifact_lifecycle.yaml",
                    "artifact lifecycle manifest is missing",
                )
            ],
        )
    contract = artifact_record_contract(api)
    try:
        changes = api._map_maintenance_changes(changed_files or [])
    except subprocess.CalledProcessError as exc:
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "work_record_git_status_failed",
                    "<git-status>",
                    f"could not read git status: {exc}",
                )
            ],
        )

    substantive = sorted(
        path for path in changes
        if not record_exempt_path(api, path, contract)
    )
    if not substantive:
        return api.StrictResult(ok=True, issues=[])

    if not record_path:
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "work_record_required",
                    "<work-record>",
                    "repo-changing work requires a short work record; pass --work-record-path",
                )
            ],
        )
    if not approved_work_record_path(api, record_path, contract):
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "work_record_invalid_path",
                    record_path,
                    "work record path is not approved by artifact_lifecycle.yaml",
                )
            ],
        )
    target = api.ROOT / record_path
    if not target.exists() or not target.is_file():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "work_record_missing",
                    record_path,
                    "work record file does not exist",
                )
            ],
        )
    text = target.read_text(encoding="utf-8", errors="ignore")
    issues = [
        api._issue(
            "work_record_field_missing",
            record_path,
            f"work record missing field {field!r}",
        )
        for field in contract.get("minimum_fields") or []
        if not record_text_has_field(text, str(field))
    ]
    return api.StrictResult(ok=not issues, issues=issues)
