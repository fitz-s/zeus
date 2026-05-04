# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 3: control_overrides_history_entries_gate adapter.

Probes: db:control_overrides_history (via control_overrides VIEW)
Blocks when: a live entries gate row exists in the DB.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class DbControlOverridesAdapter:
    id = 3
    name = "control_overrides_history_entries_gate"
    category = BlockCategory.DB_CONTROL_PLANE
    stage = BlockStage.DISCOVERY
    source_file_line = "src/state/db.py:5454"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            from src.state.db import query_control_override_state

            conn = deps.db_connection_factory()
            try:
                state = query_control_override_state(conn)
            finally:
                conn.close()

            entries_paused = bool(state.get("entries_paused", False))
            source = state.get("entries_pause_source") or "db"
            reason = state.get("entries_pause_reason") or "entries_paused"

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.BLOCKING if entries_paused else BlockState.CLEAR,
                blocking_reason="entries_paused (from DB gate row)" if entries_paused else None,
                state_source="db:control_overrides_history (via control_overrides VIEW)",
                source_file_line=self.source_file_line,
                owner_module="src.state.db",
                owner_function="query_control_override_state",
                raw_probe={
                    "entries_paused": entries_paused,
                    "entries_pause_source": source,
                    "entries_pause_reason": reason,
                    "db_status": state.get("status", "ok"),
                },
                notes=(
                    "query_control_override_state reads the control_overrides VIEW "
                    "(event-sourced over control_overrides_history)."
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
                state_source="db:control_overrides_history (via control_overrides VIEW)",
                source_file_line=self.source_file_line,
                owner_module="src.state.db",
                owner_function="query_control_override_state",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
