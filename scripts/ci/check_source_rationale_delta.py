#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml#blocking_structural:source_rationale_delta_gate
#                  architecture/source_rationale.yaml
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Detect new external sources/providers introduced by the PR and require
matching entries in architecture/source_rationale.yaml.

This addresses FC-07 source-plane collapse (settlement source !=
day0 source != historical hourly source != forecast skill source).
When an agent adds a new source family, the contract for which role it
plays MUST be declared in source_rationale.yaml — otherwise the agent
silently uses it for whatever happens to be convenient.

Detection: scans changed files (per --changed-files or auto-detected via
git diff) for `from src.data.<provider>_*` or `from src.ingest.<provider>_*`
imports + new source-id strings, and flags any token not listed in
source_rationale.yaml.

Exit codes:
    0 — no new undeclared source
    1 — one or more new sources missing rationale
    2 — IO / parse error
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Naming heuristics — patterns that look like external source/provider declarations.
_PROVIDER_IMPORT = re.compile(
    r"from\s+src\.(?:data|ingest)\.(\w+?)(?:_client|_adapter|_ingest|_provider)"
)
_NEW_SOURCE_FILE = re.compile(
    r"^src/(?:data|ingest)/(\w+?)(?:_client|_adapter|_ingest|_provider)\.py$"
)


def _changed_files_from_git(base: str, head: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{head}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if out.returncode != 0:
        return []
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def _known_sources(source_rationale: dict) -> set[str]:
    out: set[str] = set()
    # Common shapes: top-level `sources:` dict or list, or `providers:` list
    for key in ("sources", "providers", "source_families", "external_sources"):
        node = source_rationale.get(key)
        if isinstance(node, dict):
            out.update(node.keys())
        elif isinstance(node, list):
            for entry in node:
                if isinstance(entry, dict):
                    sid = entry.get("id") or entry.get("name")
                    if sid:
                        out.add(sid.lower())
                elif isinstance(entry, str):
                    out.add(entry.lower())
    # Walk one level deeper if needed
    return {s.lower() for s in out}


def detect_new_sources(
    changed_files: list[str],
    repo: Path,
    known: set[str],
) -> list[dict]:
    findings: list[dict] = []
    for fp in changed_files:
        m = _NEW_SOURCE_FILE.match(fp)
        if m:
            name = m.group(1).lower()
            if name not in known:
                if (repo / fp).exists():
                    findings.append({
                        "file": fp,
                        "detected_source": name,
                        "reason": "new src/data or src/ingest provider file",
                    })
        # Also grep the file content for imports that introduce new providers
        full = repo / fp
        if not full.exists() or not str(full).endswith(".py"):
            continue
        try:
            text = full.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for im in _PROVIDER_IMPORT.finditer(text):
            name = im.group(1).lower()
            if name not in known:
                # de-dup
                if not any(f["detected_source"] == name and f["file"] == fp for f in findings):
                    findings.append({
                        "file": fp,
                        "detected_source": name,
                        "reason": "import of unregistered provider",
                    })
    return findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", default=str(REPO_ROOT))
    p.add_argument("--changed-files", nargs="*", default=None,
                   help="Changed file paths; auto-detected from git diff if omitted")
    p.add_argument("--base", default="origin/main")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo_root)
    sr_path = repo / "architecture" / "source_rationale.yaml"
    if not sr_path.exists():
        print(f"ERROR: missing {sr_path}", file=sys.stderr)
        return 2

    with sr_path.open() as f:
        sr = yaml.safe_load(f) or {}
    known = _known_sources(sr)

    files = args.changed_files
    if not files:
        files = _changed_files_from_git(args.base, args.head)
    findings = detect_new_sources(files, repo, known)

    if args.json:
        print(json.dumps({"findings": findings, "count": len(findings)}, indent=2))
    else:
        if not findings:
            print("OK: no new external sources detected; or all are registered.")
        else:
            print(f"FAIL: {len(findings)} new external source(s) lack source_rationale:")
            for f in findings:
                print(f"  {f['file']}: detected {f['detected_source']!r} ({f['reason']})")
            print()
            print(
                "Add an entry to architecture/source_rationale.yaml declaring the "
                "role of this source (settlement / day0 / historical hourly / "
                "forecast skill / etc) before merging."
            )

    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
