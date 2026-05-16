#!/usr/bin/env python3
# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: WAVE_3_CRITIC.md MAJOR-1 fix — replace PR# column with verifiable SHA ranges
"""
One-off: replace the "Anchor PR / commit" column in docs/operations/INDEX.md
with git SHA ranges computed mechanically from `git log -- <dir>`.

Only rows whose anchor cell matches `PR #NN` (optionally prefixed with
"pre-", "branch:", etc.) are rewritten.  Rows with explicit SHA refs
(backtick-quoted hex), free-text anchors ("evidence packet", "superseded",
"workflow record", "planning packet", "operations run", "diagnostic",
"this PR", "DDD v2 commits", "(multi-commit)", "(planning artifacts)"),
or the column-header row are left untouched.

Output: atomic write (tmp → replace).
"""

import re
import subprocess
import sys
import os
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(REPO_ROOT, "docs", "operations", "INDEX.md")
OPS_DIR = os.path.join(REPO_ROOT, "docs", "operations")

# Pattern: PR# cells to replace (with optional prefix words)
PR_PATTERN = re.compile(r"^\s*PR\s*#\d+\s*$", re.IGNORECASE)

# Row pattern: | `dir/` | anchor | status | date |
ROW_PATTERN = re.compile(r"^\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|")


def get_sha_range(rel_dir: str) -> str:
    """Return 'FIRST_SHA..LAST_SHA' for files ever committed under rel_dir.
    Returns 'NO_DIR' if the directory does not exist or has no git history."""
    full_path = os.path.join(OPS_DIR, rel_dir)
    if not os.path.isdir(full_path):
        return "NO_DIR"

    git_path = "docs/operations/" + rel_dir.rstrip("/")

    # First commit touching this path
    first = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%h", "--reverse", "--", git_path],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout.strip().split("\n")[0].strip()

    # Last commit touching this path
    last = subprocess.run(
        ["git", "log", "--format=%h", "-1", "--", git_path],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout.strip()

    if not first and not last:
        # Try without --diff-filter (covers renames)
        first = subprocess.run(
            ["git", "log", "--format=%h", "--reverse", "--", git_path],
            cwd=REPO_ROOT, capture_output=True, text=True,
        ).stdout.strip().split("\n")[0].strip()
        last = subprocess.run(
            ["git", "log", "--format=%h", "-1", "--", git_path],
            cwd=REPO_ROOT, capture_output=True, text=True,
        ).stdout.strip()

    if not first and not last:
        return "NO_GIT_HISTORY"
    if first == last or not last:
        return f"`{first or last}`"
    return f"`{first}..{last}`"


def extract_dir(cell: str) -> str | None:
    """Pull the backtick-quoted dir name from the first table cell."""
    m = re.search(r"`([^`]+/)`", cell)
    if m:
        return m.group(1)
    # Multi-dir rows like `task_2026-05-05_object_invariance_wave5/` through `wave8/`
    # Use the first dir found
    m = re.search(r"`([^`]+/)`", cell)
    return m.group(1) if m else None


def should_replace(anchor_cell: str) -> bool:
    """Return True only if this anchor cell is a bare PR# reference."""
    stripped = anchor_cell.strip()
    # Keep rows with explicit SHA already (backtick + hex)
    if re.search(r"`[0-9a-f]{6,}`", stripped):
        return False
    # Keep free-text non-PR anchors
    free_text_keywords = [
        "evidence packet", "superseded", "workflow record", "planning packet",
        "operations run", "diagnostic", "this PR", "DDD v2 commits",
        "(multi-commit)", "(planning artifacts)", "branch:", "pre-PR",
    ]
    for kw in free_text_keywords:
        if kw.lower() in stripped.lower():
            return False
    # Replace if it looks like "PR #NN" (with optional modifiers)
    if re.search(r"PR\s*#\d+", stripped, re.IGNORECASE):
        return True
    return False


def process():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    rewritten = []
    replaced_count = 0
    no_dir_count = 0

    for line in lines:
        m = ROW_PATTERN.match(line)
        if not m:
            rewritten.append(line)
            continue

        dir_cell, anchor_cell, status_cell, date_cell = m.group(1), m.group(2), m.group(3), m.group(4)

        if not should_replace(anchor_cell):
            rewritten.append(line)
            continue

        rel_dir = extract_dir(dir_cell)
        if rel_dir is None:
            rewritten.append(line)
            continue

        sha_range = get_sha_range(rel_dir)
        if sha_range in ("NO_DIR", "NO_GIT_HISTORY"):
            no_dir_count += 1
            new_anchor = f" {sha_range} "
        else:
            new_anchor = f" {sha_range} "

        new_line = f"|{dir_cell}|{new_anchor}|{status_cell}|{date_cell}|"
        # Preserve trailing newline from original
        if line.endswith("\n") and not new_line.endswith("\n"):
            new_line += "\n"
        rewritten.append(new_line)
        replaced_count += 1

    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(rewritten)
    os.replace(tmp, INDEX_PATH)

    print(f"Done. Replaced {replaced_count} anchor cells. NO_DIR/NO_GIT_HISTORY: {no_dir_count}.")
    print(f"Line count: before={len(lines)}, after={len(rewritten)}")


if __name__ == "__main__":
    process()
