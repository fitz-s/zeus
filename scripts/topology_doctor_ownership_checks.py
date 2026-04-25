"""Ownership lane for topology_doctor manifest fact ownership."""
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Validate manifest fact-type ownership and module-manifest maturity.
# Reuse: Keep checks schema-driven; do not duplicate manifest row catalogs here.

from __future__ import annotations

from typing import Any


REQUIRED_OWNERSHIP_FIELDS = {"canonical_owner", "derived_owners", "companion_update_rule"}


def ownership_fact_types(schema: dict[str, Any]) -> dict[str, Any]:
    ownership = schema.get("ownership") or {}
    return ownership.get("fact_types") or {}


def check_ownership_schema(api: Any, schema: dict[str, Any] | None = None) -> list[Any]:
    schema = schema or api.load_schema()
    fact_types = ownership_fact_types(schema)
    issues: list[Any] = []
    if not fact_types:
        return [
            api.issue(
                "ownership_schema_missing",
                "architecture/topology_schema.yaml",
                "missing ownership.fact_types section",
                owner_manifest="architecture/topology_schema.yaml",
                repair_kind="propose_owner_manifest",
                blocking_modes=("strict_full_repo", "closeout"),
            )
        ]
    for fact_type, spec in fact_types.items():
        if not isinstance(spec, dict):
            issues.append(
                api.issue(
                    "ownership_required_field_missing",
                    f"architecture/topology_schema.yaml:ownership.fact_types.{fact_type}",
                    "ownership fact type value is not a dict",
                    owner_manifest="architecture/topology_schema.yaml",
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
                    f"architecture/topology_schema.yaml:ownership.fact_types.{fact_type}.{field}",
                    "ownership fact type missing required field",
                    owner_manifest="architecture/topology_schema.yaml",
                    repair_kind="propose_owner_manifest",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
        canonical_owner = str((spec or {}).get("canonical_owner") or "")
        if canonical_owner and not (api.ROOT / canonical_owner).exists():
            issues.append(
                api.issue(
                    "ownership_canonical_owner_missing",
                    f"architecture/topology_schema.yaml:ownership.fact_types.{fact_type}",
                    f"canonical owner does not exist: {canonical_owner}",
                    owner_manifest="architecture/topology_schema.yaml",
                    repair_kind="propose_owner_manifest",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
        canonical_owners = spec.get("canonical_owners") if isinstance(spec, dict) else None
        if canonical_owners:
            issues.append(
                api.issue(
                    "ownership_multiple_canonical_owners",
                    f"architecture/topology_schema.yaml:ownership.fact_types.{fact_type}",
                    "fact type declares multiple canonical owners",
                    owner_manifest="architecture/topology_schema.yaml",
                    repair_kind="propose_owner_manifest",
                    blocking_modes=("strict_full_repo", "closeout"),
                )
            )
    return issues


def check_module_manifest_maturity(api: Any, module_manifest: dict[str, Any] | None = None) -> list[Any]:
    schema = api.load_schema()
    allowed = set((schema.get("ownership") or {}).get("maturity_values") or [])
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
                    owner_manifest="architecture/topology_schema.yaml",
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
