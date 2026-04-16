"""Change-receipt checker family for topology_doctor."""

from __future__ import annotations

import json
from fnmatch import fnmatch
from typing import Any


def path_matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def load_change_receipt_schema(api: Any) -> dict[str, Any]:
    return api._load_yaml(api.CHANGE_RECEIPT_SCHEMA_PATH)


def effective_changed_files(api: Any, changed_files: list[str] | None = None) -> list[str]:
    return sorted(api._map_maintenance_changes(changed_files or []))


def high_risk_changed_files(api: Any, changed_files: list[str], schema: dict[str, Any]) -> list[str]:
    patterns = [str(pattern) for pattern in schema.get("high_risk_required_patterns") or []]
    return [path for path in changed_files if path_matches_any(path, patterns)]


def approved_receipt_path(path: str, schema: dict[str, Any]) -> bool:
    patterns = [str(pattern) for pattern in schema.get("approved_receipt_globs") or []]
    return path_matches_any(path, patterns)


def repo_path_like(value: str) -> bool:
    if not value or " " in value:
        return False
    if any(char in value for char in "*?[]"):
        return False
    return "/" in value or value.endswith((".md", ".yaml", ".yml", ".json", ".py", ".sh", ".sql", ".txt", ".xlsx", ".csv"))


def existing_repo_path(api: Any, value: str) -> bool:
    if not repo_path_like(value):
        return True
    return (api.ROOT / value).exists()


def validate_receipt_payload(
    api: Any,
    schema: dict[str, Any],
    receipt: dict[str, Any],
    receipt_path: str,
    changed_files: list[str],
) -> list[Any]:
    issues: list[Any] = []
    for field in schema.get("required_fields") or []:
        value = receipt.get(field)
        if api._metadata_missing(value):
            issues.append(
                api._issue(
                    "change_receipt_required_field_missing",
                    receipt_path,
                    f"receipt missing field {field!r}",
                )
            )

    route_source = receipt.get("route_source")
    allowed_route_sources = set(schema.get("allowed_route_sources") or [])
    if route_source and route_source not in allowed_route_sources:
        issues.append(
            api._issue(
                "change_receipt_invalid_route_source",
                receipt_path,
                f"route_source {route_source!r} is not allowed",
            )
        )
    route_evidence = [str(ref) for ref in (receipt.get("route_evidence") or [])]
    for ref in receipt.get("route_evidence") or []:
        ref = str(ref)
        if not existing_repo_path(api, ref):
            issues.append(
                api._issue(
                    "change_receipt_route_evidence_missing",
                    receipt_path,
                    f"route_evidence references missing path {ref}",
                )
            )
    allowed_evidence_globs = [
        str(pattern)
        for pattern in ((schema.get("route_evidence_globs_by_source") or {}).get(str(route_source)) or [])
    ]
    if route_source and route_evidence and allowed_evidence_globs:
        if not any(path_matches_any(ref, allowed_evidence_globs) for ref in route_evidence):
            issues.append(
                api._issue(
                    "change_receipt_route_evidence_invalid",
                    receipt_path,
                    f"route_evidence does not match allowed artifacts for route_source {route_source!r}",
                )
            )

    actual_changed = sorted(changed_files)
    receipt_changed = sorted(str(path) for path in (receipt.get("changed_files") or []))
    if not set(actual_changed).issubset(set(receipt_changed)):
        issues.append(
            api._issue(
                "change_receipt_changed_files_mismatch",
                receipt_path,
                f"receipt changed_files {receipt_changed!r} do not cover actual diff {actual_changed!r}",
            )
        )

    allowed_files = [str(pattern) for pattern in (receipt.get("allowed_files") or [])]
    forbidden_files = [str(pattern) for pattern in (receipt.get("forbidden_files") or [])]
    for path in actual_changed:
        if allowed_files and not path_matches_any(path, allowed_files):
            issues.append(
                api._issue(
                    "change_receipt_file_out_of_scope",
                    path,
                    f"changed file is outside receipt allowed_files ({receipt_path})",
                )
            )
        if forbidden_files and path_matches_any(path, forbidden_files):
            issues.append(
                api._issue(
                    "change_receipt_forbidden_file",
                    path,
                    f"changed file matches receipt forbidden_files ({receipt_path})",
                )
            )

    for ref in receipt.get("required_law") or []:
        ref = str(ref)
        if not existing_repo_path(api, ref):
            issues.append(
                api._issue(
                    "change_receipt_law_target_missing",
                    receipt_path,
                    f"required_law references missing path {ref}",
                )
            )
    for ref in receipt.get("tests_evidence") or []:
        ref = str(ref)
        if not existing_repo_path(api, ref):
            issues.append(
                api._issue(
                    "change_receipt_evidence_target_missing",
                    receipt_path,
                    f"tests_evidence references missing path {ref}",
                )
            )

    required_law = {str(ref) for ref in (receipt.get("required_law") or [])}
    for spec in schema.get("required_law_by_pattern") or []:
        pattern = str(spec.get("pattern") or "")
        requires_any = {str(ref) for ref in spec.get("requires_any") or []}
        if not pattern or not requires_any:
            continue
        touched = [path for path in actual_changed if path_matches_any(path, [pattern])]
        if touched and not (required_law & requires_any):
            issues.append(
                api._issue(
                    "change_receipt_inadequate_law_coverage",
                    receipt_path,
                    f"receipt required_law does not cover {pattern} changes; add one of {sorted(requires_any)}",
                )
            )
    return issues


def run_change_receipts(
    api: Any,
    changed_files: list[str] | None = None,
    receipt_path: str | None = None,
) -> Any:
    if not api.CHANGE_RECEIPT_SCHEMA_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "change_receipt_schema_missing",
                    "architecture/change_receipt_schema.yaml",
                    "change receipt schema is missing",
                )
            ],
        )

    schema = load_change_receipt_schema(api)
    actual_changed = effective_changed_files(api, changed_files)
    if not actual_changed and not receipt_path:
        return api.StrictResult(ok=True, issues=[])

    high_risk = high_risk_changed_files(api, actual_changed, schema)
    receipt_required = bool(high_risk)
    if not receipt_path:
        if not receipt_required:
            return api.StrictResult(ok=True, issues=[])
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "change_receipt_required",
                    "<receipt>",
                    f"high-risk changes require --receipt-path; affected files: {', '.join(high_risk)}",
                )
            ],
        )

    if not approved_receipt_path(receipt_path, schema):
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "change_receipt_invalid_path",
                    receipt_path,
                    "receipt path is not approved by architecture/change_receipt_schema.yaml",
                )
            ],
        )

    target = api.ROOT / receipt_path
    if not target.exists() or not target.is_file():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "change_receipt_missing",
                    receipt_path,
                    "receipt file does not exist",
                )
            ],
        )

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "change_receipt_invalid_json",
                    receipt_path,
                    f"receipt is not valid JSON: {exc}",
                )
            ],
        )

    if not isinstance(payload, dict):
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "change_receipt_invalid_json",
                    receipt_path,
                    "receipt JSON root must be an object",
                )
            ],
        )

    issues = validate_receipt_payload(api, schema, payload, receipt_path, actual_changed)
    return api.StrictResult(ok=not issues, issues=issues)
