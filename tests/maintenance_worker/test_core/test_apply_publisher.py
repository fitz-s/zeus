# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3.5 (P5.1↔P5.5 boundary)
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Forbidden Actions" + §"Audit-by-Grep Discipline"
"""
Tests for maintenance_worker.core.apply_publisher.

Covers:
- publish: skipped when dry_run_only=True
- publish: skipped when no mutations
- publish: URL allowlist check is called before any git operation (structural)
- publish: FORBIDDEN_OPERATION on allowlist failure → no git commands
- publish: full happy path (stage → commit → push → PublishResult with sha)
- publish: commit rollback on push failure (--soft, NEVER --hard)
- publish: PR opened when requires_pr=True
- publish: PR failure is non-fatal (commit + push succeeded path still returns sha)
- publish: protected branch push refused (main/master)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from maintenance_worker.core.apply_publisher import ApplyPublisher, PublishResult
from maintenance_worker.core.install_metadata import InstallMetadata
from maintenance_worker.core.validator import ActionValidator
from maintenance_worker.types.results import ApplyResult, ValidatorResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_install_meta(allowed_urls: tuple[str, ...] = ("https://github.com/org/repo.git",)) -> InstallMetadata:
    return InstallMetadata(
        schema_version=1,
        first_run_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        agent_version="0.1.0",
        install_run_id="testinstall-00000000",
        allowed_remote_urls=allowed_urls,
    )


def _make_apply_result(
    task_id: str = "test_task",
    dry_run_only: bool = False,
    requires_pr: bool = False,
    with_mutations: bool = True,
) -> ApplyResult:
    if with_mutations:
        created = (Path("/tmp/created_file.txt"),)
    else:
        created = ()
    return ApplyResult(
        task_id=task_id,
        created=created,
        dry_run_only=dry_run_only,
        requires_pr=requires_pr,
    )


def _make_publisher(
    tmp_path: Path,
    install_meta: InstallMetadata | None = None,
    validator: ActionValidator | None = None,
    branch: str = "maintenance/test-branch",
) -> ApplyPublisher:
    return ApplyPublisher(
        repo_root=tmp_path,
        install_meta=install_meta or _make_install_meta(),
        validator=validator,
        branch=branch,
    )


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def test_publish_skipped_when_dry_run_only(tmp_path: Path) -> None:
    """dry_run_only=True → skipped=True, no git commands."""
    publisher = _make_publisher(tmp_path)
    result = publisher.publish(
        _make_apply_result(dry_run_only=True),
        run_id="testid-12345678",
    )
    assert result.skipped is True
    assert result.commit_sha == ""
    assert result.error == ""


def test_publish_skipped_when_no_mutations(tmp_path: Path) -> None:
    """Empty mutations (no moved/deleted/created) → skipped=True."""
    publisher = _make_publisher(tmp_path)
    result = publisher.publish(
        _make_apply_result(with_mutations=False),
        run_id="testid-12345678",
    )
    assert result.skipped is True


# ---------------------------------------------------------------------------
# Allowlist check — structural assertion
# ---------------------------------------------------------------------------


def test_publish_calls_allowlist_check_before_git_ops(tmp_path: Path) -> None:
    """
    Structural test: check_remote_url_allowlist is called before any git stage/commit.

    The validator mock records all calls. We verify allowlist check happens
    and that git add is NOT called when allowlist returns FORBIDDEN_OPERATION.
    """
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.FORBIDDEN_OPERATION

    publisher = ApplyPublisher(
        repo_root=tmp_path,
        install_meta=_make_install_meta(),
        validator=mock_validator,
        branch="maintenance/branch",
    )

    with patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/org/repo.git"):
        with patch.object(publisher, "_stage_changes") as mock_stage:
            result = publisher.publish(
                _make_apply_result(),
                run_id="testid-12345678",
            )

    # Allowlist must have been called
    mock_validator.check_remote_url_allowlist.assert_called_once()
    # git add must NOT have been called
    mock_stage.assert_not_called()
    assert result.error != ""
    assert "allowlist" in result.error.lower() or "not in" in result.error.lower()


def test_publish_forbidden_operation_on_allowlist_failure(tmp_path: Path) -> None:
    """FORBIDDEN_OPERATION from allowlist → error in result, no commit."""
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.FORBIDDEN_OPERATION

    publisher = ApplyPublisher(
        repo_root=tmp_path,
        install_meta=_make_install_meta(),
        validator=mock_validator,
        branch="maintenance/branch",
    )

    with patch.object(publisher, "_resolve_remote_url", return_value="https://evil.com/repo.git"):
        result = publisher.publish(_make_apply_result(), run_id="testid-12345678")

    assert result.commit_sha == ""
    assert result.error


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_publish_happy_path_returns_commit_sha(tmp_path: Path) -> None:
    """Full happy path: stage → commit → push → PublishResult with sha."""
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.ALLOWED

    publisher = ApplyPublisher(
        repo_root=tmp_path,
        install_meta=_make_install_meta(),
        validator=mock_validator,
        branch="maintenance/branch",
    )

    fake_sha = "abc123def456"

    with patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/org/repo.git"):
        with patch.object(publisher, "_stage_changes", return_value=(True, "")):
            with patch.object(publisher, "_commit", return_value=(fake_sha, "")):
                with patch("maintenance_worker.core.apply_publisher.set_commit_identity") as mock_ctx:
                    mock_ctx.return_value.__enter__ = lambda s: None
                    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                    with patch.object(publisher, "_push", return_value=(True, "")):
                        result = publisher.publish(
                            _make_apply_result(),
                            run_id="testid-12345678",
                        )

    assert result.commit_sha == fake_sha
    assert result.skipped is False
    assert result.rolled_back is False
    assert result.error == ""


def test_publish_happy_path_no_pr_when_not_required(tmp_path: Path) -> None:
    """requires_pr=False → pr_url is empty in result."""
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.ALLOWED

    publisher = ApplyPublisher(
        repo_root=tmp_path,
        install_meta=_make_install_meta(),
        validator=mock_validator,
        branch="maintenance/branch",
    )

    with patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/org/repo.git"):
        with patch.object(publisher, "_stage_changes", return_value=(True, "")):
            with patch.object(publisher, "_commit", return_value=("sha123", "")):
                with patch("maintenance_worker.core.apply_publisher.set_commit_identity") as mock_ctx:
                    mock_ctx.return_value.__enter__ = lambda s: None
                    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                    with patch.object(publisher, "_push", return_value=(True, "")):
                        with patch.object(publisher, "_open_pr") as mock_pr:
                            result = publisher.publish(
                                _make_apply_result(requires_pr=False),
                                run_id="testid-12345678",
                            )

    mock_pr.assert_not_called()
    assert result.pr_url == ""


# ---------------------------------------------------------------------------
# Rollback on push failure
# ---------------------------------------------------------------------------


def test_publish_rollback_on_push_failure(tmp_path: Path) -> None:
    """Push failure → rollback commit via --soft, rolled_back=True."""
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.ALLOWED

    publisher = ApplyPublisher(
        repo_root=tmp_path,
        install_meta=_make_install_meta(),
        validator=mock_validator,
        branch="maintenance/branch",
    )

    with patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/org/repo.git"):
        with patch.object(publisher, "_stage_changes", return_value=(True, "")):
            with patch.object(publisher, "_commit", return_value=("sha123", "")):
                with patch("maintenance_worker.core.apply_publisher.set_commit_identity") as mock_ctx:
                    mock_ctx.return_value.__enter__ = lambda s: None
                    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                    with patch.object(publisher, "_push", return_value=(False, "network error")):
                        with patch.object(publisher, "_rollback_commit", return_value=True) as mock_rollback:
                            result = publisher.publish(
                                _make_apply_result(),
                                run_id="testid-12345678",
                            )

    mock_rollback.assert_called_once()
    assert result.rolled_back is True
    assert "push failed" in result.error.lower() or "network error" in result.error


def test_rollback_uses_soft_not_hard(tmp_path: Path) -> None:
    """
    Structural safety test: _rollback_commit uses '--soft', NEVER '--hard'.

    This directly invokes _rollback_commit via subprocess mock and inspects
    the command. SAFETY_CONTRACT §"Forbidden Actions": git reset --hard is blocked.
    """
    publisher = _make_publisher(tmp_path)

    captured_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("maintenance_worker.core.apply_publisher.subprocess.run", side_effect=fake_run):
        publisher._rollback_commit()

    reset_calls = [c for c in captured_cmds if "reset" in c]
    assert reset_calls, "Expected at least one git reset call"
    for cmd in reset_calls:
        assert "--hard" not in cmd, f"--hard must NEVER appear in rollback: {cmd}"
        assert "--soft" in cmd, f"Expected --soft in rollback: {cmd}"


# ---------------------------------------------------------------------------
# Stage failure (no commit to roll back)
# ---------------------------------------------------------------------------


def test_publish_stage_failure_no_rollback(tmp_path: Path) -> None:
    """Stage failure → error returned, no rollback (no commit happened)."""
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.ALLOWED

    publisher = ApplyPublisher(
        repo_root=tmp_path,
        install_meta=_make_install_meta(),
        validator=mock_validator,
        branch="maintenance/branch",
    )

    with patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/org/repo.git"):
        with patch.object(publisher, "_stage_changes", return_value=(False, "index locked")):
            with patch.object(publisher, "_rollback_commit") as mock_rollback:
                result = publisher.publish(_make_apply_result(), run_id="testid-12345678")

    mock_rollback.assert_not_called()
    assert result.error
    assert result.commit_sha == ""


# ---------------------------------------------------------------------------
# Protected branch check
# ---------------------------------------------------------------------------


def test_publish_refuses_push_to_main(tmp_path: Path) -> None:
    """Pushing to 'main' is refused at the publisher level."""
    publisher = _make_publisher(tmp_path, branch="main")
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.ALLOWED
    publisher._validator = mock_validator

    with patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/org/repo.git"):
        with patch.object(publisher, "_stage_changes", return_value=(True, "")):
            with patch.object(publisher, "_commit", return_value=("sha123", "")):
                with patch("maintenance_worker.core.apply_publisher.set_commit_identity") as mock_ctx:
                    mock_ctx.return_value.__enter__ = lambda s: None
                    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                    result = publisher.publish(_make_apply_result(), run_id="testid-12345678")

    assert result.error
    assert "main" in result.error.lower() or "protected" in result.error.lower()


def test_publish_refuses_push_to_master(tmp_path: Path) -> None:
    """Pushing to 'master' is refused at the publisher level."""
    publisher = _make_publisher(tmp_path, branch="master")
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.ALLOWED
    publisher._validator = mock_validator

    with patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/org/repo.git"):
        with patch.object(publisher, "_stage_changes", return_value=(True, "")):
            with patch.object(publisher, "_commit", return_value=("sha123", "")):
                with patch("maintenance_worker.core.apply_publisher.set_commit_identity") as mock_ctx:
                    mock_ctx.return_value.__enter__ = lambda s: None
                    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                    result = publisher.publish(_make_apply_result(), run_id="testid-12345678")

    assert result.error
    assert "master" in result.error.lower() or "protected" in result.error.lower()


# ---------------------------------------------------------------------------
# PR opened when requires_pr=True
# ---------------------------------------------------------------------------


def test_publish_opens_pr_when_required(tmp_path: Path) -> None:
    """requires_pr=True → _open_pr is called and pr_url in result."""
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.ALLOWED

    publisher = ApplyPublisher(
        repo_root=tmp_path,
        install_meta=_make_install_meta(),
        validator=mock_validator,
        branch="maintenance/branch",
    )

    with patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/org/repo.git"):
        with patch.object(publisher, "_stage_changes", return_value=(True, "")):
            with patch.object(publisher, "_commit", return_value=("sha123", "")):
                with patch("maintenance_worker.core.apply_publisher.set_commit_identity") as mock_ctx:
                    mock_ctx.return_value.__enter__ = lambda s: None
                    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                    with patch.object(publisher, "_push", return_value=(True, "")):
                        with patch.object(
                            publisher, "_open_pr",
                            return_value=("https://github.com/org/repo/pull/42", "")
                        ):
                            result = publisher.publish(
                                _make_apply_result(requires_pr=True),
                                run_id="testid-12345678",
                            )

    assert result.pr_url == "https://github.com/org/repo/pull/42"
    assert result.commit_sha == "sha123"


def test_publish_pr_failure_is_non_fatal(tmp_path: Path) -> None:
    """PR failure after successful push → commit_sha present, error describes PR failure."""
    mock_validator = MagicMock(spec=ActionValidator)
    mock_validator.check_remote_url_allowlist.return_value = ValidatorResult.ALLOWED

    publisher = ApplyPublisher(
        repo_root=tmp_path,
        install_meta=_make_install_meta(),
        validator=mock_validator,
        branch="maintenance/branch",
    )

    with patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/org/repo.git"):
        with patch.object(publisher, "_stage_changes", return_value=(True, "")):
            with patch.object(publisher, "_commit", return_value=("sha123", "")):
                with patch("maintenance_worker.core.apply_publisher.set_commit_identity") as mock_ctx:
                    mock_ctx.return_value.__enter__ = lambda s: None
                    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                    with patch.object(publisher, "_push", return_value=(True, "")):
                        with patch.object(publisher, "_open_pr", return_value=("", "gh not found")):
                            result = publisher.publish(
                                _make_apply_result(requires_pr=True),
                                run_id="testid-12345678",
                            )

    # Commit + push succeeded; sha is preserved even though PR failed
    assert result.commit_sha == "sha123"
    assert result.error  # error mentions PR failure
    assert result.rolled_back is False


# ---------------------------------------------------------------------------
# PublishResult structure
# ---------------------------------------------------------------------------


def test_publish_result_is_frozen() -> None:
    """PublishResult is a frozen dataclass."""
    r = PublishResult(task_id="t")
    with pytest.raises((AttributeError, TypeError)):
        r.commit_sha = "new"  # type: ignore[misc]


def test_publish_result_defaults() -> None:
    """PublishResult has sensible defaults."""
    r = PublishResult(task_id="t")
    assert r.commit_sha == ""
    assert r.pr_url == ""
    assert r.rolled_back is False
    assert r.skipped is False
    assert r.error == ""


# ---------------------------------------------------------------------------
# Resolve remote URL failure
# ---------------------------------------------------------------------------


def test_publish_error_on_unresolvable_remote(tmp_path: Path) -> None:
    """If remote URL can't be resolved, publish returns an error (fail-safe)."""
    publisher = _make_publisher(tmp_path)

    with patch.object(publisher, "_resolve_remote_url", return_value=None):
        result = publisher.publish(_make_apply_result(), run_id="testid-12345678")

    assert result.error
    assert result.commit_sha == ""
