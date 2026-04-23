"""Docs checker family for topology_doctor.

This module intentionally receives the topology_doctor module as `api` instead
of importing its internals. That keeps this first checker-family split small and
preserves the existing public helper surface during migration.
"""
# Lifecycle: created=2026-04-16; last_reviewed=2026-04-21; last_reused=2026-04-21
# Purpose: Docs-tree, operations-registry, runtime-plan, and docs-registry checks for topology_doctor.
# Reuse: Keep docs-specific policy checks here; route root/state/source checks through their checker modules.

from __future__ import annotations

import glob
import json
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

DOCS_REGISTRY_REQUIRED_FIELDS = {
    "path",
    "doc_class",
    "default_read",
    "direct_reference_allowed",
    "current_role",
    "canonical_replaced_by",
    "next_action",
    "lifecycle_state",
    "coverage_scope",
    "parent_coverage_allowed",
    "truth_profile",
    "freshness_class",
    "supersedes",
    "superseded_by",
    "may_live_in_reference",
    "contains_volatile_metrics",
    "current_tense_allowed",
    "refresh_source",
}

REMOVED_REFERENCE_SUPPORT_PATTERNS = (
    "docs/reference/repo_overview.md",
    "docs/reference/data_inventory.md",
    "docs/reference/data_strategy.md",
    "docs/reference/settlement_source_provenance.md",
    "docs/reference/statistical_methodology.md",
    "docs/reference/quantitative_research.md",
    "docs/reference/market_microstructure.md",
)

STALE_TRUTH_PATTERNS = (
    re.compile(r"\brainstorm\.db\b", re.IGNORECASE),
    re.compile(r"\bdetails pending extraction\b", re.IGNORECASE),
    re.compile(r"\bUpdated:\s*`?2026-04-01`?", re.IGNORECASE),
)

DOCS_REGISTRY_PARENT_PATTERNS = (
    "docs/operations/task_*/",
    "docs/operations/*_package_*/",
    "docs/reports/",
    "docs/artifacts/",
    "docs/to-do-list/",
    "docs/runbooks/",
)


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
    allowed_root_files = set(topology.get("docs_root_allowed_files") or {"docs/AGENTS.md", "docs/README.md", "docs/archive_registry.md"})
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


def docs_registry_path_matches(path: str, entry: dict[str, Any]) -> bool:
    raw = str(entry.get("path") or "")
    if entry.get("coverage_scope") == "descendants" and entry.get("parent_coverage_allowed"):
        pattern = raw + ("**" if raw.endswith("/") else "/**")
        return re.match(glob.translate(pattern, recursive=True, include_hidden=True, seps="/"), path) is not None
    if any(char in raw for char in "*?["):
        return re.match(glob.translate(raw, recursive=True, include_hidden=True, seps="/"), path) is not None
    return path == raw


def docs_registry_parent_allowed(path: str) -> bool:
    return any(
        re.match(glob.translate(pattern, recursive=True, include_hidden=True, seps="/"), path) is not None
        for pattern in DOCS_REGISTRY_PARENT_PATTERNS
    )


def docs_registry_covers(path: str, entries: list[dict[str, Any]]) -> bool:
    return any(docs_registry_path_matches(path, entry) for entry in entries)


def check_docs_registry(api: Any, topology: dict[str, Any]) -> list[Any]:
    issues: list[Any] = []
    registry_path = getattr(api, "DOCS_REGISTRY_PATH", api.ROOT / "architecture" / "docs_registry.yaml")
    if not registry_path.exists():
        return [
            api._issue(
                "docs_registry_missing",
                "architecture/docs_registry.yaml",
                "docs registry is missing",
            )
        ]

    registry = api.load_docs_registry()
    entries = list(registry.get("entries") or [])
    allowed_classes = set(registry.get("allowed_doc_classes") or [])
    allowed_next = set(registry.get("allowed_next_actions") or [])
    allowed_lifecycle = set(registry.get("allowed_lifecycle_states") or [])
    allowed_scopes = set(registry.get("allowed_coverage_scopes") or [])
    allowed_truth_profiles = set(registry.get("allowed_truth_profiles") or [])
    allowed_freshness_classes = set(registry.get("allowed_freshness_classes") or [])
    allowed_refresh_sources = set(registry.get("allowed_refresh_sources") or [])
    seen: set[str] = set()

    for entry in entries:
        path = str(entry.get("path") or "")
        missing = sorted(field for field in DOCS_REGISTRY_REQUIRED_FIELDS if field not in entry)
        if missing:
            issues.append(
                api._issue(
                    "docs_registry_required_field_missing",
                    f"architecture/docs_registry.yaml:{path or '<missing-path>'}",
                    f"docs registry entry missing fields: {', '.join(missing)}",
                )
            )
            continue
        if path in seen:
            issues.append(api._issue("docs_registry_duplicate_path", path, "duplicate docs registry path"))
        seen.add(path)
        if entry.get("doc_class") not in allowed_classes:
            issues.append(api._issue("docs_registry_invalid_enum", path, f"invalid doc_class {entry.get('doc_class')!r}"))
        if entry.get("next_action") not in allowed_next:
            issues.append(api._issue("docs_registry_invalid_enum", path, f"invalid next_action {entry.get('next_action')!r}"))
        if entry.get("lifecycle_state") not in allowed_lifecycle:
            issues.append(api._issue("docs_registry_invalid_enum", path, f"invalid lifecycle_state {entry.get('lifecycle_state')!r}"))
        if entry.get("coverage_scope") not in allowed_scopes:
            issues.append(api._issue("docs_registry_invalid_enum", path, f"invalid coverage_scope {entry.get('coverage_scope')!r}"))
        if entry.get("truth_profile") not in allowed_truth_profiles:
            issues.append(api._issue("docs_registry_invalid_enum", path, f"invalid truth_profile {entry.get('truth_profile')!r}"))
        if entry.get("freshness_class") not in allowed_freshness_classes:
            issues.append(api._issue("docs_registry_invalid_enum", path, f"invalid freshness_class {entry.get('freshness_class')!r}"))
        if entry.get("refresh_source") not in allowed_refresh_sources:
            issues.append(api._issue("docs_registry_invalid_enum", path, f"invalid refresh_source {entry.get('refresh_source')!r}"))
        if not isinstance(entry.get("supersedes"), list):
            issues.append(api._issue("docs_registry_invalid_enum", path, "supersedes must be a list"))
        if not isinstance(entry.get("superseded_by"), list):
            issues.append(api._issue("docs_registry_invalid_enum", path, "superseded_by must be a list"))
        for bool_field in ("may_live_in_reference", "contains_volatile_metrics", "current_tense_allowed"):
            if not isinstance(entry.get(bool_field), bool):
                issues.append(api._issue("docs_registry_invalid_enum", path, f"{bool_field} must be boolean"))
        if path.startswith("docs/reference/") and path != "docs/reference/AGENTS.md":
            if entry.get("truth_profile") != "durable_reference" or entry.get("may_live_in_reference") is not True:
                issues.append(api._issue("docs_reference_not_canonical", path, "docs/reference may contain only durable canonical reference docs"))
        if entry.get("coverage_scope") == "descendants":
            if not entry.get("parent_coverage_allowed"):
                issues.append(api._issue("docs_registry_parent_not_allowed", path, "descendant coverage requires parent_coverage_allowed=true"))
            elif not docs_registry_parent_allowed(path):
                issues.append(api._issue("docs_registry_parent_not_allowed", path, "parent coverage is not allowed for this docs path"))
        elif entry.get("parent_coverage_allowed"):
            issues.append(api._issue("docs_registry_parent_not_allowed", path, "parent_coverage_allowed requires coverage_scope=descendants"))
        for replacement in entry.get("canonical_replaced_by") or []:
            replacement = str(replacement)
            if replacement and not (api.ROOT / replacement).exists():
                issues.append(api._issue("docs_registry_replacement_missing", path, f"replacement target missing: {replacement}"))

    excluded_roots = docs_mode_excluded_roots(api, topology)
    visible_docs = [
        rel
        for rel in api._git_visible_files()
        if rel.startswith("docs/")
        and (api.ROOT / rel).is_file()
        and not any(is_under(api.ROOT / rel, root) for root in excluded_roots)
    ]
    for rel in sorted(visible_docs):
        if rel.startswith("docs/archives/"):
            continue
        if rel.startswith("docs/reference/") and Path(rel).name.startswith("legacy_reference_"):
            issues.append(api._issue("docs_reference_legacy_snapshot", rel, "legacy reference snapshots must live outside docs/reference"))
        if not docs_registry_covers(rel, entries):
            issues.append(api._issue("docs_registry_unclassified_doc", rel, "tracked docs file is not classified by architecture/docs_registry.yaml"))

    default_surfaces = [
        api.ROOT / "docs" / "README.md",
        api.ROOT / "docs" / "AGENTS.md",
        api.ROOT / "docs" / "reference" / "AGENTS.md",
    ]
    for entry in entries:
        if entry.get("direct_reference_allowed") is not False:
            continue
        path = str(entry.get("path") or "")
        if not path or any(char in path for char in "*?["):
            continue
        for surface in default_surfaces:
            if surface.exists() and path in surface.read_text(encoding="utf-8", errors="ignore"):
                issues.append(
                    api._issue(
                        "docs_registry_direct_reference_leak",
                        surface.relative_to(api.ROOT).as_posix(),
                        f"default-read router references non-direct doc {path}",
                    )
                )

    trusted_scan = [
        api.ROOT / "AGENTS.md",
        api.ROOT / "workspace_map.md",
        api.ROOT / "docs" / "AGENTS.md",
        api.ROOT / "docs" / "README.md",
        api.ROOT / "docs" / "reference" / "AGENTS.md",
        api.ROOT / "docs" / "operations" / "current_state.md",
    ]
    for surface in trusted_scan:
        if not surface.exists():
            continue
        text = surface.read_text(encoding="utf-8", errors="ignore")
        for removed in REMOVED_REFERENCE_SUPPORT_PATTERNS:
            if removed in text:
                issues.append(
                    api._issue(
                        "docs_removed_reference_path_leak",
                        surface.relative_to(api.ROOT).as_posix(),
                        f"trusted router references removed support doc path: {removed}",
                    )
                )

    current_state = api.ROOT / "docs" / "operations" / "current_state.md"
    if current_state.exists():
        text = current_state.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"/Users/[^\\s`]+", text):
            issues.append(api._issue("docs_current_state_local_path", "docs/operations/current_state.md", "current_state must not contain local absolute paths"))

    trusted_docs = [
        path for path in (api.ROOT / "docs" / "reference").glob("*.md")
        if path.name != "AGENTS.md"
    ]
    trusted_docs.extend((api.ROOT / "docs" / "runbooks").glob("*.md"))
    trusted_docs.extend([
        api.ROOT / "docs" / "operations" / "current_state.md",
        api.ROOT / "docs" / "operations" / "current_data_state.md",
        api.ROOT / "docs" / "operations" / "current_source_validity.md",
    ])
    for path in trusted_docs:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(api.ROOT).as_posix()
        for removed in REMOVED_REFERENCE_SUPPORT_PATTERNS:
            if removed in text:
                issues.append(api._issue("docs_removed_reference_path_leak", rel, f"trusted doc references removed support doc path: {removed}"))
        for pattern in STALE_TRUTH_PATTERNS:
            if pattern.search(text):
                issues.append(api._issue("docs_stale_truth_phrase", rel, f"trusted doc contains stale truth marker: {pattern.pattern}"))
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


def current_state_label_value(text: str, label: str) -> str | None:
    match = re.search(rf"^- {re.escape(label)}:\s+`([^`]+)`", text, re.MULTILINE)
    if match:
        return match.group(1)
    return None


def load_receipt(api: Any, receipt_rel: str) -> tuple[dict[str, Any] | None, str | None]:
    path = api.ROOT / receipt_rel
    if not path.exists():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def check_current_state_receipt_bound(api: Any, topology: dict[str, Any]) -> list[Any]:
    registry = topology.get("active_operations_registry") or {}
    rel = str(registry.get("current_state") or "docs/operations/current_state.md")
    path = api.ROOT / rel
    if not path.exists():
        return [api._issue("current_state_receipt_missing", rel, "current_state missing")]
    text = path.read_text(encoding="utf-8", errors="ignore")
    active_packet = current_state_label_value(text, "Active execution packet")
    active_source = current_state_label_value(text, "Active package source")
    receipt_source = current_state_label_value(text, "Receipt-bound source")
    issues: list[Any] = []
    if not active_packet:
        issues.append(api._issue("current_state_receipt_missing", rel, "missing Active execution packet"))
        return issues
    if active_source and active_source.startswith((".omx/", ".omc/")):
        issues.append(api._issue("current_state_receipt_mismatch", rel, "Active package source must be tracked packet evidence, not runtime-local scratch"))
    expected_receipt = (Path(active_packet).parent / "receipt.json").as_posix()
    if not receipt_source:
        issues.append(api._issue("current_state_receipt_missing", rel, "missing Receipt-bound source"))
        receipt_source = expected_receipt
    if receipt_source != expected_receipt:
        issues.append(
            api._issue(
                "current_state_receipt_mismatch",
                rel,
                f"Receipt-bound source {receipt_source} does not match active packet receipt {expected_receipt}",
            )
        )
    receipt, error = load_receipt(api, receipt_source)
    if error:
        issues.append(api._issue("current_state_receipt_missing", receipt_source, f"receipt unavailable: {error}"))
        return issues
    if str(receipt.get("packet") or "") != active_packet:
        issues.append(
            api._issue(
                "current_state_receipt_mismatch",
                receipt_source,
                f"receipt packet {receipt.get('packet')!r} does not match active execution packet {active_packet!r}",
            )
        )
    if receipt_source not in text:
        issues.append(api._issue("current_state_receipt_missing", rel, "current_state does not mention its receipt source"))
    if rel not in (receipt.get("changed_files") or []):
        issues.append(
            api._issue(
                "current_state_receipt_mismatch",
                receipt_source,
                f"receipt changed_files must include {rel}",
            )
        )
    return issues


def build_current_state_candidate(api: Any, receipt_path: str) -> dict[str, Any]:
    receipt, error = load_receipt(api, receipt_path)
    if error or receipt is None:
        return {
            "ok": False,
            "authority_status": "generated_current_state_candidate_not_authority",
            "source_receipt": receipt_path,
            "issues": [
                {
                    "code": "current_state_receipt_missing",
                    "path": receipt_path,
                    "message": f"receipt unavailable: {error}",
                    "severity": "error",
                }
            ],
        }
    packet = str(receipt.get("packet") or "")
    task = str(receipt.get("task") or "")
    markdown = "\n".join(
        [
            "# Current State",
            "",
            "Role: single live control pointer for the repo.",
            "",
            "## Active program",
            "",
            "- Branch: `data-improve`",
            f"- Mainline task: `{task}`",
            f"- Active package source: `{packet}`",
            f"- Active execution packet: `{packet}`",
            f"- Receipt-bound source: `{receipt_path}`",
            "- Status: generated candidate; review before applying",
            "",
            "## Required evidence",
            "",
            *[f"- `{item}`" for item in receipt.get("route_evidence") or []],
            f"- `{receipt_path}`",
        ]
    )
    return {
        "ok": True,
        "authority_status": "generated_current_state_candidate_not_authority",
        "source_receipt": receipt_path,
        "packet": packet,
        "task": task,
        "current_state_markdown": markdown,
        "issues": [],
    }


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
    issues.extend(check_current_state_receipt_bound(api, topology))
    return issues
