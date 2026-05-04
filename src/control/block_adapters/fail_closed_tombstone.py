# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 1: auto_pause_failclosed_tombstone adapter.

Gate retired 2026-05-04 (Stage 2 gate-purge): tombstone machinery removed.
Adapter always returns CLEAR so zeus_blocks.py / registry snapshot reflect
the retired state.  Raw probe records the retirement note.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class FailClosedTombstoneAdapter:
    id = 1
    name = "auto_pause_failclosed_tombstone"
    category = BlockCategory.FILE_FAIL_CLOSED
    stage = BlockStage.DISCOVERY
    source_file_line = "src/control/control_plane.py:385"

    def probe(self, deps: RegistryDeps) -> Block:
        return Block(
            id=self.id,
            name=self.name,
            category=self.category,
            stage=self.stage,
            state=BlockState.CLEAR,
            blocking_reason=None,
            state_source="file:state/auto_pause_failclosed.tombstone",
            source_file_line=self.source_file_line,
            owner_module="src.control.heartbeat_supervisor",
            owner_function="_write_failclosed_tombstone",
            raw_probe={"retired": True, "note": "Gate retired 2026-05-04 Stage 2 gate-purge. Runtime safety covered by gate 6/9/10."},
            notes="Gate retired 2026-05-04. Always CLEAR.",
        )
