# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §7 P5.4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/PURGE_CATEGORIES.md §"Category 1"
"""
Fixture tests — Category 1: LaunchAgent Backup Shrapnel.

10 mess scenarios exercising the ActionValidator against LaunchAgents paths.
Canonical rule: files matching ~/Library/LaunchAgents/*.bak|*.backup|*.replaced|
*.locked|*before_* are candidates for move to .archive/<YYYY-MM>/.
Active plists (*.plist directly under LaunchAgents, not under .archive/) are FORBIDDEN.

Each scenario:
  - Sets up a path (real or synthetic)
  - Calls validate_action() with the appropriate Operation
  - Asserts the expected ValidatorResult
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from maintenance_worker.core.validator import ActionValidator
from maintenance_worker.types.operations import Operation
from maintenance_worker.types.results import ValidatorResult

# ---------------------------------------------------------------------------
# Helper — build a validator (no state_dir needed for these path checks)
# ---------------------------------------------------------------------------

_LAUNCH_AGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")
_LAUNCH_AGENTS_ARCHIVE = os.path.join(_LAUNCH_AGENTS_DIR, ".archive")


def _v() -> ActionValidator:
    return ActionValidator()


# ---------------------------------------------------------------------------
# Parametrized scenarios
# ---------------------------------------------------------------------------
# Each tuple: (scenario_id, path_str, operation, expected_result)
# Paths use generic names — no Zeus identifiers.

_SCENARIOS: list[tuple[str, str, Operation, ValidatorResult]] = [
    # 1. Backup file (.bak) — MOVE is permitted (archive destination not checked here)
    (
        "bak_file_move_allowed",
        os.path.join(_LAUNCH_AGENTS_DIR, "com.example.foo.plist.bak"),
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 2. Backup file (.backup) — DELETE is permitted
    (
        "backup_file_delete_allowed",
        os.path.join(_LAUNCH_AGENTS_DIR, "com.example.bar.plist.backup"),
        Operation.DELETE,
        ValidatorResult.ALLOWED,
    ),
    # 3. Replaced backup — MOVE permitted
    (
        "replaced_file_move_allowed",
        os.path.join(_LAUNCH_AGENTS_DIR, "com.example.baz.plist.replaced"),
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 4. Locked backup — MOVE permitted
    (
        "locked_file_move_allowed",
        os.path.join(_LAUNCH_AGENTS_DIR, "com.example.qux.plist.locked"),
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 5. before_update suffix backup (non-.plist filename) — MOVE permitted
    #    The before_ prefix naming convention uses a suffix form:
    #    com.example.svc.plist.before_update (not active; no .plist suffix)
    (
        "before_suffix_move_allowed",
        os.path.join(_LAUNCH_AGENTS_DIR, "com.example.svc.plist.before_update"),
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 6. Active plist directly under LaunchAgents — MOVE is FORBIDDEN_PATH
    (
        "active_plist_move_forbidden",
        os.path.join(_LAUNCH_AGENTS_DIR, "com.example.active.plist"),
        Operation.MOVE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 7. Active plist — DELETE is FORBIDDEN_PATH
    (
        "active_plist_delete_forbidden",
        os.path.join(_LAUNCH_AGENTS_DIR, "com.example.live.plist"),
        Operation.DELETE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 8. Active plist — WRITE is FORBIDDEN_PATH
    (
        "active_plist_write_forbidden",
        os.path.join(_LAUNCH_AGENTS_DIR, "com.example.daemon.plist"),
        Operation.WRITE,
        ValidatorResult.FORBIDDEN_PATH,
    ),
    # 9. Archived plist (under .archive/) — MOVE permitted (not an active plist)
    (
        "archived_plist_move_allowed",
        os.path.join(_LAUNCH_AGENTS_ARCHIVE, "2026-01", "com.example.old.plist"),
        Operation.MOVE,
        ValidatorResult.ALLOWED,
    ),
    # 10. Backup file READ — READ of a LaunchAgents backup is also FORBIDDEN_PATH
    #     (SAFETY_CONTRACT §"Validator Semantics" (a): READ is not exempt for protected paths)
    #     Active plist rule applies to READ too.
    (
        "active_plist_read_forbidden",
        os.path.join(_LAUNCH_AGENTS_DIR, "com.example.readonly.plist"),
        Operation.READ,
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
def test_category_1_launchagent_backups(
    scenario_id: str,
    path_str: str,
    operation: Operation,
    expected: ValidatorResult,
) -> None:
    """
    Validate each Category 1 mess scenario against ActionValidator.

    Does NOT touch the real filesystem — paths are passed as strings;
    validator pattern-matches them without requiring the file to exist.
    """
    validator = _v()
    result = validator.validate_action(Path(path_str), operation)
    assert result == expected, (
        f"[{scenario_id}] validate_action({path_str!r}, {operation.name}) "
        f"returned {result.name!r}, expected {expected.name!r}"
    )
