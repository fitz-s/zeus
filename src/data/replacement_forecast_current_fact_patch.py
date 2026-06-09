"""Current-fact patch planner for replacement forecast simple-switch evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from src.data.replacement_forecast_live_switch_surface import CURRENT_DATA_FACT_FILE, CURRENT_SOURCE_FACT_FILE


REQUIRED_SOURCE_EVIDENCE = (
    "openmeteo_ecmwf_ifs9_endpoint_verified",
    "ecmwf_aifs_ens_download_verified",
    "aifs_sampled_2t_identity_verified",
    "settlement_source_routing_unchanged",
    "live_root_read_files_verified",
)
REQUIRED_DATA_EVIDENCE = (
    "replacement_shadow_schema_dry_run_passed",
    "raw_artifact_manifest_writes_verified",
    "posterior_materialization_verified",
    "materialization_seed_builder_verified",
    "materialization_seed_discovery_verified",
    "materialization_request_builder_verified",
    "emos_product_identity_isolated",
    "refit_gate_blocks_promotion",
    "finetune_artifact_builder_verified",
    "refit_handoff_builder_verified",
    "refit_handoff_install_plan_verified",
    "promotion_evidence_composer_verified",
    "full_replacement_test_suite_passed",
    "event_reactor_no_bypass_suite_passed",
)


@dataclass(frozen=True)
class ReplacementForecastCurrentFactPatchPlan:
    status: str
    reason_codes: tuple[str, ...]
    source_fact_path: str
    data_fact_path: str
    required_source_evidence: tuple[str, ...]
    required_data_evidence: tuple[str, ...]
    source_patch: str | None
    data_patch: str | None

    @property
    def ready(self) -> bool:
        return self.status == "CURRENT_FACT_PATCH_READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "source_fact_path": self.source_fact_path,
            "data_fact_path": self.data_fact_path,
            "required_source_evidence": list(self.required_source_evidence),
            "required_data_evidence": list(self.required_data_evidence),
            "source_patch": self.source_patch,
            "data_patch": self.data_patch,
        }


def _truthy_evidence(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.upper() in {"PASS", "PASSED", "VERIFIED", "TRUE"}
    return False


def _string_sequence(raw: object, *, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(str(item) for item in raw)


def _patch_text(
    *,
    title: str,
    generated_at: str,
    evidence_refs: tuple[str, ...],
    notes: tuple[str, ...],
) -> str:
    lines = [
        f"# {title}",
        "",
        "Status: CURRENT_FOR_LIVE - replacement forecast shadow/veto simple-switch fact refresh",
        f"Last audited: {generated_at}",
        "Max staleness: 14 days for replacement forecast simple-switch planning",
        "Authority status: not authority law; audit-bound current fact only",
        "",
        "## Replacement Forecast Simple-Switch Evidence",
        "",
    ]
    for ref in evidence_refs:
        lines.append(f"- {ref}")
    if notes:
        lines.extend(["", "## Notes", ""])
        for note in notes:
            lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This current-fact refresh authorizes shadow/veto readiness only.",
            "- It does not authorize live trade authority, Kelly increase, direction flip, settlement rewrites, calibration refit promotion, or source-route changes.",
            "",
        ]
    )
    return "\n".join(lines)


def build_replacement_forecast_current_fact_patch_plan(
    root: Path | str,
    *,
    evidence: Mapping[str, object] | None = None,
) -> ReplacementForecastCurrentFactPatchPlan:
    """Build reviewable current-fact patches only when required evidence is present."""

    root_path = Path(root)
    evidence_payload = dict(evidence or {})
    missing_source = tuple(key for key in REQUIRED_SOURCE_EVIDENCE if not _truthy_evidence(evidence_payload, key))
    missing_data = tuple(key for key in REQUIRED_DATA_EVIDENCE if not _truthy_evidence(evidence_payload, key))
    generated_at = str(evidence_payload.get("generated_at") or "")
    evidence_refs = _string_sequence(evidence_payload.get("evidence_refs"), field_name="evidence_refs")
    notes = _string_sequence(evidence_payload.get("notes"), field_name="notes")
    reasons: list[str] = []
    if missing_source:
        reasons.append("REPLACEMENT_CURRENT_FACT_SOURCE_EVIDENCE_MISSING")
    if missing_data:
        reasons.append("REPLACEMENT_CURRENT_FACT_DATA_EVIDENCE_MISSING")
    if not generated_at:
        reasons.append("REPLACEMENT_CURRENT_FACT_GENERATED_AT_REQUIRED")
    if not evidence_refs:
        reasons.append("REPLACEMENT_CURRENT_FACT_EVIDENCE_REFS_REQUIRED")
    source_patch = None
    data_patch = None
    if not reasons:
        source_patch = _patch_text(
            title="Current Source Validity",
            generated_at=generated_at,
            evidence_refs=evidence_refs,
            notes=notes,
        )
        data_patch = _patch_text(
            title="Current Data State",
            generated_at=generated_at,
            evidence_refs=evidence_refs,
            notes=notes,
        )
    status = "CURRENT_FACT_PATCH_READY" if not reasons else "CURRENT_FACT_PATCH_BLOCKED"
    return ReplacementForecastCurrentFactPatchPlan(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons or ["REPLACEMENT_CURRENT_FACT_PATCH_READY"])),
        source_fact_path=str(root_path / CURRENT_SOURCE_FACT_FILE),
        data_fact_path=str(root_path / CURRENT_DATA_FACT_FILE),
        required_source_evidence=missing_source,
        required_data_evidence=missing_data,
        source_patch=source_patch,
        data_patch=data_patch,
    )


def normalize_replacement_forecast_current_fact_evidence(payload: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if payload is None:
        return None
    nested = payload.get("evidence")
    if isinstance(nested, Mapping):
        return nested
    return payload


def read_replacement_forecast_current_fact_patch_plan(
    root: Path | str,
    *,
    evidence_json: Path | str | None = None,
) -> ReplacementForecastCurrentFactPatchPlan:
    evidence = None
    if evidence_json is not None:
        payload = json.loads(Path(evidence_json).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("evidence JSON must decode to an object")
        evidence = normalize_replacement_forecast_current_fact_evidence(payload)
    return build_replacement_forecast_current_fact_patch_plan(root, evidence=evidence)
