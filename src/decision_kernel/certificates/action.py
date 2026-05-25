"""Actionable-trade certificate builders and verifier entrypoints."""

from src.decision_kernel.verifier import verify_actionable_trade
from src.decision_kernel import claims
from src.decision_kernel.certificate import DecisionCertificate, ParentEdge, build_certificate


def build_actionable_trade_certificate(
    *,
    payload: dict,
    parent_certificates: tuple[DecisionCertificate, ...],
    decision_time,
) -> DecisionCertificate:
    return build_certificate(
        certificate_type=claims.ACTIONABLE_TRADE,
        semantic_key=f"actionable:{payload.get('event_id')}:{payload.get('candidate_id')}",
        claim_type=claims.ACTIONABLE_TRADE,
        mode="LIVE",
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload=payload,
        parent_edges=tuple(
            ParentEdge(_role(parent.certificate_type), parent.certificate_hash, parent.certificate_type)
            for parent in parent_certificates
        ),
        parent_certificates=parent_certificates,
        authority_id="edli.actionable_trade",
        authority_version="v1",
        algorithm_id="edli.event_bound_actionable_builder",
        algorithm_version="v1",
    )


def _role(certificate_type: str) -> str:
    return certificate_type.removesuffix("Certificate").replace("Evidence", "").lower()


__all__ = ["build_actionable_trade_certificate", "verify_actionable_trade"]
