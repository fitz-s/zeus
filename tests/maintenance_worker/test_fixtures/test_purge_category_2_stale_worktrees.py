# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §7 P5.4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md §"Category 2"
"""
Fixture tests — Category 2: Stale Worktrees.

10 mess scenarios for worktree paths under .claude/worktrees/ and sibling
app-* directories. Idle TTL = 21 days; action = git worktree remove --force.

Forbidden: source code trees inside worktrees (src/, tests/, scripts/, bin/).
These are caught by the source_code_and_tests group in the validator.

Scenarios test that:
  - Worktree metadata files (HEAD, config, gitdir) outside src/ are ALLOWED
  - Source trees inside worktrees remain FORBIDDEN_PATH
  - GIT_EXEC operations return MISSING_PRECHECK (specialized guard required)
  - Sibling app-* directory files follow the same source-code rules
"""
from __future__ import annotations

from pathlib import Path

import pytest

from maintenance_worker.core.validator import ActionValidator
from maintenance_worker.types.operations import Operation
from maintenance_worker.types.results import ValidatorResult


def _v() -> ActionValidator:
    return ActionValidator()


# Synthetic repo root for path construction — does not need to exist.
_REPO = "/home/user/projects/myapp"

_SCENARIOS: list[tuple[str, str, Operation, ValidatorResult]] = [
    # 1. Worktree gitdir link file — DELETE permitted (housekeeping metadata)
    (
        "worktree_gitdir_delete_allowed",
        f"{_REPO}/.claude/worktrees/agent-abc123/gitdir",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 2. Worktree ORIG_HEAD marker — DELETE permitted
    (
        "worktree_orig_head_delete_allowed",
        f"{_REPO}/.claude/worktrees/agent-abc123/ORIG_HEAD",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 3. Worktree HEAD file — READ permitted (not a db or plist)
    (
        "worktree_head_read_allowed",
        f"{_REPO}/.claude/worktrees/agent-abc123/HEAD",
        Operation.READ,
        ValidatorResult.ALLOWED,
    ),
    # 4. Source file inside worktree src/ — MOVE is FORBIDDEN_PATH (Group 1)
    (
        "worktree_src_move_forbidden",
        f"{_REPO}/.claude/worktrees/agent-abc123/src/module.py",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 5. Test file inside worktree — DELETE is FORBIDDEN_PATH (Group 1)
    (
        "worktree_tests_delete_forbidden",
        f"{_REPO}/.claude/worktrees/agent-abc123/tests/test_something.py",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 6. Scripts tree inside worktree — MOVE is FORBIDDEN_PATH (Group 1)
    (
        "worktree_scripts_move_forbidden",
        f"{_REPO}/.claude/worktrees/agent-abc123/scripts/run.py",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 7. GIT_EXEC operation — always MISSING_PRECHECK (specialized guard required)
    (
        "worktree_git_exec_missing_precheck",
        f"{_REPO}/.claude/worktrees/agent-abc123",
        Operation.GIT_EXEC,
        ValidatorResult.MISSING_PRECHECK,
    ),
    # 8. Sibling app-* directory plain text file — DELETE permitted
    (
        "sibling_app_txt_delete_allowed",
        f"{_REPO}/../myapp-feature-branch/notes.txt",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 9. Sibling app-* source tree — MOVE is FORBIDDEN_PATH (Group 1)
    (
        "sibling_app_src_move_forbidden",
        f"{_REPO}/../myapp-feature-branch/src/main.py",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 10. Worktree bin/ dir — MOVE is FORBIDDEN_PATH (Group 1)
    (
        "worktree_bin_move_forbidden",
        f"{_REPO}/.claude/worktrees/agent-abc123/bin/runner",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
]


@pytest.mark.parametrize(
    "scenario_id,path_str,operation,expected",
    [
        pytest.param(sid, p, op, exp, id=sid)
        for sid, p, op, exp in _SCENARIOS
    ],
)
def test_category_2_stale_worktrees(
    scenario_id: str,
    path_str: str,
    operation: Operation,
    expected: ValidatorResult,
) -> None:
    """Validate each Category 2 mess scenario against ActionValidator."""
    validator = _v()
    result = validator.validate_action(Path(path_str), operation)
    assert result == expected, (
        f"[{scenario_id}] validate_action({path_str!r}, {operation.name}) "
        f"returned {result.name!r}, expected {expected.name!r}"
    )
