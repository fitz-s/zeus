#!/usr/bin/env python3
# Created: 2026-06-29
# Last reused or audited: 2026-07-28
# Authority basis: AGENTS.md §5 live branch law; worktree lifecycle repair
"""Retired compatibility entrypoint for the forbidden direct-live integrator.

The former implementation could fast-forward a caller-selected main checkout
and forcibly remove staging worktrees. ``live`` now accepts only a verified
hot-pick or merged PR. This retained entrypoint deliberately performs neither
operation, so an old invocation fails closed without touching the 24/7 tree.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "INTEGRATION_REFUSED: multiagent_integrator.py is retired. It never "
        "mutates live or removes worktrees. Validate committed work in its "
        "worktree, then use a verified git cherry-pick or a PR into live.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
