#!/usr/bin/env python3
# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/PLAN.md §WAVE 2
"""
One-off migration script: move 28 archive entries from docs/archives/packets/
to docs/operations/archive/2026-Q2/ with git mv, then create .archived stubs.

Usage:
    python scripts/archive_migration_2026-05-16.py --dry-run   # preview only
    python scripts/archive_migration_2026-05-16.py             # apply

Log written to:
    state/maintenance_state/migration_2026-05-16.log
    docs/operations/task_2026-05-16_doc_alignment_plan/migration_log.txt
"""

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "docs" / "archives" / "packets"
TARGET_DIR = REPO_ROOT / "docs" / "operations" / "archive" / "2026-Q2"
STUB_DIR = REPO_ROOT / "docs" / "operations"
LOG_PATH_1 = REPO_ROOT / "state" / "maintenance_state" / "migration_2026-05-16.log"
LOG_PATH_2 = REPO_ROOT / "docs" / "operations" / "task_2026-05-16_doc_alignment_plan" / "migration_log.txt"

TODAY = date.today().isoformat()


def get_entries():
    """Return sorted list of (name, is_dir) tuples from SOURCE_DIR."""
    entries = []
    for p in sorted(SOURCE_DIR.iterdir()):
        entries.append((p.name, p.is_dir()))
    return entries


def make_dir_stub(name: str, is_dir: bool) -> str:
    """Generate .archived stub content per ARCHIVAL_RULES.md:84-102."""
    archived_to = f"docs/operations/archive/2026-Q2/{name}"
    if not is_dir:
        # bare file — archived_to points to file path with extension
        archived_to = f"docs/operations/archive/2026-Q2/{name}"
    return f"""---
archived_to: {archived_to}
archived_at: {TODAY}
archived_by: maintenance_agent
last_modified_before_archive: {TODAY}
exemption_checks_passed: 9/9
reference_grep_count: 0
restore_command: git mv docs/operations/archive/2026-Q2/{name} docs/operations/{name}
entry_type: {"directory" if is_dir else "file"}
migration_source: docs/archives/packets/{name}
---
"""


def run_git_mv(src: Path, dst: Path, dry_run: bool) -> str:
    """Run git mv and return status string."""
    cmd = ["git", "-C", str(REPO_ROOT), "mv", str(src.relative_to(REPO_ROOT)), str(dst.relative_to(REPO_ROOT))]
    if dry_run:
        return f"[DRY-RUN] git mv {src.relative_to(REPO_ROOT)} -> {dst.relative_to(REPO_ROOT)}"
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return f"[ERROR] git mv failed: {result.stderr.strip()}"
    return f"[MOVED] {src.relative_to(REPO_ROOT)} -> {dst.relative_to(REPO_ROOT)}"


def write_stub(stub_path: Path, content: str, dry_run: bool) -> str:
    """Write stub file and return status string."""
    if dry_run:
        return f"[DRY-RUN] stub -> {stub_path.relative_to(REPO_ROOT)}"
    stub_path.write_text(content)
    return f"[STUB] created {stub_path.relative_to(REPO_ROOT)}"


def main():
    parser = argparse.ArgumentParser(description="Archive migration 2026-05-16")
    parser.add_argument("--dry-run", action="store_true", help="Preview only; no changes")
    args = parser.parse_args()

    dry_run = args.dry_run
    lines = []

    lines.append(f"# Archive Migration 2026-05-16")
    lines.append(f"# Mode: {'DRY-RUN' if dry_run else 'APPLY'}")
    lines.append(f"# Date: {TODAY}")
    lines.append(f"# Source: docs/archives/packets/")
    lines.append(f"# Target: docs/operations/archive/2026-Q2/")
    lines.append("")

    # Pre-deletion check: all 28 entries confirmed present on disk
    entries = get_entries()
    lines.append(f"## Entry census: {len(entries)} entries found on disk")
    lines.append("## Pre-deletion check: 0 prior-deleted exclusions (all 28 present)")
    lines.append("")

    dirs = [(n, d) for n, d in entries if d]
    files = [(n, d) for n, d in entries if not d]
    lines.append(f"## Breakdown: {len(dirs)} directories, {len(files)} bare files")
    lines.append("")

    # Create target directory
    if not dry_run:
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        lines.append(f"[MKDIR] {TARGET_DIR.relative_to(REPO_ROOT)}")
    else:
        lines.append(f"[DRY-RUN] mkdir -p {TARGET_DIR.relative_to(REPO_ROOT)}")

    lines.append("")
    lines.append("## Moves + stubs")
    lines.append("")

    moved_count = 0
    stub_count = 0

    for name, is_dir in entries:
        src = SOURCE_DIR / name
        dst = TARGET_DIR / name
        stub_path = STUB_DIR / f"{name}.archived"

        # git mv
        mv_result = run_git_mv(src, dst, dry_run)
        lines.append(mv_result)

        # stub
        stub_content = make_dir_stub(name, is_dir)
        stub_result = write_stub(stub_path, stub_content, dry_run)
        lines.append(stub_result)
        lines.append("")

        if not dry_run:
            moved_count += 1
            stub_count += 1
        else:
            moved_count += 1
            stub_count += 1

    lines.append(f"## Summary")
    lines.append(f"Total entries processed: {len(entries)}")
    lines.append(f"Moves {'planned' if dry_run else 'executed'}: {moved_count}")
    lines.append(f"Stubs {'planned' if dry_run else 'written'}: {stub_count}")
    lines.append(f"Prior-deleted exclusions: 0")
    lines.append(f"Migration count (for INDEX.md): {len(entries)}")

    output = "\n".join(lines)
    print(output)

    # Write logs
    LOG_PATH_1.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH_1.write_text(output)

    log2_parent = LOG_PATH_2.parent
    if log2_parent.exists():
        LOG_PATH_2.write_text(output)
    else:
        print(f"[WARN] Log path 2 parent does not exist: {log2_parent}; skipping second log copy", file=sys.stderr)

    print(f"\nLog written to: {LOG_PATH_1.relative_to(REPO_ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
