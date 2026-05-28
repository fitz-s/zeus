"""Core decision certificate grammar."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal

from src.decision_kernel.canonicalization import (
    CANONICALIZATION_VERSION,
    canonical_json,
    stable_hash,
)
from src.decision_kernel.modes import CertificateMode

VerifierStatus = Literal["VERIFIED", "REJECTED", "SUPERSEDED", "REVIEW_REQUIRED"]


@dataclass(frozen=True)
class ParentEdge:
    role: str
    certificate_hash: str
    certificate_type: str
    required: bool = True


@dataclass(frozen=True)
class CertificateHeader:
    certificate_id: str
    certificate_type: str
    schema_version: int
    canonicalization_version: str
    semantic_key: str
    claim_type: str
    mode: CertificateMode
    decision_time: datetime
    source_available_at: datetime | None
    agent_received_at: datetime | None
    persisted_at: datetime | None
    max_parent_source_available_at: datetime | None
    max_parent_agent_received_at: datetime | None
    max_parent_persisted_at: datetime | None
    parent_edges: tuple[ParentEdge, ...]
    authority_id: str
    authority_version: str
    algorithm_id: str
    algorithm_version: str
    config_hash: str | None
    model_version_hash: str | None
    payload_hash: str
    certificate_hash: str
    verifier_status: VerifierStatus


@dataclass(frozen=True)
class DecisionCertificate:
    header: CertificateHeader
    payload: dict[str, Any]

    @property
    def certificate_id(self) -> str:
        return self.header.certificate_id

    @property
    def certificate_hash(self) -> str:
        return self.header.certificate_hash

    @property
    def certificate_type(self) -> str:
        return self.header.certificate_type

    @property
    def mode(self) -> str:
        return self.header.mode

    @property
    def semantic_key(self) -> str:
        return self.header.semantic_key


def build_certificate(
    *,
    certificate_type: str,
    semantic_key: str,
    claim_type: str,
    mode: CertificateMode,
    decision_time: datetime,
    payload: dict[str, Any],
    authority_id: str,
    authority_version: str,
    algorithm_id: str,
    algorithm_version: str,
    parent_edges: tuple[ParentEdge, ...] = (),
    parent_certificates: tuple[DecisionCertificate, ...] = (),
    source_available_at: datetime | None = None,
    agent_received_at: datetime | None = None,
    persisted_at: datetime | None = None,
    config_hash: str | None = None,
    model_version_hash: str | None = None,
    schema_version: int = 1,
    verifier_status: VerifierStatus = "VERIFIED",
) -> DecisionCertificate:
    decision_utc = _utc(decision_time)
    parent_edges = tuple(parent_edges)
    payload_hash = stable_hash(payload)
    max_source, max_agent, max_persisted = _max_parent_times(parent_certificates, payload)
    header_without_hash = CertificateHeader(
        certificate_id="",
        certificate_type=certificate_type,
        schema_version=schema_version,
        canonicalization_version=CANONICALIZATION_VERSION,
        semantic_key=semantic_key,
        claim_type=claim_type,
        mode=mode,
        decision_time=decision_utc,
        source_available_at=_utc_or_none(source_available_at),
        agent_received_at=_utc_or_none(agent_received_at),
        persisted_at=_utc_or_none(persisted_at),
        max_parent_source_available_at=max_source,
        max_parent_agent_received_at=max_agent,
        max_parent_persisted_at=max_persisted,
        parent_edges=parent_edges,
        authority_id=authority_id,
        authority_version=authority_version,
        algorithm_id=algorithm_id,
        algorithm_version=algorithm_version,
        config_hash=config_hash,
        model_version_hash=model_version_hash,
        payload_hash=payload_hash,
        certificate_hash="",
        verifier_status=verifier_status,
    )
    cert_hash = certificate_hash_for(header_without_hash)
    cert_id = f"{certificate_type}:{cert_hash[:24]}"
    return DecisionCertificate(
        header=replace(
            header_without_hash,
            certificate_id=cert_id,
            certificate_hash=cert_hash,
        ),
        payload=dict(payload),
    )


def certificate_hash_for(header: CertificateHeader) -> str:
    hash_input = {
        "certificate_type": header.certificate_type,
        "schema_version": header.schema_version,
        "canonicalization_version": header.canonicalization_version,
        "semantic_key": header.semantic_key,
        "claim_type": header.claim_type,
        "mode": header.mode,
        "decision_time": header.decision_time,
        "source_available_at": header.source_available_at,
        "agent_received_at": header.agent_received_at,
        "persisted_at": header.persisted_at,
        "parent_edges": header.parent_edges,
        "authority_id": header.authority_id,
        "authority_version": header.authority_version,
        "algorithm_id": header.algorithm_id,
        "algorithm_version": header.algorithm_version,
        "config_hash": header.config_hash,
        "model_version_hash": header.model_version_hash,
        "payload_hash": header.payload_hash,
    }
    return stable_hash(hash_input)


def certificate_payload_json(cert: DecisionCertificate) -> str:
    return canonical_json(cert.payload)


def _max_parent_times(
    parent_certificates: tuple[DecisionCertificate, ...],
    payload: dict[str, Any],
) -> tuple[datetime | None, datetime | None, datetime | None]:
    if parent_certificates:
        return (
            _max_dt(parent.header.source_available_at for parent in parent_certificates),
            _max_dt(parent.header.agent_received_at for parent in parent_certificates),
            _max_dt(parent.header.persisted_at for parent in parent_certificates),
        )
    parent_times = payload.get("_parent_times")
    if not isinstance(parent_times, dict):
        return None, None, None
    return (
        _utc_or_none(parent_times.get("source_available_at")),
        _utc_or_none(parent_times.get("agent_received_at")),
        _utc_or_none(parent_times.get("persisted_at")),
    )


def _max_dt(values: Any) -> datetime | None:
    parsed = [_utc_or_none(value) for value in values]
    present = [value for value in parsed if value is not None]
    return max(present) if present else None


def _utc_or_none(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _utc(value)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
