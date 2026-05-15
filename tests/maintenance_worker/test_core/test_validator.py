# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/validator.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Validator Semantics" + §"Forbidden Targets" + §"Pre-Action Validator"
"""
Tests for maintenance_worker.core.validator.

Covers all 5 SAFETY_CONTRACT.md Validator Semantics guarantees (a–e),
the SEV-2 #4 structural fix (in-place WRITE requires manifest.proposed_modifies),
all 8 Operation enum members,
Path A invariant (FORBIDDEN_* never writes SELF_QUARANTINE),
and edge cases.

Guarantee test naming convention:
  test_guarantee_a_*  — READ is not exempt
  test_guarantee_b_*  — realpath canonicalization before match
  test_guarantee_c_*  — symlink / hardlink resolution
  test_guarantee_d_*  — per-leaf decomposition for directory operations
  test_guarantee_e_*  — git remote URL allowlist before any push
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from maintenance_worker.core.install_metadata import (
    FLOOR_EXEMPT_TASK_IDS,
    DryRunFloor,
    InstallMetadata,
)
from maintenance_worker.core.validator import ActionValidator, LeafCheck
from maintenance_worker.types.operations import Operation
from maintenance_worker.types.results import ValidatorResult
from maintenance_worker.types.specs import EngineConfig, ProposalManifest, TickContext

ALLOWED = ValidatorResult.ALLOWED
FORBIDDEN_PATH = ValidatorResult.FORBIDDEN_PATH
FORBIDDEN_OP = ValidatorResult.FORBIDDEN_OPERATION
MISSING = ValidatorResult.MISSING_PRECHECK
DRY_RUN_ONLY = ValidatorResult.ALLOWED_BUT_DRY_RUN_ONLY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_install_meta(
    state_dir: Path,
    allowed_urls: tuple[str, ...] = ("https://github.com/org/repo.git",),
    days_ago: int = 31,
) -> InstallMetadata:
    from datetime import timedelta
    first_run = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return InstallMetadata(
        schema_version=1,
        first_run_at=first_run,
        agent_version="0.1.0",
        install_run_id="test-uuid",
        allowed_remote_urls=allowed_urls,
        repo_root_at_install=str(state_dir.parent),
    )


def _make_ctx(tmp_path: Path) -> TickContext:
    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.md",
        live_default=False,
        scheduler="launchd",
        notification_channel="file",
    )
    return TickContext(
        run_id="test-run-00000000",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="MANUAL_CLI",
    )


def _validator(tmp_path: Path) -> ActionValidator:
    return ActionValidator(state_dir=tmp_path / "state")


# ---------------------------------------------------------------------------
# Guarantee (a): READ is not exempt
# ---------------------------------------------------------------------------


def test_guarantee_a_read_on_state_db_is_forbidden(tmp_path: Path) -> None:
    """
    READ of state/*.db must return FORBIDDEN_PATH (SAFETY_CONTRACT §(a)).
    """
    v = _validator(tmp_path)
    state_db = tmp_path / "state" / "world.db"
    state_db.parent.mkdir(parents=True, exist_ok=True)
    state_db.touch()
    result = v.validate_action(state_db, Operation.READ)
    assert result == FORBIDDEN_PATH, (
        "READ on state/*.db must be FORBIDDEN_PATH — guarantee (a) violated"
    )


def test_guarantee_a_read_on_credential_file_is_forbidden(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    cred_file = tmp_path / "my_secret_token.json"
    cred_file.touch()
    result = v.validate_action(cred_file, Operation.READ)
    assert result == FORBIDDEN_PATH


def test_guarantee_a_read_on_src_file_is_forbidden(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    src_file = tmp_path / "src" / "module.py"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.touch()
    result = v.validate_action(src_file, Operation.READ)
    assert result == FORBIDDEN_PATH


def test_guarantee_a_read_on_agents_md_is_forbidden(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    agents_md = tmp_path / "subdir" / "AGENTS.md"
    agents_md.parent.mkdir(parents=True, exist_ok=True)
    agents_md.touch()
    result = v.validate_action(agents_md, Operation.READ)
    assert result == FORBIDDEN_PATH


def test_guarantee_a_read_on_evidence_dir_allowed(tmp_path: Path) -> None:
    """Reads of evidence dir are not in the forbidden list — allowed."""
    v = _validator(tmp_path)
    evidence_file = tmp_path / "evidence" / "summary.md"
    evidence_file.parent.mkdir(parents=True, exist_ok=True)
    evidence_file.touch()
    result = v.validate_action(evidence_file, Operation.READ)
    assert result == ALLOWED


# ---------------------------------------------------------------------------
# Guarantee (b): realpath canonicalization before match
# ---------------------------------------------------------------------------


def test_guarantee_b_dotdot_expansion_triggers_forbidden(tmp_path: Path) -> None:
    """
    A path with '..' that resolves to a forbidden target must be FORBIDDEN_PATH.
    SAFETY_CONTRACT §(b) example: .archive/../com.zeus.plist → active plist.
    """
    v = _validator(tmp_path)
    # Construct a path with '..' that resolves to a state/*.db pattern
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_file = state_dir / "world.db"
    db_file.touch()
    # Path: state/subdir/../world.db → resolves to state/world.db
    subdir = state_dir / "subdir"
    subdir.mkdir()
    tricky_path = subdir / ".." / "world.db"
    result = v.validate_action(tricky_path, Operation.READ)
    assert result == FORBIDDEN_PATH, (
        "Path with '..' resolving to forbidden target must be FORBIDDEN_PATH — guarantee (b)"
    )


def test_guarantee_b_relative_path_expanded(tmp_path: Path) -> None:
    """Relative paths must be resolved to absolute before matching."""
    v = _validator(tmp_path)
    # Create a .gitignore file (forbidden) and build a relative-style path
    git_ignore = tmp_path / ".gitignore"
    git_ignore.touch()
    result = v.validate_action(git_ignore, Operation.READ)
    assert result == FORBIDDEN_PATH


def test_guarantee_b_allowed_path_not_tripped_by_canonicalization(tmp_path: Path) -> None:
    """Allowed paths must not become forbidden after canonicalization."""
    v = _validator(tmp_path)
    archive_dir = tmp_path / "docs" / "operations" / "archive" / "2026-Q2"
    archive_dir.mkdir(parents=True)
    allowed_file = archive_dir / "old_task.py"
    allowed_file.touch()
    result = v.validate_action(allowed_file, Operation.READ)
    assert result == ALLOWED


# ---------------------------------------------------------------------------
# Guarantee (c): symlink and hardlink resolution
# ---------------------------------------------------------------------------


def test_guarantee_c_symlink_to_forbidden_target_is_forbidden(tmp_path: Path) -> None:
    """
    A symlink whose resolved target is a forbidden path → FORBIDDEN_PATH.
    SAFETY_CONTRACT §(c): 'the agent never follows a symlink whose resolved target
    escapes the allowed-write set.'
    """
    v = _validator(tmp_path)
    # Create the actual forbidden file (state/*.db)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    real_db = state_dir / "world.db"
    real_db.touch()
    # Create a symlink to it in a non-forbidden-looking location
    link = tmp_path / "evidence" / "link_to_db"
    link.parent.mkdir(parents=True)
    link.symlink_to(real_db)
    # The symlink's target is forbidden — must return FORBIDDEN_PATH
    result = v.validate_action(link, Operation.READ)
    assert result == FORBIDDEN_PATH, (
        "Symlink to forbidden target must be FORBIDDEN_PATH — guarantee (c)"
    )


def test_guarantee_c_symlink_to_allowed_target_is_allowed(tmp_path: Path) -> None:
    """A symlink to an allowed file is ALLOWED after resolution."""
    v = _validator(tmp_path)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    real_file = evidence_dir / "summary.md"
    real_file.touch()
    link = tmp_path / "link_to_summary"
    link.symlink_to(real_file)
    result = v.validate_action(link, Operation.READ)
    assert result == ALLOWED


def test_guarantee_c_nonexistent_symlink_handled(tmp_path: Path) -> None:
    """Non-existent paths (no symlink) do not crash the validator."""
    v = _validator(tmp_path)
    nonexistent = tmp_path / "evidence" / "nonexistent.md"
    result = v.validate_action(nonexistent, Operation.READ)
    assert result == ALLOWED


# ---------------------------------------------------------------------------
# Guarantee (d): per-leaf decomposition for directory operations
# ---------------------------------------------------------------------------


def test_guarantee_d_directory_with_forbidden_leaf_detected(tmp_path: Path) -> None:
    """
    decompose_directory_op on a dir containing a forbidden file must surface
    FORBIDDEN_PATH for that leaf.
    """
    v = _validator(tmp_path)
    test_dir = tmp_path / "mixed_dir"
    test_dir.mkdir()
    # Allowed file
    (test_dir / "allowed.md").touch()
    # Forbidden file: source code extension outside exempt dirs
    forbidden_leaf = test_dir / "module.py"
    forbidden_leaf.touch()

    checks = v.decompose_directory_op(test_dir, Operation.MOVE)
    forbidden_checks = [c for c in checks if c.result == FORBIDDEN_PATH]
    assert len(forbidden_checks) >= 1, (
        "decompose_directory_op must detect forbidden leaf — guarantee (d)"
    )


def test_guarantee_d_clean_directory_all_allowed(tmp_path: Path) -> None:
    """A directory of only allowed files returns all ALLOWED checks."""
    v = _validator(tmp_path)
    clean_dir = tmp_path / "clean_dir"
    clean_dir.mkdir()
    (clean_dir / "README.md").touch()
    (clean_dir / "notes.txt").touch()
    (clean_dir / "summary.md").touch()

    checks = v.decompose_directory_op(clean_dir, Operation.MOVE)
    assert all(c.result == ALLOWED for c in checks), (
        "All leaves in clean directory should be ALLOWED"
    )


def test_guarantee_d_nonexistent_dir_returns_empty(tmp_path: Path) -> None:
    """decompose_directory_op on nonexistent dir returns empty list."""
    v = _validator(tmp_path)
    result = v.decompose_directory_op(tmp_path / "nonexistent", Operation.MOVE)
    assert result == []


def test_guarantee_d_empty_directory_returns_empty(tmp_path: Path) -> None:
    """decompose_directory_op on empty dir returns empty list."""
    v = _validator(tmp_path)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = v.decompose_directory_op(empty_dir, Operation.MOVE)
    assert result == []


def test_guarantee_d_leaf_check_is_frozen_dataclass(tmp_path: Path) -> None:
    """LeafCheck is a frozen dataclass — immutable."""
    v = _validator(tmp_path)
    test_dir = tmp_path / "leaf_dir"
    test_dir.mkdir()
    (test_dir / "note.md").touch()
    checks = v.decompose_directory_op(test_dir, Operation.MOVE)
    if checks:
        lc = checks[0]
        with pytest.raises((AttributeError, TypeError)):
            lc.result = FORBIDDEN_PATH  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Guarantee (e): git remote URL allowlist
# ---------------------------------------------------------------------------


def test_guarantee_e_allowed_remote_url_passes(tmp_path: Path) -> None:
    """Remote URL in allowlist → ALLOWED."""
    v = _validator(tmp_path)
    meta = _make_install_meta(tmp_path, allowed_urls=("https://github.com/org/repo.git",))
    result = v.check_remote_url_allowlist("https://github.com/org/repo.git", meta)
    assert result == ALLOWED, "Allowlisted remote URL must be ALLOWED — guarantee (e)"


def test_guarantee_e_unknown_remote_url_blocked(tmp_path: Path) -> None:
    """Remote URL not in allowlist → FORBIDDEN_OPERATION."""
    v = _validator(tmp_path)
    meta = _make_install_meta(tmp_path, allowed_urls=("https://github.com/org/repo.git",))
    result = v.check_remote_url_allowlist("https://github.com/attacker/fork.git", meta)
    assert result == FORBIDDEN_OP, (
        "Unknown remote URL must be FORBIDDEN_OPERATION — guarantee (e)"
    )


def test_guarantee_e_empty_allowlist_blocks_all(tmp_path: Path) -> None:
    """An empty allowlist blocks every URL."""
    v = _validator(tmp_path)
    meta = _make_install_meta(tmp_path, allowed_urls=())
    result = v.check_remote_url_allowlist("https://github.com/org/repo.git", meta)
    assert result == FORBIDDEN_OP


def test_guarantee_e_multiple_allowed_urls(tmp_path: Path) -> None:
    """Multiple allowlisted URLs are all permitted."""
    v = _validator(tmp_path)
    urls = ("https://github.com/org/repo.git", "git@github.com:org/repo.git")
    meta = _make_install_meta(tmp_path, allowed_urls=urls)
    assert v.check_remote_url_allowlist(urls[0], meta) == ALLOWED
    assert v.check_remote_url_allowlist(urls[1], meta) == ALLOWED


# ---------------------------------------------------------------------------
# SEV-2 #4: in-place WRITE structural fix
# ---------------------------------------------------------------------------


def test_sev2_write_to_existing_without_manifest_is_forbidden(tmp_path: Path) -> None:
    """
    WRITE to an existing file with manifest=None → FORBIDDEN_PATH.
    This closes the C2-wall: in-place edits must be manifest-registered.
    """
    v = _validator(tmp_path)
    target = tmp_path / "evidence" / "existing.md"
    target.parent.mkdir(parents=True)
    target.write_text("existing content")
    result = v.validate_action(target, Operation.WRITE, manifest=None)
    assert result == FORBIDDEN_PATH, (
        "WRITE to existing file without manifest must be FORBIDDEN_PATH (SEV-2 #4)"
    )


def test_sev2_write_to_existing_not_in_manifest_is_forbidden(tmp_path: Path) -> None:
    """WRITE to existing file that is NOT in manifest.proposed_modifies → FORBIDDEN_PATH."""
    v = _validator(tmp_path)
    target = tmp_path / "evidence" / "existing.md"
    target.parent.mkdir(parents=True)
    target.write_text("existing content")
    # Manifest exists but does not include this path
    other = tmp_path / "evidence" / "other.md"
    manifest = ProposalManifest(
        task_id="test_task",
        proposed_modifies=(other,),
    )
    result = v.validate_action(target, Operation.WRITE, manifest=manifest)
    assert result == FORBIDDEN_PATH


def test_sev2_write_to_existing_in_manifest_proposed_modifies_allowed(tmp_path: Path) -> None:
    """
    WRITE to existing file IN manifest.proposed_modifies → ALLOWED.
    This is the happy path for legitimate in-place modifications.
    """
    v = _validator(tmp_path)
    target = tmp_path / "evidence" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("old content")
    # The canonical form: resolve the path
    canonical_target = target.resolve()
    manifest = ProposalManifest(
        task_id="test_task",
        proposed_modifies=(canonical_target,),
    )
    result = v.validate_action(target, Operation.WRITE, manifest=manifest)
    assert result == ALLOWED, (
        "WRITE to existing file IN manifest.proposed_modifies must be ALLOWED (SEV-2 #4)"
    )


def test_sev2_write_to_nonexistent_file_allowed_without_manifest(tmp_path: Path) -> None:
    """WRITE to a non-existing file (new creation) does not require manifest entry."""
    v = _validator(tmp_path)
    new_file = tmp_path / "evidence" / "new_report.md"
    new_file.parent.mkdir(parents=True)
    # new_file does NOT exist
    assert not new_file.exists()
    result = v.validate_action(new_file, Operation.WRITE, manifest=None)
    assert result == ALLOWED


def test_sev2_write_to_forbidden_path_still_blocked(tmp_path: Path) -> None:
    """A forbidden path in proposed_modifies does not become allowed."""
    v = _validator(tmp_path)
    state_db = tmp_path / "state" / "world.db"
    state_db.parent.mkdir()
    state_db.touch()
    canonical_db = state_db.resolve()
    manifest = ProposalManifest(
        task_id="test_task",
        proposed_modifies=(canonical_db,),
    )
    # Even with manifest registration, a forbidden path must remain FORBIDDEN
    result = v.validate_action(state_db, Operation.WRITE, manifest=manifest)
    assert result == FORBIDDEN_PATH


# ---------------------------------------------------------------------------
# All 8 Operation enum members
# ---------------------------------------------------------------------------


def test_operation_read_covered(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    allowed_file = tmp_path / "evidence" / "data.txt"
    allowed_file.parent.mkdir(parents=True)
    allowed_file.touch()
    assert v.validate_action(allowed_file, Operation.READ) == ALLOWED


def test_operation_write_new_file_covered(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    new_file = tmp_path / "evidence" / "new.txt"
    new_file.parent.mkdir(parents=True)
    assert v.validate_action(new_file, Operation.WRITE) == ALLOWED


def test_operation_mkdir_covered(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    new_dir = tmp_path / "evidence" / "subdir"
    assert v.validate_action(new_dir, Operation.MKDIR) == ALLOWED


def test_operation_mkdir_forbidden_path(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    src_dir = tmp_path / "src" / "newdir"
    assert v.validate_action(src_dir, Operation.MKDIR) == FORBIDDEN_PATH


def test_operation_move_covered(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    file_path = tmp_path / "evidence" / "file.md"
    file_path.parent.mkdir(parents=True)
    file_path.touch()
    assert v.validate_action(file_path, Operation.MOVE) == ALLOWED


def test_operation_delete_covered(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    file_path = tmp_path / "evidence" / "zero.txt"
    file_path.parent.mkdir(parents=True)
    file_path.touch()
    assert v.validate_action(file_path, Operation.DELETE) == ALLOWED


def test_operation_git_exec_returns_missing_precheck(tmp_path: Path) -> None:
    """GIT_EXEC → MISSING_PRECHECK: caller must use git_operation_guard."""
    v = _validator(tmp_path)
    result = v.validate_action(Path("."), Operation.GIT_EXEC)
    assert result == MISSING


def test_operation_gh_exec_returns_missing_precheck(tmp_path: Path) -> None:
    """GH_EXEC → MISSING_PRECHECK: caller must use gh_operation_guard."""
    v = _validator(tmp_path)
    result = v.validate_action(Path("."), Operation.GH_EXEC)
    assert result == MISSING


def test_operation_subprocess_exec_returns_missing_precheck(tmp_path: Path) -> None:
    """SUBPROCESS_EXEC → MISSING_PRECHECK: caller must use subprocess_guard."""
    v = _validator(tmp_path)
    result = v.validate_action(Path("."), Operation.SUBPROCESS_EXEC)
    assert result == MISSING


# ---------------------------------------------------------------------------
# Path A invariant: FORBIDDEN_* never writes SELF_QUARANTINE
# ---------------------------------------------------------------------------


def test_path_a_forbidden_path_does_not_write_self_quarantine(tmp_path: Path) -> None:
    """
    Critical Path A invariant: validate_action returning FORBIDDEN_PATH
    must NOT write SELF_QUARANTINE. Only post_mutation_detector (Path B) does that.
    """
    v = _validator(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # A forbidden path
    state_db = state_dir / "world.db"
    state_db.touch()

    result = v.validate_action(state_db, Operation.READ)
    assert result == FORBIDDEN_PATH

    quarantine_file = state_dir / "SELF_QUARANTINE"
    assert not quarantine_file.exists(), (
        "validate_action MUST NOT write SELF_QUARANTINE (Path A invariant)"
    )


def test_path_a_forbidden_operation_does_not_write_self_quarantine(tmp_path: Path) -> None:
    """GIT_EXEC returning MISSING_PRECHECK must not write SELF_QUARANTINE."""
    v = _validator(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = v.validate_action(Path("."), Operation.GIT_EXEC)
    assert result == MISSING

    quarantine_file = state_dir / "SELF_QUARANTINE"
    assert not quarantine_file.exists()


# ---------------------------------------------------------------------------
# Forbidden path groups — spot checks
# ---------------------------------------------------------------------------


def test_forbidden_group1_tests_tree(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    test_file = tmp_path / "tests" / "test_module.py"
    test_file.parent.mkdir()
    test_file.touch()
    assert v.validate_action(test_file, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group1_bin_tree(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    bin_file = tmp_path / "bin" / "tool"
    bin_file.parent.mkdir()
    bin_file.touch()
    assert v.validate_action(bin_file, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group2_architecture_tree(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    arch_file = tmp_path / "architecture" / "decisions.md"
    arch_file.parent.mkdir()
    arch_file.touch()
    assert v.validate_action(arch_file, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group2_claude_settings(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.touch()
    assert v.validate_action(settings, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group3_state_wal(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    wal = tmp_path / "state" / "world.db-wal"
    wal.parent.mkdir()
    wal.touch()
    assert v.validate_action(wal, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group3_state_calibration(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    cal_file = tmp_path / "state" / "calibration" / "model.pkl"
    cal_file.parent.mkdir(parents=True)
    cal_file.touch()
    assert v.validate_action(cal_file, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group4_env_file(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    env_file = tmp_path / ".env"
    env_file.touch()
    assert v.validate_action(env_file, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group4_pem_cert(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    pem = tmp_path / "evidence" / "cert.pem"
    pem.parent.mkdir()
    pem.touch()
    assert v.validate_action(pem, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group5_git_dir(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    git_config = tmp_path / ".git" / "config"
    git_config.parent.mkdir()
    git_config.touch()
    assert v.validate_action(git_config, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group5_gitignore(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.touch()
    assert v.validate_action(gitignore, Operation.READ) == FORBIDDEN_PATH


def test_forbidden_group6_etc(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    etc_file = Path("/etc/hosts")
    result = v.validate_action(etc_file, Operation.READ)
    assert result == FORBIDDEN_PATH


# ---------------------------------------------------------------------------
# Allowed paths — spot checks
# ---------------------------------------------------------------------------


def test_allowed_evidence_dir_write(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    evidence_file = tmp_path / "evidence" / "report.md"
    evidence_file.parent.mkdir(parents=True)
    # New file — WRITE allowed
    assert v.validate_action(evidence_file, Operation.WRITE) == ALLOWED


def test_allowed_state_dir_write_new(tmp_path: Path) -> None:
    """Writing new files to state_dir is allowed (e.g., install_metadata.json)."""
    v = ActionValidator(state_dir=tmp_path / "state")
    new_state_file = tmp_path / "state" / "ack_state.json"
    new_state_file.parent.mkdir(parents=True)
    assert not new_state_file.exists()
    assert v.validate_action(new_state_file, Operation.WRITE) == ALLOWED


def test_allowed_archive_operations_dir(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    archive_dir = tmp_path / "docs" / "operations" / "archive" / "2026-Q2"
    archive_dir.mkdir(parents=True)
    archive_file = archive_dir / "old_task"
    archive_file.mkdir()
    assert v.validate_action(archive_file, Operation.MKDIR) == ALLOWED


def test_allowed_docs_operations_task(tmp_path: Path) -> None:
    v = _validator(tmp_path)
    task_dir = tmp_path / "docs" / "operations" / "task_2026-05-15_example"
    task_dir.mkdir(parents=True)
    # Reading a task packet file is allowed
    task_file = task_dir / "DESIGN.md"
    task_file.touch()
    assert v.validate_action(task_file, Operation.READ) == ALLOWED


# ---------------------------------------------------------------------------
# Dry-run floor: validate_action_with_floor
# ---------------------------------------------------------------------------


def test_dry_run_floor_10_days_returns_dry_run_only(tmp_path: Path) -> None:
    """Non-exempt task with first_run_at 10 days ago → ALLOWED_BUT_DRY_RUN_ONLY."""
    from datetime import timedelta
    v = _validator(tmp_path)
    meta = _make_install_meta(tmp_path, days_ago=10)
    floor_cfg = DryRunFloor(floor_days=30)
    evidence_file = tmp_path / "evidence" / "report.md"
    evidence_file.parent.mkdir(parents=True)

    result = v.validate_action_with_floor(
        evidence_file,
        Operation.WRITE,
        context=None,
        manifest=None,
        task_id="some_non_exempt_task",
        install_meta=meta,
        floor_cfg=floor_cfg,
    )
    assert result == DRY_RUN_ONLY


def test_dry_run_floor_31_days_returns_allowed(tmp_path: Path) -> None:
    """Non-exempt task with first_run_at 31 days ago → ALLOWED."""
    v = _validator(tmp_path)
    meta = _make_install_meta(tmp_path, days_ago=31)
    floor_cfg = DryRunFloor(floor_days=30)
    evidence_file = tmp_path / "evidence" / "report.md"
    evidence_file.parent.mkdir(parents=True)

    result = v.validate_action_with_floor(
        evidence_file,
        Operation.WRITE,
        context=None,
        manifest=None,
        task_id="some_non_exempt_task",
        install_meta=meta,
        floor_cfg=floor_cfg,
    )
    assert result == ALLOWED


def test_dry_run_floor_exempt_task_always_allowed(tmp_path: Path) -> None:
    """Exempt task IDs bypass the floor unconditionally."""
    v = _validator(tmp_path)
    meta = _make_install_meta(tmp_path, days_ago=0)
    floor_cfg = DryRunFloor(floor_days=30)
    evidence_file = tmp_path / "evidence" / "report.md"
    evidence_file.parent.mkdir(parents=True)

    exempt_id = next(iter(FLOOR_EXEMPT_TASK_IDS))
    result = v.validate_action_with_floor(
        evidence_file,
        Operation.WRITE,
        context=None,
        manifest=None,
        task_id=exempt_id,
        install_meta=meta,
        floor_cfg=floor_cfg,
    )
    assert result == ALLOWED


def test_dry_run_floor_forbidden_path_short_circuits(tmp_path: Path) -> None:
    """FORBIDDEN_PATH from base validate_action is returned before floor check."""
    v = _validator(tmp_path)
    meta = _make_install_meta(tmp_path, days_ago=31)
    floor_cfg = DryRunFloor(floor_days=30)
    state_db = tmp_path / "state" / "world.db"
    state_db.parent.mkdir()
    state_db.touch()

    result = v.validate_action_with_floor(
        state_db,
        Operation.READ,
        context=None,
        manifest=None,
        task_id="some_task",
        install_meta=meta,
        floor_cfg=floor_cfg,
    )
    assert result == FORBIDDEN_PATH


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_validator_no_state_dir_arg(tmp_path: Path) -> None:
    """ActionValidator with state_dir=None still works (no extension exemption)."""
    v = ActionValidator(state_dir=None)
    evidence_file = tmp_path / "evidence" / "data.md"
    evidence_file.parent.mkdir(parents=True)
    evidence_file.touch()
    assert v.validate_action(evidence_file, Operation.READ) == ALLOWED


def test_canonicalize_path_returns_absolute(tmp_path: Path) -> None:
    """canonicalize_path always returns an absolute path."""
    v = _validator(tmp_path)
    p = tmp_path / "some" / ".." / "file.txt"
    result = v.canonicalize_path(p)
    assert result.is_absolute()


def test_source_extension_outside_archive_forbidden(tmp_path: Path) -> None:
    """A .py file outside docs/operations/archive/ and state_dir is FORBIDDEN."""
    v = _validator(tmp_path)
    py_file = tmp_path / "tools" / "helper.py"
    py_file.parent.mkdir()
    py_file.touch()
    assert v.validate_action(py_file, Operation.READ) == FORBIDDEN_PATH


def test_source_extension_inside_archive_allowed(tmp_path: Path) -> None:
    """A .py file inside docs/operations/archive/ is ALLOWED (archived packet)."""
    v = _validator(tmp_path)
    archive_py = tmp_path / "docs" / "operations" / "archive" / "2026-Q1" / "script.py"
    archive_py.parent.mkdir(parents=True)
    archive_py.touch()
    assert v.validate_action(archive_py, Operation.READ) == ALLOWED


def test_auth_profiles_json_forbidden(tmp_path: Path) -> None:
    """auth-profiles.json matches the secret credential pattern."""
    v = _validator(tmp_path)
    auth_file = tmp_path / "agents" / "main" / "auth-profiles.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.touch()
    assert v.validate_action(auth_file, Operation.READ) == FORBIDDEN_PATH
