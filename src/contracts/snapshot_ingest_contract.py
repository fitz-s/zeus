# Created: 2026-04-17
# Last reused/audited: 2026-04-17
# Authority basis: zeus_dual_track_refactor_package_v2_2026-04-16/04_CODE_SNIPPETS/ingest_snapshot_contract.py + R-AF/R-AH/R-AJ
"""Snapshot ingest contract — 3-law quarantine gating for low-track snapshots.

Phase 5B (B078 / SD-1): enforces the dual-track ingest boundary laws:
  Law 1 (low only): boundary_ambiguous=True → training_allowed=False
  Law 2 (low only): causality=N/A_CAUSAL_DAY_ALREADY_STARTED → training_allowed=False
  Law 3 (all): issue_time_utc absent/None → training_allowed=False, causality=RUNTIME_ONLY_FALLBACK
  Law 4 (all): members_unit absent → rejected (Kelvin silent-default is a Forbidden Move)
  Law 5 (all): absent causality field → rejected (causality is first-class, never defaulted)
"""
from __future__ import annotations

from dataclasses import dataclass

from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity


_ALLOWED_DATA_VERSIONS: dict[str, MetricIdentity] = {
    HIGH_LOCALDAY_MAX.data_version: HIGH_LOCALDAY_MAX,
    LOW_LOCALDAY_MIN.data_version: LOW_LOCALDAY_MIN,
}


@dataclass(frozen=True, slots=True)
class SnapshotIngestDecision:
    accepted: bool
    reason: str
    training_allowed: bool
    causality_status: str


def validate_snapshot_contract(payload: dict) -> SnapshotIngestDecision:
    data_version = payload.get("data_version")
    spec: MetricIdentity | None = _ALLOWED_DATA_VERSIONS.get(data_version)
    if spec is None:
        return SnapshotIngestDecision(False, "DATA_VERSION_NOT_ALLOWED", False, "UNKNOWN")

    if payload.get("temperature_metric") != spec.temperature_metric:
        return SnapshotIngestDecision(False, "METRIC_MISMATCH", False, "UNKNOWN")

    if payload.get("physical_quantity") != spec.physical_quantity:
        return SnapshotIngestDecision(False, "PHYSICAL_QUANTITY_MISMATCH", False, "UNKNOWN")

    members = payload.get("members")
    if not isinstance(members, list) or len(members) != 51:
        return SnapshotIngestDecision(False, "BAD_MEMBER_COUNT", False, "UNKNOWN")

    # R-AH: members_unit must be explicit — Kelvin silent-default is a Forbidden Move.
    if payload.get("members_unit") is None:
        return SnapshotIngestDecision(False, "MISSING_MEMBERS_UNIT", False, "UNKNOWN")

    # R-AJ: causality field must be present — absent causality must never silently default to OK.
    causality_field = payload.get("causality")
    if causality_field is None:
        return SnapshotIngestDecision(False, "MISSING_CAUSALITY_FIELD", False, "UNKNOWN")

    causality_status = causality_field.get("status", "UNKNOWN") if isinstance(causality_field, dict) else "UNKNOWN"
    boundary_ambiguous = bool(
        payload.get("boundary_policy", {}).get("boundary_ambiguous", False)
    )
    issue_time = payload.get("issue_time_utc")

    training_allowed = True

    # Law 3: runtime-only fallback rows — missing issue_time_utc blocks training.
    if issue_time in (None, ""):
        training_allowed = False
        if causality_status == "OK":
            causality_status = "RUNTIME_ONLY_FALLBACK"

    # Law 1 (low only): boundary-ambiguous snapshots must not enter calibration training.
    if spec.temperature_metric == "low" and boundary_ambiguous:
        training_allowed = False
        if causality_status == "OK":
            causality_status = "REJECTED_BOUNDARY_AMBIGUOUS"

    # Law 2 (low only): day-already-started snapshots must not enter calibration training.
    if spec.temperature_metric == "low" and causality_status == "N/A_CAUSAL_DAY_ALREADY_STARTED":
        training_allowed = False

    return SnapshotIngestDecision(
        accepted=True,
        reason="OK",
        training_allowed=training_allowed,
        causality_status=causality_status,
    )
