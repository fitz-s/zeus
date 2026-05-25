# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §9, §14 PR-B.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.decision_kernel import claims
from src.decision_kernel.certificate import ParentEdge, build_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.ledger import CompileFailure, DecisionCertificateLedger


def _cert(certificate_type: str, semantic_key: str, payload: dict, parents=()):
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type=certificate_type,
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload=payload,
        parent_edges=tuple(parents),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


def test_certificate_ledger_persists_verified_certificate_and_edges():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = DecisionCertificateLedger(conn)
    parent = _cert("ClockModeCertificate", "clock:event", {"mode": "NO_SUBMIT"})
    child = _cert(
        "CausalEventCertificate",
        "event:e1",
        {"event_id": "e1"},
        (ParentEdge("clock_mode", parent.certificate_hash, parent.certificate_type),),
    )

    ledger.persist_all((parent, child))

    assert conn.execute("SELECT COUNT(*) FROM decision_certificates").fetchone()[0] == 2
    edge = conn.execute("SELECT parent_role FROM decision_certificate_edges").fetchone()
    assert edge["parent_role"] == "clock_mode"


def test_compile_failures_persisted():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger = DecisionCertificateLedger(conn)
    failure = CompileFailure(
        event_id="event-1",
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
        mode="NO_SUBMIT",
        claim_type="no_submit_dry_run_decision",
        stage="FDR",
        reason_code="TESTING_PROTOCOL_MISSING",
        parent_hashes=("abc",),
    )

    ledger.persist_failures((failure,))
    row = conn.execute("SELECT reason_code, parent_hashes_json FROM decision_compile_failures").fetchone()
    assert row["reason_code"] == "TESTING_PROTOCOL_MISSING"
    assert "abc" in row["parent_hashes_json"]


def test_ledger_rejects_no_submit_certificate_with_proof_accepted_false():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"proof_accepted": False})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="proof_accepted=true"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_certificate_with_submitted_true():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"submitted": True})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="submitted=true"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_certificate_missing_forecast_required_parents():
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    no_submit = build_certificate(
        certificate_type=claims.NO_SUBMIT_DECISION,
        semantic_key="no_submit:event-1:intent-1",
        claim_type="no_submit_dry_run_decision",
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload=_no_submit_payload(),
        parent_edges=(),
        parent_certificates=(),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="missing parents"):
        ledger.persist_all((no_submit,))


def test_ledger_rejects_no_submit_certificate_with_execution_command_id():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"execution_command_id": "cmd-1"})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="execution command"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_quote_token_candidate_token_mismatch():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={claims.QUOTE_FEASIBILITY: {"token_id": "other-token", "selected_token_id": "other-token"}}
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="candidate.selected_token_id"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_forecast_snapshot_causal_snapshot_mismatch():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={claims.FORECAST_AUTHORITY: {"snapshot_id": "other-snapshot"}}
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="source_truth.snapshot_id"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_fdr_family_topology_family_mismatch():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={claims.FDR: {"fdr_family_id": "other-family"}}
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="family_closure.family_id"):
        ledger.persist_all(parents + (no_submit,))


def test_ledger_rejects_no_submit_kelly_cost_basis_cost_model_mismatch():
    parents, no_submit = _minimal_no_submit_graph(
        parent_payload_overrides={claims.KELLY_DRY_RUN: {"cost_basis_id": "other-cost"}}
    )
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="kelly.cost_basis_id"):
        ledger.persist_all(parents + (no_submit,))


def test_no_submit_generated_certificate_requires_generated_at_decision_time_payload():
    parents, no_submit = _minimal_no_submit_graph(no_submit_payload={"generated_at_decision_time": False})
    ledger = DecisionCertificateLedger(_conn())

    with pytest.raises(CertificateVerificationError, match="generated_at_decision_time=true"):
        ledger.persist_all(parents + (no_submit,))


def test_non_generated_authority_certificate_cannot_use_generated_at_decision_time_semantics():
    conn = _conn()
    ledger = DecisionCertificateLedger(conn)
    cert = _cert("ClockModeCertificate", "clock:generated", {"mode": "NO_SUBMIT", "generated_at_decision_time": True})

    with pytest.raises(CertificateVerificationError, match="only allowed for generated decision"):
        ledger.persist_all((cert,))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _minimal_no_submit_graph(
    *,
    no_submit_payload: dict | None = None,
    parent_payload_overrides: dict[str, dict] | None = None,
):
    now = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
    parent_payload_overrides = parent_payload_overrides or {}
    parent_payloads = {
        claims.CLOCK_MODE: {"mode": "NO_SUBMIT"},
        claims.CAUSAL_EVENT: {"event_id": "event-1", "causal_snapshot_id": "snap-1", "payload_hash": "payload-hash-1", "source": "forecast_live"},
        claims.SOURCE_TRUTH: {
            "event_id": "event-1",
            "event_source": "forecast_live",
            "source_status": "LIVE_ELIGIBLE",
            "source_authority_id": "read_executable_forecast",
            "source_reason_code": None,
            "source_id": "opendata",
            "source_run_id": "run-1",
            "snapshot_id": "snap-1",
            "causal_snapshot_id": "snap-1",
            "payload_hash": "payload-hash-1",
            "derived_from_certificate_type": claims.FORECAST_AUTHORITY,
            "derived_from_snapshot_id": "snap-1",
            "derived_from_reader_status": "LIVE_ELIGIBLE",
        },
        claims.MARKET_TOPOLOGY: {"family_id": "family-1"},
        claims.FAMILY_CLOSURE: {"family_id": "family-1", "bin_labels_hash": "bins-hash-1"},
        claims.FORECAST_AUTHORITY: {
            "snapshot_id": "snap-1",
            "reader_authority": "read_executable_forecast",
            "reader_status": "LIVE_ELIGIBLE",
            "forecast_source_id": "opendata",
            "source_run_id": "run-1",
        },
        claims.CALIBRATION: {"calibrator_model_key": "model-1", "model_hash": "model-hash-1"},
        claims.MODEL_CONFIG: {"calibrator_model_key": "model-1", "calibrator_model_hash": "model-hash-1"},
        claims.BELIEF: {
            "forecast_snapshot_id": "snap-1",
            "calibrator_model_key": "model-1",
            "calibrator_model_hash": "model-hash-1",
            "p_cal_vector_hash": "pcal-vector-hash",
            "p_live_vector_hash": "plive-vector-hash",
            "p_cal_hash": "pcal-vector-hash",
            "p_live_hash": "plive-vector-hash",
            "bin_labels_hash": "bins-hash-1",
        },
        claims.EXECUTABLE_SNAPSHOT: {"selected_snapshot_id": "exec-snap-1", "condition_id": "condition-1", "token_id": "yes-1"},
        claims.QUOTE_FEASIBILITY: {"condition_id": "condition-1", "token_id": "yes-1", "selected_token_id": "yes-1", "cost_source": "native_orderbook_ask"},
        claims.COST_MODEL: {"condition_id": "condition-1", "token_id": "yes-1", "cost_basis_id": "cost-1", "cost_source": "native_orderbook_ask"},
        claims.PRE_TRADE_EVIDENCE: {"actionable_trade_score": 0.0},
        claims.CANDIDATE_EVIDENCE: {"family_id": "family-1", "condition_id": "condition-1", "selected_token_id": "yes-1", "hypothesis_id": "family-1:yes-1"},
        claims.TESTING_PROTOCOL: {"family_id": "family-1"},
        claims.FDR: {"fdr_family_id": "family-1", "selected_hypotheses": ("family-1:yes-1",)},
        claims.KELLY_DRY_RUN: {"cost_basis_id": "cost-1"},
        claims.RISK_LEVEL: {"risk_level": "GREEN", "final_intent_id": "intent-1"},
        claims.NO_SUBMIT_MODE: {"side_effect_status": "NO_SUBMIT"},
    }
    parents = []
    for certificate_type, payload in parent_payloads.items():
        payload = {**payload, **parent_payload_overrides.get(certificate_type, {})}
        parents.append(_cert(certificate_type, f"{certificate_type}:event-1", payload))
    parent_tuple = tuple(parents)
    payload = {**_no_submit_payload(), **(no_submit_payload or {})}
    no_submit = build_certificate(
        certificate_type=claims.NO_SUBMIT_DECISION,
        semantic_key="no_submit:event-1:intent-1",
        claim_type="no_submit_dry_run_decision",
        mode="NO_SUBMIT",
        decision_time=now,
        source_available_at=now,
        agent_received_at=now,
        persisted_at=now,
        payload=payload,
        parent_edges=tuple(ParentEdge(_role(parent.certificate_type), parent.certificate_hash, parent.certificate_type) for parent in parent_tuple),
        parent_certificates=parent_tuple,
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    return parent_tuple, no_submit


def _no_submit_payload() -> dict:
    return {
        "event_id": "event-1",
        "decision_source": "forecast",
        "final_intent_id": "intent-1",
        "side_effect_status": "NO_SUBMIT",
        "proof_accepted": True,
        "submitted": False,
        "actionable_trade_score": 0.0,
        "generated_at_decision_time": True,
        "header_persisted_at_semantics": "decision_kernel_generated_at_decision_time",
        "db_created_at_may_follow_header_persisted_at": True,
    }


def _role(certificate_type: str) -> str:
    return certificate_type.removesuffix("Certificate").replace("Evidence", "").lower()
