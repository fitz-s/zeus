"""Core-map and compiled-topology builder family for topology_doctor."""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-23; last_reused=2026-04-23
# Purpose: Build core maps and compiled topology read models from manifest-backed evidence.
# Reuse: Keep generated maps derived-only; add source manifests here when new manifest families affect boot context.

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any


COMPILED_TOPOLOGY_SOURCE_MANIFESTS = [
    "architecture/topology.yaml",
    "architecture/artifact_lifecycle.yaml",
    "architecture/context_budget.yaml",
    "architecture/change_receipt_schema.yaml",
    "architecture/source_rationale.yaml",
    "architecture/test_topology.yaml",
    "architecture/script_manifest.yaml",
    "architecture/reference_replacement.yaml",
    "architecture/core_claims.yaml",
    "architecture/history_lore.yaml",
    "architecture/context_pack_profiles.yaml",
    "architecture/task_boot_profiles.yaml",
    "architecture/fatal_misreads.yaml",
    "architecture/city_truth_contract.yaml",
    "architecture/code_review_graph_protocol.yaml",
    "architecture/map_maintenance.yaml",
]

CORE_MAP_FORBIDDEN_PATTERNS = (
    re.compile(r"round\s*\(\s*(?:value|x)\s*\+\s*0\.5\s*\)", re.IGNORECASE),
    re.compile(r"\bpython\s*(?:/numpy\s*)?(?:built-?in\s*)?round\b", re.IGNORECASE),
    re.compile(r"\bnumpy\s+(?:round|around)\b", re.IGNORECASE),
    re.compile(r"(?<!not )\bbanker(?:'s)?\s+round", re.IGNORECASE),
)


def core_map_profiles(api: Any) -> dict[str, dict[str, Any]]:
    return {
        str(profile.get("id")): profile
        for profile in api.load_topology().get("core_map_profiles") or []
        if profile.get("id")
    }


def source_entry_by_path(api: Any) -> dict[str, dict[str, Any]]:
    rationale = api.load_source_rationale()
    entries: dict[str, dict[str, Any]] = {}
    for item in api._source_rationale_for(list((rationale.get("files") or {}).keys())):
        entries[item["path"]] = item
    return entries


def validate_proof_target_exists(api: Any, target: dict[str, Any]) -> bool:
    target_path = str(target.get("path") or "")
    if not target_path:
        return False
    return (api.ROOT / target_path).exists()


def locator_exists(api: Any, path: str, locator: str | None) -> bool:
    if not locator:
        return False
    target = api.ROOT / path
    if not target.exists() or not target.is_file():
        return False
    text = target.read_text(encoding="utf-8", errors="ignore")
    if locator in text:
        return True
    if target.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return False
        if "." in locator:
            class_name, member_name = locator.split(".", 1)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == member_name:
                            return True
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == locator:
                return True
            if isinstance(node, ast.Attribute) and node.attr == locator:
                return True
            if isinstance(node, ast.Name) and node.id == locator:
                return True
    return False


def proof_contains(api: Any, path: str, needle: str) -> bool:
    if not needle:
        return False
    return needle in (api.ROOT / path).read_text(encoding="utf-8", errors="ignore")


def edge_proof_status(
    api: Any,
    edge: dict[str, Any],
    *,
    node_ids: set[str],
    node_files: dict[str, str],
    source_entries: dict[str, dict[str, Any]],
) -> tuple[str, str | None]:
    from_id = str(edge.get("from") or "")
    to_id = str(edge.get("to") or "")
    if from_id not in node_ids or to_id not in node_ids:
        return ("invalid", f"unknown edge endpoint: {from_id}->{to_id}")

    proof = edge.get("proof") or {}
    kind = proof.get("kind")
    path = str(proof.get("path") or "")
    if kind == "profile_declared_provisional":
        return ("profile_declared_provisional", None)
    if not path or not (api.ROOT / path).exists():
        return ("invalid", f"proof path missing: {path}")
    if kind == "import_or_call":
        module = str(proof.get("module") or "")
        symbol = str(proof.get("symbol") or proof.get("contains") or "")
        if path != node_files.get(to_id):
            return ("invalid", f"import_or_call proof path must equal target node file {node_files.get(to_id)}")
        if not module or not symbol:
            return ("invalid", "import_or_call proof requires module and symbol")
        text = (api.ROOT / path).read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return ("invalid", f"cannot parse proof path: {path}")
        imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == module:
                imported = any(alias.name == symbol for alias in node.names)
                if imported:
                    break
            if isinstance(node, ast.Import):
                imported = any(alias.name == module for alias in node.names)
                if imported:
                    break
        if not imported:
            return ("invalid", f"import proof not found in {path}: {module}.{symbol}")
        if symbol not in text:
            return ("invalid", f"proof symbol not used in {path}: {symbol}")
        return ("proof_backed_edge", None)
    if kind == "relationship_test":
        needle = str(proof.get("contains") or proof.get("locator") or "")
        if not proof_contains(api, path, needle):
            return ("invalid", f"relationship_test proof text not found in {path}: {needle}")
        return ("proof_backed_edge", None)
    if kind == "source_rationale_downstream":
        from_file = node_files.get(from_id, "")
        to_file = node_files.get(to_id, "")
        downstream = set((source_entries.get(from_file) or {}).get("downstream") or [])
        if to_file not in downstream:
            return ("invalid", f"source_rationale downstream does not link {from_file} -> {to_file}")
        return ("proof_backed_edge", None)
    return ("invalid", f"invalid edge proof kind: {kind!r}")


def core_map_forbidden_hits(payload: dict[str, Any], phrases: list[Any]) -> list[str]:
    serialized = json.dumps(payload)
    hits = [str(phrase) for phrase in phrases if str(phrase) in serialized]
    normalized = " ".join(serialized.split())
    hits.extend(pattern.pattern for pattern in CORE_MAP_FORBIDDEN_PATTERNS if pattern.search(normalized))
    return sorted(dict.fromkeys(hits))


def build_core_map(api: Any, profile_id: str) -> dict[str, Any]:
    profiles = core_map_profiles(api)
    profile = profiles.get(profile_id)
    if not profile:
        raise ValueError(f"unknown core map profile {profile_id!r}")

    node_specs = profile.get("nodes") or []
    edge_specs = profile.get("edges") or []
    max_nodes = int(profile.get("max_nodes") or len(node_specs))
    max_edges = int(profile.get("max_edges") or len(edge_specs))
    if len(node_specs) > max_nodes:
        raise ValueError(f"core map profile {profile_id} exceeds max_nodes={max_nodes}")
    if len(edge_specs) > max_edges:
        raise ValueError(f"core map profile {profile_id} exceeds max_edges={max_edges}")

    source_entries = source_entry_by_path(api)
    node_ids = {str(node.get("id") or "") for node in node_specs}
    node_files = {
        str(node.get("id") or ""): str(node.get("file") or "")
        for node in node_specs
    }
    claim_index = api._claim_proof_index()
    nodes: list[dict[str, Any]] = []
    invalid: list[str] = []

    for node in node_specs:
        node_id = str(node.get("id") or "")
        node_file = str(node.get("file") or "")
        source_entry = source_entries.get(node_file)
        if not node_id or not node_file or not (api.ROOT / node_file).exists():
            invalid.append(f"node {node_id or '<missing>'} missing file {node_file!r}")
            continue
        if not source_entry:
            invalid.append(f"node {node_id} missing source_rationale entry for {node_file}")
            continue

        facts: list[dict[str, Any]] = []
        for claim_id in node.get("required_claims") or []:
            proof = claim_index.get(str(claim_id))
            if not proof:
                invalid.append(f"node {node_id} missing required claim {claim_id}")
                continue
            if proof.get("claim_status") != "replaced":
                invalid.append(f"node {node_id} required claim {claim_id} is not replaced")
                continue
            bad_targets = [
                target for target in proof.get("proof_targets") or []
                if not validate_proof_target_exists(api, target)
            ]
            if bad_targets:
                invalid.append(f"claim {claim_id} has missing proof target")
                continue
            facts.append(
                {
                    "claim_id": claim_id,
                    "text": proof.get("assertion", ""),
                    "confidence": "verified_claim",
                    "source": proof.get("source", {}),
                    "proof_targets": proof.get("proof_targets", []),
                    "gates": proof.get("gates", []),
                }
            )

        nodes.append(
            {
                "id": node_id,
                "file": node_file,
                "zone": source_entry.get("zone", "unknown"),
                "authority_role": source_entry.get("authority_role", ""),
                "confidence": "manifest_grounded",
                "why": source_entry.get("why", ""),
                "hazards": source_entry.get("hazards", []),
                "write_routes": source_entry.get("write_routes", []),
                "facts": facts,
            }
        )

    edges: list[dict[str, Any]] = []
    for edge in edge_specs:
        confidence, reason = edge_proof_status(
            api,
            edge,
            node_ids=node_ids,
            node_files=node_files,
            source_entries=source_entries,
        )
        if edge.get("required", True) and confidence != "proof_backed_edge":
            invalid.append(f"edge {edge.get('from')}->{edge.get('to')} invalid: {reason}")
        edges.append(
            {
                "from": edge.get("from"),
                "to": edge.get("to"),
                "confidence": confidence,
                "proof": edge.get("proof", {}),
                "invalid_reason": reason,
            }
        )

    payload = {
        "profile": profile_id,
        "purpose": profile.get("purpose", ""),
        "authority_status": "generated_view_not_authority",
        "nodes": nodes,
        "edges": edges,
        "invalid": invalid,
        "context_assumption": api.build_context_assumption(
            profile=profile_id,
            profile_kind="core_map_profile",
            source_entries=[
                {"path": node["file"], "upstream": [], "downstream": []}
                for node in nodes
            ],
            confidence_basis=["topology_manifest", "source_rationale"],
        ),
    }
    for hit in core_map_forbidden_hits(payload, profile.get("forbidden_phrases") or []):
        invalid.append(f"forbidden phrase emitted: {hit}")
    return payload


def run_core_maps(api: Any) -> Any:
    issues: list[Any] = []
    profiles = core_map_profiles(api)
    for profile_id in sorted(profiles):
        profile = profiles[profile_id]
        for node in profile.get("nodes") or []:
            node_file = str(node.get("file") or "")
            if node_file.startswith("docs/reference/"):
                issues.append(
                    api._issue(
                        "core_map_reference_authority_leak",
                        profile_id,
                        f"reference doc cannot be a core-map authority node: {node_file}",
                    )
                )
        try:
            payload = build_core_map(api, profile_id)
        except ValueError as exc:
            issues.append(api._issue("core_map_profile_invalid", profile_id, str(exc)))
            continue
        for item in payload.get("invalid") or []:
            issues.append(api._issue("core_map_profile_invalid", profile_id, str(item)))
    return api.StrictResult(ok=not issues, issues=issues)


def build_compiled_topology(api: Any) -> dict[str, Any]:
    topology = api.load_topology()
    lifecycle = api.load_artifact_lifecycle()
    context_budget = api.load_context_budget()
    source_manifests = [
        {"path": path, "exists": (api.ROOT / path).exists()}
        for path in COMPILED_TOPOLOGY_SOURCE_MANIFESTS
    ]
    docs_subroots = topology.get("docs_subroots") or []
    docs_subroot_paths = {str(item.get("path") or "") for item in docs_subroots}
    reviewer_visible = [
        {
            "path": item.get("path"),
            "role": item.get("role"),
            "route_status": "reviewer_visible",
        }
        for item in docs_subroots
        if item.get("path") != "docs/archives"
    ]
    local_only = [
        {
            "path": "docs/archives",
            "role": "historical_archive",
            "route_status": "ignored_archive",
            "reviewer_visible": False,
        }
    ]
    current_state = api.ROOT / "docs/operations/current_state.md"
    current_text = current_state.read_text(encoding="utf-8", errors="ignore") if current_state.exists() else ""
    active_surfaces = sorted(
        api._current_state_operation_paths(
            current_text,
            str((topology.get("active_operations_registry") or {}).get("surface_prefix") or "docs/operations/"),
        )
    )
    active_anchors = sorted(
        str(anchor)
        for anchor in (topology.get("active_operations_registry") or {}).get("required_anchors") or []
        if str(anchor) in current_text
    )
    artifact_roles = [
        {
            "path": item.get("path"),
            "artifact_role": item.get("artifact_role"),
            "route_class": item.get("route_class"),
            "authority_behavior": item.get("authority_behavior"),
        }
        for item in lifecycle.get("liminal_artifacts") or []
    ]
    broken_visible = [
        api.asdict(issue)
        for issue in api._check_broken_internal_paths()
        if issue.code == "docs_broken_internal_path"
    ]
    unclassified_docs_artifacts = [
        api.asdict(issue)
        for issue in api._check_hidden_docs(topology)
        if issue.code in {"docs_unregistered_subtree", "docs_non_markdown_artifact"}
    ]
    dark_write_targets: list[dict[str, Any]] = []
    script_manifest = api.load_script_manifest().get("scripts") or {}
    for script_name, spec in script_manifest.items():
        for target in spec.get("write_targets") or []:
            target = str(target)
            if not target.startswith("docs/"):
                continue
            if any(target == root or target.startswith(f"{root}/") or target.startswith(f"{root}/*") for root in docs_subroot_paths):
                continue
            dark_write_targets.append(
                {
                    "script": script_name,
                    "target": target,
                    "reason": "script manifest writes to docs path outside declared docs_subroots",
                }
            )

    visible_files = set(api._git_visible_files())
    evidence_surface_specs = ((context_budget.get("evidence_surface_budgets") or {}).get("surfaces") or [])
    evidence_surface_telemetry = []
    for spec in evidence_surface_specs:
        rel = str(spec.get("path") or "")
        if not rel:
            continue
        files = sorted(
            path
            for path in visible_files
            if path.startswith(f"{rel}/")
            and (api.ROOT / path).exists()
            and (api.ROOT / path).is_file()
            and (api.ROOT / path).name not in {"AGENTS.md", "README.md"}
        )
        total_bytes = sum((api.ROOT / path).stat().st_size for path in files)
        extension_counts = Counter((api.ROOT / path).suffix or "<none>" for path in files)
        expected_extensions = [str(value) for value in spec.get("expected_extensions") or []]
        unexpected_extensions = [
            ext
            for ext in sorted(extension_counts)
            if expected_extensions and ext not in expected_extensions
        ]
        evidence_surface_telemetry.append(
            {
                "path": rel,
                "role": spec.get("role"),
                "enforcement": spec.get("enforcement", "advisory"),
                "budget_basis": spec.get("budget_basis", "provisional_snapshot"),
                "file_count": len(files),
                "total_bytes": total_bytes,
                "largest_file_bytes": max(((api.ROOT / path).stat().st_size for path in files), default=0),
                "expected_extensions": expected_extensions,
                "extension_counts": dict(sorted(extension_counts.items())),
                "unexpected_extensions": unexpected_extensions,
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "authority_status": "derived_not_authority",
        "source_manifests": source_manifests,
        "freshness_status": "ok" if all(item["exists"] for item in source_manifests) else "missing_source_manifest",
        "docs_subroots": docs_subroots,
        "reviewer_visible_routes": reviewer_visible,
        "local_only_routes": local_only,
        "active_operations_surfaces": {
            "current_state": "docs/operations/current_state.md",
            "operation_paths": active_surfaces,
            "required_anchors": active_anchors,
        },
        "artifact_roles": artifact_roles,
        "broken_visible_routes": broken_visible,
        "unclassified_docs_artifacts": unclassified_docs_artifacts,
        "telemetry": {
            "dark_write_targets": dark_write_targets,
            "dark_write_target_count": len(dark_write_targets),
            "broken_visible_route_count": len(broken_visible),
            "unclassified_docs_artifact_count": len(unclassified_docs_artifacts),
            "evidence_surfaces": evidence_surface_telemetry,
        },
    }
