# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.decision_kernel import claims
from src.decision_kernel.certificate import ParentEdge, build_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.ledger import DecisionCertificateLedger
from src.decision_kernel.verifier import verify_execution_command


NOW = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)


def test_execution_command_requires_live_mode():
    parents, command = execution_graph(mode="NO_SUBMIT")

    with pytest.raises(CertificateVerificationError, match="LIVE mode"):
        verify_execution_command(command, parents)


def test_execution_command_requires_actionable_parent():
    parents, command = execution_graph(drop_parent=claims.ACTIONABLE_TRADE)

    with pytest.raises(CertificateVerificationError, match="ActionableTradeCertificate"):
        verify_execution_command(command, parents)


def test_execution_command_requires_final_intent_parent():
    parents, command = execution_graph(drop_parent=claims.FINAL_INTENT)

    with pytest.raises(CertificateVerificationError, match="FinalIntentCertificate"):
        verify_execution_command(command, parents)


def test_execution_command_requires_executor_expressibility_parent():
    parents, command = execution_graph(drop_parent=claims.EXECUTOR_EXPRESSIBILITY)

    with pytest.raises(CertificateVerificationError, match="ExecutorExpressibilityCertificate"):
        verify_execution_command(command, parents)


def test_execution_command_requires_live_cap_parent():
    parents, command = execution_graph(drop_parent=claims.LIVE_CAP)

    with pytest.raises(CertificateVerificationError, match="LiveCapCertificate"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_submitted_true_before_executor():
    parents, command = execution_graph(command_payload={"submitted": True})

    with pytest.raises(CertificateVerificationError, match="submitted=false"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_wrong_token():
    parents, command = execution_graph(command_payload={"token_id": "other-token"})

    with pytest.raises(CertificateVerificationError, match="token_id"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_wrong_condition():
    parents, command = execution_graph(command_payload={"condition_id": "other-condition"})

    with pytest.raises(CertificateVerificationError, match="condition_id"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_wrong_direction():
    parents, command = execution_graph(command_payload={"direction": "sell_yes"})

    with pytest.raises(CertificateVerificationError, match="direction"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_size_below_min_order():
    parents, command = execution_graph(command_payload={"size": 0.01, "min_order_size": 1.0})

    with pytest.raises(CertificateVerificationError, match="min_order_size"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_tick_misaligned_price():
    parents, command = execution_graph(command_payload={"limit_price": 0.333, "tick_size": 0.01})

    with pytest.raises(CertificateVerificationError, match="tick-aligned"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_missing_idempotency_key():
    parents, command = execution_graph(command_payload={"idempotency_key": ""})

    with pytest.raises(CertificateVerificationError, match="idempotency_key"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_venue_order_id_before_submit():
    parents, command = execution_graph(command_payload={"venue_order_id": "venue-1"})

    with pytest.raises(CertificateVerificationError, match="venue_order_id"):
        verify_execution_command(command, parents)


def test_ledger_rejects_forged_execution_command_certificate():
    parents, command = execution_graph(command_payload={"limit_price": 0.333})

    with pytest.raises(CertificateVerificationError, match="tick-aligned|actionable trade missing parents"):
        DecisionCertificateLedger(_conn()).persist_all(parents + (command,))


def test_ledger_rejects_execution_command_with_generic_verifier_only_path():
    _, command = execution_graph(command_payload={"submitted": True})

    with pytest.raises(CertificateVerificationError, match="missing parent|ActionableTradeCertificate|submitted=false"):
        DecisionCertificateLedger(_conn()).insert_idempotent(command)


def execution_graph(*, mode: str = "LIVE", command_payload: dict | None = None, drop_parent: str | None = None):
    actionable = _cert(claims.ACTIONABLE_TRADE, "actionable:event-1", _actionable_payload())
    final_intent = _cert(
        claims.FINAL_INTENT,
        "final-intent:intent-1",
        {"final_intent_id": "intent-1", "token_id": "yes-1", "condition_id": "condition-1"},
    )
    expressibility = _cert(claims.EXECUTOR_EXPRESSIBILITY, "executor-expressibility:intent-1", {"passed": True})
    live_cap = _cert(
        claims.LIVE_CAP,
        "live-cap:cap-1",
        {"usage_id": "cap-1", "event_id": "event-1", "reservation_status": "RESERVED", "max_notional_usd": 5.0},
    )
    parents = tuple(parent for parent in (actionable, final_intent, expressibility, live_cap) if parent.certificate_type != drop_parent)
    payload = {**_command_payload(actionable), **(command_payload or {})}
    command = _cert(claims.EXECUTION_COMMAND, "execution-command:cmd-1", payload, mode=mode, parents=parents)
    return parents, command


def _actionable_payload() -> dict:
    return {
        "event_id": "event-1",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": "snap-1",
        "family_id": "family-1",
        "candidate_id": "candidate-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "direction": "buy_yes",
        "executable_snapshot_id": "exec-1",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.4,
        "c_cost_95pct": 0.45,
        "p_fill_lcb": 0.1,
        "trade_score": 0.2,
        "action_score": 0.2,
        "fdr_family_id": "family-1",
        "kelly_decision_id": "kelly-1",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-1",
        "live_cap_usage_id": "cap-1",
        "final_intent_id": "intent-1",
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": True,
        "submitted": False,
    }


def _command_payload(actionable) -> dict:
    return {
        "event_id": "event-1",
        "actionable_certificate_hash": actionable.certificate_hash,
        "final_intent_id": "intent-1",
        "execution_command_id": "cmd-1",
        "executor_name": "execute_final_intent",
        "order_type": "POST_ONLY_LIMIT",
        "side": "BUY",
        "direction": "buy_yes",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "limit_price": 0.40,
        "size": 10.0,
        "time_in_force": "GTC",
        "post_only": True,
        "maker": True,
        "neg_risk": False,
        "tick_size": 0.01,
        "min_order_size": 1.0,
        "fee_rate": 0.0,
        "idempotency_key": "edli:event-1:cmd-1",
        "submitted": False,
    }


def _cert(certificate_type: str, semantic_key: str, payload: dict, *, mode: str = "LIVE", parents=()):
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type=certificate_type,
        mode=mode,
        decision_time=NOW,
        source_available_at=NOW,
        agent_received_at=NOW,
        persisted_at=NOW,
        payload=payload,
        parent_edges=tuple(ParentEdge(_role(parent.certificate_type), parent.certificate_hash, parent.certificate_type) for parent in parents),
        parent_certificates=tuple(parents),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


def _role(certificate_type: str) -> str:
    return certificate_type.removesuffix("Certificate").replace("Evidence", "").lower()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
