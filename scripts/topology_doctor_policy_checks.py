"""Governance and policy checker family for topology_doctor."""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-23; last_reused=2026-04-23
# Purpose: Validate governance manifests, planning locks, context budgets, semantic boot profiles, and policy gates.
# Reuse: Prefer small manifest-specific helpers here when the check is governance/tooling policy rather than source or docs topology.

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any


def has_antibody(antibodies: Any) -> bool:
    if not isinstance(antibodies, dict):
        return False
    return any(bool(antibodies.get(key)) for key in ("code", "tests", "gates", "docs"))


def history_lore_path_exists(api: Any, value: str) -> bool:
    if not value or " " in value:
        return True
    if any(char in value for char in "*?[]"):
        return any(api.ROOT.glob(value))
    if "/" not in value and "." not in value:
        return True
    return (api.ROOT / value).exists()


def gate_path_tokens(gate: str) -> list[str]:
    tokens = []
    for token in re.split(r"\s+", gate):
        token = token.strip("'\"")
        if token.startswith(("./", "../")):
            token = token.removeprefix("./")
        if token.startswith(("src/", "scripts/", "tests/", "architecture/", "docs/", "config/")):
            tokens.append(token)
    return tokens


def check_history_lore_antibody_references(api: Any, card_id: str, antibodies: Any) -> list[Any]:
    if not isinstance(antibodies, dict):
        return []
    issues: list[Any] = []
    path = f"architecture/history_lore.yaml:{card_id}"
    for field in ("code", "tests", "docs"):
        for ref in antibodies.get(field) or []:
            ref = str(ref)
            if not history_lore_path_exists(api, ref):
                issues.append(
                    api._issue(
                        "history_lore_stale_antibody_reference",
                        path,
                        f"antibodies.{field} references missing path: {ref}",
                    )
                )
    for gate in antibodies.get("gates") or []:
        for ref in gate_path_tokens(str(gate)):
            if not history_lore_path_exists(api, ref):
                issues.append(
                    api._issue(
                        "history_lore_stale_antibody_reference",
                        path,
                        f"antibodies.gates references missing path: {ref}",
                    )
                )
    return issues


def run_history_lore(api: Any) -> Any:
    lore = api.load_history_lore()
    issues: list[Any] = []
    required = lore.get("required_card_fields") or []
    allowed_statuses = set(lore.get("allowed_statuses") or [])
    allowed_severities = set(lore.get("allowed_severities") or [])
    cards = lore.get("cards") or []
    seen: set[str] = set()

    if not cards:
        issues.append(api._issue("history_lore_empty", "architecture/history_lore.yaml", "no lore cards declared"))

    for idx, card in enumerate(cards):
        card_id = str(card.get("id") or f"card[{idx}]")
        path = f"architecture/history_lore.yaml:{card_id}"
        if card_id in seen:
            issues.append(api._issue("history_lore_duplicate_id", path, "duplicate lore id"))
        seen.add(card_id)

        for field in required:
            if api._metadata_missing(card.get(field)):
                issues.append(api._issue("history_lore_required_field_missing", path, f"missing {field}"))

        status = card.get("status")
        severity = card.get("severity")
        if status not in allowed_statuses:
            issues.append(api._issue("history_lore_invalid_status", path, f"invalid status {status!r}"))
        if severity not in allowed_severities:
            issues.append(api._issue("history_lore_invalid_severity", path, f"invalid severity {severity!r}"))

        routing = card.get("routing") or {}
        if api._metadata_missing(routing.get("task_terms")) and api._metadata_missing(routing.get("file_patterns")):
            issues.append(api._issue("history_lore_missing_routing", path, "task_terms or file_patterns required"))

        if severity in {"critical", "high"} and status in {"active_law", "mitigated"}:
            if not has_antibody(card.get("antibodies")):
                issues.append(
                    api._issue(
                        "history_lore_missing_antibody",
                        path,
                        "critical/high active lore needs code, test, gate, or doc antibody",
                    )
                )
            issues.extend(check_history_lore_antibody_references(api, card_id, card.get("antibodies")))

        if status == "open_gap" and api._metadata_missing(card.get("residual_risk")):
            issues.append(api._issue("history_lore_open_gap_without_residual", path, "open gaps need residual risk"))

        digest = str(card.get("zero_context_digest") or "")
        if len(digest) < 40:
            issues.append(
                api._issue(
                    "history_lore_digest_too_thin",
                    path,
                    "zero_context_digest must be dense enough for routing",
                )
            )
    return api.StrictResult(ok=not issues, issues=issues)


def budget_issue(api: Any, code: str, path: str, message: str, *, enforcement: str) -> Any:
    if enforcement == "blocking":
        return api._issue(code, path, message)
    return api._warning(code, path, message)


def budget_has_blocking_promotion(spec: dict[str, Any]) -> bool:
    return bool(spec.get("promotion_packet") or spec.get("blocking_authority"))


def budget_enforcement_issues(api: Any, spec: dict[str, Any], path: str) -> list[Any]:
    if str(spec.get("enforcement") or "advisory") == "blocking" and not budget_has_blocking_promotion(spec):
        return [
            api._issue(
                "context_budget_blocking_without_promotion",
                path,
                "enforcement=blocking requires promotion_packet or blocking_authority",
            )
        ]
    return []


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())


def run_context_budget(api: Any) -> Any:
    if not api.CONTEXT_BUDGET_PATH.exists():
        issue = api._issue(
            "context_budget_manifest_missing",
            "architecture/context_budget.yaml",
            "context budget manifest is missing",
        )
        return api.StrictResult(ok=False, issues=[issue])

    budget = api.load_context_budget()
    issues: list[Any] = []
    for entry in budget.get("file_budgets") or []:
        rel = str(entry.get("path") or "")
        enforcement = str(entry.get("enforcement") or "advisory")
        max_lines = entry.get("max_lines")
        path = api.ROOT / rel
        issues.extend(budget_enforcement_issues(api, entry, rel or "architecture/context_budget.yaml"))
        if not rel:
            issues.append(budget_issue(api, "context_budget_invalid_value", "architecture/context_budget.yaml", "file budget entry missing path", enforcement=enforcement))
            continue
        if not path.exists():
            issues.append(budget_issue(api, "context_budget_file_missing", rel, "budgeted file is missing", enforcement=enforcement))
            continue
        if not isinstance(max_lines, int) or max_lines <= 0:
            issues.append(budget_issue(api, "context_budget_invalid_value", rel, f"invalid max_lines {max_lines!r}", enforcement=enforcement))
            continue
        actual = line_count(path)
        if actual > max_lines:
            issues.append(budget_issue(api, "context_budget_file_over", rel, f"{actual} lines exceeds budget {max_lines} ({entry.get('role', 'unspecified role')})", enforcement=enforcement))

    lore_budget = ((budget.get("digest_budgets") or {}).get("history_lore") or {})
    lore_enforcement = str(lore_budget.get("enforcement") or "advisory")
    issues.extend(budget_enforcement_issues(api, lore_budget, "digest_budgets.history_lore"))
    max_cards = lore_budget.get("max_cards_per_digest")
    max_digest_chars = lore_budget.get("max_zero_context_digest_chars")
    if max_cards is not None and (not isinstance(max_cards, int) or max_cards <= 0):
        issues.append(budget_issue(api, "context_budget_invalid_value", "digest_budgets.history_lore.max_cards_per_digest", f"invalid max_cards_per_digest {max_cards!r}", enforcement=lore_enforcement))
    if max_digest_chars is not None and (not isinstance(max_digest_chars, int) or max_digest_chars <= 0):
        issues.append(budget_issue(api, "context_budget_invalid_value", "digest_budgets.history_lore.max_zero_context_digest_chars", f"invalid max_zero_context_digest_chars {max_digest_chars!r}", enforcement=lore_enforcement))

    if isinstance(max_cards, int) and max_cards > 0:
        for task in lore_budget.get("sample_tasks") or []:
            digest = api.build_digest(str(task))
            card_count = len(digest.get("history_lore") or [])
            if card_count > max_cards:
                issues.append(budget_issue(api, "context_budget_digest_card_over", f"digest:{task}", f"{card_count} lore cards exceeds budget {max_cards}", enforcement=lore_enforcement))

    if isinstance(max_digest_chars, int) and max_digest_chars > 0:
        for card in (api.load_history_lore().get("cards") or []):
            digest_text = str(card.get("zero_context_digest") or "")
            if len(digest_text) > max_digest_chars:
                issues.append(budget_issue(api, "context_budget_digest_text_over", f"architecture/history_lore.yaml:{card.get('id', '<missing-id>')}", f"zero_context_digest has {len(digest_text)} chars, budget {max_digest_chars}", enforcement=lore_enforcement))

    read_path = budget.get("default_read_path") or {}
    max_files = read_path.get("max_pre_code_reads")
    if max_files is not None and (not isinstance(max_files, int) or max_files <= 0):
        issues.append(api._warning("context_budget_invalid_value", "default_read_path.max_pre_code_reads", f"invalid max_pre_code_reads {max_files!r}"))
    for route, spec in (read_path.get("route_budgets") or {}).items():
        soft_limit = (spec or {}).get("soft_limit")
        expected_reads = (spec or {}).get("expected_reads") or []
        issues.extend(budget_enforcement_issues(api, spec or {}, f"default_read_path.route_budgets.{route}"))
        if not isinstance(soft_limit, int) or soft_limit <= 0:
            issues.append(api._warning("context_budget_invalid_value", f"default_read_path.route_budgets.{route}.soft_limit", f"invalid soft_limit {soft_limit!r}"))
            continue
        if len(expected_reads) > soft_limit:
            issues.append(api._warning("context_budget_route_over", f"default_read_path.route_budgets.{route}", f"{len(expected_reads)} expected reads exceeds soft limit {soft_limit}"))

    evidence_budget = budget.get("evidence_surface_budgets") or {}
    for idx, spec in enumerate(evidence_budget.get("surfaces") or []):
        path = str(spec.get("path") or f"evidence_surface_budgets.surfaces[{idx}]")
        expected_extensions = spec.get("expected_extensions")
        if not path or path.endswith(f"[{idx}]"):
            issues.append(api._warning("context_budget_invalid_value", path, "evidence surface budget missing path"))
            continue
        if not (api.ROOT / path).exists():
            issues.append(api._warning("context_budget_invalid_value", path, "evidence surface budget path does not exist"))
        if not isinstance(expected_extensions, list) or not expected_extensions:
            issues.append(api._warning("context_budget_invalid_value", path, "expected_extensions must be a non-empty list"))
    blocking = [issue for issue in issues if issue.severity == "error"]
    return api.StrictResult(ok=not blocking, issues=issues)


def zone_for_changed_file(api: Any, path: str) -> str:
    if path.startswith("architecture/"):
        return "architecture"
    if path.startswith("docs/authority/"):
        return "docs_authority"
    if path.startswith(".github/workflows/"):
        return "ci"
    if path.startswith("src/control/"):
        return "K1_governance"
    if path.startswith("src/supervisor_api/"):
        return "K2_runtime"
    rationale = api.load_source_rationale()
    files = rationale.get("files") or {}
    if path in files and files[path].get("zone"):
        return str(files[path]["zone"])
    for prefix, spec in (rationale.get("package_defaults") or {}).items():
        if path.startswith(f"{prefix}/") and spec.get("zone"):
            return str(spec["zone"])
    return "unknown"


def planning_lock_trigger(path: str) -> str | None:
    if path.startswith("architecture/"):
        return "architecture"
    if path.startswith("docs/authority/"):
        return "authority docs"
    if path.startswith(".github/workflows/"):
        return "CI workflow"
    if path.startswith("src/control/"):
        return "control plane"
    if path.startswith("src/supervisor_api/"):
        return "supervisor API"
    if path in {
        "src/state/ledger.py",
        "src/state/projection.py",
        "src/state/lifecycle_manager.py",
        "src/state/db.py",
    }:
        return "state truth/schema/lifecycle"
    return None


def valid_plan_evidence(api: Any, path: str | None) -> bool:
    if not path:
        return False
    evidence = api.ROOT / path
    if not evidence.exists() or not evidence.is_file():
        return False
    normalized = evidence.relative_to(api.ROOT).as_posix() if evidence.is_relative_to(api.ROOT) else str(evidence)
    return (
        normalized.startswith("docs/operations/")
        or normalized.startswith(".omx/plans/")
        or normalized.startswith(".omx/context/")
    )


def run_planning_lock(api: Any, changed_files: list[str], plan_evidence: str | None = None) -> Any:
    issues: list[Any] = []
    triggers: dict[str, str] = {}
    for path in changed_files:
        reason = planning_lock_trigger(path)
        if reason:
            triggers[path] = reason
    zones = {zone_for_changed_file(api, path) for path in changed_files}
    zones.discard("unknown")
    zones.discard("docs")
    if len(changed_files) > 4:
        triggers["<change-set>"] = "more than 4 changed files"
    if len(zones) > 1:
        triggers["<change-set>"] = f"cross-zone edit {sorted(zones)}"
    if triggers and not valid_plan_evidence(api, plan_evidence):
        for path, reason in sorted(triggers.items()):
            issues.append(
                api._issue(
                    "planning_lock_required",
                    path,
                    f"planning lock triggered by {reason}; pass --plan-evidence with a valid plan/current-state file",
                )
            )
        if plan_evidence:
            issues.append(
                api._issue(
                    "planning_lock_evidence_invalid",
                    plan_evidence,
                    "plan evidence path does not exist or is not in an approved planning/evidence directory",
                )
            )
    return api.StrictResult(ok=not issues, issues=issues)


def idiom_pattern_for(api: Any, idiom_id: str) -> re.Pattern[str] | None:
    if idiom_id == "SEMANTIC_PROVENANCE_GUARD":
        return api.SEMANTIC_PROVENANCE_GUARD_PATTERN
    return None


def run_idioms(api: Any) -> Any:
    if not api.CODE_IDIOMS_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "code_idiom_manifest_missing",
                    "architecture/code_idioms.yaml",
                    "code idiom manifest is missing",
                )
            ],
        )
    manifest = api.load_code_idioms()
    issues: list[Any] = []
    required = manifest.get("required_idiom_fields") or []
    idioms = manifest.get("idioms") or []
    seen: set[str] = set()
    registered_examples: set[str] = set()
    for idx, idiom in enumerate(idioms):
        idiom_id = str(idiom.get("id") or f"idiom[{idx}]")
        path = f"architecture/code_idioms.yaml:{idiom_id}"
        if idiom_id in seen:
            issues.append(api._issue("code_idiom_duplicate_id", path, "duplicate code idiom id"))
        seen.add(idiom_id)
        for field in required:
            if api._metadata_missing(idiom.get(field)):
                issues.append(api._issue("code_idiom_required_field_missing", path, f"missing {field}"))
        owner_gate = idiom.get("owner_gate")
        if owner_gate and not (api.ROOT / str(owner_gate)).exists():
            issues.append(api._issue("code_idiom_owner_gate_missing", str(owner_gate), f"owner gate for {idiom_id} missing"))
        pattern = idiom_pattern_for(api, idiom_id)
        for example in idiom.get("examples") or []:
            example_path = api.ROOT / str(example)
            registered_examples.add(str(example))
            if not example_path.exists():
                issues.append(api._issue("code_idiom_example_missing", str(example), f"example for {idiom_id} missing"))
                continue
            if pattern and not pattern.search(example_path.read_text(encoding="utf-8", errors="ignore")):
                issues.append(api._issue("code_idiom_example_without_pattern", str(example), f"example does not contain {idiom_id} pattern"))
    semantic_files = set()
    for path in sorted((api.ROOT / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if api.SEMANTIC_PROVENANCE_GUARD_PATTERN.search(text):
            semantic_files.add(path.relative_to(api.ROOT).as_posix())
    for rel in sorted(semantic_files - registered_examples):
        issues.append(api._issue("code_idiom_unregistered_occurrence", rel, "Semantic Provenance Guard occurrence is not registered in architecture/code_idioms.yaml"))
    return api.StrictResult(ok=not issues, issues=issues)


def run_self_check_coherence(api: Any) -> Any:
    issues: list[Any] = []
    root_text = (api.ROOT / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    self_check = (api.ROOT / "architecture" / "self_check" / "zero_context_entry.md").read_text(encoding="utf-8", errors="ignore")
    self_agents = (api.ROOT / "architecture" / "self_check" / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    required_root_refs = [
        "architecture/self_check/authority_index.md",
        "architecture/self_check/zero_context_entry.md",
        "Default Navigation",
        "What To Read By Task",
    ]
    for needle in required_root_refs:
        if needle not in root_text:
            issues.append(api._issue("self_check_root_reference_missing", "AGENTS.md", f"missing {needle}"))
    required_overlay_refs = [
        "Minimum high-risk startup spine",
        "architecture/self_check/authority_index.md",
        "topology_doctor.py --navigation",
        "architecture/kernel_manifest.yaml",
        "architecture/invariants.yaml",
        "architecture/zones.yaml",
        "architecture/source_rationale.yaml",
        "docs/authority/zeus_current_architecture.md",
        "docs/authority/zeus_current_delivery.md",
        "map-maintenance result",
    ]
    for needle in required_overlay_refs:
        if needle not in self_check:
            issues.append(api._issue("self_check_required_read_missing", "architecture/self_check/zero_context_entry.md", f"missing {needle}"))
    if "§9" in self_agents or "What to read next" in self_agents:
        issues.append(api._issue("self_check_stale_cross_reference", "architecture/self_check/AGENTS.md", "stale root AGENTS section reference"))
    if "Default Navigation" not in self_agents or "What To Read By Task" not in self_agents:
        issues.append(api._issue("self_check_stale_cross_reference", "architecture/self_check/AGENTS.md", "missing current root section names"))
    return api.StrictResult(ok=not issues, issues=issues)


def run_runtime_modes(api: Any) -> Any:
    topology = api.load_runtime_modes()
    issues: list[Any] = []
    source = topology.get("source")
    if not source or not (api.ROOT / str(source)).exists():
        issues.append(api._issue("runtime_mode_source_missing", str(source or ""), "runtime mode source missing"))
        return api.StrictResult(ok=False, issues=issues)
    text = (api.ROOT / str(source)).read_text(encoding="utf-8", errors="ignore")
    required_modes = topology.get("required_modes") or {}
    for value, spec in required_modes.items():
        enum = spec.get("enum")
        if not enum:
            issues.append(api._issue("runtime_mode_required_field_missing", value, "missing enum"))
        if not spec.get("purpose"):
            issues.append(api._issue("runtime_mode_required_field_missing", value, "missing purpose"))
        if not spec.get("timing_rule"):
            issues.append(api._issue("runtime_mode_required_field_missing", value, "missing timing_rule"))
        if enum and str(enum) not in text:
            issues.append(api._issue("runtime_mode_enum_missing", str(source), f"enum {enum} missing"))
        if value not in text:
            issues.append(api._issue("runtime_mode_value_missing", str(source), f"value {value} missing"))
    for rel in topology.get("shared_runtime_path") or []:
        if not (api.ROOT / str(rel)).exists():
            issues.append(api._issue("runtime_mode_path_missing", str(rel), "shared runtime path missing"))
    for rel in topology.get("required_tests") or []:
        if not (api.ROOT / str(rel)).exists():
            issues.append(api._issue("runtime_mode_test_missing", str(rel), "runtime mode test missing"))
    root_text = (api.ROOT / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    for value in required_modes:
        if value not in root_text:
            issues.append(api._issue("runtime_mode_root_reference_missing", "AGENTS.md", f"root AGENTS missing {value}"))
    if "architecture/runtime_modes.yaml" not in root_text:
        issues.append(api._issue("runtime_mode_root_reference_missing", "AGENTS.md", "root AGENTS missing runtime mode manifest"))
    return api.StrictResult(ok=not issues, issues=issues)


def manifest_ref_exists(api: Any, value: str) -> bool:
    if not value or " " in value:
        return True
    ref = value.split(":", 1)[0]
    if ref.startswith(("python", "pytest")):
        return True
    if any(char in ref for char in "*?[]"):
        return any(api.ROOT.glob(ref))
    if "/" not in ref and "." not in ref:
        return True
    return (api.ROOT / ref).exists()


def manifest_gate_refs(api: Any, gate: str) -> list[str]:
    return [ref for ref in gate_path_tokens(gate) if not manifest_ref_exists(api, ref)]


def fatal_misread_ids(api: Any) -> set[str]:
    if not api.FATAL_MISREADS_PATH.exists():
        return set()
    return {
        str(item.get("id"))
        for item in (api.load_fatal_misreads().get("misreads") or [])
        if item.get("id")
    }


def task_boot_class_ids(api: Any) -> set[str]:
    if not api.TASK_BOOT_PROFILES_PATH.exists():
        return set()
    return {
        str(item.get("id"))
        for item in (api.load_task_boot_profiles().get("profiles") or [])
        if item.get("id")
    }


def run_task_boot_profiles(api: Any) -> Any:
    if not api.TASK_BOOT_PROFILES_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "task_boot_profiles_manifest_missing",
                    "architecture/task_boot_profiles.yaml",
                    "semantic task boot profile manifest is missing",
                )
            ],
        )
    manifest = api.load_task_boot_profiles()
    issues: list[Any] = []
    required_fields = manifest.get("required_profile_fields") or []
    required_classes = set(manifest.get("required_task_classes") or [])
    graph_stages = set(manifest.get("allowed_graph_stages") or [])
    fatal_ids = fatal_misread_ids(api)
    seen: set[str] = set()

    for profile in manifest.get("profiles") or []:
        profile_id = str(profile.get("id") or "<missing>")
        path = f"architecture/task_boot_profiles.yaml:{profile_id}"
        if profile_id in seen:
            issues.append(api._issue("task_boot_profile_duplicate_id", path, "duplicate task boot profile id"))
        seen.add(profile_id)
        if profile_id not in required_classes:
            issues.append(api._issue("task_boot_profile_unknown_class", path, f"unknown task class {profile_id!r}"))
        for field in required_fields:
            if api._metadata_missing(profile.get(field)):
                issues.append(api._issue("task_boot_profile_required_field_missing", path, f"missing {field}"))
        for field in ("required_reads", "current_fact_surfaces"):
            for ref in profile.get(field) or []:
                ref = str(ref)
                if not manifest_ref_exists(api, ref):
                    issues.append(api._issue("task_boot_profile_path_missing", path, f"{field} references missing path: {ref}"))
        for proof in profile.get("required_proofs") or []:
            proof_id = str(proof.get("id") or "<missing-proof>")
            if api._metadata_missing(proof.get("question")):
                issues.append(api._issue("task_boot_profile_required_field_missing", path, f"proof {proof_id} missing question"))
            evidence = proof.get("evidence") or []
            if not evidence:
                issues.append(api._issue("task_boot_profile_required_field_missing", path, f"proof {proof_id} missing evidence"))
            for ref in evidence:
                ref = str(ref)
                if not manifest_ref_exists(api, ref):
                    issues.append(api._issue("task_boot_profile_path_missing", path, f"proof {proof_id} references missing evidence: {ref}"))
        for misread_id in profile.get("fatal_misreads") or []:
            misread_id = str(misread_id)
            if misread_id not in fatal_ids:
                issues.append(api._issue("task_boot_profile_unknown_fatal_misread", path, f"unknown fatal misread id: {misread_id}"))
        graph_usage = profile.get("graph_usage") or {}
        stage = graph_usage.get("stage")
        if stage not in graph_stages:
            issues.append(api._issue("task_boot_profile_invalid_graph_stage", path, f"invalid graph stage {stage!r}"))
        if graph_usage.get("authority_status") != "derived_not_authority":
            issues.append(
                api._issue(
                    "task_boot_profile_invalid_graph_stage",
                    path,
                    "graph_usage.authority_status must be derived_not_authority",
                )
            )
        for gate in profile.get("verification_gates") or []:
            for ref in manifest_gate_refs(api, str(gate)):
                issues.append(api._issue("task_boot_profile_path_missing", path, f"verification gate references missing path: {ref}"))

    missing = sorted(required_classes - seen)
    for profile_id in missing:
        issues.append(
            api._issue(
                "task_boot_profile_missing_required_class",
                "architecture/task_boot_profiles.yaml",
                f"missing required task class {profile_id}",
            )
        )
    return api.StrictResult(ok=not issues, issues=issues)


def run_fatal_misreads(api: Any) -> Any:
    if not api.FATAL_MISREADS_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "fatal_misreads_manifest_missing",
                    "architecture/fatal_misreads.yaml",
                    "fatal semantic misreads manifest is missing",
                )
            ],
        )
    manifest = api.load_fatal_misreads()
    issues: list[Any] = []
    required_fields = manifest.get("required_misread_fields") or []
    severities = set(manifest.get("allowed_severities") or [])
    task_classes: set[str] = set()
    if api.TASK_BOOT_PROFILES_PATH.exists():
        task_classes = task_boot_class_ids(api) or set((api.load_task_boot_profiles().get("required_task_classes") or []))
    seen: set[str] = set()

    for item in manifest.get("misreads") or []:
        misread_id = str(item.get("id") or "<missing>")
        path = f"architecture/fatal_misreads.yaml:{misread_id}"
        if misread_id in seen:
            issues.append(api._issue("fatal_misread_duplicate_id", path, "duplicate fatal misread id"))
        seen.add(misread_id)
        for field in required_fields:
            if api._metadata_missing(item.get(field)):
                issues.append(api._issue("fatal_misread_required_field_missing", path, f"missing {field}"))
        severity = item.get("severity")
        if severity not in severities:
            issues.append(api._issue("fatal_misread_invalid_severity", path, f"invalid severity {severity!r}"))
        for ref in item.get("proof_files") or []:
            ref = str(ref)
            if not manifest_ref_exists(api, ref):
                issues.append(api._issue("fatal_misread_path_missing", path, f"proof_files references missing path: {ref}"))
        for task_class in item.get("task_classes") or []:
            task_class = str(task_class)
            if task_class not in task_classes:
                issues.append(api._issue("fatal_misread_unknown_task_class", path, f"unknown task class: {task_class}"))
        for gate in item.get("tests") or []:
            for ref in manifest_gate_refs(api, str(gate)):
                issues.append(api._issue("fatal_misread_gate_target_missing", path, f"test gate references missing path: {ref}"))
    return api.StrictResult(ok=not issues, issues=issues)


def city_truth_caution_ids(contract: dict[str, Any]) -> set[str]:
    return {
        str(item.get("id"))
        for item in contract.get("caution_flags") or []
        if item.get("id")
    }


def city_truth_evidence_refs(assertion: dict[str, Any]) -> list[str]:
    refs = assertion.get("evidence_refs")
    if isinstance(refs, list):
        return [str(ref) for ref in refs]
    return []


def check_city_truth_current_assertions(api: Any, contract: dict[str, Any], issues: list[Any]) -> None:
    for idx, assertion in enumerate(contract.get("current_city_truth") or []):
        assertion_id = str(assertion.get("id") or f"current_city_truth[{idx}]")
        path = f"architecture/city_truth_contract.yaml:{assertion_id}"
        for field in ("freshness_class", "current_fact_surface", "invalidation_condition"):
            if api._metadata_missing(assertion.get(field)):
                issues.append(api._issue("city_truth_contract_current_claim_unbacked", path, f"current assertion missing {field}"))
        refs = city_truth_evidence_refs(assertion)
        if not refs:
            issues.append(api._issue("city_truth_contract_current_claim_unbacked", path, "current assertion missing evidence_refs"))
        for ref in refs + [str(assertion.get("current_fact_surface") or "")]:
            if ref and not manifest_ref_exists(api, ref):
                issues.append(api._issue("city_truth_contract_path_missing", path, f"current assertion references missing path: {ref}"))


def run_city_truth_contract(api: Any) -> Any:
    if not api.CITY_TRUTH_CONTRACT_PATH.exists():
        return api.StrictResult(
            ok=False,
            issues=[
                api._issue(
                    "city_truth_contract_manifest_missing",
                    "architecture/city_truth_contract.yaml",
                    "city truth contract manifest is missing",
                )
            ],
        )
    contract = api.load_city_truth_contract()
    issues: list[Any] = []
    required_sections = contract.get("required_contract_sections") or []
    for section in required_sections:
        if api._metadata_missing(contract.get(section)):
            issues.append(
                api._issue(
                    "city_truth_contract_required_field_missing",
                    "architecture/city_truth_contract.yaml",
                    f"missing {section}",
                )
            )

    roles = contract.get("source_roles") or {}
    for role in ("settlement_daily_source", "day0_live_monitor_source", "historical_hourly_source", "forecast_skill_source"):
        path = f"architecture/city_truth_contract.yaml:{role}"
        if role not in roles:
            issues.append(api._issue("city_truth_contract_role_missing", path, f"missing source role {role}"))
            continue
        role_spec = roles.get(role) or {}
        if api._metadata_missing(role_spec.get("description")):
            issues.append(api._issue("city_truth_contract_required_field_missing", path, "missing description"))
        if api._metadata_missing(role_spec.get("required_fields")):
            issues.append(api._issue("city_truth_contract_required_field_missing", path, "missing required_fields"))

    truth_boundary = contract.get("truth_boundary") or {}
    for field in ("current_truth_owner", "current_data_owner", "runtime_config_source"):
        ref = str(truth_boundary.get(field) or "")
        if not ref:
            issues.append(api._issue("city_truth_contract_required_field_missing", "architecture/city_truth_contract.yaml:truth_boundary", f"missing {field}"))
        elif not manifest_ref_exists(api, ref):
            issues.append(api._issue("city_truth_contract_path_missing", "architecture/city_truth_contract.yaml:truth_boundary", f"{field} references missing path: {ref}"))

    caution_ids = city_truth_caution_ids(contract)
    if not caution_ids:
        issues.append(api._issue("city_truth_contract_required_field_missing", "architecture/city_truth_contract.yaml:caution_flags", "missing caution flags"))

    for idx, evidence_class in enumerate(contract.get("evidence_classes") or []):
        class_id = str(evidence_class.get("id") or f"evidence_classes[{idx}]")
        path = f"architecture/city_truth_contract.yaml:{class_id}"
        for field in ("description", "accepted_locations"):
            if api._metadata_missing(evidence_class.get(field)):
                issues.append(api._issue("city_truth_contract_required_field_missing", path, f"missing {field}"))

    for example in contract.get("examples") or []:
        example_id = str(example.get("id") or "<missing-example>")
        path = f"architecture/city_truth_contract.yaml:{example_id}"
        if example.get("classification") != "schema_example_not_current_truth":
            issues.append(api._issue("city_truth_contract_invalid_example", path, "examples must be marked schema_example_not_current_truth"))
        for flag in example.get("caution_flags") or []:
            if str(flag) not in caution_ids:
                issues.append(api._issue("city_truth_contract_invalid_example", path, f"unknown caution flag: {flag}"))
        refs = city_truth_evidence_refs(example)
        if not refs:
            issues.append(api._issue("city_truth_contract_invalid_example", path, "example missing evidence_refs"))
        for ref in refs:
            if not manifest_ref_exists(api, ref):
                issues.append(api._issue("city_truth_contract_path_missing", path, f"example references missing path: {ref}"))

    check_city_truth_current_assertions(api, contract, issues)
    return api.StrictResult(ok=not issues, issues=issues)
