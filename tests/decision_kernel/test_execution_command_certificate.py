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
from src.decision_kernel.verifier import (
    verify_execution_command,
    verify_execution_receipt,
    verify_executor_expressibility,
    verify_final_intent,
)
from src.decision_kernel.certificates.execution import (
    build_execution_command_certificate_from_final_intent,
    build_execution_receipt_certificate,
    build_executor_expressibility_certificate,
    build_final_intent_certificate_from_actionable,
)
from src.engine.event_bound_final_intent import validate_final_intent_cert_for_existing_executor


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


def test_final_intent_requires_actionable_parent():
    _, final_intent = final_intent_graph(drop_parent=claims.ACTIONABLE_TRADE)

    with pytest.raises(CertificateVerificationError, match="ActionableTradeCertificate"):
        verify_final_intent(final_intent, ())


def test_final_intent_matches_actionable_event_token_condition_direction():
    parents, final_intent = final_intent_graph()

    verify_final_intent(final_intent, parents)


def test_final_intent_rejects_wrong_token():
    parents, final_intent = final_intent_graph(final_payload={"token_id": "other-token"})

    with pytest.raises(CertificateVerificationError, match="token_id"):
        verify_final_intent(final_intent, parents)


def test_final_intent_rejects_wrong_condition():
    parents, final_intent = final_intent_graph(final_payload={"condition_id": "other-condition"})

    with pytest.raises(CertificateVerificationError, match="condition_id"):
        verify_final_intent(final_intent, parents)


def test_final_intent_rejects_wrong_direction():
    parents, final_intent = final_intent_graph(final_payload={"direction": "sell_yes"})

    with pytest.raises(CertificateVerificationError, match="direction"):
        verify_final_intent(final_intent, parents)


def test_final_intent_rejects_missing_order_type():
    parents, final_intent = final_intent_graph(final_payload={"order_type": ""})

    with pytest.raises(CertificateVerificationError, match="order_type"):
        verify_final_intent(final_intent, parents)


def test_final_intent_rejects_venue_order_id_before_submit():
    parents, final_intent = final_intent_graph(final_payload={"venue_order_id": "venue-1"})

    with pytest.raises(CertificateVerificationError, match="venue_order_id"):
        verify_final_intent(final_intent, parents)


def test_executor_expressibility_requires_final_intent_parent():
    parents, expressibility = executor_expressibility_graph(drop_parent=claims.FINAL_INTENT)

    with pytest.raises(CertificateVerificationError, match="FinalIntentCertificate"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_requires_can_express_true():
    parents, expressibility = executor_expressibility_graph(express_payload={"can_express": False})

    with pytest.raises(CertificateVerificationError, match="can_express"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_rejects_tick_misaligned_price():
    parents, expressibility = executor_expressibility_graph(express_payload={"limit_price": 0.333})

    with pytest.raises(CertificateVerificationError, match="tick-aligned"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_rejects_size_below_min_order():
    parents, expressibility = executor_expressibility_graph(express_payload={"size": 0.1, "min_order_size": 1.0})

    with pytest.raises(CertificateVerificationError, match="min_order_size"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_rejects_neg_risk_mismatch():
    parents, expressibility = executor_expressibility_graph(
        express_payload={"neg_risk": True},
        executable_payload={"neg_risk": False},
    )

    with pytest.raises(CertificateVerificationError, match="neg_risk"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_rejects_taker_order_when_executor_law_requires_maker():
    parents, expressibility = executor_expressibility_graph(express_payload={"post_only": False})

    with pytest.raises(CertificateVerificationError, match="passive maker"):
        verify_executor_expressibility(expressibility, parents)


def test_execution_command_builder_preserves_event_token_condition_direction():
    actionable, final_intent, expressibility, live_cap = builder_chain()

    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        decision_time=NOW,
    )

    assert command.payload["event_id"] == actionable.payload["event_id"]
    assert command.payload["token_id"] == actionable.payload["token_id"]
    assert command.payload["condition_id"] == actionable.payload["condition_id"]
    assert command.payload["direction"] == actionable.payload["direction"]
    verify_execution_command(command, (actionable, final_intent, expressibility, live_cap))


def test_execution_command_builder_deterministic_idempotency_key():
    actionable, final_intent, expressibility, live_cap = builder_chain()

    first = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        decision_time=NOW,
    )
    second = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        decision_time=NOW,
    )

    assert first.payload["idempotency_key"] == second.payload["idempotency_key"]


def test_execution_command_builder_no_venue_order_id_before_submit():
    actionable, final_intent, expressibility, live_cap = builder_chain()

    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        decision_time=NOW,
    )

    assert command.payload["venue_order_id"] is None
    assert command.payload["submitted"] is False


def test_execution_command_builder_rejects_invalid_final_intent_parent():
    actionable, final_intent, expressibility, live_cap = builder_chain(final_payload={"token_id": "other-token"})

    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        decision_time=NOW,
    )

    with pytest.raises(CertificateVerificationError, match="token_id"):
        verify_execution_command(command, (actionable, final_intent, expressibility, live_cap))


def test_execution_receipt_submit_disabled_has_no_venue_order_id():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(execution_command_cert=command, decision_time=NOW)

    assert receipt.payload["status"] == "SUBMIT_DISABLED"
    assert receipt.payload["venue_order_id"] is None
    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_matches_execution_command():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(execution_command_cert=command, decision_time=NOW)

    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_submitted_fixture_response_verifies():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="SUBMITTED",
        reason_code="OK",
        submit_started_at=NOW.isoformat(),
        submit_finished_at=NOW.isoformat(),
        venue_order_id="venue-1",
        raw_response={"status": "submitted"},
    )

    assert receipt.payload["venue_order_id"] == "venue-1"
    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_rejected_fixture_response_verifies():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="REJECTED",
        reason_code="VENUE_REJECTED",
        submit_started_at=NOW.isoformat(),
        submit_finished_at=NOW.isoformat(),
        raw_response={"status": "rejected"},
    )

    assert receipt.payload["status"] == "REJECTED"
    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_timeout_requires_reconcile_followup():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="TIMEOUT_UNKNOWN",
        reason_code="SUBMIT_TIMEOUT",
    )

    with pytest.raises(CertificateVerificationError, match="reconciliation"):
        verify_execution_receipt(receipt, (command,))


def test_execution_receipt_timeout_fixture_response_verifies_with_reconcile_followup():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="TIMEOUT_UNKNOWN",
        reason_code="SUBMIT_TIMEOUT",
        submit_started_at=NOW.isoformat(),
        submit_finished_at=NOW.isoformat(),
        raw_response={"status": "timeout"},
        reconciliation_followup_required=True,
    )

    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_accepted_not_equal_filled():
    command = receipt_command()
    receipt = _cert(
        claims.EXECUTION_RECEIPT,
        "execution-receipt:accepted",
        {
            "event_id": command.payload["event_id"],
            "execution_command_id": command.payload["execution_command_id"],
            "final_intent_id": command.payload["final_intent_id"],
            "executor_name": command.payload["executor_name"],
            "status": "ACCEPTED",
            "submit_started_at": NOW.isoformat(),
            "submit_finished_at": NOW.isoformat(),
            "venue_order_id": "venue-1",
            "raw_response_hash": "hash",
            "idempotency_key": command.payload["idempotency_key"],
            "reason_code": "OK",
        },
        parents=(command,),
    )

    verify_execution_receipt(receipt, (command,))
    assert receipt.payload["status"] == "ACCEPTED"


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


def final_intent_graph(*, final_payload: dict | None = None, drop_parent: str | None = None):
    actionable = _cert(claims.ACTIONABLE_TRADE, "actionable:event-1", _actionable_payload())
    parents = tuple(parent for parent in (actionable,) if parent.certificate_type != drop_parent)
    payload = {**_final_intent_payload(actionable), **(final_payload or {})}
    final_intent = _cert(claims.FINAL_INTENT, "final-intent:intent-1", payload, parents=parents)
    return parents, final_intent


def executor_expressibility_graph(
    *,
    express_payload: dict | None = None,
    executable_payload: dict | None = None,
    drop_parent: str | None = None,
):
    final_parents, final_intent = final_intent_graph()
    executable = _cert(
        claims.EXECUTABLE_SNAPSHOT,
        "executable:exec-1",
        {"condition_id": "condition-1", "token_id": "yes-1", "neg_risk": False, **(executable_payload or {})},
    )
    live_cap = _cert(claims.LIVE_CAP, "live-cap:cap-1", _live_cap_payload())
    parents = tuple(
        parent for parent in (final_intent, executable, live_cap) if parent.certificate_type != drop_parent
    )
    payload = {**_expressibility_payload(final_intent), **(express_payload or {})}
    expressibility = _cert(claims.EXECUTOR_EXPRESSIBILITY, "executor-expressibility:intent-1", payload, parents=parents)
    return parents, expressibility


def builder_chain(final_payload: dict | None = None):
    actionable = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:event-1",
        {**_actionable_payload(), "live_cap_reserved_notional_usd": 5.0, "neg_risk": False},
    )
    final_intent = build_final_intent_certificate_from_actionable(actionable_cert=actionable, decision_time=NOW)
    if final_payload:
        final_intent = _cert(claims.FINAL_INTENT, "final-intent:intent-1", {**final_intent.payload, **final_payload}, parents=(actionable,))
    executable = _cert(
        claims.EXECUTABLE_SNAPSHOT,
        "executable:exec-1",
        {"condition_id": "condition-1", "token_id": "yes-1", "neg_risk": False},
    )
    live_cap = _cert(claims.LIVE_CAP, "live-cap:cap-1", _live_cap_payload())
    expressibility = build_executor_expressibility_certificate(
        final_intent_cert=final_intent,
        executable_snapshot_cert=executable,
        live_cap_cert=live_cap,
        decision_time=NOW,
        executor_native_intent_hash=validate_final_intent_cert_for_existing_executor(final_intent),
    )
    return actionable, final_intent, expressibility, live_cap


def receipt_command():
    actionable, final_intent, expressibility, live_cap = builder_chain()
    return build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        decision_time=NOW,
    )


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


def _final_intent_payload(actionable) -> dict:
    payload = _actionable_payload()
    return {
        "event_id": payload["event_id"],
        "actionable_certificate_hash": actionable.certificate_hash,
        "final_intent_id": payload["final_intent_id"],
        "family_id": payload["family_id"],
        "candidate_id": payload["candidate_id"],
        "condition_id": payload["condition_id"],
        "token_id": payload["token_id"],
        "direction": payload["direction"],
        "side": "BUY",
        "order_type": "POST_ONLY_LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "maker_intent": True,
        "limit_price": 0.4,
        "size": 10.0,
        "notional_usd": 4.0,
        "executable_snapshot_id": payload["executable_snapshot_id"],
        "execution_price_type": "ExecutionPrice",
        "fee_deducted": True,
        "neg_risk": False,
        "tick_size": 0.01,
        "min_order_size": 1.0,
        "fee_rate": 0.0,
        "executable_snapshot_hash": "a" * 64,
        "cost_basis_hash": "b" * 64,
        "cost_basis_id": "cost_basis:" + ("b" * 16),
        "executor_order_type": "GTC",
        "decision_source_context": {
            "source_id": "edli_event_bound",
            "model_family": "edli_v1",
            "forecast_issue_time": NOW.isoformat(),
            "forecast_valid_time": NOW.isoformat(),
            "forecast_fetch_time": NOW.isoformat(),
            "forecast_available_at": NOW.isoformat(),
            "raw_payload_hash": "c" * 64,
            "degradation_level": "OK",
            "forecast_source_role": "entry_primary",
            "authority_tier": "FORECAST",
            "decision_time": NOW.isoformat(),
            "decision_time_status": "OK",
            "observation_time": NOW.isoformat(),
            "observation_available_at": NOW.isoformat(),
            "polymarket_end_anchor_source": "gamma_explicit",
            "first_member_observed_time": NOW.isoformat(),
            "run_complete_time": NOW.isoformat(),
            "zeus_submit_intent_time": NOW.isoformat(),
            "venue_ack_time": NOW.isoformat(),
        },
        "passive_maker_context": {
            "spread_usd": "0.01",
            "quote_age_ms": 0,
            "expected_fill_probability": "0.1",
            "orderbook_hash_age_ms": 0,
        },
        "live_cap_usage_id": payload["live_cap_usage_id"],
        "source": "existing_final_intent_builder",
        "submitted": False,
        "venue_order_id": None,
    }


def _expressibility_payload(final_intent) -> dict:
    payload = final_intent.payload
    return {
        "event_id": payload["event_id"],
        "final_intent_id": payload["final_intent_id"],
        "executor_name": "execute_final_intent",
        "executor_capability_version": "existing_executor_passive_limit_v1",
        "can_express": True,
        "passed": True,
        "reason_code": "OK",
        "executor_native_intent_hash": "d" * 64,
        "order_type": payload["order_type"],
        "side": payload["side"],
        "direction": payload["direction"],
        "token_id": payload["token_id"],
        "condition_id": payload["condition_id"],
        "limit_price": payload["limit_price"],
        "size": payload["size"],
        "time_in_force": payload["time_in_force"],
        "post_only": payload["post_only"],
        "maker_intent": payload["maker_intent"],
        "tick_size": payload["tick_size"],
        "min_order_size": payload["min_order_size"],
        "neg_risk": payload["neg_risk"],
        "fee_rate": payload["fee_rate"],
    }


def _live_cap_payload() -> dict:
    return {
        "usage_id": "cap-1",
        "event_id": "event-1",
        "reservation_status": "RESERVED",
        "max_notional_usd": 5.0,
        "reserved_notional_usd": 5.0,
        "order_count": 1,
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
