#!/usr/bin/env python3
"""Topology doctor for Zeus's compiled agent-navigation graph."""
# Lifecycle: created=2026-04-13; last_reviewed=2026-04-21; last_reused=2026-04-21
# Purpose: Main facade for compiled topology, navigation, and closeout checks.
# Reuse: Prefer adding narrow checker-family modules instead of expanding this facade directly.

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import asdict as dataclass_asdict, dataclass, fields
from datetime import date
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

try:
    from _yaml_bootstrap import import_yaml
except ModuleNotFoundError:  # pytest imports this as scripts.topology_doctor
    from scripts._yaml_bootstrap import import_yaml

yaml = import_yaml()

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY_PATH = ROOT / "architecture" / "topology.yaml"
SCHEMA_PATH = ROOT / "architecture" / "topology_schema.yaml"
INVARIANTS_PATH = ROOT / "architecture" / "invariants.yaml"
SOURCE_RATIONALE_PATH = ROOT / "architecture" / "source_rationale.yaml"
TEST_TOPOLOGY_PATH = ROOT / "architecture" / "test_topology.yaml"
SCRIPT_MANIFEST_PATH = ROOT / "architecture" / "script_manifest.yaml"
NAMING_CONVENTIONS_PATH = ROOT / "architecture" / "naming_conventions.yaml"
DATA_REBUILD_TOPOLOGY_PATH = ROOT / "architecture" / "data_rebuild_topology.yaml"
HISTORY_LORE_PATH = ROOT / "architecture" / "history_lore.yaml"
CONTEXT_BUDGET_PATH = ROOT / "architecture" / "context_budget.yaml"
ARTIFACT_LIFECYCLE_PATH = ROOT / "architecture" / "artifact_lifecycle.yaml"
CHANGE_RECEIPT_SCHEMA_PATH = ROOT / "architecture" / "change_receipt_schema.yaml"
CONTEXT_PACK_PROFILES_PATH = ROOT / "architecture" / "context_pack_profiles.yaml"
CODE_IDIOMS_PATH = ROOT / "architecture" / "code_idioms.yaml"
RUNTIME_MODES_PATH = ROOT / "architecture" / "runtime_modes.yaml"
TASK_BOOT_PROFILES_PATH = ROOT / "architecture" / "task_boot_profiles.yaml"
FATAL_MISREADS_PATH = ROOT / "architecture" / "fatal_misreads.yaml"
CITY_TRUTH_CONTRACT_PATH = ROOT / "architecture" / "city_truth_contract.yaml"
CODE_REVIEW_GRAPH_PROTOCOL_PATH = ROOT / "architecture" / "code_review_graph_protocol.yaml"
REFERENCE_REPLACEMENT_PATH = ROOT / "architecture" / "reference_replacement.yaml"
CORE_CLAIMS_PATH = ROOT / "architecture" / "core_claims.yaml"
MAP_MAINTENANCE_PATH = ROOT / "architecture" / "map_maintenance.yaml"
DOCS_REGISTRY_PATH = ROOT / "architecture" / "docs_registry.yaml"
MODULE_MANIFEST_PATH = ROOT / "architecture" / "module_manifest.yaml"
CODE_REVIEW_GRAPH_DB_PATH = ROOT / ".code-review-graph" / "graph.db"
SKIP_PATTERN = re.compile(r"pytest\.mark\.skip|pytest\.skip\(")
DANGEROUS_REVERSE_ANTIBODY_PATTERNS = (
    re.compile(r"assert\s+.*round_single\([^)]*(?:52|72|74)\.5[^)]*\)\s*==\s*(?:52|72|74)\.0"),
    re.compile(r"assert\s+.*round_single\([^)]*-1(?:5)?\.5[^)]*\)\s*==\s*-?(?:16|2)\.0"),
    re.compile(r"assert\s+.*(?:round|np\.round)\(\s*74\.5\s*\)\s*==\s*74"),
)
ONE_OFF_SCRIPT_NAME_PATTERN = re.compile(
    r"(^task_\d{4}-\d{2}-\d{2}_|(^|_)(scratch|probe|oneoff|one_off|tmp)(_|\.))"
)
EPHEMERAL_SCRIPT_NAME_PATTERN = re.compile(r"^task_\d{4}-\d{2}-\d{2}_[a-z0-9_]+\.py$")
EMPTY_METADATA_VALUES = {"", "tbd", "todo", "unknown", "n/a"}
ZONE_DECLARATION_PATTERN = re.compile(r"^Zone:\s*([A-Za-z0-9_]+)", re.MULTILINE)
SEMANTIC_PROVENANCE_GUARD_PATTERN = re.compile(
    r"if\s+False\s*:\s*(?=[^\n]*(?:selected_method|entry_method|bias_correction|p_raw|p_posterior))"
)


@dataclass(frozen=True)
class TopologyIssue:
    code: str
    path: str
    message: str
    severity: str = "error"
    lane: str | None = None
    scope: str | None = None
    owner_manifest: str | None = None
    repair_kind: str | None = None
    blocking_modes: tuple[str, ...] | None = None
    related_paths: tuple[str, ...] | None = None
    companion_of: str | None = None
    maturity: str | None = None
    expires_at: str | None = None
    confidence: str | float | None = None
    authority_status: str | None = None
    repair_hint: str | None = None


ISSUE_SCHEMA_VERSIONS = {"1", "2"}
ISSUE_LEGACY_FIELDS = ("code", "path", "message", "severity")
ISSUE_TYPED_FIELDS = (
    "lane",
    "scope",
    "owner_manifest",
    "repair_kind",
    "blocking_modes",
    "related_paths",
    "companion_of",
    "maturity",
    "expires_at",
    "confidence",
    "authority_status",
    "repair_hint",
)
ISSUE_REPAIR_KINDS = {
    "add_registry_row",
    "update_companion",
    "extract_law_to_book",
    "propose_owner_manifest",
    "refresh_graph",
    "none",
}
ISSUE_MATURITY_VALUES = {"stable", "provisional", "placeholder"}
ISSUE_AUTHORITY_STATUSES = {"authority", "derived", "evidence", "unknown"}


_NAVIGATION_MODE_POLICY = {
    "navigation": "requested_file_scoped",
    "navigation_strict_health": "all_errors_block",
    "closeout": "changed_file_scoped",
    "strict_full_repo": "all_errors_block",
    "global_health": "advisory_counts",
}

ISSUE_BLOCKING_MODES = tuple(_NAVIGATION_MODE_POLICY)


def topology_issue_field_names() -> tuple[str, ...]:
    return tuple(field.name for field in fields(TopologyIssue))


def _normalize_issue_sequence(value: tuple[str, ...] | list[str] | set[str] | str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _issue_metadata_for_code(code: str, path: str) -> dict[str, Any]:
    del path
    families = (
        ("docs_registry", "docs", "architecture/docs_registry.yaml", "add_registry_row"),
        ("operations_", "docs", "architecture/docs_registry.yaml", "update_companion"),
        ("current_state_", "docs", "docs/operations/current_state.md", "update_companion"),
        ("source_", "source", "architecture/source_rationale.yaml", "add_registry_row"),
        ("test_", "tests", "architecture/test_topology.yaml", "add_registry_row"),
        ("script_", "scripts", "architecture/script_manifest.yaml", "add_registry_row"),
        ("map_maintenance", "map_maintenance", "architecture/map_maintenance.yaml", "update_companion"),
        ("code_review_graph", "code_review_graph", "architecture/code_review_graph_protocol.yaml", "refresh_graph"),
        ("module_book", "module_books", "architecture/module_manifest.yaml", "update_companion"),
        ("module_manifest", "module_books", "architecture/module_manifest.yaml", "update_companion"),
    )
    for prefix, lane, owner_manifest, repair_kind in families:
        if code.startswith(prefix):
            return {
                "lane": lane,
                "scope": "changed_file",
                "owner_manifest": owner_manifest,
                "repair_kind": repair_kind,
                "blocking_modes": ISSUE_BLOCKING_MODES,
                "maturity": "stable",
                "authority_status": "authority",
            }
    return {}


def _issue_to_json(issue: TopologyIssue, schema_version: str = "1") -> dict[str, Any]:
    if schema_version not in ISSUE_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported issue schema version: {schema_version}")
    payload: dict[str, Any] = {field: getattr(issue, field) for field in ISSUE_LEGACY_FIELDS}
    if schema_version == "2":
        for field in ISSUE_TYPED_FIELDS:
            value = getattr(issue, field)
            if value is None:
                continue
            payload[field] = list(value) if isinstance(value, tuple) else value
    return payload


def asdict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, TopologyIssue):
        return _issue_to_json(obj)
    return dataclass_asdict(obj)


@dataclass(frozen=True)
class StrictResult:
    ok: bool
    issues: list[TopologyIssue]
    details: dict[str, Any] | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_topology() -> dict[str, Any]:
    return _load_yaml(TOPOLOGY_PATH)


def load_schema() -> dict[str, Any]:
    return _load_yaml(SCHEMA_PATH)


def load_invariants() -> dict[str, Any]:
    return _load_yaml(INVARIANTS_PATH)


def load_source_rationale() -> dict[str, Any]:
    return _load_yaml(SOURCE_RATIONALE_PATH)


def load_test_topology() -> dict[str, Any]:
    return _load_yaml(TEST_TOPOLOGY_PATH)


def load_script_manifest() -> dict[str, Any]:
    return _load_yaml(SCRIPT_MANIFEST_PATH)


def load_naming_conventions() -> dict[str, Any]:
    return _load_yaml(NAMING_CONVENTIONS_PATH)


def load_data_rebuild_topology() -> dict[str, Any]:
    return _load_yaml(DATA_REBUILD_TOPOLOGY_PATH)


def load_history_lore() -> dict[str, Any]:
    return _load_yaml(HISTORY_LORE_PATH)


def load_context_budget() -> dict[str, Any]:
    return _load_yaml(CONTEXT_BUDGET_PATH)


def load_artifact_lifecycle() -> dict[str, Any]:
    return _load_yaml(ARTIFACT_LIFECYCLE_PATH)


def load_change_receipt_schema() -> dict[str, Any]:
    return _load_yaml(CHANGE_RECEIPT_SCHEMA_PATH)


def load_context_pack_profiles() -> dict[str, Any]:
    return _load_yaml(CONTEXT_PACK_PROFILES_PATH)


def load_code_idioms() -> dict[str, Any]:
    return _load_yaml(CODE_IDIOMS_PATH)


def load_runtime_modes() -> dict[str, Any]:
    return _load_yaml(RUNTIME_MODES_PATH)


def load_task_boot_profiles() -> dict[str, Any]:
    return _load_yaml(TASK_BOOT_PROFILES_PATH)


def load_fatal_misreads() -> dict[str, Any]:
    return _load_yaml(FATAL_MISREADS_PATH)


def load_city_truth_contract() -> dict[str, Any]:
    return _load_yaml(CITY_TRUTH_CONTRACT_PATH)


def load_code_review_graph_protocol() -> dict[str, Any]:
    return _load_yaml(CODE_REVIEW_GRAPH_PROTOCOL_PATH)


def load_reference_replacement() -> dict[str, Any]:
    return _load_yaml(REFERENCE_REPLACEMENT_PATH)


def load_core_claims() -> dict[str, Any]:
    return _load_yaml(CORE_CLAIMS_PATH)


def load_map_maintenance() -> dict[str, Any]:
    return _load_yaml(MAP_MAINTENANCE_PATH)


def load_docs_registry() -> dict[str, Any]:
    return _load_yaml(DOCS_REGISTRY_PATH)


def load_module_manifest() -> dict[str, Any]:
    return _load_yaml(MODULE_MANIFEST_PATH)


def _git_ls_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(line for line in proc.stdout.splitlines() if line)


def _git_visible_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(line for line in proc.stdout.splitlines() if line)


def _make_issue(
    code: str,
    path: str,
    message: str,
    *,
    severity: str = "error",
    lane: str | None = None,
    scope: str | None = None,
    owner_manifest: str | None = None,
    repair_kind: str | None = None,
    blocking_modes: tuple[str, ...] | list[str] | set[str] | str | None = None,
    related_paths: tuple[str, ...] | list[str] | set[str] | str | None = None,
    companion_of: str | None = None,
    maturity: str | None = None,
    expires_at: str | None = None,
    confidence: str | float | None = None,
    authority_status: str | None = None,
    repair_hint: str | None = None,
    annotate: bool = True,
) -> TopologyIssue:
    metadata = _issue_metadata_for_code(code, path) if annotate else {}
    metadata.update(
        {
            key: value
            for key, value in {
                "lane": lane,
                "scope": scope,
                "owner_manifest": owner_manifest,
                "repair_kind": repair_kind,
                "blocking_modes": blocking_modes,
                "related_paths": related_paths,
                "companion_of": companion_of,
                "maturity": maturity,
                "expires_at": expires_at,
                "confidence": confidence,
                "authority_status": authority_status,
                "repair_hint": repair_hint,
            }.items()
            if value is not None
        }
    )
    return TopologyIssue(
        code=code,
        path=path,
        message=message,
        severity=severity,
        lane=metadata.get("lane"),
        scope=metadata.get("scope"),
        owner_manifest=metadata.get("owner_manifest"),
        repair_kind=metadata.get("repair_kind"),
        blocking_modes=_normalize_issue_sequence(metadata.get("blocking_modes")),
        related_paths=_normalize_issue_sequence(metadata.get("related_paths")),
        companion_of=metadata.get("companion_of"),
        maturity=metadata.get("maturity"),
        expires_at=metadata.get("expires_at"),
        confidence=metadata.get("confidence"),
        authority_status=metadata.get("authority_status"),
        repair_hint=metadata.get("repair_hint"),
    )


def issue(code: str, path: str, message: str, **metadata: Any) -> TopologyIssue:
    return _make_issue(code, path, message, **metadata)


def warning(code: str, path: str, message: str, **metadata: Any) -> TopologyIssue:
    return _make_issue(code, path, message, severity="warning", **metadata)


def advisory(code: str, path: str, message: str, **metadata: Any) -> TopologyIssue:
    metadata.setdefault("blocking_modes", ("global_health",))
    return _make_issue(code, path, message, severity="warning", **metadata)


def blocking(code: str, path: str, message: str, **metadata: Any) -> TopologyIssue:
    metadata.setdefault("blocking_modes", ISSUE_BLOCKING_MODES)
    return _make_issue(code, path, message, severity="error", **metadata)


def global_drift(code: str, path: str, message: str, **metadata: Any) -> TopologyIssue:
    metadata.setdefault("scope", "global")
    metadata.setdefault("blocking_modes", ("global_health",))
    return _make_issue(code, path, message, severity="warning", **metadata)


def legacy_issue(code: str, path: str, message: str, severity: str = "error") -> TopologyIssue:
    return _make_issue(code, path, message, severity=severity, annotate=False)


def _issue(code: str, path: str, message: str) -> TopologyIssue:
    return issue(code, path, message)


def _warning(code: str, path: str, message: str) -> TopologyIssue:
    return warning(code, path, message)


def _registry_checks():
    try:
        from scripts import topology_doctor_registry_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_registry_checks

    return topology_doctor_registry_checks


def _ownership_checks():
    try:
        from scripts import topology_doctor_ownership_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_ownership_checks

    return topology_doctor_ownership_checks


def _declared_paths(items: list[dict[str, Any]]) -> set[str]:
    return _registry_checks().declared_paths(items)


def _path_declared(path: str, declared: set[str]) -> bool:
    return _registry_checks().path_declared(path, declared)


def _markdown_path_tokens(text: str) -> set[str]:
    return _registry_checks().markdown_path_tokens(text)


def _registry_entries(agents_path: Path, *, include_directory_tokens: bool = False) -> set[str]:
    return _registry_checks().registry_entries(
        sys.modules[__name__],
        agents_path,
        include_directory_tokens=include_directory_tokens,
    )


def _registry_target(directory: Path, token: str) -> Path:
    return _registry_checks().registry_target(sys.modules[__name__], directory, token)


def _active_registry_dirs(topology: dict[str, Any]) -> list[Path]:
    return _registry_checks().active_registry_dirs(sys.modules[__name__], topology)


def _is_root_scratch(path: Path) -> bool:
    return _registry_checks().is_root_scratch(path)


def _check_schema(topology: dict[str, Any], schema: dict[str, Any]) -> list[TopologyIssue]:
    return _registry_checks().check_schema(sys.modules[__name__], topology, schema)


def _check_coverage(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _registry_checks().check_coverage(sys.modules[__name__], topology)


def _check_active_pointers(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _registry_checks().check_active_pointers(sys.modules[__name__], topology)


def _check_registries(topology: dict[str, Any], tracked: list[str]) -> list[TopologyIssue]:
    return _registry_checks().check_registries(sys.modules[__name__], topology, tracked)


def _check_reference_authority(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _registry_checks().check_reference_authority(sys.modules[__name__], topology)



def _docs_checks():
    try:
        from scripts import topology_doctor_docs_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_docs_checks

    return topology_doctor_docs_checks


def _docs_mode_excluded_roots(topology: dict[str, Any]) -> list[Path]:
    return _docs_checks().docs_mode_excluded_roots(sys.modules[__name__], topology)


def _docs_subroot_specs(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _docs_checks().docs_subroot_specs(topology)


def _is_under(path: Path, root: Path) -> bool:
    return _docs_checks().is_under(path, root)


def _check_hidden_docs(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _docs_checks().check_hidden_docs(sys.modules[__name__], topology)


def _check_progress_handoff_paths() -> list[TopologyIssue]:
    return _docs_checks().check_progress_handoff_paths(sys.modules[__name__])


def _check_docs_subtree_agents(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _docs_checks().check_docs_subtree_agents(sys.modules[__name__], topology)


def _check_operations_task_folders(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _docs_checks().check_operations_task_folders(sys.modules[__name__], topology)


def _check_runtime_plan_inventory(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _docs_checks().check_runtime_plan_inventory(sys.modules[__name__], topology)


def _internal_path_candidates(text: str) -> set[str]:
    return _docs_checks().internal_path_candidates(text)


def _check_broken_internal_paths() -> list[TopologyIssue]:
    return _docs_checks().check_broken_internal_paths(sys.modules[__name__])


def _check_config_agents_volatile_facts() -> list[TopologyIssue]:
    return _docs_checks().check_config_agents_volatile_facts(sys.modules[__name__])


def _check_docs_registry(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _docs_checks().check_docs_registry(sys.modules[__name__], topology)


def _current_state_operation_paths(text: str, surface_prefix: str) -> set[str]:
    return _docs_checks().current_state_operation_paths(sys.modules[__name__], text, surface_prefix)


def _check_current_state_receipt_bound(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _docs_checks().check_current_state_receipt_bound(sys.modules[__name__], topology)


def build_current_state_candidate(receipt_path: str) -> dict[str, Any]:
    return _docs_checks().build_current_state_candidate(sys.modules[__name__], receipt_path)


def run_current_state_receipt_bound() -> StrictResult:
    issues = _check_current_state_receipt_bound(load_topology())
    return StrictResult(ok=not issues, issues=issues)


def _check_active_operations_registry(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _docs_checks().check_active_operations_registry(sys.modules[__name__], topology)


def _check_root_and_state_classification(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _registry_checks().check_root_and_state_classification(sys.modules[__name__], topology)


def _check_shadow_authority_references() -> list[TopologyIssue]:
    return _registry_checks().check_shadow_authority_references(sys.modules[__name__])


def _check_wmo_gate() -> list[TopologyIssue]:
    return _registry_checks().check_wmo_gate(sys.modules[__name__])


def run_strict() -> StrictResult:
    result = _registry_checks().run_strict(sys.modules[__name__])
    ownership = run_ownership()
    issues = result.issues + ownership.issues
    return StrictResult(ok=not issues, issues=issues)


def run_ownership() -> StrictResult:
    return _ownership_checks().run_ownership(sys.modules[__name__])


def run_docs() -> StrictResult:
    return _registry_checks().run_docs(sys.modules[__name__])


def _source_checks():
    try:
        from scripts import topology_doctor_source_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_source_checks

    return topology_doctor_source_checks


def run_source() -> StrictResult:
    return _source_checks().run_source(sys.modules[__name__])


def _expected_zone_for_agents_path(rationale: dict[str, Any], agents_rel: str) -> str | None:
    return _source_checks().expected_zone_for_agents_path(rationale, agents_rel)


def _declared_zone_in_agents(path: Path) -> str | None:
    return _source_checks().declared_zone_in_agents(sys.modules[__name__], path)


def run_agents_coherence() -> StrictResult:
    return _source_checks().run_agents_coherence(sys.modules[__name__])


def _test_checks():
    try:
        from scripts import topology_doctor_test_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_test_checks

    return topology_doctor_test_checks


def run_tests() -> StrictResult:
    return _test_checks().run_tests(sys.modules[__name__])


def _script_checks():
    try:
        from scripts import topology_doctor_script_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_script_checks

    return topology_doctor_script_checks


def _top_level_scripts() -> set[str]:
    return _script_checks().top_level_scripts(sys.modules[__name__])


def _effective_script_entry(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    return _script_checks().effective_script_entry(manifest, name)


def _metadata_missing(value: Any) -> bool:
    return _script_checks().metadata_missing(sys.modules[__name__], value)


def _long_lived_script_name_allowed(manifest: dict[str, Any], name: str) -> bool:
    return _script_checks().long_lived_script_name_allowed(sys.modules[__name__], manifest, name)


def _write_target_allowed(target: str, allowed: set[str]) -> bool:
    return _script_checks().write_target_allowed(target, allowed)


def _parse_delete_by(value: Any) -> date | None:
    return _script_checks().parse_delete_by(value)


def _check_script_lifecycle(
    manifest: dict[str, Any],
    name: str,
    effective: dict[str, Any],
) -> list[TopologyIssue]:
    return _script_checks().check_script_lifecycle(sys.modules[__name__], manifest, name, effective)


def run_scripts() -> StrictResult:
    return _script_checks().run_scripts(sys.modules[__name__])


def _data_rebuild_checks():
    try:
        from scripts import topology_doctor_data_rebuild_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_data_rebuild_checks

    return topology_doctor_data_rebuild_checks


def run_data_rebuild() -> StrictResult:
    return _data_rebuild_checks().run_data_rebuild(sys.modules[__name__])


def _policy_checks():
    try:
        from scripts import topology_doctor_policy_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_policy_checks

    return topology_doctor_policy_checks


def _has_antibody(antibodies: Any) -> bool:
    return _policy_checks().has_antibody(antibodies)


def _history_lore_path_exists(value: str) -> bool:
    return _policy_checks().history_lore_path_exists(sys.modules[__name__], value)


def _gate_path_tokens(gate: str) -> list[str]:
    return _policy_checks().gate_path_tokens(gate)


def _check_history_lore_antibody_references(
    card_id: str,
    antibodies: Any,
) -> list[TopologyIssue]:
    return _policy_checks().check_history_lore_antibody_references(sys.modules[__name__], card_id, antibodies)


def run_history_lore() -> StrictResult:
    return _policy_checks().run_history_lore(sys.modules[__name__])


def _budget_issue(
    code: str,
    path: str,
    message: str,
    *,
    enforcement: str,
) -> TopologyIssue:
    return _policy_checks().budget_issue(sys.modules[__name__], code, path, message, enforcement=enforcement)


def _budget_has_blocking_promotion(spec: dict[str, Any]) -> bool:
    return _policy_checks().budget_has_blocking_promotion(spec)


def _budget_enforcement_issues(
    spec: dict[str, Any],
    path: str,
) -> list[TopologyIssue]:
    return _policy_checks().budget_enforcement_issues(sys.modules[__name__], spec, path)


def _line_count(path: Path) -> int:
    return _policy_checks().line_count(path)


def run_context_budget() -> StrictResult:
    return _policy_checks().run_context_budget(sys.modules[__name__])


def _zone_for_changed_file(path: str) -> str:
    return _policy_checks().zone_for_changed_file(sys.modules[__name__], path)


def _planning_lock_trigger(path: str) -> str | None:
    return _policy_checks().planning_lock_trigger(path)


def _valid_plan_evidence(path: str | None) -> bool:
    return _policy_checks().valid_plan_evidence(sys.modules[__name__], path)


def run_planning_lock(changed_files: list[str], plan_evidence: str | None = None) -> StrictResult:
    return _policy_checks().run_planning_lock(sys.modules[__name__], changed_files, plan_evidence)


def _idiom_pattern_for(idiom_id: str) -> re.Pattern[str] | None:
    return _policy_checks().idiom_pattern_for(sys.modules[__name__], idiom_id)


def run_idioms() -> StrictResult:
    return _policy_checks().run_idioms(sys.modules[__name__])


def run_self_check_coherence() -> StrictResult:
    return _policy_checks().run_self_check_coherence(sys.modules[__name__])


def run_runtime_modes() -> StrictResult:
    return _policy_checks().run_runtime_modes(sys.modules[__name__])


def run_task_boot_profiles() -> StrictResult:
    return _policy_checks().run_task_boot_profiles(sys.modules[__name__])


def run_fatal_misreads() -> StrictResult:
    return _policy_checks().run_fatal_misreads(sys.modules[__name__])


def run_city_truth_contract() -> StrictResult:
    return _policy_checks().run_city_truth_contract(sys.modules[__name__])


def run_code_review_graph_protocol() -> StrictResult:
    return _policy_checks().run_code_review_graph_protocol(sys.modules[__name__])



def _reference_checks():
    try:
        from scripts import topology_doctor_reference_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_reference_checks

    return topology_doctor_reference_checks


def _reference_default_reads() -> set[str]:
    return _reference_checks().reference_default_reads(sys.modules[__name__])


def _reference_conditional_reads() -> set[str]:
    return _reference_checks().reference_conditional_reads(sys.modules[__name__])


def _validate_reference_claim_proofs(
    entry_path: str,
    entry: dict[str, Any],
    manifest: dict[str, Any],
    seen_claim_ids: set[str],
) -> list[TopologyIssue]:
    return _reference_checks().validate_reference_claim_proofs(
        sys.modules[__name__],
        entry_path,
        entry,
        manifest,
        seen_claim_ids,
    )


def _claim_proof_index() -> dict[str, dict[str, Any]]:
    return _reference_checks().claim_proof_index(sys.modules[__name__])


def run_core_claims() -> StrictResult:
    return _reference_checks().run_core_claims(sys.modules[__name__])


def run_reference_replacement() -> StrictResult:
    return _reference_checks().run_reference_replacement(sys.modules[__name__])


def _map_maintenance_checks():
    try:
        from scripts import topology_doctor_map_maintenance
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_map_maintenance

    return topology_doctor_map_maintenance


def _change_kind(path: str, tracked: set[str]) -> str:
    return _map_maintenance_checks().change_kind(sys.modules[__name__], path, tracked)


def _git_status_changes() -> dict[str, str]:
    return _map_maintenance_checks().git_status_changes(sys.modules[__name__])


def _map_maintenance_changes(changed_files: list[str]) -> dict[str, str]:
    return _map_maintenance_checks().map_maintenance_changes(sys.modules[__name__], changed_files)


def run_map_maintenance(changed_files: list[str] | None = None, mode: str = "advisory") -> StrictResult:
    return _map_maintenance_checks().run_map_maintenance(sys.modules[__name__], changed_files, mode)


def _freshness_checks():
    try:
        from scripts import topology_doctor_freshness_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_freshness_checks

    return topology_doctor_freshness_checks


def run_freshness_metadata(changed_files: list[str] | None = None) -> StrictResult:
    return _freshness_checks().run_freshness_metadata(sys.modules[__name__], changed_files)


def run_naming_conventions() -> StrictResult:
    return _freshness_checks().run_naming_conventions(sys.modules[__name__])


def _code_review_graph_checks():
    try:
        from scripts import topology_doctor_code_review_graph
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_code_review_graph

    return topology_doctor_code_review_graph


def run_code_review_graph_status(changed_files: list[str] | None = None, *, include_appendix: bool = True) -> StrictResult:
    return _code_review_graph_checks().run_code_review_graph_status(
        sys.modules[__name__], changed_files, include_appendix=include_appendix
    )


def build_code_impact_graph(files: list[str], task: str = "") -> dict[str, Any]:
    return _code_review_graph_checks().build_code_impact_graph(sys.modules[__name__], files, task=task)


def build_graph_appendix(files: list[str], task: str | None = None) -> dict[str, Any]:
    return _code_review_graph_checks().build_graph_appendix(sys.modules[__name__], files, task=task)


def _packet_prefill_checks():
    try:
        from scripts import topology_doctor_packet_prefill
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_packet_prefill

    return topology_doctor_packet_prefill


CONTEXT_EXPAND_TRIGGERS = _packet_prefill_checks().CONTEXT_EXPAND_TRIGGERS


def _source_rationale_for(files: list[str]) -> list[dict[str, Any]]:
    return _packet_prefill_checks().source_rationale_for(sys.modules[__name__], files)


def build_context_assumption(
    *,
    profile: str = "",
    profile_kind: str = "digest_profile",
    source_entries: list[dict[str, Any]] | None = None,
    confidence_basis: list[str] | None = None,
) -> dict[str, Any]:
    return _packet_prefill_checks().build_context_assumption(
        profile=profile,
        profile_kind=profile_kind,
        source_entries=source_entries,
        confidence_basis=confidence_basis,
    )


def _normalize_scope(scope: str | None) -> str:
    return _packet_prefill_checks().normalize_scope(scope)


def _scope_is_file(scope: str) -> bool:
    return _packet_prefill_checks().scope_is_file(sys.modules[__name__], scope)


def _scope_agent_path(scope: str) -> str | None:
    return _packet_prefill_checks().scope_agent_path(sys.modules[__name__], scope)


def _zones_for_scope(scope: str) -> list[str]:
    return _packet_prefill_checks().zones_for_scope(sys.modules[__name__], scope)


def _zones_for_files(files: list[str]) -> list[str]:
    return _packet_prefill_checks().zones_for_files(sys.modules[__name__], files)


def _packet_zones(scope: str, files: list[str]) -> list[str]:
    return _packet_prefill_checks().packet_zones(sys.modules[__name__], scope, files)


def _invariants_for_zones(zones: list[str]) -> list[dict[str, Any]]:
    return _packet_prefill_checks().invariants_for_zones(sys.modules[__name__], zones)


def build_invariants_slice(zone: str | None = None) -> dict[str, Any]:
    return _packet_prefill_checks().build_invariants_slice(sys.modules[__name__], zone)


def _files_may_not_change(zones_touched: list[str]) -> list[str]:
    return _packet_prefill_checks().files_may_not_change(sys.modules[__name__], zones_touched)


def _package_gates(scope: str, files: list[str]) -> list[str]:
    return _packet_prefill_checks().package_gates(sys.modules[__name__], scope, files)


def _tests_for_packet(scope: str, files: list[str]) -> list[str]:
    return _packet_prefill_checks().tests_for_packet(sys.modules[__name__], scope, files)


def build_packet_prefill(
    *,
    packet_type: str,
    task: str,
    scope: str = "",
    files: list[str] | None = None,
) -> dict[str, Any]:
    return _packet_prefill_checks().build_packet_prefill(
        sys.modules[__name__],
        packet_type=packet_type,
        task=task,
        scope=scope,
        files=files,
    )



def _context_pack_checks():
    try:
        from scripts import topology_doctor_context_pack
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_context_pack

    return topology_doctor_context_pack


def build_impact(files: list[str]) -> dict[str, Any]:
    return _context_pack_checks().build_impact(sys.modules[__name__], files)


def _context_pack_profiles() -> dict[str, dict[str, Any]]:
    return _context_pack_checks().context_pack_profiles(sys.modules[__name__])


def run_context_packs() -> StrictResult:
    return _context_pack_checks().run_context_packs(sys.modules[__name__])


def run_module_books() -> StrictResult:
    return _context_pack_checks().run_module_books(sys.modules[__name__])


def run_module_manifest() -> StrictResult:
    return _context_pack_checks().run_module_manifest(sys.modules[__name__])


def _artifact_checks():
    try:
        from scripts import topology_doctor_artifact_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_artifact_checks

    return topology_doctor_artifact_checks


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    return _artifact_checks().path_matches_any(path, patterns)


def _artifact_record_contract() -> dict[str, Any]:
    return _artifact_checks().artifact_record_contract(sys.modules[__name__])


def _approved_work_record_path(path: str, contract: dict[str, Any] | None = None) -> bool:
    return _artifact_checks().approved_work_record_path(sys.modules[__name__], path, contract)


def _record_exempt_path(path: str, contract: dict[str, Any] | None = None) -> bool:
    return _artifact_checks().record_exempt_path(sys.modules[__name__], path, contract)


def run_artifact_lifecycle() -> StrictResult:
    return _artifact_checks().run_artifact_lifecycle(sys.modules[__name__])


def _record_text_has_field(text: str, field: str) -> bool:
    return _artifact_checks().record_text_has_field(text, field)


def run_work_record(
    changed_files: list[str] | None = None,
    record_path: str | None = None,
) -> StrictResult:
    return _artifact_checks().run_work_record(sys.modules[__name__], changed_files, record_path)


def _receipt_checks():
    try:
        from scripts import topology_doctor_receipt_checks
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_receipt_checks

    return topology_doctor_receipt_checks


def _receipt_path_matches_any(path: str, patterns: list[str]) -> bool:
    return _receipt_checks().path_matches_any(path, patterns)


def _effective_receipt_changed_files(changed_files: list[str] | None = None) -> list[str]:
    return _receipt_checks().effective_changed_files(sys.modules[__name__], changed_files)


def run_change_receipts(
    changed_files: list[str] | None = None,
    receipt_path: str | None = None,
) -> StrictResult:
    return _receipt_checks().run_change_receipts(sys.modules[__name__], changed_files, receipt_path)


def _closeout_checks():
    try:
        from scripts import topology_doctor_closeout
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_closeout

    return topology_doctor_closeout


def run_closeout(
    *,
    changed_files: list[str] | None = None,
    plan_evidence: str | None = None,
    work_record_path: str | None = None,
    receipt_path: str | None = None,
    issue_schema_version: str = "1",
) -> dict[str, Any]:
    return _closeout_checks().run_closeout(
        sys.modules[__name__],
        changed_files=changed_files,
        plan_evidence=plan_evidence,
        work_record_path=work_record_path,
        receipt_path=receipt_path,
        issue_schema_version=issue_schema_version,
    )



def _strict_result_summary(result: StrictResult) -> dict[str, Any]:
    return _context_pack_checks().strict_result_summary(sys.modules[__name__], result)


def _route_health_for_context_pack(files: list[str]) -> dict[str, Any]:
    return _context_pack_checks().route_health_for_context_pack(sys.modules[__name__], files)


def _repo_health_for_context_pack() -> dict[str, Any]:
    return _context_pack_checks().repo_health_for_context_pack(sys.modules[__name__])


def _proof_claims_for_files(files: list[str]) -> list[dict[str, Any]]:
    return _context_pack_checks().proof_claims_for_files(sys.modules[__name__], files)


def _lore_summary(card: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return _context_pack_checks().lore_summary(card, reason=reason)


def _layered_history_lore(task: str, files: list[str]) -> dict[str, Any]:
    return _context_pack_checks().layered_history_lore(sys.modules[__name__], task, files)


def _context_pack_contract_surfaces(impact: dict[str, Any], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _context_pack_checks().context_pack_contract_surfaces(impact, claims)


def _context_pack_coverage_gaps(impact: dict[str, Any], files: list[str]) -> list[dict[str, Any]]:
    return _context_pack_checks().context_pack_coverage_gaps(impact, files)


def _context_pack_downstream_risks(impact: dict[str, Any]) -> list[dict[str, Any]]:
    return _context_pack_checks().context_pack_downstream_risks(impact)


def _context_pack_questions(
    profile: dict[str, Any],
    impact: dict[str, Any],
    claims: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> list[str]:
    return _context_pack_checks().context_pack_questions(profile, impact, claims, gaps)


def _debug_red_green_checks(
    *,
    files: list[str],
    impact: dict[str, Any],
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return _context_pack_checks().debug_red_green_checks(files=files, impact=impact, claims=claims)


def _debug_suspected_boundaries(
    impact: dict[str, Any],
    claims: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return _context_pack_checks().debug_suspected_boundaries(impact, claims, gaps)


def build_debug_context_pack(task: str, files: list[str]) -> dict[str, Any]:
    return _context_pack_checks().build_debug_context_pack(sys.modules[__name__], task, files)


def _looks_like_package_review(task: str, files: list[str]) -> bool:
    return _context_pack_checks().looks_like_package_review(sys.modules[__name__], task, files)


def _looks_like_debug(task: str, files: list[str]) -> bool:
    return _context_pack_checks().looks_like_debug(sys.modules[__name__], task, files)


def _infer_task_class(task: str, files: list[str]) -> str | None:
    return _context_pack_checks().infer_task_class(sys.modules[__name__], task, files)


def build_semantic_bootstrap(task_class: str, *, task: str = "", files: list[str] | None = None) -> dict[str, Any]:
    return _context_pack_checks().build_semantic_bootstrap(
        sys.modules[__name__],
        task_class,
        task=task,
        files=files or [],
    )


def build_package_review_context_pack(task: str, files: list[str]) -> dict[str, Any]:
    return _context_pack_checks().build_package_review_context_pack(sys.modules[__name__], task, files)


def build_context_pack(pack_type: str, *, task: str, files: list[str], task_class: str | None = None) -> dict[str, Any]:
    return _context_pack_checks().build_context_pack(
        sys.modules[__name__],
        pack_type,
        task=task,
        files=files,
        task_class=task_class,
    )



def _core_map_checks():
    try:
        from scripts import topology_doctor_core_map
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_core_map

    return topology_doctor_core_map


def _core_map_profiles() -> dict[str, dict[str, Any]]:
    return _core_map_checks().core_map_profiles(sys.modules[__name__])


def _source_entry_by_path() -> dict[str, dict[str, Any]]:
    return _core_map_checks().source_entry_by_path(sys.modules[__name__])


def _validate_proof_target_exists(target: dict[str, Any]) -> bool:
    return _core_map_checks().validate_proof_target_exists(sys.modules[__name__], target)


def _locator_exists(path: str, locator: str | None) -> bool:
    return _core_map_checks().locator_exists(sys.modules[__name__], path, locator)


def _proof_contains(path: str, needle: str) -> bool:
    return _core_map_checks().proof_contains(sys.modules[__name__], path, needle)


def _edge_proof_status(
    edge: dict[str, Any],
    *,
    node_ids: set[str],
    node_files: dict[str, str],
    source_entries: dict[str, dict[str, Any]],
) -> tuple[str, str | None]:
    return _core_map_checks().edge_proof_status(
        sys.modules[__name__],
        edge,
        node_ids=node_ids,
        node_files=node_files,
        source_entries=source_entries,
    )


def _core_map_forbidden_hits(payload: dict[str, Any], phrases: list[Any]) -> list[str]:
    return _core_map_checks().core_map_forbidden_hits(payload, phrases)


def build_core_map(profile_id: str) -> dict[str, Any]:
    return _core_map_checks().build_core_map(sys.modules[__name__], profile_id)


def run_core_maps() -> StrictResult:
    return _core_map_checks().run_core_maps(sys.modules[__name__])


def _digest_checks():
    try:
        from scripts import topology_doctor_digest
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_digest

    return topology_doctor_digest


def _data_rebuild_digest() -> dict[str, Any]:
    return _digest_checks().data_rebuild_digest(sys.modules[__name__])


def _script_lifecycle_digest() -> dict[str, Any]:
    return _digest_checks().script_lifecycle_digest(sys.modules[__name__])


def _compact_lore_card(card: dict[str, Any]) -> dict[str, Any]:
    return _digest_checks().compact_lore_card(card)


def _matched_history_lore(task: str, files: list[str]) -> list[dict[str, Any]]:
    return _digest_checks().matched_history_lore(sys.modules[__name__], task, files)


def build_digest(task: str, files: list[str] | None = None) -> dict[str, Any]:
    return _digest_checks().build_digest(sys.modules[__name__], task, files)


def _navigation_issue_path_in_scope(issue_path: str, requested_paths: list[str]) -> bool:
    """Check if an issue's path is in scope for the requested navigation paths.

    Handles both real file paths and semantic IDs (e.g. schema key paths).
    Guards against path traversal via resolve() check.
    """
    if not requested_paths:
        return False
    normalized = issue_path.split(":", 1)[0].rstrip("/")
    # Path traversal guard: resolve and verify the normalized path stays under ROOT
    try:
        resolved = (ROOT / normalized).resolve()
        if not resolved.is_relative_to(ROOT.resolve()):
            return False
    except (OSError, ValueError):
        # Malformed path — treat as non-file semantic ID; fall through to
        # lane-level matching below.
        pass
    for requested in requested_paths:
        scoped = requested.rstrip("/")
        same_path = normalized == scoped
        annotated_requested_file = issue_path.startswith(f"{scoped}:")
        issue_directory_contains_requested_file = scoped.startswith(f"{normalized}/")
        requested_directory_contains_issue_file = normalized.startswith(f"{scoped}/")
        if (
            same_path
            or annotated_requested_file
            or issue_directory_contains_requested_file
            or requested_directory_contains_issue_file
        ):
            return True
    return False


def _issue_blocks_mode(issue: dict[str, Any], mode: str) -> bool:
    blocking_modes = issue.get("blocking_modes")
    if not blocking_modes:
        return True
    return mode in blocking_modes


def _navigation_direct_blockers(issues: list[dict[str, Any]], requested_paths: list[str]) -> list[dict[str, Any]]:
    blockers = []
    for issue in issues:
        if issue.get("severity") != "error":
            continue
        if not _issue_blocks_mode(issue, "navigation"):
            continue
        path = str(issue.get("path", ""))
        # Match by path scope OR by lane for semantic-ID issues that
        # don't correspond to a repo file path (Bug #1: non-file paths
        # like schema key paths were silently dropped).
        if _navigation_issue_path_in_scope(path, requested_paths):
            blockers.append(issue)
        elif issue.get("lane") == "navigation":
            # Lane-level match: navigation-lane errors always surface
            # regardless of whether their path is a real file.
            blockers.append(issue)
    return blockers


def _navigation_requested_file_issues(requested_paths: list[str]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for requested in requested_paths:
        normalized = requested.strip().removeprefix("./").rstrip("/")
        if not normalized:
            continue
        if (ROOT / normalized).exists():
            continue
        issues.append(
            {
                "lane": "navigation",
                "code": "navigation_requested_file_unclassified",
                "path": normalized,
                "message": "requested file is outside known workspace routes and cannot be classified",
                "severity": "error",
            }
        )
    return issues


def _global_health_counts(checks: dict[str, StrictResult]) -> dict[str, dict[str, int]]:
    return {
        lane: {
            "issue_count": len(result.issues),
            "blocking_count": len([issue for issue in result.issues if issue.severity == "error"]),
            "warning_count": len([issue for issue in result.issues if issue.severity == "warning"]),
        }
        for lane, result in checks.items()
    }


def _issue_with_lane(lane: str, issue: TopologyIssue, schema_version: str = "1") -> dict[str, Any]:
    payload = _issue_to_json(issue, schema_version)
    payload["lane"] = payload.get("lane") or lane
    return payload


def run_navigation(
    task: str,
    files: list[str] | None = None,
    *,
    strict_health: bool = False,
    issue_schema_version: str = "1",
) -> dict[str, Any]:
    checks = {
        "context_budget": run_context_budget(),
        "docs": run_docs(),
        "source": run_source(),
        "history_lore": run_history_lore(),
        "agents_coherence": run_agents_coherence(),
        "self_check_coherence": run_self_check_coherence(),
        "idioms": run_idioms(),
        "runtime_modes": run_runtime_modes(),
        "reference_replacement": run_reference_replacement(),
    }
    requested_paths = files or []
    digest = build_digest(task, requested_paths)
    route_issues = _navigation_requested_file_issues(requested_paths)
    health_issues = [
        _issue_with_lane(lane, issue, issue_schema_version)
        for lane, result in checks.items()
        for issue in result.issues
    ]
    health_policy_issues = [
        _issue_with_lane(lane, issue, "2")
        for lane, result in checks.items()
        for issue in result.issues
    ]
    issues = health_issues + route_issues
    direct_health_blocker_indexes = {
        index
        for index, issue in enumerate(health_policy_issues)
        if issue in _navigation_direct_blockers([issue], requested_paths)
    }
    direct_blockers = route_issues + [
        issue for index, issue in enumerate(health_issues) if index in direct_health_blocker_indexes
    ]
    repo_health_warnings = [issue for issue in issues if issue not in direct_blockers]
    legacy_blocking = [issue for issue in issues if issue.get("severity") == "error"]
    route_context = {
        "mode": "navigation_strict_health" if strict_health else "navigation",
        "policy": _NAVIGATION_MODE_POLICY["navigation_strict_health" if strict_health else "navigation"],
        "requested_files": requested_paths,
        "allowed_files": digest.get("allowed_files", []),
        "forbidden_files": digest.get("forbidden_files", []),
        "gates": digest.get("gates", []),
        "stop_conditions": digest.get("stop_conditions", []),
    }
    return {
        "ok": not legacy_blocking if strict_health else not direct_blockers,
        "task": task,
        "digest": digest,
        "context_assumption": digest.get("context_assumption", {}),
        "checks": {
            lane: {
                "ok": result.ok,
                "issue_count": len(result.issues),
                "blocking_count": len([issue for issue in result.issues if issue.severity == "error"]),
                "warning_count": len([issue for issue in result.issues if issue.severity == "warning"]),
            }
            for lane, result in checks.items()
        },
        "issues": issues,
        "direct_blockers": legacy_blocking if strict_health else direct_blockers,
        "route_context": route_context,
        "repo_health_warnings": [] if strict_health else repo_health_warnings,
        "global_health_counts": _global_health_counts(checks),
        "excluded_lanes": {
            "strict": "strict includes transient root/state artifact classification; run explicitly when workspace is quiescent",
            "scripts": "script manifest can be blocked by active package scripts; run explicitly for script work",
            "planning_lock": "requires caller-supplied --changed-files and optional --plan-evidence",
        },
    }



def build_compiled_topology() -> dict[str, Any]:
    return _core_map_checks().build_compiled_topology(sys.modules[__name__])


def format_issues(issues: list[TopologyIssue]) -> str:
    if not issues:
        return "no topology issues"
    lines = [f"{len(issues)} topology issue(s):"]
    for index, issue in enumerate(issues, 1):
        typed = ""
        if issue.owner_manifest or issue.repair_kind:
            owner = issue.owner_manifest or "unknown_owner"
            repair = issue.repair_kind or "unknown_repair"
            typed = f" ({owner}; {repair})"
        lines.append(f"{index}. [{issue.severity}:{issue.code}] {issue.path}: {issue.message}{typed}")
    return "\n".join(lines)


def summarize_issues(issues: list[TopologyIssue]) -> str:
    if not issues:
        return "no topology issues"
    counts: dict[tuple[str, str], int] = {}
    repair_counts: dict[tuple[str, str], int] = {}
    for issue in issues:
        key = (issue.severity, issue.code)
        counts[key] = counts.get(key, 0) + 1
        if issue.owner_manifest or issue.repair_kind:
            repair_key = (issue.owner_manifest or "unknown_owner", issue.repair_kind or "unknown_repair")
            repair_counts[repair_key] = repair_counts.get(repair_key, 0) + 1
    lines = [f"{len(issues)} topology issue(s)"]
    for (severity, code), count in sorted(counts.items()):
        lines.append(f"- {severity}:{code}: {count}")
    if repair_counts:
        lines.append("by owner/repair:")
        for (owner, repair), count in sorted(repair_counts.items()):
            lines.append(f"- {owner}:{repair}: {count}")
    return "\n".join(lines)


def _print_strict(
    result: StrictResult,
    *,
    as_json: bool = False,
    summary_only: bool = False,
    issue_schema_version: str = "1",
) -> None:
    if as_json:
        payload = {"ok": result.ok, "issues": [_issue_to_json(i, issue_schema_version) for i in result.issues]}
        if result.details is not None:
            payload["details"] = result.details
        print(json.dumps(payload, indent=2))
        return
    if summary_only:
        print(summarize_issues(result.issues))
        return
    if result.ok and not result.issues:
        print("topology check ok")
        return
    if result.ok:
        print("topology check ok with advisory warnings")
        for issue in result.issues:
            print(f"[{issue.severity}:{issue.code}] {issue.path}: {issue.message}")
        return
    print("topology check failed")
    for issue in result.issues:
        print(f"[{issue.severity}:{issue.code}] {issue.path}: {issue.message}")



def main(argv: list[str] | None = None) -> int:
    try:
        from scripts import topology_doctor_cli
    except ModuleNotFoundError:  # direct script execution from scripts/
        import topology_doctor_cli

    return topology_doctor_cli.main(argv, api=sys.modules[__name__])


if __name__ == "__main__":
    raise SystemExit(main())
