# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 5: entries_blocked_reason_string adapter.

Probes: memory:entries_blocked_reason (local var in discover_cycle_opportunities)
        populated from L609 through 11 conditional branches (L715-L751).

Because this is a local variable in cycle_runner.py, we cannot probe it
directly from an adapter without wiring. Instead, this adapter probes the
underlying state that contributes to entries_blocked_reason: it reads the
DB-persisted last summary's entries_blocked_reason field.  This gives
operators visibility into what the previous cycle's reason was.

State source: db:cycles (last summary entries_blocked_reason field).
"""

from __future__ import annotations

import json

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)

_VALID_REASONS = frozenset({
    "chain_sync_unavailable",
    "portfolio_quarantined",
    "force_exit_review_daily_loss_red",
    "entry_bankroll_unavailable",
    "entry_bankroll_non_positive",
    "near_max_exposure",
    "entries_paused",
})


class EntriesBlockedReasonAdapter:
    id = 5
    name = "entries_blocked_reason_string"
    category = BlockCategory.DB_CONTROL_PLANE
    stage = BlockStage.DISCOVERY
    source_file_line = "src/engine/cycle_runner.py:609"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            conn = deps.db_connection_factory()
            try:
                # Try to get the most recent cycle summary entries_blocked_reason
                row = conn.execute(
                    """
                    SELECT summary_json FROM cycles
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                conn.close()

            last_reason: str | None = None
            if row and row[0]:
                try:
                    summary = json.loads(row[0])
                    last_reason = summary.get("entries_blocked_reason")
                    if not isinstance(last_reason, str):
                        last_reason = None
                except (json.JSONDecodeError, AttributeError):
                    last_reason = None

            is_blocking = last_reason is not None

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.BLOCKING if is_blocking else BlockState.CLEAR,
                blocking_reason=last_reason if is_blocking else None,
                state_source="memory:entries_blocked_reason (local var in discover_cycle_opportunities)",
                source_file_line=self.source_file_line,
                owner_module="src.engine.cycle_runner",
                owner_function="discover_cycle_opportunities",
                raw_probe={"last_cycle_entries_blocked_reason": last_reason},
                notes=(
                    "Local var initialised to None at L609, set by 11 branches (L715-L751). "
                    "This adapter reads the last cycle's persisted summary value."
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
                state_source="memory:entries_blocked_reason (local var in discover_cycle_opportunities)",
                source_file_line=self.source_file_line,
                owner_module="src.engine.cycle_runner",
                owner_function="discover_cycle_opportunities",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
