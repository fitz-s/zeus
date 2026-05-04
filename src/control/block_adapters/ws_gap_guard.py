# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 10: ws_gap_guard_allow_submit adapter.

Probes: memory:WSGapStatus singleton via deps.ws_gap_guard_module.summary()
Blocks when: summary()['entry']['allow_submit'] is False.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class WsGapGuardAdapter:
    id = 10
    name = "ws_gap_guard_allow_submit"
    category = BlockCategory.RUNTIME_HEALTH
    stage = BlockStage.DISCOVERY
    source_file_line = "src/engine/cycle_runner.py:672"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            ws_summary = deps.ws_gap_guard_module.summary()
            allow_submit: bool = bool(ws_summary.get("entry", {}).get("allow_submit", False))
            subscription_state: str = str(ws_summary.get("subscription_state", "UNKNOWN"))
            gap_reason: str = str(ws_summary.get("gap_reason", "unknown"))

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.CLEAR if allow_submit else BlockState.BLOCKING,
                blocking_reason=(
                    f"ws_gap={subscription_state}:{gap_reason}" if not allow_submit else None
                ),
                state_source="memory:WSGapStatus singleton (module-level _status)",
                source_file_line=self.source_file_line,
                owner_module="src.control.ws_gap_guard",
                owner_function="summary",
                raw_probe={
                    "allow_submit": allow_submit,
                    "subscription_state": subscription_state,
                    "gap_reason": gap_reason,
                    "m5_reconcile_required": ws_summary.get("m5_reconcile_required"),
                },
                notes=(
                    "summary() at ws_gap_guard.py:116. Default boot state is "
                    "DISCONNECTED/not_configured → blocks."
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
                state_source="memory:WSGapStatus singleton (module-level _status)",
                source_file_line=self.source_file_line,
                owner_module="src.control.ws_gap_guard",
                owner_function="summary",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
