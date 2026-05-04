# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 2: auto_pause_streak_escalation adapter.

Gate retired 2026-05-04 (Stage 2 gate-purge): auto_pause_streak module deleted
and streak machinery removed from cycle_runner.py exception handler.
Adapter always returns CLEAR.  Does NOT import the deleted module.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class AutoPauseStreakAdapter:
    id = 2
    name = "auto_pause_streak_escalation"
    category = BlockCategory.FILE_FAIL_CLOSED
    stage = BlockStage.DISCOVERY
    source_file_line = "src/engine/cycle_runner.py:767"

    def probe(self, deps: RegistryDeps) -> Block:
        return Block(
            id=self.id,
            name=self.name,
            category=self.category,
            stage=self.stage,
            state=BlockState.CLEAR,
            blocking_reason=None,
            state_source="file:state/auto_pause_streak.json",
            source_file_line=self.source_file_line,
            owner_module="src.control.auto_pause_streak",
            owner_function="record_failure",
            raw_probe={"retired": True, "note": "Gate retired 2026-05-04 Stage 2 gate-purge. auto_pause_streak module deleted."},
            notes="Gate retired 2026-05-04. Always CLEAR.",
        )
