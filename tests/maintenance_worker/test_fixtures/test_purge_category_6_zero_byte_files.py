# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §7 P5.4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md §"Category 6"
"""
Fixture tests — Category 6: Empty/Zero-Byte Result Files.

10 mess scenarios for 0-byte files older than 7 days under state/, logs/,
evidence/, proofs/ directories. Forbidden: *.db, *.sqlite*, WAL/SHM files,
any non-zero file, files with active lsof handles (checked by caller).

The ActionValidator enforces the DB/sqlite pattern-based prohibition.
Non-zero and lsof checks are caller-side; validator only knows path + operation.

Scenarios test that:
  - 0-byte placeholder files in logs/, evidence/, proofs/ are ALLOWED for DELETE
  - state/*.db files are FORBIDDEN_PATH (Group 3 runtime/state)
  - state/*.db-wal files are FORBIDDEN_PATH (Group 3)
  - state/*.db-shm files are FORBIDDEN_PATH (Group 3)
  - state/*.sqlite* files are FORBIDDEN_PATH (Group 3)
  - Files in state/calibration/ are FORBIDDEN_PATH (Group 3)
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
    # 1. Zero-byte log file — DELETE permitted
    (
        "zero_byte_log_delete_allowed",
        f"{_REPO}/logs/run_2026_01_01.log",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 2. Zero-byte evidence placeholder — DELETE permitted
    (
        "zero_byte_evidence_delete_allowed",
        f"{_REPO}/evidence/2026-01-01/empty_result.json",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 3. Zero-byte proof file — DELETE permitted
    (
        "zero_byte_proof_delete_allowed",
        f"{_REPO}/proofs/guard_check_empty.txt",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 4. Zero-byte state result (non-DB) — DELETE permitted
    (
        "zero_byte_state_result_delete_allowed",
        f"{_REPO}/state/last_run_output.json",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 5. state/*.db file — FORBIDDEN_PATH (Group 3)
    (
        "state_db_forbidden",
        f"{_REPO}/state/app.db",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 6. state/*.db-wal file — FORBIDDEN_PATH (Group 3)
    (
        "state_db_wal_forbidden",
        f"{_REPO}/state/app.db-wal",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 7. state/*.db-shm file — FORBIDDEN_PATH (Group 3)
    (
        "state_db_shm_forbidden",
        f"{_REPO}/state/app.db-shm",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 8. state/*.sqlite file — FORBIDDEN_PATH (Group 3)
    (
        "state_sqlite_forbidden",
        f"{_REPO}/state/cache.sqlite",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 9. state/calibration/ file — FORBIDDEN_PATH (Group 3)
    (
        "state_calibration_forbidden",
        f"{_REPO}/state/calibration/model_params.json",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 10. Zero-byte log MOVE (to archive) — permitted
    (
        "zero_byte_log_move_allowed",
        f"{_REPO}/logs/stale_empty_2026_01.log",
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
]


@pytest.mark.parametrize(
    "scenario_id,path_str,operation,expected",
    [
        pytest.param(sid, p, op, exp, id=sid)
        for sid, p, op, exp in _SCENARIOS
    ],
)
def test_category_6_zero_byte_files(
    scenario_id: str,
    path_str: str,
    operation: Operation,
    expected: ValidatorResult,
) -> None:
    """Validate each Category 6 mess scenario against ActionValidator."""
    validator = _v()
    result = validator.validate_action(Path(path_str), operation)
    assert result == expected, (
        f"[{scenario_id}] validate_action({path_str!r}, {operation.name}) "
        f"returned {result.name!r}, expected {expected.name!r}"
    )
