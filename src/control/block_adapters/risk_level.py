# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 6: risk_allows_new_entries_predicate adapter.

Probes: db:risk_state (latest row) — uses deps.risk_state_db_connection_factory()
Blocks when: risk_level != RiskLevel.GREEN.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class RiskLevelAdapter:
    id = 6
    name = "risk_allows_new_entries_predicate"
    category = BlockCategory.RISKGUARD
    stage = BlockStage.DISCOVERY
    source_file_line = "src/engine/cycle_runner.py:268"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            conn = deps.risk_state_db_connection_factory()
            try:
                row = conn.execute(
                    "SELECT level FROM risk_state ORDER BY checked_at DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()

            level: str = row[0] if row else "UNKNOWN"
            is_green = level == "GREEN"

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.CLEAR if is_green else BlockState.BLOCKING,
                blocking_reason=f"risk_level={level}" if not is_green else None,
                state_source="db:risk_state (latest row) via get_current_level()",
                source_file_line=self.source_file_line,
                owner_module="src.riskguard.riskguard",
                owner_function="tick_with_portfolio",
                raw_probe={"risk_level": level, "is_green": is_green},
                notes=(
                    "Only RiskLevel.GREEN allows new entries. "
                    "YELLOW/ORANGE/RED/DATA_DEGRADED all block."
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
                state_source="db:risk_state (latest row) via get_current_level()",
                source_file_line=self.source_file_line,
                owner_module="src.riskguard.riskguard",
                owner_function="tick_with_portfolio",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
