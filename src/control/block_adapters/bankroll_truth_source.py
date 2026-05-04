# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 8: bankroll_truth_source_polymarket_wallet_filter adapter.

Probes: db:risk_state.details_json field 'bankroll_truth_source' (latest row)
Blocks when: latest row lacks bankroll_truth_source='polymarket_wallet'.

This gate is informational — it exposes whether the most recent risk_state
row is a post-cutover row. If not, gate 7 will eventually flag DATA_DEGRADED.
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


class BankrollTruthSourceAdapter:
    id = 8
    name = "bankroll_truth_source_polymarket_wallet_filter"
    category = BlockCategory.RISKGUARD
    stage = BlockStage.DISCOVERY
    source_file_line = "src/riskguard/riskguard.py:257"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            conn = deps.risk_state_db_connection_factory()
            try:
                row = conn.execute(
                    """
                    SELECT details_json FROM risk_state
                    ORDER BY checked_at DESC, id DESC LIMIT 1
                    """
                ).fetchone()
            finally:
                conn.close()

            if row is None:
                truth_source = None
                has_polymarket_wallet = False
            else:
                try:
                    details = json.loads(row[0]) if row[0] else {}
                    truth_source = details.get("bankroll_truth_source")
                except (json.JSONDecodeError, TypeError):
                    truth_source = None
                has_polymarket_wallet = truth_source == "polymarket_wallet"

            # Blocks when the latest row lacks the field (will cause DATA_DEGRADED over time)
            blocking = not has_polymarket_wallet

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.BLOCKING if blocking else BlockState.CLEAR,
                blocking_reason=(
                    "DATA_DEGRADED (insufficient_history or no_reference_row)"
                    if blocking else None
                ),
                state_source="db:risk_state.details_json field 'bankroll_truth_source'",
                source_file_line=self.source_file_line,
                owner_module="src.riskguard.riskguard",
                owner_function="tick",
                raw_probe={
                    "bankroll_truth_source": truth_source,
                    "has_polymarket_wallet": has_polymarket_wallet,
                },
                notes=(
                    "Field written at riskguard.py:976 inside tick(). "
                    "Pre-cutover rows lack the field and are excluded at SQL layer by gate 7."
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
                state_source="db:risk_state.details_json field 'bankroll_truth_source'",
                source_file_line=self.source_file_line,
                owner_module="src.riskguard.riskguard",
                owner_function="tick",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
