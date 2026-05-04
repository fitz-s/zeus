# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 4: entries_paused_in_memory_flag adapter.

Probes: memory:_control_state['entries_paused'] via control_plane.is_entries_paused()

Gate-purge 2026-05-04 Stage 3: entries_paused is no longer consulted by the
discovery short-circuit.  Adapter always returns CLEAR with a note that the
flag is informational (derived from gate 3 operator manual pause).
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
                state=BlockState.CLEAR,
                blocking_reason=None,
                state_source="memory:_control_state['entries_paused']",
                source_file_line=self.source_file_line,
                owner_module="src.control.control_plane",
                owner_function="pause_entries",
                raw_probe={"entries_paused": paused, "note": "derived from gate 3; informational — not in short-circuit (gate-purge 2026-05-04)."},
                notes=(
                    "Gate-purge 2026-05-04 Stage 3: entries_paused removed from short-circuit. "
                    "Derived from gate 3 (operator manual pause). Always CLEAR in registry."
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
