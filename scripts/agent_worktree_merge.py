#!/usr/bin/env python3
# Lifecycle: retired=2026-07-23; last_reviewed=2026-07-23
# Purpose: fail closed for the retired direct-main-tree merge path.
# Authority basis: AGENTS.md §5; docs/operations/current/plans/live_branch_workflow_2026-07-20.md.
"""Retired direct-main-tree worktree merger.

This command formerly fast-forwarded a worktree tip into the checkout that
daemons execute from. That is a third landing lane and is now forbidden. Keep
the small stub so older agents receive an actionable refusal instead of a
silent direct merge.
"""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "MERGE_REFUSED: agent_worktree_merge is retired; it would mutate the "
        "live checkout. Commit and prove the worktree, then land only through "
        "the privileged pick lane or a PR merged into live. After absorption, "
        "run `python3 scripts/worktree_doctor.py closeout` from the worktree.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
