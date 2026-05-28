"""Market-channel adapter boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.decision_kernel import claims
from src.decision_kernel.certificate import DecisionCertificate
from src.decision_kernel.certificates.evidence import build_market_channel_certificate


def build_market_data_certificate(
    *,
    semantic_key: str,
    decision_time: datetime,
    payload: dict[str, Any],
) -> DecisionCertificate:
    return build_market_channel_certificate(
        certificate_type=claims.MARKET_DATA,
        semantic_key=semantic_key,
        decision_time=decision_time,
        payload=payload,
    )


def build_market_channel_quote_certificate(
    *,
    semantic_key: str,
    decision_time: datetime,
    payload: dict[str, Any],
) -> DecisionCertificate:
    return build_market_channel_certificate(
        certificate_type=claims.QUOTE_FEASIBILITY,
        semantic_key=semantic_key,
        decision_time=decision_time,
        payload={**payload, "fill_claim": False},
    )
