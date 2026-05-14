#!/usr/bin/env python3
# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §3 A8 (REV 4)
#   CRITIC_REVIEW_REV2 §3.2 / A8 AST writer-signature audit
"""CI hook: AST audit for writer-function connection-type signatures.

Detects src/ functions that write to Zeus DB tables without declaring
a typed connection parameter. P1 establishes the BASELINE of known violations
for P3 to fix. P1 scope: surface violations, not fix them.

Exit 0 = PASS (no new violations beyond baseline, or --baseline mode).
Exit 1 = FAIL (new violations found vs baseline).
Exit 2 = SETUP ERROR.

Usage:
    python3 scripts/check_writer_signature_typing.py [--verbose] [--baseline]

    --baseline: Print current violations (for establishing P1 baseline). Always exits 0.
    --verbose:  Print all checked functions, not just violations.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent

# DB writer function name patterns — functions that write to Zeus DB tables.
# Conservative subset: functions with "write_", "insert_", "record_", "save_",
# "_append", "_upsert", "_create", "_update" in name that take a "conn" param.
_WRITER_PATTERNS = (
    "write_",
    "insert_",
    "record_",
    "save_",
    "_append",
    "_upsert",
    "append_",
    "upsert_",
)

# Typed connection type names (P1 introduces these; P3 wires them to callsites).
_TYPED_CONN_TYPES = frozenset({
    "WorldConnection",
    "ForecastsConnection",
    "TradeConnection",
    "TypedConnection",
})

# P1 known-baseline violations: functions that write to DB but use untyped conn.
# P3 will fix these one by one. This list is the P1 snapshot; do NOT prune
# without fixing the underlying function signature.
#
# Format: "src/path/to/file.py::function_name"
# Generate fresh baseline via: python3 scripts/check_writer_signature_typing.py --baseline
_P1_BASELINE_VIOLATIONS: frozenset[str] = frozenset()  # populated below after scan


def _is_writer(name: str) -> bool:
    return any(pat in name for pat in _WRITER_PATTERNS)


def _has_conn_param(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function has a parameter named 'conn'."""
    all_args = (
        func.args.args
        + func.args.posonlyargs
        + func.args.kwonlyargs
        + ([func.args.vararg] if func.args.vararg else [])
        + ([func.args.kwarg] if func.args.kwarg else [])
    )
    return any(arg.arg == "conn" for arg in all_args)


def _conn_param_annotation(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return the string annotation for the 'conn' parameter, or None if unannotated."""
    all_args = func.args.args + func.args.posonlyargs + func.args.kwonlyargs
    for arg in all_args:
        if arg.arg == "conn" and arg.annotation is not None:
            return ast.unparse(arg.annotation)
    return None


def scan_violations(src_root: Path, verbose: bool = False) -> list[str]:
    """Return list of 'file::function' strings for untyped writer functions."""
    violations: list[str] = []

    for py_file in sorted(src_root.rglob("*.py")):
        rel = str(py_file.relative_to(_REPO_ROOT))
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, OSError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_writer(node.name):
                continue
            if not _has_conn_param(node):
                continue

            annotation = _conn_param_annotation(node)
            is_typed = annotation is not None and any(
                t in annotation for t in _TYPED_CONN_TYPES
            )

            if verbose:
                typed_str = f"typed={annotation}" if annotation else "UNTYPED"
                print(f"  {rel}::{node.name} conn={typed_str}")

            if not is_typed:
                violations.append(f"{rel}::{node.name}")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--baseline", action="store_true",
        help="Print current violations (establishes P1 baseline). Always exits 0."
    )
    args = parser.parse_args()

    src_root = _REPO_ROOT / "src"
    violations = scan_violations(src_root, verbose=args.verbose)

    if args.baseline:
        print(f"P1 BASELINE: {len(violations)} writer functions with untyped conn parameter")
        print("(P3 will fix these. Add to _P1_BASELINE_VIOLATIONS in this script.)")
        for v in sorted(violations):
            print(f"  {v}")
        return 0

    # Compare against P1 baseline.
    known = _P1_BASELINE_VIOLATIONS
    new_violations = [v for v in violations if v not in known]
    fixed = [v for v in known if v not in violations]

    if fixed:
        print(f"IMPROVEMENTS: {len(fixed)} previously-untyped writers are now typed:")
        for v in sorted(fixed):
            print(f"  + {v}")

    if new_violations:
        print(f"FAIL: {len(new_violations)} new untyped writer functions (not in P1 baseline):")
        for v in sorted(new_violations):
            print(f"  ! {v}")
        print("\nFIX: Add typed connection annotation to these functions, OR")
        print("add to _P1_BASELINE_VIOLATIONS if this is a known pre-P3 debt item.")
        return 1

    if args.verbose:
        print(f"PASS: {len(violations)} known-baseline violations, 0 new (P3 will fix)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
