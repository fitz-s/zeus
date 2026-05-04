# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 11: evaluate_entry_forecast_rollout_gate adapter.

Probes: env:ZEUS_ENTRY_FORECAST_ROLLOUT_GATE + file:state/entry_forecast_promotion_evidence.json
        + config:rollout_mode via deps.rollout_gate_module.evaluate_entry_forecast_rollout_gate()
Blocks when: decision.may_submit_live_orders is False.

Stage: EVALUATOR — this gate fires in the evaluator phase, not discovery.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class EvaluatorRolloutGateAdapter:
    id = 11
    name = "evaluate_entry_forecast_rollout_gate"
    category = BlockCategory.OPERATOR_ROLLOUT
    stage = BlockStage.EVALUATOR
    source_file_line = "src/engine/evaluator.py:784"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            # Check if the rollout gate flag is enabled at all
            gate_flag_on = deps.env.get("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1") == "1"

            if not gate_flag_on:
                return Block(
                    id=self.id,
                    name=self.name,
                    category=self.category,
                    stage=self.stage,
                    state=BlockState.CLEAR,
                    blocking_reason=None,
                    state_source=(
                        "env:ZEUS_ENTRY_FORECAST_ROLLOUT_GATE + "
                        "file:state/entry_forecast_promotion_evidence.json + "
                        "config:rollout_mode"
                    ),
                    source_file_line=self.source_file_line,
                    owner_module="src.control.entry_forecast_rollout",
                    owner_function="evaluate_entry_forecast_rollout_gate",
                    raw_probe={"gate_flag_on": False, "gate_bypassed": True},
                    notes="ZEUS_ENTRY_FORECAST_ROLLOUT_GATE=0 — gate bypassed.",
                )

            # Read promotion evidence from the state dir
            from src.control.entry_forecast_promotion_evidence_io import (
                PromotionEvidenceCorruption,
                read_promotion_evidence,
            )

            evidence_path = deps.state_dir / "entry_forecast_promotion_evidence.json"
            try:
                evidence = read_promotion_evidence(path=evidence_path)
                evidence_error: str | None = None
            except PromotionEvidenceCorruption as exc:
                evidence = None
                evidence_error = f"ENTRY_FORECAST_PROMOTION_EVIDENCE_CORRUPT:{exc}"

            # Read rollout config
            from src.config import entry_forecast_config

            config = entry_forecast_config()

            # Call the real gate function
            decision = deps.rollout_gate_module.evaluate_entry_forecast_rollout_gate(
                config=config,
                evidence=evidence,
            )

            may_submit = decision.may_submit_live_orders
            reason_codes = list(decision.reason_codes)
            if evidence_error:
                reason_codes.insert(0, evidence_error)

            blocking_reason: str | None = None
            if not may_submit:
                blocking_reason = reason_codes[0] if reason_codes else "ENTRY_FORECAST_ROLLOUT_BLOCKED"

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.CLEAR if may_submit else BlockState.BLOCKING,
                blocking_reason=blocking_reason,
                state_source=(
                    "env:ZEUS_ENTRY_FORECAST_ROLLOUT_GATE + "
                    "file:state/entry_forecast_promotion_evidence.json + "
                    "config:rollout_mode"
                ),
                source_file_line=self.source_file_line,
                owner_module="src.control.entry_forecast_rollout",
                owner_function="evaluate_entry_forecast_rollout_gate",
                raw_probe={
                    "gate_flag_on": gate_flag_on,
                    "decision_status": decision.status,
                    "reason_codes": reason_codes,
                    "may_submit_live_orders": may_submit,
                    "rollout_mode": str(config.rollout_mode),
                    "evidence_present": evidence is not None,
                    "evidence_error": evidence_error,
                },
                notes=(
                    "Called from _live_entry_forecast_rollout_blocker (evaluator.py:759). "
                    "Gate fires in evaluator phase, not discovery short-circuit."
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
                state_source=(
                    "env:ZEUS_ENTRY_FORECAST_ROLLOUT_GATE + "
                    "file:state/entry_forecast_promotion_evidence.json + "
                    "config:rollout_mode"
                ),
                source_file_line=self.source_file_line,
                owner_module="src.control.entry_forecast_rollout",
                owner_function="evaluate_entry_forecast_rollout_gate",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
