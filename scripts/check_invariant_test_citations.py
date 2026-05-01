#!/usr/bin/env python3
# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P1-8 +
#                  repo_review_2026-05-01 SYNTHESIS K-A (two-ring enforcement)
# Purpose: validate every `tests:` citation in architecture/invariants.yaml
#          resolves to a real `def test_*` in the cited file. Catches the
#          INV-05 doc-only failure mode at CI time, before architect/critic
#          review surfaces it as a P0.
"""Invariant citation consistency check.

Why this exists
---------------
On 2026-05-01 the multi-lane review found INV-05's cited test
(`tests/test_architecture_contracts.py::test_risk_actions_exist_in_schema`)
did not exist anywhere in the repo. Three independent agents (architect /
critic-opus / test-engineer) flagged it as a P0. The drift was invisible
to grep-based audits because the YAML cite parses but the resolution step
(does the test_name actually exist?) was never automated.

This script automates that resolution step. For every invariant with a
`tests:` array in architecture/invariants.yaml, parse each `path::test_name`
citation and verify:

  1. `path` exists and is readable.
  2. The file contains a top-level `def test_name(` definition.
  3. (Lenient) Class-method tests (e.g. `Class::method`) are NOT supported
     yet — pytest's collection model is `path::function`. If that pattern
     emerges, extend this script before adding to invariants.yaml.

Exit codes
----------
0 — every citation resolves.
2 — at least one citation fails to resolve. Stderr lists each failure.

CLI
---
    python3 scripts/check_invariant_test_citations.py            # full check, exit 0/2
    python3 scripts/check_invariant_test_citations.py --json     # JSON report on stdout

Test coverage
-------------
A pytest wrapper at `tests/test_invariant_citations.py` calls the same
resolver and asserts zero unresolved citations. Adding a broken citation
to invariants.yaml fails that test (pre-commit baseline blocks the commit).

Scope
-----
Only validates the `tests:` field. Other enforcement fields
(`semgrep_rule_ids`, `scripts`, `schema`, `negative_constraints`) are out of
scope for this script — they need their own consistency checks (filed as
follow-up in SYNTHESIS.md §3).
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INVARIANTS_YAML = REPO_ROOT / "architecture" / "invariants.yaml"


@dataclass(frozen=True)
class CitationFailure:
    inv_id: str
    citation: str
    reason: str

    def render(self) -> str:
        return f"  {self.inv_id} -> {self.citation}\n      reason: {self.reason}"


def _collect_pytest_node_ids(path: Path) -> set[str]:
    """Parse a Python file and return the set of pytest-collectible node IDs
    (without the file-path prefix).

    Two shapes are supported, matching pytest's actual collection grammar:
      - `test_X`              for top-level `def test_X(...)`
      - `TestC::test_X`       for `class TestC: def test_X(...)`

    Method-style cites (Class::method) are pytest-canonical and used
    extensively in this repo. Free-function cites are also pytest-canonical.
    Nested classes / parametrize IDs / fixture-scoped collection are NOT
    supported here — keep invariants.yaml citations to the two simple shapes.

    Uses the AST so we don't get fooled by commented-out defs, string
    literals, or test names referenced (but not defined).
    """
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise CitationFailureError(
            citation=str(path),
            reason=f"could not parse file: {exc}",
        )

    ids: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                ids.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if member.name.startswith("test_"):
                        ids.add(f"{node.name}::{member.name}")
    return ids


class CitationFailureError(Exception):
    def __init__(self, *, citation: str, reason: str) -> None:
        super().__init__(f"{citation}: {reason}")
        self.citation = citation
        self.reason = reason


def _resolve_one(inv_id: str, citation: str) -> CitationFailure | None:
    """Return None on success, a CitationFailure on failure.

    Accepts pytest-canonical citations of two shapes:
      - `path::test_name`               (top-level free function)
      - `path::TestClass::test_name`    (method on Test* class)

    Bare `path` (no `::`) is rejected — every cite must be a collectible
    node id, not just a file path.
    """
    if "::" not in citation:
        return CitationFailure(
            inv_id=inv_id,
            citation=citation,
            reason=(
                "malformed citation: every `tests:` entry must be a pytest "
                "node id, not just a file path. Use `path::test_name` for "
                "top-level tests or `path::TestClass::test_name` for "
                "class-method tests."
            ),
        )

    head, _, rest = citation.partition("::")
    path_part = head
    node_id = rest  # may itself contain `::` for class-method form

    if not path_part or not node_id:
        return CitationFailure(
            inv_id=inv_id,
            citation=citation,
            reason="malformed citation (empty path or test node id)",
        )

    target = REPO_ROOT / path_part
    if not target.exists():
        return CitationFailure(
            inv_id=inv_id,
            citation=citation,
            reason=f"file does not exist: {path_part}",
        )
    if not target.is_file():
        return CitationFailure(
            inv_id=inv_id,
            citation=citation,
            reason=f"path is not a regular file: {path_part}",
        )

    try:
        collectible = _collect_pytest_node_ids(target)
    except CitationFailureError as exc:
        return CitationFailure(inv_id=inv_id, citation=citation, reason=exc.reason)

    if node_id in collectible:
        return None

    # Reject deeper nesting (parametrize, nested class) — keep cites canonical.
    if node_id.count("::") > 1:
        return CitationFailure(
            inv_id=inv_id,
            citation=citation,
            reason=(
                "deeper than `Class::method` nesting not supported; keep "
                "invariants.yaml citations to top-level or one-class-deep "
                "shapes only"
            ),
        )

    # Build a short "did you mean" hint using a fuzzy substring match.
    needle = node_id.split("::")[-1][:10]
    suggestions = sorted(c for c in collectible if needle and needle in c)[:5]
    return CitationFailure(
        inv_id=inv_id,
        citation=citation,
        reason=(
            f"no pytest node `{node_id}` in {path_part}. File exposes "
            f"{len(collectible)} collectible node(s); nearest matches: "
            f"{suggestions or '<none>'}. Did the test get renamed, moved, "
            f"or wrapped in a Test* class? If you wrote a method-style cite, "
            f"check the class name."
        ),
    )


def collect_failures() -> list[CitationFailure]:
    """Scan invariants.yaml and return all unresolved citations."""
    if not INVARIANTS_YAML.exists():
        raise FileNotFoundError(f"invariants.yaml not found at {INVARIANTS_YAML}")
    data = yaml.safe_load(INVARIANTS_YAML.read_text()) or {}
    invariants = data.get("invariants") or []

    failures: list[CitationFailure] = []
    for inv in invariants:
        inv_id = inv.get("id", "<unknown>")
        eb = inv.get("enforced_by") or {}
        tests: Iterable[str] = eb.get("tests") or ()
        for cit in tests:
            failure = _resolve_one(inv_id, cit)
            if failure is not None:
                failures.append(failure)
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON report on stdout instead of human text on stderr",
    )
    args = parser.parse_args(argv)

    failures = collect_failures()

    if args.json:
        report = {
            "ok": not failures,
            "failure_count": len(failures),
            "failures": [
                {"inv_id": f.inv_id, "citation": f.citation, "reason": f.reason}
                for f in failures
            ],
        }
        print(json.dumps(report, indent=2))
        return 0 if not failures else 2

    if not failures:
        print("[check_invariant_test_citations] OK — every cited test resolves.")
        return 0

    print(
        f"[check_invariant_test_citations] BLOCKED — {len(failures)} unresolved "
        f"citation(s) in architecture/invariants.yaml:",
        file=sys.stderr,
    )
    for failure in failures:
        print(failure.render(), file=sys.stderr)
    print(
        "\nFix the citation OR update architecture/invariants.yaml to remove the "
        "stale `tests:` entry. A doc-only invariant with no test antibody is "
        "an INV-05-shaped P0 finding waiting to surface in the next review.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
