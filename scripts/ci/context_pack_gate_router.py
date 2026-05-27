#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase E
#                  architecture/context_pack_schema.yaml
"""
Gate router: read a Context Pack JSON bundle and emit the set of CI gates
(relationship tests + static gates) selected by the matched surfaces.

This is the Phase E enforcement primitive that the
.github/workflows/topology-context-required.yml workflow consumes. It
turns "this PR touched these surfaces → these are the failure chains →
this is the substrate that proves the surfaces still hold" into a
concrete shell-consumable list of test paths.

Input:
    A JSON bundle in the shape produced by
    scripts/topology_doctor_context_pack.py (--format json), i.e.:
        {
          "schema_version": 1,
          "matched_files": [...],
          "packs": [
            {
              "id": "...",
              "required_relationship_tests": [
                {"path": "...", "blocking": true, ...},
                ...
              ],
              "required_static_gates": [
                {"id": "...", "script": "...", "blocking": true},
                ...
              ],
              ...
            },
            ...
          ],
          ...
        }

Output (one of):
    --emit-tests <path>       newline-delimited test paths
    --emit-gate-plan <path>   JSON gate plan {tests, static_gates, packs}
    (default to stdout if no output flag given)

Filters:
    --blocking-only           only emit gates with blocking: true
    --dedupe                  (default true) collapse duplicate paths

Exit codes:
    0 — success, with or without emitted gates
    1 — input bundle malformed
    2 — IO error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_bundle(path: str | None) -> dict[str, Any]:
    """Load Context Pack bundle from a file path, or stdin if path is '-' / None."""
    if path is None or path == "-":
        text = sys.stdin.read()
    else:
        text = Path(path).read_text()
    if not text.strip():
        return {"schema_version": 1, "packs": []}
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"bundle is not valid JSON: {e}") from e


def select_tests(
    bundle: dict[str, Any],
    *,
    blocking_only: bool = False,
    dedupe: bool = True,
) -> list[str]:
    """
    Return relationship test paths from the bundle, in first-occurrence
    order. When blocking_only=True, omit non-blocking entries. Dedup
    preserves first occurrence.
    """
    seen: set[str] = set()
    out: list[str] = []
    for pack in (bundle.get("packs") or []):
        for entry in (pack.get("required_relationship_tests") or []):
            if blocking_only and not entry.get("blocking", False):
                continue
            path = entry.get("path")
            if not path:
                continue
            if dedupe:
                if path in seen:
                    continue
                seen.add(path)
            out.append(path)
    return out


def select_static_gates(
    bundle: dict[str, Any],
    *,
    blocking_only: bool = False,
    dedupe: bool = True,
) -> list[dict[str, Any]]:
    """Return static gate entries (each {id, script, blocking, reason})."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for pack in (bundle.get("packs") or []):
        for entry in (pack.get("required_static_gates") or []):
            if blocking_only and not entry.get("blocking", True):
                continue
            gid = entry.get("id")
            if not gid:
                continue
            if dedupe:
                if gid in seen:
                    continue
                seen.add(gid)
            out.append(entry)
    return out


def build_gate_plan(
    bundle: dict[str, Any],
    *,
    blocking_only: bool = False,
) -> dict[str, Any]:
    """
    Build a comprehensive gate plan: which tests/scripts to run for this
    bundle, in deterministic order.
    """
    return {
        "schema_version": 1,
        "matched_packs": [p["id"] for p in (bundle.get("packs") or []) if "id" in p],
        "matched_files": list(bundle.get("matched_files") or []),
        "tests": select_tests(bundle, blocking_only=blocking_only, dedupe=True),
        "static_gates": select_static_gates(bundle, blocking_only=blocking_only, dedupe=True),
        "failure_chains": _collect_failure_chains(bundle),
        "review_required": list(bundle.get("review_required") or []),
    }


def _collect_failure_chains(bundle: dict[str, Any]) -> list[str]:
    """Return unique failure chain IDs across packs (first-occurrence order)."""
    seen: set[str] = set()
    out: list[str] = []
    for pack in (bundle.get("packs") or []):
        for fc in (pack.get("failure_chains") or []):
            fid = fc.get("id") if isinstance(fc, dict) else fc
            if not fid or fid in seen:
                continue
            seen.add(fid)
            out.append(fid)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--context-packs",
        default=None,
        help="Path to context_packs JSON bundle (or '-' for stdin; default stdin)",
    )
    p.add_argument(
        "--emit-tests",
        default=None,
        help="Write selected test paths (newline-delimited) to this file",
    )
    p.add_argument(
        "--emit-gate-plan",
        default=None,
        help="Write full gate plan JSON to this file",
    )
    p.add_argument(
        "--blocking-only",
        action="store_true",
        help="Only emit blocking=true entries (default: emit all)",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="stdout format when no --emit-* flags given (default: text)",
    )
    args = p.parse_args(argv)

    try:
        bundle = _load_bundle(args.context_packs)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1 if isinstance(e, ValueError) else 2

    plan = build_gate_plan(bundle, blocking_only=args.blocking_only)

    if args.emit_tests:
        Path(args.emit_tests).write_text("\n".join(plan["tests"]) + ("\n" if plan["tests"] else ""))
    if args.emit_gate_plan:
        Path(args.emit_gate_plan).write_text(json.dumps(plan, indent=2))

    # Stdout: text mode prints tests one per line; json mode prints full plan
    if not args.emit_tests and not args.emit_gate_plan:
        if args.format == "json":
            print(json.dumps(plan, indent=2))
        else:
            for t in plan["tests"]:
                print(t)
    else:
        # Brief confirmation when writing to files
        print(
            f"OK: {len(plan['tests'])} test(s), {len(plan['static_gates'])} "
            f"static gate(s), {len(plan['matched_packs'])} pack(s), "
            f"{len(plan['failure_chains'])} failure chain(s)."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
