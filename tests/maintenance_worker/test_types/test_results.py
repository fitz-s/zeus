# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3, §6 (P5.0a)
#                  SAFETY_CONTRACT.md §"Pre-Action Validator" (5 ValidatorResult values)
#                  SAFETY_CONTRACT.md §3.5 (ApplyResult does not commit to git)
"""
Tests for maintenance_worker.types.results — ValidatorResult, RefusalReason
(re-export), ApplyResult, CheckResult.
"""
from pathlib import Path

import pytest

from maintenance_worker.types.results import (
    ApplyResult,
    CheckResult,
    RefusalReason,
    ValidatorResult,
)


class TestValidatorResult:
    def test_exactly_five_members(self) -> None:
        """SAFETY_CONTRACT §128-136: exactly 5 ValidatorResult values."""
        expected = {
            "ALLOWED",
            "FORBIDDEN_PATH",
            "FORBIDDEN_OPERATION",
            "MISSING_PRECHECK",
            "ALLOWED_BUT_DRY_RUN_ONLY",
        }
        assert {v.value for v in ValidatorResult} == expected

    def test_forbidden_path_not_allowed(self) -> None:
        assert ValidatorResult.FORBIDDEN_PATH is not ValidatorResult.ALLOWED

    def test_dry_run_only_distinct_from_allowed(self) -> None:
        """DRY_RUN_ONLY prevents live apply; must differ from ALLOWED."""
        assert ValidatorResult.ALLOWED_BUT_DRY_RUN_ONLY is not ValidatorResult.ALLOWED

    def test_string_subclass(self) -> None:
        assert isinstance(ValidatorResult.ALLOWED, str)


class TestRefusalReasonReexport:
    def test_refusal_reason_importable_from_results(self) -> None:
        """Brief smoke test imports RefusalReason from results; verify re-export."""
        assert RefusalReason.KILL_SWITCH.value == "KILL_SWITCH"

    def test_hard_guards_present(self) -> None:
        hard = {
            "KILL_SWITCH", "DIRTY_REPO", "ACTIVE_REBASE", "LOW_DISK",
            "INFLIGHT_PR", "SELF_QUARANTINED",
            "FORBIDDEN_PATH_VIOLATION", "FORBIDDEN_OPERATION_VIOLATION",
        }
        actual = {r.value for r in RefusalReason}
        assert hard.issubset(actual)

    def test_soft_guards_present(self) -> None:
        soft = {"MAINTENANCE_PAUSED", "ONCALL_QUIET"}
        actual = {r.value for r in RefusalReason}
        assert soft.issubset(actual)


class TestCheckResult:
    def test_defaults(self) -> None:
        cr = CheckResult(ok=True)
        assert cr.ok is True
        assert cr.reason == ""
        assert cr.details == {}

    def test_frozen(self) -> None:
        cr = CheckResult(ok=False, reason="KILL_SWITCH")
        with pytest.raises((AttributeError, TypeError)):
            cr.ok = True  # type: ignore[misc]

    def test_failure_with_reason(self) -> None:
        cr = CheckResult(ok=False, reason="DIRTY_REPO", details={"branch": "main"})
        assert cr.ok is False
        assert cr.reason == "DIRTY_REPO"
        assert cr.details["branch"] == "main"


class TestApplyResult:
    def test_defaults(self) -> None:
        ar = ApplyResult(task_id="test_task")
        assert ar.task_id == "test_task"
        assert ar.moved == ()
        assert ar.deleted == ()
        assert ar.created == ()
        assert ar.requires_pr is False
        assert ar.dry_run_only is False

    def test_frozen(self) -> None:
        ar = ApplyResult(task_id="t1")
        with pytest.raises((AttributeError, TypeError)):
            ar.requires_pr = True  # type: ignore[misc]

    def test_with_moves(self) -> None:
        src = Path("/repo/docs/old")
        dst = Path("/repo/docs/archive/2026-Q2/old")
        ar = ApplyResult(
            task_id="archive_task",
            moved=((src, dst),),
            requires_pr=True,
        )
        assert len(ar.moved) == 1
        assert ar.moved[0] == (src, dst)
        assert ar.requires_pr is True

    def test_dry_run_only_flag(self) -> None:
        ar = ApplyResult(task_id="t2", dry_run_only=True)
        assert ar.dry_run_only is True
        assert ar.requires_pr is False
