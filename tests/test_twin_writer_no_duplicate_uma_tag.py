# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ADDENDUM.md
"""
Relationship test R-1.3: AST audit — twin writer consolidation completeness.

SCAFFOLD — test body not yet implemented (xfail pending implementation).

RELATIONSHIP INVARIANT (cross-module):
    After PR 1 implementation, the literal string "harvester_live_uma_vote"
    MUST NOT appear in either of the two historical write paths:
      - src/execution/harvester.py
      - src/ingest/harvester_truth_writer.py
    The string is only permitted to appear in:
      - scripts/backfill_settlements_v2_era_provenance.py (compatibility shim comments)
      - scripts/audit_settlements_v2_era_provenance.py (query string)
      - tests/test_inv_era_provenance_post_cutover_count.py (antibody query)

    This is an AST/text audit test, not a runtime test. It statically verifies
    that consolidation was complete — no partial migration leaving a twin path.

TEST (SCAFFOLD — body is docstring only):

R-1.3 (AST audit — no 'harvester_live_uma_vote' in source paths post-consolidation):
    Use ast.parse() or grep-equivalent on:
      - src/execution/harvester.py
      - src/ingest/harvester_truth_writer.py
    Assert that the literal string 'harvester_live_uma_vote' does NOT appear
    in either file. Permitted locations (not checked by this test):
      - scripts/backfill_settlements_v2_era_provenance.py
      - tests/test_inv_era_provenance_post_cutover_count.py

    NOTE: This test is expected to FAIL before PR 1 implementation (the
    string currently exists at harvester.py:1338 and harvester_truth_writer.py:556).
    The xfail marker is removed when those lines are consolidated in the
    implementation PR.
"""
import ast
import pathlib

import pytest


# Source files that must NOT contain 'harvester_live_uma_vote' after consolidation
_SOURCE_PATHS_TO_AUDIT = [
    "src/execution/harvester.py",
    "src/ingest/harvester_truth_writer.py",
]

_FORBIDDEN_LITERAL = "harvester_live_uma_vote"


def test_r1_3_no_harvester_live_uma_vote_in_twin_paths():
    """R-1.3: The literal 'harvester_live_uma_vote' must not appear in harvester.py
    or harvester_truth_writer.py after twin writer consolidation in PR 1.

    Uses ast.walk to check all string constants (Constant nodes) in the parsed AST
    of each file. Also checks non-AST string literals in comments via line scan
    as a belt-and-suspenders measure.
    """
    repo_root = pathlib.Path(__file__).parent.parent
    violations: list[str] = []

    for rel_path in _SOURCE_PATHS_TO_AUDIT:
        source_file = repo_root / rel_path
        source_text = source_file.read_text(encoding="utf-8")

        # AST check: string constants
        tree = ast.parse(source_text, filename=rel_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _FORBIDDEN_LITERAL in node.value:
                    violations.append(
                        f"{rel_path}:{node.lineno}: AST constant contains '{_FORBIDDEN_LITERAL}'"
                    )

        # Line scan: comments and f-strings (not always caught by AST)
        for lineno, line in enumerate(source_text.splitlines(), start=1):
            if _FORBIDDEN_LITERAL in line:
                if not any(v.startswith(f"{rel_path}:{lineno}") for v in violations):
                    violations.append(
                        f"{rel_path}:{lineno}: line contains '{_FORBIDDEN_LITERAL}'"
                    )

    assert not violations, (
        f"Twin writer consolidation incomplete. Found '{_FORBIDDEN_LITERAL}' in source paths:\n"
        + "\n".join(violations)
    )
