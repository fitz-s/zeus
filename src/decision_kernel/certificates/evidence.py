"""Evidence certificate helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.decision_kernel import claims
from src.decision_kernel.authority import DECISION_KERNEL_AUTHORITY_ID, DECISION_KERNEL_AUTHORITY_VERSION
from src.decision_kernel.certificate import DecisionCertificate, build_certificate
from src.decision_kernel.verifier import assert_market_channel_not_fill


def build_market_channel_certificate(
    *,
    certificate_type: str,
    semantic_key: str,
    decision_time: datetime,
    payload: dict[str, Any],
) -> DecisionCertificate:
    payload = dict(payload)
    payload.setdefault("source_kind", claims.PUBLIC_MARKET_CHANNEL_SOURCE)
    cert = build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type="public_market_channel_evidence",
        mode="NO_SUBMIT",
        decision_time=decision_time,
        source_available_at=decision_time,  # AVAIL-POSSESSION-EXEMPTED: structural decision-time cert (public_market_channel_evidence wraps no external source clock); field consumed only by verifier no-future-leakage check (<=decision_time), cert hash, and max_parent_* monotonicity — never a freshness gate or q. decision_time is the only honest anchor.
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload=payload,
        authority_id=DECISION_KERNEL_AUTHORITY_ID,
        authority_version=DECISION_KERNEL_AUTHORITY_VERSION,
        algorithm_id="decision_kernel.market_channel",
        algorithm_version="v1",
    )
    assert_market_channel_not_fill(cert)
    return cert
