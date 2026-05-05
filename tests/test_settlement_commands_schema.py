# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md §10 T1A
"""Relationship test: settlement_commands DDL has exactly one definition site.

Invariant T1A-DDL-SINGLE-SOURCE: git grep for the CREATE TABLE statement
must return exactly one match and that match must be in
src/execution/settlement_commands.py.

Fails on HEAD 1116d827 (two sites: settlement_commands.py:28 and db.py:1398).
Passes after T1A B2 removes the inline DDL from db.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DDL_PATTERN = "CREATE TABLE IF NOT EXISTS settlement_commands"
CANONICAL_PATH = "src/execution/settlement_commands.py"


def test_settlement_commands_single_source_of_truth():
    result = subprocess.check_output(
        ["git", "grep", "-n", DDL_PATTERN, "--", "src/"],
        cwd=REPO_ROOT,
        text=True,
    )
    matches = [line for line in result.splitlines() if line.strip()]
    assert len(matches) == 1, (
        f"Expected exactly 1 DDL definition site for settlement_commands, "
        f"got {len(matches)}: {matches}"
    )
    assert matches[0].startswith(CANONICAL_PATH), (
        f"DDL definition must be in {CANONICAL_PATH}, got: {matches[0]}"
    )
