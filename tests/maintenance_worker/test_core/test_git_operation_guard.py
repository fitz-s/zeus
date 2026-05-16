# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/git_operation_guard.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Forbidden Actions"
"""
Tests for maintenance_worker.core.git_operation_guard.

Covers:
- git push --force (all flag forms: -f, --force, --force-with-lease)
- git push to main / master (protected branches)
- git rebase (unconditional block)
- git reset --hard
- git branch -D (force delete)
- git filter-branch / fast-import / am (history rewrites)
- Allowed operations: status, diff, log, show, mv, commit, push (maintenance branch),
  branch listing, add, fetch, remote, worktree, stash, checkout, restore
- Non-git argv passes through as ALLOWED
- bare 'git' with no subcommand allowed
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from maintenance_worker.core.git_operation_guard import check_git_operation
from maintenance_worker.core.install_metadata import InstallMetadata
from maintenance_worker.types.results import ValidatorResult

ALLOWED = ValidatorResult.ALLOWED
FORBIDDEN = ValidatorResult.FORBIDDEN_OPERATION


# ---------------------------------------------------------------------------
# git push --force variants
# ---------------------------------------------------------------------------


def test_push_force_short_flag_blocked() -> None:
    assert check_git_operation(["git", "push", "-f"]) == FORBIDDEN


def test_push_force_long_flag_blocked() -> None:
    assert check_git_operation(["git", "push", "--force"]) == FORBIDDEN


def test_push_force_with_lease_blocked() -> None:
    assert check_git_operation(["git", "push", "--force-with-lease"]) == FORBIDDEN


def test_push_force_with_lease_value_form_blocked() -> None:
    assert check_git_operation(["git", "push", "--force-with-lease=origin/main"]) == FORBIDDEN


def test_push_force_with_remote_blocked() -> None:
    assert check_git_operation(["git", "push", "origin", "--force"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# git push to protected branches
# ---------------------------------------------------------------------------


def test_push_to_main_blocked() -> None:
    assert check_git_operation(["git", "push", "origin", "main"]) == FORBIDDEN


def test_push_to_master_blocked() -> None:
    assert check_git_operation(["git", "push", "origin", "master"]) == FORBIDDEN


def test_push_to_main_with_refspec_blocked() -> None:
    assert check_git_operation(["git", "push", "origin", "HEAD:main"]) == FORBIDDEN


def test_push_to_master_with_refspec_blocked() -> None:
    assert check_git_operation(["git", "push", "origin", "feature:master"]) == FORBIDDEN


def test_push_to_refs_heads_main_blocked() -> None:
    assert check_git_operation(["git", "push", "origin", "refs/heads/main"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# git rebase — unconditional block
# ---------------------------------------------------------------------------


def test_rebase_blocked() -> None:
    assert check_git_operation(["git", "rebase", "main"]) == FORBIDDEN


def test_rebase_interactive_blocked() -> None:
    assert check_git_operation(["git", "rebase", "-i", "HEAD~3"]) == FORBIDDEN


def test_rebase_onto_blocked() -> None:
    assert check_git_operation(["git", "rebase", "--onto", "main", "feature"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# git reset --hard
# ---------------------------------------------------------------------------


def test_reset_hard_blocked() -> None:
    assert check_git_operation(["git", "reset", "--hard"]) == FORBIDDEN


def test_reset_hard_with_ref_blocked() -> None:
    assert check_git_operation(["git", "reset", "--hard", "HEAD~1"]) == FORBIDDEN


def test_reset_soft_allowed() -> None:
    assert check_git_operation(["git", "reset", "--soft", "HEAD~1"]) == ALLOWED


def test_reset_mixed_allowed() -> None:
    assert check_git_operation(["git", "reset", "--mixed", "HEAD~1"]) == ALLOWED


def test_reset_no_flag_allowed() -> None:
    assert check_git_operation(["git", "reset", "HEAD"]) == ALLOWED


# ---------------------------------------------------------------------------
# git branch -D (force delete)
# ---------------------------------------------------------------------------


def test_branch_force_delete_blocked() -> None:
    assert check_git_operation(["git", "branch", "-D", "old-feature"]) == FORBIDDEN


def test_branch_list_allowed() -> None:
    assert check_git_operation(["git", "branch", "-l"]) == ALLOWED


def test_branch_list_bare_allowed() -> None:
    assert check_git_operation(["git", "branch"]) == ALLOWED


def test_branch_delete_lowercase_allowed() -> None:
    # -d is a safe delete (merged branches only) — not blocked
    assert check_git_operation(["git", "branch", "-d", "old-feature"]) == ALLOWED


def test_branch_create_allowed() -> None:
    assert check_git_operation(["git", "branch", "maintenance/new-branch"]) == ALLOWED


# ---------------------------------------------------------------------------
# History rewrite commands — unconditional blocks
# ---------------------------------------------------------------------------


def test_filter_branch_blocked() -> None:
    assert check_git_operation(["git", "filter-branch", "--tree-filter", "rm"]) == FORBIDDEN


def test_fast_import_blocked() -> None:
    assert check_git_operation(["git", "fast-import"]) == FORBIDDEN


def test_am_blocked() -> None:
    assert check_git_operation(["git", "am", "patch.mbox"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# Allowed operations
# ---------------------------------------------------------------------------


def test_status_allowed() -> None:
    assert check_git_operation(["git", "status"]) == ALLOWED


def test_diff_allowed() -> None:
    assert check_git_operation(["git", "diff"]) == ALLOWED


def test_diff_cached_allowed() -> None:
    assert check_git_operation(["git", "diff", "--cached"]) == ALLOWED


def test_log_allowed() -> None:
    assert check_git_operation(["git", "log", "--oneline"]) == ALLOWED


def test_show_allowed() -> None:
    assert check_git_operation(["git", "show", "HEAD"]) == ALLOWED


def test_mv_allowed() -> None:
    assert check_git_operation(["git", "mv", "old.txt", "new.txt"]) == ALLOWED


def test_commit_allowed() -> None:
    assert check_git_operation(["git", "commit", "-m", "message"]) == ALLOWED


def test_push_maintenance_branch_allowed() -> None:
    assert check_git_operation(["git", "push", "origin", "maintenance/hygiene-2026-05-15"]) == ALLOWED


def test_push_feature_branch_allowed() -> None:
    assert check_git_operation(["git", "push", "origin", "feature/my-feature"]) == ALLOWED


def test_add_allowed() -> None:
    assert check_git_operation(["git", "add", "file.txt"]) == ALLOWED


def test_fetch_allowed() -> None:
    assert check_git_operation(["git", "fetch", "origin"]) == ALLOWED


def test_remote_v_allowed() -> None:
    assert check_git_operation(["git", "remote", "-v"]) == ALLOWED


def test_remote_get_url_allowed() -> None:
    assert check_git_operation(["git", "remote", "get-url", "origin"]) == ALLOWED


def test_worktree_list_allowed() -> None:
    assert check_git_operation(["git", "worktree", "list"]) == ALLOWED


def test_stash_allowed() -> None:
    assert check_git_operation(["git", "stash"]) == ALLOWED


def test_checkout_file_allowed() -> None:
    assert check_git_operation(["git", "checkout", "file.txt"]) == ALLOWED


def test_restore_allowed() -> None:
    assert check_git_operation(["git", "restore", "file.txt"]) == ALLOWED


def test_tag_list_allowed() -> None:
    assert check_git_operation(["git", "tag", "-l"]) == ALLOWED


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_argv_allowed() -> None:
    assert check_git_operation([]) == ALLOWED


def test_bare_git_allowed() -> None:
    assert check_git_operation(["git"]) == ALLOWED


def test_non_git_command_allowed() -> None:
    # Not a git invocation — not this guard's responsibility
    assert check_git_operation(["python", "script.py"]) == ALLOWED


def test_git_full_path_push_force_blocked() -> None:
    assert check_git_operation(["/usr/bin/git", "push", "--force"]) == FORBIDDEN


def test_git_full_path_status_allowed() -> None:
    assert check_git_operation(["/usr/bin/git", "status"]) == ALLOWED


# ---------------------------------------------------------------------------
# SEV-1 #4 adversarial tests — guarantee (e) remote URL allowlist
# ---------------------------------------------------------------------------


def _make_install_meta(allowed_urls: tuple[str, ...]) -> InstallMetadata:
    return InstallMetadata(
        schema_version=1,
        first_run_at=datetime.now(tz=timezone.utc) - timedelta(days=31),
        agent_version="0.1.0",
        install_run_id="test-uuid",
        allowed_remote_urls=allowed_urls,
        repo_root_at_install="/repo",
    )


class TestSev1GuaranteeEUrlAllowlist:
    """
    Guarantee (e): git push must be blocked if remote URL is not in the
    install-time allowlist when install_meta is provided.
    """

    def test_push_allowlisted_url_allowed(self) -> None:
        meta = _make_install_meta(("https://github.com/org/repo.git",))
        result = check_git_operation(
            ["git", "push", "origin", "maintenance/task-1"],
            install_meta=meta,
            remote_url="https://github.com/org/repo.git",
        )
        assert result == ALLOWED

    def test_push_unknown_url_blocked(self) -> None:
        meta = _make_install_meta(("https://github.com/org/repo.git",))
        result = check_git_operation(
            ["git", "push", "origin", "maintenance/task-1"],
            install_meta=meta,
            remote_url="https://evil.com/attacker/repo.git",
        )
        assert result == FORBIDDEN

    def test_push_remote_url_none_with_meta_blocked(self) -> None:
        """Fail-closed: unresolved URL cannot be validated — block."""
        meta = _make_install_meta(("https://github.com/org/repo.git",))
        result = check_git_operation(
            ["git", "push", "origin", "maintenance/task-1"],
            install_meta=meta,
            remote_url=None,
        )
        assert result == FORBIDDEN

    def test_push_no_install_meta_skips_url_check(self) -> None:
        """Backward compat: no install_meta → URL check skipped, push allowed."""
        result = check_git_operation(
            ["git", "push", "origin", "maintenance/task-1"],
            install_meta=None,
            remote_url=None,
        )
        assert result == ALLOWED

    def test_push_no_install_meta_explicit_url_skips_check(self) -> None:
        """No install_meta means no allowlist — URL value is irrelevant."""
        result = check_git_operation(
            ["git", "push", "origin", "maintenance/task-1"],
            install_meta=None,
            remote_url="https://evil.com/repo.git",
        )
        assert result == ALLOWED

    def test_non_push_subcommand_not_affected_by_meta(self) -> None:
        """URL check only applies to push; status is always allowed."""
        meta = _make_install_meta(())  # empty allowlist
        result = check_git_operation(
            ["git", "status"],
            install_meta=meta,
            remote_url=None,
        )
        assert result == ALLOWED

    def test_push_force_still_blocked_even_with_valid_url(self) -> None:
        """Force push is blocked regardless of URL allowlist."""
        meta = _make_install_meta(("https://github.com/org/repo.git",))
        result = check_git_operation(
            ["git", "push", "--force", "origin", "maintenance/task-1"],
            install_meta=meta,
            remote_url="https://github.com/org/repo.git",
        )
        assert result == FORBIDDEN

    def test_push_to_main_still_blocked_even_with_valid_url(self) -> None:
        """Protected-branch block takes precedence over URL check."""
        meta = _make_install_meta(("https://github.com/org/repo.git",))
        result = check_git_operation(
            ["git", "push", "origin", "main"],
            install_meta=meta,
            remote_url="https://github.com/org/repo.git",
        )
        assert result == FORBIDDEN

    def test_push_multiple_allowed_urls_matches_correctly(self) -> None:
        meta = _make_install_meta((
            "https://github.com/org/repo.git",
            "git@github.com:org/repo.git",
        ))
        result = check_git_operation(
            ["git", "push", "origin", "maintenance/task-1"],
            install_meta=meta,
            remote_url="git@github.com:org/repo.git",
        )
        assert result == ALLOWED
