"""No-submit decision certificate builder."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.decision_kernel import claims
from src.decision_kernel.authority import DECISION_KERNEL_AUTHORITY_ID, DECISION_KERNEL_AUTHORITY_VERSION
from src.decision_kernel.certificate import DecisionCertificate, ParentEdge, build_certificate
from src.decision_kernel.verifier import verify_no_submit_decision


def build_no_submit_decision_certificate(
    *,
    semantic_key: str,
    decision_time: datetime,
    parent_edges: tuple[ParentEdge, ...],
    parents: tuple[DecisionCertificate, ...],
    payload: dict[str, Any],
    source_available_at: datetime,
    agent_received_at: datetime,
    persisted_at: datetime,
) -> DecisionCertificate:
    if payload.get("submitted") is True:
        raise ValueError("NO_SUBMIT decision cannot set submitted=true")
    for key in ("action_score", "actionable_trade_score", "actionable_executable_trade_score"):
        if payload.get(key) is not None and float(payload[key]) > 0.0:
            raise ValueError(f"NO_SUBMIT decision cannot carry positive {key}")
    cert = build_certificate(
        certificate_type=claims.NO_SUBMIT_DECISION,
        semantic_key=semantic_key,
        claim_type="no_submit_dry_run_decision",
        mode="NO_SUBMIT",
        decision_time=decision_time,
        source_available_at=source_available_at,
        agent_received_at=agent_received_at,
        persisted_at=persisted_at,
        parent_edges=parent_edges,
        parent_certificates=parents,
        payload=payload,
        authority_id=DECISION_KERNEL_AUTHORITY_ID,
        authority_version=DECISION_KERNEL_AUTHORITY_VERSION,
        algorithm_id="decision_kernel.no_submit",
        algorithm_version="v1",
    )
    verify_no_submit_decision(cert, parents)
    return cert
