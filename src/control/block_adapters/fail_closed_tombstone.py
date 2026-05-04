# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 1: auto_pause_failclosed_tombstone adapter.

Probes: state/auto_pause_failclosed.tombstone (file existence)
Blocks when: tombstone file exists.
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
        tombstone_path = deps.state_dir / "auto_pause_failclosed.tombstone"
        try:
            exists = tombstone_path.exists()
            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.BLOCKING if exists else BlockState.CLEAR,
                blocking_reason="entries_paused" if exists else None,
                state_source="file:state/auto_pause_failclosed.tombstone",
                source_file_line=self.source_file_line,
                owner_module="src.control.heartbeat_supervisor",
                owner_function="_write_failclosed_tombstone",
                raw_probe={"tombstone_exists": exists, "path": str(tombstone_path)},
                notes=(
                    "Also written by src/control/control_plane.py:278 (pause_entries path). "
                    "Read site at 385 is inside refresh_control_state()."
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
                state_source="file:state/auto_pause_failclosed.tombstone",
                source_file_line=self.source_file_line,
                owner_module="src.control.heartbeat_supervisor",
                owner_function="_write_failclosed_tombstone",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
