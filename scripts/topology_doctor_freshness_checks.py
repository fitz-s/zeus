"""Freshness metadata checker family for topology_doctor."""
# Lifecycle: created=2026-04-16; last_reviewed=2026-04-16; last_reused=never
# Purpose: Enforce changed-file freshness headers and validate the canonical naming map.
# Reuse: Inspect architecture/naming_conventions.yaml before changing this gate.

from __future__ import annotations

import re
import subprocess
from datetime import date
from fnmatch import fnmatch
from typing import Any


HEADER_LINE_LIMIT = 30
LIFECYCLE_PATTERN = re.compile(
    r"Lifecycle:\s*created=(?P<created>\d{4}-\d{2}-\d{2});\s*"
    r"last_reviewed=(?P<last_reviewed>\d{4}-\d{2}-\d{2});\s*"
    r"last_reused=(?P<last_reused>\d{4}-\d{2}-\d{2}|never)"
)
PURPOSE_PATTERN = re.compile(r"Purpose:\s*\S")
REUSE_PATTERN = re.compile(r"Reuse:\s*\S")


def freshness_metadata_contract(api: Any) -> dict[str, Any]:
    return (api.load_naming_conventions().get("freshness_metadata") or {})


def freshness_target_patterns(api: Any) -> tuple[str, ...]:
    contract = freshness_metadata_contract(api)
    patterns = tuple(str(pattern) for pattern in contract.get("applies_to") or ())
    return patterns or ("scripts/*.py", "scripts/*.sh", "tests/test_*.py")


def freshness_header_line_limit(api: Any) -> int:
    value = freshness_metadata_contract(api).get("header_line_limit")
    return value if isinstance(value, int) and value > 0 else HEADER_LINE_LIMIT


def freshness_target(api: Any, path: str) -> bool:
    return any(fnmatch(path, pattern) for pattern in freshness_target_patterns(api))


def first_header_lines(api: Any, text: str) -> str:
    return "\n".join(text.splitlines()[:freshness_header_line_limit(api)])


def valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_freshness_header(api: Any, path: str, text: str) -> list[Any]:
    header = first_header_lines(api, text)
    issues: list[Any] = []
    lifecycle = LIFECYCLE_PATTERN.search(header)
    if not lifecycle:
        return [
            api._issue(
                "freshness_header_missing",
                path,
                "changed script/test file needs a Lifecycle header with created, last_reviewed, and last_reused",
            )
        ]

    for field in ("created", "last_reviewed"):
        if not valid_date(lifecycle.group(field)):
            issues.append(
                api._issue(
                    "freshness_header_date_invalid",
                    path,
                    f"Lifecycle {field} must be an ISO date",
                )
            )
    last_reused = lifecycle.group("last_reused")
    if last_reused != "never" and not valid_date(last_reused):
        issues.append(
            api._issue(
                "freshness_header_date_invalid",
                path,
                "Lifecycle last_reused must be an ISO date or never",
            )
        )
    if not PURPOSE_PATTERN.search(header):
        issues.append(
            api._issue(
                "freshness_header_field_missing",
                path,
                "changed script/test file needs a Purpose header",
            )
        )
    if not REUSE_PATTERN.search(header):
        issues.append(
            api._issue(
                "freshness_header_field_missing",
                path,
                "changed script/test file needs a Reuse header",
            )
        )
    return issues


def run_freshness_metadata(api: Any, changed_files: list[str] | None = None) -> Any:
    try:
        changes = api._map_maintenance_changes(changed_files or [])
    except subprocess.CalledProcessError as exc:
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "freshness_header_git_status_failed",
                    "<git-status>",
                    f"could not read git status: {exc}",
                )
            ],
        )

    issues: list[Any] = []
    for path, kind in sorted(changes.items()):
        if kind == "deleted" or not freshness_target(api, path):
            continue
        target = api.ROOT / path
        if not target.exists() or not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")
        issues.extend(check_freshness_header(api, path, text))
    return api.StrictResult(ok=not issues, issues=issues)


def run_naming_conventions(api: Any) -> Any:
    if not api.NAMING_CONVENTIONS_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "naming_conventions_manifest_missing",
                    "architecture/naming_conventions.yaml",
                    "naming conventions manifest is missing",
                )
            ],
        )

    manifest = api.load_naming_conventions()
    issues: list[Any] = []
    for field in manifest.get("required_top_level_keys") or []:
        if api._metadata_missing(manifest.get(field)):
            issues.append(
                api._issue(
                    "naming_conventions_required_field_missing",
                    "architecture/naming_conventions.yaml",
                    f"missing top-level field {field!r}",
                )
            )

    scripts = ((manifest.get("file_naming") or {}).get("scripts") or {})
    long_lived = scripts.get("long_lived") or {}
    if api._metadata_missing(long_lived.get("allowed_prefixes")):
        issues.append(
            api._issue(
                "naming_conventions_rule_invalid",
                "architecture/naming_conventions.yaml:file_naming.scripts.long_lived",
                "long-lived script naming needs allowed_prefixes",
            )
        )
    if api._metadata_missing((manifest.get("function_naming") or {}).get("preferred_shape")):
        issues.append(
            api._issue(
                "naming_conventions_rule_invalid",
                "architecture/naming_conventions.yaml:function_naming",
                "function naming needs preferred_shape",
            )
        )
    if api._metadata_missing(freshness_metadata_contract(api).get("applies_to")):
        issues.append(
            api._issue(
                "naming_conventions_rule_invalid",
                "architecture/naming_conventions.yaml:freshness_metadata",
                "freshness metadata needs applies_to patterns",
            )
        )
    return api.StrictResult(ok=not issues, issues=issues)
