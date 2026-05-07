"""Ownership lane for topology_doctor manifest fact ownership."""
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-25; last_reused=2026-05-06
# Purpose: Validate manifest fact-type ownership and module-manifest maturity.
# Reuse: fact_types and maturity_values are inlined as module constants (R12 option b).
# R12 (2026-05-06): ownership block inlined from topology_schema.yaml; schema consumers
#   for issue_json_contract / agent_runtime_contract remain in topology_schema.yaml
#   pending Phase 5 full deletion.

from __future__ import annotations

from typing import Any


REQUIRED_OWNERSHIP_FIELDS = {"canonical_owner", "derived_owners", "companion_update_rule"}

# Inlined from architecture/topology_schema.yaml ownership.maturity_values
# Update here if maturity enum changes (single source after R12).
OWNERSHIP_MATURITY_VALUES: tuple[str, ...] = ("stable", "provisional", "placeholder")

# Inlined from architecture/topology_schema.yaml ownership.fact_types
# Each entry: {canonical_owner, derived_owners, companion_update_rule}
OWNERSHIP_FACT_TYPES: dict[str, dict[str, Any]] = {
    "doc_classification": {
        "canonical_owner": "architecture/docs_registry.yaml",
        "derived_owners": ["docs/AGENTS.md", "docs/reference/AGENTS.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "module_routing": {
        "canonical_owner": "architecture/module_manifest.yaml",
        "derived_owners": ["docs/reference/modules/AGENTS.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "source_rationale": {
        "canonical_owner": "architecture/source_rationale.yaml",
        "derived_owners": ["src/AGENTS.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "test_category_and_law_gate": {
        "canonical_owner": "architecture/test_topology.yaml",
        "derived_owners": ["tests/AGENTS.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "script_lifecycle_and_safety": {
        "canonical_owner": "architecture/script_manifest.yaml",
        "derived_owners": ["scripts/AGENTS.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "companion_update_rule": {
        "canonical_owner": "architecture/map_maintenance.yaml",
        "derived_owners": ["architecture/topology.yaml"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "context_budget_posture": {
        "canonical_owner": "architecture/context_budget.yaml",
        "derived_owners": ["workspace_map.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "artifact_class": {
        "canonical_owner": "architecture/artifact_lifecycle.yaml",
        "derived_owners": ["docs/operations/AGENTS.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "graph_protocol": {
        "canonical_owner": "architecture/code_review_graph_protocol.yaml",
        "derived_owners": ["docs/reference/modules/code_review_graph.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "history_lore_card": {
        "canonical_owner": "architecture/history_lore.yaml",
        "derived_owners": ["docs/archive_registry.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "invariant_definition": {
        "canonical_owner": "architecture/invariants.yaml",
        "derived_owners": ["docs/authority/zeus_current_architecture.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
    "negative_constraint": {
        "canonical_owner": "architecture/negative_constraints.yaml",
        "derived_owners": ["AGENTS.md"],
        "companion_update_rule": "architecture/map_maintenance.yaml",
    },
}


def ownership_fact_types(schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the ownership fact types. schema arg is ignored (inlined as constant)."""
    return OWNERSHIP_FACT_TYPES


def check_ownership_schema(api: Any, schema: dict[str, Any] | None = None) -> list[Any]:
    # schema arg accepted for backward compat; ownership data is now inlined as constants
    fact_types = schema["ownership"]["fact_types"] if schema is not None else OWNERSHIP_FACT_TYPES
    issues: list[Any] = []
    if not fact_types:
        return [
            api.issue(
                "ownership_schema_missing",
                "scripts/topology_doctor_ownership_checks.py",
                "missing ownership.fact_types section",
                owner_manifest="scripts/topology_doctor_ownership_checks.py",
                repair_kind="propose_owner_manifest",
                blocking_modes=("strict_full_repo", "closeout"),
            )
        ]
    for fact_type, spec in fact_types.items():
        if not isinstance(spec, dict):
            issues.append(
                api.issue(
                    "ownership_required_field_missing",
                    f"scripts/topology_doctor_ownership_checks.py:OWNERSHIP_FACT_TYPES.{fact_type}",
                    "ownership fact type value is not a dict",
                    owner_manifest="scripts/topology_doctor_ownership_checks.py",
                    repair_kind="propose_owner_manifest",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
            continue
        missing = sorted(REQUIRED_OWNERSHIP_FIELDS - set(spec or {}))
        for field in missing:
            issues.append(
                api.issue(
                    "ownership_required_field_missing",
                    f"scripts/topology_doctor_ownership_checks.py:OWNERSHIP_FACT_TYPES.{fact_type}.{field}",
                    "ownership fact type missing required field",
                    owner_manifest="scripts/topology_doctor_ownership_checks.py",
                    repair_kind="propose_owner_manifest",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
        canonical_owner = str((spec or {}).get("canonical_owner") or "")
        if canonical_owner and not (api.ROOT / canonical_owner).exists():
            issues.append(
                api.issue(
                    "ownership_canonical_owner_missing",
                    f"scripts/topology_doctor_ownership_checks.py:OWNERSHIP_FACT_TYPES.{fact_type}",
                    f"canonical owner does not exist: {canonical_owner}",
                    owner_manifest="scripts/topology_doctor_ownership_checks.py",
                    repair_kind="propose_owner_manifest",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
        canonical_owners = spec.get("canonical_owners") if isinstance(spec, dict) else None
        if canonical_owners:
            issues.append(
                api.issue(
                    "ownership_multiple_canonical_owners",
                    f"scripts/topology_doctor_ownership_checks.py:OWNERSHIP_FACT_TYPES.{fact_type}",
                    "fact type declares multiple canonical owners",
                    owner_manifest="scripts/topology_doctor_ownership_checks.py",
                    repair_kind="propose_owner_manifest",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
    return issues


def check_module_manifest_maturity(api: Any, module_manifest: dict[str, Any] | None = None) -> list[Any]:
    allowed = set(OWNERSHIP_MATURITY_VALUES)
    module_manifest = module_manifest or api.load_module_manifest()
    issues: list[Any] = []
    for module_id, spec in (module_manifest.get("modules") or {}).items():
        if not isinstance(spec, dict):
            issues.append(
                api.issue(
                    "module_manifest_maturity_invalid",
                    f"architecture/module_manifest.yaml:{module_id}",
                    "module manifest row is not a dict",
                    owner_manifest="architecture/module_manifest.yaml",
                    repair_kind="update_companion",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
            continue
        maturity = spec.get("maturity")
        if maturity not in allowed:
            issues.append(
                api.issue(
                    "module_manifest_maturity_invalid",
                    f"architecture/module_manifest.yaml:{module_id}",
                    "module manifest row missing or invalid maturity",
                    owner_manifest="architecture/module_manifest.yaml",
                    repair_kind="update_companion",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
    return issues


def check_first_wave_issue_owners(api: Any) -> list[Any]:
    samples = (
        ("docs_registry_missing", "architecture/docs_registry.yaml"),
        ("source_rationale_missing", "architecture/source_rationale.yaml"),
        ("test_topology_missing", "architecture/test_topology.yaml"),
        ("script_manifest_missing", "architecture/script_manifest.yaml"),
        ("map_maintenance_required", "architecture/map_maintenance.yaml"),
        ("code_review_graph_stale_head", "architecture/code_review_graph_protocol.yaml"),
        ("module_book_missing", "architecture/module_manifest.yaml"),
    )
    issues: list[Any] = []
    for code, expected_owner in samples:
        issue = api.issue(code, "fixture", "fixture")
        if issue.owner_manifest != expected_owner:
            issues.append(
                api.issue(
                    "ownership_issue_owner_missing",
                    f"scripts/topology_doctor.py:{code}",
                    f"first-wave issue family must set owner_manifest={expected_owner}",
                    owner_manifest="scripts/topology_doctor_ownership_checks.py",
                    repair_kind="propose_owner_manifest",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
    return issues


def run_ownership(api: Any) -> Any:
    issues: list[Any] = []
    issues.extend(check_ownership_schema(api))
    issues.extend(check_module_manifest_maturity(api))
    issues.extend(check_first_wave_issue_owners(api))
    return api.StrictResult(ok=not issues, issues=issues)
