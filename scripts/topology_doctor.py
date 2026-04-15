#!/usr/bin/env python3
"""Topology doctor for Zeus's compiled agent-navigation graph."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
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
DATA_REBUILD_TOPOLOGY_PATH = ROOT / "architecture" / "data_rebuild_topology.yaml"
HISTORY_LORE_PATH = ROOT / "architecture" / "history_lore.yaml"
CONTEXT_BUDGET_PATH = ROOT / "architecture" / "context_budget.yaml"
ARTIFACT_LIFECYCLE_PATH = ROOT / "architecture" / "artifact_lifecycle.yaml"
CONTEXT_PACK_PROFILES_PATH = ROOT / "architecture" / "context_pack_profiles.yaml"
CODE_IDIOMS_PATH = ROOT / "architecture" / "code_idioms.yaml"
RUNTIME_MODES_PATH = ROOT / "architecture" / "runtime_modes.yaml"
REFERENCE_REPLACEMENT_PATH = ROOT / "architecture" / "reference_replacement.yaml"
CORE_CLAIMS_PATH = ROOT / "architecture" / "core_claims.yaml"
MAP_MAINTENANCE_PATH = ROOT / "architecture" / "map_maintenance.yaml"
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


@dataclass(frozen=True)
class StrictResult:
    ok: bool
    issues: list[TopologyIssue]


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


def load_data_rebuild_topology() -> dict[str, Any]:
    return _load_yaml(DATA_REBUILD_TOPOLOGY_PATH)


def load_history_lore() -> dict[str, Any]:
    return _load_yaml(HISTORY_LORE_PATH)


def load_context_budget() -> dict[str, Any]:
    return _load_yaml(CONTEXT_BUDGET_PATH)


def load_artifact_lifecycle() -> dict[str, Any]:
    return _load_yaml(ARTIFACT_LIFECYCLE_PATH)


def load_context_pack_profiles() -> dict[str, Any]:
    return _load_yaml(CONTEXT_PACK_PROFILES_PATH)


def load_code_idioms() -> dict[str, Any]:
    return _load_yaml(CODE_IDIOMS_PATH)


def load_runtime_modes() -> dict[str, Any]:
    return _load_yaml(RUNTIME_MODES_PATH)


def load_reference_replacement() -> dict[str, Any]:
    return _load_yaml(REFERENCE_REPLACEMENT_PATH)


def load_core_claims() -> dict[str, Any]:
    return _load_yaml(CORE_CLAIMS_PATH)


def load_map_maintenance() -> dict[str, Any]:
    return _load_yaml(MAP_MAINTENANCE_PATH)


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


def _issue(code: str, path: str, message: str) -> TopologyIssue:
    return TopologyIssue(code=code, path=path, message=message)


def _warning(code: str, path: str, message: str) -> TopologyIssue:
    return TopologyIssue(code=code, path=path, message=message, severity="warning")


def _declared_paths(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("path", "")) for item in items if item.get("path")}


def _path_declared(path: str, declared: set[str]) -> bool:
    return path in declared or any(
        any(char in pattern for char in "*?[") and fnmatch(path, pattern)
        for pattern in declared
    )


def _markdown_path_tokens(text: str) -> set[str]:
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


def _registry_entries(agents_path: Path, *, include_directory_tokens: bool = False) -> set[str]:
    entries: set[str] = set()
    for line in agents_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 3:
            continue
        first_cell = cells[1]
        for token in _markdown_path_tokens(first_cell):
            if "*" in token or (token.endswith("/") and not include_directory_tokens):
                continue
            entries.add(token)
    return entries


def _registry_target(directory: Path, token: str) -> Path:
    if token.startswith(("/", "~")):
        return Path(token).expanduser()
    if "/" in token and (ROOT / token).exists():
        return ROOT / token
    return directory / token


def _active_registry_dirs(topology: dict[str, Any]) -> list[Path]:
    dirs = []
    for item in topology.get("registry_directories", []):
        rel = item.get("path")
        if not rel:
            continue
        directory = ROOT / rel
        if (directory / "AGENTS.md").exists():
            dirs.append(directory)
    return dirs


def _is_root_scratch(path: Path) -> bool:
    return path.name in {".DS_Store", ".git-commit-msg.tmp"} or path.name.startswith(".")


def _check_schema(topology: dict[str, Any], schema: dict[str, Any]) -> list[TopologyIssue]:
    issues = []
    for key in schema.get("required_top_level_keys", []):
        if key not in topology:
            issues.append(_issue("missing_schema_key", "architecture/topology.yaml", key))
    return issues


def _check_coverage(topology: dict[str, Any]) -> list[TopologyIssue]:
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
    declared = _declared_paths(topology.get("coverage_roots", []))
    return [
        _issue("missing_coverage_root", path, "coverage root is not declared")
        for path in sorted(required - declared)
    ]


def _check_active_pointers(topology: dict[str, Any]) -> list[TopologyIssue]:
    issues = []
    for item in topology.get("required_active_pointers", []):
        rel = item["path"]
        if not (ROOT / rel).exists():
            issues.append(_issue("missing_active_pointer", rel, item.get("rationale", "")))
    return issues


def _check_registries(topology: dict[str, Any], tracked: list[str]) -> list[TopologyIssue]:
    issues: list[TopologyIssue] = []
    tracked_set = set(tracked)
    script_manifest = load_script_manifest().get("scripts") if SCRIPT_MANIFEST_PATH.exists() else {}
    test_topology = load_test_topology().get("categories") if TEST_TOPOLOGY_PATH.exists() else {}
    test_manifest: set[str] = set()
    for paths in (test_topology or {}).values():
        test_manifest.update(paths or [])

    for directory in _active_registry_dirs(topology):
        agents_path = directory / "AGENTS.md"
        rel_dir = directory.relative_to(ROOT).as_posix()
        entries = _registry_entries(agents_path)

        for token in sorted(entries):
            target = _registry_target(directory, token)
            if not target.exists():
                target_rel = (
                    target.relative_to(ROOT).as_posix()
                    if target.is_relative_to(ROOT)
                    else str(target)
                )
                issues.append(
                    _issue(
                        "stale_registry_entry",
                        f"{agents_path.relative_to(ROOT).as_posix()}:{token}",
                        f"registry target missing: {target_rel}",
                    )
                )

        for rel in tracked_set:
            path = ROOT / rel
            if path.parent != directory or path.name == "AGENTS.md":
                continue
            if rel.startswith("scripts/") and path.name in script_manifest:
                continue
            if rel.startswith("tests/") and rel in test_manifest:
                continue
            if path.name not in entries and rel not in entries:
                issues.append(
                    _issue(
                        "unregistered_tracked_file",
                        rel,
                        f"not listed in {rel_dir}/AGENTS.md file registry",
                    )
                )
    return issues


def _check_reference_authority(topology: dict[str, Any]) -> list[TopologyIssue]:
    evidence = {
        item.get("path"): item.get("enforcement", [])
        for item in topology.get("authority_claims", [])
    }
    issues = []
    for path in sorted((ROOT / "docs" / "reference").glob("*.md")):
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        claims_authority = (
            "**Authority**" in text
            or "code is wrong" in text
            or "source of truth for implementation" in text
        )
        explicitly_reference_only = "Reference-only" in text or "Reference material" in text
        if claims_authority and not explicitly_reference_only and not evidence.get(rel):
            issues.append(
                _issue(
                    "reference_authority_without_enforcement",
                    rel,
                    "reference doc claims authority/source-of-truth without enforcement link",
                )
            )
    return issues



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


def _check_docs_subtree_agents(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _docs_checks().check_docs_subtree_agents(sys.modules[__name__], topology)


def _internal_path_candidates(text: str) -> set[str]:
    return _docs_checks().internal_path_candidates(text)


def _check_broken_internal_paths() -> list[TopologyIssue]:
    return _docs_checks().check_broken_internal_paths(sys.modules[__name__])


def _check_config_agents_volatile_facts() -> list[TopologyIssue]:
    return _docs_checks().check_config_agents_volatile_facts(sys.modules[__name__])


def _current_state_operation_paths(text: str, surface_prefix: str) -> set[str]:
    return _docs_checks().current_state_operation_paths(sys.modules[__name__], text, surface_prefix)


def _check_active_operations_registry(topology: dict[str, Any]) -> list[TopologyIssue]:
    return _docs_checks().check_active_operations_registry(sys.modules[__name__], topology)


def _check_root_and_state_classification(topology: dict[str, Any]) -> list[TopologyIssue]:
    issues = []
    visible_files = {
        rel
        for rel in _git_visible_files()
        if (ROOT / rel).exists() and (ROOT / rel).is_file()
    }
    root_declared = _declared_paths(topology.get("root_governed_files", []))
    for rel in sorted(path for path in visible_files if "/" not in path):
        path = ROOT / rel
        if _is_root_scratch(path):
            continue
        if not _path_declared(rel, root_declared):
            issues.append(
                _issue("unclassified_root_artifact", rel, "repo-root file is not classified")
            )

    state_declared = _declared_paths(topology.get("state_surfaces", []))
    for rel in sorted(
        path
        for path in visible_files
        if path.startswith("state/") and Path(path).parent.as_posix() == "state"
    ):
        if Path(rel).name == ".DS_Store":
            continue
        if not _path_declared(rel, state_declared):
            issues.append(
                _issue("unclassified_state_surface", rel, "state/artifact file is not classified")
            )
    return issues


def _check_shadow_authority_references() -> list[TopologyIssue]:
    issues = []
    scan_roots = [ROOT / "AGENTS.md", ROOT / "workspace_map.md", ROOT / "docs"]
    for root in scan_roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*.md"))
        for path in paths:
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("docs/archives/"):
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                lower = line.lower()
                if (".omx/" in line or ".claude/worktrees/" in line) and (
                    "active" in lower or "authority" in lower or "companion" in lower
                ):
                    issues.append(
                        _issue(
                            "shadow_runtime_authority_reference",
                            f"{rel}:{lineno}",
                            "runtime/shadow path is referenced in an active-authority context",
                        )
                    )
    return issues


def _check_wmo_gate() -> list[TopologyIssue]:
    issues = []
    settlement = (ROOT / "src" / "contracts" / "settlement_semantics.py").read_text(encoding="utf-8")
    assumptions = (ROOT / "state" / "assumptions.json").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_instrument_invariants.py").read_text(encoding="utf-8")
    if "wmo_half_up" not in settlement or "np.floor(scaled + 0.5)" not in settlement:
        issues.append(_issue("wmo_law_gate_missing", "src/contracts/settlement_semantics.py", "WMO helper missing"))
    if '"rounding_rule": "wmo_half_up"' not in assumptions:
        issues.append(_issue("wmo_law_gate_missing", "state/assumptions.json", "assumption manifest not WMO"))
    if "test_no_python_banker_rounding_of_settlement_values_in_active_code" not in tests:
        issues.append(_issue("wmo_law_gate_missing", "tests/test_instrument_invariants.py", "recurrence test missing"))
    return issues


def run_strict() -> StrictResult:
    topology = load_topology()
    schema = load_schema()
    tracked = _git_ls_files()
    issues: list[TopologyIssue] = []
    issues.extend(_check_schema(topology, schema))
    issues.extend(_check_coverage(topology))
    issues.extend(_check_active_pointers(topology))
    issues.extend(_check_registries(topology, tracked))
    issues.extend(_check_reference_authority(topology))
    issues.extend(_check_hidden_docs(topology))
    issues.extend(_check_root_and_state_classification(topology))
    issues.extend(_check_shadow_authority_references())
    issues.extend(_check_wmo_gate())
    return StrictResult(ok=not issues, issues=issues)


def run_docs() -> StrictResult:
    topology = load_topology()
    tracked = _git_ls_files()
    issues: list[TopologyIssue] = []
    issues.extend(_check_active_pointers(topology))
    issues.extend(_check_registries(topology, tracked))
    issues = [
        issue
        for issue in issues
        if issue.path.startswith("docs/")
        or issue.path.startswith("architecture/self_check/")
        or issue.path.startswith("docs")
    ]
    issues.extend(_check_reference_authority(topology))
    issues.extend(_check_hidden_docs(topology))
    issues.extend(_check_docs_subtree_agents(topology))
    issues.extend(_check_broken_internal_paths())
    issues.extend(_check_active_operations_registry(topology))
    issues.extend(_check_config_agents_volatile_facts())
    issues.extend(_check_shadow_authority_references())
    return StrictResult(ok=not issues, issues=issues)


def run_source() -> StrictResult:
    rationale = load_source_rationale()
    tracked_src = sorted(path for path in _git_ls_files() if path.startswith("src/"))
    declared = set((rationale.get("files") or {}).keys())
    issues: list[TopologyIssue] = []

    for path in tracked_src:
        if path not in declared:
            issues.append(_issue("source_rationale_missing", path, "tracked src file has no rationale entry"))

    for path in sorted(declared):
        if path.startswith("src/") and path not in tracked_src:
            issues.append(_issue("source_rationale_stale", path, "rationale entry has no tracked src file"))

    state_files = [
        path
        for path in tracked_src
        if path.startswith("src/state/")
        and path not in {"src/state/AGENTS.md", "src/state/__init__.py"}
    ]
    files = rationale.get("files") or {}
    hazards = set((rationale.get("hazard_badges") or {}).keys())
    write_routes = set((rationale.get("write_routes") or {}).keys())
    for path in state_files:
        zone = (files.get(path) or {}).get("zone")
        if zone not in {"K0_frozen_kernel", "K2_runtime"}:
            issues.append(
                _issue(
                    "source_state_role_unsplit",
                    path,
                    f"src/state file must be split K0/K2, got {zone!r}",
                )
            )

    for route in (
        "canonical_position_write",
        "control_write",
        "settlement_write",
        "backtest_diagnostic_write",
        "calibration_persistence_write",
        "calibration_decision_group_write",
        "decision_artifact_write",
        "script_repair_write",
    ):
        if route not in (rationale.get("write_routes") or {}):
            issues.append(_issue("source_write_route_missing", route, "required write route card missing"))

    for path, entry in files.items():
        for field in ("zone", "authority_role", "why"):
            if not entry.get(field):
                issues.append(_issue("source_required_field_missing", path, f"missing {field}"))
        for hazard in entry.get("hazards", []):
            if hazard not in hazards:
                issues.append(_issue("source_unknown_hazard", path, f"unknown hazard badge {hazard}"))
        for route in entry.get("write_routes", []):
            if route not in write_routes:
                issues.append(_issue("source_unknown_write_route", path, f"unknown write route {route}"))

    required_file_routes = {
        "src/calibration/store.py": "calibration_persistence_write",
        "src/calibration/effective_sample_size.py": "calibration_decision_group_write",
        "src/state/decision_chain.py": "decision_artifact_write",
        "src/state/ledger.py": "canonical_position_write",
        "src/state/projection.py": "canonical_position_write",
        "src/execution/harvester.py": "settlement_write",
        "src/engine/replay.py": "backtest_diagnostic_write",
        "src/control/control_plane.py": "control_write",
    }
    for path, route in required_file_routes.items():
        if route not in (files.get(path) or {}).get("write_routes", []):
            issues.append(_issue("source_file_write_route_missing", path, f"missing required write route {route}"))

    required_file_roles = {
        "src/state/strategy_tracker.py": ("K2_runtime", "derived_strategy_tracker"),
        "src/observability/status_summary.py": ("K2_runtime", "derived_status_read_model"),
    }
    for path, (zone, role) in required_file_roles.items():
        entry = files.get(path) or {}
        if entry.get("zone") != zone or entry.get("authority_role") != role:
            issues.append(
                _issue(
                    "source_file_role_mismatch",
                    path,
                    f"expected zone={zone} authority_role={role}",
                )
            )

    return StrictResult(ok=not issues, issues=issues)


def _expected_zone_for_agents_path(rationale: dict[str, Any], agents_rel: str) -> str | None:
    directory = agents_rel.removesuffix("/AGENTS.md")
    defaults = rationale.get("package_defaults") or {}
    if directory in defaults:
        return (defaults.get(directory) or {}).get("zone")
    files = rationale.get("files") or {}
    if agents_rel in files:
        return (files.get(agents_rel) or {}).get("zone")
    return None


def _declared_zone_in_agents(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = ZONE_DECLARATION_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(1)
    aliases = {
        "K0": "K0_frozen_kernel",
        "K1": "K1_governance",
        "K2": "K2_runtime",
        "K3": "K3_extension",
        "K4": "K4_experimental",
        "Cross": "K0_frozen_kernel",
        "Cross-cutting": "K0_frozen_kernel",
    }
    return aliases.get(raw, raw)


def run_agents_coherence() -> StrictResult:
    rationale = load_source_rationale()
    issues: list[TopologyIssue] = []
    for path in sorted((ROOT / "src").glob("*/AGENTS.md")):
        rel = path.relative_to(ROOT).as_posix()
        declared = _declared_zone_in_agents(path)
        expected = _expected_zone_for_agents_path(rationale, rel)
        if declared and expected and declared != expected:
            issues.append(
                _issue(
                    "agents_zone_mismatch",
                    rel,
                    f"declares {declared}, but source_rationale/package default declares {expected}",
                )
            )
        if expected in {"K0_frozen_kernel", "K1_governance", "K2_runtime"}:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if "no planning lock required" in text:
                issues.append(
                    _issue(
                        "agents_planning_lock_downgrade",
                        rel,
                        f"{expected} scoped AGENTS lowers planning-lock expectations",
                    )
                )
    return StrictResult(ok=not issues, issues=issues)


def run_tests() -> StrictResult:
    topology = load_test_topology()
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
    }
    categories = topology.get("categories") or {}
    classified: dict[str, str] = {}
    issues: list[TopologyIssue] = []

    for category, paths in categories.items():
        for path in paths or []:
            if path in classified:
                issues.append(
                    _issue(
                        "test_topology_duplicate_classification",
                        path,
                        f"classified in both {classified[path]} and {category}",
                    )
                )
            classified[path] = category

    classified_set = set(classified)
    for path in sorted(actual - classified_set):
        issues.append(_issue("test_topology_missing", path, "test file has no topology classification"))
    for path in sorted(classified_set - actual):
        issues.append(_issue("test_topology_stale", path, "classified test file is absent"))

    required_law = {
        "WMO_ROUNDING",
        "LIFECYCLE",
        "CANONICAL_DB_TRUTH",
        "LIVE_BACKTEST_SHADOW",
        "STRATEGY_KEY",
        "UNIT_BIN_TOPOLOGY",
        "P_RAW_PROVENANCE",
        "FDR_FAMILY",
        "NO_DIAGNOSTIC_PROMOTION",
    }
    law_gate = topology.get("law_gate") or {}
    law_test_exceptions = set(topology.get("law_test_category_exceptions") or [])
    for law in sorted(required_law - set(law_gate)):
        issues.append(_issue("test_law_gate_missing", law, "required law gate topic missing"))
    for law, spec in law_gate.items():
        if not spec.get("tests"):
            issues.append(_issue("test_law_gate_missing_tests", law, "law gate has no tests"))
        for path in spec.get("tests", []):
            if path not in actual:
                issues.append(_issue("test_law_gate_stale_test", path, f"{law} references absent test"))
            elif classified.get(path) != "core_law_antibody" and path not in law_test_exceptions:
                issues.append(
                    _issue(
                        "test_law_gate_non_core",
                        path,
                        f"{law} law-gate test is classified as {classified.get(path)}",
                    )
                )
        for path in spec.get("protects", []):
            if not (ROOT / path).exists():
                issues.append(_issue("test_law_gate_stale_protects", path, f"{law} protects missing path"))

    wmo_protects = set((law_gate.get("WMO_ROUNDING") or {}).get("protects", []))
    for path in (
        "src/engine/replay.py",
        "src/engine/monitor_refresh.py",
        "src/execution/harvester.py",
        "src/calibration/store.py",
    ):
        if path not in wmo_protects:
            issues.append(_issue("test_law_gate_incomplete_protects", path, "WMO_ROUNDING missing Packet 1 downstream"))

    high_sensitivity = topology.get("high_sensitivity_skips") or {}
    high_sensitivity_required = {
        path
        for path in actual
        if classified.get(path) == "core_law_antibody"
        and SKIP_PATTERN.search((ROOT / path).read_text(encoding="utf-8", errors="ignore"))
    }
    for path in sorted(high_sensitivity_required - set(high_sensitivity)):
        issues.append(
            _issue(
                "test_high_sensitivity_missing",
                path,
                "core law test contains skip markers but has no high-sensitivity skip status",
            )
        )
    for path, spec in high_sensitivity.items():
        if path not in actual:
            issues.append(_issue("test_high_sensitivity_stale", path, "skip status references absent test"))
        for key in ("owner", "packet", "reason", "sunset"):
            if not spec.get(key):
                issues.append(_issue("test_high_sensitivity_incomplete", path, f"missing {key}"))
        if path in actual:
            text = (ROOT / path).read_text(encoding="utf-8", errors="ignore")
            skip_count = len(SKIP_PATTERN.findall(text))
            if spec.get("skip_count") != skip_count:
                issues.append(
                    _issue(
                        "test_high_sensitivity_skip_count_mismatch",
                        path,
                        f"expected {spec.get('skip_count')} skips, found {skip_count}",
                    )
                )
            for pattern in spec.get("reason_patterns", []):
                if pattern not in text:
                    issues.append(
                        _issue(
                            "test_high_sensitivity_reason_missing",
                            path,
                            f"missing skip reason pattern {pattern!r}",
                        )
                    )

    reverse = topology.get("reverse_antibody_status") or {}
    for item in reverse.get("active", []) or []:
        issues.append(
            _issue(
                "test_reverse_antibody_active",
                str(item),
                "active reverse-antibody must be rewritten or quarantined",
            )
        )
    for path in sorted(actual):
        text = (ROOT / path).read_text(encoding="utf-8", errors="ignore")
        for pattern in DANGEROUS_REVERSE_ANTIBODY_PATTERNS:
            if pattern.search(text):
                issues.append(
                    _issue(
                        "test_reverse_antibody_detected",
                        path,
                        f"dangerous assertion shape matched {pattern.pattern}",
                    )
                )

    for manifest in topology.get("relationship_test_manifests") or []:
        rel = manifest.get("path")
        if not rel:
            issues.append(_issue("test_relationship_manifest_missing_path", "relationship_test_manifests", "missing path"))
            continue
        manifest_path = ROOT / str(rel)
        if not manifest_path.exists():
            issues.append(_issue("test_relationship_manifest_missing", str(rel), "relationship manifest file missing"))
            continue
        module = ast.parse(manifest_path.read_text(encoding="utf-8"))
        defined: set[str] = set()
        for node in module.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)
        for symbol in manifest.get("required_symbols") or []:
            if symbol not in defined:
                issues.append(_issue("test_relationship_manifest_missing_symbol", str(rel), f"missing {symbol}"))
        for protected in manifest.get("protects") or []:
            if not (ROOT / str(protected)).exists():
                issues.append(_issue("test_relationship_manifest_protects_missing", str(protected), f"{rel} protects missing test"))

    return StrictResult(ok=not issues, issues=issues)


SQL_MUTATION_PATTERN = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE)\b", re.IGNORECASE)
FILE_WRITE_PATTERN = re.compile(r"(\.write_text\(|open\([^)]*['\"]w|json\.dump\()", re.IGNORECASE)
CANONICAL_WRITE_HELPERS = (
    "append_event_and_project",
    "append_many_and_project",
    "log_trade_entry",
    "log_settlement_event",
    "log_shadow_signal",
    "store_artifact",
)


def _top_level_scripts() -> set[str]:
    return {
        path.name
        for path in (ROOT / "scripts").iterdir()
        if path.is_file() and path.suffix in {".py", ".sh"}
    }


def _effective_script_entry(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    scripts = manifest.get("scripts") or {}
    entry = dict(scripts.get(name) or {})
    defaults = dict((manifest.get("class_defaults") or {}).get(entry.get("class"), {}))
    return {**defaults, **entry}


def _metadata_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in EMPTY_METADATA_VALUES
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


def _long_lived_script_name_allowed(manifest: dict[str, Any], name: str) -> bool:
    naming = manifest.get("long_lived_naming") or {}
    prefixes = tuple(naming.get("allowed_prefixes") or ())
    exceptions = set((naming.get("exceptions") or {}).keys())
    return name.startswith(prefixes) or name in exceptions


def _write_target_allowed(target: str, allowed: set[str]) -> bool:
    return any(target == pattern or fnmatch(target, pattern) for pattern in allowed)


def _parse_delete_by(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _check_script_lifecycle(
    manifest: dict[str, Any],
    name: str,
    effective: dict[str, Any],
) -> list[TopologyIssue]:
    rel = f"scripts/{name}"
    issues: list[TopologyIssue] = []
    lifecycle = effective.get("lifecycle")
    allowed_lifecycles = set(
        manifest.get("allowed_lifecycles")
        or {"long_lived", "packet_ephemeral", "promotion_candidate", "deprecated_fail_closed"}
    )

    if lifecycle not in allowed_lifecycles:
        return [_issue("script_lifecycle_invalid", rel, f"invalid lifecycle {lifecycle!r}")]

    if effective.get("status") == "deprecated" and lifecycle != "deprecated_fail_closed":
        issues.append(
            _issue(
                "script_deprecated_not_fail_closed",
                rel,
                "deprecated scripts must use lifecycle=deprecated_fail_closed",
            )
        )

    if lifecycle == "long_lived":
        for field in ("reuse_when", "do_not_use_when", "canonical_command", "delete_policy"):
            if _metadata_missing(effective.get(field)):
                issues.append(_issue("script_long_lived_metadata_missing", rel, f"missing {field}"))
        if effective.get("delete_policy") == "delete_on_packet_close":
            issues.append(
                _issue(
                    "script_long_lived_delete_policy_invalid",
                    rel,
                    "long-lived scripts must not use delete_on_packet_close",
                )
            )
        if ONE_OFF_SCRIPT_NAME_PATTERN.search(name):
            issues.append(
                _issue(
                    "script_long_lived_one_off_name",
                    rel,
                    "task/probe/scratch names must be packet_ephemeral or renamed before promotion",
                )
            )
        if not _long_lived_script_name_allowed(manifest, name):
            issues.append(
                _issue(
                    "script_long_lived_bad_name",
                    rel,
                    "long-lived script name must use an allowed prefix or a documented naming exception",
                )
            )

    if lifecycle == "packet_ephemeral":
        if not EPHEMERAL_SCRIPT_NAME_PATTERN.fullmatch(name):
            issues.append(
                _issue(
                    "script_ephemeral_bad_name",
                    rel,
                    "packet-ephemeral scripts must use task_YYYY-MM-DD_<purpose> naming",
                )
            )
        for field in ("owner_packet", "created_for"):
            if _metadata_missing(effective.get(field)):
                issues.append(_issue("script_ephemeral_metadata_missing", rel, f"missing {field}"))
        delete_by_raw = effective.get("delete_by")
        delete_by = _parse_delete_by(delete_by_raw)
        if not delete_by:
            issues.append(
                _issue(
                    "script_ephemeral_delete_policy_missing",
                    rel,
                    "packet-ephemeral scripts need delete_by=YYYY-MM-DD",
                )
            )
        if delete_by_raw and not delete_by:
            issues.append(
                _issue(
                    "script_ephemeral_delete_by_invalid",
                    rel,
                    "delete_by must be YYYY-MM-DD",
                )
            )
        if delete_by and delete_by < date.today():
            issues.append(
                _issue(
                    "script_ephemeral_expired",
                    rel,
                    "packet-ephemeral script is past delete_by and must be deleted or promoted",
                )
            )
        if effective.get("delete_policy") == "retain_until_superseded":
            issues.append(
                _issue(
                    "script_ephemeral_delete_policy_invalid",
                    rel,
                    "packet-ephemeral scripts must not retain_until_superseded",
                )
            )

    if lifecycle == "promotion_candidate":
        for field in ("owner_packet", "created_for", "promotion_deadline", "promotion_decision"):
            if _metadata_missing(effective.get(field)):
                issues.append(_issue("script_promotion_candidate_metadata_missing", rel, f"missing {field}"))
        promotion_deadline = _parse_delete_by(effective.get("promotion_deadline"))
        if effective.get("promotion_deadline") and not promotion_deadline:
            issues.append(
                _issue(
                    "script_promotion_candidate_deadline_invalid",
                    rel,
                    "promotion_deadline must be YYYY-MM-DD",
                )
            )
        if promotion_deadline and promotion_deadline < date.today():
            issues.append(
                _issue(
                    "script_promotion_candidate_expired",
                    rel,
                    "promotion candidate is past promotion_deadline and must be promoted or deleted",
                )
            )

    if lifecycle == "deprecated_fail_closed":
        if effective.get("status") != "deprecated":
            issues.append(
                _issue(
                    "script_deprecated_lifecycle_status_mismatch",
                    rel,
                    "deprecated_fail_closed scripts must carry status=deprecated",
                )
            )
        if effective.get("fail_closed") is not True:
            issues.append(
                _issue(
                    "script_deprecated_not_fail_closed",
                    rel,
                    "deprecated_fail_closed scripts must set fail_closed=true",
                )
            )
        if not any(effective.get(field) for field in ("reason", "replacement", "archive_reason")):
            issues.append(
                _issue(
                    "script_deprecated_missing_disposition",
                    rel,
                    "deprecated scripts need reason, replacement, or archive_reason",
                )
            )
        if effective.get("canonical_command") != "DO_NOT_RUN":
            issues.append(
                _issue(
                    "script_deprecated_runnable_command",
                    rel,
                    "deprecated scripts must use canonical_command=DO_NOT_RUN",
                )
            )

    return issues


def run_scripts() -> StrictResult:
    manifest = load_script_manifest()
    actual = _top_level_scripts()
    declared = set((manifest.get("scripts") or {}).keys())
    required = manifest.get("required_effective_fields") or []
    diagnostic_allowed = set(manifest.get("diagnostic_allowed_write_targets") or [])
    canonical_targets = set(manifest.get("canonical_db_targets") or [])
    issues: list[TopologyIssue] = []

    for name in sorted(actual - declared):
        issues.append(_issue("script_manifest_missing", f"scripts/{name}", "top-level script has no manifest entry"))
    for name in sorted(declared - actual):
        issues.append(_issue("script_manifest_stale", f"scripts/{name}", "manifest entry has no top-level script"))

    for name in sorted(actual & declared):
        effective = _effective_script_entry(manifest, name)
        rel = f"scripts/{name}"
        for field in required:
            if field not in effective:
                issues.append(_issue("script_manifest_required_field_missing", rel, f"missing {field}"))
        issues.extend(_check_script_lifecycle(manifest, name, effective))

        write_targets = set(effective.get("write_targets") or [])
        authority_scope = str(effective.get("authority_scope", ""))
        is_diagnostic_scope = authority_scope.startswith(
            "diagnostic_non_promotion"
        ) or authority_scope.startswith("report_artifact_non_promotion")
        if is_diagnostic_scope:
            forbidden_writes = sorted(
                target for target in write_targets if not _write_target_allowed(target, diagnostic_allowed)
            )
            if forbidden_writes:
                issues.append(
                    _issue(
                        "script_diagnostic_forbidden_write_target",
                        rel,
                        f"diagnostic writes forbidden targets {forbidden_writes}",
                    )
                )
            text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            if any(helper in text for helper in CANONICAL_WRITE_HELPERS):
                issues.append(
                    _issue(
                        "script_diagnostic_imports_canonical_write_helper",
                        rel,
                        "diagnostic script references canonical write helper",
                    )
                )
            if SQL_MUTATION_PATTERN.search(text) and write_targets - {"state/zeus_backtest.db", "stdout", "temp"}:
                issues.append(
                    _issue(
                        "script_diagnostic_mutates_canonical_surface",
                        rel,
                        "diagnostic script contains SQL mutation outside diagnostic targets",
                    )
                )
            if FILE_WRITE_PATTERN.search(text) and not (write_targets - {"stdout"}):
                issues.append(
                    _issue(
                        "script_diagnostic_untracked_file_write",
                        rel,
                        "diagnostic script appears to write files but manifest declares stdout only",
                    )
                )

        if effective.get("dangerous_if_run"):
            fail_closed = effective.get("status") == "deprecated" and effective.get("fail_closed")
            if not fail_closed:
                apply_flag = effective.get("apply_flag")
                text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
                if not apply_flag:
                    issues.append(_issue("script_dangerous_missing_apply_flag", rel, "dangerous script needs explicit apply/no-dry-run flag"))
                elif apply_flag == "implicit":
                    if not effective.get("unguarded_write_rationale"):
                        issues.append(
                            _issue(
                                "script_dangerous_implicit_apply_without_rationale",
                                rel,
                                "implicit write must carry unguarded_write_rationale",
                            )
                        )
                elif apply_flag != "explicit_import_only" and str(apply_flag) not in text:
                    issues.append(
                        _issue(
                            "script_dangerous_apply_flag_not_in_source",
                            rel,
                            f"declared apply flag {apply_flag!r} not found in source",
                        )
                    )
                if not effective.get("target_db"):
                    issues.append(_issue("script_dangerous_missing_target_db", rel, "dangerous script needs explicit target DB metadata"))

        if write_targets & canonical_targets and is_diagnostic_scope:
            issues.append(
                _issue(
                    "script_diagnostic_writes_canonical_db",
                    rel,
                    f"diagnostic write targets canonical DB {sorted(write_targets & canonical_targets)}",
                )
            )

    return StrictResult(ok=not issues, issues=issues)


def run_data_rebuild() -> StrictResult:
    topology = load_data_rebuild_topology()
    issues: list[TopologyIssue] = []
    criteria = topology.get("criteria") or {}
    required = set((topology.get("live_math_certification") or {}).get("required_before_allowed") or [])
    actual = set(criteria)

    for criterion in sorted(required - actual):
        issues.append(_issue("data_rebuild_criterion_missing", criterion, "required certification criterion missing"))
    for name, criterion in criteria.items():
        if not criterion.get("status"):
            issues.append(_issue("data_rebuild_criterion_incomplete", name, "missing status"))
        if not criterion.get("source"):
            issues.append(_issue("data_rebuild_criterion_incomplete", name, "missing source"))
        if not criterion.get("certification_gate"):
            issues.append(_issue("data_rebuild_criterion_incomplete", name, "missing certification_gate"))
        if "blocks_live_math_certification" not in criterion:
            issues.append(_issue("data_rebuild_criterion_incomplete", name, "missing blocks_live_math_certification"))
        for path in criterion.get("protects", []):
            if not (ROOT / path).exists():
                issues.append(_issue("data_rebuild_protects_missing", path, f"{name} protects missing path"))
        for path in criterion.get("required_tests", []):
            if not (ROOT / path).exists():
                issues.append(_issue("data_rebuild_required_test_missing", path, f"{name} references missing test"))

    certification = topology.get("live_math_certification") or {}
    if "allowed" not in certification:
        issues.append(_issue("data_rebuild_certification_allowed_missing", "live_math_certification", "allowed key is required"))
    elif not isinstance(certification.get("allowed"), bool):
        issues.append(_issue("data_rebuild_certification_allowed_invalid", "live_math_certification", "allowed must be boolean"))
    elif certification.get("allowed") is not False:
        issues.append(_issue("data_rebuild_certification_allowed_unsafe", "live_math_certification", "Packet 8 topology must not allow live math certification"))

    for criterion in sorted(required):
        if not (criteria.get(criterion) or {}).get("blocks_live_math_certification"):
            issues.append(
                _issue(
                    "data_rebuild_required_criterion_not_blocking",
                    criterion,
                    "required_before_allowed criterion must block live math certification",
                )
            )

    if certification.get("allowed") is True:
        blockers = [
            name
            for name, criterion in criteria.items()
            if criterion.get("blocks_live_math_certification")
            and criterion.get("status") != "certified"
        ]
        if blockers:
            issues.append(
                _issue(
                    "data_rebuild_live_math_certification_unsafe",
                    "live_math_certification",
                    f"cannot allow live math certification while blockers remain: {sorted(blockers)}",
                )
            )

    row_contract = topology.get("rebuilt_row_contract", {}).get("tables") or {}
    for table, spec in row_contract.items():
        if not spec.get("authority_required"):
            issues.append(_issue("data_rebuild_row_contract_missing_authority", table, "authority label required"))
        if not spec.get("provenance_required"):
            issues.append(_issue("data_rebuild_row_contract_missing_provenance", table, "provenance required"))
        if not spec.get("required_fields"):
            issues.append(_issue("data_rebuild_row_contract_missing_fields", table, "required_fields must be non-empty"))
        producer = spec.get("producer_contract") or spec.get("producer_script")
        if not producer:
            issues.append(_issue("data_rebuild_row_contract_missing_producer", table, "producer_contract or producer_script required"))
        elif not (ROOT / str(producer)).exists():
            issues.append(_issue("data_rebuild_row_contract_missing_producer", str(producer), f"producer for {table} missing"))

    replay_rule = topology.get("replay_coverage_rule") or {}
    if replay_rule.get("wu_settlement_sample_is_strategy_coverage") is not False:
        issues.append(
            _issue(
                "data_rebuild_replay_coverage_unsafe",
                "replay_coverage_rule",
                "WU settlement sample must not be treated as strategy replay coverage",
            )
        )
    for item in (
        "wu_settlement_value",
        "point_in_time_forecast_reference",
        "vector_compatible_p_raw_json",
        "parseable_typed_bin_labels",
        "market_price_linkage",
    ):
        if item not in (replay_rule.get("required_for_strategy_replay_coverage") or []):
            issues.append(_issue("data_rebuild_replay_coverage_incomplete", item, "required replay coverage prerequisite missing"))
    if "N/A" not in str(replay_rule.get("p_and_l_without_market_price", "")):
        issues.append(_issue("data_rebuild_replay_pnl_unsafe", "p_and_l_without_market_price", "missing N/A rule"))

    diagnostic = topology.get("diagnostic_non_promotion") or {}
    if diagnostic.get("authority_scope") != "diagnostic_non_promotion":
        issues.append(_issue("data_rebuild_non_promotion_missing", "diagnostic_non_promotion", "authority_scope must be diagnostic_non_promotion"))
    for target in (
        "state/zeus_trades.db",
        "state/zeus-world.db",
        "live strategy thresholds",
        "calibration model activation",
    ):
        if target not in (diagnostic.get("forbidden_promotions") or []):
            issues.append(_issue("data_rebuild_non_promotion_incomplete", target, "canonical target missing from forbidden promotions"))

    return StrictResult(ok=not issues, issues=issues)


def _has_antibody(antibodies: Any) -> bool:
    if not isinstance(antibodies, dict):
        return False
    return any(bool(antibodies.get(key)) for key in ("code", "tests", "gates", "docs"))


def _history_lore_path_exists(value: str) -> bool:
    if not value or " " in value:
        return True
    if any(char in value for char in "*?[]"):
        return any(ROOT.glob(value))
    if "/" not in value and "." not in value:
        return True
    return (ROOT / value).exists()


def _gate_path_tokens(gate: str) -> list[str]:
    tokens = []
    for token in re.split(r"\s+", gate):
        token = token.strip("'\"")
        if token.startswith(("./", "../")):
            token = token.removeprefix("./")
        if token.startswith(("src/", "scripts/", "tests/", "architecture/", "docs/", "config/")):
            tokens.append(token)
    return tokens


def _check_history_lore_antibody_references(
    card_id: str,
    antibodies: Any,
) -> list[TopologyIssue]:
    if not isinstance(antibodies, dict):
        return []
    issues: list[TopologyIssue] = []
    path = f"architecture/history_lore.yaml:{card_id}"
    for field in ("code", "tests", "docs"):
        for ref in antibodies.get(field) or []:
            ref = str(ref)
            if not _history_lore_path_exists(ref):
                issues.append(
                    _issue(
                        "history_lore_stale_antibody_reference",
                        path,
                        f"antibodies.{field} references missing path: {ref}",
                    )
                )
    for gate in antibodies.get("gates") or []:
        for ref in _gate_path_tokens(str(gate)):
            if not _history_lore_path_exists(ref):
                issues.append(
                    _issue(
                        "history_lore_stale_antibody_reference",
                        path,
                        f"antibodies.gates references missing path: {ref}",
                    )
                )
    return issues


def run_history_lore() -> StrictResult:
    lore = load_history_lore()
    issues: list[TopologyIssue] = []
    required = lore.get("required_card_fields") or []
    allowed_statuses = set(lore.get("allowed_statuses") or [])
    allowed_severities = set(lore.get("allowed_severities") or [])
    cards = lore.get("cards") or []
    seen: set[str] = set()

    if not cards:
        issues.append(_issue("history_lore_empty", "architecture/history_lore.yaml", "no lore cards declared"))

    for idx, card in enumerate(cards):
        card_id = str(card.get("id") or f"card[{idx}]")
        path = f"architecture/history_lore.yaml:{card_id}"
        if card_id in seen:
            issues.append(_issue("history_lore_duplicate_id", path, "duplicate lore id"))
        seen.add(card_id)

        for field in required:
            if _metadata_missing(card.get(field)):
                issues.append(_issue("history_lore_required_field_missing", path, f"missing {field}"))

        status = card.get("status")
        severity = card.get("severity")
        if status not in allowed_statuses:
            issues.append(_issue("history_lore_invalid_status", path, f"invalid status {status!r}"))
        if severity not in allowed_severities:
            issues.append(_issue("history_lore_invalid_severity", path, f"invalid severity {severity!r}"))

        routing = card.get("routing") or {}
        if _metadata_missing(routing.get("task_terms")) and _metadata_missing(routing.get("file_patterns")):
            issues.append(_issue("history_lore_missing_routing", path, "task_terms or file_patterns required"))

        if severity in {"critical", "high"} and status in {"active_law", "mitigated"}:
            if not _has_antibody(card.get("antibodies")):
                issues.append(
                    _issue(
                        "history_lore_missing_antibody",
                        path,
                        "critical/high active lore needs code, test, gate, or doc antibody",
                    )
                )
            issues.extend(_check_history_lore_antibody_references(card_id, card.get("antibodies")))

        if status == "open_gap" and _metadata_missing(card.get("residual_risk")):
            issues.append(_issue("history_lore_open_gap_without_residual", path, "open gaps need residual risk"))

        digest = str(card.get("zero_context_digest") or "")
        if len(digest) < 40:
            issues.append(
                _issue(
                    "history_lore_digest_too_thin",
                    path,
                    "zero_context_digest must be dense enough for routing",
                )
            )

    return StrictResult(ok=not issues, issues=issues)


def _budget_issue(
    code: str,
    path: str,
    message: str,
    *,
    enforcement: str,
) -> TopologyIssue:
    if enforcement == "blocking":
        return _issue(code, path, message)
    return _warning(code, path, message)


def _budget_has_blocking_promotion(spec: dict[str, Any]) -> bool:
    return bool(spec.get("promotion_packet") or spec.get("blocking_authority"))


def _budget_enforcement_issues(
    spec: dict[str, Any],
    path: str,
) -> list[TopologyIssue]:
    if str(spec.get("enforcement") or "advisory") == "blocking" and not _budget_has_blocking_promotion(spec):
        return [
            _issue(
                "context_budget_blocking_without_promotion",
                path,
                "enforcement=blocking requires promotion_packet or blocking_authority",
            )
        ]
    return []


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())


def run_context_budget() -> StrictResult:
    if not CONTEXT_BUDGET_PATH.exists():
        issue = _issue(
            "context_budget_manifest_missing",
            "architecture/context_budget.yaml",
            "context budget manifest is missing",
        )
        return StrictResult(ok=False, issues=[issue])

    budget = load_context_budget()
    issues: list[TopologyIssue] = []

    for entry in budget.get("file_budgets") or []:
        rel = str(entry.get("path") or "")
        enforcement = str(entry.get("enforcement") or "advisory")
        max_lines = entry.get("max_lines")
        path = ROOT / rel
        issues.extend(_budget_enforcement_issues(entry, rel or "architecture/context_budget.yaml"))
        if not rel:
            issues.append(
                _budget_issue(
                    "context_budget_invalid_value",
                    "architecture/context_budget.yaml",
                    "file budget entry missing path",
                    enforcement=enforcement,
                )
            )
            continue
        if not path.exists():
            issues.append(
                _budget_issue(
                    "context_budget_file_missing",
                    rel,
                    "budgeted file is missing",
                    enforcement=enforcement,
                )
            )
            continue
        if not isinstance(max_lines, int) or max_lines <= 0:
            issues.append(
                _budget_issue(
                    "context_budget_invalid_value",
                    rel,
                    f"invalid max_lines {max_lines!r}",
                    enforcement=enforcement,
                )
            )
            continue
        actual = _line_count(path)
        if actual > max_lines:
            issues.append(
                _budget_issue(
                    "context_budget_file_over",
                    rel,
                    f"{actual} lines exceeds budget {max_lines} ({entry.get('role', 'unspecified role')})",
                    enforcement=enforcement,
                )
            )

    lore_budget = ((budget.get("digest_budgets") or {}).get("history_lore") or {})
    lore_enforcement = str(lore_budget.get("enforcement") or "advisory")
    issues.extend(_budget_enforcement_issues(lore_budget, "digest_budgets.history_lore"))
    max_cards = lore_budget.get("max_cards_per_digest")
    max_digest_chars = lore_budget.get("max_zero_context_digest_chars")
    if max_cards is not None and (not isinstance(max_cards, int) or max_cards <= 0):
        issues.append(
            _budget_issue(
                "context_budget_invalid_value",
                "digest_budgets.history_lore.max_cards_per_digest",
                f"invalid max_cards_per_digest {max_cards!r}",
                enforcement=lore_enforcement,
            )
        )
    if max_digest_chars is not None and (not isinstance(max_digest_chars, int) or max_digest_chars <= 0):
        issues.append(
            _budget_issue(
                "context_budget_invalid_value",
                "digest_budgets.history_lore.max_zero_context_digest_chars",
                f"invalid max_zero_context_digest_chars {max_digest_chars!r}",
                enforcement=lore_enforcement,
            )
        )

    if isinstance(max_cards, int) and max_cards > 0:
        for task in lore_budget.get("sample_tasks") or []:
            digest = build_digest(str(task))
            card_count = len(digest.get("history_lore") or [])
            if card_count > max_cards:
                issues.append(
                    _budget_issue(
                        "context_budget_digest_card_over",
                        f"digest:{task}",
                        f"{card_count} lore cards exceeds budget {max_cards}",
                        enforcement=lore_enforcement,
                    )
                )

    if isinstance(max_digest_chars, int) and max_digest_chars > 0:
        for card in (load_history_lore().get("cards") or []):
            digest_text = str(card.get("zero_context_digest") or "")
            if len(digest_text) > max_digest_chars:
                issues.append(
                    _budget_issue(
                        "context_budget_digest_text_over",
                        f"architecture/history_lore.yaml:{card.get('id', '<missing-id>')}",
                        f"zero_context_digest has {len(digest_text)} chars, budget {max_digest_chars}",
                        enforcement=lore_enforcement,
                    )
                )

    read_path = budget.get("default_read_path") or {}
    max_files = read_path.get("max_pre_code_reads")
    if max_files is not None and (not isinstance(max_files, int) or max_files <= 0):
        issues.append(
            _warning(
                "context_budget_invalid_value",
                "default_read_path.max_pre_code_reads",
                f"invalid max_pre_code_reads {max_files!r}",
            )
        )
    for route, spec in (read_path.get("route_budgets") or {}).items():
        soft_limit = (spec or {}).get("soft_limit")
        expected_reads = (spec or {}).get("expected_reads") or []
        issues.extend(
            _budget_enforcement_issues(
                spec or {},
                f"default_read_path.route_budgets.{route}",
            )
        )
        if not isinstance(soft_limit, int) or soft_limit <= 0:
            issues.append(
                _warning(
                    "context_budget_invalid_value",
                    f"default_read_path.route_budgets.{route}.soft_limit",
                    f"invalid soft_limit {soft_limit!r}",
                )
            )
            continue
        if len(expected_reads) > soft_limit:
            issues.append(
                _warning(
                    "context_budget_route_over",
                    f"default_read_path.route_budgets.{route}",
                    f"{len(expected_reads)} expected reads exceeds soft limit {soft_limit}",
                )
            )

    blocking = [issue for issue in issues if issue.severity == "error"]
    return StrictResult(ok=not blocking, issues=issues)


def _zone_for_changed_file(path: str) -> str:
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
    rationale = load_source_rationale()
    files = rationale.get("files") or {}
    if path in files and files[path].get("zone"):
        return str(files[path]["zone"])
    for prefix, spec in (rationale.get("package_defaults") or {}).items():
        if path.startswith(f"{prefix}/") and spec.get("zone"):
            return str(spec["zone"])
    return "unknown"


def _planning_lock_trigger(path: str) -> str | None:
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


def _valid_plan_evidence(path: str | None) -> bool:
    if not path:
        return False
    evidence = ROOT / path
    if not evidence.exists() or not evidence.is_file():
        return False
    normalized = evidence.relative_to(ROOT).as_posix() if evidence.is_relative_to(ROOT) else str(evidence)
    return (
        normalized.startswith("docs/operations/")
        or normalized.startswith(".omx/plans/")
        or normalized.startswith(".omx/context/")
    )


def run_planning_lock(changed_files: list[str], plan_evidence: str | None = None) -> StrictResult:
    issues: list[TopologyIssue] = []
    triggers: dict[str, str] = {}
    for path in changed_files:
        reason = _planning_lock_trigger(path)
        if reason:
            triggers[path] = reason

    zones = {_zone_for_changed_file(path) for path in changed_files}
    zones.discard("unknown")
    zones.discard("docs")
    if len(changed_files) > 4:
        triggers["<change-set>"] = "more than 4 changed files"
    if len(zones) > 1:
        triggers["<change-set>"] = f"cross-zone edit {sorted(zones)}"

    if triggers and not _valid_plan_evidence(plan_evidence):
        for path, reason in sorted(triggers.items()):
            issues.append(
                _issue(
                    "planning_lock_required",
                    path,
                    f"planning lock triggered by {reason}; pass --plan-evidence with a valid plan/current-state file",
                )
            )
        if plan_evidence:
            issues.append(
                _issue(
                    "planning_lock_evidence_invalid",
                    plan_evidence,
                    "plan evidence path does not exist or is not in an approved planning/evidence directory",
                )
            )
    return StrictResult(ok=not issues, issues=issues)


def _idiom_pattern_for(idiom_id: str) -> re.Pattern[str] | None:
    if idiom_id == "SEMANTIC_PROVENANCE_GUARD":
        return SEMANTIC_PROVENANCE_GUARD_PATTERN
    return None


def run_idioms() -> StrictResult:
    if not CODE_IDIOMS_PATH.exists():
        return StrictResult(
            ok=False,
            issues=[
                _issue(
                    "code_idiom_manifest_missing",
                    "architecture/code_idioms.yaml",
                    "code idiom manifest is missing",
                )
            ],
        )
    manifest = load_code_idioms()
    issues: list[TopologyIssue] = []
    required = manifest.get("required_idiom_fields") or []
    idioms = manifest.get("idioms") or []
    seen: set[str] = set()
    registered_examples: set[str] = set()

    for idx, idiom in enumerate(idioms):
        idiom_id = str(idiom.get("id") or f"idiom[{idx}]")
        path = f"architecture/code_idioms.yaml:{idiom_id}"
        if idiom_id in seen:
            issues.append(_issue("code_idiom_duplicate_id", path, "duplicate code idiom id"))
        seen.add(idiom_id)
        for field in required:
            if _metadata_missing(idiom.get(field)):
                issues.append(_issue("code_idiom_required_field_missing", path, f"missing {field}"))
        owner_gate = idiom.get("owner_gate")
        if owner_gate and not (ROOT / str(owner_gate)).exists():
            issues.append(_issue("code_idiom_owner_gate_missing", str(owner_gate), f"owner gate for {idiom_id} missing"))
        pattern = _idiom_pattern_for(idiom_id)
        for example in idiom.get("examples") or []:
            example_path = ROOT / str(example)
            registered_examples.add(str(example))
            if not example_path.exists():
                issues.append(_issue("code_idiom_example_missing", str(example), f"example for {idiom_id} missing"))
                continue
            if pattern and not pattern.search(example_path.read_text(encoding="utf-8", errors="ignore")):
                issues.append(_issue("code_idiom_example_without_pattern", str(example), f"example does not contain {idiom_id} pattern"))

    semantic_files = set()
    for path in sorted((ROOT / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if SEMANTIC_PROVENANCE_GUARD_PATTERN.search(text):
            semantic_files.add(path.relative_to(ROOT).as_posix())
    for rel in sorted(semantic_files - registered_examples):
        issues.append(
            _issue(
                "code_idiom_unregistered_occurrence",
                rel,
                "Semantic Provenance Guard occurrence is not registered in architecture/code_idioms.yaml",
            )
        )

    return StrictResult(ok=not issues, issues=issues)


def run_self_check_coherence() -> StrictResult:
    issues: list[TopologyIssue] = []
    root_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    self_check = (ROOT / "architecture" / "self_check" / "zero_context_entry.md").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    self_agents = (ROOT / "architecture" / "self_check" / "AGENTS.md").read_text(
        encoding="utf-8",
        errors="ignore",
    )

    required_root_refs = [
        "architecture/self_check/authority_index.md",
        "architecture/self_check/zero_context_entry.md",
        "Default Navigation",
        "What To Read By Task",
    ]
    for needle in required_root_refs:
        if needle not in root_text:
            issues.append(
                _issue(
                    "self_check_root_reference_missing",
                    "AGENTS.md",
                    f"missing {needle}",
                )
            )

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
            issues.append(
                _issue(
                    "self_check_required_read_missing",
                    "architecture/self_check/zero_context_entry.md",
                    f"missing {needle}",
                )
            )

    if "§9" in self_agents or "What to read next" in self_agents:
        issues.append(
            _issue(
                "self_check_stale_cross_reference",
                "architecture/self_check/AGENTS.md",
                "stale root AGENTS section reference",
            )
        )
    if "Default Navigation" not in self_agents or "What To Read By Task" not in self_agents:
        issues.append(
            _issue(
                "self_check_stale_cross_reference",
                "architecture/self_check/AGENTS.md",
                "missing current root section names",
            )
        )

    return StrictResult(ok=not issues, issues=issues)


def run_runtime_modes() -> StrictResult:
    topology = load_runtime_modes()
    issues: list[TopologyIssue] = []
    source = topology.get("source")
    if not source or not (ROOT / str(source)).exists():
        issues.append(_issue("runtime_mode_source_missing", str(source or ""), "runtime mode source missing"))
        return StrictResult(ok=False, issues=issues)

    text = (ROOT / str(source)).read_text(encoding="utf-8", errors="ignore")
    required_modes = topology.get("required_modes") or {}
    for value, spec in required_modes.items():
        enum = spec.get("enum")
        if not enum:
            issues.append(_issue("runtime_mode_required_field_missing", value, "missing enum"))
        if not spec.get("purpose"):
            issues.append(_issue("runtime_mode_required_field_missing", value, "missing purpose"))
        if not spec.get("timing_rule"):
            issues.append(_issue("runtime_mode_required_field_missing", value, "missing timing_rule"))
        if enum and str(enum) not in text:
            issues.append(_issue("runtime_mode_enum_missing", str(source), f"enum {enum} missing"))
        if value not in text:
            issues.append(_issue("runtime_mode_value_missing", str(source), f"value {value} missing"))

    for rel in topology.get("shared_runtime_path") or []:
        if not (ROOT / str(rel)).exists():
            issues.append(_issue("runtime_mode_path_missing", str(rel), "shared runtime path missing"))
    for rel in topology.get("required_tests") or []:
        if not (ROOT / str(rel)).exists():
            issues.append(_issue("runtime_mode_test_missing", str(rel), "runtime mode test missing"))

    root_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    for value in required_modes:
        if value not in root_text:
            issues.append(_issue("runtime_mode_root_reference_missing", "AGENTS.md", f"root AGENTS missing {value}"))
    if "architecture/runtime_modes.yaml" not in root_text:
        issues.append(_issue("runtime_mode_root_reference_missing", "AGENTS.md", "root AGENTS missing runtime mode manifest"))

    return StrictResult(ok=not issues, issues=issues)



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


def _change_kind(path: str, tracked: set[str]) -> str:
    exists_now = (ROOT / path).exists()
    if exists_now and path not in tracked:
        return "added"
    if not exists_now and path in tracked:
        return "deleted"
    return "modified"


def _git_status_changes() -> dict[str, str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    entries = [entry for entry in proc.stdout.split("\0") if entry]
    changes: dict[str, str] = {}
    index = 0
    while index < len(entries):
        entry = entries[index]
        status = entry[:2]
        path = entry[3:]
        if not path:
            index += 1
            continue

        if "R" in status or "C" in status:
            old_path = entries[index + 1] if index + 1 < len(entries) else ""
            if old_path:
                changes[old_path] = "deleted"
            changes[path] = "added"
            index += 2
            continue

        if status == "??" or "A" in status:
            kind = "added"
        elif "D" in status:
            kind = "deleted"
        else:
            kind = "modified"
        changes[path] = kind
        index += 1
    return changes


def _map_maintenance_changes(changed_files: list[str]) -> dict[str, str]:
    if not changed_files:
        return _git_status_changes()
    git_changes = _git_status_changes()
    tracked = set(_git_ls_files())
    return {path: git_changes.get(path, _change_kind(path, tracked)) for path in changed_files}


def run_map_maintenance(changed_files: list[str] | None = None, mode: str = "advisory") -> StrictResult:
    if not MAP_MAINTENANCE_PATH.exists():
        return StrictResult(
            ok=False,
            issues=[
                _issue(
                    "map_maintenance_manifest_missing",
                    "architecture/map_maintenance.yaml",
                    "map maintenance manifest is missing",
                )
            ],
        )
    manifest = load_map_maintenance()
    issues: list[TopologyIssue] = []
    mode_spec = (manifest.get("modes") or {}).get(mode)
    if not mode_spec:
        return StrictResult(
            ok=False,
            issues=[
                _issue(
                    "map_maintenance_invalid_mode",
                    "architecture/map_maintenance.yaml",
                    f"unknown mode {mode!r}",
                )
            ],
        )
    issue_fn = _issue if mode_spec.get("blocking") else _warning
    try:
        changes = _map_maintenance_changes(changed_files or [])
    except subprocess.CalledProcessError as exc:
        return StrictResult(
            ok=False,
            issues=[
                _issue(
                    "map_maintenance_git_status_failed",
                    "<git-status>",
                    f"could not read git status: {exc}",
                )
            ],
        )

    for rule in manifest.get("rules") or []:
        if not rule.get("path_globs"):
            issues.append(_issue("map_maintenance_missing_path_glob", str(rule.get("id", "<unknown>")), "rule missing path_globs"))
        if not rule.get("required_companions"):
            issues.append(_issue("map_maintenance_missing_companion", str(rule.get("id", "<unknown>")), "rule missing required_companions"))

    changed_set = set(changes)
    for path, kind in changes.items():
        for rule in manifest.get("rules") or []:
            if kind not in (rule.get("on_change") or []):
                continue
            if not any(fnmatch(path, pattern) for pattern in rule.get("path_globs") or []):
                continue
            for companion in rule.get("required_companions") or []:
                if companion not in changed_set:
                    issues.append(
                        issue_fn(
                            "map_maintenance_companion_missing",
                            path,
                            f"{kind} file requires companion update {companion} ({rule.get('id')})",
                        )
                    )
    blocking = [issue for issue in issues if issue.severity == "error"]
    return StrictResult(ok=not blocking, issues=issues)


def _source_rationale_for(files: list[str]) -> list[dict[str, Any]]:
    if not files:
        return []
    source_map = load_source_rationale()
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


CONTEXT_EXPAND_TRIGGERS = [
    "touched code crosses a zone boundary",
    "tests fail outside the suggested scope",
    "implementation needs files not listed in files_may_change",
    "truth owner, lifecycle, DB, control, risk, or settlement owner is unclear",
    "reviewer asks about authority or downstream behavior",
    "target uses a write route not listed in the output",
]


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


def _normalize_scope(scope: str | None) -> str:
    if not scope:
        return ""
    return scope.strip().strip("/")


def _scope_is_file(scope: str) -> bool:
    if not scope:
        return False
    path = ROOT / scope
    return path.is_file() or Path(scope).suffix != ""


def _scope_agent_path(scope: str) -> str | None:
    if not scope:
        return None
    path = ROOT / scope
    if path.is_file():
        path = path.parent
    while path != ROOT and ROOT in path.parents:
        agents = path / "AGENTS.md"
        if agents.exists():
            return agents.relative_to(ROOT).as_posix()
        path = path.parent
    return None


def _zones_for_scope(scope: str) -> list[str]:
    normalized = _normalize_scope(scope)
    if not normalized:
        return []
    zones = load_topology()
    zone_manifest = _load_yaml(ROOT / "architecture" / "zones.yaml")
    touched: set[str] = set()
    for zone, spec in (zone_manifest.get("zones") or {}).items():
        for directory in spec.get("directories") or []:
            directory = str(directory).strip("/")
            if normalized == directory or normalized.startswith(f"{directory}/") or directory.startswith(f"{normalized}/"):
                touched.add(str(zone))
    if touched:
        return sorted(touched)

    rationale = load_source_rationale()
    for prefix, spec in (rationale.get("package_defaults") or {}).items():
        if normalized == prefix or normalized.startswith(f"{prefix}/") or prefix.startswith(f"{normalized}/"):
            zone = spec.get("zone")
            if zone:
                touched.add(str(zone))
    return sorted(touched)


def _zones_for_files(files: list[str]) -> list[str]:
    zones = {_zone_for_changed_file(file) for file in files}
    zones.discard("unknown")
    zones.discard("docs")
    return sorted(zones)


def _packet_zones(scope: str, files: list[str]) -> list[str]:
    zones = set(_zones_for_scope(scope))
    zones.update(_zones_for_files(files))
    return sorted(zones)


def _invariants_for_zones(zones: list[str]) -> list[dict[str, Any]]:
    wanted = set(zones)
    if not wanted:
        return []
    invariants = load_invariants().get("invariants") or []
    return [
        invariant
        for invariant in invariants
        if wanted & set(invariant.get("zones") or [])
    ]


def build_invariants_slice(zone: str | None = None) -> dict[str, Any]:
    invariants = load_invariants().get("invariants") or []
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


def _files_may_not_change(zones_touched: list[str]) -> list[str]:
    zone_manifest = _load_yaml(ROOT / "architecture" / "zones.yaml")
    forbidden: list[str] = []
    for zone, spec in (zone_manifest.get("zones") or {}).items():
        if zone in zones_touched:
            continue
        forbidden.extend(str(item) for item in spec.get("directories") or [])
    return sorted(dict.fromkeys(forbidden))


def _package_gates(scope: str, files: list[str]) -> list[str]:
    rationale = load_source_rationale()
    defaults = rationale.get("package_defaults") or {}
    candidates = [path for path in files]
    if scope:
        candidates.append(_normalize_scope(scope))
    gates: list[str] = []
    for target in candidates:
        for prefix, spec in defaults.items():
            if target == prefix or target.startswith(f"{prefix}/") or prefix.startswith(f"{target}/"):
                gates.extend(spec.get("gates") or [])
    for item in _source_rationale_for(files):
        gates.extend(item.get("gates") or [])
    return sorted(dict.fromkeys(gates))


def _tests_for_packet(scope: str, files: list[str]) -> list[str]:
    tests = []
    for gate in _package_gates(scope, files):
        tests.extend(re.findall(r"tests/[A-Za-z0-9_./-]+\.py", gate))
    if not files:
        return sorted(dict.fromkeys(tests))
    test_topology = load_test_topology()
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
    *,
    packet_type: str,
    task: str,
    scope: str = "",
    files: list[str] | None = None,
) -> dict[str, Any]:
    files = files or []
    normalized_scope = _normalize_scope(scope)
    zones_touched = _packet_zones(normalized_scope, files)
    invariants = _invariants_for_zones(zones_touched)
    scoped_agents = _scope_agent_path(normalized_scope) if normalized_scope else None
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

    source_entries = _source_rationale_for(files)
    if files:
        files_may_change = files
    elif normalized_scope and _scope_is_file(normalized_scope):
        files_may_change = [normalized_scope]
    elif normalized_scope:
        files_may_change = [f"{normalized_scope}/**"]
    else:
        files_may_change = []
    ci_gates = _package_gates(normalized_scope, files)
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
        "files_may_not_change": _files_may_not_change(zones_touched),
        "schema_changes": any(zone == "K0_frozen_kernel" for zone in zones_touched),
        "ci_gates_required": sorted(dict.fromkeys(ci_gates)),
        "tests_required": _tests_for_packet(normalized_scope, files),
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


def build_package_review_context_pack(task: str, files: list[str]) -> dict[str, Any]:
    return _context_pack_checks().build_package_review_context_pack(sys.modules[__name__], task, files)


def build_context_pack(pack_type: str, *, task: str, files: list[str]) -> dict[str, Any]:
    return _context_pack_checks().build_context_pack(sys.modules[__name__], pack_type, task=task, files=files)


def _core_map_profiles() -> dict[str, dict[str, Any]]:
    return {
        str(profile.get("id")): profile
        for profile in load_topology().get("core_map_profiles") or []
        if profile.get("id")
    }


def _source_entry_by_path() -> dict[str, dict[str, Any]]:
    rationale = load_source_rationale()
    entries: dict[str, dict[str, Any]] = {}
    for item in _source_rationale_for(list((rationale.get("files") or {}).keys())):
        entries[item["path"]] = item
    return entries


def _validate_proof_target_exists(target: dict[str, Any]) -> bool:
    target_path = str(target.get("path") or "")
    if not target_path:
        return False
    return (ROOT / target_path).exists()


def _locator_exists(path: str, locator: str | None) -> bool:
    if not locator:
        return False
    target = ROOT / path
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


def _proof_contains(path: str, needle: str) -> bool:
    if not needle:
        return False
    return needle in (ROOT / path).read_text(encoding="utf-8", errors="ignore")


def _edge_proof_status(
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
    if not path or not (ROOT / path).exists():
        return ("invalid", f"proof path missing: {path}")
    if kind == "import_or_call":
        module = str(proof.get("module") or "")
        symbol = str(proof.get("symbol") or proof.get("contains") or "")
        if path != node_files.get(to_id):
            return ("invalid", f"import_or_call proof path must equal target node file {node_files.get(to_id)}")
        if not module or not symbol:
            return ("invalid", "import_or_call proof requires module and symbol")
        text = (ROOT / path).read_text(encoding="utf-8", errors="ignore")
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
        if not _proof_contains(path, needle):
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


CORE_MAP_FORBIDDEN_PATTERNS = (
    re.compile(r"round\s*\(\s*(?:value|x)\s*\+\s*0\.5\s*\)", re.IGNORECASE),
    re.compile(r"\bpython\s*(?:/numpy\s*)?(?:built-?in\s*)?round\b", re.IGNORECASE),
    re.compile(r"\bnumpy\s+(?:round|around)\b", re.IGNORECASE),
    re.compile(r"(?<!not )\bbanker(?:'s)?\s+round", re.IGNORECASE),
)


def _core_map_forbidden_hits(payload: dict[str, Any], phrases: list[Any]) -> list[str]:
    serialized = json.dumps(payload)
    hits = [str(phrase) for phrase in phrases if str(phrase) in serialized]
    normalized = " ".join(serialized.split())
    hits.extend(pattern.pattern for pattern in CORE_MAP_FORBIDDEN_PATTERNS if pattern.search(normalized))
    return sorted(dict.fromkeys(hits))


def build_core_map(profile_id: str) -> dict[str, Any]:
    profiles = _core_map_profiles()
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

    source_entries = _source_entry_by_path()
    node_ids = {str(node.get("id") or "") for node in node_specs}
    node_files = {
        str(node.get("id") or ""): str(node.get("file") or "")
        for node in node_specs
    }
    claim_index = _claim_proof_index()
    nodes: list[dict[str, Any]] = []
    invalid: list[str] = []

    for node in node_specs:
        node_id = str(node.get("id") or "")
        node_file = str(node.get("file") or "")
        source_entry = source_entries.get(node_file)
        if not node_id or not node_file or not (ROOT / node_file).exists():
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
                if not _validate_proof_target_exists(target)
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
        confidence, reason = _edge_proof_status(
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
        "context_assumption": build_context_assumption(
            profile=profile_id,
            profile_kind="core_map_profile",
            source_entries=[
                {"path": node["file"], "upstream": [], "downstream": []}
                for node in nodes
            ],
            confidence_basis=["topology_manifest", "source_rationale"],
        ),
    }
    for hit in _core_map_forbidden_hits(payload, profile.get("forbidden_phrases") or []):
        invalid.append(f"forbidden phrase emitted: {hit}")
    return payload


def run_core_maps() -> StrictResult:
    issues: list[TopologyIssue] = []
    profiles = _core_map_profiles()
    for profile_id in sorted(profiles):
        profile = profiles[profile_id]
        for node in profile.get("nodes") or []:
            node_file = str(node.get("file") or "")
            if node_file.startswith("docs/reference/"):
                issues.append(
                    _issue(
                        "core_map_reference_authority_leak",
                        profile_id,
                        f"reference doc cannot be a core-map authority node: {node_file}",
                    )
                )
        try:
            payload = build_core_map(profile_id)
        except ValueError as exc:
            issues.append(_issue("core_map_profile_invalid", profile_id, str(exc)))
            continue
        for item in payload.get("invalid") or []:
            issues.append(_issue("core_map_profile_invalid", profile_id, str(item)))
    return StrictResult(ok=not issues, issues=issues)



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


def run_navigation(task: str, files: list[str] | None = None) -> dict[str, Any]:
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
    digest = build_digest(task, files or [])
    issues = [
        {"lane": lane, **asdict(issue)}
        for lane, result in checks.items()
        for issue in result.issues
    ]
    blocking = [issue for issue in issues if issue.get("severity") == "error"]
    return {
        "ok": not blocking,
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
        "excluded_lanes": {
            "strict": "strict includes transient root/state artifact classification; run explicitly when workspace is quiescent",
            "scripts": "script manifest can be blocked by active package scripts; run explicitly for script work",
            "planning_lock": "requires caller-supplied --changed-files and optional --plan-evidence",
        },
    }


COMPILED_TOPOLOGY_SOURCE_MANIFESTS = [
    "architecture/topology.yaml",
    "architecture/artifact_lifecycle.yaml",
    "architecture/source_rationale.yaml",
    "architecture/test_topology.yaml",
    "architecture/script_manifest.yaml",
    "architecture/reference_replacement.yaml",
    "architecture/core_claims.yaml",
    "architecture/history_lore.yaml",
    "architecture/context_pack_profiles.yaml",
    "architecture/map_maintenance.yaml",
]


def build_compiled_topology() -> dict[str, Any]:
    topology = load_topology()
    lifecycle = load_artifact_lifecycle()
    source_manifests = [
        {"path": path, "exists": (ROOT / path).exists()}
        for path in COMPILED_TOPOLOGY_SOURCE_MANIFESTS
    ]
    docs_subroots = topology.get("docs_subroots") or []
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
    current_state = ROOT / "docs/operations/current_state.md"
    current_text = current_state.read_text(encoding="utf-8", errors="ignore") if current_state.exists() else ""
    active_surfaces = sorted(
        _current_state_operation_paths(
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
        asdict(issue)
        for issue in _check_broken_internal_paths()
        if issue.code == "docs_broken_internal_path"
    ]
    unclassified_docs_artifacts = [
        asdict(issue)
        for issue in _check_hidden_docs(topology)
        if issue.code in {"docs_unregistered_subtree", "docs_non_markdown_artifact"}
    ]
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
    }


def format_issues(issues: list[TopologyIssue]) -> str:
    if not issues:
        return "no topology issues"
    lines = [f"{len(issues)} topology issue(s):"]
    for index, issue in enumerate(issues, 1):
        lines.append(f"{index}. [{issue.severity}:{issue.code}] {issue.path}: {issue.message}")
    return "\n".join(lines)


def summarize_issues(issues: list[TopologyIssue]) -> str:
    if not issues:
        return "no topology issues"
    counts: dict[tuple[str, str], int] = {}
    for issue in issues:
        key = (issue.severity, issue.code)
        counts[key] = counts.get(key, 0) + 1
    lines = [f"{len(issues)} topology issue(s)"]
    for (severity, code), count in sorted(counts.items()):
        lines.append(f"- {severity}:{code}: {count}")
    return "\n".join(lines)


def _print_strict(
    result: StrictResult,
    *,
    as_json: bool = False,
    summary_only: bool = False,
) -> None:
    if as_json:
        print(json.dumps({"ok": result.ok, "issues": [asdict(i) for i in result.issues]}, indent=2))
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
