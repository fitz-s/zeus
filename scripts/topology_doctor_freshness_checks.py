"""Freshness metadata checker family for topology_doctor."""
# Lifecycle: created=2026-04-16; last_reviewed=2026-04-16; last_reused=never
# Purpose: Enforce changed-file freshness headers for scripts and top-level tests.
# Reuse: Inspect against AGENTS.md + architecture/script_manifest.yaml before changing this gate.

from __future__ import annotations

import re
import subprocess
from datetime import date
from fnmatch import fnmatch
from typing import Any


FRESHNESS_TARGET_PATTERNS = (
    "scripts/*.py",
    "scripts/*.sh",
    "tests/test_*.py",
)
HEADER_LINE_LIMIT = 30
LIFECYCLE_PATTERN = re.compile(
    r"Lifecycle:\s*created=(?P<created>\d{4}-\d{2}-\d{2});\s*"
    r"last_reviewed=(?P<last_reviewed>\d{4}-\d{2}-\d{2});\s*"
    r"last_reused=(?P<last_reused>\d{4}-\d{2}-\d{2}|never)"
)
PURPOSE_PATTERN = re.compile(r"Purpose:\s*\S")
REUSE_PATTERN = re.compile(r"Reuse:\s*\S")


def freshness_target(path: str) -> bool:
    return any(fnmatch(path, pattern) for pattern in FRESHNESS_TARGET_PATTERNS)


def first_header_lines(text: str) -> str:
    return "\n".join(text.splitlines()[:HEADER_LINE_LIMIT])


def valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_freshness_header(api: Any, path: str, text: str) -> list[Any]:
    header = first_header_lines(text)
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
        if kind == "deleted" or not freshness_target(path):
            continue
        target = api.ROOT / path
        if not target.exists() or not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")
        issues.extend(check_freshness_header(api, path, text))
    return api.StrictResult(ok=not issues, issues=issues)
