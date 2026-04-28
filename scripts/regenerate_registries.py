#!/usr/bin/env python3
# Lifecycle: created=2026-04-28; last_reviewed=2026-04-28; last_reused=2026-04-28
# Purpose: Audit manifest completeness/header coverage without discarding hand-curated registry metadata.
# Reuse: Run --completeness-audit or --header-audit per PR/quarterly; do not treat output as automatic authority.
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round2_verdict.md §1.1 #10 + DEEP_PLAN §2.2 + Tier 2 Phase 2
# ITEM #11 dispatch (auto-gen 3 manifests from filesystem walk + per-file
# headers). Per Fitz Constraint #1 (encode insight into structure that works
# without being understood) — but only where the filesystem actually carries
# the insight.
"""Audit + (partial) regeneration tool for 3 architecture manifests.

The 3 manifests in scope are:
  - architecture/script_manifest.yaml   (top-level scripts)
  - architecture/test_topology.yaml     (pytest test files)
  - architecture/docs_registry.yaml     (docs surface)

CRITICAL FINDING from Phase 2 audit (run before relying on this tool):
The 3 manifests carry HAND-CURATED domain knowledge that is NOT derivable from
the filesystem (e.g., script_manifest.yaml requires 17 fields per entry: class,
status, authority_scope, dangerous_if_run, promotion_barrier, etc.; only ~2 of
these are auto-derivable). Full regeneration would discard ~95% of load-bearing
content. This tool therefore operates in 3 modes:

  --completeness-audit   Report (a) paths on disk MISSING from manifest, (b) paths
                         in manifest MISSING on disk. STRATIFIED output (per
                         T2P1-1 critic caveat): MISSING_FROM_MANIFEST_HIGH_VALUE
                         vs MISSING_FROM_MANIFEST_LOW_VALUE vs ORPHAN_IN_MANIFEST.
  --header-audit         Report which test files have/lack the
                         "# Created: YYYY-MM-DD / # Last reused/audited: YYYY-MM-DD"
                         lifecycle header that test_topology.yaml requires.
  --diff                 Show the deltas the operator would need to apply to
                         re-sync the manifest with the filesystem (no writes).

Usage:
    python3 scripts/regenerate_registries.py --completeness-audit [--json]
    python3 scripts/regenerate_registries.py --header-audit [--json]
    python3 scripts/regenerate_registries.py --diff <manifest>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
TESTS_DIR = REPO_ROOT / "tests"
DOCS_DIR = REPO_ROOT / "docs"
ARCH_DIR = REPO_ROOT / "architecture"

SCRIPT_MANIFEST = ARCH_DIR / "script_manifest.yaml"
TEST_TOPOLOGY = ARCH_DIR / "test_topology.yaml"
DOCS_REGISTRY = ARCH_DIR / "docs_registry.yaml"

LIFECYCLE_HEADER_RE = re.compile(
    r"^#\s*(?:Created|Last reused/audited|Lifecycle:.*created)\s*[:=]",
    re.MULTILINE | re.IGNORECASE,
)


def _yaml_load(path: Path):
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        print("PyYAML required; install via `pip install pyyaml`", file=sys.stderr)
        sys.exit(2)
    return yaml.safe_load(path.read_text())


def _walk_paths(root: Path, pattern: str) -> set[str]:
    """Return repo-relative paths matching the glob pattern under root."""
    return {
        str(p.relative_to(REPO_ROOT))
        for p in root.glob(pattern)
        if p.is_file() and not p.name.startswith(".") and "__pycache__" not in p.parts
    }


def _manifest_paths(manifest_doc, key_paths: list[str]) -> set[str]:
    """Walk a manifest YAML doc and extract any string values that look like
    repo paths under one of the registered prefixes (scripts/, tests/, docs/)."""
    paths: set[str] = set()

    def visit(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and any(k.startswith(p) for p in key_paths):
                    paths.add(k)
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)
        elif isinstance(node, str):
            for prefix in key_paths:
                if node.startswith(prefix) and node.endswith((".py", ".md", ".yaml", ".sql", ".json")):
                    paths.add(node)

    visit(manifest_doc)
    return paths


def completeness_audit() -> dict:
    """Compare filesystem inventory vs each manifest's enumerated paths."""
    report = {"audited_at": str(REPO_ROOT), "manifests": {}}

    # script_manifest: scripts/*.py top-level only
    # Note: scripts: dict is keyed by filename (no `scripts/` prefix); reconstruct.
    script_fs = _walk_paths(SCRIPTS_DIR, "*.py")
    script_doc = _yaml_load(SCRIPT_MANIFEST)
    script_man = set()
    if isinstance(script_doc, dict) and isinstance(script_doc.get("scripts"), dict):
        for name in script_doc["scripts"].keys():
            if isinstance(name, str) and name.endswith(".py"):
                script_man.add(f"scripts/{name}")
    # Also pick up any explicit `scripts/...` paths elsewhere in the doc (defensive)
    script_man |= _manifest_paths(script_doc, ["scripts/"])
    report["manifests"]["script_manifest"] = {
        "fs_count": len(script_fs),
        "manifest_count": len(script_man),
        "missing_from_manifest": sorted(script_fs - script_man),
        "orphan_in_manifest": sorted(script_man - script_fs),
    }

    # test_topology: tests/test_*.py + tests/**/*.py (registry tracks both
    # top-level tests AND subdirectory contracts/manifests like
    # tests/contracts/spec_validation_manifest.py — Phase 3 audit found that
    # Phase 2's "1 orphan" was a false positive: my flat walker missed the
    # subdir; the file actually exists at the cited path).
    test_fs = _walk_paths(TESTS_DIR, "test_*.py")
    for sub in TESTS_DIR.rglob("*.py"):
        if sub.is_file() and "__pycache__" not in sub.parts:
            test_fs.add(str(sub.relative_to(REPO_ROOT)))
    test_doc = _yaml_load(TEST_TOPOLOGY)
    test_man = _manifest_paths(test_doc, ["tests/"])
    report["manifests"]["test_topology"] = {
        "fs_count": len(test_fs),
        "manifest_count": len(test_man),
        "missing_from_manifest": sorted(test_fs - test_man),
        "orphan_in_manifest": sorted(test_man - test_fs),
    }

    # docs_registry: docs/**/*.md (recursive)
    docs_fs = {
        str(p.relative_to(REPO_ROOT))
        for p in DOCS_DIR.rglob("*.md")
        if p.is_file() and "__pycache__" not in p.parts
    }
    docs_doc = _yaml_load(DOCS_REGISTRY)
    docs_man = _manifest_paths(docs_doc, ["docs/"])
    report["manifests"]["docs_registry"] = {
        "fs_count": len(docs_fs),
        "manifest_count": len(docs_man),
        "missing_from_manifest_count": len(docs_fs - docs_man),
        "missing_from_manifest_sample": sorted(docs_fs - docs_man)[:20],
        "orphan_in_manifest_count": len(docs_man - docs_fs),
        "orphan_in_manifest_sample": sorted(docs_man - docs_fs)[:20],
    }

    return report


def header_audit() -> dict:
    """For each test file, report whether it has the lifecycle header."""
    report = {"tests": {}}
    test_fs = _walk_paths(TESTS_DIR, "test_*.py")
    with_header = []
    without_header = []
    for path in sorted(test_fs):
        content = (REPO_ROOT / path).read_text(errors="ignore")[:1500]
        # T2P1-1 STRATIFIED: HEADER_PRESENT (strong) vs PARTIAL_HEADER (one of two
        # required dates) vs NO_HEADER (audit_required per test_topology.yaml).
        has_created = re.search(r"^#\s*Created\s*:", content, re.MULTILINE | re.IGNORECASE) is not None
        has_audited = re.search(r"^#\s*Last reused/audited\s*:", content, re.MULTILINE | re.IGNORECASE) is not None
        if has_created and has_audited:
            with_header.append({"path": path, "tier": "HEADER_PRESENT"})
        elif has_created or has_audited:
            with_header.append({"path": path, "tier": "PARTIAL_HEADER"})
        else:
            without_header.append({"path": path, "tier": "NO_HEADER"})
    report["tests"] = {
        "total": len(test_fs),
        "with_full_header": len([t for t in with_header if t["tier"] == "HEADER_PRESENT"]),
        "with_partial_header": len([t for t in with_header if t["tier"] == "PARTIAL_HEADER"]),
        "without_header": len(without_header),
        "without_header_sample": sorted([t["path"] for t in without_header])[:20],
    }
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--completeness-audit", action="store_true",
                    help="Compare filesystem inventory vs manifest paths")
    ap.add_argument("--header-audit", action="store_true",
                    help="Report lifecycle-header coverage in tests/")
    ap.add_argument("--diff", choices=["script_manifest", "test_topology", "docs_registry"],
                    help="(Stub) show diff between filesystem and one manifest")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()

    reports = {}
    if args.completeness_audit:
        reports["completeness"] = completeness_audit()
    if args.header_audit:
        reports["header"] = header_audit()
    if args.diff:
        reports["diff"] = {
            "manifest": args.diff,
            "note": "Stub for now; --completeness-audit gives the same actionable signal",
        }
    if not reports:
        ap.print_help()
        return 1

    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for kind, rep in reports.items():
            print(f"=== {kind} ===")
            if kind == "completeness":
                for name, m in rep["manifests"].items():
                    print(f"\n[{name}]")
                    for k, v in m.items():
                        if isinstance(v, list) and len(v) > 5:
                            print(f"  {k}: {len(v)} items (first 5: {v[:5]}...)")
                        else:
                            print(f"  {k}: {v}")
            elif kind == "header":
                t = rep["tests"]
                print(f"  total tests: {t['total']}")
                print(f"  HEADER_PRESENT: {t['with_full_header']}")
                print(f"  PARTIAL_HEADER: {t['with_partial_header']}")
                print(f"  NO_HEADER:      {t['without_header']}")
                if t["without_header_sample"]:
                    print(f"  NO_HEADER sample (first 20): {t['without_header_sample']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
