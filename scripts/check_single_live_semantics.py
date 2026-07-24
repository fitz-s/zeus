# Created: 2026-07-22
# Last reused/audited: 2026-07-22
# Authority basis: operator-directed single-live-semantics extinction pass.
"""Reject resurrection of dormant alternate-runtime concepts."""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (
    "src",
    "scripts",
    "architecture",
    "config",
    "deploy",
    ".github",
    "docs/authority",
    "docs/reference",
)
SCAN_FILES = (
    "AGENTS.md",
    "docs/operations/current/GOAL.md",
    "docs/operations/current/package.yaml",
    "docs/operations/current/plans/INDEX.md",
    "docs/operations/current/plans/single_live_semantics_2026-07-22.md",
)
TEXT_SUFFIXES = {".json", ".md", ".plist", ".py", ".sh", ".toml", ".txt", ".yaml", ".yml"}
EXCLUDED = {Path("scripts/check_single_live_semantics.py")}
EXCLUDED_SUBTREES = (
    Path("docs/archive"),
    Path("docs/evidence"),
    Path("docs/rebuild"),
    Path("docs/operations/current/plans/migration_preview"),
)

_PARALLEL_INACTIVE = "shadow_" + "veto_only"
_RETIRED_MEAN_SHIFT = "edli_" + "bias_correction"
_RETIRED_EXIT_MEAN_SHIFT = "exit_" + "bias_family_unify"
_RETIRED_AUTHORITY_COLUMN = "trade_" + "authority_status"
_FORBIDDEN = (
    _PARALLEL_INACTIVE,
    _RETIRED_AUTHORITY_COLUMN,
    "validated_calibration_" + "transfers",
    "ctf_conversion_" + "commands",
    "ctf_conversion_command_" + "events",
    "entry_forecast_" + "rollout",
    "entry_forecast_" + "promotion",
    "replacement_forecast_live_" + "dry_run",
    "experimental_" + "disabled",
    _RETIRED_MEAN_SHIFT,
    _RETIRED_EXIT_MEAN_SHIFT,
    "calibration_auto_" + "promote",
    "unified_uncertainty_" + "budget",
    "evaluator_entry_quote_" + "evidence_enabled",
    "force_exit_" + "review",
    "zeus_harvester_live_" + "enabled",
    "edli_intake_phase_filter_" + "enabled",
    "zeus_user_channel_ws_" + "enabled",
    "zeus_autonomous_redeem_" + "enabled",
    "zeus_autonomous_redeem_" + "dry_run",
    "zeus_autonomous_wrap_" + "dry_run",
    "wrap_dry_run_" + "logged",
    "kelly_dry_" + "run",
    "city_skill_gate_live_" + "enabled",
    "ingest_etl_forecast_" + "skill",
    "replacement_0_1_bayes_precision_fusion_" + "capture_enabled",
    "replacement_0_1_bayes_precision_fusion_" + "enabled",
    "openmeteo_ecmwf_ifs9_bayes_fusion_live_" + "enabled",
    "openmeteo_ecmwf_ifs9_bayes_fusion_kelly_increase_" + "enabled",
    "openmeteo_ecmwf_ifs9_bayes_fusion_direction_flip_" + "enabled",
)
_RUNTIME_CATEGORY_FORBIDDEN = (
    "telemetry_only",
    "observe_only",
    "observation_only",
)
_CONCEPT_TOKENS = (
    "sha" + "dow",
)


def violations(
    root: Path = ROOT, *, include_external_symlinks: bool = True
) -> list[str]:
    out: list[str] = []
    paths = _scan_paths(root)
    paths.update(_live_reachable_excluded_python_paths(root, paths))
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.is_symlink() and not include_external_symlinks:
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        rel_lower = rel.as_posix().lower()
        if rel in EXCLUDED:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for token in _CONCEPT_TOKENS:
            if _contains_live_alternate_concept(token, text):
                out.append(f"{rel}: forbidden alternate-runtime concept {token!r}")
        for token in _FORBIDDEN:
            if _contains_exact(token, rel_lower) or _contains_exact(token, text):
                out.append(f"{rel}: forbidden dormant-runtime token {token!r}")
        if rel.parts and rel.parts[0] in {
            "src",
            "scripts",
            "config",
            "deploy",
            ".github",
        }:
            for token in _RUNTIME_CATEGORY_FORBIDDEN:
                if _contains_exact(token, rel_lower) or _contains_exact(token, text):
                    out.append(
                        f"{rel}: forbidden vague runtime category {token!r}"
                    )
    return sorted(set(out))


def _scan_paths(root: Path) -> set[Path]:
    paths: set[Path] = set()
    for scan_root in SCAN_ROOTS:
        base = root / scan_root
        if not base.exists():
            continue
        paths.update(
            path
            for path in base.rglob("*")
            if (
                path.suffix.lower() == ".py"
                or not _is_excluded_subtree(path.relative_to(root))
            )
        )
    for subtree in EXCLUDED_SUBTREES:
        base = root / subtree
        if base.exists():
            paths.update(base.rglob("*.py"))
    paths.update(root / name for name in SCAN_FILES)
    return paths


def _is_excluded_subtree(rel: Path) -> bool:
    return any(rel == subtree or subtree in rel.parents for subtree in EXCLUDED_SUBTREES)


def _live_reachable_excluded_python_paths(root: Path, paths: set[Path]) -> set[Path]:
    modules = _python_modules(root)
    pending = [path for path in paths if path.suffix == ".py"]
    seen = set(pending)
    reachable: set[Path] = set()
    while pending:
        path = pending.pop()
        for imported in _imported_modules(path, root):
            candidate = modules.get(imported)
            if candidate is None or candidate in seen:
                continue
            seen.add(candidate)
            pending.append(candidate)
            if _is_excluded_subtree(candidate.relative_to(root)):
                reachable.add(candidate)
    return reachable


def _python_modules(root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if path.name == "__init__.py":
            rel = rel.parent
        else:
            rel = rel.with_suffix("")
        if rel.parts:
            modules[".".join(rel.parts)] = path
    return modules


def _imported_modules(path: Path, root: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    imported: set[str] = set()
    package = ".".join(path.relative_to(root).with_suffix("").parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package_parts = package.split(".") if package else []
                base_parts = package_parts[: max(0, len(package_parts) - node.level + 1)]
                if base:
                    base_parts.extend(base.split("."))
                base = ".".join(base_parts)
            if base:
                imported.add(base)
                imported.update(f"{base}.{alias.name}" for alias in node.names)
    return imported


def _contains_exact(token: str, value: str) -> bool:
    pattern = rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])"
    return re.search(pattern, value) is not None


def _contains_live_alternate_concept(token: str, value: str) -> bool:
    pattern = (
        r"(?:['\"]?(?:mode|category|lane|runtime|semantics)['\"]?\s*[:=]\s*"
        rf"['\"]?){re.escape(token)}(?:[a-z0-9_-]*)"
    )
    return re.search(pattern, value) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    found = violations()
    if found:
        print("\n".join(found))
        return 1
    print("single-live semantics: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
