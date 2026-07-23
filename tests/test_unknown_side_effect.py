# Created: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-05-15; last_reused=2026-05-15
# Purpose: R3 M2 unknown-side-effect semantics for post-POST submit uncertainty.
# Reuse: Run when executor submit exception handling, venue command recovery,
#        or idempotency/economic-intent duplicate blocking changes.
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M2.yaml
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
"""M2: post-side-effect submit uncertainty must not become semantic rejection."""

from __future__ import annotations

import json
import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

NOW = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)


def _entry_submit_payload() -> dict:
    return {
        "execution_capability": {
            "allowed": True,
            "components": [
                {
                    "component": "entry_economics",
                    "allowed": True,
                    "details": {
                        "q_live": 0.62,
                        "q_lcb_5pct": 0.55,
                        "expected_edge": 0.05,
                        "limit_price": 0.50,
                        "submit_edge": 0.05,
                        "expected_profit_usd": 1.00,
                        "min_entry_price": 0.05,
                        "min_expected_profit_usd": 1.00,
                        "submit_edge_density": 0.10,
                        "min_submit_edge_density": 0.05,
                        "shares": 20.0,
                        "qkernel_side": "YES",
                    },
                },
                {
                    "component": "entry_actionable_certificate",
                    "allowed": True,
                    "details": {"certificate_id": "cert-m2"},
                },
            ],
        },
    }


def _actionable_payload(*, token_id: str, price: float) -> dict:
    q_lcb = 0.62
    submit_edge = q_lcb - float(price)
    return {
        "event_id": "event-m2",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": f"snap-{token_id}",
        "family_id": "family-m2",
        "candidate_id": "candidate-m2",
        "condition_id": "condition-m2",
        "token_id": token_id,
        "direction": "buy_yes",
        "strategy_key": "center_buy",
        "executable_snapshot_id": f"snap-{token_id}",
        "q_live": 0.70,
        "q_lcb_5pct": q_lcb,
        "c_fee_adjusted": price,
        "c_cost_95pct": price,
        "p_fill_lcb": 0.1,
        "trade_score": submit_edge,
        "action_score": submit_edge,
        "min_entry_price": 0.10,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.70,
            "payoff_q_lcb": q_lcb,
            "cost": price,
            "edge_lcb": submit_edge,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 10.0,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.01,
            "selection_guard_basis": "EDGE_POSITIVE",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": q_lcb,
            "direction_law_ok": True,
            "coherence_allows": True,
        },
        "fdr_family_id": "family-m2",
        "kelly_decision_id": "kelly-m2",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-m2",
        "live_cap_usage_id": "cap-m2",
        "final_intent_id": "intent-m2",
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": True,
        "submitted": False,
    }


def _insert_actionable_certificate(conn: sqlite3.Connection, *, token_id: str, price: float) -> str:
    payload_json = json.dumps(_actionable_payload(token_id=token_id, price=price), sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    certificate_hash = hashlib.sha256(f"actionable:{token_id}:{price}".encode()).hexdigest()
    conn.execute(
        """
        INSERT OR REPLACE INTO decision_certificates (
            certificate_id, certificate_type, schema_version, canonicalization_version,
            semantic_key, claim_type, mode, decision_time, source_available_at,
            agent_received_at, persisted_at, max_parent_source_available_at,
            max_parent_agent_received_at, max_parent_persisted_at, authority_id,
            authority_version, algorithm_id, algorithm_version, config_hash,
            model_version_hash, payload_json, payload_hash, certificate_hash,
            verifier_status, created_at
        ) VALUES (?, 'ActionableTradeCertificate', 1, 'test', ?, 'actionable_trade',
            'LIVE', ?, ?, ?, ?, ?, ?, ?, 'test-authority', 'test', 'test-algo',
            'test', NULL, NULL, ?, ?, ?, 'VERIFIED', ?)
        """,
        (
            f"cert-{certificate_hash[:12]}",
            f"actionable:{token_id}:{price}",
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
            payload_json,
            payload_hash,
            certificate_hash,
            NOW.isoformat(),
        ),
    )
    return certificate_hash


@pytest.fixture
def conn(monkeypatch):
    """In-memory trades DB with live-money gates neutralized for unit tests."""
    from src.state.db import init_schema, init_schema_trade_only
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.collateral_ledger import CollateralLedger, CollateralSnapshot

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    init_schema_trade_only(c)
    init_collateral_schema(c)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_sell_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_sell", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.data.polymarket_client.resolve_funder_address",
        lambda: "0x0000000000000000000000000000000000000abc",
    )
    monkeypatch.setattr("src.architecture.gate_runtime.check", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.execution.executor._entry_control_pause_component",
        lambda *args, **kwargs: {
            "component": "entries_pause_control_override",
            "allowed": True,
            "reason": "not_paused",
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._assert_collateral_allows_sell",
        lambda *args, **kwargs: {
            "component": "collateral_snapshot_refresh",
            "allowed": True,
            "reason": "allowed",
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._entry_taker_quality_component",
        lambda *args, **kwargs: {
            "component": "entry_taker_quality",
            "allowed": True,
            "reason": "allowed",
        },
    )

    def _seed_submit_collateral(conn: sqlite3.Connection, **_kwargs) -> dict:
        CollateralLedger(conn).set_snapshot(
            CollateralSnapshot(
                pusd_balance_micro=1_000_000_000,
                pusd_allowance_micro=1_000_000_000,
                usdc_e_legacy_balance_micro=0,
                ctf_token_balances={
                    "tok-m2": 1_000_000_000,
                    "tok-m2-exit-init": 1_000_000_000,
                    "tok-m2-exit-lazy": 1_000_000_000,
                    "tok-m2-exit-submit-pre": 1_000_000_000,
                },
                ctf_token_allowances={
                    "tok-m2": 1_000_000_000,
                    "tok-m2-exit-init": 1_000_000_000,
                    "tok-m2-exit-lazy": 1_000_000_000,
                    "tok-m2-exit-submit-pre": 1_000_000_000,
                },
                reserved_pusd_for_buys_micro=0,
                reserved_tokens_for_sells={},
                captured_at=datetime.now(timezone.utc),
                authority_tier="CHAIN",
                raw_balance_payload_hash="test-collateral",
            )
        )
        return {
            "component": "collateral_snapshot_refresh",
            "allowed": True,
            "reason": "allowed",
            "authority_tier": "CHAIN",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setattr("src.execution.executor._refresh_entry_collateral_snapshot_for_submit", _seed_submit_collateral)
    monkeypatch.setattr("src.execution.executor._refresh_exit_collateral_snapshot_for_submit", _seed_submit_collateral)
    yield c
    c.close()


def _ensure_snapshot(conn, *, token_id: str, snapshot_id: str | None = None) -> str:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    snapshot_id = snapshot_id or f"snap-{token_id}"
    if get_snapshot(conn, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        conn,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-m2",
            event_id="event-m2",
            event_slug="weather-m2",
            condition_id="condition-m2",
            question_id="question-m2",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            fee_details={
                "source": "test",
                "token_id": token_id,
                "fee_rate_fraction": 0.0,
                "fee_rate_bps": 0.0,
                "fee_rate_source_field": "fee_rate_fraction",
                "fee_rate_raw_unit": "fraction",
            },
            token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.56"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=NOW,
            freshness_deadline=NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


def _decision_source_context():
    from src.contracts.execution_intent import DecisionSourceContext

    return DecisionSourceContext(
        source_id="tigge",
        model_family="ecmwf_ifs025",
        forecast_issue_time="2026-04-27T09:00:00+00:00",
        forecast_valid_time="2026-04-27T18:00:00+00:00",
        forecast_fetch_time="2026-04-27T10:00:00+00:00",
        forecast_available_at="2026-04-27T09:30:00+00:00",
        raw_payload_hash="f" * 64,
        degradation_level="OK",
        forecast_source_role="entry_primary",
        authority_tier="FORECAST",
        decision_time=NOW.isoformat(),
        decision_time_status="OK",
    )


def _make_entry_intent(conn, *, token_id: str = "tok-m2", price: float = 0.55):
    from src.contracts import Direction
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts.slippage_bps import SlippageBps

    snapshot_id = _ensure_snapshot(conn, token_id=token_id)
    q_lcb = 0.62
    submit_edge = q_lcb - float(price)
    qkernel_cost = float(price)
    certificate_hash = _insert_actionable_certificate(conn, token_id=token_id, price=float(price))
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="condition-m2",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.05,
        q_live=0.70,
        q_lcb_5pct=q_lcb,
        expected_edge=submit_edge,
        min_entry_price=0.10,
        min_expected_profit_usd=0.05,
        min_submit_edge_density=0.02,
        selection_authority_applied="qkernel_spine",
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.70,
            "payoff_q_lcb": q_lcb,
            "cost": qkernel_cost,
            "edge_lcb": submit_edge,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 10.0,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.01,
            "selection_guard_basis": "EDGE_POSITIVE",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": q_lcb,
            "direction_law_ok": True,
            "coherence_allows": True,
        },
        actionable_certificate_hash=certificate_hash,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
        decision_source_context=_decision_source_context(),
        submit_order_type="FOK",
        post_only=False,
        taker_quality_proof={
            "passed": True,
            "taker_fee_adjusted_edge": "0.08",
            "taker_expected_profit_usd": "0.40",
            "maker_expected_profit_usd": "0.10",
            "incremental_expected_profit_usd": "0.30",
            "model_confidence": "0.82",
        },
    )


def _insert_unknown_side_effect(
    conn,
    *,
    command_id: str = "cmd-m2",
    token_id: str = "tok-m2",
    idem: str = "1" * 32,
    created_at: datetime | None = None,
    price: float = 0.55,
    size: float = 18.19,
    final_event: str = "SUBMIT_TIMEOUT_UNKNOWN",
    final_event_payload: dict | None = None,
    raw_response_json: str | None = None,
    signed_order_hash: str | None = None,
    order_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    from src.state.venue_command_repo import append_event, insert_command, insert_submission_envelope
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    created = created_at or NOW
    snapshot_id = _ensure_snapshot(conn, token_id=token_id)
    envelope_id = f"env-{command_id}"
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="condition-m2",
            question_id="question-m2",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            side="BUY",
            price=Decimal(str(price)),
            size=Decimal(str(size)),
            order_type="GTC",
            post_only=False,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            neg_risk=False,
            fee_details={
                "source": "test",
                "token_id": token_id,
                "fee_rate_fraction": 0.0,
                "fee_rate_bps": 0.0,
                "fee_rate_source_field": "fee_rate_fraction",
                "fee_rate_raw_unit": "fraction",
            },
            canonical_pre_sign_payload_hash="d" * 64,
            signed_order=(b"signed" if signed_order_hash else None),
            signed_order_hash=signed_order_hash,
            raw_request_hash="e" * 64,
            raw_response_json=raw_response_json,
            order_id=order_id,
            trade_ids=(),
            transaction_hashes=(),
            error_code=error_code,
            error_message=error_message,
            captured_at=created.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    insert_command(
        conn,
        command_id=command_id,
        snapshot_id=snapshot_id,
        envelope_id=envelope_id,
        position_id="trade-m2",
        decision_id="decision-m2",
        idempotency_key=idem,
        intent_kind="ENTRY",
        market_id="condition-m2",
        token_id=token_id,
        side="BUY",
        size=size,
        price=price,
        created_at=created.isoformat(),
        q_version="test-q-version",
        snapshot_checked_at=created.isoformat(),
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at=created.isoformat(),
        payload=_entry_submit_payload(),
    )
    append_event(
        conn,
        command_id=command_id,
        event_type=final_event,
        occurred_at=created.isoformat(),
        payload=final_event_payload,
    )
    conn.commit()


def _insert_pre_sdk_decision_log(
    conn,
    *,
    decision_id: str = "decision-m2",
    reason: str = (
        "execution_intent_rejected:pusd_allowance_insufficient: "
        "required_micro=100 available_allowance_micro=0 allowance_micro=0"
    ),
) -> None:
    artifact = {
        "mode": "opening_hunt",
        "started_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
        "no_trade_cases": [
            {
                "decision_id": decision_id,
                "city": "Karachi",
                "target_date": "2026-05-17",
                "range_label": "test market",
                "rejection_stage": "EXECUTION_FAILED",
                "rejection_reasons": [reason],
            }
        ],
    }
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES ('opening_hunt', ?, ?, ?, ?, 'live')
        """,
        (NOW.isoformat(), NOW.isoformat(), json.dumps(artifact), NOW.isoformat()),
    )
    conn.commit()


def _materialize_confirmed_entry_exposure(
    conn,
    *,
    command_id: str,
    position_id: str = "trade-m2",
    venue_order_id: str = "ord-m2-materialized",
    include_position_current: bool = True,
) -> None:
    conn.execute(
        "UPDATE venue_commands SET venue_order_id = ? WHERE command_id = ?",
        (venue_order_id, command_id),
    )
    cur = conn.execute(
        """
        INSERT INTO venue_trade_facts (
          trade_id, venue_order_id, command_id, state, filled_size,
          fill_price, source, observed_at, local_sequence, raw_payload_hash
        ) VALUES ('trade-materialized', ?, ?, 'CONFIRMED', '9.393702',
                  '0.73', 'REST', ?, 1, ?)
        """,
        (venue_order_id, command_id, NOW.isoformat(), "c" * 64),
    )
    trade_fact_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO position_lots (
          position_id, state, shares, entry_price_avg, source_command_id,
          source_trade_fact_id, captured_at, state_changed_at, source,
          observed_at, local_sequence, raw_payload_hash
        ) VALUES (4392, 'CONFIRMED_EXPOSURE', '9.393702', '0.73', ?,
                  ?, ?, ?, 'REST', ?, 1, ?)
        """,
        (
            command_id,
            trade_fact_id,
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
            "d" * 64,
        ),
    )
    if include_position_current:
        conn.execute(
            """
            INSERT INTO position_current (
              position_id, phase, strategy_key, market_id, token_id, shares,
              chain_state, order_id, order_status, updated_at, temperature_metric
            ) VALUES (?, 'active', 'center_buy', 'condition-m2', 'tok-m2',
                      9.393702, 'synced', ?, 'partial', ?, 'high')
            """,
            (position_id, venue_order_id, NOW.isoformat()),
        )
    conn.commit()


def _review_clearance_payload(command_id: str = "cmd-m2") -> dict:
    return {
        "schema_version": 1,
        "reason": "review_cleared_no_venue_side_effect",
        "command_id": command_id,
        "decision_id": "decision-m2",
        "proof_class": "pre_sdk_no_side_effect",
        "side_effect_boundary_crossed": False,
        "sdk_submit_attempted": False,
        "required_predicates": {
            "no_venue_order_id": True,
            "no_final_submission_envelope": True,
            "no_raw_response": True,
            "no_signed_order": True,
            "no_order_facts": True,
            "no_trade_facts": True,
            "no_submit_side_effect_events": True,
            "review_required_reason_pre_sdk": True,
        },
        "source_proof": {
            "source_commit": "test-commit",
            "source_function": "_live_order",
            "source_reason": "pre_submit_collateral_reservation_failed",
            "decision_id": "decision-m2",
        },
        "review_required_proof": {
            "reason": "recovery_no_venue_order_id",
            "allowed_reasons": [
                "pre_submit_collateral_reservation_failed",
                "recovery_no_venue_order_id",
            ],
        },
        "decision_log_proof": {"decision_log_id": 1},
    }


def _geoblock_403_payload() -> dict:
    return {
        "reason": "post_submit_exception_possible_side_effect",
        "exception_type": "PolyApiException",
        "exception_message": (
            "PolyApiException[status_code=403, error_message={'error': "
            "'Trading restricted in your region, please refer to available "
            "regions - https://docs.polymarket.com/developers/CLOB/geoblock'}]"
        ),
        "idempotency_key": "1" * 32,
    }


def _command(conn):
    return conn.execute("SELECT * FROM venue_commands ORDER BY created_at DESC LIMIT 1").fetchone()


def _events(conn, command_id: str) -> list[str]:
    return [
        row["event_type"]
        for row in conn.execute(
            "SELECT event_type FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
            (command_id,),
        )
    ]


def _capture_bound_submission_envelope(mock_client):
    bound = {}
    mock_client.bind_submission_envelope.side_effect = lambda envelope: bound.__setitem__("envelope", envelope)
    return bound


def _final_submit_result(
    bound: dict,
    *,
    success: bool,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    envelope = bound.get("envelope")
    if envelope is None:
        raise AssertionError("test client did not receive a bound submission envelope")
    raw_payload = {"success": success, "status": status}
    changes = {
        "raw_response_json": json.dumps(raw_payload, sort_keys=True, separators=(",", ":")),
    }
    if error_code is not None:
        changes["error_code"] = error_code
        changes["error_message"] = error_message or ""
    final = envelope.with_updates(**changes)
    result = {
        "success": success,
        "status": status,
        "_venue_submission_envelope": final.to_dict(),
    }
    if error_code is not None:
        result["errorCode"] = error_code
        result["errorMessage"] = error_message or ""
    return result


def test_network_timeout_after_POST_creates_unknown_not_rejected(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.return_value = None
    mock_client.place_limit_order.side_effect = TimeoutError("post timed out")

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order("trade-m2-timeout", intent, shares=18.0, conn=conn, decision_id="dec-m2-timeout")

    cmd = _command(conn)
    assert result.status == "unknown_side_effect"
    assert result.command_state == "SUBMIT_UNKNOWN_SIDE_EFFECT"
    assert "submit_unknown_side_effect" in (result.reason or "")
    assert cmd["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT"
    assert "SUBMIT_TIMEOUT_UNKNOWN" in _events(conn, cmd["command_id"])
    assert "SUBMIT_REJECTED" not in _events(conn, cmd["command_id"])


def test_ambiguous_submit_persists_deterministic_order_identity(conn):
    from src.execution.executor import _live_order
    from src.venue.polymarket_v2_adapter import AmbiguousSubmitError

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.return_value = None
    bound = _capture_bound_submission_envelope(mock_client)

    def _raise_ambiguous(**kwargs):
        envelope = bound["envelope"].with_updates(
            signed_order=b"signed-order",
            signed_order_hash=hashlib.sha256(b"signed-order").hexdigest(),
            order_id="0xdeterministic-order-id",
            error_code="V2_POST_SUBMIT_AMBIGUOUS",
            error_message="post timed out",
        )
        raise AmbiguousSubmitError("post timed out", envelope=envelope)

    mock_client.place_limit_order.side_effect = _raise_ambiguous

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order(
            "trade-m2-deterministic-id",
            intent,
            shares=18.0,
            conn=conn,
            decision_id="dec-m2-deterministic-id",
        )

    cmd = _command(conn)
    assert result.status == "unknown_side_effect"
    assert cmd["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT"
    assert cmd["venue_order_id"] == "0xdeterministic-order-id"
    event = conn.execute(
        """
        SELECT payload_json
          FROM venue_command_events
         WHERE command_id = ? AND event_type = 'SUBMIT_TIMEOUT_UNKNOWN'
         ORDER BY sequence_no DESC LIMIT 1
        """,
        (cmd["command_id"],),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert payload["venue_order_id"] == "0xdeterministic-order-id"
    envelope = conn.execute(
        "SELECT order_id, signed_order_hash FROM venue_submission_envelopes WHERE envelope_id = ?",
        (payload["final_submission_envelope_id"],),
    ).fetchone()
    assert envelope["order_id"] == "0xdeterministic-order-id"
    assert envelope["signed_order_hash"] == hashlib.sha256(b"signed-order").hexdigest()


def test_typed_venue_rejection_creates_SUBMIT_REJECTED(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.return_value = None
    bound = _capture_bound_submission_envelope(mock_client)
    mock_client.place_limit_order.side_effect = lambda **kwargs: _final_submit_result(
        bound,
        success=False,
        status="rejected",
        error_code="INVALID_ORDER",
        error_message="bad tick",
    )

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order("trade-m2-reject", intent, shares=18.0, conn=conn, decision_id="dec-m2-reject")

    cmd = _command(conn)
    assert result.status == "rejected"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])


def test_geoblock_polyapi_exception_creates_terminal_rejection(conn):
    from src.execution.executor import _live_order

    class PolyApiException(Exception):
        pass

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.return_value = None
    mock_client.place_limit_order.side_effect = PolyApiException(
        "PolyApiException[status_code=403, error_message={'error': "
        "'Trading restricted in your region, please refer to available regions - "
        "https://docs.polymarket.com/developers/CLOB/geoblock'}]"
    )

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order("trade-m2-geoblock", intent, shares=18.0, conn=conn, decision_id="dec-m2-geoblock")

    cmd = _command(conn)
    assert result.status == "rejected"
    assert "venue_rejected_geoblock_403" in (result.reason or "")
    assert cmd["state"] == "REJECTED"
    events = conn.execute(
        "SELECT event_type, payload_json FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        (cmd["command_id"],),
    ).fetchall()
    assert [row["event_type"] for row in events][-1] == "SUBMIT_REJECTED"
    payload = json.loads(events[-1]["payload_json"])
    assert payload["reason"] == "venue_rejected_geoblock_403"
    assert payload["venue_order_created"] is False
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in [row["event_type"] for row in events]


def test_marketable_buy_min_size_polyapi_exception_creates_terminal_rejection(conn):
    from src.execution.executor import _live_order

    class PolyApiException(Exception):
        pass

    intent = _make_entry_intent(conn, price=0.10)
    mock_client = MagicMock()
    mock_client.v2_preflight.return_value = None
    mock_client.place_limit_order.side_effect = PolyApiException(
        "PolyApiException[status_code=400, error_message={'error': "
        "'invalid amount for a marketable BUY order ($0.30), min size: $1'}]"
    )

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order(
            "trade-m2-marketable-buy-min",
            intent,
            shares=3.0,
            conn=conn,
            decision_id="dec-m2-marketable-buy-min",
        )

    cmd = _command(conn)
    assert result.status == "rejected"
    assert "venue_rejected_invalid_amount_400" in (result.reason or "")
    assert cmd["state"] == "REJECTED"
    events = conn.execute(
        "SELECT event_type, payload_json FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        (cmd["command_id"],),
    ).fetchall()
    assert [row["event_type"] for row in events][-1] == "SUBMIT_REJECTED"
    payload = json.loads(events[-1]["payload_json"])
    assert payload["reason"] == "venue_rejected_invalid_amount_400"
    assert payload["proof_class"] == "deterministic_venue_invalid_amount_400"
    assert payload["venue_order_created"] is False
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in [row["event_type"] for row in events]


def test_marketable_buy_min_size_without_currency_polyapi_exception_creates_terminal_rejection(conn):
    from src.execution.executor import _live_order

    class PolyApiException(Exception):
        pass

    intent = _make_entry_intent(conn, price=0.10)
    mock_client = MagicMock()
    mock_client.v2_preflight.return_value = None
    mock_client.place_limit_order.side_effect = PolyApiException(
        "PolyApiException[status_code=400, error_message={'error': "
        "'invalid amount for a marketable BUY order ($0.30), min size: 1'}]"
    )

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order(
            "trade-m2-marketable-buy-min-no-currency",
            intent,
            shares=3.0,
            conn=conn,
            decision_id="dec-m2-marketable-buy-min-no-currency",
        )

    cmd = _command(conn)
    assert result.status == "rejected"
    assert "venue_rejected_invalid_amount_400" in (result.reason or "")
    assert cmd["state"] == "REJECTED"
    events = conn.execute(
        "SELECT event_type, payload_json FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        (cmd["command_id"],),
    ).fetchall()
    assert [row["event_type"] for row in events][-1] == "SUBMIT_REJECTED"
    payload = json.loads(events[-1]["payload_json"])
    assert payload["reason"] == "venue_rejected_invalid_amount_400"
    assert payload["proof_class"] == "deterministic_venue_invalid_amount_400"
    assert payload["venue_order_created"] is False
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in [row["event_type"] for row in events]


def test_risk_allocator_pre_submit_exception_does_not_create_unknown_side_effect(conn, monkeypatch):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    monkeypatch.setattr(
        "src.execution.executor._assert_risk_allocator_allows_submit",
        lambda _intent: (_ for _ in ()).throw(RuntimeError("unknown_side_effect_threshold")),
    )

    result = _live_order(
        "trade-m2-risk-pre-submit",
        intent,
        shares=5.0,
        conn=conn,
        decision_id="dec-m2-risk-pre-submit",
    )

    assert result.status == "rejected"
    assert result.command_state == "REJECTED"
    assert "risk_allocator_pre_submit_blocked" in (result.reason or "")
    assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0


def test_pre_post_signing_exception_safe_to_retry(conn):
    from src.data.polymarket_client import V2PreflightError
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.side_effect = V2PreflightError("pre-post gate failed")

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order("trade-m2-prepost", intent, shares=18.0, conn=conn, decision_id="dec-m2-prepost")

    cmd = _command(conn)
    assert result.status == "rejected"
    assert "v2_preflight_failed" in (result.reason or "")
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])
    mock_client.place_limit_order.assert_not_called()


def test_generic_pre_post_preflight_exception_safe_to_retry(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    mock_client = MagicMock()
    mock_client.v2_preflight.side_effect = RuntimeError("credential setup failed")

    with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
        result = _live_order("trade-m2-generic-prepost", intent, shares=18.0, conn=conn, decision_id="dec-m2-generic-prepost")

    cmd = _command(conn)
    assert result.status == "rejected"
    assert "v2_preflight_exception" in (result.reason or "")
    assert result.command_state == "REJECTED"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])
    mock_client.place_limit_order.assert_not_called()


def test_exit_client_construction_exception_safe_to_retry(conn):
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    token_id = "tok-m2-exit-init"
    _ensure_snapshot(conn, token_id=token_id)

    with patch(
        "src.data.polymarket_client.PolymarketClient",
        side_effect=RuntimeError("missing credentials"),
    ):
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-m2-exit-init",
                token_id=token_id,
                shares=18.0,
                current_price=0.55,
                executable_snapshot_id=f"snap-{token_id}",
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="dec-m2-exit-init",
        )

    cmd = _command(conn)
    assert result.status == "rejected"
    assert "pre_submit_client_init_failed" in (result.reason or "")
    assert result.command_state == "REJECTED"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])


def test_exit_lazy_adapter_preflight_exception_safe_to_retry(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    token_id = "tok-m2-exit-lazy"
    _ensure_snapshot(conn, token_id=token_id)

    def _raise_lazy_adapter_failure(self):
        raise RuntimeError("lazy adapter credential failure")

    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient._ensure_v2_adapter",
        _raise_lazy_adapter_failure,
    )

    result = execute_exit_order(
        create_exit_order_intent(
            trade_id="trade-m2-exit-lazy",
            token_id=token_id,
            shares=18.0,
            current_price=0.55,
            executable_snapshot_id=f"snap-{token_id}",
            executable_snapshot_min_tick_size=Decimal("0.01"),
            executable_snapshot_min_order_size=Decimal("0.01"),
            executable_snapshot_neg_risk=False,
        ),
        conn=conn,
        decision_id="dec-m2-exit-lazy",
    )

    cmd = _command(conn)
    assert result.status == "rejected"
    assert result.reason == "V2_PREFLIGHT_EXCEPTION"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])
    rejected_event = conn.execute(
        """
        SELECT payload_json
        FROM venue_command_events
        WHERE command_id = ? AND event_type = 'SUBMIT_REJECTED'
        """,
        (cmd["command_id"],),
    ).fetchone()
    rejected_payload = json.loads(rejected_event["payload_json"])
    final_envelope_id = rejected_payload["final_submission_envelope_id"]
    final_envelope = conn.execute(
        "SELECT error_code FROM venue_submission_envelopes WHERE envelope_id = ?",
        (final_envelope_id,),
    ).fetchone()
    assert final_envelope["error_code"] == "V2_PREFLIGHT_EXCEPTION"


def test_exit_adapter_submit_pre_snapshot_failure_safe_to_retry(conn, tmp_path):
    from src.data.polymarket_client import PolymarketClient
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    class FakePreflightOnlyClient:
        def __init__(self):
            self.calls = []

        def get_ok(self):
            self.calls.append(("get_ok",))
            return {"ok": True}

    token_id = "tok-m2-exit-submit-pre"
    _ensure_snapshot(conn, token_id=token_id)
    q1_evidence = tmp_path / "q1_egress.txt"
    q1_evidence.write_text(
        "Q1 Zeus egress evidence sentinel\n"
        "authority_basis: test\n"
        "operator_attestation: test current egress accepted\n"
        "live_side_effects: none; HTTPS GET probes only\n"
        "raw_secrets_or_signed_payloads: none\n"
        "probe_results:\n"
        "[{\"effective_url\":\"https://clob.polymarket.com/ok\",\"status_code\":200}]\n",
        encoding="utf-8",
    )
    fake_sdk = FakePreflightOnlyClient()
    adapter = PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=q1_evidence,
        client_factory=lambda **_kwargs: fake_sdk,
    )
    client = PolymarketClient()
    client._v2_adapter = adapter

    with patch("src.data.polymarket_client.PolymarketClient", return_value=client):
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="trade-m2-exit-submit-pre",
                token_id=token_id,
                shares=18.0,
                current_price=0.55,
                executable_snapshot_id=f"snap-{token_id}",
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="dec-m2-exit-submit-pre",
        )

    cmd = _command(conn)
    assert result.status == "rejected"
    assert result.reason == "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY"
    assert cmd["state"] == "REJECTED"
    assert "SUBMIT_REJECTED" in _events(conn, cmd["command_id"])
    assert "SUBMIT_TIMEOUT_UNKNOWN" not in _events(conn, cmd["command_id"])
    assert fake_sdk.calls == []


def test_duplicate_retry_blocked_during_unknown(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    first_client = MagicMock()
    first_client.v2_preflight.return_value = None
    first_client.place_limit_order.side_effect = TimeoutError("post timed out")
    with patch("src.data.polymarket_client.PolymarketClient", return_value=first_client):
        first = _live_order("trade-m2-dupe", intent, shares=18.0, conn=conn, decision_id="dec-m2-dupe")
    assert first.status == "unknown_side_effect"

    second_client = MagicMock()
    second_client.v2_preflight.return_value = None
    with patch("src.data.polymarket_client.PolymarketClient", return_value=second_client):
        second = _live_order("trade-m2-dupe", intent, shares=18.0, conn=conn, decision_id="dec-m2-dupe")

    assert second.status == "unknown_side_effect"
    assert "idempotency_collision" in (second.reason or "")
    second_client.place_limit_order.assert_not_called()


def test_strategy_cannot_submit_replacement_with_different_idempotency_key_for_same_economic_intent(conn):
    from src.execution.executor import _live_order

    intent = _make_entry_intent(conn)
    first_client = MagicMock()
    first_client.v2_preflight.return_value = None
    first_client.place_limit_order.side_effect = TimeoutError("post timed out")
    with patch("src.data.polymarket_client.PolymarketClient", return_value=first_client):
        first = _live_order("trade-m2-economic", intent, shares=18.0, conn=conn, decision_id="dec-m2-a")
    assert first.status == "unknown_side_effect"

    second_client = MagicMock()
    second_client.v2_preflight.return_value = None
    with patch("src.data.polymarket_client.PolymarketClient", return_value=second_client):
        second = _live_order("trade-m2-economic-replacement", intent, shares=18.0, conn=conn, decision_id="dec-m2-b")

    assert second.status == "unknown_side_effect"
    assert "economic_intent_duplication" in (second.reason or "")
    second_client.place_limit_order.assert_not_called()


def test_economic_intent_duplicate_uses_idempotency_precision(conn):
    """0.3 and 0.1 + 0.2 must compare as the same order economics."""
    from src.execution.executor import _live_order

    token_id = "tok-m2-float"
    first_intent = _make_entry_intent(conn, token_id=token_id, price=0.3)
    second_intent = _make_entry_intent(conn, token_id=token_id, price=0.1 + 0.2)

    first_client = MagicMock()
    first_client.v2_preflight.return_value = None
    first_client.place_limit_order.side_effect = TimeoutError("post timed out")
    with patch("src.data.polymarket_client.PolymarketClient", return_value=first_client):
        first = _live_order("trade-m2-float-a", first_intent, shares=18.0, conn=conn, decision_id="dec-m2-float-a")
    assert first.status == "unknown_side_effect"

    second_client = MagicMock()
    second_client.v2_preflight.return_value = None
    with patch("src.data.polymarket_client.PolymarketClient", return_value=second_client):
        second = _live_order("trade-m2-float-b", second_intent, shares=18.0, conn=conn, decision_id="dec-m2-float-b")

    assert second.status == "unknown_side_effect"
    assert "economic_intent_duplication" in (second.reason or "")
    second_client.place_limit_order.assert_not_called()


def test_reconciliation_finding_order_converts_unknown_to_acked_or_partial_until_confirmed(conn):
    from src.execution.command_recovery import reconcile_unresolved_commands

    _insert_unknown_side_effect(conn, idem="2" * 32)
    client = MagicMock()
    client.find_order_by_idempotency_key.return_value = {
        "orderID": "ord-m2-acked",
        "status": "LIVE",
    }

    summary = reconcile_unresolved_commands(conn, client)

    cmd = _command(conn)
    assert summary["advanced"] == 1
    assert cmd["state"] == "ACKED"
    assert cmd["venue_order_id"] == "ord-m2-acked"
    assert "SUBMIT_ACKED" in _events(conn, cmd["command_id"])

    _insert_unknown_side_effect(conn, command_id="cmd-m2-filled", idem="3" * 32, token_id="tok-m2-filled")
    client.find_order_by_idempotency_key.return_value = {
        "orderID": "ord-m2-filled",
        "status": "FILLED",
    }
    summary = reconcile_unresolved_commands(conn, client)
    filled = conn.execute("SELECT * FROM venue_commands WHERE command_id = ?", ("cmd-m2-filled",)).fetchone()
    assert summary["advanced"] >= 1
    assert filled["state"] == "PARTIAL"
    assert filled["venue_order_id"] == "ord-m2-filled"
    assert "PARTIAL_FILL_OBSERVED" in _events(conn, "cmd-m2-filled")
    assert "FILL_CONFIRMED" not in _events(conn, "cmd-m2-filled")

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-confirmed",
        idem="9" * 32,
        token_id="tok-m2-confirmed",
    )
    client.find_order_by_idempotency_key.return_value = {
        "orderID": "ord-m2-confirmed",
        "status": "CONFIRMED",
    }
    summary = reconcile_unresolved_commands(conn, client)
    confirmed = conn.execute("SELECT * FROM venue_commands WHERE command_id = ?", ("cmd-m2-confirmed",)).fetchone()
    assert summary["advanced"] >= 1
    assert confirmed["state"] == "REVIEW_REQUIRED"
    assert confirmed["venue_order_id"] == "ord-m2-confirmed"
    assert "REVIEW_REQUIRED" in _events(conn, "cmd-m2-confirmed")
    assert "FILL_CONFIRMED" not in _events(conn, "cmd-m2-confirmed")


def test_review_required_side_effect_still_blocks_same_economic_intent(conn):
    from src.execution.executor import _live_order
    from src.state.venue_command_repo import append_event, find_unknown_command_by_economic_intent

    token_id = "tok-m2-review-block"
    _insert_unknown_side_effect(conn, command_id="cmd-m2-review-block", idem="5" * 32, token_id=token_id, size=18.0)
    append_event(
        conn,
        command_id="cmd-m2-review-block",
        event_type="REVIEW_REQUIRED",
        occurred_at=NOW.isoformat(),
        payload={"reason": "recovery_confirmed_requires_trade_fact"},
    )
    conn.commit()

    unresolved = find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id=token_id,
        side="BUY",
        price=0.55,
        size=18.0,
    )
    assert unresolved is not None
    assert unresolved["state"] == "REVIEW_REQUIRED"

    intent = _make_entry_intent(conn, token_id=token_id)
    second_client = MagicMock()
    second_client.v2_preflight.return_value = None
    with patch("src.data.polymarket_client.PolymarketClient", return_value=second_client):
        second = _live_order("trade-m2-review-replacement", intent, shares=18.0, conn=conn, decision_id="dec-m2-review-b")

    assert second.status == "unknown_side_effect"
    assert "economic_intent_duplication" in (second.reason or "")
    assert second.command_state == "REVIEW_REQUIRED"
    second_client.place_limit_order.assert_not_called()


def test_review_required_pre_sdk_no_side_effect_can_be_cleared(conn):
    from src.execution.command_recovery import clear_review_required_no_venue_side_effect
    from src.risk_allocator.governor import count_unknown_side_effects
    from src.state.venue_command_repo import find_unknown_command_by_economic_intent

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear",
        token_id="tok-m2-clear",
        idem="7" * 32,
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )
    _insert_pre_sdk_decision_log(conn)

    payload = clear_review_required_no_venue_side_effect(
        conn,
        "cmd-m2-clear",
        source_commit="test-commit",
        source_function="_live_order",
        source_reason="pre_submit_collateral_reservation_failed",
        reviewed_by="pytest",
        occurred_at=NOW.isoformat(),
    )

    cmd = conn.execute(
        "SELECT * FROM venue_commands WHERE command_id = ?",
        ("cmd-m2-clear",),
    ).fetchone()
    assert cmd["state"] == "REJECTED"
    events = conn.execute(
        "SELECT event_type, payload_json FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        ("cmd-m2-clear",),
    ).fetchall()
    assert [row["event_type"] for row in events][-1] == "REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT"
    event_payload = json.loads(events[-1]["payload_json"])
    assert event_payload == payload
    assert payload["reason"] == "review_cleared_no_venue_side_effect"
    assert payload["proof_class"] == "pre_sdk_no_side_effect"
    assert payload["side_effect_boundary_crossed"] is False
    assert payload["sdk_submit_attempted"] is False
    assert payload["required_predicates"]["no_venue_order_id"] is True

    unknown_count, unknown_markets = count_unknown_side_effects(conn)
    assert unknown_count == 0
    assert unknown_markets == ()
    assert find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id="tok-m2-clear",
        side="BUY",
        price=0.55,
        size=18.19,
    ) is None


def test_review_required_confirmed_entry_exposure_does_not_hold_global_reduce_only(conn):
    from src.risk_allocator.governor import count_unknown_side_effects

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-materialized",
        token_id="tok-m2",
        idem="e" * 32,
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_confirmed_requires_trade_fact"},
    )
    _materialize_confirmed_entry_exposure(conn, command_id="cmd-m2-materialized")

    unknown_count, unknown_markets = count_unknown_side_effects(conn)
    assert unknown_count == 0
    assert unknown_markets == ()


def test_review_required_confirmed_trade_without_position_projection_still_blocks(conn):
    from src.risk_allocator.governor import count_unknown_side_effects

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-materialized-no-position",
        token_id="tok-m2",
        idem="f" * 32,
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_confirmed_requires_trade_fact"},
    )
    _materialize_confirmed_entry_exposure(
        conn,
        command_id="cmd-m2-materialized-no-position",
        include_position_current=False,
    )

    unknown_count, unknown_markets = count_unknown_side_effects(conn)
    assert unknown_count == 1
    assert unknown_markets == ("condition-m2",)


def test_review_required_recovery_no_venue_exposure_can_be_cleared(conn):
    from src.execution.command_recovery import (
        build_review_required_no_venue_exposure_proof,
        clear_review_required_no_venue_exposure,
    )
    from src.risk_allocator.governor import count_unknown_side_effects

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-no-exposure",
        token_id="tok-m2-clear-no-exposure",
        idem="a" * 32,
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )

    class FakeAdapter:
        def get_open_orders(self):
            return [{"id": "unrelated", "asset_id": "other-token", "status": "LIVE"}]

        def get_trades(self):
            return [{"id": "old", "asset_id": "tok-m2-clear-no-exposure", "match_time": "1"}]

    proof = build_review_required_no_venue_exposure_proof(
        conn,
        "cmd-m2-clear-no-exposure",
        FakeAdapter(),
        observed_at=NOW.isoformat(),
    )
    payload = clear_review_required_no_venue_exposure(
        conn,
        "cmd-m2-clear-no-exposure",
        venue_absence_proof=proof,
        source_commit="test-commit",
        source_function="operator_review",
        reviewed_by="pytest",
        occurred_at=NOW.isoformat(),
    )

    cmd = conn.execute(
        "SELECT * FROM venue_commands WHERE command_id = ?",
        ("cmd-m2-clear-no-exposure",),
    ).fetchone()
    assert cmd["state"] == "EXPIRED"
    events = conn.execute(
        "SELECT event_type, payload_json FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        ("cmd-m2-clear-no-exposure",),
    ).fetchall()
    assert [row["event_type"] for row in events][-1] == "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
    assert json.loads(events[-1]["payload_json"]) == payload
    assert payload["proof_class"] == "venue_absence_no_exposure"
    assert payload["venue_absence_proof"]["matching_open_order_count"] == 0
    assert payload["venue_absence_proof"]["matching_trade_count"] == 0

    unknown_count, unknown_markets = count_unknown_side_effects(conn)
    assert unknown_count == 0
    assert unknown_markets == ()


def test_review_required_recovery_no_venue_live_order_is_adopted(conn):
    from src.execution.command_recovery import reconcile_unresolved_commands
    from src.execution.exchange_reconcile import init_exchange_reconcile_schema
    from src.risk_allocator.governor import count_unknown_side_effects

    token_id = "tok-m2-adopt-live"
    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-adopt-live",
        token_id=token_id,
        idem="6" * 32,
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )
    init_exchange_reconcile_schema(conn)
    conn.execute(
        """
        INSERT INTO exchange_reconcile_findings (
          finding_id, kind, subject_id, context, evidence_json, recorded_at
        ) VALUES ('finding-adopt-live', 'exchange_ghost_order', 'ord-adopt-live',
                  'ws_gap', ?, ?)
        """,
        (
            json.dumps({"reason": "exchange_open_order_absent_from_venue_commands"}),
            NOW.isoformat(),
        ),
    )
    conn.commit()

    class FakeAdapter:
        def get_open_orders(self):
            return [
                {
                    "id": "ord-adopt-live",
                    "asset_id": token_id,
                    "side": "BUY",
                    "price": "0.55",
                    "original_size": "18.19",
                    "size": "18.19",
                    "size_matched": "0",
                    "status": "LIVE",
                }
            ]

        def get_trades(self):
            return []

    summary = reconcile_unresolved_commands(conn, FakeAdapter())

    assert summary["advanced"] >= 1
    cmd = conn.execute(
        "SELECT state, venue_order_id FROM venue_commands WHERE command_id = ?",
        ("cmd-m2-adopt-live",),
    ).fetchone()
    assert dict(cmd) == {"state": "ACKED", "venue_order_id": "ord-adopt-live"}
    event = conn.execute(
        """
        SELECT event_type, payload_json
          FROM venue_command_events
         WHERE command_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        ("cmd-m2-adopt-live",),
    ).fetchone()
    assert event["event_type"] == "REVIEW_CLEARED_VENUE_ORDER_LIVE"
    payload = json.loads(event["payload_json"])
    assert payload["proof_class"] == "recovery_no_venue_order_id_live_order"
    assert payload["required_predicates"]["unique_matching_open_order"] is True

    fact = conn.execute(
        """
        SELECT venue_order_id, command_id, state, matched_size
          FROM venue_order_facts
         WHERE command_id = ?
        """,
        ("cmd-m2-adopt-live",),
    ).fetchone()
    assert dict(fact) == {
        "venue_order_id": "ord-adopt-live",
        "command_id": "cmd-m2-adopt-live",
        "state": "LIVE",
        "matched_size": "0",
    }
    finding = conn.execute(
        """
        SELECT resolved_at, resolution, resolved_by
          FROM exchange_reconcile_findings
         WHERE finding_id = 'finding-adopt-live'
        """
    ).fetchone()
    assert finding["resolved_at"] is not None
    assert finding["resolution"] == "command_recovery_no_venue_live_order_adopted"
    assert finding["resolved_by"] == "src.execution.command_recovery"

    unknown_count, unknown_markets = count_unknown_side_effects(conn)
    assert unknown_count == 0
    assert unknown_markets == ()


def test_review_required_recovery_no_venue_live_order_ambiguous_stays_review_required(conn):
    from src.execution.command_recovery import reconcile_unresolved_commands
    from src.state.venue_command_repo import find_unknown_command_by_economic_intent

    token_id = "tok-m2-adopt-ambiguous"
    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-adopt-ambiguous",
        token_id=token_id,
        idem="0" * 32,
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )

    class FakeAdapter:
        def get_open_orders(self):
            return [
                {
                    "id": order_id,
                    "asset_id": token_id,
                    "side": "BUY",
                    "price": "0.55",
                    "original_size": "18.19",
                    "size": "18.19",
                    "size_matched": "0",
                    "status": "LIVE",
                }
                for order_id in ("ord-amb-1", "ord-amb-2")
            ]

        def get_trades(self):
            return []

    summary = reconcile_unresolved_commands(conn, FakeAdapter())

    assert summary["advanced"] == 0
    cmd = conn.execute(
        "SELECT state, venue_order_id FROM venue_commands WHERE command_id = ?",
        ("cmd-m2-adopt-ambiguous",),
    ).fetchone()
    assert cmd["state"] == "REVIEW_REQUIRED"
    assert cmd["venue_order_id"] is None
    unresolved = find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id=token_id,
        side="BUY",
        price=0.55,
        size=18.19,
    )
    assert unresolved is not None
    assert unresolved["command_id"] == "cmd-m2-adopt-ambiguous"


def test_review_required_recovery_no_venue_exposure_rejects_matching_trade(conn):
    from src.execution.command_recovery import (
        build_review_required_no_venue_exposure_proof,
        clear_review_required_no_venue_exposure,
    )

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-has-trade",
        token_id="tok-m2-clear-has-trade",
        idem="b" * 32,
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )

    class FakeAdapter:
        def get_open_orders(self):
            return []

        def get_trades(self):
            return [
                {
                    "id": "trade-match",
                    "asset_id": "tok-m2-clear-has-trade",
                    "side": "BUY",
                    "price": "0.55",
                    "size": "18.19",
                    "match_time": str(NOW.timestamp() + 1),
                }
            ]

    proof = build_review_required_no_venue_exposure_proof(
        conn,
        "cmd-m2-clear-has-trade",
        FakeAdapter(),
        observed_at=NOW.isoformat(),
    )
    assert proof["matching_trade_count"] == 1
    with pytest.raises(ValueError, match="matching trades"):
        clear_review_required_no_venue_exposure(
            conn,
            "cmd-m2-clear-has-trade",
            venue_absence_proof=proof,
            source_commit="test-commit",
            source_function="operator_review",
            reviewed_by="pytest",
        )


def test_review_required_recovery_no_venue_exposure_rejects_matching_open_order(conn):
    from src.execution.command_recovery import (
        build_review_required_no_venue_exposure_proof,
        clear_review_required_no_venue_exposure,
    )

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-has-open",
        token_id="tok-m2-clear-has-open",
        idem="c" * 32,
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )

    class FakeAdapter:
        def get_open_orders(self):
            return [
                {
                    "id": "ord-match",
                    "asset_id": "tok-m2-clear-has-open",
                    "side": "BUY",
                    "price": "0.55",
                    "original_size": "18.19",
                    "status": "LIVE",
                }
            ]

        def get_trades(self):
            return []

    proof = build_review_required_no_venue_exposure_proof(
        conn,
        "cmd-m2-clear-has-open",
        FakeAdapter(),
        observed_at=NOW.isoformat(),
    )
    assert proof["matching_open_order_count"] == 1
    with pytest.raises(ValueError, match="matching open orders"):
        clear_review_required_no_venue_exposure(
            conn,
            "cmd-m2-clear-has-open",
            venue_absence_proof=proof,
            source_commit="test-commit",
            source_function="operator_review",
            reviewed_by="pytest",
        )


def test_review_required_recovery_no_venue_exposure_rejects_stale_read(conn):
    from src.execution.command_recovery import (
        build_review_required_no_venue_exposure_proof,
        clear_review_required_no_venue_exposure,
    )

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-stale-proof",
        token_id="tok-m2-clear-stale-proof",
        idem="d" * 32,
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )

    class FakeAdapter:
        def get_open_orders(self):
            return []

        def get_trades(self):
            return []

    proof = build_review_required_no_venue_exposure_proof(
        conn,
        "cmd-m2-clear-stale-proof",
        FakeAdapter(),
        observed_at=NOW.isoformat(),
    )
    with pytest.raises(ValueError, match="stale"):
        clear_review_required_no_venue_exposure(
            conn,
            "cmd-m2-clear-stale-proof",
            venue_absence_proof=proof,
            source_commit="test-commit",
            source_function="operator_review",
            reviewed_by="pytest",
            occurred_at=(NOW + timedelta(seconds=61)).isoformat(),
        )


def test_review_required_clearance_requires_decision_log_pre_sdk_proof(conn):
    from src.execution.command_recovery import clear_review_required_no_venue_side_effect

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-no-proof",
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )

    with pytest.raises(ValueError, match="decision_log EXECUTION_FAILED"):
        clear_review_required_no_venue_side_effect(
            conn,
            "cmd-m2-clear-no-proof",
            source_commit="test-commit",
            source_function="_live_order",
            source_reason="pre_submit_collateral_reservation_failed",
            reviewed_by="pytest",
        )


def test_review_clearance_event_rejects_bare_manual_payload(conn):
    from src.state.venue_command_repo import append_event

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-bare",
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )

    with pytest.raises(ValueError, match="reason=review_cleared_no_venue_side_effect"):
        append_event(
            conn,
            command_id="cmd-m2-clear-bare",
            event_type="REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT",
            occurred_at=NOW.isoformat(),
            payload={"reason": "manual_override"},
        )


def test_review_clearance_event_rejects_valid_payload_without_decision_log_db_proof(conn):
    from src.state.venue_command_repo import append_event

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-no-db-proof",
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
    )

    with pytest.raises(ValueError, match="decision_log collateral proof"):
        append_event(
            conn,
            command_id="cmd-m2-clear-no-db-proof",
            event_type="REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT",
            occurred_at=NOW.isoformat(),
            payload=_review_clearance_payload("cmd-m2-clear-no-db-proof"),
        )


def test_review_clearance_event_rejects_valid_payload_with_db_side_effect_evidence(conn):
    from src.state.venue_command_repo import append_event

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-forged-side-effect",
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "recovery_no_venue_order_id"},
        raw_response_json='{"orderID":"ord-real"}',
    )
    _insert_pre_sdk_decision_log(conn)

    with pytest.raises(ValueError, match="review clearance DB predicates failed"):
        append_event(
            conn,
            command_id="cmd-m2-clear-forged-side-effect",
            event_type="REVIEW_CLEARED_NO_VENUE_SIDE_EFFECT",
            occurred_at=NOW.isoformat(),
            payload=_review_clearance_payload("cmd-m2-clear-forged-side-effect"),
        )


def test_review_required_clearance_rejects_post_submit_review_required_reason(conn):
    from src.execution.command_recovery import clear_review_required_no_venue_side_effect

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-post-submit-review",
        final_event="REVIEW_REQUIRED",
        final_event_payload={
            "reason": "final_submission_envelope_persistence_failed",
            "detail": "place_limit_order returned None",
        },
    )
    _insert_pre_sdk_decision_log(conn)

    with pytest.raises(ValueError, match="review clearance predicates failed"):
        clear_review_required_no_venue_side_effect(
            conn,
            "cmd-m2-clear-post-submit-review",
            source_commit="test-commit",
            source_function="_live_order",
            source_reason="pre_submit_collateral_reservation_failed",
            reviewed_by="pytest",
        )


@pytest.mark.parametrize(
    "case",
    [
        "raw_response",
        "signed_order",
        "order_fact",
        "trade_fact",
        "submit_unknown_event",
    ],
)
def test_review_required_clearance_rejects_side_effect_evidence(conn, case):
    from src.execution.command_recovery import clear_review_required_no_venue_side_effect
    from src.state.venue_command_repo import append_event

    command_id = f"cmd-m2-clear-blocked-{case}"
    kwargs = {}
    if case == "raw_response":
        kwargs["raw_response_json"] = '{"status":"LIVE"}'
    if case == "signed_order":
        kwargs["signed_order_hash"] = "f" * 64
    _insert_unknown_side_effect(
        conn,
        command_id=command_id,
        token_id=f"tok-m2-clear-blocked-{case}",
        idem=("8" if case != "signed_order" else "9") * 32,
        final_event="SUBMIT_UNKNOWN" if case == "submit_unknown_event" else "REVIEW_REQUIRED",
        final_event_payload=(
            None if case == "submit_unknown_event"
            else {"reason": "recovery_no_venue_order_id"}
        ),
        **kwargs,
    )
    if case == "order_fact":
        conn.execute(
            """
            INSERT INTO venue_order_facts (
              venue_order_id, command_id, state, source, observed_at,
              local_sequence, raw_payload_hash
            ) VALUES ('ord-blocked', ?, 'LIVE', 'REST', ?, 1, ?)
            """,
            (command_id, NOW.isoformat(), "a" * 64),
        )
    if case == "trade_fact":
        conn.execute(
            """
            INSERT INTO venue_trade_facts (
              trade_id, venue_order_id, command_id, state, filled_size,
              fill_price, source, observed_at, local_sequence, raw_payload_hash
            ) VALUES ('trade-blocked', 'ord-blocked', ?, 'MATCHED', '1.00',
                      '0.55', 'REST', ?, 1, ?)
            """,
            (command_id, NOW.isoformat(), "b" * 64),
        )
    if case == "submit_unknown_event":
        append_event(
            conn,
            command_id=command_id,
            event_type="REVIEW_REQUIRED",
            occurred_at=NOW.isoformat(),
            payload={"reason": "still_requires_review"},
        )
    conn.commit()
    _insert_pre_sdk_decision_log(conn)

    with pytest.raises(ValueError, match="review clearance predicates failed"):
        clear_review_required_no_venue_side_effect(
            conn,
            command_id,
            source_commit="test-commit",
            source_function="_live_order",
            source_reason="pre_submit_collateral_reservation_failed",
            reviewed_by="pytest",
        )


def test_review_required_clearance_cannot_run_from_arbitrary_state(conn):
    from src.execution.command_recovery import clear_review_required_no_venue_side_effect

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-clear-wrong-state",
        final_event="SUBMIT_TIMEOUT_UNKNOWN",
    )
    _insert_pre_sdk_decision_log(conn)

    with pytest.raises(ValueError, match="only legal for REVIEW_REQUIRED"):
        clear_review_required_no_venue_side_effect(
            conn,
            "cmd-m2-clear-wrong-state",
            source_commit="test-commit",
            source_function="_live_order",
            source_reason="pre_submit_collateral_reservation_failed",
            reviewed_by="pytest",
        )


def test_submit_unknown_geoblock_403_can_be_terminalized(conn):
    from src.execution.command_recovery import clear_submit_unknown_geoblock_403
    from src.risk_allocator.governor import count_unknown_side_effects
    from src.state.venue_command_repo import find_unknown_command_by_economic_intent

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-geoblock",
        token_id="tok-m2-geoblock",
        idem="b" * 32,
        final_event="SUBMIT_TIMEOUT_UNKNOWN",
        final_event_payload=_geoblock_403_payload(),
    )

    payload = clear_submit_unknown_geoblock_403(
        conn,
        "cmd-m2-geoblock",
        reviewed_by="pytest",
        occurred_at=NOW.isoformat(),
    )

    cmd = conn.execute(
        "SELECT * FROM venue_commands WHERE command_id = ?",
        ("cmd-m2-geoblock",),
    ).fetchone()
    assert cmd["state"] == "SUBMIT_REJECTED"
    events = conn.execute(
        "SELECT event_type, payload_json FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        ("cmd-m2-geoblock",),
    ).fetchall()
    assert [row["event_type"] for row in events][-1] == "SUBMIT_REJECTED"
    event_payload = json.loads(events[-1]["payload_json"])
    assert event_payload == payload
    assert payload["reason"] == "venue_rejected_geoblock_403"
    assert payload["proof_class"] == "deterministic_venue_geoblock_403"
    assert payload["side_effect_boundary_crossed"] is True
    assert payload["venue_order_created"] is False
    assert payload["required_predicates"]["exception_message_geoblock_403"] is True

    unknown_count, unknown_markets = count_unknown_side_effects(conn)
    assert unknown_count == 0
    assert unknown_markets == ()
    assert find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id="tok-m2-geoblock",
        side="BUY",
        price=0.55,
        size=18.19,
    ) is None


def test_adapter_wrapped_geoblock_403_auto_terminalizes_without_replay_wait(conn):
    from src.execution.command_recovery import (
        _terminalize_submit_unknown_geoblock_403_if_proven,
    )

    command_id = "cmd-m2-geoblock-wrapped"
    order_id = "0xclient-derived-order-id"
    final_envelope_id = f"env-{command_id}"
    payload = _geoblock_403_payload()
    payload.update(
        {
            "exception_type": "AmbiguousSubmitError",
            "final_submission_envelope_id": final_envelope_id,
            "final_submission_envelope_command_id": command_id,
            "final_submission_envelope_stage": "post_sign_pre_ack_exception",
            "venue_order_id": order_id,
        }
    )
    _insert_unknown_side_effect(
        conn,
        command_id=command_id,
        final_event_payload=payload,
        signed_order_hash="f" * 64,
        order_id=order_id,
        error_code="V2_POST_SUBMIT_AMBIGUOUS",
        error_message=payload["exception_message"],
    )
    conn.execute(
        "UPDATE venue_commands SET venue_order_id = ? WHERE command_id = ?",
        (order_id, command_id),
    )
    conn.commit()
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    )

    terminal = _terminalize_submit_unknown_geoblock_403_if_proven(
        conn,
        command,
        occurred_at=NOW.isoformat(),
    )

    assert terminal is not None
    assert terminal["reason"] == "venue_rejected_geoblock_403"
    assert terminal["terminal_no_fill"] is True
    assert terminal["exposure_created"] is False
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()["state"] == "SUBMIT_REJECTED"
    assert _events(conn, command_id)[-1] == "SUBMIT_REJECTED"


def test_adapter_wrapped_geoblock_repair_refuses_raw_venue_response(conn):
    from src.execution.command_recovery import (
        _terminalize_submit_unknown_geoblock_403_if_proven,
    )

    command_id = "cmd-m2-geoblock-wrapped-response"
    order_id = "0xpossible-venue-order"
    final_envelope_id = f"env-{command_id}"
    payload = _geoblock_403_payload()
    payload.update(
        {
            "exception_type": "AmbiguousSubmitError",
            "final_submission_envelope_id": final_envelope_id,
            "final_submission_envelope_command_id": command_id,
            "final_submission_envelope_stage": "post_sign_pre_ack_exception",
            "venue_order_id": order_id,
        }
    )
    _insert_unknown_side_effect(
        conn,
        command_id=command_id,
        final_event_payload=payload,
        raw_response_json='{"orderID":"0xpossible-venue-order"}',
        signed_order_hash="e" * 64,
        order_id=order_id,
        error_code="V2_POST_SUBMIT_AMBIGUOUS",
        error_message=payload["exception_message"],
    )
    conn.execute(
        "UPDATE venue_commands SET venue_order_id = ? WHERE command_id = ?",
        (order_id, command_id),
    )
    conn.commit()
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    )

    assert _terminalize_submit_unknown_geoblock_403_if_proven(
        conn,
        command,
        occurred_at=NOW.isoformat(),
    ) is None
    assert _events(conn, command_id)[-1] == "SUBMIT_TIMEOUT_UNKNOWN"


@pytest.mark.parametrize(
    ("payload_update", "match"),
    [
        (
            {"exception_type": "TimeoutError", "exception_message": "post timed out"},
            "exception_message_geoblock_403",
        ),
        (
            {
                "exception_type": "PolyApiException",
                "exception_message": "PolyApiException[status_code=500, error_message={'error':'server'}]",
            },
            "exception_message_geoblock_403",
        ),
        (
            {"reason": "post_submit_exception_possible_side_effect", "exception_type": "RuntimeError"},
            "exception_type_polyapi",
        ),
    ],
)
def test_submit_unknown_geoblock_terminalization_rejects_ambiguous_exceptions(
    conn,
    payload_update,
    match,
):
    from src.execution.command_recovery import clear_submit_unknown_geoblock_403

    payload = _geoblock_403_payload()
    payload.update(payload_update)
    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-geoblock-ambiguous",
        final_event="SUBMIT_TIMEOUT_UNKNOWN",
        final_event_payload=payload,
    )

    with pytest.raises(ValueError, match=match):
        clear_submit_unknown_geoblock_403(
            conn,
            "cmd-m2-geoblock-ambiguous",
            reviewed_by="pytest",
        )


@pytest.mark.parametrize("case", ["raw_response", "signed_order", "order_fact", "trade_fact"])
def test_submit_unknown_geoblock_terminalization_rejects_side_effect_evidence(conn, case):
    from src.execution.command_recovery import clear_submit_unknown_geoblock_403

    command_id = f"cmd-m2-geoblock-blocked-{case}"
    kwargs = {}
    if case == "raw_response":
        kwargs["raw_response_json"] = '{"orderID":"ord-real"}'
    if case == "signed_order":
        kwargs["signed_order_hash"] = "f" * 64
    _insert_unknown_side_effect(
        conn,
        command_id=command_id,
        token_id=f"tok-m2-geoblock-blocked-{case}",
        idem=("c" if case != "signed_order" else "d") * 32,
        final_event="SUBMIT_TIMEOUT_UNKNOWN",
        final_event_payload=_geoblock_403_payload(),
        **kwargs,
    )
    if case == "order_fact":
        conn.execute(
            """
            INSERT INTO venue_order_facts (
              venue_order_id, command_id, state, source, observed_at,
              local_sequence, raw_payload_hash
            ) VALUES ('ord-geoblock-blocked', ?, 'LIVE', 'REST', ?, 1, ?)
            """,
            (command_id, NOW.isoformat(), "a" * 64),
        )
    if case == "trade_fact":
        conn.execute(
            """
            INSERT INTO venue_trade_facts (
              trade_id, venue_order_id, command_id, state, filled_size,
              fill_price, source, observed_at, local_sequence, raw_payload_hash
            ) VALUES ('trade-geoblock-blocked', 'ord-geoblock-blocked', ?, 'MATCHED', '1.00',
                      '0.55', 'REST', ?, 1, ?)
            """,
            (command_id, NOW.isoformat(), "b" * 64),
        )
    conn.commit()

    with pytest.raises(ValueError, match="terminalization predicates failed"):
        clear_submit_unknown_geoblock_403(
            conn,
            command_id,
            reviewed_by="pytest",
        )


def test_submit_unknown_geoblock_terminalization_cannot_run_from_arbitrary_state(conn):
    from src.execution.command_recovery import clear_submit_unknown_geoblock_403

    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-geoblock-review",
        final_event="REVIEW_REQUIRED",
        final_event_payload={"reason": "still_requires_review"},
    )

    with pytest.raises(ValueError, match="only legal for SUBMIT_UNKNOWN_SIDE_EFFECT"):
        clear_submit_unknown_geoblock_403(
            conn,
            "cmd-m2-geoblock-review",
            reviewed_by="pytest",
        )


def test_unknown_state_side_effect_still_blocks_same_economic_intent(conn):
    from src.state.venue_command_repo import find_unknown_command_by_economic_intent

    token_id = "tok-m2-unknown-block"
    _insert_unknown_side_effect(
        conn,
        command_id="cmd-m2-unknown-block",
        token_id=token_id,
        idem="6" * 32,
        final_event="SUBMIT_UNKNOWN",
    )

    unresolved = find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id=token_id,
        side="BUY",
        price=0.55,
        size=18.19,
    )
    assert unresolved is not None
    assert unresolved["state"] == "UNKNOWN"


def test_reconciliation_finding_no_order_within_window_permits_safe_replay(conn):
    from src.execution.command_recovery import reconcile_unresolved_commands
    from src.state.venue_command_repo import find_unknown_command_by_economic_intent

    old = NOW - timedelta(minutes=30)
    _insert_unknown_side_effect(conn, idem="4" * 32, created_at=old)
    client = MagicMock()
    client.find_order_by_idempotency_key.return_value = None

    summary = reconcile_unresolved_commands(conn, client)

    cmd = _command(conn)
    assert summary["advanced"] == 1
    assert cmd["state"] == "SUBMIT_REJECTED"
    events = conn.execute(
        "SELECT event_type, payload_json FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        (cmd["command_id"],),
    ).fetchall()
    assert [row["event_type"] for row in events][-1] == "SUBMIT_REJECTED"
    payload = json.loads(events[-1]["payload_json"])
    assert payload["reason"] == "safe_replay_permitted_no_order_found"
    assert payload["safe_replay_permitted"] is True
    assert payload["previous_unknown_command_id"] == cmd["command_id"]
    assert payload["idempotency_key"] == "4" * 32
    assert find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id="tok-m2",
        side="BUY",
        price=0.55,
        size=18.19,
    ) is None


def test_unknown_without_idempotency_lookup_uses_authenticated_absence_before_safe_replay(conn):
    from src.execution.command_recovery import reconcile_unresolved_commands
    from src.state.venue_command_repo import find_unknown_command_by_economic_intent

    old = NOW - timedelta(minutes=30)
    _insert_unknown_side_effect(conn, idem="4" * 32, created_at=old)

    class AuthenticatedReadClient:
        venue_reads_are_complete = True

        def get_open_orders(self):
            return [{"id": "unrelated-open", "asset_id": "other-token", "status": "LIVE"}]

        def get_trades(self):
            return [{"id": "old-trade", "asset_id": "tok-m2", "match_time": "1"}]

    summary = reconcile_unresolved_commands(conn, AuthenticatedReadClient())

    cmd = _command(conn)
    assert summary["advanced"] == 1
    assert summary["errors"] == 0
    assert cmd["state"] == "SUBMIT_REJECTED"
    events = conn.execute(
        "SELECT event_type, payload_json FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        (cmd["command_id"],),
    ).fetchall()
    payload = json.loads(events[-1]["payload_json"])
    assert payload["reason"] == "safe_replay_permitted_no_order_found"
    assert payload["lookup_method"] == "authenticated_venue_absence"
    assert payload["venue_absence_proof"]["open_orders_query_complete"] is True
    assert payload["venue_absence_proof"]["trades_query_complete"] is True
    assert payload["venue_absence_proof"]["matching_open_order_count"] == 0
    assert payload["venue_absence_proof"]["matching_trade_count"] == 0
    assert find_unknown_command_by_economic_intent(
        conn,
        intent_kind="ENTRY",
        token_id="tok-m2",
        side="BUY",
        price=0.55,
        size=18.19,
    ) is None


def test_unknown_without_idempotency_lookup_requires_complete_venue_absence_reads(conn):
    from src.execution.command_recovery import reconcile_unresolved_commands

    old = NOW - timedelta(minutes=30)
    _insert_unknown_side_effect(conn, idem="6" * 32, created_at=old)

    class SingleCallClient:
        def get_open_orders(self):
            return []

        def get_trades(self):
            return []

    summary = reconcile_unresolved_commands(conn, SingleCallClient())

    cmd = _command(conn)
    assert summary["advanced"] == 0
    assert summary["errors"] == 1
    assert cmd["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT"
    events = conn.execute(
        "SELECT event_type FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        (cmd["command_id"],),
    ).fetchall()
    assert "SUBMIT_REJECTED" not in [row["event_type"] for row in events]


def test_unknown_without_idempotency_lookup_does_not_release_when_venue_reads_match(conn):
    from src.execution.command_recovery import reconcile_unresolved_commands

    old = NOW - timedelta(minutes=30)
    _insert_unknown_side_effect(conn, idem="5" * 32, created_at=old)

    class MatchingTradeClient:
        venue_reads_are_complete = True

        def get_open_orders(self):
            return []

        def get_trades(self):
            return [
                {
                    "id": "trade-match",
                    "asset_id": "tok-m2",
                    "price": "0.55",
                    "size": "18.19",
                    "side": "BUY",
                    "match_time": str((NOW + timedelta(seconds=1)).timestamp()),
                }
            ]

    summary = reconcile_unresolved_commands(conn, MatchingTradeClient())

    cmd = _command(conn)
    assert summary["advanced"] == 0
    assert summary["errors"] == 1
    assert cmd["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT"
    events = conn.execute(
        "SELECT event_type FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
        (cmd["command_id"],),
    ).fetchall()
    event_types = [row["event_type"] for row in events]
    assert "PARTIAL_FILL_OBSERVED" not in event_types
    assert "SUBMIT_REJECTED" not in event_types
