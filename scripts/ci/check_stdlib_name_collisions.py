#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml#blocking_structural:stdlib_name_collision_gate
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Prevent PR #306 recurrence: Python files inside imported packages must not
collide with stdlib module names.

PR #306 anchor: scripts/topology_v_next/dataclasses.py collided
`dataclasses` (stdlib). `from dataclasses import dataclass` inside that
package resolved to the local file → ImportError on every import path
that touched the package. The fix was renaming to topology_models.py.

This gate prevents the regression by walking each `scripts/` subpackage
that contains an `__init__.py` and rejecting any file whose stem matches
a stdlib module name.

Scoped to `scripts/**` by default (where the PR #306 hazard occurred).
Pass --include-paths to broaden.

Exit codes:
    0 — no name collision detected
    1 — one or more collided names found
    2 — IO error
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]

# Common stdlib module names that have caused real import collisions.
# Curated rather than auto-detecting sys.stdlib_module_names so the rule
# stays stable across Python versions and intentional new conflicts get
# explicit review.
_HIGH_RISK_STDLIB_NAMES = frozenset(
    {
        "dataclasses",
        "typing",
        "collections",
        "functools",
        "itertools",
        "pathlib",
        "json",
        "yaml",  # not stdlib but always imported as if it were
        "datetime",
        "time",
        "calendar",
        "subprocess",
        "argparse",
        "logging",
        "asyncio",
        "threading",
        "multiprocessing",
        "queue",
        "os",
        "sys",
        "re",
        "math",
        "random",
        "statistics",
        "hashlib",
        "base64",
        "io",
        "csv",
        "sqlite3",
        "http",
        "urllib",
        "ssl",
        "socket",
        "unittest",
        "abc",
        "enum",
        "inspect",
        "tokenize",
        "ast",
    }
)


def _find_python_packages(root: Path) -> Iterable[Path]:
    """Yield directories that contain an __init__.py (i.e. importable packages)."""
    for init in root.rglob("__init__.py"):
        yield init.parent


def find_stdlib_name_collisions(repo_root: Path, scoped_dirs: list[str]) -> list[dict]:
    """
    Walk each `scoped_dir` and yield name-collision findings.
    Finding: {"path": str, "collides_with": str}.
    """
    findings: list[dict] = []
    for scope in scoped_dirs:
        scope_path = repo_root / scope
        if not scope_path.exists():
            continue
        for pkg_dir in _find_python_packages(scope_path):
            for child in pkg_dir.iterdir():
                if not child.is_file():
                    continue
                if not child.suffix == ".py":
                    continue
                if child.name == "__init__.py":
                    continue
                stem = child.stem
                if stem in _HIGH_RISK_STDLIB_NAMES:
                    findings.append(
                        {
                            "path": str(child.relative_to(repo_root)),
                            "collides_with": stem,
                        }
                    )
    return findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
    )
    p.add_argument(
        "--include-paths",
        nargs="+",
        default=["scripts"],
        help="Subtrees to scan (default: scripts)",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo_root)
    findings = find_stdlib_name_collisions(repo, args.include_paths)

    if args.json:
        import json
        print(json.dumps({"findings": findings, "count": len(findings)}, indent=2))
    else:
        if not findings:
            print(
                "OK: no Python file inside an importable package collides with a "
                "high-risk stdlib name."
            )
        else:
            print(f"FAIL: {len(findings)} stdlib-name-collision file(s):")
            for f in findings:
                print(f"  {f['path']} collides with stdlib `{f['collides_with']}`")
            print()
            print(
                "PR #306 reproduced this class of bug. Rename the file (e.g. "
                "dataclasses.py → topology_models.py)."
            )

    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
