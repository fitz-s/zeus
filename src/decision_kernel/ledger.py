"""SQLite ledger for decision certificates and compile failures."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.decision_kernel.canonicalization import canonical_json, stable_hash
from src.decision_kernel.certificate import DecisionCertificate, certificate_payload_json
from src.decision_kernel.errors import CertificateSemanticDriftError
from src.decision_kernel import claims
from src.decision_kernel.verifier import (
    verify_actionable_trade,
    verify_certificate,
    verify_execution_command,
    verify_no_submit_decision,
)


@dataclass(frozen=True)
class CompileFailure:
    event_id: str
    decision_time: datetime
    mode: str
    claim_type: str
    stage: str
    reason_code: str
    reason_detail: str | None = None
    parent_hashes: tuple[str, ...] = ()

    @property
    def failure_id(self) -> str:
        return "decision_compile_failure:" + stable_hash({
            "event_id": self.event_id,
            "decision_time": self.decision_time,
            "mode": self.mode,
            "claim_type": self.claim_type,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "parent_hashes": self.parent_hashes,
        })[:32]


class DecisionCertificateLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._schema_ready = False

    def ensure_schema(self) -> None:
        from src.state.schema.decision_certificates_schema import ensure_tables

        ensure_tables(self.conn)
        self._schema_ready = True

    def persist_all(self, certificates: tuple[DecisionCertificate, ...]) -> None:
        by_hash = {cert.certificate_hash: cert for cert in certificates}
        for cert in certificates:
            parents = tuple(
                by_hash[edge.certificate_hash]
                for edge in cert.header.parent_edges
                if edge.certificate_hash in by_hash
            )
            _verify_for_persistence(cert, parents)
            self.insert_idempotent(cert)

    def insert_idempotent(self, cert: DecisionCertificate) -> str:
        if not self._schema_ready:
            self.ensure_schema()
        existing = self.conn.execute(
            """
            SELECT certificate_id, certificate_hash
            FROM decision_certificates
            WHERE certificate_type = ?
              AND semantic_key = ?
              AND mode = ?
              AND decision_time = ?
            LIMIT 1
            """,
            (
                cert.header.certificate_type,
                cert.header.semantic_key,
                cert.header.mode,
                _dt(cert.header.decision_time),
            ),
        ).fetchone()
        if existing is not None:
            existing_id = str(existing["certificate_id"] if isinstance(existing, sqlite3.Row) else existing[0])
            existing_hash = str(existing["certificate_hash"] if isinstance(existing, sqlite3.Row) else existing[1])
            if existing_hash == cert.certificate_hash:
                self._persist_edges(cert)
                return existing_id
            raise CertificateSemanticDriftError(
                "DECISION_CERTIFICATE_SEMANTIC_DRIFT:"
                f"type={cert.certificate_type}:semantic_key={cert.semantic_key}:"
                f"mode={cert.mode}:decision_time={_dt(cert.header.decision_time)}:"
                f"existing_hash={existing_hash}:new_hash={cert.certificate_hash}"
            )
        self.conn.execute(
            """
            INSERT INTO decision_certificates (
                certificate_id, certificate_type, schema_version,
                canonicalization_version, semantic_key, claim_type, mode,
                decision_time, source_available_at, agent_received_at,
                persisted_at, max_parent_source_available_at,
                max_parent_agent_received_at, max_parent_persisted_at,
                authority_id, authority_version, algorithm_id, algorithm_version,
                config_hash, model_version_hash, payload_json, payload_hash,
                certificate_hash, verifier_status, created_at
            ) VALUES (
                :certificate_id, :certificate_type, :schema_version,
                :canonicalization_version, :semantic_key, :claim_type, :mode,
                :decision_time, :source_available_at, :agent_received_at,
                :persisted_at, :max_parent_source_available_at,
                :max_parent_agent_received_at, :max_parent_persisted_at,
                :authority_id, :authority_version, :algorithm_id, :algorithm_version,
                :config_hash, :model_version_hash, :payload_json, :payload_hash,
                :certificate_hash, :verifier_status, :created_at
            )
            """,
            {
                "certificate_id": cert.header.certificate_id,
                "certificate_type": cert.header.certificate_type,
                "schema_version": cert.header.schema_version,
                "canonicalization_version": cert.header.canonicalization_version,
                "semantic_key": cert.header.semantic_key,
                "claim_type": cert.header.claim_type,
                "mode": cert.header.mode,
                "decision_time": _dt(cert.header.decision_time),
                "source_available_at": _dt_or_none(cert.header.source_available_at),
                "agent_received_at": _dt_or_none(cert.header.agent_received_at),
                "persisted_at": _dt_or_none(cert.header.persisted_at),
                "max_parent_source_available_at": _dt_or_none(cert.header.max_parent_source_available_at),
                "max_parent_agent_received_at": _dt_or_none(cert.header.max_parent_agent_received_at),
                "max_parent_persisted_at": _dt_or_none(cert.header.max_parent_persisted_at),
                "authority_id": cert.header.authority_id,
                "authority_version": cert.header.authority_version,
                "algorithm_id": cert.header.algorithm_id,
                "algorithm_version": cert.header.algorithm_version,
                "config_hash": cert.header.config_hash,
                "model_version_hash": cert.header.model_version_hash,
                "payload_json": certificate_payload_json(cert),
                "payload_hash": cert.header.payload_hash,
                "certificate_hash": cert.header.certificate_hash,
                "verifier_status": cert.header.verifier_status,
                "created_at": _dt(datetime.now(timezone.utc)),
            },
        )
        self._persist_edges(cert)
        return cert.header.certificate_id

    def persist_failures(self, failures: tuple[CompileFailure, ...]) -> None:
        if not self._schema_ready:
            self.ensure_schema()
        for failure in failures:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO decision_compile_failures (
                    failure_id, event_id, decision_time, mode, claim_type, stage,
                    reason_code, reason_detail, parent_hashes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    failure.failure_id,
                    failure.event_id,
                    _dt(failure.decision_time),
                    failure.mode,
                    failure.claim_type,
                    failure.stage,
                    failure.reason_code,
                    failure.reason_detail,
                    canonical_json(failure.parent_hashes),
                    _dt(datetime.now(timezone.utc)),
                ),
            )

    def _persist_edges(self, cert: DecisionCertificate) -> None:
        created_at = _dt(datetime.now(timezone.utc))
        for edge in cert.header.parent_edges:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO decision_certificate_edges (
                    child_certificate_id, parent_role, parent_certificate_hash,
                    parent_certificate_type, required, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cert.header.certificate_id,
                    edge.role,
                    edge.certificate_hash,
                    edge.certificate_type,
                    1 if edge.required else 0,
                    created_at,
                ),
            )


def _dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _dt_or_none(value: datetime | None) -> str | None:
    return None if value is None else _dt(value)


def _verify_for_persistence(cert: DecisionCertificate, parents: tuple[DecisionCertificate, ...]) -> None:
    if cert.certificate_type == claims.NO_SUBMIT_DECISION:
        verify_no_submit_decision(cert, parents)
        return
    if cert.certificate_type == claims.ACTIONABLE_TRADE:
        verify_actionable_trade(cert, parents)
        return
    if cert.certificate_type == claims.EXECUTION_COMMAND:
        verify_execution_command(cert, parents)
        return
    verify_certificate(cert, parents)
