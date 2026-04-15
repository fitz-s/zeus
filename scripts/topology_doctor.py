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
SOURCE_RATIONALE_PATH = ROOT / "architecture" / "source_rationale.yaml"
TEST_TOPOLOGY_PATH = ROOT / "architecture" / "test_topology.yaml"
SCRIPT_MANIFEST_PATH = ROOT / "architecture" / "script_manifest.yaml"
DATA_REBUILD_TOPOLOGY_PATH = ROOT / "architecture" / "data_rebuild_topology.yaml"
HISTORY_LORE_PATH = ROOT / "architecture" / "history_lore.yaml"
CONTEXT_BUDGET_PATH = ROOT / "architecture" / "context_budget.yaml"
CODE_IDIOMS_PATH = ROOT / "architecture" / "code_idioms.yaml"
RUNTIME_MODES_PATH = ROOT / "architecture" / "runtime_modes.yaml"
REFERENCE_REPLACEMENT_PATH = ROOT / "architecture" / "reference_replacement.yaml"
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


def load_code_idioms() -> dict[str, Any]:
    return _load_yaml(CODE_IDIOMS_PATH)


def load_runtime_modes() -> dict[str, Any]:
    return _load_yaml(RUNTIME_MODES_PATH)


def load_reference_replacement() -> dict[str, Any]:
    return _load_yaml(REFERENCE_REPLACEMENT_PATH)


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


def _issue(code: str, path: str, message: str) -> TopologyIssue:
    return TopologyIssue(code=code, path=path, message=message)


def _warning(code: str, path: str, message: str) -> TopologyIssue:
    return TopologyIssue(code=code, path=path, message=message, severity="warning")


def _declared_paths(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("path", "")) for item in items if item.get("path")}


def _registry_entries(agents_path: Path) -> set[str]:
    entries: set[str] = set()
    for line in agents_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 3:
            continue
        first_cell = cells[1]
        for token in re.findall(r"`([^`]+)`", first_cell):
            if "*" in token or token.endswith("/"):
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


def _docs_mode_excluded_roots(topology: dict[str, Any]) -> list[Path]:
    roots = []
    for item in topology.get("docs_mode_excluded_roots", []):
        rel = item.get("path")
        if rel:
            roots.append(ROOT / rel)
    return roots


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _check_hidden_docs(topology: dict[str, Any]) -> list[TopologyIssue]:
    issues = []
    excluded_roots = _docs_mode_excluded_roots(topology)
    for path in sorted((ROOT / "docs").rglob("*.md")):
        rel = path.relative_to(ROOT).as_posix()
        if any(_is_under(path, root) for root in excluded_roots):
            continue
        if " " in rel and "archives/" not in rel:
            issues.append(
                _issue("hidden_active_doc", rel, "active docs path contains spaces")
            )
    return issues


def _check_docs_subtree_agents() -> list[TopologyIssue]:
    issues = []
    for rel in ("authority", "reference", "operations", "runbooks", "archives"):
        path = ROOT / "docs" / rel / "AGENTS.md"
        if not path.exists():
            issues.append(
                _issue("missing_docs_agents", f"docs/{rel}/AGENTS.md", "active docs subtree lacks AGENTS.md")
            )
    return issues


def _check_root_and_state_classification(topology: dict[str, Any]) -> list[TopologyIssue]:
    issues = []
    root_declared = _declared_paths(topology.get("root_governed_files", []))
    for path in sorted(ROOT.iterdir()):
        if not path.is_file() or _is_root_scratch(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel not in root_declared:
            issues.append(
                _issue("unclassified_root_artifact", rel, "repo-root file is not classified")
            )

    state_declared = _declared_paths(topology.get("state_surfaces", []))
    state_dir = ROOT / "state"
    if state_dir.exists():
        for path in sorted(state_dir.iterdir()):
            if not path.is_file() or path.name == ".DS_Store":
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel not in state_declared:
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
    issues.extend(_check_docs_subtree_agents())
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
        "after root `AGENTS.md`",
        "task digest",
        "architecture/kernel_manifest.yaml",
        "architecture/invariants.yaml",
        "architecture/zones.yaml",
        "architecture/source_rationale.yaml",
        "docs/authority/zeus_current_architecture.md",
        "docs/authority/zeus_current_delivery.md",
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


def _reference_default_reads() -> set[str]:
    agents = ROOT / "docs" / "reference" / "AGENTS.md"
    text = agents.read_text(encoding="utf-8", errors="ignore")
    start = text.find("**Default reads**")
    end = text.find("**Conditional reads**")
    if start == -1 or end == -1 or end < start:
        return set()
    block = text[start:end]
    return set(re.findall(r"`([^`]+\.md)`", block))


def run_reference_replacement() -> StrictResult:
    manifest = load_reference_replacement()
    issues: list[TopologyIssue] = []
    required = manifest.get("required_entry_fields") or []
    allowed_statuses = set(manifest.get("allowed_replacement_statuses") or [])
    allowed_actions = set(manifest.get("allowed_actions") or [])
    entries = manifest.get("entries") or []
    entry_by_path = {str(entry.get("path")): entry for entry in entries if entry.get("path")}

    actual_reference_docs = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "docs" / "reference").glob("*.md")
        if path.name != "AGENTS.md"
    }
    declared = set(entry_by_path)

    for path in sorted(actual_reference_docs - declared):
        issues.append(_issue("reference_replacement_missing_entry", path, "reference doc has no replacement matrix entry"))
    for path in sorted(declared - actual_reference_docs):
        issues.append(_issue("reference_replacement_stale_entry", path, "replacement matrix entry has no reference doc"))

    default_reads = _reference_default_reads()
    for path, entry in sorted(entry_by_path.items()):
        short = Path(path).name
        for field in required:
            if field not in entry:
                issues.append(_issue("reference_replacement_required_field_missing", path, f"missing {field}"))
        if entry.get("replacement_status") not in allowed_statuses:
            issues.append(_issue("reference_replacement_invalid_status", path, f"invalid status {entry.get('replacement_status')!r}"))
        if entry.get("allowed_action") not in allowed_actions:
            issues.append(_issue("reference_replacement_invalid_action", path, f"invalid action {entry.get('allowed_action')!r}"))
        if bool(entry.get("default_read")) != (short in default_reads):
            issues.append(
                _issue(
                    "reference_replacement_default_read_mismatch",
                    path,
                    f"default_read={entry.get('default_read')} but docs/reference/AGENTS.md default reads contain {short}: {short in default_reads}",
                )
            )
        for replacement in entry.get("replaced_by") or []:
            if not any(char in str(replacement) for char in "*?[]") and not (ROOT / str(replacement)).exists():
                issues.append(_issue("reference_replacement_replacement_missing", path, f"replacement target missing: {replacement}"))
        if entry.get("delete_allowed") is True:
            if entry.get("replacement_status") != "replaced":
                issues.append(_issue("reference_replacement_delete_unsafe", path, "delete_allowed requires replacement_status=replaced"))
            if entry.get("unique_remaining"):
                issues.append(_issue("reference_replacement_delete_unsafe", path, "delete_allowed requires unique_remaining=[]"))
            if not entry.get("replaced_by"):
                issues.append(_issue("reference_replacement_delete_unsafe", path, "delete_allowed requires replaced_by evidence"))

    return StrictResult(ok=not issues, issues=issues)


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
    tracked = set(_git_ls_files())
    return {path: _change_kind(path, tracked) for path in changed_files}


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


def _data_rebuild_digest() -> dict[str, Any]:
    topology = load_data_rebuild_topology()
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


def _script_lifecycle_digest() -> dict[str, Any]:
    manifest = load_script_manifest()
    scripts = manifest.get("scripts") or {}
    return {
        "allowed_lifecycles": manifest.get("allowed_lifecycles", []),
        "long_lived_naming": manifest.get("long_lived_naming", {}),
        "required_effective_fields": manifest.get("required_effective_fields", []),
        "existing_scripts": {
            name: {
                "class": _effective_script_entry(manifest, name).get("class"),
                "status": _effective_script_entry(manifest, name).get("status"),
                "lifecycle": _effective_script_entry(manifest, name).get("lifecycle"),
                "write_targets": _effective_script_entry(manifest, name).get("write_targets", []),
                "dangerous_if_run": _effective_script_entry(manifest, name).get("dangerous_if_run", False),
            }
            for name in sorted(scripts)
        },
    }


def _compact_lore_card(card: dict[str, Any]) -> dict[str, Any]:
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


def _matched_history_lore(task: str, files: list[str]) -> list[dict[str, Any]]:
    lore = load_history_lore()
    task_l = task.lower()
    matched: list[dict[str, Any]] = []
    for card in lore.get("cards") or []:
        routing = card.get("routing") or {}
        terms = [str(term).lower() for term in routing.get("task_terms", [])]
        patterns = [str(pattern) for pattern in routing.get("file_patterns", [])]
        term_hit = any(term and term in task_l for term in terms)
        file_hit = any(fnmatch(file, pattern) for file in files for pattern in patterns)
        if term_hit or file_hit:
            matched.append(_compact_lore_card(card))
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        matched,
        key=lambda item: (
            severity_rank.get(str(item.get("severity")), 99),
            str(item.get("id")),
        ),
    )


def build_digest(task: str, files: list[str] | None = None) -> dict[str, Any]:
    topology = load_topology()
    task_l = task.lower()
    for profile in topology.get("digest_profiles", []):
        matches = [str(item).lower() for item in profile.get("match", [])]
        if any(match in task_l for match in matches):
            selected = profile
            break
    else:
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
    payload["source_rationale"] = _source_rationale_for(source_files)
    if selected.get("id") == "add a data backfill":
        payload["data_rebuild_topology"] = _data_rebuild_digest()
    if selected.get("id") == "add or change script":
        payload["script_lifecycle"] = _script_lifecycle_digest()
    payload["history_lore"] = _matched_history_lore(task, files or [])
    return payload


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


def _print_strict(result: StrictResult, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps({"ok": result.ok, "issues": [asdict(i) for i in result.issues]}, indent=2))
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Run strict topology checks")
    parser.add_argument("--docs", action="store_true", help="Run Packet 3 docs-mesh checks")
    parser.add_argument("--source", action="store_true", help="Run Packet 4 source-rationale checks")
    parser.add_argument("--tests", action="store_true", help="Run Packet 5 test topology checks")
    parser.add_argument("--scripts", action="store_true", help="Run Packet 6 script manifest checks")
    parser.add_argument("--data-rebuild", action="store_true", help="Run Packet 8 data/rebuild topology checks")
    parser.add_argument("--history-lore", action="store_true", help="Run historical lore card checks")
    parser.add_argument("--context-budget", action="store_true", help="Run context budget checks")
    parser.add_argument("--agents-coherence", action="store_true", help="Check scoped AGENTS prose against machine maps")
    parser.add_argument("--idioms", action="store_true", help="Check intentional non-obvious code idiom registry")
    parser.add_argument("--self-check-coherence", action="store_true", help="Check zero-context self-check alignment with root navigation")
    parser.add_argument("--runtime-modes", action="store_true", help="Check discovery/runtime mode manifest and root visibility")
    parser.add_argument("--reference-replacement", action="store_true", help="Check reference replacement matrix")
    parser.add_argument("--map-maintenance", action="store_true", help="Check companion registry updates for added/deleted files")
    parser.add_argument(
        "--map-maintenance-mode",
        choices=["advisory", "precommit", "closeout"],
        default="advisory",
        help="Map-maintenance severity mode",
    )
    parser.add_argument("--navigation", action="store_true", help="Run default navigation health and task digest")
    parser.add_argument("--planning-lock", action="store_true", help="Check whether changed files require planning evidence")
    parser.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        help="Files for --planning-lock; optional map-maintenance override (omitted there reads git status)",
    )
    parser.add_argument("--plan-evidence", default=None, help="Plan/current-state evidence path for --planning-lock")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--task", default="", help="Task string for --navigation")
    parser.add_argument("--files", nargs="*", default=[], help="Files for --navigation")
    sub = parser.add_subparsers(dest="command")
    digest = sub.add_parser("digest", help="Emit bounded task topology digest")
    digest.add_argument("--task", required=True)
    digest.add_argument("--files", nargs="*", default=[])
    digest.add_argument("--json", action="store_true", help="Emit JSON")

    args = parser.parse_args(argv)
    if args.strict:
        result = run_strict()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.docs:
        result = run_docs()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.source:
        result = run_source()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.tests:
        result = run_tests()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.scripts:
        result = run_scripts()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.data_rebuild:
        result = run_data_rebuild()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.history_lore:
        result = run_history_lore()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.context_budget:
        result = run_context_budget()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.agents_coherence:
        result = run_agents_coherence()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.idioms:
        result = run_idioms()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.self_check_coherence:
        result = run_self_check_coherence()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.runtime_modes:
        result = run_runtime_modes()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.reference_replacement:
        result = run_reference_replacement()
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.map_maintenance:
        result = run_map_maintenance(args.changed_files, mode=args.map_maintenance_mode)
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.navigation:
        payload = run_navigation(args.task or "general navigation", args.files)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"navigation ok: {payload['ok']}")
            print(f"profile: {payload['digest']['profile']}")
            if payload["issues"]:
                print("issues:")
                for issue in payload["issues"]:
                    print(f"- [{issue['severity']}:{issue['lane']}:{issue['code']}] {issue['path']}: {issue['message']}")
            print("excluded_lanes:")
            for lane, reason in payload["excluded_lanes"].items():
                print(f"- {lane}: {reason}")
        return 0 if payload["ok"] else 1
    if args.planning_lock:
        result = run_planning_lock(args.changed_files, args.plan_evidence)
        _print_strict(result, as_json=args.json)
        return 0 if result.ok else 1
    if args.command == "digest":
        payload = build_digest(args.task, args.files)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Topology digest: {payload['profile']}")
            print(f"Task: {payload['task']}")
            for key in ("required_law", "allowed_files", "forbidden_files", "gates", "downstream", "stop_conditions"):
                print(f"\n{key}:")
                for item in payload[key]:
                    print(f"- {item}")
            if payload.get("source_rationale"):
                print("\nsource_rationale:")
                for item in payload["source_rationale"]:
                    print(f"- {item['path']}: {item.get('why', '')}")
                    print(f"  zone: {item.get('zone', '')}")
                    print(f"  authority_role: {item.get('authority_role', '')}")
                    if item.get("hazards"):
                        print(f"  hazards: {', '.join(item['hazards'])}")
                    if item.get("write_routes"):
                        print(f"  write_routes: {', '.join(item['write_routes'])}")
            if payload.get("data_rebuild_topology"):
                data_topology = payload["data_rebuild_topology"]
                print("\ndata_rebuild_topology:")
                certification = data_topology.get("live_math_certification", {})
                print(f"- live_math_certification.allowed: {certification.get('allowed')}")
                print("- row_contract_tables:")
                for name, spec in data_topology.get("row_contract_tables", {}).items():
                    fields = ", ".join(spec.get("required_fields", []))
                    print(f"  - {name}: fields=[{fields}] producer={spec.get('producer', '')}")
                required = ", ".join(data_topology.get("replay_coverage_rule", {}).get("required_for_strategy_replay_coverage", []))
                print(f"- replay_coverage_required: {required}")
            if payload.get("history_lore"):
                print("\nhistory_lore:")
                for card in payload["history_lore"]:
                    print(f"- {card['id']} [{card['severity']}/{card['status']}]: {card['zero_context_digest']}")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
