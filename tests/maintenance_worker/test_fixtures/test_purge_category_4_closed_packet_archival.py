# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §7 P5.4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md §"Category 4"
"""
Fixture tests — Category 4: Closed-Packet Archival Candidates.

10 mess scenarios for docs/operations/task_*/ directories. TTL = 60 days
since last modification, no external references, no authority-status.
Forbidden: referenced packets (anything under docs/reference/), authority
surfaces (AGENTS.md, CLAUDE.md at any depth), and architecture/** paths.

Scenarios test that:
  - Plain task packet files (not authority surfaces) are ALLOWED for MOVE
  - Referenced docs/reference/ paths are FORBIDDEN_PATH (Group 2)
  - AGENTS.md files inside packets are FORBIDDEN_PATH (exact_name match)
  - CLAUDE.md files inside packets are FORBIDDEN_PATH (exact_name match)
  - architecture/** paths are FORBIDDEN_PATH (Group 2)
  - GH_EXEC returns MISSING_PRECHECK
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
    # 1. Closed task packet TASK_CATALOG.yaml — MOVE permitted
    (
        "closed_packet_yaml_move_allowed",
        f"{_REPO}/docs/operations/task_2025-12-01_old_feature/TASK_CATALOG.yaml",
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 2. Closed task packet design doc — MOVE permitted
    (
        "closed_packet_design_move_allowed",
        f"{_REPO}/docs/operations/task_2025-11-15_cleanup/DESIGN.md",
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 3. Closed task packet scaffold — DELETE permitted
    (
        "closed_packet_scaffold_delete_allowed",
        f"{_REPO}/docs/operations/task_2025-10-01_old_work/SCAFFOLD.md",
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 4. docs/reference/ path — FORBIDDEN_PATH (Group 2 authority surface)
    (
        "docs_reference_move_forbidden",
        f"{_REPO}/docs/reference/api_contract.md",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 5. AGENTS.md inside packet — FORBIDDEN_PATH (exact_name match, Group 2)
    (
        "agents_md_inside_packet_forbidden",
        f"{_REPO}/docs/operations/task_2025-12-01_old_feature/AGENTS.md",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 6. CLAUDE.md inside packet — FORBIDDEN_PATH (exact_name match, Group 2)
    (
        "claude_md_inside_packet_forbidden",
        f"{_REPO}/docs/operations/task_2025-12-01_old_feature/CLAUDE.md",
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 7. architecture/ path — FORBIDDEN_PATH (Group 2)
    (
        "architecture_path_forbidden",
        f"{_REPO}/architecture/db_table_ownership.yaml",
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 8. GH_EXEC on packet — MISSING_PRECHECK (specialized guard required)
    (
        "packet_gh_exec_missing_precheck",
        f"{_REPO}/docs/operations/task_2025-12-01_old_feature",
        Operation.GH_EXEC,
        ValidatorResult.MISSING_PRECHECK,
    ),
    # 9. Closed packet safety contract — MOVE permitted (not a reference doc)
    (
        "closed_packet_safety_contract_move_allowed",
        f"{_REPO}/docs/operations/task_2025-09-01_phase_x/SAFETY_CONTRACT.md",
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 10. Batch done log inside packet — DELETE permitted
    (
        "closed_packet_batch_done_delete_allowed",
        f"{_REPO}/docs/operations/task_2025-08-15_retired/BATCH_DONE.md",
        Operation.DELETE,
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
def test_category_4_closed_packet_archival(
    scenario_id: str,
    path_str: str,
    operation: Operation,
    expected: ValidatorResult,
) -> None:
    """Validate each Category 4 mess scenario against ActionValidator."""
    validator = _v()
    result = validator.validate_action(Path(path_str), operation)
    assert result == expected, (
        f"[{scenario_id}] validate_action({path_str!r}, {operation.name}) "
        f"returned {result.name!r}, expected {expected.name!r}"
    )
