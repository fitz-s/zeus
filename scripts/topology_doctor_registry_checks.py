"""Registry/root/docs-strict checker family for topology_doctor."""
# Lifecycle: created=2026-04-16; last_reviewed=2026-04-21; last_reused=2026-04-21
# Purpose: Registry, root, docs-strict, and archive-interface checks for topology_doctor.
# Reuse: Keep durable workspace-law checks here; route narrow docs tree checks through topology_doctor_docs_checks.py.

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


def declared_paths(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("path", "")) for item in items if item.get("path")}


def path_declared(path: str, declared: set[str]) -> bool:
    return path in declared or any(
        any(char in pattern for char in "*?[") and fnmatch(path, pattern)
        for pattern in declared
    )


def markdown_path_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"`([^`]+)`", text))
    tokens.update(re.findall(r"\[[^\]]+\]\(([^)]+)\)", text))
    tokens.update(
        match.group(0)
        for match in re.finditer(r"(?:docs/operations/)?task_\d{4}-\d{2}-\d{2}_[A-Za-z0-9_./-]+/?", text)
    )
    tokens.update(
        match.group(0)
        for match in re.finditer(r"docs/operations/[A-Za-z0-9_./-]+/?", text)
    )
    return {
        token.strip().strip(".,);:")
        for token in tokens
        if token.strip()
    }


def registry_entries(api: Any, agents_path: Path, *, include_directory_tokens: bool = False) -> set[str]:
    entries: set[str] = set()
    for line in agents_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 3:
            continue
        first_cell = cells[1]
        for token in markdown_path_tokens(first_cell):
            if "*" in token or (token.endswith("/") and not include_directory_tokens):
                continue
            entries.add(token)
    return entries


def registry_target(api: Any, directory: Path, token: str) -> Path:
    if token.startswith(("/", "~")):
        return Path(token).expanduser()
    if "/" in token and (api.ROOT / token).exists():
        return api.ROOT / token
    return directory / token


def active_registry_dirs(api: Any, topology: dict[str, Any]) -> list[Path]:
    dirs = []
    for item in topology.get("registry_directories", []):
        rel = item.get("path")
        if not rel:
            continue
        directory = api.ROOT / rel
        if (directory / "AGENTS.md").exists():
            dirs.append(directory)
    return dirs


def is_root_scratch(path: Path) -> bool:
    return path.name in {".DS_Store", ".git-commit-msg.tmp"} or path.name.startswith(".")


def check_schema(api: Any, topology: dict[str, Any], schema: dict[str, Any]) -> list[Any]:
    issues = []
    for key in schema.get("required_top_level_keys", []):
        if key not in topology:
            issues.append(api._issue("missing_schema_key", "architecture/topology.yaml", key))
    return issues


def check_coverage(api: Any, topology: dict[str, Any]) -> list[Any]:
    required = {
        ".",
        "src",
        "tests",
        "scripts",
        "docs",
        "config",
        ".github/workflows",
        "architecture",
        "state",
        ".omx",
        ".claude/worktrees",
        "zeus_data_improvement_foundation_plus",
    }
    declared = api._declared_paths(topology.get("coverage_roots", []))
    return [
        api._issue("missing_coverage_root", path, "coverage root is not declared")
        for path in sorted(required - declared)
    ]


def check_active_pointers(api: Any, topology: dict[str, Any]) -> list[Any]:
    issues = []
    for item in topology.get("required_active_pointers", []):
        rel = item["path"]
        if not (api.ROOT / rel).exists():
            issues.append(api._issue("missing_active_pointer", rel, item.get("rationale", "")))
    return issues


def check_registries(api: Any, topology: dict[str, Any], tracked: list[str]) -> list[Any]:
    issues: list[Any] = []
    tracked_set = set(tracked)
    script_manifest = api.load_script_manifest().get("scripts") if api.SCRIPT_MANIFEST_PATH.exists() else {}
    test_topology = api.load_test_topology().get("categories") if api.TEST_TOPOLOGY_PATH.exists() else {}
    test_manifest: set[str] = set()
    for paths in (test_topology or {}).values():
        test_manifest.update(paths or [])

    for directory in api._active_registry_dirs(topology):
        agents_path = directory / "AGENTS.md"
        rel_dir = directory.relative_to(api.ROOT).as_posix()
        entries = api._registry_entries(agents_path)

        for token in sorted(entries):
            target = api._registry_target(directory, token)
            if not target.exists():
                target_rel = (
                    target.relative_to(api.ROOT).as_posix()
                    if target.is_relative_to(api.ROOT)
                    else str(target)
                )
                issues.append(
                    api._issue(
                        "stale_registry_entry",
                        f"{agents_path.relative_to(api.ROOT).as_posix()}:{token}",
                        f"registry target missing: {target_rel}",
                    )
                )

        for rel in tracked_set:
            path = api.ROOT / rel
            if path.parent != directory or path.name == "AGENTS.md":
                continue
            if rel.startswith("scripts/") and path.name in script_manifest:
                continue
            if rel.startswith("tests/") and rel in test_manifest:
                continue
            if path.name not in entries and rel not in entries:
                issues.append(
                    api._issue(
                        "unregistered_tracked_file",
                        rel,
                        f"not listed in {rel_dir}/AGENTS.md file registry",
                    )
                )
    return issues


def check_reference_authority(api: Any, topology: dict[str, Any]) -> list[Any]:
    evidence = {
        item.get("path"): item.get("enforcement", [])
        for item in topology.get("authority_claims", [])
    }
    issues = []
    for path in sorted((api.ROOT / "docs" / "reference").glob("*.md")):
        rel = path.relative_to(api.ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        claims_authority = (
            "**Authority**" in text
            or "code is wrong" in text
            or "source of truth for implementation" in text
        )
        explicitly_reference_only = "Reference-only" in text or "Reference material" in text
        if claims_authority and not explicitly_reference_only and not evidence.get(rel):
            issues.append(
                api._issue(
                    "reference_authority_without_enforcement",
                    rel,
                    "reference doc claims authority/source-of-truth without enforcement link",
                )
            )
    return issues


def check_archive_interface(api: Any, topology: dict[str, Any]) -> list[Any]:
    issues: list[Any] = []
    spec = topology.get("archive_interface") or {}
    interface_rel = str(spec.get("path") or "docs/archive_registry.md")
    interface_path = api.ROOT / interface_rel
    if not interface_path.exists():
        issues.append(
            api._issue(
                "docs_archive_interface_missing",
                interface_rel,
                "visible archive interface is missing",
            )
        )
    allowed_root = set(topology.get("docs_root_allowed_files") or [])
    if interface_rel not in allowed_root:
        issues.append(
            api._issue(
                "docs_archive_interface_unregistered",
                "architecture/topology.yaml:docs_root_allowed_files",
                f"{interface_rel} must be an allowed docs-root file",
            )
        )

    for item in topology.get("docs_subroots") or []:
        if item.get("path") != "docs/archives":
            continue
        if item.get("default_read") is not False:
            issues.append(
                api._issue(
                    "docs_archive_default_read_leak",
                    "architecture/topology.yaml:docs_subroots.docs/archives",
                    "docs/archives must be explicitly non-default-read",
                )
            )
        visible_interface = str(item.get("visible_interface") or "")
        if visible_interface and visible_interface != interface_rel:
            issues.append(
                api._issue(
                    "docs_archive_visible_interface_mismatch",
                    "architecture/topology.yaml:docs_subroots.docs/archives",
                    f"docs/archives visible_interface {visible_interface!r} does not match {interface_rel!r}",
                )
            )

    forbidden_phrases = [str(item) for item in spec.get("forbidden_live_peer_phrases") or []]
    for rel in ("docs/AGENTS.md", "docs/README.md"):
        path = api.ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for phrase in forbidden_phrases:
            if phrase and phrase in text:
                issues.append(
                    api._issue(
                        "docs_archive_default_read_leak",
                        rel,
                        f"visible docs surface still uses archive-as-live-peer phrase: {phrase!r}",
                    )
                )
    return issues


def check_root_and_state_classification(api: Any, topology: dict[str, Any]) -> list[Any]:
    issues = []
    visible_files = {
        rel
        for rel in api._git_visible_files()
        if (api.ROOT / rel).exists() and (api.ROOT / rel).is_file()
    }
    root_declared = api._declared_paths(topology.get("root_governed_files", []))
    for rel in sorted(path for path in visible_files if "/" not in path):
        path = api.ROOT / rel
        if api._is_root_scratch(path):
            continue
        if not api._path_declared(rel, root_declared):
            issues.append(
                api._issue("unclassified_root_artifact", rel, "repo-root file is not classified")
            )

    state_declared = api._declared_paths(topology.get("state_surfaces", []))
    for rel in sorted(
        path
        for path in visible_files
        if path.startswith("state/") and Path(path).parent.as_posix() == "state"
    ):
        if Path(rel).name == ".DS_Store":
            continue
        if not api._path_declared(rel, state_declared):
            issues.append(
                api._issue("unclassified_state_surface", rel, "state/artifact file is not classified")
            )
    return issues


def check_shadow_authority_references(api: Any) -> list[Any]:
    issues = []
    scan_roots = [api.ROOT / "AGENTS.md", api.ROOT / "workspace_map.md", api.ROOT / "docs"]
    for root in scan_roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*.md"))
        for path in paths:
            rel = path.relative_to(api.ROOT).as_posix()
            if rel.startswith("docs/archives/"):
                continue
            if rel == "docs/operations/runtime_artifact_inventory.md":
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                lower = line.lower()
                if (".omx/" in line or ".claude/worktrees/" in line) and (
                    "active" in lower or "authority" in lower or "companion" in lower
                ):
                    issues.append(
                        api._issue(
                            "shadow_runtime_authority_reference",
                            f"{rel}:{lineno}",
                            "runtime/shadow path is referenced in an active-authority context",
                        )
                    )
    return issues


def check_wmo_gate(api: Any) -> list[Any]:
    issues = []
    settlement = (api.ROOT / "src" / "contracts" / "settlement_semantics.py").read_text(encoding="utf-8")
    assumptions = (api.ROOT / "state" / "assumptions.json").read_text(encoding="utf-8")
    tests = (api.ROOT / "tests" / "test_instrument_invariants.py").read_text(encoding="utf-8")
    if "wmo_half_up" not in settlement or "np.floor(scaled + 0.5)" not in settlement:
        issues.append(api._issue("wmo_law_gate_missing", "src/contracts/settlement_semantics.py", "WMO helper missing"))
    if '"rounding_rule": "wmo_half_up"' not in assumptions:
        issues.append(api._issue("wmo_law_gate_missing", "state/assumptions.json", "assumption manifest not WMO"))
    if "test_no_python_banker_rounding_of_settlement_values_in_active_code" not in tests:
        issues.append(api._issue("wmo_law_gate_missing", "tests/test_instrument_invariants.py", "recurrence test missing"))
    return issues


def run_strict(api: Any) -> Any:
    topology = api.load_topology()
    schema = api.load_schema()
    tracked = api._git_ls_files()
    issues: list[Any] = []
    issues.extend(api._check_schema(topology, schema))
    issues.extend(api._check_coverage(topology))
    issues.extend(api._check_active_pointers(topology))
    issues.extend(api._check_registries(topology, tracked))
    issues.extend(api._check_reference_authority(topology))
    issues.extend(check_archive_interface(api, topology))
    issues.extend(api._check_hidden_docs(topology))
    issues.extend(api._check_root_and_state_classification(topology))
    issues.extend(api._check_shadow_authority_references())
    issues.extend(api._check_wmo_gate())
    return api.StrictResult(ok=not issues, issues=issues)


def run_docs(api: Any) -> Any:
    topology = api.load_topology()
    tracked = api._git_ls_files()
    issues: list[Any] = []
    issues.extend(api._check_active_pointers(topology))
    issues.extend(api._check_registries(topology, tracked))
    issues = [
        issue
        for issue in issues
        if issue.path.startswith("docs/")
        or issue.path.startswith("architecture/self_check/")
        or issue.path.startswith("docs")
    ]
    issues.extend(api._check_reference_authority(topology))
    issues.extend(check_archive_interface(api, topology))
    issues.extend(api._check_hidden_docs(topology))
    issues.extend(api._check_progress_handoff_paths())
    issues.extend(api._check_docs_subtree_agents(topology))
    issues.extend(api._check_broken_internal_paths())
    issues.extend(api._check_active_operations_registry(topology))
    issues.extend(api._check_config_agents_volatile_facts())
    issues.extend(api._check_shadow_authority_references())
    return api.StrictResult(ok=not issues, issues=issues)
