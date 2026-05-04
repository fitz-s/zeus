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
            from src.control.entry_forecast_promotion_evidence_io import (
                PromotionEvidenceCorruption,
                read_promotion_evidence,
            )

            file_exists = evidence_path.exists()
            corruption_detail: str | None = None
            parsed_ok = False

            if not file_exists:
                # File absent → BLOCKING
                return Block(
                    id=self.id,
                    name=self.name,
                    category=self.category,
                    stage=self.stage,
                    state=BlockState.BLOCKING,
                    blocking_reason="ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING",
                    state_source="file:state/entry_forecast_promotion_evidence.json",
                    source_file_line=self.source_file_line,
                    owner_module="src.control.entry_forecast_promotion_evidence_io",
                    owner_function="write_promotion_evidence",
                    raw_probe={
                        "file_exists": False,
                        "parsed_ok": False,
                        "corruption_detail": None,
                        "path": str(evidence_path),
                    },
                    notes=(
                        "read_promotion_evidence (evidence_io.py:224) uses stat()-keyed LRU cache. "
                        "Subset of gate 11."
                    ),
                )

            # File exists — attempt parse; corruption is BLOCKING
            try:
                result = read_promotion_evidence(path=evidence_path)
                if result is None:
                    # read_promotion_evidence returns None only on absent file (shouldn't
                    # reach here), but treat defensively as missing.
                    return Block(
                        id=self.id,
                        name=self.name,
                        category=self.category,
                        stage=self.stage,
                        state=BlockState.BLOCKING,
                        blocking_reason="ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING",
                        state_source="file:state/entry_forecast_promotion_evidence.json",
                        source_file_line=self.source_file_line,
                        owner_module="src.control.entry_forecast_promotion_evidence_io",
                        owner_function="write_promotion_evidence",
                        raw_probe={
                            "file_exists": True,
                            "parsed_ok": False,
                            "corruption_detail": "read_promotion_evidence returned None",
                            "path": str(evidence_path),
                        },
                        notes=(
                            "read_promotion_evidence returned None despite file existing. "
                            "Treated as missing."
                        ),
                    )
                parsed_ok = True
            except PromotionEvidenceCorruption as exc:
                corruption_detail = str(exc)
                return Block(
                    id=self.id,
                    name=self.name,
                    category=self.category,
                    stage=self.stage,
                    state=BlockState.BLOCKING,
                    blocking_reason="ENTRY_FORECAST_PROMOTION_EVIDENCE_CORRUPT",
                    state_source="file:state/entry_forecast_promotion_evidence.json",
                    source_file_line=self.source_file_line,
                    owner_module="src.control.entry_forecast_promotion_evidence_io",
                    owner_function="write_promotion_evidence",
                    raw_probe={
                        "file_exists": True,
                        "parsed_ok": False,
                        "corruption_detail": corruption_detail,
                        "path": str(evidence_path),
                    },
                    notes=(
                        "File exists but failed PromotionEvidenceCorruption validation. "
                        "Rollout gate blocks on corruption — this adapter mirrors that."
                    ),
                )

            # File present and parsed OK → CLEAR
            return Block(
                id=self.id,
                name=self.name,
                category=self.category,
                stage=self.stage,
                state=BlockState.CLEAR,
                blocking_reason=None,
                state_source="file:state/entry_forecast_promotion_evidence.json",
                source_file_line=self.source_file_line,
                owner_module="src.control.entry_forecast_promotion_evidence_io",
                owner_function="write_promotion_evidence",
                raw_probe={
                    "file_exists": True,
                    "parsed_ok": True,
                    "corruption_detail": None,
                    "path": str(evidence_path),
                },
                notes=(
                    "read_promotion_evidence (evidence_io.py:224) uses stat()-keyed LRU cache. "
                    "Subset of gate 11."
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
