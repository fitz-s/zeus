# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §7 P5.4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md §"Category 3"
"""
Fixture tests — Category 3: In-Repo Scratch Directories.

10 mess scenarios for scratch paths matching tmp/, scratch/, debug_*/,
*.scratch.*, *.tmp patterns. TTL = 7 days; action = move to
.archive/scratch/<YYYY-MM>/. Forbidden: any path under src/, tests/,
architecture/, docs/, state/, config/, scripts/.

Scenarios test that:
  - Plain scratch files outside forbidden zones are ALLOWED for MOVE/DELETE
  - Scratch files inside forbidden source/authority zones are FORBIDDEN_PATH
  - .tmp suffix files in scratch area are ALLOWED
  - debug_ prefixed directories outside protected trees are ALLOWED
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
    # 1. tmp/ file at repo root — MOVE permitted
    (
        "tmp_root_file_move_allowed",
        f"{_REPO}/tmp/scratch_output.json",
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 2. scratch/ file — DELETE permitted
    (
        "scratch_file_delete_allowed",
        f"{_REPO}/scratch/analysis_draft.md",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 3. debug_ directory file — MOVE permitted
    (
        "debug_dir_file_move_allowed",
        f"{_REPO}/debug_run_20260101/output.log",
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 4. .scratch. middle-extension file — DELETE permitted
    (
        "scratch_extension_delete_allowed",
        f"{_REPO}/report.scratch.csv",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 5. .tmp suffix file in scratch area — MOVE permitted
    (
        "tmp_suffix_file_move_allowed",
        f"{_REPO}/scratch/working_data.tmp",
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 6. Scratch file inside src/ — FORBIDDEN_PATH (Group 1 wins)
    (
        "scratch_inside_src_forbidden",
        f"{_REPO}/src/tmp/intermediate.json",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 7. Scratch file inside tests/ — FORBIDDEN_PATH (Group 1)
    (
        "scratch_inside_tests_forbidden",
        f"{_REPO}/tests/scratch/fixture_draft.json",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 8. Scratch file inside scripts/ — FORBIDDEN_PATH (Group 1)
    (
        "scratch_inside_scripts_forbidden",
        f"{_REPO}/scripts/debug_output/run.log",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 9. Scratch file inside architecture/ — FORBIDDEN_PATH (Group 2)
    (
        "scratch_inside_architecture_forbidden",
        f"{_REPO}/architecture/tmp/draft_diagram.md",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 10. MKDIR on scratch area — permitted (creating archive target)
    (
        "scratch_mkdir_allowed",
        f"{_REPO}/.archive/scratch/2026-01",
        Operation.MKDIR,
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
def test_category_3_scratch_directories(
    scenario_id: str,
    path_str: str,
    operation: Operation,
    expected: ValidatorResult,
) -> None:
    """Validate each Category 3 mess scenario against ActionValidator."""
    validator = _v()
    result = validator.validate_action(Path(path_str), operation)
    assert result == expected, (
        f"[{scenario_id}] validate_action({path_str!r}, {operation.name}) "
        f"returned {result.name!r}, expected {expected.name!r}"
    )
