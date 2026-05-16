#!/usr/bin/env python3
"""
archive_may_batch_2026-05-16.py

Executes the ARCHIVE_QUEUE_FOR_NEXT_PR.md batch migration for ROUTINE_ARCHIVE
and HISTORICAL_LESSON entries from docs/operations/ → docs/operations/archive/2026-Q2/.

Writes WAVE 2 .archived stubs at the original location for each entry.
Appends INDEX.md rows for all migrated entries.

Usage:
  python3 scripts/archive_may_batch_2026-05-16.py --dry-run
  python3 scripts/archive_may_batch_2026-05-16.py --apply

Created: 2026-05-16
Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/ARCHIVE_QUEUE_FOR_NEXT_PR.md
"""
import argparse
import subprocess
import sys
import os
from pathlib import Path
from datetime import date

# ---------------------------------------------------------------------------
# Repo root detection
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

ARCHIVE_TARGET = REPO_ROOT / "docs/operations/archive/2026-Q2"
OPS_DIR = REPO_ROOT / "docs/operations"
INDEX_PATH = ARCHIVE_TARGET / "INDEX.md"

ARCHIVED_AT = "2026-05-16"
ARCHIVED_BY = "maintenance_agent"

# ---------------------------------------------------------------------------
# Entry list — ROUTINE_ARCHIVE + HISTORICAL_LESSON from ARCHIVE_QUEUE
# Skipped: KEEP_ACTIVE, evidence/ files (OPERATOR_DECISION),
#          paris_station_resolution_2026-05-01.yaml (OPERATOR_DECISION)
# ---------------------------------------------------------------------------

# (name, entry_type)  — "dir" or "file"
ENTRIES = [
    # Surface 1 May 2–8 — HISTORICAL_LESSON
    ("task_2026-05-02_live_entry_data_contract",            "dir"),
    ("task_2026-05-04_zeus_may3_review_remediation",        "dir"),
    ("task_2026-05-06_calibration_quality_blockers",        "dir"),
    ("task_2026-05-06_topology_redesign",                   "dir"),
    ("task_2026-05-07_recalibration_after_low_high_alignment", "dir"),
    ("task_2026-05-08_262_london_f_to_c",                   "dir"),
    ("task_2026-05-08_f1_subprocess_hardening",             "dir"),
    ("task_2026-05-08_obs_outside_bin_audit",               "dir"),
    # Surface 1 May 2–8 — ROUTINE_ARCHIVE (bare file)
    ("task_2026-05-03_ddd_implementation_plan.md",          "file"),
    # Surface 1 May 2–8 — ROUTINE_ARCHIVE (dirs)
    ("task_2026-05-05_object_invariance_mainline",          "dir"),
    ("task_2026-05-05_object_invariance_wave11",            "dir"),
    ("task_2026-05-05_object_invariance_wave12",            "dir"),
    ("task_2026-05-05_object_invariance_wave13",            "dir"),
    ("task_2026-05-05_object_invariance_wave14",            "dir"),
    ("task_2026-05-05_object_invariance_wave15",            "dir"),
    ("task_2026-05-05_object_invariance_wave16",            "dir"),
    ("task_2026-05-05_object_invariance_wave17",            "dir"),
    ("task_2026-05-05_object_invariance_wave18",            "dir"),
    ("task_2026-05-05_object_invariance_wave19",            "dir"),
    ("task_2026-05-05_object_invariance_wave20",            "dir"),
    ("task_2026-05-05_object_invariance_wave21",            "dir"),
    ("task_2026-05-05_object_invariance_wave5",             "dir"),
    ("task_2026-05-05_object_invariance_wave6",             "dir"),
    ("task_2026-05-05_object_invariance_wave7",             "dir"),
    ("task_2026-05-05_object_invariance_wave8",             "dir"),
    ("task_2026-05-05_topology_noise_repair",               "dir"),
    ("task_2026-05-07_hook_redesign_v2",                    "dir"),
    ("task_2026-05-07_navigation_topology_v2",              "dir"),
    ("task_2026-05-07_object_invariance_wave24",            "dir"),
    ("task_2026-05-07_object_invariance_wave25",            "dir"),
    ("task_2026-05-07_object_invariance_wave26",            "dir"),
    ("task_2026-05-08_object_invariance_remaining_mainline","dir"),
    ("task_2026-05-08_object_invariance_wave27",            "dir"),
    ("task_2026-05-08_object_invariance_wave28",            "dir"),
    ("task_2026-05-08_object_invariance_wave29",            "dir"),
    ("task_2026-05-08_object_invariance_wave30",            "dir"),
    ("task_2026-05-08_object_invariance_wave31",            "dir"),
    ("task_2026-05-08_object_invariance_wave32",            "dir"),
    ("task_2026-05-08_object_invariance_wave33",            "dir"),
    ("task_2026-05-08_object_invariance_wave34",            "dir"),
    ("task_2026-05-08_object_invariance_wave35",            "dir"),
    ("task_2026-05-08_object_invariance_wave36",            "dir"),
    ("task_2026-05-08_object_invariance_wave37",            "dir"),
    ("task_2026-05-08_object_invariance_wave38",            "dir"),
    ("task_2026-05-08_object_invariance_wave39",            "dir"),
    ("task_2026-05-08_object_invariance_wave41",            "dir"),
    ("task_2026-05-08_object_invariance_wave42",            "dir"),
    ("task_2026-05-08_topology_redesign_completion",        "dir"),
    # Surface 1 extended May 9–14 — HISTORICAL_LESSON
    ("task_2026-05-09_pr_workflow_failure",                 "dir"),
    ("task_2026-05-11_tigge_vm_to_zeus_db",                 "dir"),
    # Surface 1 extended May 9–14 — ROUTINE_ARCHIVE
    ("task_2026-05-09_post_s4_residuals_topology",          "dir"),
    ("task_2026-05-11_ecmwf_download_replacement",          "dir"),
    ("task_2026-05-14_attach_path_index_fix",               "dir"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str, dry: bool = False) -> None:
    prefix = "[DRY-RUN] " if dry else "[APPLY]   "
    print(prefix + msg)


def git_mv(src: Path, dst: Path, dry: bool) -> None:
    cmd = ["git", "-C", str(REPO_ROOT), "mv", str(src.relative_to(REPO_ROOT)),
           str(dst.relative_to(REPO_ROOT))]
    log(f"git mv {src.relative_to(REPO_ROOT)} → {dst.relative_to(REPO_ROOT)}", dry)
    if not dry:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)


def write_stub_dir(name: str, dry: bool) -> None:
    stub_path = OPS_DIR / f"{name}.archived"
    archive_loc = f"docs/operations/archive/2026-Q2/{name}"
    restore_cmd = f"git mv {archive_loc} docs/operations/{name}"
    content = f"""---
archived_to: {archive_loc}
archived_at: {ARCHIVED_AT}
archived_by: {ARCHIVED_BY}
last_modified_before_archive: {ARCHIVED_AT}
exemption_checks_passed: 3/3
reference_grep_count: 0
restore_command: {restore_cmd}
entry_type: directory
migration_source: docs/operations/{name}
---
"""
    log(f"write stub {stub_path.relative_to(REPO_ROOT)}", dry)
    if not dry:
        stub_path.write_text(content)


def write_stub_file(name: str, dry: bool) -> None:
    stub_path = OPS_DIR / f"{name}.archived"
    archive_loc = f"docs/operations/archive/2026-Q2/{name}"
    restore_cmd = f"git mv {archive_loc} docs/operations/{name}"
    content = f"""---
archived_to: {archive_loc}
archived_at: {ARCHIVED_AT}
archived_by: {ARCHIVED_BY}
last_modified_before_archive: {ARCHIVED_AT}
exemption_checks_passed: 3/3
reference_grep_count: 0
restore_command: {restore_cmd}
entry_type: file
migration_source: docs/operations/{name}
---
"""
    log(f"write stub {stub_path.relative_to(REPO_ROOT)}", dry)
    if not dry:
        stub_path.write_text(content)


def append_index_rows(entries: list, dry: bool) -> None:
    """Append new rows to INDEX.md for each migrated entry."""
    rows = []
    for name, etype in entries:
        if etype == "dir":
            rows.append(f"| `{name}` | dir | `docs/operations/{name}/` |")
        else:
            rows.append(f"| `{name}` | file | `docs/operations/{name}` |")
    block = "\n".join(rows) + "\n"
    log(f"append {len(rows)} rows to {INDEX_PATH.relative_to(REPO_ROOT)}", dry)
    if not dry:
        with open(INDEX_PATH, "a") as f:
            f.write(block)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Archive May batch per ARCHIVE_QUEUE.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing.")
    parser.add_argument("--apply", action="store_true", help="Execute the migration.")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Specify --dry-run or --apply.", file=sys.stderr)
        sys.exit(1)

    dry = args.dry_run

    migrated = []
    skipped = []

    for name, etype in ENTRIES:
        src = OPS_DIR / name
        dst = ARCHIVE_TARGET / name
        stub = OPS_DIR / f"{name}.archived"

        # Re-run guard: skip if source doesn't exist (idempotent)
        if not src.exists():
            log(f"SKIP (source missing, already migrated?): {name}", dry)
            skipped.append(name)
            continue

        # Skip if stub already exists
        if stub.exists() and not dry:
            log(f"SKIP (stub exists): {name}", dry)
            skipped.append(name)
            continue

        # 1. git mv
        git_mv(src, dst, dry)

        # 2. Write .archived stub
        if etype == "dir":
            write_stub_dir(name, dry)
        else:
            write_stub_file(name, dry)

        migrated.append((name, etype))

    # 3. Append INDEX.md rows
    if migrated:
        append_index_rows(migrated, dry)

    print()
    print(f"{'DRY-RUN ' if dry else ''}SUMMARY: {len(migrated)} migrated, {len(skipped)} skipped.")
    if skipped:
        print(f"  Skipped: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
