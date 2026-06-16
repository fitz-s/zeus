"""Forecast authority adapter boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from src.decision_kernel import claims
from src.decision_kernel.authority import DECISION_KERNEL_AUTHORITY_ID, DECISION_KERNEL_AUTHORITY_VERSION
from src.decision_kernel.certificate import DecisionCertificate, ParentEdge, build_certificate

CanonicalForecastReader = Callable[..., Any]


def build_forecast_authority_certificate(
    *,
    semantic_key: str,
    decision_time: datetime,
    reader: CanonicalForecastReader,
    reader_kwargs: dict[str, Any],
    parent_edges: tuple[ParentEdge, ...] = (),
    parent_certificates: tuple[DecisionCertificate, ...] = (),
) -> DecisionCertificate:
    """Call the canonical executable forecast reader and preserve its evidence.

    This adapter intentionally accepts no raw source_run/coverage rows. Scope
    construction belongs to the caller; live eligibility belongs to `reader`.
    """
    result = reader(**reader_kwargs)
    status = getattr(result, "status", None) or getattr(result, "verifier_status", None) or "UNKNOWN"
    reason_code = getattr(result, "reason_code", None)
    bundle = getattr(result, "bundle", None) or result
    snapshot = getattr(bundle, "snapshot", None)
    snapshot_id = getattr(snapshot, "snapshot_id", None) or getattr(bundle, "snapshot_id", None)
    applied_validations = getattr(bundle, "applied_validations", None)
    verifier_status = "VERIFIED" if str(status).upper() in {"OK", "VERIFIED", "READY"} else "REJECTED"
    return build_certificate(
        certificate_type=claims.FORECAST_AUTHORITY,
        semantic_key=semantic_key,
        claim_type="forecast_authority",
        mode="NO_SUBMIT",
        decision_time=decision_time,
        source_available_at=decision_time,  # AVAIL-POSSESSION-EXEMPTED: thin status-record cert (adapter accepts no raw source_run/coverage rows, has no caller in the live reactor path — the live FORECAST_AUTHORITY clock is built from real evidence in event_reactor_adapter._forecast_authority_payload_and_clock:6634, not here); field consumed only by verifier no-future-leakage check (<=decision_time), cert hash, max_parent_* — never a freshness gate or q. decision_time is the only honest anchor.
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload={
            "reader": "canonical_executable_forecast_reader",
            "reader_status": status,
            "reader_reason_code": reason_code,
            "snapshot_id": snapshot_id,
            "applied_validations": tuple(applied_validations or ()),
        },
        authority_id=DECISION_KERNEL_AUTHORITY_ID,
        authority_version=DECISION_KERNEL_AUTHORITY_VERSION,
        algorithm_id="decision_kernel.forecast_authority_adapter",
        algorithm_version="v1",
        parent_edges=parent_edges,
        parent_certificates=parent_certificates,
        verifier_status=verifier_status,
    )
