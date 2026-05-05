#!/usr/bin/env python3
# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/PROPOSALS_2026-05-04.md P1 + capsule
#                  feedback from PR #55+#56 merge collision.
"""Detect identity-bearing class collisions between open PRs.

Designed to be invoked from `.github/workflows/pr_identity_collision_check.yml`
on `pull_request: opened, synchronize, ready_for_review`.  Posts a
warning when:

  1. THIS PR ADDS a top-level class declaration (not a modification).
  2. ANOTHER open PR ADDS a class with the SAME name in an
     identity-bearing file (see ``IDENTITY_FILE_PATTERNS``).
  3. The two PRs have DIFFERENT base commits (so they are not stacked
     where one is intentionally building on the other).

Identity-bearing files are scoped via the regex allowlist below.
Outside that allowlist, two PRs adding ``class Foo`` in different
files (e.g., test helpers, throwaway scripts) is normal and benign.

The check is ADVISORY: exit 0 even on collision.  Output is written
to ``--output-file`` for the workflow to post as a PR comment.
False positives are tolerable; the cost of missing a real collision
(see PR #55 vs #56 ForecastCalibrationDomain) is much higher.

Usage:
    python scripts/check_pr_identity_collisions.py \\
        --this-pr 60 --repo fitz-s/zeus --output-file /tmp/warning.md
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Iterable


# Identity-bearing file allowlist.  Extending this list is cheap (more
# false positives, never false negatives within scope); pruning is
# expensive (false negatives are the catastrophic case).  Defaults
# scoped to the surfaces where PR #55+#56 collided plus the obvious
# adjacent type/contract zones.
IDENTITY_FILE_PATTERNS = [
    r"^src/types/",
    r"^src/contracts/",
    r"^src/calibration/forecast_",
    r"^src/calibration/manager\.py$",
    r"^src/strategy/strategy_profile",
    r"^src/strategy/market_phase",
    r"^src/strategy/oracle_",
    r"^src/data/.*registry.*\.py$",
]


def _file_matches_identity_scope(path: str) -> bool:
    return any(re.match(pat, path) for pat in IDENTITY_FILE_PATTERNS)


_CLASS_DECL_RE = re.compile(r"^\+\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[(:]")


def added_classes_in_diff(diff: str) -> set[tuple[str, str]]:
    """Parse a unified diff; return ``{(file, classname)}`` for ADDED classes.

    "Added" = line begins with `+` (excluding `+++` headers).  Only
    files matching ``IDENTITY_FILE_PATTERNS`` are considered.

    Caveats:
      - Modifications inside an existing class body would NOT be
        flagged (the `class X:` line stays unchanged).  Intentional —
        that's normal collaboration, not collision.
      - A class moved between files would show as both delete + add;
        we'd warn on the add.  Also intentional — moves are worth a
        sanity check.
    """
    out: set[tuple[str, str]] = set()
    current_file: str | None = None
    for line in diff.splitlines():
        # Track the file each hunk belongs to.
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/"):]
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            # Other diff headers; ignore.
            continue
        if current_file is None:
            continue
        if not _file_matches_identity_scope(current_file):
            continue
        m = _CLASS_DECL_RE.match(line)
        if m:
            out.add((current_file, m.group(1)))
    return out


def _gh(*args: str) -> str:
    """Run a gh CLI command and return stdout, raising on nonzero exit."""
    return subprocess.check_output(["gh", *args], text=True)


def _list_other_open_prs(this_pr: int, repo: str) -> list[dict]:
    raw = _gh(
        "pr", "list", "--state", "open",
        "--json", "number,headRefOid,headRefName,baseRefOid",
        "--repo", repo,
        "--limit", "50",
    )
    return [pr for pr in json.loads(raw) if pr["number"] != this_pr]


def _pr_base(this_pr: int, repo: str) -> str:
    raw = _gh(
        "pr", "view", str(this_pr),
        "--json", "baseRefOid",
        "--repo", repo,
    )
    return json.loads(raw)["baseRefOid"]


def _pr_diff(pr_number: int, repo: str) -> str:
    return _gh("pr", "diff", str(pr_number), "--repo", repo)


def _format_warning(
    this_pr: int,
    collisions: list[tuple[dict, set[tuple[str, str]]]],
) -> str:
    lines = [
        "<!-- identity-collision-warning -->",
        f"## Identity-collision risk on PR #{this_pr}",
        "",
        "This PR and one or more other open PRs add a class declaration "
        "with the same name in an identity-bearing file.  Two PRs each "
        "thinking they own the canonical definition is the structural "
        "fault that bit PR #55 + PR #56 with `ForecastCalibrationDomain` "
        "on 2026-05-04.",
        "",
        "**Collisions detected:**",
        "",
    ]
    for pr, overlap in collisions:
        for file, classname in sorted(overlap):
            lines.append(
                f"- `{classname}` in `{file}` — also added by "
                f"[PR #{pr['number']}]({pr['headRefName']})"
            )
    lines.extend([
        "",
        "**Resolve before merge:**",
        "",
        "1. Rebase one PR onto the other so only one branch lands the "
        "class definition.",
        "2. OR rename one to a non-conflicting name with explicit ownership "
        "documented.",
        "",
        "_This check is advisory.  Closing the warning without resolving "
        "is permitted but will be visible in the merge audit._",
    ])
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--this-pr", type=int, required=True,
                        help="PR number being checked.")
    parser.add_argument("--repo", required=True,
                        help="Owner/name, e.g. fitz-s/zeus.")
    parser.add_argument("--output-file", default=None,
                        help="If set, write the warning markdown here "
                             "for a downstream workflow step to post "
                             "as a PR comment.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    this_diff = _pr_diff(args.this_pr, args.repo)
    this_classes = added_classes_in_diff(this_diff)
    if not this_classes:
        print(f"PR #{args.this_pr} adds no identity-bearing classes; nothing to check.")
        return 0

    print(f"PR #{args.this_pr} adds {len(this_classes)} class(es) in "
          f"identity-bearing files; checking against other open PRs...")

    this_base = _pr_base(args.this_pr, args.repo)
    others = _list_other_open_prs(args.this_pr, args.repo)

    collisions: list[tuple[dict, set[tuple[str, str]]]] = []
    for pr in others:
        if pr.get("headRefOid") == this_base:
            # Stacked: the other PR's HEAD is exactly our base commit,
            # meaning THIS PR intentionally builds on top of that PR.
            # A same-name class is expected (inherited), not a collision.
            # Parallel PRs that merely share the same base commit are NOT
            # skipped — that is the primary collision scenario this tool
            # exists to catch.
            continue
        try:
            other_diff = _pr_diff(pr["number"], args.repo)
        except subprocess.CalledProcessError:
            continue
        other_classes = added_classes_in_diff(other_diff)
        overlap = this_classes & other_classes
        if overlap:
            collisions.append((pr, overlap))

    if not collisions:
        print("No identity collisions detected.")
        return 0

    warning = _format_warning(args.this_pr, collisions)
    print(warning)
    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(warning)
    return 0  # advisory — never fail the workflow.


if __name__ == "__main__":
    sys.exit(main())
