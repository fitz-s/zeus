# Created: 2026-05-26
# Last reused/audited: 2026-05-26
# Authority basis: PR332 EDLI live canary gate promotion package.
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from scripts.check_edli_live_canary_gate import (
    CANARY_PROOF_PASS,
    FAIL,
    WAITING_FOR_QUALIFYING_EVENT,
    evaluate_canary_artifact,
    load_canary_artifact,
)
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_profit_audit import LiveProfitAuditLedger
from src.state.schema.decision_certificates_schema import ensure_tables as ensure_decision_certificate_tables
from src.state.schema.edli_live_cap_usage_schema import ensure_table as ensure_live_cap_table


def test_missing_canary_artifact_waits_for_qualifying_event(tmp_path):
    assert load_canary_artifact(tmp_path / "missing.json") is None
    result = evaluate_canary_artifact(None)
    assert result.status == WAITING_FOR_QUALIFYING_EVENT


def test_canary_without_user_channel_or_reconcile_fails():
    artifact = _valid_artifact()
    artifact.pop("user_channel_observation")

    result = evaluate_canary_artifact(artifact)

    assert result.status == FAIL
    assert "CANARY_REQUIRES_USER_CHANNEL_OR_RECONCILE" in result.reasons


def test_canary_with_unresolved_submit_unknown_fails():
    artifact = _valid_artifact(unresolved_submit_unknown=True, submit_unknown={"status": "POST_SUBMIT_UNKNOWN"})

    result = evaluate_canary_artifact(artifact)

    assert result.status == FAIL
    assert "CANARY_SUBMIT_UNKNOWN_UNRESOLVED" in result.reasons


def test_canary_with_mismatched_economic_object_fails():
    artifact = _valid_artifact(pre_submit={"condition_id": "condition-1", "token_id": "other-token", "side": "BUY"})

    result = evaluate_canary_artifact(artifact)

    assert result.status == FAIL
    assert "CANARY_PRE_SUBMIT_TOKEN_ID_MISMATCH" in result.reasons


def test_canary_with_stale_quote_fails():
    artifact = _valid_artifact(quote_age_ms=1500)

    result = evaluate_canary_artifact(artifact, max_quote_age_ms=1000)

    assert result.status == FAIL
    assert "CANARY_QUOTE_STALE" in result.reasons


def test_canary_with_confirmed_lifecycle_and_cap_transition_passes():
    result = evaluate_canary_artifact(_valid_artifact())

    assert result.status == CANARY_PROOF_PASS
    assert result.reasons == ()


def test_artifact_only_pass_fails_under_db_verification():
    result = evaluate_canary_artifact(_valid_artifact(), conn=_conn())

    assert result.status == FAIL
    assert "CANARY_DB_PROJECTION_MISSING" in result.reasons


def test_db_verification_rejects_missing_profit_audit_row():
    conn = _conn()
    _seed_canary_db(conn, include_profit_audit=False)

    result = evaluate_canary_artifact(_valid_artifact(), conn=conn)

    assert result.status == FAIL
    assert "CANARY_DB_PROFIT_AUDIT_MISSING" in result.reasons


def test_db_verification_rejects_mismatched_pre_submit_token():
    conn = _conn()
    _seed_canary_db(conn, token_id="other-token")

    result = evaluate_canary_artifact(_valid_artifact(), conn=conn)

    assert result.status == FAIL
    assert "CANARY_DB_PRE_SUBMIT_TOKEN_ID_MISMATCH" in result.reasons


def test_db_verification_passes_matching_canonical_rows():
    conn = _conn()
    _seed_canary_db(conn)

    result = evaluate_canary_artifact(_valid_artifact(), conn=conn)

    assert result.status == CANARY_PROOF_PASS


def _valid_artifact(**overrides):
    artifact = {
        "event_id": "event-1",
        "aggregate_id": "event-1:intent-1",
        "final_intent_id": "intent-1",
        "execution_command_id": "command-1",
        "condition_id": "condition-1",
        "token_id": "token-1",
        "direction": "YES",
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "book_hash": "book-hash-1",
        "quote_seen_at": "2026-05-26T12:00:00+00:00",
        "quote_age_ms": 100,
        "best_bid": 0.42,
        "best_ask": 0.43,
        "limit_price": 0.42,
        "tickSize": "0.01",
        "negRisk": False,
        "balance_allowance_witness": {"status": "OK"},
        "heartbeat_witness": {"status": "OK"},
        "idempotency_key": "idem-1",
        "live_cap_usage_id": "usage-1",
        "venue_order_id": "venue-1",
        "user_channel_observation": {"trade_status": "CONFIRMED", "fill_authority_state": "FILL_CONFIRMED"},
        "cap_transition": {"to_status": "CONSUMED"},
        "order_lifecycle_projection": {"current_state": "USER_TRADE_OBSERVED", "pending_reconcile": False},
        "expected_edge": 0.01,
        "realized_state": "CONFIRMED",
        "pre_submit": {"condition_id": "condition-1", "token_id": "token-1", "side": "BUY"},
    }
    artifact.update(overrides)
    return artifact


def _seed_canary_db(conn: sqlite3.Connection, *, include_profit_audit: bool = True, token_id: str = "token-1") -> None:
    now = datetime(2026, 5, 26, 12, tzinfo=timezone.utc)
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=now,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitPlanBuilt",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=now,
        source_authority="engine_adapter",
    )
    pre_submit = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(token_id=token_id),
        occurred_at=now,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "usage_id": "usage-1",
            "reserved_notional_usd": 5.0,
            "reservation_status": "RESERVED",
        },
        occurred_at=now,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
            "usage_id": "usage-1",
        },
        occurred_at=now,
        source_authority="engine_adapter",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="UserTradeObserved",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "source_authority": "polymarket_user_channel",
            "trade_status": "CONFIRMED",
            "fill_authority_state": "FILL_CONFIRMED",
            "venue_order_id": "venue-1",
        },
        occurred_at=now,
        source_authority="user_channel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "execution_receipt_hash": "receipt-hash-1",
            "to_status": "CONSUMED",
            "projection_status": "CONSUMED",
            "transition_reason": "CONFIRMED",
        },
        occurred_at=now,
        source_authority="live_cap_ledger",
    )
    ensure_live_cap_table(conn)
    conn.execute(
        """
        INSERT INTO edli_live_cap_usage (
            usage_id, event_id, decision_time, cap_scope, max_notional_usd,
            max_orders_per_day, reserved_notional_usd, order_count,
            reservation_status, final_intent_id, execution_command_id,
            created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("usage-1", "event-1", now.isoformat(), "tiny_live_canary", 5.0, 1, 5.0, 1, "CONSUMED", "intent-1", "command-1", now.isoformat(), 1),
    )
    ensure_decision_certificate_tables(conn)
    conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_id, certificate_type, schema_version,
            canonicalization_version, semantic_key, claim_type, mode,
            decision_time, authority_id, authority_version, algorithm_id,
            algorithm_version, payload_json, payload_hash, certificate_hash,
            verifier_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cert-command-1",
            "ExecutionCommandCertificate",
            1,
            "v1",
            "command-1",
            "EXECUTION_COMMAND",
            "LIVE",
            now.isoformat(),
            "test",
            "v1",
            "test",
            "v1",
            json.dumps({"execution_command_id": "command-1"}),
            "payload-hash",
            "command-hash",
            "VERIFIED",
            now.isoformat(),
        ),
    )
    if include_profit_audit:
        LiveProfitAuditLedger(conn).insert_record(
            event_id="event-1",
            aggregate_id="event-1:intent-1",
            final_intent_id="intent-1",
            execution_command_id="command-1",
            condition_id="condition-1",
            token_id="token-1",
            direction="YES",
            side="BUY",
            realized_edge=0.01,
            order_lifecycle_state="CONFIRMED",
        )
    else:
        conn.execute("DELETE FROM edli_live_profit_audit WHERE aggregate_id = ?", ("event-1:intent-1",))


def _pre_submit_payload(**overrides):
    payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "token-1",
        "side": "BUY",
        "direction": "YES",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": "2026-05-26T12:00:00+00:00",
        "quote_seen_at": "2026-05-26T11:59:59.900000+00:00",
        "quote_age_ms": 100,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash-1",
        "current_best_bid": 0.42,
        "current_best_ask": 0.43,
        "limit_price": 0.42,
        "would_cross_book": False,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 5.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "book_authority_id": "execution_feasibility_evidence",
        "book_captured_at": "2026-05-26T11:59:59.900000+00:00",
        "heartbeat_authority_id": "heartbeat_supervisor",
        "heartbeat_checked_at": "2026-05-26T12:00:00+00:00",
        "user_ws_authority_id": "authenticated_user_channel",
        "user_ws_checked_at": "2026-05-26T12:00:00+00:00",
        "venue_connectivity_authority_id": "polymarket_preflight",
        "venue_connectivity_checked_at": "2026-05-26T12:00:00+00:00",
        "balance_allowance_authority_id": "polymarket_wallet_readonly",
        "balance_allowance_checked_at": "2026-05-26T12:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
