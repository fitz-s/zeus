# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §7 P5.4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md §"Category 5"
"""
Fixture tests — Category 5: Stale Untracked Top-Level Files.

10 mess scenarios for files appearing as `git status ??` at repo root or
one level deep. TTL = 14 days. Forbidden: .env*, *credential*, *secret*,
*token*, *key* files — these must never be deleted by automated tooling.

Scenarios test that:
  - Plain untracked files (notes, logs, drafts) are ALLOWED for DELETE/MOVE
  - .env* files are FORBIDDEN_PATH (Group 4 secrets)
  - *credential* files are FORBIDDEN_PATH (Group 4)
  - *secret* files are FORBIDDEN_PATH (Group 4)
  - *token* files are FORBIDDEN_PATH (Group 4)
  - *key* files are FORBIDDEN_PATH (Group 4)
  - auth-profiles.json is FORBIDDEN_PATH (Group 4)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from maintenance_worker.core.validator import ActionValidator
from maintenance_worker.types.operations import Operation
from maintenance_worker.types.results import ValidatorResult


def _v() -> ActionValidator:
    return ActionValidator()


_REPO = "/home/user/projects/myapp"

_SCENARIOS: list[tuple[str, str, Operation, ValidatorResult]] = [
    # 1. Untracked plain text notes file — DELETE permitted
    (
        "untracked_notes_delete_allowed",
        f"{_REPO}/scratch_notes.txt",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 2. Untracked draft markdown — MOVE permitted
    (
        "untracked_draft_move_allowed",
        f"{_REPO}/draft_plan.md",
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 3. Untracked log file — DELETE permitted
    (
        "untracked_log_delete_allowed",
        f"{_REPO}/run_output.log",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 4. .env file — FORBIDDEN_PATH (Group 4 secrets)
    (
        "env_file_forbidden",
        f"{_REPO}/.env",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 5. .env.local file — FORBIDDEN_PATH (Group 4)
    (
        "env_local_file_forbidden",
        f"{_REPO}/.env.local",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 6. *credential* file — FORBIDDEN_PATH (Group 4)
    (
        "credential_file_forbidden",
        f"{_REPO}/my_credentials.json",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 7. *secret* file — FORBIDDEN_PATH (Group 4)
    (
        "secret_file_forbidden",
        f"{_REPO}/app_secret.json",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 8. *token* file — FORBIDDEN_PATH (Group 4)
    (
        "token_file_forbidden",
        f"{_REPO}/access_token.txt",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 9. *key* file — FORBIDDEN_PATH (Group 4)
    (
        "key_file_forbidden",
        f"{_REPO}/api_key.txt",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 10. auth-profiles.json — FORBIDDEN_PATH (Group 4, explicit rule)
    (
        "auth_profiles_forbidden",
        f"{_REPO}/auth-profiles.json",
        Operation.DELETE,
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
def test_category_5_stale_untracked_files(
    scenario_id: str,
    path_str: str,
    operation: Operation,
    expected: ValidatorResult,
) -> None:
    """Validate each Category 5 mess scenario against ActionValidator."""
    validator = _v()
    result = validator.validate_action(Path(path_str), operation)
    assert result == expected, (
        f"[{scenario_id}] validate_action({path_str!r}, {operation.name}) "
        f"returned {result.name!r}, expected {expected.name!r}"
    )
