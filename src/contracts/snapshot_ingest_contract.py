# Created: 2026-04-17
# Last reused/audited: 2026-04-17
# Authority basis: zeus_dual_track_refactor_package_v2_2026-04-16/04_CODE_SNIPPETS/ingest_snapshot_contract.py + R-AF/R-AH/R-AJ
"""Snapshot ingest contract — 3-law rejection gating for low-track snapshots.

Phase 5B (B078 / SD-1): enforces the dual-track ingest boundary laws:
  Law 1 (low only): boundary_ambiguous=True → training_allowed=False
  Law 2 (low only): causality=N/A_CAUSAL_DAY_ALREADY_STARTED → training_allowed=False
  Law 3 (all): issue_time_utc absent/None → training_allowed=False, causality=ISSUE_TIME_MISSING
  Law 4 (all): members_unit absent → rejected (Kelvin silent-default is a Forbidden Move)
  Law 5 (all): absent causality field → rejected (causality is first-class, never defaulted)
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from typing import Literal

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
    _ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY,
    _ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY,
)
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity

# 2026-05-01: Open Data ENS data_versions accepted alongside the TIGGE archive
# data_versions. Same temperature_metric / observation_field spec — different
# source-provenance prefix. Both rows can coexist in ensemble_snapshots for
# the same (city, target_date, metric); readers use
# data_version_priority_for_metric() to prefer Open Data when present.
# 2026-05-07: mx2t3/mn2t3 rename. New versions are the active write path.
# Legacy mx2t6/mn2t6 kept in allow-list so historical rows remain readable.
#
# 2026-05-07 Codex P2 fix: dedicate MetricIdentity instances for the 3h
# native derived quantity ("mx2t3_local_calendar_day_max" /
# "mn2t3_local_calendar_day_min"). The cloud extract patch (see
# docs/historical_evidence/CLOUD_EXTRACT_PATCH_2026_05_07.md) writes these strings
# into payload.physical_quantity. Mapping the new data_versions to the
# legacy 6h MetricIdentity caused PHYSICAL_QUANTITY_MISMATCH on every
# correctly-tagged 3h row, dropping post-cutover Open Data rows on the
# floor. Per-quantity MetricIdentity restores 3h identity end-to-end.
_ECMWF_OPENDATA_HIGH_DATA_VERSION = "ecmwf_opendata_mx2t3_local_calendar_day_max"
_ECMWF_OPENDATA_LOW_DATA_VERSION = "ecmwf_opendata_mn2t3_local_calendar_day_min"
# Legacy versions imported from canonical source (ensemble_snapshot_provenance.py:82-83).
# Local re-definitions removed 2026-05-07; use the imported names above.

# 3h native derived-quantity MetricIdentity (Open Data post-cutover).
# These differ from HIGH_LOCALDAY_MAX / LOW_LOCALDAY_MIN only in
# (physical_quantity, data_version); the temperature_metric and
# observation_field stay identical so all downstream calibration /
# replay readers treat them as the same logical track.
_HIGH_LOCALDAY_MAX_OPENDATA_3H = MetricIdentity(
    temperature_metric="high",
    physical_quantity="mx2t3_local_calendar_day_max",
    observation_field="high_temp",
    data_version=_ECMWF_OPENDATA_HIGH_DATA_VERSION,
)
_LOW_LOCALDAY_MIN_OPENDATA_3H = MetricIdentity(
    temperature_metric="low",
    physical_quantity="mn2t3_local_calendar_day_min",
    observation_field="low_temp",
    data_version=_ECMWF_OPENDATA_LOW_DATA_VERSION,
)

_ALLOWED_DATA_VERSIONS: dict[str, MetricIdentity] = {
    HIGH_LOCALDAY_MAX.data_version: HIGH_LOCALDAY_MAX,
    LOW_LOCALDAY_MIN.data_version: LOW_LOCALDAY_MIN,
    # 2026-05-12 antibody (B/C — schema drift completion): the Open Data
    # post-cutover data_versions (mx2t3/mn2t3) carry the 3h-native
    # physical_quantity strings; mapping them to the legacy 6h
    # HIGH_LOCALDAY_MAX / LOW_LOCALDAY_MIN MetricIdentity caused
    # PHYSICAL_QUANTITY_MISMATCH on every correctly-tagged Open Data
    # row (root cause of 183 rejections since 2026-05-11 12z that
    # starved source_run COMPLETE and BLOCKED Gate #11). The 3h-aware
    # MetricIdentity instances were created on 2026-05-07 (see comment
    # block above) but never wired into this dict; the row-write side
    # of that 2026-05-07 fix already persists the payload's 3h string
    # (ingest_grib_to_snapshots.py:615-617), so the contract now needs
    # to accept it.
    _ECMWF_OPENDATA_HIGH_DATA_VERSION: _HIGH_LOCALDAY_MAX_OPENDATA_3H,
    _ECMWF_OPENDATA_LOW_DATA_VERSION: _LOW_LOCALDAY_MIN_OPENDATA_3H,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION: LOW_LOCALDAY_MIN,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION: LOW_LOCALDAY_MIN,
    # Legacy bridge — mx2t6/mn2t6 era rows written before 2026-05-07.
    # Physical quantity "mx2t6_local_calendar_day_max" / "mn2t6_local_calendar_day_min"
    # matches HIGH_LOCALDAY_MAX / LOW_LOCALDAY_MIN respectively (6h TIGGE identity).
    _ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY: HIGH_LOCALDAY_MAX,
    _ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY: LOW_LOCALDAY_MIN,
}


# PR 3+6 (2026-05-19): CausalityStatus Literal — 10 values covering all DecisionSourceContext
# integrity_errors() return codes. Alphabetized for readability.
CausalityStatus = Literal[
    "AVAILABLE_AFTER_DECISION",
    "CLOCK_DRIFT_WARNING",
    "DECISION_BEFORE_FORECAST_AVAILABLE",
    "EXCESSIVE_CLOCK_DRIFT",
    "INCLUSION_AFTER_FINALITY",
    "MISSING_CAUSALITY_FIELD",
    "OBS_AFTER_PROVIDER",
    "OK",
    "PROVIDER_AFTER_AVAILABLE",
    "SUBMIT_AFTER_ACK",
]

# Mapping from integrity_errors() string codes to CausalityStatus literals
INTEGRITY_ERROR_TO_CAUSALITY: dict[str, str] = {
    "available_after_decision": "AVAILABLE_AFTER_DECISION",
    "clock_drift_warning": "CLOCK_DRIFT_WARNING",
    "forecast_available_after_decision": "DECISION_BEFORE_FORECAST_AVAILABLE",
    "excessive_clock_drift": "EXCESSIVE_CLOCK_DRIFT",
    "inclusion_after_finality": "INCLUSION_AFTER_FINALITY",
    "missing_causality_field": "MISSING_CAUSALITY_FIELD",
    "obs_after_provider": "OBS_AFTER_PROVIDER",
    "provider_after_available": "PROVIDER_AFTER_AVAILABLE",
    "submit_after_ack": "SUBMIT_AFTER_ACK",
}


@dataclass(frozen=True, slots=True)
class SnapshotIngestDecision:
    accepted: bool
    reason: str
    training_allowed: bool
    causality_status: str


@dataclass(frozen=True, slots=True)
class BoundaryAmbiguityDecision:
    snapshot_ambiguous: bool
    ambiguous_member_count: int
    majority_threshold: int
    member_ambiguous: tuple[bool, ...] | None
    member_invalid: tuple[bool, ...] | None
    member_ids: tuple[int, ...] | None
    member_reasons: tuple[str, ...] | None


LOW_BOUNDARY_SEMANTICS_REVISION = "low_boundary_strict_majority_v1"


def _ambiguity_majority_threshold() -> int:
    raw = os.environ.get("AMBIGUITY_MAJORITY_THRESHOLD", "").strip()
    if raw:
        try:
            threshold = int(raw)
        except ValueError:
            threshold = 26
        if 1 <= threshold <= 51:
            return threshold
    return 26


def boundary_ambiguity_decision(payload: dict) -> BoundaryAmbiguityDecision:
    """Interpret LOW boundary evidence once for every ingest consumer.

    Detailed per-member minima outrank producer booleans. A boundary bucket is
    ambiguous only when it is strictly colder than the fully-inside minimum;
    ties add no information. Payloads without detailed minima retain the
    declared count, and only legacy payloads without either form fall back to
    the producer's snapshot flag.
    """

    threshold = _ambiguity_majority_threshold()
    boundary_policy = (
        payload.get("boundary_policy")
        if isinstance(payload.get("boundary_policy"), dict)
        else {}
    )
    raw_snapshot_ambiguous = bool(
        boundary_policy.get(
            "boundary_ambiguous",
            payload.get("boundary_ambiguous", False),
        )
    )
    members = payload.get("members")
    detailed = (
        isinstance(members, list)
        and len(members) == 51
        and any(
            isinstance(member, dict)
            and (
                "inner_min_native_unit" in member
                or "boundary_min_native_unit" in member
            )
            for member in members
        )
    )
    member_ambiguous: list[bool] = []
    member_invalid: list[bool] = []
    member_ids: list[int] = []
    member_reasons: list[str] = []
    if detailed:
        boundary_window_declared = bool(
            payload.get("selected_step_ranges_boundary")
        ) or any(
            isinstance(member, dict)
            and member.get("boundary_min_native_unit") is not None
            for member in members
        )
        for index, member in enumerate(members):
            member_id = index
            if isinstance(member, dict):
                try:
                    member_id = int(member.get("member", index))
                except (TypeError, ValueError):
                    member_id = index
            member_ids.append(member_id)

            if not isinstance(member, dict):
                member_ambiguous.append(False)
                member_invalid.append(True)
                member_reasons.append("invalid_member_record")
                continue
            if "inner_min_native_unit" not in member:
                member_ambiguous.append(False)
                member_invalid.append(True)
                member_reasons.append("invalid_missing_inner_min")
                continue
            inner_raw = member.get("inner_min_native_unit")
            try:
                inner = float(inner_raw)
            except (TypeError, ValueError):
                inner = math.nan
            if not math.isfinite(inner):
                member_ambiguous.append(False)
                member_invalid.append(True)
                member_reasons.append("invalid_nonfinite_inner_min")
                continue
            if "boundary_min_native_unit" not in member:
                member_ambiguous.append(False)
                member_invalid.append(True)
                member_reasons.append("invalid_missing_boundary_min")
                continue
            boundary_raw = member.get("boundary_min_native_unit")
            if boundary_raw is None:
                member_ambiguous.append(False)
                member_invalid.append(boundary_window_declared)
                member_reasons.append(
                    "invalid_missing_boundary_extrema"
                    if boundary_window_declared
                    else "accepted_no_boundary_window"
                )
                continue
            try:
                boundary = float(boundary_raw)
            except (TypeError, ValueError):
                boundary = math.nan
            if not math.isfinite(boundary):
                member_ambiguous.append(False)
                member_invalid.append(True)
                member_reasons.append("invalid_nonfinite_boundary_min")
                continue
            ambiguous = boundary < inner
            member_ambiguous.append(ambiguous)
            member_invalid.append(False)
            if ambiguous:
                member_reasons.append("quarantined_boundary_strictly_lower")
            elif boundary == inner:
                member_reasons.append("accepted_boundary_tie")
            else:
                member_reasons.append("accepted_boundary_not_lower")

    if detailed:
        count = sum(member_ambiguous)
        member_decisions: tuple[bool, ...] | None = tuple(member_ambiguous)
        invalid_decisions: tuple[bool, ...] | None = tuple(member_invalid)
        ids: tuple[int, ...] | None = tuple(member_ids)
        reasons: tuple[str, ...] | None = tuple(member_reasons)
    else:
        member_decisions = None
        invalid_decisions = None
        ids = None
        reasons = None
        try:
            count = int(boundary_policy["ambiguous_member_count"])
        except (KeyError, TypeError, ValueError):
            return BoundaryAmbiguityDecision(
                snapshot_ambiguous=raw_snapshot_ambiguous,
                ambiguous_member_count=0,
                majority_threshold=threshold,
                member_ambiguous=None,
                member_invalid=None,
                member_ids=None,
                member_reasons=None,
            )
        if not 0 <= count <= 51:
            return BoundaryAmbiguityDecision(
                snapshot_ambiguous=True,
                ambiguous_member_count=count,
                majority_threshold=threshold,
                member_ambiguous=None,
                member_invalid=None,
                member_ids=None,
                member_reasons=None,
            )

    return BoundaryAmbiguityDecision(
        snapshot_ambiguous=count >= threshold,
        ambiguous_member_count=count,
        majority_threshold=threshold,
        member_ambiguous=member_decisions,
        member_invalid=invalid_decisions,
        member_ids=ids,
        member_reasons=reasons,
    )


def _raw_boundary_evidence_sha256(payload: dict) -> str:
    boundary_policy = (
        payload.get("boundary_policy")
        if isinstance(payload.get("boundary_policy"), dict)
        else {}
    )
    members = payload.get("members")
    member_evidence = []
    if isinstance(members, list):
        for index, member in enumerate(members):
            if not isinstance(member, dict):
                member_evidence.append({"index": index, "raw_member": member})
                continue
            member_evidence.append(
                {
                    "index": index,
                    "member": member.get("member"),
                    "value_native_unit": member.get("value_native_unit"),
                    "inner_min_native_unit": member.get("inner_min_native_unit"),
                    "boundary_min_native_unit": member.get("boundary_min_native_unit"),
                    "boundary_ambiguous": member.get("boundary_ambiguous"),
                }
            )
    evidence = {
        "boundary_ambiguous": payload.get("boundary_ambiguous"),
        "boundary_policy": boundary_policy,
        "members": member_evidence,
    }
    encoded = json.dumps(
        evidence,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_low_boundary_evidence(payload: dict) -> dict:
    """Return a copy whose LOW boundary fields follow the canonical decision."""

    normalized = dict(payload)
    if str(normalized.get("temperature_metric") or "").lower() != "low":
        return normalized

    raw_boundary_policy = (
        dict(normalized["boundary_policy"])
        if isinstance(normalized.get("boundary_policy"), dict)
        else {}
    )
    raw_snapshot_ambiguous = bool(
        raw_boundary_policy.get(
            "boundary_ambiguous",
            normalized.get("boundary_ambiguous", False),
        )
    )
    raw_ambiguous_member_count = raw_boundary_policy.get(
        "ambiguous_member_count"
    )
    raw_evidence_sha256 = _raw_boundary_evidence_sha256(normalized)
    decision = boundary_ambiguity_decision(normalized)
    boundary_policy = (
        dict(normalized["boundary_policy"])
        if isinstance(normalized.get("boundary_policy"), dict)
        else {}
    )
    boundary_policy["boundary_ambiguous"] = decision.snapshot_ambiguous
    boundary_policy["ambiguous_member_count"] = decision.ambiguous_member_count
    normalized["boundary_policy"] = boundary_policy
    normalized["boundary_ambiguous"] = decision.snapshot_ambiguous

    if decision.member_ambiguous is not None:
        assert decision.member_invalid is not None
        assert decision.member_ids is not None
        assert decision.member_reasons is not None
        members: list[dict] = []
        invalid_member_ids: list[int] = []
        member_decisions: list[dict[str, object]] = []
        for raw_member, member_id, ambiguous, invalid, reason in zip(
            normalized.get("members", []),
            decision.member_ids,
            decision.member_ambiguous,
            decision.member_invalid,
            decision.member_reasons,
            strict=True,
        ):
            member = (
                dict(raw_member)
                if isinstance(raw_member, dict)
                else {"member": member_id}
            )
            member["boundary_ambiguous"] = ambiguous
            if invalid:
                member["value_native_unit"] = None
                invalid_member_ids.append(member_id)
                member_decision = "invalid"
            elif ambiguous:
                member["value_native_unit"] = None
                member_decision = "quarantined"
            else:
                member["value_native_unit"] = float(
                    member["inner_min_native_unit"]
                )
                member_decision = "accepted"
            member_decisions.append(
                {
                    "member": member_id,
                    "decision": member_decision,
                    "reason": reason,
                }
            )
            members.append(member)
        normalized["members"] = members
        missing_members = (
            list(normalized["missing_members"])
            if isinstance(normalized.get("missing_members"), list)
            else []
        )
        normalized["missing_members"] = list(
            dict.fromkeys([*missing_members, *invalid_member_ids])
        )
    else:
        invalid_member_ids = []
        member_decisions = []

    quarantined_member_ids = [
        int(item["member"])
        for item in member_decisions
        if item["decision"] == "quarantined"
    ]
    normalized["boundary_normalization"] = {
        "semantics_revision": LOW_BOUNDARY_SEMANTICS_REVISION,
        "artifact_manifest_sha256": normalized.get("manifest_sha256"),
        "raw_evidence_sha256": raw_evidence_sha256,
        "raw_boundary_ambiguous": raw_snapshot_ambiguous,
        "raw_ambiguous_member_count": raw_ambiguous_member_count,
        "canonical_boundary_ambiguous": decision.snapshot_ambiguous,
        "canonical_ambiguous_member_count": decision.ambiguous_member_count,
        "majority_threshold": decision.majority_threshold,
        "quarantined_member_ids": quarantined_member_ids,
        "invalid_member_ids": invalid_member_ids,
        "member_decisions": member_decisions,
    }

    return normalized


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
    boundary_decision = boundary_ambiguity_decision(payload)
    boundary_ambiguous = boundary_decision.snapshot_ambiguous
    issue_time = payload.get("issue_time_utc")

    training_allowed = True

    # Law 3: missing issue_time_utc blocks training.
    if issue_time in (None, ""):
        training_allowed = False
        if causality_status == "OK":
            causality_status = "ISSUE_TIME_MISSING"

    # Law 1 (low only): boundary-ambiguous snapshots must not enter calibration training.
    # 2026-05-19: boundary_ambiguous now reflects majority threshold (≥26/51) not any-member.
    # SYNTHESIS.md Addendum 2 §5. ambiguous_member_count persisted in payload for downstream audit.
    if spec.temperature_metric == "low" and boundary_ambiguous:
        training_allowed = False
        if causality_status == "OK":
            causality_status = "REJECTED_BOUNDARY_AMBIGUOUS"

    if (
        spec.temperature_metric == "low"
        and boundary_decision.member_invalid is not None
        and any(boundary_decision.member_invalid)
    ):
        training_allowed = False
        if causality_status == "OK":
            causality_status = "UNKNOWN"

    # Law 2 (low only): day-already-started snapshots must not enter calibration training.
    if spec.temperature_metric == "low" and causality_status == "N/A_CAUSAL_DAY_ALREADY_STARTED":
        training_allowed = False

    return SnapshotIngestDecision(
        accepted=True,
        reason="OK",
        training_allowed=training_allowed,
        causality_status=causality_status,
    )
