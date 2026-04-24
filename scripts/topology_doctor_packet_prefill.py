"""Packet-prefill and invariant-slice builders for topology_doctor."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


CONTEXT_EXPAND_TRIGGERS = [
    "touched code crosses a zone boundary",
    "tests fail outside the suggested scope",
    "implementation needs files not listed in files_may_change",
    "truth owner, lifecycle, DB, control, risk, or settlement owner is unclear",
    "reviewer asks about authority or downstream behavior",
    "target uses a write route not listed in the output",
]


def source_rationale_for(api: Any, files: list[str]) -> list[dict[str, Any]]:
    if not files:
        return []
    source_map = api.load_source_rationale()
    rationale = source_map.get("files") or {}
    defaults = source_map.get("package_defaults") or {}
    enriched: list[dict[str, Any]] = []
    for path in files:
        if path not in rationale:
            continue
        package_default: dict[str, Any] = {}
        for prefix, values in defaults.items():
            if path.startswith(f"{prefix}/") and len(prefix) > len(package_default.get("_prefix", "")):
                package_default = {"_prefix": prefix, **values}
        package_default.pop("_prefix", None)
        entry = {**package_default, **rationale[path]}
        entry.setdefault("upstream", [])
        entry.setdefault("downstream", [])
        entry.setdefault("gates", package_default.get("gates", []))
        enriched.append({"path": path, **entry})
    return enriched


def build_context_assumption(
    *,
    profile: str = "",
    profile_kind: str = "digest_profile",
    source_entries: list[dict[str, Any]] | None = None,
    confidence_basis: list[str] | None = None,
) -> dict[str, Any]:
    basis = list(confidence_basis or [])
    if profile:
        basis.append(profile_kind)
    entries = source_entries or []
    if entries:
        basis.append("source_rationale")
        if any(not entry.get("upstream") and not entry.get("downstream") for entry in entries):
            basis.append("missing_relations")
    if not basis:
        basis.append("topology_manifest")
    return {
        "sufficiency": "provisional_starting_packet",
        "authority_status": "incomplete_context",
        "confidence_basis": sorted(dict.fromkeys(basis)),
        "expand_context_if": CONTEXT_EXPAND_TRIGGERS,
        "planning_lock_independent": True,
    }


def normalize_scope(scope: str | None) -> str:
    if not scope:
        return ""
    return scope.strip().strip("/")


def scope_is_file(api: Any, scope: str) -> bool:
    if not scope:
        return False
    path = api.ROOT / scope
    return path.is_file() or Path(scope).suffix != ""


def scope_agent_path(api: Any, scope: str) -> str | None:
    if not scope:
        return None
    path = api.ROOT / scope
    if path.is_file():
        path = path.parent
    while path != api.ROOT and api.ROOT in path.parents:
        agents = path / "AGENTS.md"
        if agents.exists():
            return agents.relative_to(api.ROOT).as_posix()
        path = path.parent
    return None


def zones_for_scope(api: Any, scope: str) -> list[str]:
    normalized = normalize_scope(scope)
    if not normalized:
        return []
    zone_manifest = api._load_yaml(api.ROOT / "architecture" / "zones.yaml")
    touched: set[str] = set()
    for zone, spec in (zone_manifest.get("zones") or {}).items():
        for directory in spec.get("directories") or []:
            directory = str(directory).strip("/")
            if normalized == directory or normalized.startswith(f"{directory}/") or directory.startswith(f"{normalized}/"):
                touched.add(str(zone))
    if touched:
        return sorted(touched)

    rationale = api.load_source_rationale()
    for prefix, spec in (rationale.get("package_defaults") or {}).items():
        if normalized == prefix or normalized.startswith(f"{prefix}/") or prefix.startswith(f"{normalized}/"):
            zone = spec.get("zone")
            if zone:
                touched.add(str(zone))
    return sorted(touched)


def zones_for_files(api: Any, files: list[str]) -> list[str]:
    zones = {api._zone_for_changed_file(file) for file in files}
    zones.discard("unknown")
    zones.discard("docs")
    return sorted(zones)


def packet_zones(api: Any, scope: str, files: list[str]) -> list[str]:
    zones = set(zones_for_scope(api, scope))
    zones.update(zones_for_files(api, files))
    return sorted(zones)


def invariants_for_zones(api: Any, zones: list[str]) -> list[dict[str, Any]]:
    wanted = set(zones)
    if not wanted:
        return []
    invariants = api.load_invariants().get("invariants") or []
    return [
        invariant
        for invariant in invariants
        if wanted & set(invariant.get("zones") or [])
    ]


def build_invariants_slice(api: Any, zone: str | None = None) -> dict[str, Any]:
    invariants = api.load_invariants().get("invariants") or []
    if zone:
        invariants = [
            invariant
            for invariant in invariants
            if zone in set(invariant.get("zones") or [])
        ]
    return {
        "zone": zone,
        "count": len(invariants),
        "invariants": invariants,
    }


def files_may_not_change(api: Any, zones_touched: list[str]) -> list[str]:
    zone_manifest = api._load_yaml(api.ROOT / "architecture" / "zones.yaml")
    forbidden: list[str] = []
    for zone, spec in (zone_manifest.get("zones") or {}).items():
        if zone in zones_touched:
            continue
        forbidden.extend(str(item) for item in spec.get("directories") or [])
    return sorted(dict.fromkeys(forbidden))


def package_gates(api: Any, scope: str, files: list[str]) -> list[str]:
    rationale = api.load_source_rationale()
    defaults = rationale.get("package_defaults") or {}
    candidates = [path for path in files]
    if scope:
        candidates.append(normalize_scope(scope))
    gates: list[str] = []
    for target in candidates:
        for prefix, spec in defaults.items():
            if target == prefix or target.startswith(f"{prefix}/") or prefix.startswith(f"{target}/"):
                gates.extend(spec.get("gates") or [])
    for item in source_rationale_for(api, files):
        gates.extend(item.get("gates") or [])
    return sorted(dict.fromkeys(gates))


def tests_for_packet(api: Any, scope: str, files: list[str]) -> list[str]:
    tests = []
    for gate in package_gates(api, scope, files):
        tests.extend(re.findall(r"tests/[A-Za-z0-9_./-]+\.py", gate))
    if not files:
        return sorted(dict.fromkeys(tests))
    test_topology = api.load_test_topology()
    law_gate = test_topology.get("law_gate") or {}
    targets = [*files]
    for spec in law_gate.values():
        protects = spec.get("protects") or []
        if any(
            target == protected
            or protected.startswith(f"{target}/")
            or target.startswith(f"{protected}/")
            for target in targets
            for protected in protects
        ):
            tests.extend(spec.get("tests") or [])
    return sorted(dict.fromkeys(tests))


def build_packet_prefill(
    api: Any,
    *,
    packet_type: str,
    task: str,
    scope: str = "",
    files: list[str] | None = None,
) -> dict[str, Any]:
    files = files or []
    normalized_scope = normalize_scope(scope)
    zones_touched = packet_zones(api, normalized_scope, files)
    invariants = invariants_for_zones(api, zones_touched)
    scoped_agents = scope_agent_path(api, normalized_scope) if normalized_scope else None
    required_reads = [
        "AGENTS.md",
        "workspace_map.md",
        "architecture/zones.yaml",
        "architecture/negative_constraints.yaml",
    ]
    if invariants:
        required_reads.append(
            "architecture/invariants.yaml#"
            + ",".join(invariant.get("id", "") for invariant in invariants)
        )
    else:
        required_reads.append("architecture/invariants.yaml")
    if normalized_scope.startswith("src") or any(file.startswith("src/") for file in files):
        required_reads.append("architecture/source_rationale.yaml")
    if scoped_agents:
        required_reads.append(scoped_agents)

    source_entries = source_rationale_for(api, files)
    if files:
        files_may_change = files
        files_may_change_semantics = "explicit_files"
    elif normalized_scope and scope_is_file(api, normalized_scope):
        files_may_change = [normalized_scope]
        files_may_change_semantics = "explicit_files"
    elif normalized_scope:
        files_may_change = [f"{normalized_scope}/**"]
        files_may_change_semantics = "directory_glob"
    else:
        files_may_change = []
        files_may_change_semantics = "empty"
    ci_gates = package_gates(api, normalized_scope, files)
    if files:
        ci_gates.append("python scripts/semantic_linter.py --check " + " ".join(files))
    elif normalized_scope.startswith("src/"):
        ci_gates.append(f"python scripts/semantic_linter.py --check {normalized_scope}")

    payload = {
        "packet_type": f"{packet_type}_packet" if not packet_type.endswith("_packet") else packet_type,
        "task": task,
        "objective": "<fill>",
        "why_this_now": "<fill>",
        "why_not_other_approach": ["<fill>"],
        "truth_layer": "<fill>",
        "control_layer": "<fill>",
        "evidence_layer": "<fill>",
        "scope": normalized_scope,
        "zones_touched": zones_touched,
        "invariants_touched": [invariant.get("id") for invariant in invariants],
        "required_reads": sorted(dict.fromkeys(required_reads)),
        "files_may_change": files_may_change,
        "files_may_change_semantics": files_may_change_semantics,
        "files_may_not_change": files_may_not_change(api, zones_touched),
        "schema_changes": any(zone == "K0_frozen_kernel" for zone in zones_touched),
        "ci_gates_required": sorted(dict.fromkeys(ci_gates)),
        "tests_required": tests_for_packet(api, normalized_scope, files),
        "parity_required": any(zone == "K0_frozen_kernel" for zone in zones_touched),
        "replay_required": any(zone == "K3_extension" for zone in zones_touched),
        "rollback": "<fill>",
        "acceptance": ["<fill>"],
        "evidence_required": ["<fill>"],
        "refactor_questions": [
            "What truth surface becomes stronger after this refactor?",
            "Which old surface becomes weaker or is scheduled for deletion?",
            "How will no-behavior-change be proven?",
        ],
    }
    payload["context_assumption"] = build_context_assumption(
        profile=packet_type,
        profile_kind="packet_prefill",
        source_entries=source_entries,
        confidence_basis=["topology_manifest", "package_default"],
    )
    return payload
