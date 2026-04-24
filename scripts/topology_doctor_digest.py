"""Digest builder family for topology_doctor."""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-16; last_reused=2026-04-16
# Purpose: Build bounded topology digests from machine manifests for agent routing.
# Reuse: Keep emitted law sourced from manifests; do not hardcode active rules here.

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any


def data_rebuild_digest(api: Any) -> dict[str, Any]:
    topology = api.load_data_rebuild_topology()
    rows = topology.get("rebuilt_row_contract", {}).get("tables", {})
    return {
        "live_math_certification": topology.get("live_math_certification", {}),
        "row_contract_tables": {
            name: {
                "required_fields": spec.get("required_fields", []),
                "producer": spec.get("producer_contract") or spec.get("producer_script", ""),
            }
            for name, spec in rows.items()
        },
        "replay_coverage_rule": topology.get("replay_coverage_rule", {}),
        "diagnostic_non_promotion": topology.get("diagnostic_non_promotion", {}),
    }


def script_lifecycle_digest(api: Any) -> dict[str, Any]:
    manifest = api.load_script_manifest()
    naming = api.load_naming_conventions() if api.NAMING_CONVENTIONS_PATH.exists() else {}
    script_naming = (((naming.get("file_naming") or {}).get("scripts") or {}).get("long_lived") or {})
    scripts = manifest.get("scripts") or {}
    return {
        "allowed_lifecycles": manifest.get("allowed_lifecycles", []),
        "long_lived_naming": script_naming or manifest.get("long_lived_naming", {}),
        "naming_conventions": manifest.get("naming_conventions", "architecture/naming_conventions.yaml"),
        "required_effective_fields": manifest.get("required_effective_fields", []),
        "existing_scripts": {
            name: {
                "class": api._effective_script_entry(manifest, name).get("class"),
                "status": api._effective_script_entry(manifest, name).get("status"),
                "lifecycle": api._effective_script_entry(manifest, name).get("lifecycle"),
                "write_targets": api._effective_script_entry(manifest, name).get("write_targets", []),
                "dangerous_if_run": api._effective_script_entry(manifest, name).get("dangerous_if_run", False),
            }
            for name in sorted(scripts)
        },
    }


def compact_lore_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "status": card.get("status"),
        "severity": card.get("severity"),
        "failure_mode": card.get("failure_mode"),
        "wrong_moves": card.get("wrong_moves", []),
        "correct_rule": card.get("correct_rule"),
        "antibodies": card.get("antibodies", {}),
        "residual_risk": card.get("residual_risk"),
        "downstream_blast_radius": card.get("downstream_blast_radius", []),
        "zero_context_digest": card.get("zero_context_digest"),
    }


def matched_history_lore(api: Any, task: str, files: list[str]) -> list[dict[str, Any]]:
    lore = api.load_history_lore()
    task_l = task.lower()
    matched: list[dict[str, Any]] = []
    for card in lore.get("cards") or []:
        routing = card.get("routing") or {}
        terms = [str(term).lower() for term in routing.get("task_terms", [])]
        patterns = [str(pattern) for pattern in routing.get("file_patterns", [])]
        term_hit = any(term and term in task_l for term in terms)
        file_hit = any(fnmatch(file, pattern) for file in files for pattern in patterns)
        if term_hit or file_hit:
            matched.append(compact_lore_card(card))
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        matched,
        key=lambda item: (
            severity_rank.get(str(item.get("severity")), 99),
            str(item.get("id")),
        ),
    )


def build_digest(api: Any, task: str, files: list[str] | None = None) -> dict[str, Any]:
    topology = api.load_topology()
    task_l = task.lower()
    selected = None

    # Step 1: Match by task description text
    for profile in topology.get("digest_profiles", []):
        matches = [str(item).lower() for item in profile.get("match", [])]
        if any(match in task_l for match in matches):
            selected = profile
            break

    # Step 2: If no text match, try file-pattern matching
    if selected is None and files:
        for profile in topology.get("digest_profiles", []):
            file_patterns = profile.get("file_patterns", [])
            if file_patterns and any(
                fnmatch(f, pattern)
                for f in files
                for pattern in file_patterns
            ):
                selected = profile
                break

    # Step 3: Generic fallback
    if selected is None:
        selected = {
            "id": "generic",
            "required_law": ["Read root AGENTS.md and scoped AGENTS.md before editing."],
            "allowed_files": files or [],
            "forbidden_files": [".claude/worktrees/**", ".omx/**", "state/*.db"],
            "gates": ["Run focused tests for touched files."],
            "downstream": [],
            "stop_conditions": ["Stop and plan if authority, lifecycle, control, or DB truth is touched."],
        }

    source_files = files or [
        path
        for path in selected.get("allowed_files", [])
        if isinstance(path, str) and path.startswith("src/")
    ]
    payload = {
        "task": task,
        "profile": selected.get("id", "generic"),
        "files": files or [],
        "required_law": selected.get("required_law", []),
        "allowed_files": selected.get("allowed_files", []),
        "forbidden_files": selected.get("forbidden_files", []),
        "gates": selected.get("gates", []),
        "downstream": selected.get("downstream", []),
        "stop_conditions": selected.get("stop_conditions", []),
    }
    if selected.get("reference_reads"):
        payload["reference_reads"] = selected["reference_reads"]

    # Annotate gates with test trust status
    test_topology = api.load_test_topology()
    trust_policy = test_topology.get("test_trust_policy", {})
    trusted_tests = set((trust_policy.get("trusted_tests") or {}).keys())
    gate_trust = []
    for gate in selected.get("gates", []):
        if gate.startswith("pytest"):
            # Extract test file paths from pytest command
            parts = gate.split()
            test_files = [p for p in parts if p.startswith("tests/")]
            untrusted = [t for t in test_files if t not in trusted_tests]
            if untrusted:
                gate_trust.append({
                    "gate": gate,
                    "status": "audit_required",
                    "untrusted_tests": untrusted,
                })
            else:
                gate_trust.append({"gate": gate, "status": "trusted"})
    if gate_trust:
        payload["gate_trust"] = gate_trust

    source_entries = api._source_rationale_for(source_files)
    payload["source_rationale"] = source_entries
    payload["context_assumption"] = api.build_context_assumption(
        profile=str(selected.get("id", "generic")),
        source_entries=source_entries,
        confidence_basis=["topology_manifest"],
    )
    if selected.get("id") == "add a data backfill":
        payload["data_rebuild_topology"] = data_rebuild_digest(api)
    if selected.get("id") == "add or change script":
        payload["script_lifecycle"] = script_lifecycle_digest(api)
    payload["history_lore"] = matched_history_lore(api, task, files or [])
    return payload

