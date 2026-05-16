# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3.5 (P5.1↔P5.5 boundary)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Forbidden Actions" + §"Audit-by-Grep Discipline"
"""
core/apply_publisher — ApplyPublisher.publish(staged_diff, manifest, run_id)

P5.1↔P5.5 boundary (SCAFFOLD §3.5):
  engine._apply_decisions() → ApplyResult (staged filesystem mutations, NO git)
  ApplyPublisher.publish()   → git commit + optional PR (provenance-wrapped)

Publish sequence:
  1. Resolve remote URL via 'git remote get-url origin'
  2. Check URL allowlist (SAFETY_CONTRACT guarantee e) via
     ActionValidator.check_remote_url_allowlist — FORBIDDEN_OPERATION aborts
  3. Stage all changes via 'git add -A' (or per-file staging)
  4. Set commit identity (Maintenance Worker) via set_commit_identity context manager
  5. Commit with Audit-by-Grep message via make_commit_message
  6. Push to current branch (non-force, non-main/master) via git push
  7. If ApplyResult.requires_pr: open PR via 'gh pr create'
  8. On any failure: rollback via git reset --soft HEAD~1 (NEVER --hard)

Rollback safety:
  - 'git reset --soft HEAD~1' undoes the commit, preserving the working tree
  - 'git reset HEAD' (unstage only) used if commit never happened
  - NEVER 'git reset --hard' (SAFETY_CONTRACT §"Forbidden Actions")

Stdlib + subprocess only. No external deps. Zero Zeus identifiers.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from maintenance_worker.core.git_operation_guard import check_git_operation
from maintenance_worker.core.install_metadata import InstallMetadata
from maintenance_worker.core.provenance import (
    make_commit_message,
    set_commit_identity,
)
from maintenance_worker.core.validator import ActionValidator
from maintenance_worker.types.results import ApplyResult, ValidatorResult


class PublishGuardError(RuntimeError):
    """Raised when a git/gh operation guard blocks a command before execution."""


# ---------------------------------------------------------------------------
# PublishResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishResult:
    """
    Outcome of a single ApplyPublisher.publish() call.

    commit_sha: the SHA of the commit produced, or '' if none.
    pr_url: the URL of any PR opened, or '' if none.
    rolled_back: True if the commit was rolled back after a push failure.
    skipped: True if the publish was skipped (dry_run_only or no mutations).
    error: non-empty if publish failed (after rollback).
    """

    task_id: str
    commit_sha: str = ""
    pr_url: str = ""
    rolled_back: bool = False
    skipped: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# ApplyPublisher
# ---------------------------------------------------------------------------


class ApplyPublisher:
    """
    Owns all git commit / PR / provenance operations for one tick.

    Stateless: all state is in the ApplyResult + repo_root. Multiple
    ApplyPublisher instances per tick are permitted (one per task) but
    MUST NOT push concurrently (git push is not concurrency-safe).

    Usage:
        publisher = ApplyPublisher(repo_root=config.repo_root, install_meta=meta)
        result = publisher.publish(apply_result, run_id)
    """

    def __init__(
        self,
        repo_root: Path,
        install_meta: InstallMetadata,
        validator: Optional[ActionValidator] = None,
        branch: Optional[str] = None,
    ) -> None:
        """
        repo_root: absolute path to the git repository root.
        install_meta: used for remote URL allowlist check (guarantee e).
        validator: optional; defaults to ActionValidator() if not provided.
        branch: target push branch; if None, detected from current HEAD.
        """
        self._repo_root = repo_root
        self._install_meta = install_meta
        self._validator = validator or ActionValidator(state_dir=None)
        self._branch = branch

    # ------------------------------------------------------------------
    # Primary publish sequence
    # ------------------------------------------------------------------

    def publish(
        self,
        apply_result: ApplyResult,
        run_id: str,
        summary: str = "",
    ) -> PublishResult:
        """
        Commit staged changes and optionally open a PR.

        Steps:
          1. Skip if dry_run_only or no mutations.
          2. Check remote URL allowlist (SAFETY_CONTRACT guarantee e).
          3. Stage changes (git add -A).
          4. Commit with Maintenance Worker identity + Run-Id trailer.
          5. Push to current branch (non-force, non-main/master).
          6. If requires_pr: open PR via gh pr create.
          7. On push/PR failure: rollback commit via git reset --soft HEAD~1.

        Returns PublishResult. Never raises — all errors are captured in
        PublishResult.error (fail-safe for tick continuation).
        """
        task_id = apply_result.task_id

        # Step 1: skip if dry_run_only or nothing to publish.
        if apply_result.dry_run_only:
            return PublishResult(task_id=task_id, skipped=True)

        has_mutations = bool(
            apply_result.moved or apply_result.deleted or apply_result.created
        )
        if not has_mutations:
            return PublishResult(task_id=task_id, skipped=True)

        # Step 2: resolve remote URL + check allowlist (guarantee e).
        remote_url = self._resolve_remote_url()
        if remote_url is None:
            return PublishResult(
                task_id=task_id,
                error="Could not resolve remote URL for allowlist check",
            )

        allowlist_result = self._validator.check_remote_url_allowlist(
            remote_url, self._install_meta
        )
        if allowlist_result == ValidatorResult.FORBIDDEN_OPERATION:
            return PublishResult(
                task_id=task_id,
                error=f"Remote URL not in allowlist: {remote_url!r}",
            )

        commit_sha = ""
        pr_url = ""

        try:
            # Step 3: stage changes.
            stage_ok, stage_err = self._stage_changes()
            if not stage_ok:
                return PublishResult(task_id=task_id, error=f"git add failed: {stage_err}")

            # Step 4: commit with Maintenance Worker identity.
            commit_msg = make_commit_message(
                task_id=task_id,
                run_id=run_id,
                summary=summary or f"apply task {task_id}",
            )
            with set_commit_identity(self._repo_root, run_id):
                commit_sha, commit_err = self._commit(commit_msg)
            if not commit_sha:
                # Unstage only — no commit to roll back.
                self._unstage()
                return PublishResult(task_id=task_id, error=f"git commit failed: {commit_err}")

            # Step 5: push to current branch (pass remote_url for guard allowlist check).
            push_ok, push_err = self._push(remote_url=remote_url)
            if not push_ok:
                # Rollback: undo the commit (NEVER --hard).
                self._rollback_commit()
                return PublishResult(
                    task_id=task_id,
                    rolled_back=True,
                    error=f"git push failed: {push_err}",
                )

            # Step 6: open PR if required.
            if apply_result.requires_pr:
                pr_url, pr_err = self._open_pr(task_id, run_id, summary)
                if not pr_url:
                    # PR failure is non-fatal (commit + push succeeded).
                    return PublishResult(
                        task_id=task_id,
                        commit_sha=commit_sha,
                        error=f"gh pr create failed (commit pushed): {pr_err}",
                    )

        except Exception as exc:  # pylint: disable=broad-except
            # Unexpected error: attempt rollback if we have a commit.
            rolled_back = False
            if commit_sha:
                rolled_back = self._rollback_commit()
            return PublishResult(
                task_id=task_id,
                rolled_back=rolled_back,
                error=f"unexpected error: {exc}",
            )

        return PublishResult(
            task_id=task_id,
            commit_sha=commit_sha,
            pr_url=pr_url,
        )

    # ------------------------------------------------------------------
    # Internal git helpers
    # ------------------------------------------------------------------

    def _resolve_remote_url(self) -> Optional[str]:
        """
        Return the URL of 'origin' remote, or None on failure.

        SAFETY_CONTRACT guarantee (e): remote URL is resolved here and
        passed to check_remote_url_allowlist before any push.
        """
        try:
            result = subprocess.run(
                ["git", "-C", str(self._repo_root), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

    def _guarded_git(
        self,
        argv: list[str],
        remote_url: Optional[str] = None,
        timeout: int = 30,
    ) -> tuple[bool, str, str]:
        """
        Run a git command after passing it through check_git_operation.

        Returns (ok, stdout, stderr). If the guard blocks, raises
        PublishGuardError (caller converts to PublishResult.error).

        SEV-1 #3: every git subprocess in this class must go through
        this helper so the operation guard is structurally enforced.
        """
        guard_result = check_git_operation(argv, self._install_meta, remote_url)
        if guard_result == ValidatorResult.FORBIDDEN_OPERATION:
            raise PublishGuardError(
                f"git_operation_guard blocked: {' '.join(argv[:4])!r}"
            )
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode == 0, result.stdout, result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            return False, "", str(exc)

    def _stage_changes(self) -> tuple[bool, str]:
        """Run 'git add -A' (guarded). Returns (success, error_text)."""
        argv = ["git", "-C", str(self._repo_root), "add", "-A"]
        ok, _out, err = self._guarded_git(argv, timeout=30)
        return ok, err.strip() if not ok else ""

    def _commit(self, message: str) -> tuple[str, str]:
        """
        Run 'git commit -m <message>' (guarded). Returns (sha_or_empty, error_text).

        Caller must have already set the commit identity via set_commit_identity.
        """
        argv = ["git", "-C", str(self._repo_root), "commit", "-m", message]
        ok, _out, err = self._guarded_git(argv, timeout=30)
        if ok:
            sha = self._head_sha()
            return sha, ""
        return "", err.strip() or _out.strip()

    def _head_sha(self) -> str:
        """Return the SHA of HEAD, or '' on failure."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self._repo_root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return ""
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def _current_branch(self) -> str:
        """Return the current branch name, or '' on failure."""
        if self._branch:
            return self._branch
        try:
            result = subprocess.run(
                ["git", "-C", str(self._repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return ""
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def _push(self, remote_url: Optional[str] = None) -> tuple[bool, str]:
        """
        Run 'git push origin <branch>' (guarded, non-force, non-main/master).

        remote_url: the resolved remote URL, passed to check_git_operation for
        the allowlist guard (guarantee e). Defense-in-depth: we also check the
        branch name directly before invoking the guard.
        """
        branch = self._current_branch()
        if not branch:
            return False, "Could not determine current branch for push"

        # Defense-in-depth: never push to main/master (guard also blocks this).
        if branch in ("main", "master"):
            return False, f"Refusing to push to protected branch {branch!r}"

        argv = ["git", "-C", str(self._repo_root), "push", "origin", branch]
        try:
            ok, _out, err = self._guarded_git(argv, remote_url=remote_url, timeout=60)
            return ok, err.strip() if not ok else ""
        except PublishGuardError as exc:
            return False, str(exc)

    def _rollback_commit(self) -> bool:
        """
        Roll back the most recent commit via 'git reset --soft HEAD~1' (guarded).

        SAFETY_CONTRACT: NEVER use --hard (forbidden operation). --soft
        leaves the working tree and index untouched; only moves HEAD back.
        Returns True if rollback succeeded, False otherwise.

        The guard will ALLOW 'git reset --soft' and BLOCK 'git reset --hard',
        adding structural enforcement on top of the defense-in-depth hardcoded --soft.
        """
        argv = ["git", "-C", str(self._repo_root), "reset", "--soft", "HEAD~1"]
        try:
            ok, _out, _err = self._guarded_git(argv, timeout=15)
            return ok
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, PublishGuardError):
            return False

    def _unstage(self) -> None:
        """Unstage all changes via 'git reset HEAD' (guarded, no commit to undo)."""
        argv = ["git", "-C", str(self._repo_root), "reset", "HEAD"]
        try:
            self._guarded_git(argv, timeout=15)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, PublishGuardError):
            pass

    def _open_pr(
        self,
        task_id: str,
        run_id: str,
        summary: str,
    ) -> tuple[str, str]:
        """
        Open a PR via 'gh pr create'. Returns (pr_url, error_text).

        PR title follows Audit-by-Grep: includes task_id + run_id.
        Body includes the run-id and summary for evidence trail correlation.
        """
        title = f"maint({task_id}): {summary or 'maintenance changes'}"
        body = (
            f"Maintenance PR for task `{task_id}`.\n\n"
            f"Run-Id: `{run_id}`\n\n"
            f"Generated by maintenance_worker. Do not merge without review."
        )
        branch = self._current_branch()

        try:
            result = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--title", title,
                    "--body", body,
                    "--head", branch,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(self._repo_root),
            )
            if result.returncode == 0:
                pr_url = result.stdout.strip()
                return pr_url, ""
            return "", result.stderr.strip() or result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            return "", str(exc)
