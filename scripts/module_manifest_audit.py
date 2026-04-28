#!/usr/bin/env python3
# Lifecycle: created=2026-04-28; last_reviewed=2026-04-28; last_reused=2026-04-28
# Purpose: Classify module_manifest entries by hand-curated versus auto-derivable metadata before migration decisions.
# Reuse: Run read-only before proposing module_manifest replacement or hybrid extraction; reports are non-authority evidence.
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round2_verdict.md §4.2 #11 + DEEP_PLAN §4.2 #11 + Tier 2
# Phase 3 ITEM #15a dispatch (audit-first per Phase 2 lesson; apparent gap
# is not always drift). Per Fitz Constraint #1 + zeus-phase-discipline SKILL
# §"During implementation" (bidirectional grep before claiming "X% lack Z").
"""Module manifest audit — bidirectional grep + auto-derivability classification.

For each module entry in architecture/module_manifest.yaml, classify:

  KEEP_AS_YAML        Hand-curated metadata is load-bearing AND not derivable
                      from runtime introspection of src/<package>/__init__.py.
                      DEEP_PLAN §4.2 #11 was wrong about this entry.
  REPLACE_WITH_INIT_PY  Mostly auto-derivable (path/scoped_agents/module_book
                      can be regenerated from filesystem walk + per-package
                      __init__.py registries).
  HYBRID              Mix: some fields hand-curated (e.g., authority_role,
                      maturity, priority), others auto-derivable. Could move
                      auto-derivable fields to package metadata; retain
                      curated fields in YAML appendix.

Bidirectional grep:
  Forward:  YAML cites src/<package>/<file> — does that file exist?
  Reverse:  src/<package>/__init__.py declares any runtime registry
            (e.g., __all__, public_entry_points list) that mirrors the YAML?

STRATIFIED output (per T2P1-1 critic caveat).
Phase 3 STOPS at audit; per-package decisions are operator territory.

Usage:
    python3 scripts/module_manifest_audit.py [--json] [--report-out <path>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "architecture" / "module_manifest.yaml"

# Fields per entry that are hand-curated (NOT derivable from filesystem or
# runtime introspection without explicit module-author input).
HAND_CURATED_FIELDS = {
    "priority", "maturity", "zone", "authority_role",
    "law_dependencies", "current_fact_dependencies", "required_tests",
    "graph_appendix_status", "archive_extraction_status",
    "high_risk_files",   # which subset is "high risk" is curator opinion
    "public_entry_files",  # could mirror __all__ but rarely synced
}

# Fields that are filesystem-derivable.
AUTO_DERIVABLE_FIELDS = {
    "path",          # repo-relative module path
    "scoped_agents", # is there a src/<pkg>/AGENTS.md?
    "module_book",   # is there a docs/reference/modules/<pkg>.md?
}


def _yaml_load(path: Path):
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        print("PyYAML required", file=sys.stderr)
        sys.exit(2)
    return yaml.safe_load(path.read_text())


def _file_exists(rel_path: str) -> bool:
    return (REPO_ROOT / rel_path).exists()


def _has_runtime_registry(pkg_path: str) -> tuple[bool, str]:
    """Reverse grep: does src/<pkg>/__init__.py declare __all__ or a registry?"""
    init = REPO_ROOT / pkg_path / "__init__.py"
    if not init.exists():
        return False, "no __init__.py"
    text = init.read_text(errors="ignore")
    if re.search(r"^\s*__all__\s*=", text, re.MULTILINE):
        return True, "__all__ declared"
    if re.search(r"^\s*PUBLIC_ENTRY_POINTS\s*=", text, re.MULTILINE):
        return True, "PUBLIC_ENTRY_POINTS declared"
    if re.search(r"^\s*MODULE_REGISTRY\s*=", text, re.MULTILINE):
        return True, "MODULE_REGISTRY declared"
    return False, "no runtime registry symbol"


def classify_module(name: str, entry: dict) -> dict:
    """Return STRATIFIED verdict + per-field auto-derivability accounting."""
    pkg_path = entry.get("path", f"src/{name}")
    fields = set(entry.keys())
    hand = fields & HAND_CURATED_FIELDS
    auto = fields & AUTO_DERIVABLE_FIELDS
    other = fields - HAND_CURATED_FIELDS - AUTO_DERIVABLE_FIELDS

    has_registry, registry_note = _has_runtime_registry(pkg_path)

    # Forward cite: do high_risk_files + public_entry_files all exist?
    cited_paths = []
    for k in ("high_risk_files", "public_entry_files"):
        v = entry.get(k) or []
        if isinstance(v, list):
            cited_paths.extend(v)
    missing_cites = [p for p in cited_paths if not _file_exists(p)]

    # Verdict logic
    hand_count = len(hand)
    if hand_count >= 4 and not has_registry:
        # Lots of hand-curated fields and no runtime registry to take over.
        verdict = "KEEP_AS_YAML"
        rationale = f"{hand_count} hand-curated fields (no runtime registry to absorb them)"
    elif hand_count >= 4 and has_registry:
        verdict = "HYBRID"
        rationale = f"{hand_count} hand-curated fields + {registry_note}; auto-derive path/scoped_agents/module_book; retain curated in YAML appendix"
    elif hand_count <= 2 and has_registry:
        verdict = "REPLACE_WITH_INIT_PY"
        rationale = f"only {hand_count} curated fields + {registry_note}"
    else:
        verdict = "HYBRID"
        rationale = f"{hand_count} curated fields, {registry_note}"

    return {
        "module": name,
        "path": pkg_path,
        "verdict": verdict,
        "rationale": rationale,
        "hand_curated_field_count": hand_count,
        "auto_derivable_field_count": len(auto),
        "other_field_count": len(other),
        "has_runtime_registry": has_registry,
        "runtime_registry_note": registry_note,
        "cited_paths_count": len(cited_paths),
        "missing_cited_paths": missing_cites,
    }


def audit() -> dict:
    doc = _yaml_load(MANIFEST)
    modules = doc.get("modules", {}) or {}
    results = []
    for name, entry in sorted(modules.items()):
        if not isinstance(entry, dict):
            continue
        results.append(classify_module(name, entry))
    counts = {"KEEP_AS_YAML": 0, "REPLACE_WITH_INIT_PY": 0, "HYBRID": 0}
    for r in results:
        counts[r["verdict"]] += 1
    return {
        "manifest_path": str(MANIFEST.relative_to(REPO_ROOT)),
        "total_modules": len(results),
        "stratified_counts": counts,
        "modules": results,
    }


def render_md(report: dict) -> str:
    lines = [
        "# Module manifest audit — 2026-04-28",
        "",
        f"Source: `{report['manifest_path']}`",
        f"Total modules: {report['total_modules']}",
        "",
        "## Stratified counts",
        "",
        "| Verdict | Count |",
        "|---|---|",
    ]
    for k, v in report["stratified_counts"].items():
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## Per-module detail")
    lines.append("")
    lines.append("| Module | Verdict | Path | Hand-curated fields | Auto-derivable fields | Runtime registry | Missing cites | Rationale |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in report["modules"]:
        miss = ",".join(r["missing_cited_paths"][:2]) + ("..." if len(r["missing_cited_paths"]) > 2 else "")
        lines.append(
            f"| `{r['module']}` | **{r['verdict']}** | `{r['path']}` | {r['hand_curated_field_count']} | "
            f"{r['auto_derivable_field_count']} | {'✓' if r['has_runtime_registry'] else '✗'} ({r['runtime_registry_note']}) | "
            f"{len(r['missing_cited_paths'])} ({miss or '-'}) | {r['rationale']} |"
        )
    lines.append("")
    lines.append("## Recommendations (operator decides per-module)")
    lines.append("")
    lines.append("- **KEEP_AS_YAML**: hand-curated metadata is load-bearing; YAML is the right surface.")
    lines.append("- **HYBRID**: auto-derive path/scoped_agents/module_book via filesystem walk; retain hand-curated fields (priority, maturity, zone, authority_role, law/current/test dependencies) in YAML appendix.")
    lines.append("- **REPLACE_WITH_INIT_PY**: package `__init__.py` already has runtime registry (__all__ / PUBLIC_ENTRY_POINTS / MODULE_REGISTRY); migrate path-level metadata there.")
    lines.append("")
    lines.append("Per round-2 verdict §4.2 #11 + Phase 2 lesson: apparent gap ≠ drift. Verify before replacing.")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report-out", help="Write markdown report to this path")
    args = ap.parse_args()
    report = audit()
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.report_out:
        Path(args.report_out).write_text(render_md(report))
        print(f"wrote report: {args.report_out}")
        c = report["stratified_counts"]
        print(f"counts: KEEP_AS_YAML={c['KEEP_AS_YAML']} HYBRID={c['HYBRID']} REPLACE_WITH_INIT_PY={c['REPLACE_WITH_INIT_PY']}")
    else:
        print(render_md(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
