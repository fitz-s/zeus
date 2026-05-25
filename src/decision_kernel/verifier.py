"""Decision certificate verifier rules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from src.decision_kernel import claims
from src.decision_kernel.certificate import DecisionCertificate, certificate_hash_for
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.modes import ALLOWED_MODES, is_live_like


def verify_certificate(
    cert: DecisionCertificate,
    parents: Iterable[DecisionCertificate] = (),
    *,
    decision_time: datetime | None = None,
) -> None:
    header = cert.header
    if header.mode not in ALLOWED_MODES:
        raise CertificateVerificationError(f"invalid certificate mode: {header.mode}")
    expected_decision_time = _utc(decision_time) if decision_time is not None else header.decision_time
    if header.decision_time != expected_decision_time:
        raise CertificateVerificationError("certificate decision_time does not match verifier decision_time")
    _verify_parent_edges(cert, tuple(parents))
    if certificate_hash_for(header) != header.certificate_hash:
        raise CertificateVerificationError("certificate hash mismatch")
    _verify_time_filtration(cert)


def verify_no_submit_decision(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.NO_SUBMIT_DECISION:
        raise CertificateVerificationError("expected NoSubmitDecisionCertificate")
    if cert.header.mode != "NO_SUBMIT":
        raise CertificateVerificationError("no-submit decision must use NO_SUBMIT mode")
    _forbid_no_submit_payload(cert)
    parent_types = {parent.certificate_type for parent in parent_tuple}
    required = claims.NO_SUBMIT_REQUIRED_TYPES
    if cert.payload.get("decision_source") == "forecast":
        required = claims.NO_SUBMIT_FORECAST_REQUIRED_TYPES
    missing = required - parent_types
    if missing:
        raise CertificateVerificationError(f"no-submit decision missing parents: {sorted(missing)}")
    forbidden = claims.NO_SUBMIT_FORBIDDEN_TYPES & parent_types
    if forbidden:
        raise CertificateVerificationError(f"no-submit decision has forbidden parents: {sorted(forbidden)}")


def verify_actionable_trade(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.ACTIONABLE_TRADE:
        raise CertificateVerificationError("expected ActionableTradeCertificate")
    parent_types = {parent.certificate_type for parent in parent_tuple}
    missing = claims.ACTIONABLE_REQUIRED_TYPES - parent_types
    if missing:
        raise CertificateVerificationError(f"actionable trade missing parents: {sorted(missing)}")
    _forbid_public_market_channel_fill(parent_tuple)


def verify_execution_command(cert: DecisionCertificate, parents: Iterable[DecisionCertificate]) -> None:
    parent_tuple = tuple(parents)
    verify_certificate(cert, parent_tuple)
    if cert.certificate_type != claims.EXECUTION_COMMAND:
        raise CertificateVerificationError("expected ExecutionCommandCertificate")
    parent_types = {parent.certificate_type for parent in parent_tuple}
    missing = claims.EXECUTION_COMMAND_REQUIRED_TYPES - parent_types
    if missing:
        raise CertificateVerificationError(f"execution command missing parents: {sorted(missing)}")


def assert_market_channel_not_fill(cert: DecisionCertificate) -> None:
    if (
        cert.certificate_type == claims.FILL
        and cert.payload.get("source_kind") == claims.PUBLIC_MARKET_CHANNEL_SOURCE
    ):
        raise CertificateVerificationError("public market-channel data cannot produce FillCertificate")
    if (
        cert.certificate_type == claims.FILL_FEASIBILITY
        and cert.payload.get("source_kind") == claims.PUBLIC_MARKET_CHANNEL_SOURCE
    ):
        raise CertificateVerificationError("public market-channel data cannot produce FillFeasibilityEvidence")


def _verify_parent_edges(cert: DecisionCertificate, parents: tuple[DecisionCertificate, ...]) -> None:
    seen_roles: set[str] = set()
    parent_by_hash = {parent.certificate_hash: parent for parent in parents}
    for edge in cert.header.parent_edges:
        if edge.role in seen_roles and edge.required:
            raise CertificateVerificationError(f"duplicate required parent role: {edge.role}")
        seen_roles.add(edge.role)
        parent = parent_by_hash.get(edge.certificate_hash)
        if parent is None:
            raise CertificateVerificationError(f"missing parent for role {edge.role}")
        if parent.certificate_type != edge.certificate_type:
            raise CertificateVerificationError(f"parent type mismatch for role {edge.role}")


def _verify_time_filtration(cert: DecisionCertificate) -> None:
    decision_time = cert.header.decision_time
    for name, value in (
        ("source_available_at", cert.header.source_available_at),
        ("agent_received_at", cert.header.agent_received_at),
        ("persisted_at", cert.header.persisted_at),
        ("max_parent_source_available_at", cert.header.max_parent_source_available_at),
        ("max_parent_agent_received_at", cert.header.max_parent_agent_received_at),
        ("max_parent_persisted_at", cert.header.max_parent_persisted_at),
    ):
        if value is None:
            continue
        if value > decision_time:
            if name.endswith("persisted_at") and cert.header.mode == "REPLAY_COUNTERFACTUAL":
                continue
            raise CertificateVerificationError(f"{name} after decision_time")
    if is_live_like(cert.header.mode):
        required = (
            cert.header.source_available_at,
            cert.header.agent_received_at,
            cert.header.persisted_at,
        )
        if any(value is None for value in required):
            raise CertificateVerificationError("live/no-submit certificate missing filtration timestamp")


def _forbid_no_submit_payload(cert: DecisionCertificate) -> None:
    if cert.payload.get("submitted") is True:
        raise CertificateVerificationError("NO_SUBMIT certificate cannot set submitted=true")
    for key in ("action_score", "actionable_trade_score", "actionable_executable_trade_score"):
        value = cert.payload.get(key)
        if value is not None and float(value) > 0.0:
            raise CertificateVerificationError(f"NO_SUBMIT certificate cannot carry positive {key}")
    if cert.payload.get("execution_command_id"):
        raise CertificateVerificationError("NO_SUBMIT certificate cannot carry execution command")


def _forbid_public_market_channel_fill(parents: tuple[DecisionCertificate, ...]) -> None:
    for parent in parents:
        assert_market_channel_not_fill(parent)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
