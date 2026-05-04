# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 4: entries_paused_in_memory_flag adapter.

Probes: memory:_control_state['entries_paused'] via control_plane.is_entries_paused()
Blocks when: is_entries_paused() returns True.

Informational/derived: this reflects state that gates 1 and 3 already determine,
but gives the registry direct visibility into the in-memory flag.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class EntriesPausedFlagAdapter:
    id = 4
    name = "entries_paused_in_memory_flag"
    category = BlockCategory.DB_CONTROL_PLANE
    stage = BlockStage.DISCOVERY
    source_file_line = "src/engine/cycle_runner.py:736"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            from src.control import control_plane

            paused = control_plane.is_entries_paused()

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.BLOCKING if paused else BlockState.CLEAR,
                blocking_reason="entries_paused" if paused else None,
                state_source="memory:_control_state['entries_paused']",
                source_file_line=self.source_file_line,
                owner_module="src.control.control_plane",
                owner_function="pause_entries",
                raw_probe={"entries_paused": paused},
                notes=(
                    "In-memory dict populated from DB (#3) + tombstone (#1) by "
                    "refresh_control_state(). is_entries_paused() at control_plane.py:118-119."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.UNKNOWN,
                blocking_reason=f"adapter_error:{exc.__class__.__name__}: {exc}",
                state_source="memory:_control_state['entries_paused']",
                source_file_line=self.source_file_line,
                owner_module="src.control.control_plane",
                owner_function="pause_entries",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
