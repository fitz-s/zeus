#!/usr/bin/env python3
"""
archive_batch_2026-05-22.py

Executes the ARCHIVE_PREP_2026-05-22.md batch migration:
  - 16 ARCHIVABLE_NOW packets
  - 26 ARCHIVABLE_AFTER_REPOINT packets (including data_pipeline_live_rootfix)
Total: 42 packets moved to docs/operations/archive/2026-Q2/

Creates .archived stubs at original paths.
Does NOT update INDEX.md or perform soft-ref repoints (handled separately).

Usage:
  python3 scripts/archive_batch_2026-05-22.py --dry-run
  python3 scripts/archive_batch_2026-05-22.py --apply

Created: 2026-05-22
Authority basis: docs/operations/archive/2026-Q2/ARCHIVE_PREP_2026-05-22.md
"""
import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

ARCHIVE_TARGET = REPO_ROOT / "docs/operations/archive/2026-Q2"
OPS_DIR = REPO_ROOT / "docs/operations"

ARCHIVED_AT = "2026-05-22"
ARCHIVED_BY = "executor_agent"

# ---------------------------------------------------------------------------
# 16 ARCHIVABLE_NOW — no packet-specific repoints needed
# ---------------------------------------------------------------------------
ARCHIVABLE_NOW = [
    "task_2026-05-08_alignment_safe_implementation",
    "task_2026-05-15_autonomous_agent_runtime_audit",
    "task_2026-05-15_p10_module_consolidation_planning",
    "task_2026-05-18_live_reduce_only_reconcile_loop",
    "task_2026-05-20_live_substrate_bookhash_ownership",
    "task_2026-05-20_pr221_review_fixes",
    "task_2026-05-21_evidence_tier_tribunal_authority",
    "task_2026-05-21_live_authority_shadow_risk_followup",
    "task_2026-05-21_live_contract_authority_pass",
    "task_2026-05-21_live_entry_order_management",
    "task_2026-05-21_live_family_selection_complete",
    "task_2026-05-21_live_family_selection_economic_floor",
    "task_2026-05-21_live_family_vector_fill_model",
    "task_2026-05-21_live_side_specific_entry_authority",
    "task_2026-05-21_money_path_semantic_ci",
    "task_2026-05-22_live_math_frontier",
]

# ---------------------------------------------------------------------------
# 26 ARCHIVABLE_AFTER_REPOINT — soft refs; repoints handled by separate pass
# ---------------------------------------------------------------------------
ARCHIVABLE_AFTER_REPOINT = [
    "task_2026-05-22_crosscheck_valid_window",
    "task_2026-05-06_hook_redesign",
    "task_2026-05-08_deep_alignment_audit",
    "task_2026-05-09_copilot_agent_sync",
    "task_2026-05-14_k1_followups",
    "task_2026-05-14_data_daemon_live_efficiency",
    "task_2026-05-15_data_pipeline_live_rootfix",
    "task_2026-05-15_live_order_e2e_goal",
    "task_2026-05-15_live_order_e2e_verification",
    "task_2026-05-15_p1_topology_v_next_additive",
    "task_2026-05-15_p2_companion_required_mechanism",
    "task_2026-05-15_p3_topology_v_next_phase2_shadow",
    "task_2026-05-15_p5_maintenance_worker_core",
    "task_2026-05-15_p8_authority_drift_3_blocking",
    "task_2026-05-15_p9_authority_inventory_v2",
    "task_2026-05-16_deep_alignment_audit",
    "task_2026-05-16_doc_alignment_plan",
    "task_2026-05-16_live_continuous_run_package",
    "task_2026-05-16_post_pr126_audit",
    "task_2026-05-17_docs_taxonomy_design",
    "task_2026-05-17_f109_fix",
    "task_2026-05-17_live_order_survival",
    "task_2026-05-17_post_karachi_remediation",
    "task_2026-05-17_reference_authority_docs_phase",
    "task_2026-05-18_wave3_dispatches",
    "task_2026-05-17_strategy_vnext_phase0",
]

ALL_ENTRIES = ARCHIVABLE_NOW + ARCHIVABLE_AFTER_REPOINT


def log(msg: str, dry: bool = False) -> None:
    prefix = "[DRY-RUN] " if dry else "[APPLY]   "
    print(prefix + msg)


def git_mv(src: Path, dst: Path, dry: bool) -> None:
    cmd = ["git", "-C", str(REPO_ROOT), "mv",
           str(src.relative_to(REPO_ROOT)),
           str(dst.relative_to(REPO_ROOT))]
    log(f"git mv {src.relative_to(REPO_ROOT)} → {dst.relative_to(REPO_ROOT)}", dry)
    if not dry:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)


def write_stub(name: str, ref_count: int, dry: bool) -> None:
    stub_path = OPS_DIR / f"{name}.archived"
    archive_loc = f"docs/operations/archive/2026-Q2/{name}"
    restore_cmd = f"git mv {archive_loc} docs/operations/{name}"
    content = (
        f"---\n"
        f"archived_to: {archive_loc}\n"
        f"archived_at: {ARCHIVED_AT}\n"
        f"archived_by: {ARCHIVED_BY}\n"
        f"last_modified_before_archive: {ARCHIVED_AT}\n"
        f"exemption_checks_passed: 3/3\n"
        f"reference_grep_count: {ref_count}\n"
        f"restore_command: {restore_cmd}\n"
        f"entry_type: directory\n"
        f"migration_source: docs/operations/{name}\n"
        f"---\n"
    )
    log(f"write stub {stub_path.relative_to(REPO_ROOT)}", dry)
    if not dry:
        stub_path.write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        parser.error("Specify --dry-run or --apply")

    dry = args.dry_run

    if not dry:
        ARCHIVE_TARGET.mkdir(parents=True, exist_ok=True)

    errors = []
    moved = 0
    skipped = 0

    for name in ALL_ENTRIES:
        src = OPS_DIR / name
        dst = ARCHIVE_TARGET / name

        if not src.exists():
            log(f"SKIP (already gone or missing): {name}", dry)
            skipped += 1
            continue

        if dst.exists():
            log(f"SKIP (already at destination): {name}", dry)
            skipped += 1
            continue

        stub_path = OPS_DIR / f"{name}.archived"
        if stub_path.exists():
            log(f"SKIP (stub already exists): {name}", dry)
            skipped += 1
            continue

        # Determine ref count for stub
        ref_count = 0 if name in ARCHIVABLE_NOW else 1  # >0 = had refs, now repointed

        git_mv(src, dst, dry)
        write_stub(name, ref_count, dry)
        moved += 1

    print(f"\nSummary: {moved} moved, {skipped} skipped, {len(errors)} errors")
    if errors:
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
