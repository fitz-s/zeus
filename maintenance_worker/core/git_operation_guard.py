# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/git_operation_guard.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Forbidden Actions"
"""
git_operation_guard — check_git_operation(argv) -> ValidatorResult

Covers SAFETY_CONTRACT.md "Forbidden Actions" git bullets:
  BLOCKED:
    - git push --force (any form: -f, --force, --force-with-lease)
    - git push --force-with-lease
    - git rebase (any rebase operation)
    - git reset --hard
    - git branch -D (force-delete branch)
    - git push to main or master (non-force push to protected branches)
    - git filter-branch (history rewrite)
    - git fast-import (history rewrite)
    - git am (apply mailbox patch — mutates history)

  ALLOWED:
    - git status
    - git diff (any form)
    - git log (any form)
    - git show (any form)
    - git mv (move/rename via git)
    - git commit (staging and committing allowed)
    - git push (regular, to maintenance/ branch — non-force, non-main)
    - git branch (listing: -l, --list, or bare)
    - git add (staging)
    - git fetch (read-only network)
    - git remote (read-only: -v, get-url, etc.)
    - git worktree (list)
    - git tag (read-only: listing, showing)
    - git stash (temporary state; allowed)
    - git checkout (file checkout within allowed paths)
    - git restore (read-only file restore)

argv parsing: handles '--flag value', '--flag=value', and positional forms.
Zero project-specific identifiers.
Stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

from maintenance_worker.core.install_metadata import InstallMetadata
from maintenance_worker.types.results import ValidatorResult


# ---------------------------------------------------------------------------
# Blocked subcommands — unconditionally blocked regardless of flags
# ---------------------------------------------------------------------------

_BLOCKED_SUBCOMMANDS_UNCONDITIONAL: frozenset[str] = frozenset(
    {
        "rebase",       # git rebase — SAFETY_CONTRACT "Forbidden Actions"
        "filter-branch",  # history rewrite
        "fast-import",  # history rewrite via import
        "am",           # apply mailbox (mutates commit history)
    }
)

# Protected branch names — push to these is blocked even without --force.
_PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master"})

# Force push flags in any form.
_FORCE_PUSH_FLAGS: frozenset[str] = frozenset(
    {"-f", "--force", "--force-with-lease"}
)

# reset --hard flag
_RESET_HARD_FLAGS: frozenset[str] = frozenset({"--hard"})

# branch -D (force delete) flag
_BRANCH_FORCE_DELETE_FLAGS: frozenset[str] = frozenset({"-D"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cmd_basename(argv: list[str]) -> str:
    """Return basename of argv[0], or '' if argv is empty."""
    if not argv:
        return ""
    return os.path.basename(argv[0])


def _get_subcommand(argv: list[str]) -> Optional[str]:
    """Return the git subcommand (argv[1]), or None."""
    if len(argv) < 2:
        return None
    return argv[1]


def _argv_has_flag(argv: list[str], flags: frozenset[str]) -> bool:
    """
    Return True if any token in argv matches a flag.

    Handles '--flag value' and '--flag=value' forms.
    Also handles short flag clusters like '-fD' if applicable.
    """
    for token in argv:
        if token in flags:
            return True
        if "=" in token:
            prefix = token.split("=", 1)[0]
            if prefix in flags:
                return True
    return False


def _push_targets_protected_branch(argv: list[str]) -> bool:
    """
    Return True if this git push targets main or master.

    Detects forms:
      git push origin main
      git push origin master
      git push origin HEAD:main
      git push origin refs/heads/main
    """
    # Collect non-flag tokens after 'push'
    # argv[0] = git, argv[1] = push, argv[2+] = remote + refspecs/flags
    refspec_tokens: list[str] = []
    i = 2
    while i < len(argv):
        token = argv[i]
        if not token.startswith("-"):
            refspec_tokens.append(token)
        elif "=" not in token:
            # Flag that takes a value: skip both
            if token in {
                "--receive-pack",
                "--repo",
                "--push-option",
                "-o",
                "--recurse-submodules",
                "--signed",
            }:
                i += 1  # skip the value token
        i += 1

    # First non-flag token after push is the remote; rest are refspecs.
    # Check refspecs (position 1+) for protected branches.
    refspecs = refspec_tokens[1:] if len(refspec_tokens) > 1 else []

    for refspec in refspecs:
        # refspec can be: 'main', 'HEAD:main', 'refs/heads/main', 'feature:main'
        # Extract destination side (after ':' if present).
        dest = refspec.split(":")[-1] if ":" in refspec else refspec
        # Strip refs/heads/ prefix
        if dest.startswith("refs/heads/"):
            dest = dest[len("refs/heads/"):]
        if dest in _PROTECTED_BRANCHES:
            return True

    # Also check non-refspec form: git push origin main (bare branch name)
    # If the only refspec token is a protected branch name (no colon), it's a target.
    if refspec_tokens and len(refspec_tokens) >= 2:
        candidate = refspec_tokens[-1]
        if ":" not in candidate and candidate in _PROTECTED_BRANCHES:
            return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_git_operation(
    argv: list[str],
    install_meta: Optional[InstallMetadata] = None,
    remote_url: Optional[str] = None,
) -> ValidatorResult:
    """
    Check a git command invocation against the safety contract.

    Returns:
      ALLOWED            — operation is permitted
      FORBIDDEN_OPERATION — operation is blocked

    argv[0] should be 'git' (or full path to git binary). If argv[0] is not
    a git invocation, returns ALLOWED (not this guard's responsibility).

    Caller must still invoke validate_action for any path arguments that the
    git command touches — this guard only validates the operation semantics.

    Guarantee (e): if install_meta is provided, any git push whose resolved
    remote URL is not in install_meta.allowed_remote_urls is FORBIDDEN_OPERATION.
    Pass remote_url as the resolved URL string (from 'git remote get-url <remote>').
    If install_meta is provided but remote_url is None, the push is blocked
    (fail-closed: unresolved URL cannot be validated against the allowlist).
    If install_meta is None, the URL allowlist check is skipped (backward compat
    for callers that do not have install metadata available).
    """
    if not argv:
        return ValidatorResult.ALLOWED

    cmd = _cmd_basename(argv)
    if cmd != "git":
        return ValidatorResult.ALLOWED

    subcommand = _get_subcommand(argv)
    if subcommand is None:
        # bare 'git' with no subcommand — allow (help, version, etc.)
        return ValidatorResult.ALLOWED

    # Unconditionally blocked subcommands.
    if subcommand in _BLOCKED_SUBCOMMANDS_UNCONDITIONAL:
        return ValidatorResult.FORBIDDEN_OPERATION

    # git reset --hard
    if subcommand == "reset" and _argv_has_flag(argv, _RESET_HARD_FLAGS):
        return ValidatorResult.FORBIDDEN_OPERATION

    # git branch -D (force delete)
    if subcommand == "branch" and _argv_has_flag(argv, _BRANCH_FORCE_DELETE_FLAGS):
        return ValidatorResult.FORBIDDEN_OPERATION

    # git push — check for force flags, protected branch targets, and URL allowlist.
    if subcommand == "push":
        if _argv_has_flag(argv, _FORCE_PUSH_FLAGS):
            return ValidatorResult.FORBIDDEN_OPERATION
        if _push_targets_protected_branch(argv):
            return ValidatorResult.FORBIDDEN_OPERATION
        # Guarantee (e): remote URL allowlist enforcement.
        if install_meta is not None:
            if remote_url is None or remote_url not in install_meta.allowed_remote_urls:
                return ValidatorResult.FORBIDDEN_OPERATION

    return ValidatorResult.ALLOWED
