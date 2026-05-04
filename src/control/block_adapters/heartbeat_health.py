# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 9: heartbeat_supervisor_allow_submit adapter.

Probes: memory:HeartbeatSupervisor._health (in-process singleton)
        via deps.heartbeat_module.summary()['entry']['allow_submit']
Blocks when: allow_submit is False.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class HeartbeatHealthAdapter:
    id = 9
    name = "heartbeat_supervisor_allow_submit"
    category = BlockCategory.RUNTIME_HEALTH
    stage = BlockStage.DISCOVERY
    source_file_line = "src/engine/cycle_runner.py:657"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            hb_summary = deps.heartbeat_module.summary()
            allow_submit: bool = bool(hb_summary.get("entry", {}).get("allow_submit", False))
            health: str = str(hb_summary.get("health", "UNKNOWN"))

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.CLEAR if allow_submit else BlockState.BLOCKING,
                blocking_reason=f"heartbeat={health}" if not allow_submit else None,
                state_source="memory:HeartbeatSupervisor._health (in-process singleton)",
                source_file_line=self.source_file_line,
                owner_module="src.control.heartbeat_supervisor",
                owner_function="summary",
                raw_probe={
                    "allow_submit": allow_submit,
                    "health": health,
                    "entry_summary": hb_summary.get("entry", {}),
                },
                notes=(
                    "summary() at heartbeat_supervisor.py:269. "
                    "DEGRADED/LOST/STARTING all block. If supervisor is None, allow_submit=False."
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
                state_source="memory:HeartbeatSupervisor._health (in-process singleton)",
                source_file_line=self.source_file_line,
                owner_module="src.control.heartbeat_supervisor",
                owner_function="summary",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
