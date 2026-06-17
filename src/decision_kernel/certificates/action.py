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
        source_available_at=decision_time,  # AVAIL-POSSESSION-EXEMPTED: structural decision-time cert (generated AT decision_time, wraps no external source); field consumed only by verifier no-future-leakage check (<=decision_time), cert hash, and max_parent_* monotonicity — never a freshness gate or q (quote/orderbook age metrics read the quote_feasibility/executable_snapshot certs' real clocks, not this). decision_time is the only honest anchor.
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
    import re

    base = certificate_type.removesuffix("Certificate").replace("Evidence", "")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()


__all__ = ["build_actionable_trade_certificate", "verify_actionable_trade"]
