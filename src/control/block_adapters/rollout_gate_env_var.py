# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 13: ZEUS_ENTRY_FORECAST_ROLLOUT_GATE_env_var adapter.

Probes: env:ZEUS_ENTRY_FORECAST_ROLLOUT_GATE
Blocks when: value == '0' (gate bypassed; informational — never BLOCKING by itself).

Per spec: this flag enables/disables gate #11, not a block reason itself.
state is always CLEAR — this adapter exists so `zeus blocks` shows the flag state.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class RolloutGateEnvVarAdapter:
    id = 13
    name = "ZEUS_ENTRY_FORECAST_ROLLOUT_GATE_env_var"
    category = BlockCategory.OPERATOR_ROLLOUT
    stage = BlockStage.EVALUATOR
    source_file_line = "src/engine/evaluator.py:748"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            value = deps.env.get("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
            gate_on = value == "1"

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                # Per spec: "never BLOCKING by itself" — always CLEAR
                state=BlockState.CLEAR,
                blocking_reason=None,
                state_source="env:ZEUS_ENTRY_FORECAST_ROLLOUT_GATE",
                source_file_line=self.source_file_line,
                owner_module="src.engine.evaluator",
                owner_function="_entry_forecast_rollout_gate_flag_on",
                raw_probe={
                    "env_value": value,
                    "gate_on": gate_on,
                    "note": "value=1 means gate is ACTIVE (default); value=0 means gate is BYPASSED",
                },
                notes=(
                    "ZEUS_ENTRY_FORECAST_ROLLOUT_GATE_FLAG='ZEUS_ENTRY_FORECAST_ROLLOUT_GATE'. "
                    "Default '1' (ON). Setting to '0' disables the full promotion-evidence gate. "
                    "Informational only — never BLOCKING by itself."
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
                state_source="env:ZEUS_ENTRY_FORECAST_ROLLOUT_GATE",
                source_file_line=self.source_file_line,
                owner_module="src.engine.evaluator",
                owner_function="_entry_forecast_rollout_gate_flag_on",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
