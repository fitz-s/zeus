"""Docs checker family for topology_doctor.

This module intentionally receives the topology_doctor module as `api` instead
of importing its internals. That keeps this first checker-family split small and
preserves the existing public helper surface during migration.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


INTERNAL_PATH_PATTERN = re.compile(
    r"\b(?:docs|architecture|src|scripts|tests|config)/[A-Za-z0-9_./-]+(?:\.[A-Za-z0-9_]+)?"
)

CONFIG_AGENTS_VOLATILE_FACT_PATTERNS = (
    re.compile(r"\bverified\s+\d{4}-\d{2}-\d{2}\b", re.IGNORECASE),
    re.compile(r"\bnon[-_]wu_icao cities\s*\(\d{4}-\d{2}-\d{2}\)", re.IGNORECASE),
)

PROGRESS_HANDOFF_PATTERN = re.compile(r"(progress|handoff|work[_-]?log|closeout)", re.IGNORECASE)


def docs_mode_excluded_roots(api: Any, topology: dict[str, Any]) -> list[Path]:
    roots = []
    for item in topology.get("docs_mode_excluded_roots", []):
        rel = item.get("path")
        if rel:
            roots.append(api.ROOT / rel)
    return roots


def docs_subroot_specs(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    specs = {
        str(item.get("path", "")).strip("/"): item
        for item in topology.get("docs_subroots") or []
        if item.get("path")
    }
    if specs:
        return specs
    return {
        "docs/authority": {"allow_non_markdown": False, "requires_agents": True},
        "docs/reference": {"allow_non_markdown": False, "requires_agents": True},
        "docs/operations": {"allow_non_markdown": False, "requires_agents": True},
        "docs/runbooks": {"allow_non_markdown": False, "requires_agents": True},
        "docs/archives": {"allow_non_markdown": True, "requires_agents": True},
    }


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def check_hidden_docs(api: Any, topology: dict[str, Any]) -> list[Any]:
    issues = []
    excluded_roots = docs_mode_excluded_roots(api, topology)
    subroots = docs_subroot_specs(topology)
    visible_docs_files = [
        api.ROOT / rel
        for rel in api._git_visible_files()
        if rel.startswith("docs/") and (api.ROOT / rel).is_file()
    ]
    allowed_root_files = set(topology.get("docs_root_allowed_files") or {"docs/AGENTS.md", "docs/README.md", "docs/known_gaps.md"})
    for path in sorted(visible_docs_files):
        rel = path.relative_to(api.ROOT).as_posix()
        if any(is_under(path, root) for root in excluded_roots):
            continue
        parts = Path(rel).parts
        subroot = "/".join(parts[:2]) if len(parts) > 1 else ""
        if len(parts) > 1 and subroot not in subroots and rel not in allowed_root_files:
            issues.append(
                api._issue(
                    "docs_unregistered_subtree",
                    subroot,
                    "docs subtree has visible files but is not declared in topology docs_subroots",
                )
            )
        spec = subroots.get(subroot, {})
        suffix = path.suffix.lower()
        if suffix != ".md":
            allowed = set(str(item).lower() for item in spec.get("allowed_extensions") or [])
            if not spec.get("allow_non_markdown") or suffix not in allowed:
                issues.append(
                    api._issue(
                        "docs_non_markdown_artifact",
                        rel,
                        "non-markdown docs artifact must live in a declared artifact/evidence subroot",
                    )
                )
        if path.name in {"plan.md", "progress.md"} and not rel.startswith("docs/operations/task_"):
            issues.append(
                api._issue(
                    "docs_generic_name",
                    rel,
                    "generic docs names are only allowed inside active task folders",
                )
            )
        if " " in rel:
            issues.append(api._issue("hidden_active_doc", rel, "active docs path contains spaces"))
    return issues


def check_progress_handoff_paths(api: Any) -> list[Any]:
    issues: list[Any] = []
    for rel in api._git_visible_files():
        if not rel.startswith("docs/") or not rel.endswith(".md"):
            continue
        if rel.startswith("docs/archives/"):
            continue
        name = Path(rel).name
        if not PROGRESS_HANDOFF_PATTERN.search(name):
            continue
        allowed = (
            rel.startswith("docs/operations/task_")
            or rel in {"docs/operations/current_state.md"}
        )
        if not allowed:
            issues.append(
                api._issue(
                    "progress_handoff_path_violation",
                    rel,
                    "progress/handoff/work-log files must live inside a task folder, current_state, or archives",
                )
            )
    return issues


def check_docs_subtree_agents(api: Any, topology: dict[str, Any]) -> list[Any]:
    issues = []
    for rel, spec in sorted(docs_subroot_specs(topology).items()):
        if not spec.get("requires_agents", True):
            continue
        if rel == "docs/archives" and not (api.ROOT / rel).exists():
            continue
        path = api.ROOT / rel / "AGENTS.md"
        if not path.exists():
            issues.append(api._issue("missing_docs_agents", f"{rel}/AGENTS.md", "active docs subtree lacks AGENTS.md"))
    return issues


def operation_task_dirs(api: Any) -> set[str]:
    root = api.ROOT / "docs" / "operations"
    dirs: set[str] = set()
    if not root.exists():
        return dirs
    for path in root.glob("task_*"):
        if path.is_dir():
            dirs.add(path.relative_to(api.ROOT).as_posix())
    return dirs


def check_operations_task_folders(api: Any, topology: dict[str, Any]) -> list[Any]:
    issues: list[Any] = []
    task_dirs = operation_task_dirs(api)
    if not task_dirs:
        return issues
    agents = api.ROOT / "docs" / "operations" / "AGENTS.md"
    registered = api._registry_entries(agents, include_directory_tokens=True) if agents.exists() else set()
    current_state = api.ROOT / "docs" / "operations" / "current_state.md"
    current_text = current_state.read_text(encoding="utf-8", errors="ignore") if current_state.exists() else ""
    for rel in sorted(task_dirs):
        name = Path(rel).name
        registered_here = (
            name in registered
            or f"{name}/" in registered
            or rel in registered
            or f"{rel}/" in registered
        )
        referenced = rel in current_text or f"{rel}/" in current_text or f"{rel}/plan.md" in current_text
        if not registered_here or not referenced:
            missing = []
            if not registered_here:
                missing.append("docs/operations/AGENTS.md")
            if not referenced:
                missing.append("docs/operations/current_state.md")
            issues.append(
                api._issue(
                    "operations_task_unregistered",
                    rel,
                    f"operation task folder is not registered/referenced by {', '.join(missing)}",
                )
            )
    return issues


def runtime_plan_paths(api: Any, topology: dict[str, Any]) -> list[str]:
    spec = topology.get("runtime_artifact_inventory") or {}
    patterns = [str(pattern) for pattern in spec.get("runtime_plan_globs") or []]
    paths: set[str] = set()
    for pattern in patterns:
        for path in api.ROOT.glob(pattern):
            if path.is_file() and path.name != ".DS_Store":
                paths.add(path.relative_to(api.ROOT).as_posix())
    return sorted(paths)


def check_runtime_plan_inventory(api: Any, topology: dict[str, Any]) -> list[Any]:
    runtime_paths = runtime_plan_paths(api, topology)
    if not runtime_paths:
        return []
    spec = topology.get("runtime_artifact_inventory") or {}
    inventory_rel = str(spec.get("path") or "")
    if not inventory_rel:
        return [
            api._issue(
                "runtime_plan_inventory_missing",
                "architecture/topology.yaml:runtime_artifact_inventory",
                "runtime plan artifacts exist but no inventory path is declared",
            )
        ]
    inventory = api.ROOT / inventory_rel
    if not inventory.exists():
        return [
            api._issue(
                "runtime_plan_inventory_missing",
                inventory_rel,
                "runtime plan artifacts exist but inventory file is missing",
            )
        ]
    text = inventory.read_text(encoding="utf-8", errors="ignore")
    issues: list[Any] = []
    for rel in runtime_paths:
        if rel not in text:
            issues.append(
                api._issue(
                    "runtime_plan_artifact_unindexed",
                    rel,
                    f"runtime planning artifact is not listed in {inventory_rel}",
                )
            )
    return issues


def internal_path_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    for match in INTERNAL_PATH_PATTERN.findall(text):
        value = match.strip().strip(".,);:")
        if any(char in value for char in "*?[]<>"):
            continue
        if not Path(value.rstrip("/")).suffix and not value.endswith("/"):
            continue
        candidates.add(value.rstrip("/"))
    return candidates


def check_broken_internal_paths(api: Any) -> list[Any]:
    issues: list[Any] = []
    checked_files = [
        api.ROOT / "AGENTS.md",
        api.ROOT / "workspace_map.md",
        api.ROOT / "docs" / "AGENTS.md",
        api.ROOT / "docs" / "operations" / "AGENTS.md",
        api.ROOT / "docs" / "operations" / "current_state.md",
        api.ROOT / "docs" / "authority" / "AGENTS.md",
        api.ROOT / "architecture" / "kernel_manifest.yaml",
    ]
    for file_path in checked_files:
        if not file_path.exists():
            continue
        source_rel = file_path.relative_to(api.ROOT).as_posix()
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        for candidate in sorted(internal_path_candidates(text)):
            if candidate == "docs/archives" or candidate.startswith("docs/archives/"):
                continue
            if not (api.ROOT / candidate).exists():
                issues.append(
                    api._issue(
                        "docs_broken_internal_path",
                        source_rel,
                        f"internal path is not visible in this tree: {candidate}",
                    )
                )
    return issues


def check_config_agents_volatile_facts(api: Any) -> list[Any]:
    path = api.ROOT / "config" / "AGENTS.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    issues: list[Any] = []
    for pattern in CONFIG_AGENTS_VOLATILE_FACT_PATTERNS:
        if pattern.search(text):
            issues.append(
                api._issue(
                    "config_agents_volatile_fact",
                    "config/AGENTS.md",
                    "config AGENTS should route to audit artifacts instead of embedding dated external market facts",
                )
            )
            break
    return issues


def current_state_operation_paths(api: Any, text: str, surface_prefix: str) -> set[str]:
    paths = api._markdown_path_tokens(text)
    return {path for path in paths if path.startswith(surface_prefix)}


def check_active_operations_registry(api: Any, topology: dict[str, Any]) -> list[Any]:
    registry = topology.get("active_operations_registry") or {}
    rel = str(registry.get("current_state") or "docs/operations/current_state.md")
    path = api.ROOT / rel
    if not path.exists():
        return [api._issue("missing_active_pointer", rel, "active operations current_state missing")]

    text = path.read_text(encoding="utf-8", errors="ignore")
    issues: list[Any] = []
    for label in registry.get("required_labels") or []:
        if str(label) not in text:
            issues.append(
                api._issue(
                    "operations_current_state_missing_label",
                    rel,
                    f"current_state missing required operations label: {label}",
                )
            )
    for anchor in registry.get("required_anchors") or []:
        anchor = str(anchor)
        if anchor not in text:
            issues.append(
                api._issue(
                    "operations_current_state_missing_anchor",
                    rel,
                    f"current_state missing required active anchor: {anchor}",
                )
            )
        elif not (api.ROOT / anchor.rstrip("/")).exists():
            issues.append(
                api._issue(
                    "operations_current_state_path_missing",
                    rel,
                    f"current_state required anchor does not exist: {anchor}",
                )
            )

    surface_prefix = str(registry.get("surface_prefix") or "docs/operations/")
    surfaces = current_state_operation_paths(api, text, surface_prefix)
    registered = api._registry_entries(
        api.ROOT / "docs" / "operations" / "AGENTS.md",
        include_directory_tokens=True,
    )
    for surface in sorted(surfaces):
        normalized = surface.rstrip("/")
        if not (api.ROOT / normalized).exists():
            issues.append(
                api._issue(
                    "operations_current_state_path_missing",
                    rel,
                    f"current_state references missing operations surface: {surface}",
                )
            )
            continue
        local_name = Path(normalized).name
        if normalized == "docs/operations/current_state.md":
            continue
        if (
            local_name not in registered
            and f"{local_name}/" not in registered
            and surface not in registered
            and normalized not in registered
            and not any(
                item.endswith("/") and normalized.startswith(f"docs/operations/{item}")
                for item in registered
            )
        ):
            issues.append(
                api._issue(
                    "operations_current_state_unregistered_surface",
                    rel,
                    f"operations surface is in current_state but not docs/operations/AGENTS.md registry: {surface}",
                )
            )
    issues.extend(check_operations_task_folders(api, topology))
    issues.extend(check_runtime_plan_inventory(api, topology))
    return issues
