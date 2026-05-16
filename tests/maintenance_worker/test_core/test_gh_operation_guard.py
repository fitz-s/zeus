# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/gh_operation_guard.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Forbidden Actions" + §"Allowed Targets"
"""
Tests for maintenance_worker.core.gh_operation_guard.

Covers:
- gh pr merge / approve / close (blocked)
- gh pr review --approve / --request-changes (blocked)
- gh issue close / create / comment / edit / reopen (blocked)
- gh repo edit / rename / delete (blocked)
- gh release create / delete / edit (blocked)
- gh workflow run / enable / disable (blocked)
- gh secret set / delete (blocked)
- gh variable set / delete (blocked)
- gh api with POST/PUT/PATCH/DELETE method (blocked)
- Blocked top-level subcommands: codespace, gist, ssh-key, gpg-key, alias, extension
- Allowed: gh pr create, pr list, pr view, pr comment, pr checks, pr diff, pr status
- Allowed: gh issue list, issue view
- Allowed: gh run list, run view
- Allowed: gh api (GET / no method flag)
- Allowed: gh auth status, repo view, release list/view, label list/create
- Empty argv / bare gh / non-gh commands
"""
from __future__ import annotations

import pytest

from maintenance_worker.core.gh_operation_guard import check_gh_operation
from maintenance_worker.types.results import ValidatorResult

ALLOWED = ValidatorResult.ALLOWED
FORBIDDEN = ValidatorResult.FORBIDDEN_OPERATION


# ---------------------------------------------------------------------------
# gh pr blocked operations
# ---------------------------------------------------------------------------


def test_pr_merge_blocked() -> None:
    assert check_gh_operation(["gh", "pr", "merge", "123"]) == FORBIDDEN


def test_pr_approve_blocked() -> None:
    assert check_gh_operation(["gh", "pr", "approve", "123"]) == FORBIDDEN


def test_pr_close_blocked() -> None:
    assert check_gh_operation(["gh", "pr", "close", "123"]) == FORBIDDEN


def test_pr_review_approve_flag_blocked() -> None:
    assert check_gh_operation(["gh", "pr", "review", "--approve", "123"]) == FORBIDDEN


def test_pr_review_approve_short_flag_blocked() -> None:
    assert check_gh_operation(["gh", "pr", "review", "-a", "123"]) == FORBIDDEN


def test_pr_review_request_changes_blocked() -> None:
    assert check_gh_operation(["gh", "pr", "review", "--request-changes", "123"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# gh issue blocked operations
# ---------------------------------------------------------------------------


def test_issue_close_blocked() -> None:
    assert check_gh_operation(["gh", "issue", "close", "42"]) == FORBIDDEN


def test_issue_create_blocked() -> None:
    assert check_gh_operation(["gh", "issue", "create"]) == FORBIDDEN


def test_issue_comment_blocked() -> None:
    assert check_gh_operation(["gh", "issue", "comment", "42", "--body", "text"]) == FORBIDDEN


def test_issue_reopen_blocked() -> None:
    assert check_gh_operation(["gh", "issue", "reopen", "42"]) == FORBIDDEN


def test_issue_edit_blocked() -> None:
    assert check_gh_operation(["gh", "issue", "edit", "42"]) == FORBIDDEN


def test_issue_delete_blocked() -> None:
    assert check_gh_operation(["gh", "issue", "delete", "42"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# gh repo blocked mutations
# ---------------------------------------------------------------------------


def test_repo_edit_blocked() -> None:
    assert check_gh_operation(["gh", "repo", "edit"]) == FORBIDDEN


def test_repo_rename_blocked() -> None:
    assert check_gh_operation(["gh", "repo", "rename", "new-name"]) == FORBIDDEN


def test_repo_delete_blocked() -> None:
    assert check_gh_operation(["gh", "repo", "delete"]) == FORBIDDEN


def test_repo_archive_blocked() -> None:
    assert check_gh_operation(["gh", "repo", "archive"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# gh release blocked mutations
# ---------------------------------------------------------------------------


def test_release_create_blocked() -> None:
    assert check_gh_operation(["gh", "release", "create", "v1.0"]) == FORBIDDEN


def test_release_delete_blocked() -> None:
    assert check_gh_operation(["gh", "release", "delete", "v1.0"]) == FORBIDDEN


def test_release_edit_blocked() -> None:
    assert check_gh_operation(["gh", "release", "edit", "v1.0"]) == FORBIDDEN


def test_release_upload_blocked() -> None:
    assert check_gh_operation(["gh", "release", "upload", "v1.0", "file.tar.gz"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# gh workflow blocked mutations
# ---------------------------------------------------------------------------


def test_workflow_run_blocked() -> None:
    assert check_gh_operation(["gh", "workflow", "run", "ci.yml"]) == FORBIDDEN


def test_workflow_enable_blocked() -> None:
    assert check_gh_operation(["gh", "workflow", "enable", "ci.yml"]) == FORBIDDEN


def test_workflow_disable_blocked() -> None:
    assert check_gh_operation(["gh", "workflow", "disable", "ci.yml"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# gh secret / variable blocked mutations
# ---------------------------------------------------------------------------


def test_secret_set_blocked() -> None:
    assert check_gh_operation(["gh", "secret", "set", "MY_SECRET"]) == FORBIDDEN


def test_secret_delete_blocked() -> None:
    assert check_gh_operation(["gh", "secret", "delete", "MY_SECRET"]) == FORBIDDEN


def test_variable_set_blocked() -> None:
    assert check_gh_operation(["gh", "variable", "set", "MY_VAR"]) == FORBIDDEN


def test_variable_delete_blocked() -> None:
    assert check_gh_operation(["gh", "variable", "delete", "MY_VAR"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# gh api — mutation methods blocked, GET allowed
# ---------------------------------------------------------------------------


def test_api_post_method_blocked() -> None:
    assert check_gh_operation(["gh", "api", "--method", "POST", "/repos/owner/repo"]) == FORBIDDEN


def test_api_put_method_blocked() -> None:
    assert check_gh_operation(["gh", "api", "--method", "PUT", "/repos/owner/repo"]) == FORBIDDEN


def test_api_patch_method_blocked() -> None:
    assert check_gh_operation(["gh", "api", "--method", "PATCH", "/repos/owner/repo"]) == FORBIDDEN


def test_api_delete_method_blocked() -> None:
    assert check_gh_operation(["gh", "api", "--method", "DELETE", "/repos/owner/repo"]) == FORBIDDEN


def test_api_x_post_short_flag_blocked() -> None:
    assert check_gh_operation(["gh", "api", "-X", "POST", "/endpoint"]) == FORBIDDEN


def test_api_get_no_method_allowed() -> None:
    # Default method for gh api is GET — read-only
    assert check_gh_operation(["gh", "api", "/repos/owner/repo/labels"]) == ALLOWED


def test_api_method_get_allowed() -> None:
    assert check_gh_operation(["gh", "api", "--method", "GET", "/repos/owner/repo"]) == ALLOWED


# ---------------------------------------------------------------------------
# Blocked top-level subcommands
# ---------------------------------------------------------------------------


def test_codespace_blocked() -> None:
    assert check_gh_operation(["gh", "codespace", "list"]) == FORBIDDEN


def test_gist_blocked() -> None:
    assert check_gh_operation(["gh", "gist", "list"]) == FORBIDDEN


def test_ssh_key_blocked() -> None:
    assert check_gh_operation(["gh", "ssh-key", "list"]) == FORBIDDEN


def test_gpg_key_blocked() -> None:
    assert check_gh_operation(["gh", "gpg-key", "list"]) == FORBIDDEN


def test_alias_blocked() -> None:
    assert check_gh_operation(["gh", "alias", "set", "co", "pr checkout"]) == FORBIDDEN


def test_extension_blocked() -> None:
    assert check_gh_operation(["gh", "extension", "install", "owner/repo"]) == FORBIDDEN


# ---------------------------------------------------------------------------
# Allowed: gh pr operations
# ---------------------------------------------------------------------------


def test_pr_create_allowed() -> None:
    assert check_gh_operation(["gh", "pr", "create", "--title", "Maintenance", "--body", "..."]) == ALLOWED


def test_pr_list_allowed() -> None:
    assert check_gh_operation(["gh", "pr", "list"]) == ALLOWED


def test_pr_view_allowed() -> None:
    assert check_gh_operation(["gh", "pr", "view", "123"]) == ALLOWED


def test_pr_comment_allowed() -> None:
    # Limitation: maintenance-only check not enforced in guard; see module docstring
    assert check_gh_operation(["gh", "pr", "comment", "123", "--body", "Maintenance run complete."]) == ALLOWED


def test_pr_checks_allowed() -> None:
    assert check_gh_operation(["gh", "pr", "checks", "123"]) == ALLOWED


def test_pr_diff_allowed() -> None:
    assert check_gh_operation(["gh", "pr", "diff", "123"]) == ALLOWED


def test_pr_status_allowed() -> None:
    assert check_gh_operation(["gh", "pr", "status"]) == ALLOWED


def test_pr_review_no_flags_allowed() -> None:
    # gh pr review with no approve/request-changes flags (e.g., just comment)
    assert check_gh_operation(["gh", "pr", "review", "123", "--comment", "-b", "text"]) == ALLOWED


# ---------------------------------------------------------------------------
# Allowed: gh issue read operations
# ---------------------------------------------------------------------------


def test_issue_list_allowed() -> None:
    assert check_gh_operation(["gh", "issue", "list"]) == ALLOWED


def test_issue_view_allowed() -> None:
    assert check_gh_operation(["gh", "issue", "view", "42"]) == ALLOWED


# ---------------------------------------------------------------------------
# Allowed: gh run operations
# ---------------------------------------------------------------------------


def test_run_list_allowed() -> None:
    assert check_gh_operation(["gh", "run", "list"]) == ALLOWED


def test_run_view_allowed() -> None:
    assert check_gh_operation(["gh", "run", "view", "12345"]) == ALLOWED


def test_run_watch_allowed() -> None:
    assert check_gh_operation(["gh", "run", "watch", "12345"]) == ALLOWED


# ---------------------------------------------------------------------------
# Allowed: other read-only operations
# ---------------------------------------------------------------------------


def test_auth_status_allowed() -> None:
    assert check_gh_operation(["gh", "auth", "status"]) == ALLOWED


def test_repo_view_allowed() -> None:
    assert check_gh_operation(["gh", "repo", "view"]) == ALLOWED


def test_repo_clone_allowed() -> None:
    assert check_gh_operation(["gh", "repo", "clone", "owner/repo"]) == ALLOWED


def test_release_list_allowed() -> None:
    assert check_gh_operation(["gh", "release", "list"]) == ALLOWED


def test_release_view_allowed() -> None:
    assert check_gh_operation(["gh", "release", "view", "v1.0"]) == ALLOWED


def test_label_list_allowed() -> None:
    assert check_gh_operation(["gh", "label", "list"]) == ALLOWED


def test_label_create_allowed() -> None:
    assert check_gh_operation(["gh", "label", "create", "maintenance"]) == ALLOWED


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_argv_allowed() -> None:
    assert check_gh_operation([]) == ALLOWED


def test_bare_gh_allowed() -> None:
    assert check_gh_operation(["gh"]) == ALLOWED


def test_non_gh_command_allowed() -> None:
    # Not a gh invocation — not this guard's responsibility
    assert check_gh_operation(["git", "push"]) == ALLOWED


def test_gh_full_path_pr_merge_blocked() -> None:
    assert check_gh_operation(["/usr/local/bin/gh", "pr", "merge", "123"]) == FORBIDDEN


def test_gh_full_path_pr_list_allowed() -> None:
    assert check_gh_operation(["/usr/local/bin/gh", "pr", "list"]) == ALLOWED
