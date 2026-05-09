"""Map-maintenance checker family for topology_doctor."""
# Lifecycle: created=2026-04-14; last_reviewed=2026-04-29; last_reused=2026-05-09
# Purpose: Check changed-file companion registry requirements for map-maintenance closeout.
# Reuse: Keep packet-local evidence rules narrow; do not require broad docs companions for covered descendants.

from __future__ import annotations

import fnmatch
import re
from typing import Any


def change_kind(api: Any, path: str, tracked: set[str]) -> str:
    exists_now = (api.ROOT / path).exists()
    if exists_now and path not in tracked:
        return "added"
    if not exists_now and path in tracked:
        return "deleted"
    return "modified"


def git_status_changes(api: Any) -> dict[str, str]:
    proc = api.subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=api.ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    entries = [entry for entry in proc.stdout.split("\0") if entry]
    changes: dict[str, str] = {}
    index = 0
    while index < len(entries):
        entry = entries[index]
        status = entry[:2]
        path = entry[3:]
        if not path:
            index += 1
            continue

        if "R" in status or "C" in status:
            old_path = entries[index + 1] if index + 1 < len(entries) else ""
            if old_path:
                changes[old_path] = "deleted"
            changes[path] = "added"
            index += 2
            continue

        if status == "??" or "A" in status:
            kind = "added"
        elif "D" in status:
            kind = "deleted"
        else:
            kind = "modified"
        changes[path] = kind
        index += 1
    return changes


def map_maintenance_changes(api: Any, changed_files: list[str]) -> dict[str, str]:
    if not changed_files:
        return api._git_status_changes()
    git_changes = api._git_status_changes()
    tracked = set(api._git_ls_files())
    return {path: git_changes.get(path, api._change_kind(path, tracked)) for path in changed_files}


def path_glob_matches(path: str, pattern: str) -> bool:
    if "**" in pattern:
        parts = pattern.split("**")
        if len(parts) == 2:
            prefix = parts[0].rstrip("/")
            suffix = parts[1].lstrip("/")
            if not prefix:
                return fnmatch.fnmatch(path.split("/")[-1], suffix) if suffix else True
            prefix_depth = prefix.count("/") + 1
            path_parts = path.split("/")
            if len(path_parts) < prefix_depth:
                return False
            dir_part = "/".join(path_parts[:prefix_depth])
            if not fnmatch.fnmatch(dir_part, prefix):
                return False
            if suffix:
                return fnmatch.fnmatch(path_parts[-1], suffix)
            return len(path_parts) > prefix_depth
    # Python fnmatch lets "*" cross "/" boundaries. Map-maintenance rules use
    # shell-like path intent where docs/*.md means only direct docs children.
    if path.count("/") != pattern.count("/"):
        return False
    return fnmatch.fnmatch(path, pattern)


def run_map_maintenance(api: Any, changed_files: list[str] | None = None, mode: str = "advisory") -> Any:
    if not api.MAP_MAINTENANCE_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "map_maintenance_manifest_missing",
                    "architecture/map_maintenance.yaml",
                    "map maintenance manifest is missing",
                )
            ],
        )
    manifest = api.load_map_maintenance()
    issues: list[Any] = []
    mode_spec = (manifest.get("modes") or {}).get(mode)
    if not mode_spec:
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "map_maintenance_invalid_mode",
                    "architecture/map_maintenance.yaml",
                    f"unknown mode {mode!r}",
                )
            ],
        )
    issue_fn = api.issue if mode_spec.get("blocking") else api.warning
    try:
        changes = api._map_maintenance_changes(changed_files or [])
    except api.subprocess.CalledProcessError as exc:
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "map_maintenance_git_status_failed",
                    "<git-status>",
                    f"could not read git status: {exc}",
                )
            ],
        )

    for rule in manifest.get("rules") or []:
        if not rule.get("path_globs"):
            issues.append(api._issue("map_maintenance_missing_path_glob", str(rule.get("id", "<unknown>")), "rule missing path_globs"))
        if not rule.get("required_companions"):
            issues.append(api._issue("map_maintenance_missing_companion", str(rule.get("id", "<unknown>")), "rule missing required_companions"))

    changed_set = set(changes)
    for path, kind in changes.items():
        for rule in manifest.get("rules") or []:
            if kind not in (rule.get("on_change") or []):
                continue
            if not any(path_glob_matches(path, pattern) for pattern in rule.get("path_globs") or []):
                continue
            for companion in rule.get("required_companions") or []:
                if companion not in changed_set:
                    issues.append(
                        issue_fn(
                            "map_maintenance_companion_missing",
                            path,
                            f"{kind} file requires companion update {companion} ({rule.get('id')})",
                            owner_manifest="architecture/map_maintenance.yaml",
                            repair_kind="update_companion",
                            related_paths=(companion,),
                            companion_of=path,
                        )
                    )
    blocking = [issue for issue in issues if issue.severity == "error"]
    return api.StrictResult(ok=not blocking, issues=issues)
