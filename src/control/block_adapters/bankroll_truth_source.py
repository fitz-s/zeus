# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 8: bankroll_truth_source_polymarket_wallet_filter adapter.

Probes: db:risk_state.details_json field 'bankroll_truth_source' (latest row)

This gate is INFORMATIONAL ONLY — it always returns CLEAR.  It exposes
whether the most recent risk_state row is a post-cutover row.  The presence
or absence of bankroll_truth_source does not by itself imply entries are
blocked; gate 6 (risk_level) is the authoritative blocker.

Field presence and value are surfaced in raw_probe for operator visibility.
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

                # Count rows for context while connection is still open
                try:
                    rows_total_r = conn.execute(
                        "SELECT COUNT(*) FROM risk_state"
                    ).fetchone()
                    rows_total: int | None = rows_total_r[0] if rows_total_r else 0
                    rows_with_field_r = conn.execute(
                        """
                        SELECT COUNT(*) FROM risk_state
                        WHERE json_extract(details_json, '$.bankroll_truth_source') = 'polymarket_wallet'
                        """
                    ).fetchone()
                    rows_with_field: int | None = rows_with_field_r[0] if rows_with_field_r else 0
                except Exception:  # noqa: BLE001
                    rows_total = None
                    rows_with_field = None
            finally:
                conn.close()

            if row is None:
                truth_source = None
            else:
                try:
                    details = json.loads(row[0]) if row[0] else {}
                    truth_source = details.get("bankroll_truth_source")
                except (json.JSONDecodeError, TypeError):
                    truth_source = None

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.CLEAR,
                blocking_reason=None,
                state_source="db:risk_state.details_json field 'bankroll_truth_source'",
                source_file_line=self.source_file_line,
                owner_module="src.riskguard.riskguard",
                owner_function="tick",
                raw_probe={
                    "latest_truth_source": truth_source,
                    "rows_with_field": rows_with_field,
                    "rows_total": rows_total,
                },
                notes=(
                    "Informational only — always CLEAR. "
                    "Field written at riskguard.py:976 inside tick(). "
                    "Pre-cutover rows lack the field and are excluded at SQL layer by gate 7."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # Informational gate — never fail-close to UNKNOWN.  Surface the
            # error in raw_probe and stay CLEAR.
            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.CLEAR,
                blocking_reason=None,
                state_source="db:risk_state.details_json field 'bankroll_truth_source'",
                source_file_line=self.source_file_line,
                owner_module="src.riskguard.riskguard",
                owner_function="tick",
                raw_probe={"probe_error": f"{exc.__class__.__name__}: {exc}"},
                notes="Informational only — exception during probe surfaced in raw_probe; state stays CLEAR.",
            )
