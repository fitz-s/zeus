# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_live_block_root_cause/REGISTRY_DESIGN.md
"""Gate 12: entry_forecast_promotion_evidence_file adapter.

Probes: file:state/entry_forecast_promotion_evidence.json (existence)
Blocks when: file is absent.

Informational/derived — subset of gate 11. Registry exposes it so
`zeus blocks` shows the full picture.
"""

from __future__ import annotations

from src.control.block_adapters._base import RegistryDeps
from src.control.entries_block_registry import (
    Block,
    BlockCategory,
    BlockStage,
    BlockState,
)


class PromotionEvidenceFileAdapter:
    id = 12
    name = "entry_forecast_promotion_evidence_file"
    category = BlockCategory.OPERATOR_ROLLOUT
    stage = BlockStage.EVALUATOR
    source_file_line = "src/control/entry_forecast_promotion_evidence_io.py:224"

    def probe(self, deps: RegistryDeps) -> Block:
        evidence_path = deps.state_dir / "entry_forecast_promotion_evidence.json"
        try:
            exists = evidence_path.exists()

            # Check for corruption if file exists
            corruption_detail: str | None = None
            if exists:
                try:
                    from src.control.entry_forecast_promotion_evidence_io import (
                        PromotionEvidenceCorruption,
                        read_promotion_evidence,
                    )
                    result = read_promotion_evidence(path=evidence_path)
                    if result is None:
                        exists = False  # treat as absent
                except Exception as exc:  # noqa: BLE001
                    corruption_detail = str(exc)

            is_blocking = not exists

            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.BLOCKING if is_blocking else BlockState.CLEAR,
                blocking_reason="ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" if is_blocking else None,
                state_source="file:state/entry_forecast_promotion_evidence.json",
                source_file_line=self.source_file_line,
                owner_module="src.control.entry_forecast_promotion_evidence_io",
                owner_function="write_promotion_evidence",
                raw_probe={
                    "file_exists": evidence_path.exists(),
                    "parsed_ok": exists and corruption_detail is None,
                    "corruption_detail": corruption_detail,
                    "path": str(evidence_path),
                },
                notes=(
                    "read_promotion_evidence (evidence_io.py:224) uses stat()-keyed LRU cache. "
                    "Subset of gate 11 — informational."
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
                state_source="file:state/entry_forecast_promotion_evidence.json",
                source_file_line=self.source_file_line,
                owner_module="src.control.entry_forecast_promotion_evidence_io",
                owner_function="write_promotion_evidence",
                raw_probe={"exception": str(exc)},
                notes="probe raised — fail-closed",
            )
