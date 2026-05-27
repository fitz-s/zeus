# Created: 2026-05-26
# Last reused/audited: 2026-05-26
# Authority basis: PR332 EDLI live profit-audit promotion package.
from __future__ import annotations

import json
import sqlite3

import pytest

from src.events.live_profit_audit import (
    LiveProfitAuditLedger,
    compute_realized_edge_from_authorities,
    promotion_summary,
    write_promotion_artifact,
)
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_profit_audit import verify_edli_live_promotion_artifact
from src.state.schema.decision_certificates_schema import ensure_tables as ensure_decision_certificate_tables


def test_profit_audit_requires_event_bound_identity_fields():
    ledger = LiveProfitAuditLedger(_conn())

    with pytest.raises(ValueError, match="EDLI_LIVE_PROFIT_AUDIT_REQUIRED_FIELDS_MISSING"):
        ledger.insert_record(
            aggregate_id="event-1:intent-1",
            condition_id="condition-1",
            token_id="token-1",
            order_lifecycle_state="CONFIRMED",
        )


def test_profit_audit_records_terminal_and_unknown_lifecycle_states(tmp_path):
    conn = _conn()
    ledger = LiveProfitAuditLedger(conn)
    ledger.insert_record(
        event_id="event-1",
        aggregate_id="event-1:intent-1",
        final_intent_id="intent-1",
        execution_command_id="command-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="YES",
        side="BUY",
        expected_edge=0.02,
        realized_edge=0.01,
        order_lifecycle_state="CONFIRMED",
        expected_edge_source_certificate_hash="actionable-hash-1",
        cost_basis_source_certificate_hash="cost-hash-1",
        fill_source_event_hash="fill-event-hash-1",
        promotion_eligible=1,
    )
    ledger.insert_record(
        event_id="event-2",
        aggregate_id="event-2:intent-2",
        final_intent_id="intent-2",
        execution_command_id="command-2",
        condition_id="condition-2",
        token_id="token-2",
        direction="YES",
        side="BUY",
        expected_edge=0.01,
        order_lifecycle_state="POST_SUBMIT_UNKNOWN",
    )

    summary = promotion_summary(conn)
    assert summary.canary_count == 1
    assert summary.confirmed_fill_count == 1
    assert summary.terminal_no_fill_count == 0
    assert summary.reconciled_no_order_count == 0
    assert summary.unresolved_unknowns == 1
    assert summary.realized_edge_bps == 100.0
    assert summary.median_realized_edge_bps_from_confirmed_fills == 100.0

    artifact_path = tmp_path / "promotion.json"
    write_promotion_artifact(conn, str(artifact_path))
    artifact = json.loads(artifact_path.read_text())
    assert artifact["schema"] == "edli_live_promotion_v1"
    assert artifact["canary_count"] == 1
    assert artifact["confirmed_fill_count"] == 1
    assert artifact["realized_edge_bps"] == 100.0
    assert artifact["median_realized_edge_bps_from_confirmed_fills"] == 100.0
    assert artifact["unresolved_unknowns"] == 1
    assert artifact["audit_ids"]
    assert artifact["source_summary_hash"]


def test_verified_promotion_artifact_rejects_scalar_or_stale_json(tmp_path):
    conn = _conn()
    _seed_confirmed_aggregate(conn)
    artifact_path = tmp_path / "promotion.json"
    write_promotion_artifact(conn, str(artifact_path))
    artifact = json.loads(artifact_path.read_text())

    verified = verify_edli_live_promotion_artifact(
        conn,
        artifact,
        min_canary_count=1,
        max_unresolved_unknowns=0,
        min_realized_edge_bps=0,
    )
    assert verified.ok is True

    scalar = {"canary_count": 1, "unresolved_unknowns": 0, "realized_edge_bps": 1}
    rejected = verify_edli_live_promotion_artifact(
        conn,
        scalar,
        min_canary_count=1,
        max_unresolved_unknowns=0,
        min_realized_edge_bps=0,
    )
    assert rejected.ok is False
    assert rejected.reason == "EDLI_LIVE_PROMOTION_ARTIFACT_SCHEMA_INVALID"

    conn.execute(
        "UPDATE edli_live_profit_audit SET realized_edge = ? WHERE aggregate_id = ?",
        (-0.01, "event-1:intent-1"),
    )
    stale = verify_edli_live_promotion_artifact(
        conn,
        artifact,
        min_canary_count=1,
        max_unresolved_unknowns=0,
        min_realized_edge_bps=0,
    )
    assert stale.ok is False
    assert stale.reason.startswith("EDLI_LIVE_PROMOTION_ARTIFACT_DB_MISMATCH")


def test_terminal_no_fill_does_not_unlock_scaleout_promotion(tmp_path):
    conn = _conn()
    _seed_confirmed_aggregate(conn)
    conn.execute("DELETE FROM edli_live_profit_audit WHERE aggregate_id = ?", ("event-1:intent-1",))
    ledger = LiveProfitAuditLedger(conn)
    ledger.insert_record(
        event_id="event-1",
        aggregate_id="event-1:intent-1",
        final_intent_id="intent-1",
        execution_command_id="command-1",
        condition_id="condition-1",
        token_id="token-1",
        order_lifecycle_state="TERMINAL_NO_FILL",
    )

    artifact_path = tmp_path / "promotion.json"
    write_promotion_artifact(conn, str(artifact_path))
    artifact = json.loads(artifact_path.read_text())

    assert artifact["terminal_no_fill_count"] == 1
    assert artifact["confirmed_fill_count"] == 0
    rejected = verify_edli_live_promotion_artifact(
        conn,
        artifact,
        min_canary_count=1,
        max_unresolved_unknowns=0,
        min_realized_edge_bps=0,
    )
    assert rejected.ok is False
    assert rejected.reason == "EDLI_LIVE_PROMOTION_CANARY_COUNT_INSUFFICIENT"


def test_confirmed_fill_requires_positive_realized_edge_for_scaleout(tmp_path):
    conn = _conn()
    _seed_confirmed_aggregate(conn, realized_edge=0.0)
    artifact_path = tmp_path / "promotion.json"
    write_promotion_artifact(conn, str(artifact_path))
    artifact = json.loads(artifact_path.read_text())

    rejected = verify_edli_live_promotion_artifact(
        conn,
        artifact,
        min_canary_count=1,
        max_unresolved_unknowns=0,
        min_realized_edge_bps=0,
    )
    assert rejected.ok is False
    assert rejected.reason == "EDLI_LIVE_PROMOTION_REALIZED_EDGE_INSUFFICIENT"


def test_confirmed_fill_must_be_promotion_eligible_for_scaleout(tmp_path):
    conn = _conn()
    _seed_confirmed_aggregate(conn)
    conn.execute(
        "UPDATE edli_live_profit_audit SET promotion_eligible = 0 WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    )
    artifact_path = tmp_path / "promotion.json"
    write_promotion_artifact(conn, str(artifact_path))

    rejected = verify_edli_live_promotion_artifact(
        conn,
        json.loads(artifact_path.read_text()),
        min_canary_count=1,
        max_unresolved_unknowns=0,
        min_realized_edge_bps=0,
    )
    assert rejected.ok is False
    assert rejected.reason == "EDLI_LIVE_PROMOTION_CONFIRMED_FILL_NOT_PROMOTION_ELIGIBLE"


@pytest.mark.parametrize(
    ("field", "reason"),
    (
        ("expected_edge_source_certificate_hash", "EDLI_LIVE_PROMOTION_EXPECTED_EDGE_PROVENANCE_MISSING"),
        ("cost_basis_source_certificate_hash", "EDLI_LIVE_PROMOTION_COST_BASIS_PROVENANCE_MISSING"),
        ("fill_source_event_hash", "EDLI_LIVE_PROMOTION_FILL_PROVENANCE_MISSING"),
    ),
)
def test_confirmed_fill_requires_promotion_provenance_for_scaleout(tmp_path, field, reason):
    conn = _conn()
    _seed_confirmed_aggregate(conn)
    conn.execute(
        f"UPDATE edli_live_profit_audit SET {field} = NULL WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    )
    artifact_path = tmp_path / "promotion.json"
    write_promotion_artifact(conn, str(artifact_path))

    rejected = verify_edli_live_promotion_artifact(
        conn,
        json.loads(artifact_path.read_text()),
        min_canary_count=1,
        max_unresolved_unknowns=0,
        min_realized_edge_bps=0,
    )
    assert rejected.ok is False
    assert rejected.reason == reason


def test_live_order_lifecycle_events_emit_profit_audit_rows():
    conn = _conn()
    ledger = _seed_confirmed_aggregate(conn)

    row = conn.execute(
        "SELECT * FROM edli_live_profit_audit WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    ).fetchone()
    row = conn.execute(
        """
        SELECT *
        FROM edli_live_profit_audit
        WHERE aggregate_id = ? AND order_lifecycle_state = 'CONFIRMED'
        """,
        ("event-1:intent-1",),
    ).fetchone()
    assert row is not None
    assert row["order_lifecycle_state"] == "CONFIRMED"
    assert row["condition_id"] == "condition-1"
    assert row["token_id"] == "token-1"
    assert row["expected_cost_basis"] == 0.421
    assert row["expected_cost_basis"] != row["limit_price"]
    assert row["expected_fee"] == 0.001
    assert row["expected_spread_cost"] == 0.0005
    assert row["visible_depth_fill_lcb"] == 0.95
    assert row["order_policy"] == "maker_post_only"
    assert row["native_token_side"] == "YES"
    assert row["avg_fill_price"] == 0.44
    assert row["filled_size"] == 10.0
    assert row["fees"] == 0.0
    assert row["realized_edge"] == pytest.approx(0.01)
    assert row["edge_value_usd"] == pytest.approx(0.1)
    assert row["pnl_usd"] is None
    assert row["promotion_eligible"] == 1
    assert ledger.get_projection("event-1:intent-1").pending_reconcile is False


def test_spoofed_lifecycle_realized_edge_is_ignored():
    conn = _conn()
    _seed_confirmed_aggregate(conn, realized_edge=0.01, spoofed_lifecycle_realized_edge=999.0)

    row = conn.execute(
        """
        SELECT realized_edge, edge_value_usd, pnl_usd, promotion_eligible
        FROM edli_live_profit_audit
        WHERE aggregate_id = ? AND order_lifecycle_state = 'CONFIRMED'
        """,
        ("event-1:intent-1",),
    ).fetchone()

    assert row["realized_edge"] == pytest.approx(0.01)
    assert row["edge_value_usd"] == pytest.approx(0.1)
    assert row["pnl_usd"] is None
    assert row["promotion_eligible"] == 1


def test_realized_edge_computes_sell_yes_proceeds_semantics():
    conn = _conn()
    _seed_authority_certificates(
        conn,
        cert_overrides={
            ("cost", "side"): "SELL",
            ("edge", "side"): "SELL",
        },
    )

    realized = compute_realized_edge_from_authorities(
        conn=conn,
        cost_model_cert_hash="cost-hash-1",
        expected_edge_cert_hash="actionable-hash-1",
        fill_event_hash="fill-event-hash-1",
        pre_submit={
            "condition_id": "condition-1",
            "token_id": "token-1",
            "side": "SELL",
            "direction": "YES",
            "native_token_side": "YES",
            "order_policy": "maker_post_only",
            "requested_size": 100.0,
        },
        fill_payload={"avg_fill_price": 0.47, "filled_size": 5.0, "fees": 0.01},
    )

    assert realized is not None
    assert realized["realized_edge"] == pytest.approx(0.47 - 0.45 - (0.01 / 5.0))
    assert realized["edge_value_usd"] == pytest.approx((0.47 - 0.45 - (0.01 / 5.0)) * 5.0)
    assert "pnl_usd" not in realized


def test_realized_edge_partial_fill_uses_filled_size_not_requested_size():
    conn = _conn()
    _seed_authority_certificates(conn)

    realized = compute_realized_edge_from_authorities(
        conn=conn,
        cost_model_cert_hash="cost-hash-1",
        expected_edge_cert_hash="actionable-hash-1",
        fill_event_hash="fill-event-hash-1",
        pre_submit={
            "condition_id": "condition-1",
            "token_id": "token-1",
            "side": "BUY",
            "direction": "YES",
            "native_token_side": "YES",
            "order_policy": "maker_post_only",
            "requested_size": 100.0,
        },
        fill_payload={"avg_fill_price": 0.44, "filled_size": 2.0, "fees": 0.0},
    )

    assert realized is not None
    assert realized["realized_edge"] == pytest.approx(0.01)
    assert realized["edge_value_usd"] == pytest.approx(0.02)


@pytest.mark.parametrize(
    ("cert_kind", "field", "value"),
    (
        ("cost", "condition_id", "other-condition"),
        ("cost", "token_id", "other-token"),
        ("edge", "condition_id", "other-condition"),
        ("edge", "token_id", "other-token"),
        ("edge", "direction", "NO"),
        ("cost", "native_token_side", "NO"),
    ),
)
def test_certificate_identity_mismatch_blocks_promotion_eligibility(cert_kind, field, value):
    conn = _conn()
    _seed_authority_certificates(conn, cert_overrides={(cert_kind, field): value})
    _seed_confirmed_aggregate(conn, seed_certificates=False)

    row = conn.execute(
        """
        SELECT realized_edge, promotion_eligible
        FROM edli_live_profit_audit
        WHERE aggregate_id = ? AND order_lifecycle_state = 'CONFIRMED'
        """,
        ("event-1:intent-1",),
    ).fetchone()

    assert row["realized_edge"] is None
    assert row["promotion_eligible"] == 0


def test_confirmed_fill_without_fill_economics_is_not_promotion_eligible():
    conn = _conn()
    _seed_confirmed_aggregate(conn, include_fill_economics=False)

    row = conn.execute(
        """
        SELECT realized_edge, avg_fill_price, filled_size, promotion_eligible
        FROM edli_live_profit_audit
        WHERE aggregate_id = ? AND order_lifecycle_state = 'CONFIRMED'
        """,
        ("event-1:intent-1",),
    ).fetchone()

    assert row["realized_edge"] is None
    assert row["avg_fill_price"] is None
    assert row["filled_size"] is None
    assert row["promotion_eligible"] == 0


def test_missing_cost_basis_certificate_keeps_audit_non_promotion_eligible():
    conn = _conn()
    ledger = _seed_confirmed_aggregate(conn, seed_cost_certificate=False)

    row = conn.execute(
        "SELECT promotion_eligible FROM edli_live_profit_audit WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    ).fetchone()

    assert row["promotion_eligible"] == 0
    assert ledger.get_projection("event-1:intent-1").current_state == "CAP_TRANSITIONED"


def _seed_confirmed_aggregate(
    conn: sqlite3.Connection,
    *,
    realized_edge: float = 0.01,
    include_fill_economics: bool = True,
    seed_cost_certificate: bool = True,
    seed_certificates: bool = True,
    spoofed_lifecycle_realized_edge: float | None = None,
) -> LiveOrderAggregateLedger:
    if seed_certificates:
        _seed_authority_certificates(conn, include_cost=seed_cost_certificate)
    ledger = LiveOrderAggregateLedger(conn)
    now = "2026-05-26T12:00:00+00:00"
    from datetime import datetime

    occurred_at = datetime.fromisoformat(now)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=occurred_at,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitPlanBuilt",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=occurred_at,
        source_authority="engine_adapter",
    )
    pre_submit = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),
        occurred_at=occurred_at,
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
        occurred_at=occurred_at,
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
        occurred_at=occurred_at,
        source_authority="engine_adapter",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "command-1"},
        occurred_at=occurred_at,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "venue_order_id": "venue-1",
        },
        occurred_at=occurred_at,
        source_authority="existing_executor",
    )
    trade_payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "source_authority": "polymarket_user_channel",
        "trade_status": "CONFIRMED",
        "fill_authority_state": "FILL_CONFIRMED",
        "venue_order_id": "venue-1",
        "raw_user_channel_message_hash": "trade-msg-1",
    }
    if include_fill_economics:
        trade_payload.update(
            {
                "avg_fill_price": 0.45 - realized_edge,
                "filled_size": 10.0,
                "fees": 0.0,
            }
        )
    if spoofed_lifecycle_realized_edge is not None:
        trade_payload["realized_edge"] = spoofed_lifecycle_realized_edge
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="UserTradeObserved",
        payload=trade_payload,
        occurred_at=occurred_at,
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
        occurred_at=occurred_at,
        source_authority="live_cap_ledger",
    )
    return ledger


def _seed_authority_certificates(
    conn: sqlite3.Connection,
    *,
    include_cost: bool = True,
    cert_overrides: dict[tuple[str, str], object] | None = None,
) -> None:
    ensure_decision_certificate_tables(conn)
    cert_overrides = cert_overrides or {}
    edge_payload = {
        "q_live": 0.45,
        "expected_edge": 0.029,
        "condition_id": "condition-1",
        "token_id": "token-1",
        "side": "BUY",
        "direction": "YES",
        "native_token_side": "YES",
        "order_policy": "maker_post_only",
    }
    cost_payload = {
        "expected_cost_basis": 0.421,
        "expected_fee": 0.001,
        "expected_spread_cost": 0.0005,
        "visible_depth_fill_lcb": 0.95,
        "order_policy": "maker_post_only",
        "native_token_side": "YES",
        "condition_id": "condition-1",
        "token_id": "token-1",
        "side": "BUY",
        "direction": "YES",
    }
    for (kind, field), value in cert_overrides.items():
        if kind == "edge":
            edge_payload[field] = value
        elif kind == "cost":
            cost_payload[field] = value
    rows = [
        (
            "actionable-cert-1",
            "ActionableTradeCertificate",
            "actionable-hash-1",
            edge_payload,
        )
    ]
    if include_cost:
        rows.append(
            (
                "cost-cert-1",
                "ExecutableCostCertificate",
                "cost-hash-1",
                cost_payload,
            )
        )
    for certificate_id, certificate_type, certificate_hash, payload in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO decision_certificates (
                certificate_id, certificate_type, schema_version,
                canonicalization_version, semantic_key, claim_type, mode,
                decision_time, source_available_at, agent_received_at,
                persisted_at, max_parent_source_available_at,
                max_parent_agent_received_at, max_parent_persisted_at,
                authority_id, authority_version, algorithm_id, algorithm_version,
                config_hash, model_version_hash, payload_json, payload_hash,
                certificate_hash, verifier_status, created_at
            ) VALUES (
                ?, ?, 1, 'canonical-json-v1', ?, 'edli_live_profit_authority', 'LIVE',
                '2026-05-26T12:00:00+00:00', NULL, NULL, NULL, NULL, NULL, NULL,
                'test_authority', 'v1', 'test_algorithm', 'v1',
                NULL, NULL, ?, ?, ?, 'VERIFIED', '2026-05-26T12:00:00+00:00'
            )
            """,
            (
                certificate_id,
                certificate_type,
                certificate_id,
                json.dumps(payload, sort_keys=True),
                f"payload-{certificate_id}",
                certificate_hash,
            ),
        )


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
        "q_live": 0.45,
        "expected_cost_basis": 0.421,
        "expected_fee": 0.001,
        "expected_spread_cost": 0.0005,
        "visible_depth_fill_lcb": 0.95,
        "order_policy": "maker_post_only",
        "native_token_side": "YES",
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
        "expected_edge_source_certificate_hash": "actionable-hash-1",
        "cost_basis_source_certificate_hash": "cost-hash-1",
    }
    payload.update(overrides)
    return payload


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
