#!/usr/bin/env python3
# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: round2_verdict.md §4.1 #3 + §1.1 #5 + opponent §3.4 (architecture
# YAML citation coverage extension); both proponent + opponent endorsed in
# verdict §6.1 #3 (round-1 LOCKED).
"""Top-level r3 drift check shim with architecture/*.yaml citation coverage.

Two operating modes:

(1) Default mode — delegates to the canonical r3 drift checker at
    docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py
    (slice-card citations vs HEAD).

(2) `--architecture-yaml` mode — walks every `architecture/*.yaml` file,
    extracts cited paths from `enforced_by`, `proof_files`, `tests`,
    `schema`, `scripts`, `docs` blocks, and reports any cited path that
    does not exist at HEAD. This catches the failure pattern surfaced
    in proponent R2 §1 Finding 1 (7 INVs cited a `migrations/` schema
    path that lives at `architecture/`).

Usage:
    python3 scripts/r3_drift_check.py                    # default: slice cards
    python3 scripts/r3_drift_check.py --phase Z2         # default: single phase
    python3 scripts/r3_drift_check.py --architecture-yaml  # NEW: scan architecture/*.yaml
    python3 scripts/r3_drift_check.py --architecture-yaml --json  # NEW: JSON output

Exit codes:
    0 — GREEN (no drift, or YELLOW line drift only)
    1 — RED (SEMANTIC_MISMATCH / FILE_MISSING / cited path absent at HEAD)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent  # zeus/
ARCH_DIR = REPO_ROOT / "architecture"
R3_SCRIPT = REPO_ROOT / "docs" / "operations" / "task_2026-04-26_ultimate_plan" / "r3" / "scripts" / "r3_drift_check.py"

# Match path/file.py:NNN[-MMM] citations within YAML scalar values
PATH_RE = re.compile(r"((?:src|tests|scripts|architecture|docs)/[\w./_-]+\.(?:py|md|yaml|json|sql))(?::(\d+)(?:-(\d+))?)?")


def _walk_yaml_for_paths(node, found):
    """Recursively walk a parsed YAML doc, accumulating any matched paths."""
    if isinstance(node, dict):
        for v in node.values():
            _walk_yaml_for_paths(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk_yaml_for_paths(v, found)
    elif isinstance(node, str):
        for m in PATH_RE.finditer(node):
            found.append((m.group(0), m.group(1)))


def check_architecture_yaml() -> dict:
    """Scan architecture/*.yaml for cited paths; report missing.

    Returns: {"green": [...], "red": [{yaml, cite, missing_path}, ...]}
    """
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return {"error": "PyYAML not installed; cannot run architecture-yaml check"}

    green = []
    red = []

    for yaml_path in sorted(ARCH_DIR.glob("*.yaml")):
        try:
            doc = yaml.safe_load(yaml_path.read_text())
        except yaml.YAMLError as e:
            red.append({"yaml": str(yaml_path.relative_to(REPO_ROOT)), "kind": "YAML_PARSE_ERROR", "detail": str(e)})
            continue
        if doc is None:
            continue

        paths_found: list[tuple[str, str]] = []  # (full_cite, path_only)
        _walk_yaml_for_paths(doc, paths_found)

        for cite, path in paths_found:
            if (REPO_ROOT / path).exists():
                green.append({"yaml": str(yaml_path.relative_to(REPO_ROOT)), "cite": cite})
            else:
                red.append({
                    "yaml": str(yaml_path.relative_to(REPO_ROOT)),
                    "cite": cite,
                    "missing_path": path,
                    "kind": "PATH_MISSING_AT_HEAD",
                })

    return {"green": green, "red": red}


def _delegate_to_r3():
    """Spawn the canonical r3 drift check and propagate exit code."""
    import subprocess  # noqa: PLC0415
    if not R3_SCRIPT.exists():
        print(f"r3 drift script not found at {R3_SCRIPT}", file=sys.stderr)
        return 1
    # Pass-through argv excluding our own --architecture-yaml flag
    passthrough = [a for a in sys.argv[1:] if a not in ("--architecture-yaml", "--json")]
    return subprocess.call([sys.executable, str(R3_SCRIPT)] + passthrough)


def main():
    parser = argparse.ArgumentParser(description="Zeus r3 drift check (top-level shim).")
    parser.add_argument("--architecture-yaml", action="store_true",
                        help="Scan architecture/*.yaml for cited-path drift (NEW; verdict §4.1 #3)")
    parser.add_argument("--json", action="store_true", help="JSON output (architecture-yaml mode only)")
    parser.add_argument("--phase", help="Pass-through to r3 drift check (default mode only)")
    args, _unknown = parser.parse_known_args()

    if not args.architecture_yaml:
        sys.exit(_delegate_to_r3())

    result = check_architecture_yaml()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        green_n = len(result.get("green", []))
        red_n = len(result.get("red", []))
        print(f"architecture/*.yaml drift check: {green_n} GREEN, {red_n} RED")
        for r in result.get("red", []):
            print(f"  RED: {r.get('yaml')}: {r.get('cite')} → {r.get('kind')} ({r.get('missing_path', r.get('detail',''))})")

    sys.exit(0 if not result.get("red") else 1)


if __name__ == "__main__":
    main()
