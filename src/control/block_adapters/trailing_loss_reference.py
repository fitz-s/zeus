# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 7: trailing_loss_reference_limit100_scan adapter.

Probes: db:risk_state ORDER BY checked_at DESC LIMIT 100 (same SQL as audit)

This adapter is INFORMATIONAL ONLY — it always returns CLEAR.  RiskGuard
explicitly treats no_reference_row and insufficient_history as
bootstrap-allowlisted GREEN (not an entry block), so the registry must not
claim entries are blocked during cold-start or history-bootstrap windows.

The scan status and qualifying row counts are surfaced in raw_probe for
operator visibility.

Re-uses the same SQL query cited in GATE_AUDIT.yaml:
  SELECT id, checked_at, details_json FROM risk_state
  WHERE checked_at <= ? AND json_extract(details_json, '$.bankroll_truth_source') = 'polymarket_wallet'
  ORDER BY checked_at DESC, id DESC LIMIT 100
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)

_LOOKBACK_HOURS = 24  # same lookback as riskguard's trailing loss window


class TrailingLossReferenceAdapter:
    id = 7
    name = "trailing_loss_reference_limit100_scan"
    category = BlockCategory.RISKGUARD
    stage = BlockStage.DISCOVERY
    source_file_line = "src/riskguard/riskguard.py:221"

    def probe(self, deps: RegistryDeps) -> Block:
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=_LOOKBACK_HOURS)
            ).isoformat()

            conn = deps.risk_state_db_connection_factory()
            try:
                try:
                    rows = conn.execute(
                        """
                        SELECT id, checked_at, details_json
                        FROM risk_state
                        WHERE checked_at <= ?
                          AND json_extract(details_json, '$.bankroll_truth_source') = 'polymarket_wallet'
                        ORDER BY checked_at DESC, id DESC
                        LIMIT 100
                        """,
                        (cutoff,),
                    ).fetchall()
                except Exception:  # noqa: BLE001 — table may not exist yet
                    rows = []

                has_qualifying_rows = len(rows) > 0

                if not has_qualifying_rows:
                    # Distinguish no_reference_row (empty table) vs insufficient_history
                    # (rows exist but none old enough / none with the filter field)
                    try:
                        total = conn.execute(
                            "SELECT COUNT(*) FROM risk_state"
                        ).fetchone()[0]
                    except Exception:  # noqa: BLE001
                        total = 0
                    status = "no_reference_row" if total == 0 else "insufficient_history"
                else:
                    status = "has_qualifying_rows"
            finally:
                conn.close()

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.CLEAR,
                blocking_reason=None,
                state_source="db:risk_state ORDER BY checked_at DESC LIMIT 100",
                source_file_line=self.source_file_line,
                owner_module="src.riskguard.riskguard",
                owner_function="_trailing_loss_reference",
                raw_probe={
                    "status": status,
                    "qualifying_rows_in_lookback": len(rows),
                    "lookback_hours": _LOOKBACK_HOURS,
                    "cutoff": cutoff,
                },
                notes=(
                    "Informational only — always CLEAR. "
                    "no_reference_row and insufficient_history are bootstrap-allowlisted "
                    "to GREEN in riskguard.py; gate 6 (risk_level) is the live blocker. "
                    "SF7 fix: pre-filters at SQL layer to post-cutover rows with "
                    "bankroll_truth_source=polymarket_wallet."
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # Informational gate — never fail-close to UNKNOWN.  Surface the
            # error in raw_probe and stay CLEAR so blocking_blocks() does not
            # treat this gate as a blocker.  Gate 6 is the authoritative live
            # risk-level signal.
            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.CLEAR,
                blocking_reason=None,
                state_source="db:risk_state ORDER BY checked_at DESC LIMIT 100",
                source_file_line=self.source_file_line,
                owner_module="src.riskguard.riskguard",
                owner_function="_trailing_loss_reference",
                raw_probe={"probe_error": f"{exc.__class__.__name__}: {exc}"},
                notes="Informational only — exception during probe surfaced in raw_probe; state stays CLEAR.",
            )
