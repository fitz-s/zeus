#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-07-28
# Authority basis: AGENTS.md §5 live branch law; worktree lifecycle repair
"""Retired compatibility entrypoint for the forbidden direct-live merge path.

This file remains so an old worker receives an explicit, safe refusal instead
of silently changing the live checkout. It never invokes git and never removes
a worktree. Land a verified committed change with a fast-forward
``git push origin HEAD:live`` or a merged PR (AGENTS.md §5).
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "MERGE_REFUSED: agent_worktree_merge.py is retired. It never mutates "
        "live or removes worktrees. Report the committed SHA to the landing "
        "authority; land with a fast-forward `git push origin HEAD:live` or a merged PR.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
