"""Reduce-only exit certificate builder.

AUDIT SPINE PARITY (wave 5, 2026-09-13): entries persist a decision
certificate before the venue call (see
``event_reactor_adapter.py::_persist_live_command_certificates_before_executor_submit``);
reduce-only exits never did, for any day, leaving every SELL Zeus places
invisible to every evaluator that reads ``decision_certificates``. This
builder gives exits the same hash/seal discipline without the entry path's
full pre-submit compile chain, which a reduce-only SELL has no evidence
inputs for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.decision_kernel import claims
from src.decision_kernel.certificate import DecisionCertificate, build_certificate
from src.decision_kernel.verifier import verify_certificate

# condition_id is intentionally absent: ExitOrderIntent carries no
# condition_id surface (the venue command row itself keys on token_id as
# the market identity — see market_id_for_cmd in execute_exit_order); it may
# be null in the payload but every other field here is load-bearing.
REQUIRED_EXIT_CERTIFICATE_FIELDS = (
    "position_id",
    "token_id",
    "side",
    "size",
    "limit_price",
    "exit_reason",
)


def build_reduce_only_exit_certificate(
    *,
    payload: dict[str, Any],
    decision_time: datetime,
) -> DecisionCertificate:
    missing = [
        field
        for field in REQUIRED_EXIT_CERTIFICATE_FIELDS
        if payload.get(field) in (None, "")
    ]
    if missing:
        raise ValueError(
            f"reduce-only exit certificate missing required payload fields: {missing}"
        )
    if payload.get("side") != "SELL":
        raise ValueError("reduce-only exit certificate side must be SELL")
    semantic_key = f"reduce_only_exit:{payload.get('command_id') or payload['position_id']}"
    cert = build_certificate(
        certificate_type=claims.REDUCE_ONLY_EXIT,
        semantic_key=semantic_key,
        claim_type=claims.REDUCE_ONLY_EXIT,
        mode="LIVE",
        decision_time=decision_time,
        # AVAIL-POSSESSION-EXEMPTED: structural decision-time cert. A
        # reduce-only exit's trigger (held-position monitor state, protective
        # authority, global sell auction) is decided AT this call, wraps no
        # external source with its own clock, and these fields are consumed
        # only by the verifier's no-future-leakage / monotonicity checks —
        # never a freshness gate or price. decision_time is the only honest
        # anchor, matching ActionableTradeCertificate/ClockModeCertificate.
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload=payload,
        authority_id="edli.reduce_only_exit",
        authority_version="v1",
        algorithm_id="edli.reduce_only_exit_builder",
        algorithm_version="v1",
    )
    verify_certificate(cert, ())
    return cert


__all__ = ["build_reduce_only_exit_certificate", "REQUIRED_EXIT_CERTIFICATE_FIELDS"]
