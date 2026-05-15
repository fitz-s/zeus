# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/gh_operation_guard.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Forbidden Actions" + §"Allowed Targets"
"""
gh_operation_guard — check_gh_operation(argv) -> ValidatorResult

Covers SAFETY_CONTRACT.md "Forbidden Actions" GitHub CLI bullets:
  BLOCKED:
    - gh pr merge (merge any PR)
    - gh pr approve / gh pr review --approve (approve any PR)
    - gh pr close (close a PR without --comment form; close is unconditional block)
    - gh pr review --approve (approve via review command)
    - gh issue close (close issues)
    - gh issue create (create issues — not in maintenance scope)
    - gh issue comment (comment on issues)
    - gh issue reopen (reopen issues)
    - gh issue edit (edit issues)
    - gh repo settings (any repo settings mutation)
    - gh repo edit (mutate repo settings)
    - gh api (mutation methods: POST, PUT, PATCH, DELETE) except maintenance allowlist
    - gh release create / delete / edit (release management)
    - gh workflow run / enable / disable (workflow management)
    - gh secret set / delete (secret management)
    - gh variable set / delete (variable management)

  ALLOWED:
    - gh pr create (with maintenance label — structural enforcement note below)
    - gh pr list (read-only)
    - gh pr view (read-only)
    - gh pr comment (posting comment on a PR — limitation note below)
    - gh pr checks (read-only status check)
    - gh pr diff (read-only diff)
    - gh pr status (read-only)
    - gh issue list (read-only)
    - gh issue view (read-only)
    - gh run list (read-only)
    - gh run view (read-only)
    - gh run watch (read-only)
    - gh api (GET method only, or read-only repos/.../labels)
    - gh auth status (read-only)
    - gh repo view (read-only)
    - gh repo clone (read operation)
    - gh release list / view (read-only)
    - gh label list / create (label management for maintenance PRs)

LIMITATION NOTE — gh pr comment maintenance-only:
  Whether a comment targets a maintenance PR cannot be determined from argv
  alone without a GitHub API call. This guard permits 'gh pr comment' structurally;
  enforcement that comments target only maintenance PRs is a caller responsibility
  (validate PR number against the agent's own open PRs before invocation).

argv parsing: handles '--flag value', '--flag=value', and positional forms.
Zero project-specific identifiers.
Stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

from maintenance_worker.types.results import ValidatorResult


# ---------------------------------------------------------------------------
# Blocked (subcommand, subsubcommand) pairs
# ---------------------------------------------------------------------------

# Maps top-level gh subcommand → blocked subsubcommands.
# An empty set means the entire subcommand is blocked.
_BLOCKED_SUBCOMMAND_PAIRS: dict[str, frozenset[str]] = {
    "pr": frozenset(
        {
            "merge",    # merge any PR — SAFETY_CONTRACT Forbidden Actions
            "approve",  # approve any PR
            "close",    # close any PR
        }
    ),
    "issue": frozenset(
        {
            "close",    # close issues
            "create",   # create issues (not maintenance scope)
            "comment",  # comment on issues (separate from PR comment)
            "reopen",   # reopen issues
            "edit",     # edit issues
            "delete",   # delete issues
        }
    ),
    "repo": frozenset(
        {
            "edit",     # mutate repo settings
            "rename",   # rename repo
            "delete",   # delete repo
            "archive",  # archive repo
        }
    ),
    "release": frozenset(
        {
            "create",   # create releases
            "delete",   # delete releases
            "edit",     # edit releases
            "upload",   # upload release assets
        }
    ),
    "workflow": frozenset(
        {
            "run",      # trigger workflow runs
            "enable",   # enable workflow
            "disable",  # disable workflow
        }
    ),
    "secret": frozenset(
        {
            "set",      # set secrets
            "delete",   # delete secrets
            "remove",   # remove secrets
        }
    ),
    "variable": frozenset(
        {
            "set",      # set variables
            "delete",   # delete variables
            "remove",   # remove variables
        }
    ),
}

# Entire top-level subcommands that are blocked (no subsubcommand needed).
_BLOCKED_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "codespace",   # codespace management — not in maintenance scope
        "gist",        # gist management — not in maintenance scope
        "ssh-key",     # SSH key management
        "gpg-key",     # GPG key management
        "alias",       # alias management
        "extension",   # extension management
    }
)

# gh pr review flags that make an approval — blocked.
_PR_REVIEW_APPROVE_FLAGS: frozenset[str] = frozenset({"--approve", "-a"})
_PR_REVIEW_REQUEST_CHANGES_FLAGS: frozenset[str] = frozenset(
    {"--request-changes", "-r"}
)

# gh api method flags that indicate mutation.
_API_MUTATION_METHOD_FLAGS: frozenset[str] = frozenset(
    {
        "--method",
        "-X",
    }
)
_API_MUTATION_METHODS: frozenset[str] = frozenset(
    {"POST", "PUT", "PATCH", "DELETE"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cmd_basename(argv: list[str]) -> str:
    """Return basename of argv[0], or '' if argv is empty."""
    if not argv:
        return ""
    return os.path.basename(argv[0])


def _get_subcommand(argv: list[str]) -> Optional[str]:
    """Return gh subcommand (argv[1])."""
    if len(argv) < 2:
        return None
    return argv[1]


def _get_subsubcommand(argv: list[str]) -> Optional[str]:
    """Return gh subsubcommand (argv[2])."""
    if len(argv) < 3:
        return None
    return argv[2]


def _argv_has_flag(argv: list[str], flags: frozenset[str]) -> bool:
    """
    Return True if any token in argv matches a flag.
    Handles '--flag value' and '--flag=value' forms.
    """
    for token in argv:
        if token in flags:
            return True
        if "=" in token:
            prefix = token.split("=", 1)[0]
            if prefix in flags:
                return True
    return False


def _get_flag_value(argv: list[str], flags: frozenset[str]) -> Optional[str]:
    """
    Return the value of a flag, handling '--flag value' and '--flag=value' forms.
    Returns None if flag not found.
    """
    for i, token in enumerate(argv):
        if token in flags:
            if i + 1 < len(argv):
                return argv[i + 1]
        elif "=" in token:
            prefix = token.split("=", 1)[0]
            if prefix in flags:
                return token.split("=", 1)[1]
    return None


def _is_api_mutation(argv: list[str]) -> bool:
    """
    Return True if 'gh api' invocation uses a mutating HTTP method.

    Checks --method/-X flags for POST, PUT, PATCH, DELETE.
    Default method for 'gh api' is GET (read-only), so absence of a method
    flag means read-only.
    """
    method = _get_flag_value(argv, _API_MUTATION_METHOD_FLAGS)
    if method is None:
        return False
    return method.upper() in _API_MUTATION_METHODS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_gh_operation(argv: list[str]) -> ValidatorResult:
    """
    Check a gh CLI command invocation against the safety contract.

    Returns:
      ALLOWED            — operation is permitted
      FORBIDDEN_OPERATION — operation is blocked

    argv[0] should be 'gh' or full path to gh binary. If argv[0] is not
    a gh invocation, returns ALLOWED (not this guard's responsibility).

    See module docstring for LIMITATION NOTE on gh pr comment enforcement.
    """
    if not argv:
        return ValidatorResult.ALLOWED

    cmd = _cmd_basename(argv)
    if cmd != "gh":
        return ValidatorResult.ALLOWED

    subcommand = _get_subcommand(argv)
    if subcommand is None:
        # bare 'gh' — allow (shows help)
        return ValidatorResult.ALLOWED

    # Blocked top-level subcommands.
    if subcommand in _BLOCKED_TOP_LEVEL:
        return ValidatorResult.FORBIDDEN_OPERATION

    # Check (subcommand, subsubcommand) blocked pairs.
    if subcommand in _BLOCKED_SUBCOMMAND_PAIRS:
        subsubcommand = _get_subsubcommand(argv)
        if subsubcommand in _BLOCKED_SUBCOMMAND_PAIRS[subcommand]:
            return ValidatorResult.FORBIDDEN_OPERATION

    # gh pr review --approve / --request-changes — blocked.
    if subcommand == "pr":
        subsubcommand = _get_subsubcommand(argv)
        if subsubcommand == "review":
            if _argv_has_flag(argv, _PR_REVIEW_APPROVE_FLAGS):
                return ValidatorResult.FORBIDDEN_OPERATION
            if _argv_has_flag(argv, _PR_REVIEW_REQUEST_CHANGES_FLAGS):
                return ValidatorResult.FORBIDDEN_OPERATION

    # gh api — block mutating methods; allow GET (default).
    if subcommand == "api":
        if _is_api_mutation(argv):
            return ValidatorResult.FORBIDDEN_OPERATION

    return ValidatorResult.ALLOWED
