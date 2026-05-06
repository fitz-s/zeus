#!/usr/bin/env python3
"""Topology doctor for Zeus's compiled agent-navigation graph."""
# Lifecycle: created=2026-04-13; last_reviewed=2026-04-30; last_reused=2026-04-30
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
    lifecycle_state: str | None = None
    lifecycle_owner: str | None = None
    deferred_until: str | None = None
    invalidation_condition: str | None = None
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
    "lifecycle_state",
    "lifecycle_owner",
    "deferred_until",
    "invalidation_condition",
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
    "scope_expansion",
}
ISSUE_MATURITY_VALUES = {"stable", "provisional", "placeholder"}
ISSUE_AUTHORITY_STATUSES = {"authority", "derived", "evidence", "unknown"}
ISSUE_LIFECYCLE_STATES = {
    "new",
    "acknowledged",
    "deferred_until",
    "expires_at",
    "promoted_to_blocker",
    "retired",
}


_NAVIGATION_MODE_POLICY = {
    "navigation": "requested_file_scoped",
    "navigation_strict_health": "all_errors_block",
    "closeout": "changed_file_scoped",
    "strict_full_repo": "all_errors_block",
    "global_health": "advisory_counts",
    "admission": "block_on_invalid",
}

ISSUE_BLOCKING_MODES = tuple(_NAVIGATION_MODE_POLICY)

RUNTIME_RISK_GATE_BUDGETS = {
    "T0": {
        "label": "read_only_or_orientation",
        "required": ["route_card"],
        "optional": ["semantic_bootstrap", "context_pack"],
        "stop": "do not edit files",
    },
    "T1": {
        "label": "narrow_docs_tests_or_tools",
        "required": ["route_card", "focused_test_or_checker"],
        "optional": ["map_maintenance"],
        "stop": "stop if authority, schema, lifecycle, DB, or live behavior appears",
    },
    "T2": {
        "label": "source_or_cross_module_behavior",
        "required": ["route_card", "scoped_tests", "relationship_test"],
        "optional": ["context_pack", "graph_appendix_if_fresh"],
        "stop": "stop if canonical truth, lifecycle grammar, control, or DB authority changes",
    },
    "T3": {
        "label": "architecture_governance_or_runtime_tooling",
        "required": ["route_card", "planning_lock_if_governed", "focused_tests"],
        "optional": [
            "work_record_if_nontrivial_packet",
            "closeout_receipt_if_packet_or_claim",
            "critic_review_if_high_risk_or_ambiguous",
            "graph_appendix_if_fresh",
        ],
        "stop": "stop if the change would duplicate authority or widen live permissions",
    },
    "T4": {
        "label": "live_side_effect_or_production_data",
        "required": ["explicit_operator_go", "dry_run", "apply_guard", "rollback_plan"],
        "optional": ["independent_verifier"],
        "stop": "do not proceed without explicit live/prod authorization",
    },
}

RUNTIME_CLAIM_CONTRACTS = {
    "admission_valid": {
        "depends_on": ["admission"],
        "required_gates": ["navigation_admission"],
    },
    "semantic_boot_answered": {
        "depends_on": ["semantic_bootstrap"],
        "required_gates": ["semantic_bootstrap"],
    },
    "graph_impact_validated": {
        "depends_on": ["code_review_graph"],
        "required_gates": ["code_review_graph_status"],
    },
    "repo_health_clean": {
        "depends_on": ["global_health"],
        "required_gates": ["strict_or_global_health"],
    },
    "packet_closeout_complete": {
        "depends_on": ["closeout"],
        "required_gates": ["planning_lock", "work_record", "change_receipts", "map_maintenance"],
    },
    "live_side_effect_authorized": {
        "depends_on": ["operator_authority"],
        "required_gates": ["explicit_operator_go", "dry_run", "apply_guard"],
    },
}

GRAPH_CLAIM_BLOCKING_CODES = {
    "code_review_graph_missing",
    "code_review_graph_unreadable",
    "code_review_graph_stale_head",
    "code_review_graph_stale_branch",
    "code_review_graph_partial_coverage",
    "code_review_graph_dirty_file_stale",
    "code_review_graph_git_status_failed",
    "code_review_graph_untracked_db",
}


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
        ("digest_profile_", "schema_law", "architecture/topology.yaml", "update_companion"),
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
    lifecycle_state: str | None = None,
    lifecycle_owner: str | None = None,
    deferred_until: str | None = None,
    invalidation_condition: str | None = None,
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
                "lifecycle_state": lifecycle_state,
                "lifecycle_owner": lifecycle_owner,
                "deferred_until": deferred_until,
                "invalidation_condition": invalidation_condition,
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
        lifecycle_state=metadata.get("lifecycle_state"),
        lifecycle_owner=metadata.get("lifecycle_owner"),
        deferred_until=metadata.get("deferred_until"),
        invalidation_condition=metadata.get("invalidation_condition"),
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


def normalize_runtime_claims(claims: list[str] | tuple[str, ...] | None) -> list[str]:
    if not claims:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in claims:
        claim = str(raw or "").strip()
        if not claim or claim in seen:
            continue
        seen.add(claim)
        normalized.append(claim)
    return normalized


def _graph_result_invalidates_claim(result: StrictResult | None) -> bool:
    if result is None:
        return True
    return any(
        issue.severity == "error" or issue.code in GRAPH_CLAIM_BLOCKING_CODES
        for issue in result.issues
    )


def _global_health_has_blockers(global_health: dict[str, Any] | None) -> bool:
    if not global_health:
        return True
    return any(
        (summary.get("blocking_count") or 0) > 0 or summary.get("ok") is False
        for summary in global_health.values()
    )


def build_runtime_claim_evaluation(
    claims: list[str] | tuple[str, ...] | None,
    *,
    admission_status: str | None = None,
    semantic_bootstrap: dict[str, Any] | None = None,
    graph_result: StrictResult | None = None,
    global_health: dict[str, Any] | None = None,
    closeout_ok: bool | None = None,
    task_class: str | None = None,
    write_intent: str | None = None,
) -> dict[str, Any]:
    requested = normalize_runtime_claims(list(claims or []))
    evaluated: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    advisory: list[dict[str, Any]] = []

    for claim in requested:
        contract = RUNTIME_CLAIM_CONTRACTS.get(claim)
        if contract is None:
            blocked.append({
                "claim": claim,
                "reason": "unknown runtime claim",
                "required_gates": [],
            })
            continue
        entry = {
            "claim": claim,
            "depends_on": list(contract["depends_on"]),
            "required_gates": list(contract["required_gates"]),
        }
        if claim == "admission_valid" and admission_status != "admitted":
            blocked.append({**entry, "reason": f"admission_status={admission_status or 'unknown'}"})
        elif claim == "semantic_boot_answered" and not semantic_bootstrap:
            blocked.append({**entry, "reason": "semantic bootstrap was not evaluated"})
        elif claim == "semantic_boot_answered" and not semantic_bootstrap.get("ok"):
            blocked.append({**entry, "reason": "semantic bootstrap has blocking issues"})
        elif claim == "graph_impact_validated" and _graph_result_invalidates_claim(graph_result):
            blocked.append({**entry, "reason": "code review graph is unavailable, stale, or has blocking issues"})
        elif claim == "repo_health_clean" and _global_health_has_blockers(global_health):
            blocked.append({**entry, "reason": "global health has blocking issues"})
        elif claim == "packet_closeout_complete" and closeout_ok is False:
            blocked.append({**entry, "reason": "closeout has blocking issues"})
        elif claim == "packet_closeout_complete" and closeout_ok is None:
            advisory.append({**entry, "reason": "closeout was not evaluated in this command"})
        elif claim == "live_side_effect_authorized":
            blocked.append({**entry, "reason": "explicit operator-go evidence is not present"})
        else:
            evaluated.append(entry)

    return {
        "requested": requested,
        "evaluated": evaluated,
        "blocked": blocked,
        "advisory": advisory,
        "contracts": {claim: RUNTIME_CLAIM_CONTRACTS[claim] for claim in requested if claim in RUNTIME_CLAIM_CONTRACTS},
    }


def runtime_claim_issues(claim_evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "severity": "error",
            "lane": "runtime_claims",
            "code": "runtime_claim_blocked",
            "path": f"<runtime-claim:{item['claim']}>",
            "message": item["reason"],
            "blocking_modes": ["navigation", "closeout"],
        }
        for item in claim_evaluation.get("blocked", [])
    ]


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


def run_schema() -> StrictResult:
    issues = _check_schema(load_topology(), load_schema())
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
    claims: list[str] | None = None,
    issue_schema_version: str = "1",
) -> dict[str, Any]:
    return _closeout_checks().run_closeout(
        sys.modules[__name__],
        changed_files=changed_files,
        plan_evidence=plan_evidence,
        work_record_path=work_record_path,
        receipt_path=receipt_path,
        claims=claims,
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


def build_context_pack(
    pack_type: str,
    *,
    task: str,
    files: list[str],
    task_class: str | None = None,
    intent: str | None = None,
    write_intent: str | None = None,
    claims: list[str] | None = None,
) -> dict[str, Any]:
    return _context_pack_checks().build_context_pack(
        sys.modules[__name__],
        pack_type,
        task=task,
        files=files,
        task_class=task_class,
        intent=intent,
        write_intent=write_intent,
        claims=claims,
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


def build_digest(
    task: str,
    files: list[str] | None = None,
    *,
    intent: str | None = None,
    task_class: str | None = None,
    write_intent: str | None = None,
    claims: list[str] | None = None,
    operation_stage: str | None = None,
    mutation_surfaces: list[str] | None = None,
    side_effect: str | None = None,
    artifact_target: str | None = None,
    merge_state: str | None = None,
) -> dict[str, Any]:
    return _digest_checks().build_digest(
        sys.modules[__name__],
        task,
        files,
        intent=intent,
        task_class=task_class,
        write_intent=write_intent,
        claims=claims,
        operation_stage=operation_stage,
        mutation_surfaces=mutation_surfaces,
        side_effect=side_effect,
        artifact_target=artifact_target,
        merge_state=merge_state,
    )


OPERATION_STAGES = frozenset({"explore", "plan", "edit", "merge", "closeout", "handoff"})
MUTATION_SURFACES = frozenset(
    {
        "none",
        "docs",
        "runtime_tooling",
        "runtime_hooks",
        "static_analysis_rules",
        "architecture_policy",
        "source_behavior",
        "evaluator_behavior",
        "execution_behavior",
        "db_schema_truth",
        "state_truth",
        "data_truth",
    }
)
SIDE_EFFECTS = frozenset({"read_only", "repo_edit", "data_mutation", "live_mutation"})
ARTIFACT_TARGETS = frozenset(
    {
        "none",
        "final_response",
        "existing_work_log",
        "receipt",
        "plan_packet",
        "runtime_scratch",
        "new_evidence",
        "new_findings",
    }
)
MERGE_STATES = frozenset(
    {
        "not_merge",
        "clean",
        "narrow_conflict",
        "broad_conflict",
        "high_risk_conflict",
        "unknown",
    }
)


@dataclass(frozen=True)
class OperationVector:
    operation_stage: str
    mutation_surfaces: tuple[str, ...]
    side_effect: str
    authority_surfaces: tuple[str, ...]
    artifact_target: str
    merge_state: str
    claims: tuple[str, ...]
    unresolved_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


def _clean_typed(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned or None


def _authority_surface_for_path(path: str) -> str:
    if path.startswith(".claude/hooks/") or path == ".claude/settings.json":
        return "runtime_hooks"
    if path.startswith("src/control/"):
        return "src_control"
    if path.startswith("src/execution/"):
        return "src_execution"
    if path.startswith("src/state/"):
        return "src_state"
    if path.startswith("src/"):
        return "src"
    if path.startswith("state/") and path.endswith((".db", ".sqlite", ".sqlite3")):
        return "db"
    if path.startswith("architecture/"):
        return "architecture"
    if path.startswith("docs/authority/"):
        return "docs_authority"
    if path.startswith("docs/"):
        return "docs"
    if path.startswith(".omx/"):
        return "runtime_scratch"
    if path.startswith("scripts/"):
        return "scripts"
    if path.startswith("tests/"):
        return "tests"
    if path in {"AGENTS.md", "workspace_map.md"}:
        return "root_authority"
    return "unknown"


def _mutation_surface_for_path(path: str, *, profile: str = "") -> str:
    if path.startswith(".claude/hooks/") or path == ".claude/settings.json":
        return "runtime_hooks"
    if path.startswith("architecture/ast_rules/"):
        return "static_analysis_rules"
    if path in {"architecture/invariants.yaml", "architecture/negative_constraints.yaml"}:
        return "architecture_policy"
    if path == "src/state/db.py":
        return "db_schema_truth"
    if path == "src/engine/evaluator.py":
        return "evaluator_behavior"
    if path.startswith("src/execution/"):
        return "execution_behavior"
    if path.startswith("src/state/"):
        return "state_truth"
    if path.startswith("src/control/freshness_gate.py"):
        return "source_behavior"
    if path.startswith("src/control/"):
        return "execution_behavior"
    if path.startswith("src/data/"):
        if any(token in path for token in ("source", "forecast", "tier", "station")):
            return "source_behavior"
        return "data_truth"
    if path.startswith("src/"):
        if "source" in profile:
            return "source_behavior"
        return "runtime_tooling"
    if path.startswith("docs/"):
        return "docs"
    if path.startswith(("architecture/", "scripts/topology_doctor", "tests/")):
        return "runtime_tooling"
    return "none"


def _merge_task_text(task: str) -> str:
    # "pre-merge hook" is runtime tooling, not a git merge operation.
    return re.sub(r"\bpre[-_ ]merge\b", "premerge", task.lower())


def _task_text_contains_marker(task_l: str, marker: str) -> bool:
    if marker.isascii():
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])", task_l) is not None
    return marker in task_l


def _infer_merge_state(task: str, explicit: str | None, sources: dict[str, str]) -> str:
    explicit_state = _clean_typed(explicit)
    if explicit_state and explicit_state in MERGE_STATES:
        sources["merge_state"] = "cli"
        return explicit_state
    task_l = _merge_task_text(task)
    if "clean merge" in task_l or "no conflict" in task_l or "no-conflict" in task_l:
        sources["merge_state"] = "task_hint"
        return "clean"
    high_risk_markers = (
        "schema conflict",
        "lifecycle conflict",
        "db conflict",
        "control conflict",
        "live conflict",
        "semantic ambiguity",
        "semantic conflict",
    )
    if any(marker in task_l for marker in high_risk_markers):
        sources["merge_state"] = "task_hint"
        return "high_risk_conflict"
    if "broad conflict" in task_l or "multi-zone conflict" in task_l:
        sources["merge_state"] = "task_hint"
        return "broad_conflict"
    if "narrow conflict" in task_l or "mechanical conflict" in task_l:
        sources["merge_state"] = "task_hint"
        return "narrow_conflict"
    if re.search(r"\b(?:merge|rebase|pull|git[-_ ]?am|cherry[-_ ]?pick)\b", task_l) or "conflict" in task_l:
        sources["merge_state"] = "task_hint"
        return "unknown"
    sources["merge_state"] = "default"
    return "not_merge"


def _task_is_planning_package(task: str, files: list[str]) -> bool:
    task_l = task.lower()
    planning_markers = (
        "plan",
        "planning",
        "prepare fix",
        "prepare repair",
        "fix package",
        "repair package",
        "remediation package",
        "impact analysis",
        "影响范围",
        "计划",
        "准备修复",
        "分组",
    )
    if any(_task_text_contains_marker(task_l, marker) for marker in planning_markers):
        return True
    semantic_files = [
        path
        for path in files
        if path.startswith(("architecture/", "src/", ".claude/hooks/", "tests/"))
    ]
    return len(semantic_files) > 4 and any(_task_text_contains_marker(task_l, word) for word in ("issues", "critical", "fix"))


def _infer_artifact_target(task: str, files: list[str], profile: str, explicit: str | None, sources: dict[str, str]) -> str:
    explicit_target = _clean_typed(explicit)
    if explicit_target and explicit_target in ARTIFACT_TARGETS:
        sources["artifact_target"] = "cli"
        return explicit_target
    task_l = task.lower()
    feedback_like = profile == "direct operation feedback capsule" or any(
        phrase in task_l
        for phrase in (
            "feedback capsule",
            "context recovery",
            "context recycling",
            "topology usage",
            "topology experience",
            "回收context",
            "回收 context",
            "使用体验",
        )
    )
    if any(path.startswith(".omx/") for path in files):
        sources["artifact_target"] = "file_fact"
        return "runtime_scratch"
    if any(path.endswith("findings.md") for path in files):
        sources["artifact_target"] = "file_fact"
        return "new_findings"
    if any(path.endswith("evidence.md") for path in files):
        sources["artifact_target"] = "file_fact"
        return "new_evidence"
    if any(path.endswith("receipt.json") for path in files):
        sources["artifact_target"] = "file_fact"
        return "receipt"
    if any(path.endswith(("/PLAN.md", "/plan.md", "/implementation_plan.md")) for path in files):
        sources["artifact_target"] = "file_fact"
        return "plan_packet"
    if any(path.endswith("work_log.md") for path in files):
        sources["artifact_target"] = "file_fact"
        return "existing_work_log"
    if feedback_like:
        sources["artifact_target"] = "profile_or_task_hint"
        return "final_response"
    sources["artifact_target"] = "default"
    return "none"


def build_operation_vector(
    *,
    task: str,
    files: list[str],
    profile: str = "generic",
    write_intent: str | None = None,
    claims: list[str] | None = None,
    operation_stage: str | None = None,
    mutation_surfaces: list[str] | None = None,
    side_effect: str | None = None,
    artifact_target: str | None = None,
    merge_state: str | None = None,
) -> dict[str, Any]:
    sources: dict[str, str] = {}
    normalized_files = [path.strip().removeprefix("./") for path in files if path and path.strip()]
    authority_surfaces = tuple(dict.fromkeys(_authority_surface_for_path(path) for path in normalized_files))
    if authority_surfaces:
        sources["authority_surfaces"] = "file_fact"
    else:
        sources["authority_surfaces"] = "default"

    explicit_mutation = [
        cleaned
        for value in (mutation_surfaces or [])
        if (cleaned := _clean_typed(value)) in MUTATION_SURFACES
    ]
    if explicit_mutation:
        mutation = tuple(dict.fromkeys(explicit_mutation))
        sources["mutation_surfaces"] = "cli"
    else:
        inferred = [
            surface
            for surface in (_mutation_surface_for_path(path, profile=profile) for path in normalized_files)
            if surface != "none"
        ]
        mutation = tuple(dict.fromkeys(inferred)) or ("none",)
        sources["mutation_surfaces"] = "file_fact" if inferred else "default"

    inferred_merge_state = _infer_merge_state(task, merge_state, sources)
    inferred_artifact_target = _infer_artifact_target(task, normalized_files, profile, artifact_target, sources)

    explicit_side_effect = _clean_typed(side_effect)
    if explicit_side_effect and explicit_side_effect in SIDE_EFFECTS:
        inferred_side_effect = explicit_side_effect
        sources["side_effect"] = "cli"
    else:
        intent = (write_intent or "").strip().lower().replace("-", "_")
        if intent in {"live", "production", "prod"}:
            inferred_side_effect = "live_mutation"
            sources["side_effect"] = "write_intent"
        elif intent == "apply" or "db" in authority_surfaces:
            inferred_side_effect = "data_mutation"
            sources["side_effect"] = "write_intent_or_file_fact"
        elif intent in {"edit", "write"}:
            inferred_side_effect = "repo_edit"
            sources["side_effect"] = "write_intent"
        else:
            inferred_side_effect = "read_only"
            sources["side_effect"] = "default"

    explicit_stage = _clean_typed(operation_stage)
    if explicit_stage and explicit_stage in OPERATION_STAGES:
        inferred_stage = explicit_stage
        sources["operation_stage"] = "cli"
    elif inferred_merge_state != "not_merge":
        inferred_stage = "merge"
        sources["operation_stage"] = "merge_state"
    elif inferred_artifact_target == "plan_packet" or _task_is_planning_package(task, normalized_files):
        inferred_stage = "plan"
        sources["operation_stage"] = "artifact_or_task_hint"
    elif inferred_artifact_target != "none":
        inferred_stage = "closeout"
        sources["operation_stage"] = "artifact_target"
    elif inferred_side_effect == "read_only":
        inferred_stage = "explore"
        sources["operation_stage"] = "side_effect"
    else:
        inferred_stage = "edit"
        sources["operation_stage"] = "side_effect"

    conflicts: list[str] = []
    if inferred_artifact_target == "runtime_scratch" and inferred_side_effect != "read_only":
        conflicts.append("runtime_scratch_is_not_repo_persistence")
    if inferred_side_effect == "live_mutation" and inferred_stage not in {"edit", "merge"}:
        conflicts.append("live_mutation_requires_explicit_apply_stage")

    vector = OperationVector(
        operation_stage=inferred_stage,
        mutation_surfaces=mutation,
        side_effect=inferred_side_effect,
        authority_surfaces=authority_surfaces,
        artifact_target=inferred_artifact_target,
        merge_state=inferred_merge_state,
        claims=tuple(normalize_runtime_claims(claims)),
        unresolved_fields=(),
        conflicts=tuple(conflicts),
    )
    vector_payload = dataclass_asdict(vector)
    for field in ("mutation_surfaces", "authority_surfaces", "claims", "unresolved_fields", "conflicts"):
        vector_payload[field] = list(vector_payload.get(field) or [])
    return {
        "vector": vector_payload,
        "field_sources": sources,
        "authority_status": "generated_operation_vector_not_authority",
    }


def _runtime_risk_tier(files: list[str], *, task: str = "", write_intent: str | None = None) -> str:
    normalized = [path.strip().removeprefix("./") for path in files if path and path.strip()]
    intent = (write_intent or "").strip().lower()
    normalized_intent = intent.replace("_", "-").replace(" ", "-")
    task_l = task.lower()
    if intent in {"live", "production", "prod", "apply"} or any(
        token in task_l for token in ("live deploy", "production db", "prod db", "live venue", "apply mutation")
    ):
        return "T4"
    if normalized_intent in {"read-only", "readonly", "none"} or not normalized:
        return "T0"
    if any(
        path.startswith(("architecture/", "docs/authority/", "src/state/", "src/control/", "src/supervisor_api/"))
        or path in {"AGENTS.md", "workspace_map.md"}
        or path.startswith(".claude/hooks/")
        or path == ".claude/settings.json"
        or path.startswith("scripts/topology_doctor")
        for path in normalized
    ):
        return "T3"
    if any(path.startswith("src/") for path in normalized):
        return "T2"
    return "T1"


def _route_card_next_action(
    admission_status: str,
    risk_tier: str,
    operation_vector: dict[str, Any] | None = None,
) -> str:
    operation_stage = str((operation_vector or {}).get("operation_stage") or "")
    if admission_status == "admitted":
        if risk_tier == "T3":
            return (
                "proceed with planning-lock as needed and focused gates; add "
                "receipt/critic only for packet closeout, explicit claims, or "
                "semantic ambiguity"
            )
        if risk_tier == "T4":
            return "stop until explicit operator-go, dry-run evidence, apply guard, and rollback plan exist"
        return "proceed with admitted files and focused verification"
    if admission_status == "advisory_only":
        if operation_stage == "plan":
            return (
                "planning only: use requested files as read-only impact context, "
                "write/route one plan packet, then split implementation by structural decision"
            )
        return "read-only orientation only; pass typed intent or narrower files before editing"
    if admission_status == "scope_expansion_required":
        return "stop and update packet/profile scope before editing out-of-scope files"
    if admission_status == "blocked":
        return "stop; requested files hit forbidden surfaces"
    if admission_status == "ambiguous":
        return "stop; pass typed --intent or narrow the task wording"
    return "stop and inspect admission decision"


def _route_card_expansion_hints(
    admission_status: str,
    risk_tier: str,
    operation_vector: dict[str, Any] | None = None,
) -> list[str]:
    operation_stage = str((operation_vector or {}).get("operation_stage") or "")
    if admission_status not in {"admitted", "advisory_only"}:
        return [
            "inspect admission.decision_basis before changing files",
            "do not edit until requested files are admitted",
        ]
    if admission_status == "advisory_only":
        if operation_stage == "plan":
            return [
                "treat requested files as impact context, not edit permission",
                "write one plan packet before touching governed/source files",
                "run separate navigation + planning-lock for each structural decision slice",
            ]
        return [
            "treat requested files as orientation context, not edit permission",
            "pass typed intent or narrow task wording before editing",
        ]
    if risk_tier == "T0":
        return [
            "stay in read-only orientation unless the user asks for edits",
            "load appendices only for a claim that depends on them",
        ]
    if risk_tier in {"T3", "T4"}:
        if risk_tier == "T4":
            return [
                "load operator-go evidence before any live/prod action",
                "run dry-run and rollback checks before apply-capable work",
            ]
        return [
            "run planning-lock for governed files before edits",
            "add receipt, work record, or critic only when a packet/claim consumes it",
        ]
    return [
        "use focused context for admitted files",
        "run the smallest checker that proves the completion claim",
    ]


def _route_card_why_not_admitted(admission: dict[str, Any]) -> list[str]:
    status = str(admission.get("status") or "advisory_only")
    if status in {"admitted", "advisory_only"} and not admission.get("out_of_scope_files"):
        return []
    decision = admission.get("decision_basis") or {}
    reasons = [str(reason) for reason in decision.get("why") or [] if str(reason)]
    if admission.get("out_of_scope_files"):
        reasons.append(f"out_of_scope_files={list(admission.get('out_of_scope_files') or [])}")
    if admission.get("forbidden_hits"):
        reasons.append(f"forbidden_hits={list(admission.get('forbidden_hits') or [])}")
    if status not in {"admitted", "advisory_only"}:
        reasons.insert(0, f"admission_status={status}")
    return list(dict.fromkeys(reasons))


def _route_card_safe_next_files(task: str, profile: str, admission: dict[str, Any]) -> list[str]:
    admitted = list(admission.get("admitted_files") or [])
    if admitted:
        return admitted
    task_l = task.lower()
    if "provider hot-swap" in task_l or ("canary" in task_l and "freshness" in task_l):
        return ["src/control/freshness_gate.py", "src/engine/cycle_runner.py"]
    if profile == "direct operation feedback capsule":
        return []
    suggested = list(admission.get("profile_suggested_files") or [])
    return [path for path in suggested if isinstance(path, str) and not any(mark in path for mark in ("*", "<"))][:5]


def _route_card_suggested_next_command(
    *,
    task: str,
    profile: str,
    status: str,
    intent: str | None,
    write_intent: str | None,
    safe_next_files: list[str],
    operation_vector: dict[str, Any],
    persistence_target: str | None,
    merge_evidence_required: dict[str, Any],
) -> str | None:
    if status == "admitted":
        return None
    task_l = task.lower()
    operation_stage = str(operation_vector.get("operation_stage") or "")
    artifact_target = str(operation_vector.get("artifact_target") or "none")
    if profile == "direct operation feedback capsule":
        if artifact_target == "final_response":
            return None
        if artifact_target in {"runtime_scratch", "new_evidence", "new_findings"}:
            return (
                "python3 scripts/topology_doctor.py --navigation --route-card-only "
                "--task \"direct operation feedback capsule: context recovery, Zeus improvement insights, "
                "topology helped/blocked\" --intent \"direct operation feedback capsule\" "
                "--write-intent read_only --operation-stage closeout --artifact-target final_response"
            )
        if artifact_target in {"existing_work_log", "receipt"} and status != "admitted":
            target = "docs/operations/task_YYYY-MM-DD_slug/work_log.md"
            if artifact_target == "receipt":
                target = "docs/operations/task_YYYY-MM-DD_slug/receipt.json"
            return (
                "python3 scripts/topology_doctor.py --navigation "
                "--task \"operation feedback capsule for packet closeout\" "
                "--intent \"direct operation feedback capsule\" --write-intent edit "
                f"--operation-stage closeout --artifact-target {artifact_target} --files {target}"
            )
        if not safe_next_files:
            return None
    if operation_stage == "plan" and artifact_target != "plan_packet":
        return (
            "python3 scripts/topology_doctor.py --navigation --route-card-only "
            "--task \"operation planning packet: structural decisions, impact context, "
            "slice routes, and verification plan\" --intent \"operation planning packet\" "
            "--write-intent edit --operation-stage plan --artifact-target plan_packet "
            "--files docs/operations/task_YYYY-MM-DD_slug/PLAN.md"
        )
    if merge_evidence_required.get("required"):
        return (
            "python3 scripts/topology_doctor.py --navigation "
            "--task \"merge conflict escalation: broad/high-risk conflict surface\" "
            "--operation-stage merge --merge-state high_risk_conflict --write-intent edit"
        )
    if "provider hot-swap" in task_l or ("canary" in task_l and "freshness" in task_l):
        return (
            "python3 scripts/topology_doctor.py --navigation "
            "--intent \"source canary readiness hot-swap\" --write-intent edit "
            f"--files {' '.join(safe_next_files or ['src/control/freshness_gate.py', 'src/engine/cycle_runner.py'])}"
        )
    if "dead navigation" in task_l or "stale operations packet reference" in task_l:
        return (
            "python3 scripts/topology_doctor.py --navigation "
            "--intent \"docs navigation cleanup\" --write-intent edit "
            "--files docs/operations/current_state.md"
        )
    if "evaluator" in task_l and ("script" in task_l or "bridge" in task_l or "import" in task_l):
        return (
            "python3 scripts/topology_doctor.py --navigation "
            "--intent \"evaluator script import bridge\" --write-intent edit "
            "--files src/engine/evaluator.py"
        )
    if intent:
        return None
    if safe_next_files:
        write_flag = f"--write-intent {write_intent or 'edit'}"
        return (
            "python3 scripts/topology_doctor.py --navigation "
            f"--task \"{task}\" {write_flag} --files {' '.join(safe_next_files)}"
        )
    return None


def _script_manifest_note_for_path(path: str) -> dict[str, Any] | None:
    if not path.startswith("scripts/"):
        return None
    manifest = load_script_manifest()
    name = Path(path).name
    entry = _effective_script_entry(manifest, name)
    if not entry:
        return {"kind": "script_manifest", "path": path, "status": "unregistered"}
    return {
        "kind": "script_manifest",
        "path": path,
        "class": entry.get("class"),
        "status": entry.get("status"),
        "lifecycle": entry.get("lifecycle"),
        "canonical_command": entry.get("canonical_command"),
        "replacement": entry.get("replacement"),
        "dangerous_if_run": bool(entry.get("dangerous_if_run", False)),
    }


def _docs_provenance_note_for_path(path: str) -> dict[str, Any] | None:
    if not path.startswith("docs/"):
        return None
    full = ROOT / path
    if path.startswith("docs/archives/"):
        status = "archived" if full.exists() else "missing"
    elif path.startswith("docs/operations/task_"):
        status = "active" if full.exists() else "missing"
    elif path in {"docs/operations/current_state.md", "docs/operations/AGENTS.md", "docs/archive_registry.md"}:
        status = "active" if full.exists() else "missing"
    else:
        status = "present" if full.exists() else "missing"
    return {"kind": "docs_path", "path": path, "status": status}


def _route_card_provenance_notes(files: list[str]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for path in files:
        script_note = _script_manifest_note_for_path(path)
        if script_note:
            notes.append(script_note)
            continue
        docs_note = _docs_provenance_note_for_path(path)
        if docs_note:
            notes.append(docs_note)
    return notes


def _route_card_blocked_file_reasons(
    *,
    admission: dict[str, Any],
    provenance_notes: list[dict[str, Any]],
) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    admitted_paths = {str(path) for path in admission.get("admitted_files") or []}
    for path in admission.get("out_of_scope_files") or []:
        reasons.setdefault(path, []).append("not declared in selected profile.allowed_files")
    for path in admission.get("forbidden_hits") or []:
        reasons.setdefault(path, []).append("matched selected profile forbidden_files")
    for path in list(admission.get("out_of_scope_files") or []) + list(admission.get("forbidden_hits") or []):
        if str(path).startswith(".omx/context"):
            reasons.setdefault(path, []).append("local runtime scratch, not repo evidence")
    for note in provenance_notes:
        path = str(note.get("path") or "")
        if note.get("kind") == "script_manifest":
            status = str(note.get("status") or "")
            lifecycle = str(note.get("lifecycle") or "")
            script_class = str(note.get("class") or "")
            if status == "deprecated" or lifecycle == "deprecated_fail_closed" or script_class == "stale_deprecated":
                reasons.setdefault(path, []).append(
                    "script manifest provenance: deprecated or fail-closed"
                )
            elif status == "unregistered":
                reasons.setdefault(path, []).append("script manifest provenance: unregistered script")
        elif (
            note.get("kind") == "docs_path"
            and note.get("status") == "missing"
            and path not in admitted_paths
        ):
            reasons.setdefault(path, []).append("docs provenance: path is missing")
    return reasons


def _route_card_persistence_target(profile: str, task: str, files: list[str], operation_vector: dict[str, Any]) -> str | None:
    artifact_target = str(operation_vector.get("artifact_target") or "none")
    if artifact_target == "none":
        task_l = task.lower()
        if profile != "direct operation feedback capsule" and "context recovery" not in task_l:
            return None
    if artifact_target == "runtime_scratch":
        return "context_worklog"
    if artifact_target == "existing_work_log":
        return "work_log"
    if artifact_target == "plan_packet":
        return "plan_packet"
    if artifact_target in {"final_response", "receipt", "new_evidence", "new_findings"}:
        return artifact_target
    task_l = task.lower()
    if profile != "direct operation feedback capsule" and "context recovery" not in task_l:
        return None
    if any(path.startswith(".omx/context") for path in files) or ".omx/context" in task_l:
        return "context_worklog"
    if any(path.endswith("receipt.json") for path in files):
        return "receipt"
    if any(path.endswith("work_log.md") for path in files):
        return "work_log"
    return "final_response"


def _route_card_merge_conflict_scan(operation_vector: dict[str, Any]) -> str:
    merge_state = str(operation_vector.get("merge_state") or "not_merge")
    if merge_state == "not_merge":
        return "not_applicable"
    return merge_state


def _route_card_merge_evidence_required(operation_vector: dict[str, Any]) -> dict[str, Any]:
    merge_state = str(operation_vector.get("merge_state") or "not_merge")
    if merge_state == "not_merge":
        return {"required": False, "reason": "not_a_merge_task"}
    if merge_state == "clean":
        return {"required": False, "reason": "conflict_first_clean_merge"}
    if merge_state == "narrow_conflict":
        return {"required": False, "reason": "conflict_first_narrow_mechanical_conflict"}
    if merge_state in {"broad_conflict", "high_risk_conflict"}:
        return {"required": True, "reason": "conflict_first_escalation_trigger"}
    high_risk_surfaces = {"src_state", "src_control", "src_execution", "db"}
    authority_surfaces = set(operation_vector.get("authority_surfaces") or [])
    if merge_state == "unknown" and authority_surfaces & high_risk_surfaces:
        return {"required": True, "reason": "conflict_first_unknown_high_risk_surface"}
    return {"required": False, "reason": "conflict_first_advisory_unknown"}


def _route_card_structural_decision_hints(operation_vector: dict[str, Any]) -> list[str]:
    if str(operation_vector.get("operation_stage") or "") != "plan":
        return []
    if str(operation_vector.get("artifact_target") or "") == "plan_packet":
        return [
            "plan packet should name K structural decisions before implementation",
            "each implementation slice still needs its own admitted navigation route",
        ]
    surfaces = [str(surface) for surface in operation_vector.get("mutation_surfaces") or []]
    if not surfaces or surfaces == ["none"]:
        return [
            "plan packet should name the structural decisions before implementation",
            "implementation still needs a separate admitted navigation route per slice",
        ]
    labels = {
        "runtime_hooks": "hook fail-closed behavior",
        "static_analysis_rules": "static rule precision",
        "architecture_policy": "invariant/negative-constraint authority",
        "db_schema_truth": "DB schema/default semantics",
        "state_truth": "state truth semantics",
        "runtime_tooling": "runtime tooling/governance",
        "docs": "docs/reference consistency",
    }
    grouped = [labels.get(surface, surface) for surface in surfaces if surface != "none"]
    return [
        "split implementation by structural decision, not by every issue",
        f"candidate split surfaces: {', '.join(dict.fromkeys(grouped))}",
        "requested implementation files remain read-only impact context until each slice is admitted",
    ]


def _route_card_dominant_driver(
    *,
    task: str,
    profile: str,
    status: str,
    profile_selection: dict[str, Any],
    persistence_target: str | None,
    provenance_notes: list[dict[str, Any]],
    blocked_file_reasons: dict[str, list[str]],
    merge_evidence_required: dict[str, Any],
    operation_vector: dict[str, Any],
) -> str:
    if persistence_target:
        return f"persistence_target:{persistence_target}"
    if str(operation_vector.get("operation_stage") or "") == "plan":
        return "planning_package_split"
    if str(operation_vector.get("merge_state") or "not_merge") != "not_merge":
        return "merge_conflict_first"
    if "source_behavior" in set(operation_vector.get("mutation_surfaces") or []) and "source canary" in profile:
        return "source_canary_readiness_hot_swap"
    task_l = task.lower()
    if "provider hot-swap" in task_l or ("canary" in task_l and "freshness" in task_l):
        return "source_canary_readiness_hot_swap"
    blocked_paths = set(blocked_file_reasons)
    if any(note.get("kind") == "script_manifest" and note.get("path") in blocked_paths for note in provenance_notes):
        return "script_manifest_provenance"
    if merge_evidence_required.get("reason") != "not_a_merge_task":
        return "merge_conflict_first"
    if status != "admitted":
        selected_by = profile_selection.get("selected_by") or profile_selection.get("evidence_class") or "unknown"
        return f"profile_needs_typed_intent:{selected_by}"
    return f"profile:{profile}"


def build_runtime_route_card(
    *,
    task: str,
    files: list[str],
    digest: dict[str, Any],
    mode: str,
    intent: str | None = None,
    task_class: str | None = None,
    write_intent: str | None = None,
    claims: list[str] | None = None,
) -> dict[str, Any]:
    admission = digest.get("admission") or {}
    profile_selection = digest.get("profile_selection") or {}
    status = str(admission.get("status") or "advisory_only")
    risk_tier = _runtime_risk_tier(files, task=task, write_intent=write_intent)
    gate_budget = RUNTIME_RISK_GATE_BUDGETS[risk_tier]
    profile = str(digest.get("profile", "generic"))
    operation_payload = digest.get("operation_vector") or build_operation_vector(
        task=task,
        files=list(files or []),
        profile=profile,
        write_intent=write_intent,
        claims=claims,
    )
    operation_vector = dict(operation_payload.get("vector") or {})
    safe_next_files = _route_card_safe_next_files(task, profile, admission)
    provenance_notes = _route_card_provenance_notes(list(files or []))
    blocked_file_reasons = _route_card_blocked_file_reasons(
        admission=admission,
        provenance_notes=provenance_notes,
    )
    persistence_target = _route_card_persistence_target(profile, task, list(files or []), operation_vector)
    merge_conflict_scan = _route_card_merge_conflict_scan(operation_vector)
    merge_evidence_required = _route_card_merge_evidence_required(operation_vector)
    return {
        "schema_version": "1",
        "authority_status": "generated_route_card_not_authority",
        "mode": mode,
        "task": task,
        "profile": profile,
        "intent": intent,
        "task_class": task_class,
        "write_intent": write_intent,
        "selection_evidence_class": profile_selection.get("evidence_class"),
        "needs_typed_intent": bool(profile_selection.get("needs_typed_intent")),
        "companion_files": list(profile_selection.get("companion_file_hits") or [])
        + list(profile_selection.get("shared_file_hits") or []),
        "admission_status": status,
        "risk_tier": risk_tier,
        "gate_budget": gate_budget,
        "next_action": _route_card_next_action(status, risk_tier, operation_vector),
        "admitted_files": list(admission.get("admitted_files") or []),
        "out_of_scope_files": list(admission.get("out_of_scope_files") or []),
        "forbidden_hits": list(admission.get("forbidden_hits") or []),
        "hard_stops": list(digest.get("stop_conditions") or []),
        "claims": normalize_runtime_claims(claims),
        "expansion_hints": _route_card_expansion_hints(status, risk_tier, operation_vector),
        "operation_vector": operation_vector,
        "operation_vector_sources": dict(operation_payload.get("field_sources") or {}),
        "structural_decision_hints": _route_card_structural_decision_hints(operation_vector),
        "dominant_driver": _route_card_dominant_driver(
            task=task,
            profile=profile,
            status=status,
            profile_selection=profile_selection,
            persistence_target=persistence_target,
            provenance_notes=provenance_notes,
            blocked_file_reasons=blocked_file_reasons,
            merge_evidence_required=merge_evidence_required,
            operation_vector=operation_vector,
        ),
        "why_not_admitted": _route_card_why_not_admitted(admission),
        "suggested_next_command": _route_card_suggested_next_command(
            task=task,
            profile=profile,
            status=status,
            intent=intent,
            write_intent=write_intent,
            safe_next_files=safe_next_files,
            operation_vector=operation_vector,
            persistence_target=persistence_target,
            merge_evidence_required=merge_evidence_required,
        ),
        "safe_next_files": safe_next_files,
        "blocked_file_reasons": blocked_file_reasons,
        "persistence_target": persistence_target,
        "merge_conflict_scan": merge_conflict_scan,
        "merge_evidence_required": merge_evidence_required,
        "provenance_notes": provenance_notes,
        "appendix_policy": "expand appendices only when the task claim depends on them or risk_tier is T3/T4",
        "claim_scope": {
            "admission": "blocks editing when status is not admitted",
            "repo_health": "blocks only requested-file or explicitly strict-health claims",
            "graph": "blocks graph-impact claims only unless graph files are directly changed",
        },
    }


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


def _navigation_requested_file_issues(
    requested_paths: list[str],
    admitted_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    admitted = {path.strip().removeprefix("./").rstrip("/") for path in admitted_paths or []}
    for requested in requested_paths:
        normalized = requested.strip().removeprefix("./").rstrip("/")
        if not normalized:
            continue
        if normalized in admitted:
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
    intent: str | None = None,
    task_class: str | None = None,
    write_intent: str | None = None,
    claims: list[str] | None = None,
    operation_stage: str | None = None,
    mutation_surfaces: list[str] | None = None,
    side_effect: str | None = None,
    artifact_target: str | None = None,
    merge_state: str | None = None,
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
    digest = build_digest(
        task,
        requested_paths,
        intent=intent,
        task_class=task_class,
        write_intent=write_intent,
        claims=claims,
        operation_stage=operation_stage,
        mutation_surfaces=mutation_surfaces,
        side_effect=side_effect,
        artifact_target=artifact_target,
        merge_state=merge_state,
    )
    admission = digest.get("admission") or {}
    admission_status = admission.get("status", "advisory_only")
    route_issues = _navigation_requested_file_issues(
        requested_paths,
        list(admission.get("admitted_files") or []),
    )
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
    requested_claims = normalize_runtime_claims(claims)
    graph_result = (
        run_code_review_graph_status(requested_paths)
        if "graph_impact_validated" in requested_claims
        else None
    )
    semantic_bootstrap = (
        build_semantic_bootstrap(task_class, task=task, files=requested_paths)
        if "semantic_boot_answered" in requested_claims and task_class
        else None
    )
    claim_evaluation = build_runtime_claim_evaluation(
        requested_claims,
        admission_status=admission_status,
        semantic_bootstrap=semantic_bootstrap,
        graph_result=graph_result,
        global_health=_global_health_counts(checks),
        task_class=task_class,
        write_intent=write_intent,
    )
    claim_issues = runtime_claim_issues(claim_evaluation)
    issues = health_issues + route_issues + claim_issues
    admission_issue = _admission_typed_issue(admission, issue_schema_version)
    if admission_issue is not None:
        issues = issues + [admission_issue]
    direct_health_blocker_indexes = {
        index
        for index, issue in enumerate(health_policy_issues)
        if issue in _navigation_direct_blockers([issue], requested_paths)
    }
    direct_blockers = route_issues + claim_issues + [
        issue for index, issue in enumerate(health_issues) if index in direct_health_blocker_indexes
    ]
    if admission_issue is not None:
        direct_blockers = direct_blockers + [admission_issue]
    repo_health_warnings = [issue for issue in issues if issue not in direct_blockers]
    task_blockers = list(direct_blockers)
    admission_blockers = [admission_issue] if admission_issue is not None else []
    legacy_blocking = [issue for issue in issues if issue.get("severity") == "error"]
    route_context = {
        "mode": "navigation_strict_health" if strict_health else "navigation",
        "policy": _NAVIGATION_MODE_POLICY["navigation_strict_health" if strict_health else "navigation"],
        "requested_files": requested_paths,
        "admitted_files": admission.get("admitted_files", []),
        "profile_suggested_files": admission.get("profile_suggested_files", []),
        "out_of_scope_files": admission.get("out_of_scope_files", []),
        "forbidden_hits": admission.get("forbidden_hits", []),
        "allowed_files": digest.get("allowed_files", []),
        "legacy_advisory": True,
        "forbidden_files": digest.get("forbidden_files", []),
        "gates": digest.get("gates", []),
        "stop_conditions": digest.get("stop_conditions", []),
        "route_card": digest.get("route_card", {}),
        "operation_vector": digest.get("operation_vector", {}),
        "risk_tier": (digest.get("route_card") or {}).get("risk_tier"),
        "gate_budget": (digest.get("route_card") or {}).get("gate_budget"),
    }
    admission_ok = admission_status in {"admitted", "advisory_only"}
    if strict_health:
        nav_ok = (not legacy_blocking) and admission_ok
    else:
        nav_ok = (not direct_blockers) and admission_ok
    return {
        "ok": nav_ok,
        "command_ok": True,
        "ok_semantics": "command_success_only_not_write_authorization",
        "task": task,
        "digest": digest,
        "admission": admission,
        "route_card": digest.get("route_card", {}),
        "claim_evaluation": claim_evaluation,
        "claims_evaluated": claim_evaluation["evaluated"],
        "claims_blocked": claim_evaluation["blocked"],
        "claims_advisory": claim_evaluation["advisory"],
        "semantic_bootstrap": semantic_bootstrap,
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
        "issues_contract": "legacy_aggregate_not_task_blockers",
        "task_blockers": task_blockers,
        "admission_blockers": admission_blockers,
        "profile_selection_warnings": [
            issue for issue in task_blockers if issue.get("lane") == "navigation"
        ],
        "global_health_warnings": [] if strict_health else repo_health_warnings,
        "legacy_issues": issues,
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


def build_runtime_packet(
    *,
    task: str,
    files: list[str] | None = None,
    intent: str | None = None,
    task_class: str | None = None,
    write_intent: str | None = None,
    role: str | None = None,
    claims: list[str] | None = None,
    operation_stage: str | None = None,
    mutation_surfaces: list[str] | None = None,
    side_effect: str | None = None,
    artifact_target: str | None = None,
    merge_state: str | None = None,
    route_card_only: bool = False,
) -> dict[str, Any]:
    target_files = files or []
    navigation = run_navigation(
        task,
        target_files,
        intent=intent,
        task_class=task_class,
        write_intent=write_intent,
        claims=claims,
        operation_stage=operation_stage,
        mutation_surfaces=mutation_surfaces,
        side_effect=side_effect,
        artifact_target=artifact_target,
        merge_state=merge_state,
    )
    if route_card_only:
        return {"ok": navigation["ok"], "route_card": navigation["route_card"]}

    semantic_bootstrap = (
        build_semantic_bootstrap(task_class, task=task, files=target_files)
        if task_class
        else None
    )
    role_context = None
    if role and target_files:
        role_context = build_context_pack(
            role,
            task=task,
            files=target_files,
            task_class=task_class,
            intent=intent,
            write_intent=write_intent,
            claims=claims,
        )

    route_card = navigation.get("route_card") or {}
    dispatch_guidance = {
        "authority_status": "generated_dispatch_guidance_not_authority",
        "default_mode": "solo",
        "parallelism_rule": (
            "Use subagents only for independent bounded tasks when explicitly "
            "authorized by the user or when OMX team runtime is active."
        ),
        "role_prompt_shape": [
            "authority reads and topology command",
            "single task objective",
            "owned files and non-goals",
            "invariants that must not move",
            "required verification and rollback note",
            "preserve unrelated dirty work",
        ],
        "team_threshold": "consider team mode only for four or more independent lanes with shared verification",
    }
    return {
        "ok": navigation["ok"],
        "authority_status": "generated_runtime_packet_not_authority",
        "task": task,
        "files": target_files,
        "route_card": route_card,
        "admission": navigation.get("admission", {}),
        "claim_evaluation": navigation.get("claim_evaluation", {}),
        "semantic_bootstrap": semantic_bootstrap,
        "role_context": role_context,
        "dispatch_guidance": dispatch_guidance,
        "gate_budget": route_card.get("gate_budget"),
        "artifact_treatment_hints": [
            "keep scratch analysis out of authority surfaces",
            "promote stable lessons through manifests, module books, tests, or source",
            "record durable packet evidence only when an active gate or handoff consumes it",
            "close complete operations with a compact feedback capsule in the final response or an already-required packet surface",
            "make topology capsule notes diagnostic: route/admission/risk, semantic match, help, friction, next delta or none_observed",
            "route direct feedback with intent 'direct operation feedback capsule'; do not persist it under .omx/context",
        ],
    }


def _admission_typed_issue(
    admission: dict[str, Any], schema_version: str
) -> dict[str, Any] | None:
    """Translate an admission denial into a navigation typed issue.

    Returns None when admission is admitted/advisory_only; otherwise emits
    a typed issue (severity=error) so closeout / CI consumers see the
    route-admission denial alongside repo-health issues.
    """
    status = admission.get("status", "advisory_only")
    if status in {"admitted", "advisory_only"}:
        return None
    code_map = {
        "blocked": "navigation_admission_blocked",
        "scope_expansion_required": "navigation_scope_expansion_required",
        "route_contract_conflict": "navigation_route_contract_conflict",
        "ambiguous": "navigation_route_ambiguous",
    }
    code = code_map.get(status, "navigation_admission_denied")
    out_of_scope = list(admission.get("out_of_scope_files") or [])
    forbidden_hits = list(admission.get("forbidden_hits") or [])
    primary_path = (
        forbidden_hits[0]
        if forbidden_hits
        else (out_of_scope[0] if out_of_scope else "<no path>")
    )
    profile_id = admission.get("profile_id") or "generic"
    message_parts = [f"admission status={status}", f"profile={profile_id}"]
    if forbidden_hits:
        message_parts.append(f"forbidden_hits={forbidden_hits}")
    if out_of_scope:
        message_parts.append(f"out_of_scope={out_of_scope}")
    issue: dict[str, Any] = {
        "severity": "error",
        "lane": "navigation",
        "code": code,
        "path": primary_path,
        "message": "; ".join(message_parts),
    }
    if schema_version != "1":
        issue["owner_manifest"] = "architecture/topology.yaml"
        issue["repair_kind"] = "scope_expansion"
    return issue



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
