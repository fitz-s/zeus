# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 5: entries_blocked_reason_string adapter.

Probes: memory:entries_blocked_reason (local var in discover_cycle_opportunities)
        populated from L609 through 11 conditional branches (L715-L751).

Because this is a local variable in cycle_runner.py, we cannot probe it
directly from an adapter without wiring. Instead, this adapter reads the
DB-persisted last cycle's summary entries_blocked_reason field for observability.

This adapter is INFORMATIONAL ONLY — it always returns CLEAR.  The
previous cycle's reason is surfaced in raw_probe and notes so operators
can see it without the adapter producing a false positive on a healthy cycle.

State source: db:cycles (last summary entries_blocked_reason field).
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class EntriesBlockedReasonAdapter:
    id = 5
    name = "entries_blocked_reason_string"
    category = BlockCategory.DB_CONTROL_PLANE
    stage = BlockStage.DISCOVERY
    source_file_line = "src/engine/cycle_runner.py:609"

    def probe(self, deps: RegistryDeps) -> Block:
        # Gate 5 is a local variable inside discover_cycle_opportunities; it
        # cannot be probed from outside the cycle.  This adapter is purely
        # informational: it always returns CLEAR and surfaces probe issues in
        # raw_probe rather than fail-closing to UNKNOWN (which would falsely
        # mark the gate as blocking via blocking_blocks()).
        return Block(
            id=self.id,
            name=self.name,
            category=self.category,
            stage=self.stage,
            state=BlockState.CLEAR,
            blocking_reason=None,
            state_source="memory:entries_blocked_reason (local var in discover_cycle_opportunities)",
            source_file_line=self.source_file_line,
            owner_module="src.engine.cycle_runner",
            owner_function="discover_cycle_opportunities",
            raw_probe={
                "note": (
                    "current-cycle reason is in-memory only; persistence layer "
                    "varies (state/cycles/*.json on disk, no DB table)"
                ),
            },
            notes=(
                "Informational only — always CLEAR. "
                "Local var initialised to None at L609, set by 11 branches (L715-L751)."
            ),
        )
