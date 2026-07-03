# Lifecycle: created=2026-04-26; last_reviewed=2026-05-21; last_reused=2026-07-01
# Purpose: Lock executor command split phase ordering and ACK invariants.
# Reuse: Run when venue command persistence, live order submission, or ACK handling changes.
# Created: 2026-04-26
# Last reused/audited: 2026-07-02
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md §P1.S3
#                  + docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md
#                  + docs/operations/task_2026-05-21_live_side_effect_risk_boundaries/task.md P1-4 side-effect boundary.
"""INV-30 relationship tests: executor split build/persist/submit/ack.

Each test names the relationship it locks, not just the function.
See implementation_plan.md §P1.S3 for the full phase-order spec.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch, call
import json

import pytest

_NOW = datetime(2026, 4, 27, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_conn():
    """In-memory DB with full schema (venue_commands + venue_command_events)."""
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _cutover_guard_live_enabled(monkeypatch):
    """This file tests command-journal ordering, not cutover gating."""
    from src.state.collateral_ledger import CollateralLedger, CollateralSnapshot
    from src.state.db import apply_architecture_kernel_schema

    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_sell_preflight", lambda *args, **kwargs: None)
    # Pre-submit identity binding resolves the canonical funder address via the
    # operator Keychain (OPENCLAW_HOME) — absent in CI. Not this file's subject;
    # same hermetic stub as tests/test_unknown_side_effect.py's conn fixture.
    monkeypatch.setattr(
        "src.data.polymarket_client.resolve_funder_address",
        lambda: "0x0000000000000000000000000000000000000abc",
    )
    world_conn = sqlite3.connect(":memory:")
    world_conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(world_conn)
    monkeypatch.setattr("src.state.db.get_world_connection", lambda: world_conn)
    monkeypatch.setattr(
        "src.execution.executor._assert_collateral_allows_sell",
        lambda *args, **kwargs: {"component": "collateral_ledger", "allowed": True},
    )
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_sell", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.data.polymarket_client.resolve_funder_address",
        lambda: "0x0000000000000000000000000000000000000abc",
    )

    def _seed_submit_collateral(conn: sqlite3.Connection, **_kwargs) -> dict:
        ctf_units = 1_000_000_000
        ctf_tokens = {
            "tok-" + "1" * 36: ctf_units,
            "tok-" + "7" * 36: ctf_units,
            "tok-exit-idem" + "0" * 27: ctf_units,
        }
        CollateralLedger(conn).set_snapshot(
            CollateralSnapshot(
                pusd_balance_micro=1_000_000_000,
                pusd_allowance_micro=1_000_000_000,
                usdc_e_legacy_balance_micro=0,
                ctf_token_balances=ctf_tokens,
                ctf_token_allowances=ctf_tokens,
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
            gamma_market_id="gamma-test",
            event_id="event-test",
            event_slug="event-test",
            condition_id="condition-test",
            question_id="question-test",
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
            captured_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


def test_pre_submit_envelope_uses_canonical_funder_identity(mem_conn, monkeypatch):
    from src.execution.executor import _build_pre_submit_envelope

    token_id = "token-canonical-funder"
    snapshot_id = _ensure_snapshot(mem_conn, token_id=token_id)
    monkeypatch.delenv("POLYMARKET_FUNDER_ADDRESS", raising=False)
    monkeypatch.delenv("POLYMARKET_PROXY_ADDRESS", raising=False)
    monkeypatch.setattr(
        "src.data.polymarket_client.resolve_funder_address",
        lambda: "0xcanonicalfunder",
    )

    envelope = _build_pre_submit_envelope(
        mem_conn,
        command_id="cmd-canonical-funder",
        snapshot_id=snapshot_id,
        token_id=token_id,
        side="BUY",
        price=0.56,
        size=10.0,
        order_type="GTC",
        post_only=False,
        captured_at=_NOW.isoformat(),
    )

    assert envelope is not None
    assert envelope.funder_address == "0xcanonicalfunder"


def test_pre_submit_envelope_fails_closed_without_canonical_funder(mem_conn, monkeypatch):
    from src.execution.executor import (
        PreSubmitIdentityBindingError,
        _build_pre_submit_envelope,
    )

    token_id = "token-missing-funder"
    snapshot_id = _ensure_snapshot(mem_conn, token_id=token_id)
    monkeypatch.setattr(
        "src.data.polymarket_client.resolve_funder_address",
        lambda: (_ for _ in ()).throw(RuntimeError("missing keychain funder")),
    )

    with pytest.raises(PreSubmitIdentityBindingError, match="missing keychain funder"):
        _build_pre_submit_envelope(
            mem_conn,
            command_id="cmd-missing-funder",
            snapshot_id=snapshot_id,
            token_id=token_id,
            side="BUY",
            price=0.56,
            size=10.0,
            order_type="GTC",
            post_only=False,
            captured_at=_NOW.isoformat(),
        )


def _ensure_envelope(
    conn,
    *,
    token_id: str,
    envelope_id: str | None = None,
    side: str = "BUY",
    price: float | Decimal = Decimal("0.50"),
    size: float | Decimal = Decimal("10"),
) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    price_dec = Decimal(str(price))
    size_dec = Decimal(str(size))
    envelope_id = envelope_id or f"env-{token_id}-{side}-{price_dec}-{size_dec}"
    if conn.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone():
        return envelope_id
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            side=side,
            price=price_dec,
            size=size_dec,
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
            signed_order=None,
            signed_order_hash=None,
            raw_request_hash="e" * 64,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=_NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    return envelope_id


def _submit_requested_payload(command_id: str) -> dict:
    return {
        "execution_capability": {
            "allowed": True,
            "command_id": command_id,
            "capability_id": f"cap-{command_id}",
            "components": [
                {
                    "component": "entry_actionable_certificate",
                    "allowed": True,
                    "reason": "allowed",
                    "details": {"certificate_hash": "a" * 64},
                },
                {
                    "component": "entry_economics",
                    "allowed": True,
                    "reason": "allowed",
                    "details": {
                        "q_live": 0.99,
                        "q_lcb_5pct": 0.95,
                        "expected_edge": 0.07,
                        "min_entry_price": 0.10,
                        "limit_price": 0.55,
                        "submit_edge": 0.40,
                        "expected_profit_usd": 7.276,
                        "min_expected_profit_usd": 1.0,
                        "submit_edge_density": 0.7272727272727273,
                        "min_submit_edge_density": 0.05,
                        "shares": 18.19,
                        "qkernel_side": "YES",
                    },
                },
            ],
        }
    }


def test_existing_command_orderresult_carries_durable_command_id(mem_conn):
    from src.execution.command_bus import CommandState, IdempotencyKey, IntentKind, VenueCommand
    from src.execution.executor import _orderresult_from_existing

    idem = IdempotencyKey.from_external("a" * 32)
    existing = VenueCommand(
        command_id="cmd-existing-123",
        position_id="pos-existing",
        decision_id="dec-existing",
        idempotency_key=idem,
        intent_kind=IntentKind.ENTRY,
        market_id="mkt-test",
        token_id="tok-existing",
        side="BUY",
        size=10.0,
        price=0.42,
        state=CommandState.ACKED,
        venue_order_id="ord-existing",
    )

    result = _orderresult_from_existing(
        mem_conn,
        existing,
        trade_id="trade-existing",
        limit_price=0.42,
        shares=10.0,
        idem_value=idem.value,
        intent_id="intent-existing",
        order_role="entry",
    )

    assert result.command_id == "cmd-existing-123"
    assert result.command_state == "ACKED"
    assert result.idempotency_key == idem.value


def test_economic_unknown_orderresult_carries_durable_command_id(mem_conn):
    from src.execution.command_bus import CommandState, IdempotencyKey, IntentKind, VenueCommand
    from src.execution.executor import _orderresult_from_economic_unknown

    idem = IdempotencyKey.from_external("b" * 32)
    existing = VenueCommand(
        command_id="cmd-unknown-123",
        position_id="pos-existing",
        decision_id="dec-existing",
        idempotency_key=idem,
        intent_kind=IntentKind.ENTRY,
        market_id="mkt-test",
        token_id="tok-existing",
        side="BUY",
        size=10.0,
        price=0.42,
        state=CommandState.SUBMIT_UNKNOWN_SIDE_EFFECT,
    )

    result = _orderresult_from_economic_unknown(
        existing,
        trade_id="trade-existing",
        limit_price=0.42,
        shares=10.0,
        idem_value=idem.value,
        intent_id="intent-existing",
        order_role="entry",
    )

    assert result.status == "unknown_side_effect"
    assert result.command_id == "cmd-unknown-123"
    assert result.command_state == "SUBMIT_UNKNOWN_SIDE_EFFECT"


_DEFAULT_CONTEXT = object()


def _decision_source_context(**overrides):
    from src.contracts.execution_intent import DecisionSourceContext

    fields = {
        "source_id": "tigge",
        "model_family": "ecmwf_ifs025",
        "forecast_issue_time": "2026-04-26T00:00:00+00:00",
        "forecast_valid_time": "2026-04-26T06:00:00+00:00",
        "forecast_fetch_time": "2026-04-26T01:00:00+00:00",
        "forecast_available_at": "2026-04-26T00:30:00+00:00",
        "raw_payload_hash": "a" * 64,
        "degradation_level": "OK",
        "forecast_source_role": "entry_primary",
        "authority_tier": "FORECAST",
        "decision_time": "2026-04-26T02:00:00+00:00",
        "decision_time_status": "OK",
    }
    fields.update(overrides)
    return DecisionSourceContext(**fields)


def _make_entry_intent(
    conn=None,
    limit_price: float = 0.55,
    token_id: str = "tok-" + "0" * 36,
    decision_source_context=_DEFAULT_CONTEXT,
    submit_order_type: str = "GTC",
    post_only: bool = True,
    actionable_certificate_hash: str | None = None,
) -> object:
    """Build a minimal ExecutionIntent that passes the ExecutionPrice guard."""
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts import Direction
    from src.contracts.slippage_bps import SlippageBps

    snapshot_id = f"snap-{token_id}"
    if conn is not None:
        _ensure_snapshot(conn, token_id=token_id, snapshot_id=snapshot_id)
    if decision_source_context is _DEFAULT_CONTEXT:
        decision_source_context = _decision_source_context()
    auto_insert_actionable_certificate = actionable_certificate_hash is None and conn is not None
    if auto_insert_actionable_certificate:
        import hashlib

        actionable_certificate_hash = hashlib.sha256(
            f"actionable:{token_id}:{limit_price}:{snapshot_id}".encode()
        ).hexdigest()
    intent = ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="mkt-test-001",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.05,
        submit_order_type=submit_order_type,
        post_only=post_only,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
        decision_source_context=decision_source_context,
        q_live=0.99,
        q_lcb_5pct=0.95,
        expected_edge=0.07,
        min_entry_price=0.10,
        min_expected_profit_usd=1.0,
        min_submit_edge_density=0.05,
        selection_authority_applied="qkernel_spine",
        actionable_certificate_hash=actionable_certificate_hash,
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "route_id": "DIRECT_YES:bin-test@proof",
            "route_type": "direct",
            "candidate_id": "YES:bin-test:DIRECT_YES:bin-test@proof",
            "bin_id": "bin-test",
            "side": "YES",
            "payoff_q_point": 0.99,
            "payoff_q_lcb": 0.95,
            "cost": limit_price,
            "edge_lcb": 0.95 - limit_price,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 10.0,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.95,
        },
    )
    if auto_insert_actionable_certificate and conn is not None and actionable_certificate_hash:
        _insert_actionable_certificate_for_intent(
            conn,
            intent,
            certificate_hash=actionable_certificate_hash,
        )
    return intent


def _insert_actionable_certificate_for_intent(
    conn: sqlite3.Connection,
    intent,
    *,
    certificate_hash: str,
) -> None:
    payload = {
        "event_id": "event-entry-capability",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": "forecast-snap-entry-capability",
        "family_id": "family-entry-capability",
        "candidate_id": "candidate-entry-capability",
        "condition_id": "condition-test",
        "token_id": intent.token_id,
        "direction": "buy_yes",
        "strategy_key": "center_buy",
        "executable_snapshot_id": intent.executable_snapshot_id,
        "q_live": intent.q_live,
        "q_lcb_5pct": intent.q_lcb_5pct,
        "c_fee_adjusted": intent.limit_price,
        "c_cost_95pct": intent.limit_price,
        "p_fill_lcb": 0.5,
        "trade_score": 0.10,
        "action_score": 0.10,
        "min_entry_price": intent.min_entry_price,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": dict(intent.qkernel_execution_economics),
        "fdr_family_id": "fdr-family-entry-capability",
        "kelly_decision_id": "kelly-entry-capability",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-entry-capability",
        "live_cap_usage_id": "cap-entry-capability",
        "final_intent_id": "intent-entry-capability",
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": True,
        "submitted": False,
    }
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = "a" * 64
    conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_id, certificate_type, schema_version, canonicalization_version,
            semantic_key, claim_type, mode, decision_time, authority_id,
            authority_version, algorithm_id, algorithm_version, payload_json,
            payload_hash, certificate_hash, verifier_status, created_at
        ) VALUES (?, 'ActionableTradeCertificate', 1, 'test-v1',
                  ?, 'actionable_trade', 'LIVE', ?, 'test-authority',
                  'v1', 'test-algorithm', 'v1', ?, ?, ?, 'VERIFIED', ?)
        """,
        (
            f"ActionableTradeCertificate:{certificate_hash[:24]}",
            f"actionable:event-entry-capability:{intent.token_id}",
            _NOW.isoformat(),
            payload_json,
            payload_hash,
            certificate_hash,
            _NOW.isoformat(),
        ),
    )


def _make_exit_intent(
    conn=None,
    trade_id: str = "trd-exit-001",
    token_id: str = "tok-" + "1" * 36,
    shares: float = 10.0,
    current_price: float = 0.55,
) -> object:
    """Build a minimal ExitOrderIntent."""
    from src.execution.executor import create_exit_order_intent

    snapshot_id = f"snap-{token_id}"
    if conn is not None:
        _ensure_snapshot(conn, token_id=token_id, snapshot_id=snapshot_id)
    return create_exit_order_intent(
        trade_id=trade_id,
        token_id=token_id,
        shares=shares,
        current_price=current_price,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
    )


def _capture_bound_submission_envelope(mock_client):
    bound = {}
    mock_client.bind_submission_envelope.side_effect = lambda envelope: bound.__setitem__("envelope", envelope)
    return bound


def _final_submit_result(
    bound: dict,
    *,
    order_id: str | None = None,
    status: str = "LIVE",
    success: bool | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    raw_extra: dict | None = None,
) -> dict:
    envelope = bound.get("envelope")
    if envelope is None:
        raise AssertionError("test client did not receive a bound submission envelope")
    raw_payload = {"status": status}
    if order_id is not None:
        raw_payload["orderID"] = order_id
    if success is not None:
        raw_payload["success"] = success
    if raw_extra:
        raw_payload.update(raw_extra)
    changes = {
        "raw_response_json": json.dumps(raw_payload, sort_keys=True, separators=(",", ":")),
        "order_id": order_id,
    }
    if raw_extra and raw_extra.get("transactionsHashes"):
        changes["transaction_hashes"] = tuple(raw_extra["transactionsHashes"])
    if error_code is not None:
        changes["error_code"] = error_code
        changes["error_message"] = error_message or ""
    final = envelope.with_updates(**changes)
    result = {
        "status": status,
        "_venue_submission_envelope": final.to_dict(),
    }
    if order_id is not None:
        result["orderID"] = order_id
    if success is not None:
        result["success"] = success
    if raw_extra:
        result.update(raw_extra)
    if error_code is not None:
        result["errorCode"] = error_code
        result["errorMessage"] = error_message or ""
    return result


def _allow_entry_submit_until_client(monkeypatch):
    import src.execution.executor as executor_module

    monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
    monkeypatch.setattr(
        executor_module,
        "_assert_ws_gap_allows_submit",
        lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
    )
    monkeypatch.setattr(
        executor_module,
        "_entry_actionable_certificate_payload_and_component",
        lambda *args, **kwargs: (
            {"component": "entry_actionable_certificate", "allowed": True, "reason": "test"},
            {"actionable_certificate_hash": "hash-test"},
        ),
    )
    monkeypatch.setattr(
        executor_module,
        "_entry_control_pause_component",
        lambda *args, **kwargs: {
            "component": "entries_pause_control_override",
            "allowed": True,
            "reason": "not_paused",
        },
    )
    monkeypatch.setattr(
        executor_module,
        "_entry_duplicate_same_token_component",
        lambda *args, **kwargs: {
            "component": "entry_duplicate_same_token",
            "allowed": True,
            "reason": "no_duplicate",
        },
    )
    monkeypatch.setattr(
        executor_module,
        "_entry_same_token_cooldown_component",
        lambda *args, **kwargs: {
            "component": "entry_same_token_cooldown",
            "allowed": True,
            "reason": "not_in_cooldown",
        },
    )
    monkeypatch.setattr(
        executor_module,
        "_entry_decision_source_component",
        lambda *args, **kwargs: {
            "component": "entry_decision_source",
            "allowed": True,
            "reason": "valid",
        },
    )


# ---------------------------------------------------------------------------
# TestLiveOrderCommandSplit — entry path (_live_order / IntentKind.ENTRY)
# ---------------------------------------------------------------------------

class TestLiveOrderCommandSplit:
    """INV-30: _live_order must persist before it submits."""

    def test_persist_precedes_submit(self, mem_conn):
        """insert_command must run before place_limit_order.

        Uses a call-order spy: both insert_command and place_limit_order are
        wrapped; the spy records the order of calls. Assert insert_command
        index < place_limit_order index.
        """
        from src.execution.executor import _live_order

        call_log: list[str] = []

        real_insert = None
        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def spy_insert(*args, **kwargs):
            call_log.append("insert_command")
            return _real_insert(*args, **kwargs)

        intent = _make_entry_intent(mem_conn)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=spy_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)

            def spy_place(**kwargs):
                call_log.append("place_limit_order")
                return _final_submit_result(bound, order_id="ord-test-001")

            mock_inst.place_limit_order.side_effect = spy_place

            _live_order(
                trade_id="trd-001",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-001",
            )

        assert "insert_command" in call_log, "insert_command must have been called"
        assert "place_limit_order" in call_log, "place_limit_order must have been called"
        assert call_log.index("insert_command") < call_log.index("place_limit_order"), (
            f"INV-30: insert_command must precede place_limit_order; call order was {call_log}"
        )
        bound_envelope = mock_inst.bind_submission_envelope.call_args.args[0]
        assert bound_envelope.condition_id == "condition-test"
        assert bound_envelope.selected_outcome_token_id == intent.token_id

    def test_entry_submit_requested_persists_execution_capability_proof(self, mem_conn, monkeypatch):
        """Entry SUBMIT_REQUESTED carries one pre-submit capability proof."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )
        monkeypatch.setattr(
            executor_module,
            "_entry_control_pause_component",
            lambda *args, **kwargs: {
                "component": "entries_pause_control_override",
                "allowed": True,
                "reason": "not_paused",
            },
        )

        certificate_hash = "c" * 64
        intent = _make_entry_intent(
            mem_conn,
            actionable_certificate_hash=certificate_hash,
        )
        _insert_actionable_certificate_for_intent(
            mem_conn,
            intent,
            certificate_hash=certificate_hash,
        )

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-entry-capability")
            )

            result = _live_order(
                trade_id="trd-entry-capability",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-capability",
            )

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        row = mem_conn.execute(
            "SELECT command_id FROM venue_commands WHERE position_id = ?",
            ("trd-entry-capability",),
        ).fetchone()
        events = list_events(mem_conn, row["command_id"])
        requested = [event for event in events if event["event_type"] == "SUBMIT_REQUESTED"]
        payload = json.loads(requested[0]["payload_json"])
        capability = payload["execution_capability"]

        assert capability["action"] == "ENTRY"
        assert capability["intent_kind"] == "ENTRY"
        assert capability["allowed"] is True
        assert len(capability["capability_id"]) == 32
        assert capability["command_id"] == row["command_id"]
        assert capability["executable_snapshot_id"] == intent.executable_snapshot_id
        components_by_name = {component["component"]: component for component in capability["components"]}
        assert {component["component"] for component in capability["components"]} >= {
            "cutover_guard",
            "risk_allocator",
            "order_type_selection",
            "heartbeat_supervisor",
            "ws_gap_guard",
            "collateral_ledger",
            "decision_source_integrity",
            "entry_economics",
            "entry_actionable_certificate",
            "executable_snapshot_gate",
        }
        assert components_by_name["entry_economics"]["allowed"] is True
        assert components_by_name["entry_economics"]["details"]["min_entry_price"] == 0.10
        assert components_by_name["entry_economics"]["details"]["live_min_entry_price"] == 0.02
        assert components_by_name["entry_actionable_certificate"]["allowed"] is True
        assert (
            components_by_name["entry_actionable_certificate"]["details"]["certificate_hash"]
            == certificate_hash
        )
        assert components_by_name["decision_source_integrity"]["allowed"] is True
        assert components_by_name["decision_source_integrity"]["details"]["source_id"] == "tigge"
        assert components_by_name["decision_source_integrity"]["details"]["degradation_level"] == "OK"

    def test_entry_pre_submit_integrity_does_not_require_post_submit_audit_fields(
        self,
        mem_conn,
        monkeypatch,
    ):
        """Pre-submit source proof must not require fields only produced by submit/ack."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        context = _decision_source_context(
            decision_time="2026-05-20T13:30:40+00:00",
            forecast_issue_time="2026-05-20T00:00:00+00:00",
            forecast_valid_time="2026-05-22T00:00:00+00:00",
            forecast_fetch_time="2026-05-20T13:26:03+00:00",
            forecast_available_at="2026-05-20T00:00:00+00:00",
            polymarket_end_anchor_source="gamma_explicit",
            first_member_observed_time="2026-05-20T13:20:00+00:00",
            run_complete_time="2026-05-20T13:26:00+00:00",
        )
        assert {
            "missing_observation_time",
            "missing_observation_available_at",
            "missing_zeus_submit_intent_time",
            "missing_venue_ack_time",
        }.issubset(set(context.integrity_errors()))
        intent = _make_entry_intent(mem_conn, decision_source_context=context)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-entry-pre-submit-audit")
            )

            result = _live_order(
                trade_id="trd-entry-pre-submit-audit",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-pre-submit-audit",
            )

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        assert mock_inst.place_limit_order.called

    def test_entry_same_token_open_position_blocks_before_command_persistence(
        self,
        mem_conn,
        monkeypatch,
    ):
        """Executor must not submit a second live ENTRY for an already-open token."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order

        token_id = "tok-duplicate-entry"
        _ensure_snapshot(mem_conn, token_id=token_id)
        mem_conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, target_date,
                bin_label, direction, unit, size_usd, shares, cost_basis_usd,
                entry_price, p_posterior, strategy_key, edge_source,
                discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at,
                temperature_metric
            ) VALUES (
                'pos-open-existing', 'active', 'trade-existing', 'mkt-test-001',
                'Shenzhen', '2026-06-19', '34C+', 'buy_no', 'C',
                11.84, 16.0, 11.84, 0.74, 0.30, 'center_buy',
                'replacement', 'live', 'synced', '', ?,
                'condition-test', 'order-existing', 'filled',
                '2026-06-17T16:22:21+00:00', 'high'
            )
            """,
            (token_id,),
        )
        mem_conn.commit()

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(mem_conn, token_id=token_id)

        with patch("src.state.venue_command_repo.insert_command") as insert_command, patch(
            "src.data.polymarket_client.PolymarketClient"
        ) as MockClient:
            result = _live_order(
                trade_id="trd-duplicate-attempt",
                intent=intent,
                shares=15.5,
                conn=mem_conn,
                decision_id="dec-duplicate-attempt",
            )

        assert result.status == "rejected"
        assert result.reason == "duplicate_entry_same_token:open_position_same_token"
        assert result.command_state == "REJECTED"
        insert_command.assert_not_called()
        MockClient.assert_not_called()
        assert mem_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0

    def test_entry_same_token_filled_command_blocks_before_position_bridge(
        self,
        mem_conn,
        monkeypatch,
    ):
        """A FILLED ENTRY command without a terminal position row is exposure."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order

        token_id = "tok-filled-no-position-yet"
        snapshot_id = _ensure_snapshot(mem_conn, token_id=token_id)
        envelope_id = _ensure_envelope(
            mem_conn,
            token_id=token_id,
            price=Decimal("0.74"),
            size=Decimal("16.0"),
        )
        mem_conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, snapshot_id, envelope_id, position_id, decision_id,
                idempotency_key, intent_kind, market_id, token_id, side, size,
                price, venue_order_id, state, last_event_id, created_at,
                updated_at, review_required_reason
            ) VALUES (
                'cmd-filled-existing', ?, ?, 'pos-filled-existing',
                'dec-filled-existing', 'idem-filled-existing', 'ENTRY',
                'mkt-test-001', ?, 'BUY', 16.0, 0.74, 'order-filled',
                'FILLED', NULL, '2026-06-17T16:20:27+00:00',
                '2026-06-17T16:20:58+00:00', NULL
            )
            """,
            (snapshot_id, envelope_id, token_id),
        )
        mem_conn.commit()

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(mem_conn, token_id=token_id)

        with patch("src.state.venue_command_repo.insert_command") as insert_command, patch(
            "src.data.polymarket_client.PolymarketClient"
        ) as MockClient:
            result = _live_order(
                trade_id="trd-filled-duplicate-attempt",
                intent=intent,
                shares=15.5,
                conn=mem_conn,
                decision_id="dec-filled-duplicate-attempt",
            )

        assert result.status == "rejected"
        assert result.reason == "duplicate_entry_same_token:open_or_filled_entry_command_same_token"
        insert_command.assert_not_called()
        MockClient.assert_not_called()
        assert mem_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 1

    def test_entry_control_pause_blocks_before_command_persistence(
        self,
        mem_conn,
        monkeypatch,
    ):
        """A durable entries pause must stop queued EDLI submits at executor."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.db import DEFAULT_CONTROL_OVERRIDE_PRECEDENCE, get_world_connection, upsert_control_override

        world_conn = get_world_connection()
        upsert_control_override(
            world_conn,
            override_id="control_plane:global:entries_paused",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by="control_plane",
            issued_at="2026-06-17T16:27:51+00:00",
            reason="manual_pause:test_duplicate_entry",
            effective_until=None,
            precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
        )
        world_conn.commit()

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(mem_conn, token_id="tok-paused-entry")

        with patch("src.state.venue_command_repo.insert_command") as insert_command, patch(
            "src.data.polymarket_client.PolymarketClient"
        ) as MockClient:
            result = _live_order(
                trade_id="trd-paused-attempt",
                intent=intent,
                shares=15.5,
                conn=mem_conn,
                decision_id="dec-paused-attempt",
            )

        assert result.status == "rejected"
        assert result.reason == "entries_paused:manual_pause:test_duplicate_entry"
        assert result.command_state == "REJECTED"
        insert_command.assert_not_called()
        MockClient.assert_not_called()
        assert mem_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0

    def test_entry_control_pause_reads_world_control_authority(
        self,
        mem_conn,
        monkeypatch,
    ):
        """Trade connections read pause authority from world control state."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order

        from src.state.db import (
            DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
            apply_architecture_kernel_schema,
            upsert_control_override,
        )

        world_conn = sqlite3.connect(":memory:")
        world_conn.row_factory = sqlite3.Row
        apply_architecture_kernel_schema(world_conn)
        upsert_control_override(
            world_conn,
            override_id="control_plane:global:entries_paused",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by="control_plane",
            issued_at="2026-06-17T16:27:51+00:00",
            reason="manual_pause:world_control",
            effective_until=None,
            precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
        )
        world_conn.commit()
        monkeypatch.setattr("src.state.db.get_world_connection", lambda: world_conn)

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(mem_conn, token_id="tok-attached-pause")

        with patch("src.state.venue_command_repo.insert_command") as insert_command, patch(
            "src.data.polymarket_client.PolymarketClient"
        ) as MockClient:
            result = _live_order(
                trade_id="trd-attached-pause",
                intent=intent,
                shares=15.5,
                conn=mem_conn,
                decision_id="dec-attached-pause",
            )

        assert result.status == "rejected"
        assert result.reason == "entries_paused:manual_pause:world_control"
        insert_command.assert_not_called()
        MockClient.assert_not_called()

    def test_entry_taker_order_blocks_before_command_persistence(
        self,
        mem_conn,
        monkeypatch,
    ):
        """Live ENTRY taker orders require explicit quality proof."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "FOK")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(
            mem_conn,
            token_id="tok-taker-entry",
            submit_order_type="FOK",
            post_only=False,
        )

        with patch("src.state.venue_command_repo.insert_command") as insert_command, patch(
            "src.data.polymarket_client.PolymarketClient"
        ) as MockClient:
            result = _live_order(
                trade_id="trd-taker-attempt",
                intent=intent,
                shares=13.5,
                conn=mem_conn,
                decision_id="dec-taker-attempt",
            )

        assert result.status == "rejected"
        assert result.reason == "entry_taker_quality:missing_taker_quality_proof"
        assert result.command_state == "REJECTED"
        insert_command.assert_not_called()
        MockClient.assert_not_called()
        assert mem_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0

    def test_entry_taker_order_with_quality_proof_can_submit(
        self,
        mem_conn,
        monkeypatch,
    ):
        """A high-confidence, fee-adjusted taker edge may submit."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "FOK")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(
            mem_conn,
            limit_price=0.50,
            token_id="tok-quality-taker-entry",
            submit_order_type="FOK",
            post_only=False,
        )
        object.__setattr__(
            intent,
            "taker_quality_proof",
            {
                "passed": True,
                "taker_fee_adjusted_edge": "0.08",
                "taker_expected_profit_usd": "0.50",
                "maker_expected_profit_usd": "0.20",
                "incremental_expected_profit_usd": "0.30",
                "model_confidence": "0.72",
            },
        )

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-quality-taker")
            )

            result = _live_order(
                trade_id="trd-quality-taker",
                intent=intent,
                shares=13.5,
                conn=mem_conn,
                decision_id="dec-quality-taker",
            )

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        assert result.order_id == "ord-quality-taker"
        assert mock_inst.place_limit_order.called

    def test_entry_same_token_recent_terminal_command_cools_down_before_persistence(
        self,
        mem_conn,
        monkeypatch,
    ):
        """A top-ranked token cannot be retried immediately after any entry command."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order

        token_id = "tok-cooldown-entry"
        snapshot_id = _ensure_snapshot(mem_conn, token_id=token_id)
        envelope_id = _ensure_envelope(
            mem_conn,
            token_id=token_id,
            price=Decimal("0.40"),
            size=Decimal("10.0"),
        )
        mem_conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, snapshot_id, envelope_id, position_id, decision_id,
                idempotency_key, intent_kind, market_id, token_id, side, size,
                price, venue_order_id, state, last_event_id, created_at,
                updated_at, review_required_reason
            ) VALUES (
                'cmd-recent-cancelled', ?, ?, 'pos-recent-cancelled',
                'dec-recent-cancelled', 'idem-recent-cancelled', 'ENTRY',
                'mkt-test-001', ?, 'BUY', 10.0, 0.40, 'order-cancelled',
                'CANCELLED', NULL, ?, ?, NULL
            )
            """,
            (
                snapshot_id,
                envelope_id,
                token_id,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        mem_conn.execute(
            """
            INSERT INTO venue_trade_facts (
                trade_id, venue_order_id, command_id, state, filled_size,
                fill_price, fee_paid_micro, tx_hash, block_number,
                confirmation_count, source, observed_at, venue_timestamp,
                local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (
                'fill-recent-cancelled', 'order-cancelled',
                'cmd-recent-cancelled', 'MATCHED', '1.0', '0.40',
                0, NULL, NULL, 0, 'FAKE_VENUE', ?, ?, 1, ?, '{}'
            )
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
                "d" * 64,
            ),
        )
        mem_conn.commit()

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        certificate_hash = "c" * 64
        intent = _make_entry_intent(
            mem_conn,
            token_id=token_id,
            actionable_certificate_hash=certificate_hash,
        )
        _insert_actionable_certificate_for_intent(
            mem_conn,
            intent,
            certificate_hash=certificate_hash,
        )
        monkeypatch.setattr(
            executor_module,
            "_entry_control_pause_component",
            lambda *args, **kwargs: {
                "component": "entries_pause_control_override",
                "allowed": True,
                "reason": "not_paused",
            },
        )

        with patch("src.state.venue_command_repo.insert_command") as insert_command, patch(
            "src.data.polymarket_client.PolymarketClient"
        ) as MockClient:
            result = _live_order(
                trade_id="trd-cooldown-attempt",
                intent=intent,
                shares=11.0,
                conn=mem_conn,
                decision_id="dec-cooldown-attempt",
            )

        assert result.status == "rejected"
        assert result.reason == "entry_cooldown:same_token_entry_cooling_down"
        assert result.command_state == "REJECTED"
        insert_command.assert_not_called()
        MockClient.assert_not_called()
        assert mem_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 1

    def test_entry_same_token_terminal_no_fill_redecision_cools_down_before_repost(
        self,
        mem_conn,
        monkeypatch,
    ):
        """A proven zero-fill terminal ENTRY releases exposure but not cooldown."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order

        token_id = "tok-terminal-nofill-repost"
        snapshot_id = _ensure_snapshot(mem_conn, token_id=token_id)
        envelope_id = _ensure_envelope(
            mem_conn,
            token_id=token_id,
            price=Decimal("0.40"),
            size=Decimal("10.0"),
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        mem_conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, snapshot_id, envelope_id, position_id, decision_id,
                idempotency_key, intent_kind, market_id, token_id, side, size,
                price, venue_order_id, state, last_event_id, created_at,
                updated_at, review_required_reason
            ) VALUES (
                'cmd-recent-terminal-nofill', ?, ?, 'pos-recent-terminal-nofill',
                'dec-recent-terminal-nofill', 'idem-recent-terminal-nofill', 'ENTRY',
                'mkt-test-001', ?, 'BUY', 10.0, 0.40, 'order-terminal-nofill',
                'CANCELLED', NULL, ?, ?, NULL
            )
            """,
            (
                snapshot_id,
                envelope_id,
                token_id,
                now_iso,
                now_iso,
            ),
        )
        mem_conn.execute(
            """
            INSERT INTO venue_order_facts (
                venue_order_id, command_id, state, remaining_size, matched_size,
                source, observed_at, venue_timestamp, local_sequence,
                raw_payload_hash, raw_payload_json
            ) VALUES (
                'order-terminal-nofill', 'cmd-recent-terminal-nofill',
                'CANCEL_CONFIRMED', '10.0', '0', 'WS_USER', ?, ?, 1, ?, '{}'
            )
            """,
            (
                now_iso,
                now_iso,
                "f" * 64,
            ),
        )
        mem_conn.commit()

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        certificate_hash = "b" * 64
        intent = _make_entry_intent(
            mem_conn,
            token_id=token_id,
            actionable_certificate_hash=certificate_hash,
        )
        _insert_actionable_certificate_for_intent(
            mem_conn,
            intent,
            certificate_hash=certificate_hash,
        )
        monkeypatch.setattr(
            executor_module,
            "_entry_control_pause_component",
            lambda *args, **kwargs: {
                "component": "entries_pause_control_override",
                "allowed": True,
                "reason": "not_paused",
            },
        )

        with patch("src.state.venue_command_repo.insert_command") as insert_command, patch(
            "src.data.polymarket_client.PolymarketClient"
        ) as MockClient:
            result = _live_order(
                trade_id="trd-terminal-nofill-repost",
                intent=intent,
                shares=11.0,
                conn=mem_conn,
                decision_id="dec-terminal-nofill-repost",
            )

        assert result.status == "rejected"
        assert result.reason == "entry_cooldown:same_token_terminal_no_fill_cooling_down"
        assert result.command_state == "REJECTED"
        insert_command.assert_not_called()
        MockClient.assert_not_called()
        assert (
            mem_conn.execute(
                """
                SELECT COUNT(*)
                  FROM venue_commands
                 WHERE token_id = ? AND intent_kind = 'ENTRY'
                """,
                (token_id,),
            ).fetchone()[0]
            == 1
        )

    def test_final_intent_legacy_envelope_ignores_pre_submit_audit_only_gaps(self):
        """FinalExecutionIntent handoff must use the same pre-submit integrity split."""
        from src.contracts.execution_intent import FinalExecutionIntent
        from src.execution.executor import _legacy_entry_intent_from_final

        context = _decision_source_context(
            decision_time="2026-05-20T13:30:40+00:00",
            forecast_issue_time="2026-05-20T00:00:00+00:00",
            forecast_valid_time="2026-05-22T00:00:00+00:00",
            forecast_fetch_time="2026-05-20T13:26:03+00:00",
            forecast_available_at="2026-05-20T00:00:00+00:00",
            polymarket_end_anchor_source="gamma_explicit",
            first_member_observed_time="2026-05-20T13:20:00+00:00",
            run_complete_time="2026-05-20T13:26:00+00:00",
        )
        final_intent = FinalExecutionIntent(
            hypothesis_id="hyp-pre-submit-audit",
            selected_token_id="tok-pre-submit-audit",
            direction="buy_yes",
            size_kind="shares",
            size_value=Decimal("5"),
            submitted_shares=Decimal("5"),
            final_limit_price=Decimal("0.25"),
            expected_fill_price_before_fee=Decimal("0.25"),
            fee_adjusted_execution_price=Decimal("0.25"),
            order_policy="marketable_limit_depth_bound",
            order_type="FOK",
            post_only=False,
            cancel_after=datetime.now(timezone.utc) + timedelta(minutes=5),
            snapshot_id="snap-pre-submit-audit",
            snapshot_hash="a" * 64,
            cost_basis_id="cost_basis:" + ("b" * 16),
            cost_basis_hash="b" * 64,
            max_slippage_bps=Decimal("200"),
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            fee_rate=Decimal("0"),
            neg_risk=False,
            event_id="event-pre-submit-audit",
            resolution_window="2026-05-22",
            correlation_key="Jeddah:2026-05-22",
            decision_source_context=context,
            q_live=0.99,
            q_lcb_5pct=0.95,
            expected_edge=0.07,
            min_entry_price=0.05,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
            qkernel_execution_economics={
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.99,
                "payoff_q_lcb": 0.95,
                "cost": 0.25,
                "edge_lcb": 0.70,
                "optimal_delta_u": 0.01,
                "false_edge_rate": 0.01,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.95,
            },
        )

        legacy_intent = _legacy_entry_intent_from_final(
            final_intent,
            market_id="condition-pre-submit-audit",
            event_id="event-pre-submit-audit",
            submitted_shares=5.0,
        )

        assert legacy_intent.decision_source_context is context
        assert legacy_intent.token_id == "tok-pre-submit-audit"
        assert legacy_intent.actionable_executable_snapshot_id == "snap-pre-submit-audit"
        assert legacy_intent.min_entry_price == pytest.approx(0.05)
        assert legacy_intent.min_expected_profit_usd == pytest.approx(0.05)
        assert legacy_intent.min_submit_edge_density == pytest.approx(0.02)

    @pytest.mark.parametrize(
        ("context", "expected_reason"),
        [
            (None, "missing_decision_source_context"),
            (
                _decision_source_context(degradation_level="DEGRADED_FORECAST_FALLBACK"),
                "invalid_decision_source_context:forecast_degraded:DEGRADED_FORECAST_FALLBACK",
            ),
            (
                _decision_source_context(forecast_source_role="monitor_fallback"),
                "invalid_decision_source_context:forecast_role_not_entry_primary:monitor_fallback",
            ),
            (
                _decision_source_context(forecast_fetch_time="2026-04-26T03:00:00+00:00"),
                "invalid_decision_source_context:forecast_fetch_after_decision",
            ),
            (
                _decision_source_context(raw_payload_hash="not-a-valid-hash"),
                "invalid_decision_source_context:invalid_raw_payload_hash",
            ),
        ],
    )
    def test_entry_rejects_missing_decision_source_context_before_command_persistence(
        self,
        mem_conn,
        context,
        expected_reason,
    ):
        """Entry source evidence must fail closed before command persistence."""
        from src.execution.executor import _live_order

        intent = _make_entry_intent(mem_conn, decision_source_context=context)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-entry-source-missing",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-source-missing",
            )

        command_count = mem_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0]
        assert result.status == "rejected"
        assert result.reason is not None
        assert result.reason.startswith("decision_source_integrity:")
        assert expected_reason in result.reason
        assert command_count == 0
        mock_inst.place_limit_order.assert_not_called()

    def test_own_connection_commits_pre_submit_rows_before_sdk_submit(self, tmp_path, monkeypatch):
        """Own-connection live entry must make command/envelope durable before SDK contact."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.db import get_connection, init_schema

        token_id = "tok-" + "2" * 36
        db_path = tmp_path / "entry-pre-submit-durable.db"
        setup_conn = get_connection(db_path)
        init_schema(setup_conn)
        intent = _make_entry_intent(setup_conn, token_id=token_id)
        setup_conn.commit()
        setup_conn.close()

        def _trade_conn():
            conn = get_connection(db_path)
            init_schema(conn)
            return conn

        # get_trade_connection_with_world (non-required variant) was deleted from
        # executor's namespace — only the _required form remains (single lane).
        monkeypatch.setattr(executor_module, "get_trade_connection_with_world_required", _trade_conn)
        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )
        monkeypatch.setattr(executor_module, "alert_trade", lambda *args, **kwargs: None)

        observed = {}

        class DurableVisibilityClient:
            def v2_preflight(self):
                observed["preflight"] = True

            def bind_submission_envelope(self, envelope):
                observed["bound_envelope"] = envelope

            def place_limit_order(self, **kwargs):
                read_conn = get_connection(db_path)
                init_schema(read_conn)
                try:
                    command_rows = read_conn.execute(
                        """
                        SELECT command_id, snapshot_id, envelope_id, state
                        FROM venue_commands
                        WHERE snapshot_id = ?
                        """,
                        (intent.executable_snapshot_id,),
                    ).fetchall()
                    envelope_count = read_conn.execute(
                        "SELECT COUNT(*) FROM venue_submission_envelopes"
                    ).fetchone()[0]
                    requested_count = read_conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM venue_command_events
                        WHERE command_id = ? AND event_type = 'SUBMIT_REQUESTED'
                        """,
                        (command_rows[0]["command_id"] if command_rows else "",),
                    ).fetchone()[0]
                finally:
                    read_conn.close()

                observed["submit_kwargs"] = kwargs
                observed["durable_command_count"] = len(command_rows)
                observed["durable_command_state"] = command_rows[0]["state"] if command_rows else None
                observed["durable_envelope_count"] = envelope_count
                observed["durable_submit_requested_count"] = requested_count
                final = observed["bound_envelope"].with_updates(
                    raw_response_json='{"orderID":"ord-entry-durable"}',
                    order_id="ord-entry-durable",
                )
                return {
                    "orderID": "ord-entry-durable",
                    "status": "LIVE",
                    "_venue_submission_envelope": final.to_dict(),
                }

        with patch("src.data.polymarket_client.PolymarketClient", return_value=DurableVisibilityClient()):
            result = _live_order(
                trade_id="trd-entry-durable",
                intent=intent,
                shares=18.19,
                decision_id="dec-entry-durable",
            )

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        assert observed["preflight"] is True
        assert observed["durable_command_count"] == 1
        assert observed["durable_command_state"] == "SUBMITTING"
        assert observed["durable_envelope_count"] == 1
        assert observed["durable_submit_requested_count"] == 1
        assert observed["submit_kwargs"]["token_id"] == token_id

    def test_entry_caller_connection_commits_submit_boundaries_before_preflight(self, tmp_path, monkeypatch):
        """Caller-owned entry conn must not hold write locks across CLOB preflight."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.db import get_connection, init_schema

        monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "100")
        token_id = "tok-" + "8" * 36
        db_path = tmp_path / "entry-caller-conn-durable.db"
        setup_conn = get_connection(db_path)
        init_schema(setup_conn)
        intent = _make_entry_intent(setup_conn, token_id=token_id)
        setup_conn.commit()
        setup_conn.close()

        submit_conn = get_connection(db_path)
        init_schema(submit_conn)
        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )
        monkeypatch.setattr(executor_module, "alert_trade", lambda *args, **kwargs: None)

        observed = {}

        class DurableVisibilityClient:
            def v2_preflight(self):
                read_conn = get_connection(db_path)
                init_schema(read_conn)
                try:
                    command_rows = read_conn.execute(
                        """
                        SELECT command_id, snapshot_id, envelope_id, state
                        FROM venue_commands
                        WHERE snapshot_id = ?
                        """,
                        (intent.executable_snapshot_id,),
                    ).fetchall()
                    envelope_count = read_conn.execute(
                        "SELECT COUNT(*) FROM venue_submission_envelopes"
                    ).fetchone()[0]
                    requested_count = read_conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM venue_command_events
                        WHERE command_id = ? AND event_type = 'SUBMIT_REQUESTED'
                        """,
                        (command_rows[0]["command_id"] if command_rows else "",),
                    ).fetchone()[0]
                finally:
                    read_conn.close()

                observed["durable_command_count_before_preflight"] = len(command_rows)
                observed["durable_command_state_before_preflight"] = command_rows[0]["state"] if command_rows else None
                observed["durable_envelope_count_before_preflight"] = envelope_count
                observed["durable_submit_requested_count_before_preflight"] = requested_count

            def bind_submission_envelope(self, envelope):
                observed["bound_envelope"] = envelope

            def place_limit_order(self, **kwargs):
                final = observed["bound_envelope"].with_updates(
                    raw_response_json='{"orderID":"ord-entry-caller-durable"}',
                    order_id="ord-entry-caller-durable",
                )
                return {
                    "orderID": "ord-entry-caller-durable",
                    "status": "LIVE",
                    "_venue_submission_envelope": final.to_dict(),
                }

        try:
            with patch("src.data.polymarket_client.PolymarketClient", return_value=DurableVisibilityClient()):
                result = _live_order(
                    trade_id="trd-entry-caller-commit",
                    intent=intent,
                    shares=18.19,
                    conn=submit_conn,
                    decision_id="dec-entry-caller-commit",
                )
            assert not submit_conn.in_transaction
        finally:
            submit_conn.close()

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        assert observed["durable_command_count_before_preflight"] == 1
        assert observed["durable_command_state_before_preflight"] == "SUBMITTING"
        assert observed["durable_envelope_count_before_preflight"] == 1
        assert observed["durable_submit_requested_count_before_preflight"] == 1

    def test_entry_persists_final_submit_envelope_as_append_only_row(self, mem_conn, monkeypatch):
        """Entry ACK keeps command on pre-submit envelope and appends final SDK envelope."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(mem_conn)

        class FinalEnvelopeClient:
            def __init__(self):
                self.bound_envelope = None

            def v2_preflight(self):
                return None

            def bind_submission_envelope(self, envelope):
                self.bound_envelope = envelope

            def place_limit_order(self, **kwargs):
                assert self.bound_envelope is not None
                final = self.bound_envelope.with_updates(
                    raw_response_json='{"orderID":"ord-entry-final"}',
                    order_id="ord-entry-final",
                )
                return {
                    "orderID": "ord-entry-final",
                    "status": "LIVE",
                    "_venue_submission_envelope": final.to_dict(),
                }

        with patch("src.data.polymarket_client.PolymarketClient", return_value=FinalEnvelopeClient()):
            result = _live_order(
                trade_id="trd-entry-final-envelope",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-final-envelope",
            )

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        command = mem_conn.execute(
            "SELECT command_id, envelope_id FROM venue_commands WHERE position_id = ?",
            ("trd-entry-final-envelope",),
        ).fetchone()
        rows = mem_conn.execute(
            "SELECT envelope_id, order_id FROM venue_submission_envelopes ORDER BY captured_at, envelope_id"
        ).fetchall()
        assert len(rows) == 2
        pre_ids = {row["envelope_id"] for row in rows if row["order_id"] is None}
        final_rows = [row for row in rows if row["order_id"] == "ord-entry-final"]
        assert command["envelope_id"] in pre_ids
        assert len(final_rows) == 1
        assert final_rows[0]["envelope_id"] != command["envelope_id"]

        ack = [e for e in list_events(mem_conn, command["command_id"]) if e["event_type"] == "SUBMIT_ACKED"][0]
        payload = json.loads(ack["payload_json"])
        assert payload["final_submission_envelope_id"] == final_rows[0]["envelope_id"]
        assert payload["final_submission_envelope_stage"] == "post_submit_result"

    def test_entry_missing_final_submit_envelope_goes_review_required(self, mem_conn, monkeypatch):
        """A post-submit ACK-shaped result without final envelope cannot become ACKED."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_entry_intent(mem_conn)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            mock_inst.place_limit_order.return_value = {"orderID": "ord-entry-no-envelope", "status": "LIVE"}

            result = _live_order(
                trade_id="trd-entry-no-final-envelope",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-no-final-envelope",
            )

        assert result.status == "unknown_side_effect"
        assert result.command_state == "REVIEW_REQUIRED"
        command = mem_conn.execute(
            "SELECT command_id, idempotency_key, state FROM venue_commands WHERE position_id = ?",
            ("trd-entry-no-final-envelope",),
        ).fetchone()
        assert result.command_id == command["command_id"]
        assert result.idempotency_key == command["idempotency_key"]
        assert command["state"] == "REVIEW_REQUIRED"
        events = list_events(mem_conn, command["command_id"])
        event_types = [event["event_type"] for event in events]
        assert "REVIEW_REQUIRED" in event_types
        assert "SUBMIT_ACKED" not in event_types
        assert "SUBMIT_REJECTED" not in event_types
        review = [event for event in events if event["event_type"] == "REVIEW_REQUIRED"][0]
        payload = json.loads(review["payload_json"])
        assert payload["reason"] == "final_submission_envelope_persistence_failed"
        assert payload["venue_order_id"] == "ord-entry-no-envelope"

    def test_submit_unknown_writes_event_with_side_effect_unknown(self, mem_conn):
        """Crash-injection drill: place_limit_order raises RuntimeError.

        M2: once place_limit_order may have crossed the venue side-effect
        boundary, the row must reach SUBMIT_UNKNOWN_SIDE_EFFECT via
        SUBMIT_TIMEOUT_UNKNOWN. The row is the recovery anchor.
        """
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import find_unresolved_commands, list_events

        intent = _make_entry_intent(mem_conn)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            mock_inst.place_limit_order.side_effect = RuntimeError("simulated venue timeout")

            result = _live_order(
                trade_id="trd-002",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-002",
            )

        # The OrderResult reflects the unknown side-effect outcome.
        assert result.status == "unknown_side_effect", (
            f"Expected status=unknown_side_effect, got {result.status!r}"
        )
        assert result.reason is not None and "submit_unknown_side_effect" in result.reason, (
            f"Expected reason to contain 'submit_unknown_side_effect', got {result.reason!r}"
        )
        assert result.command_state == "SUBMIT_UNKNOWN_SIDE_EFFECT"

        # The durable record must show SUBMIT_UNKNOWN_SIDE_EFFECT (recovery can resolve).
        unresolved = find_unresolved_commands(mem_conn)
        assert len(unresolved) == 1, (
            f"Expected 1 unresolved command (SUBMIT_UNKNOWN_SIDE_EFFECT), found {len(unresolved)}: {unresolved}"
        )
        assert unresolved[0]["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT", (
            f"Expected state=SUBMIT_UNKNOWN_SIDE_EFFECT in journal, got {unresolved[0]['state']!r}"
        )

        # Check the event chain
        events = list_events(mem_conn, unresolved[0]["command_id"])
        event_types = [e["event_type"] for e in events]
        assert "INTENT_CREATED" in event_types
        assert "SUBMIT_REQUESTED" in event_types
        assert "SUBMIT_TIMEOUT_UNKNOWN" in event_types

    def test_submit_rejected_writes_event_with_state_rejected(self, mem_conn):
        """place_limit_order returns None -> REVIEW_REQUIRED, not normal rejected."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        real_insert = None
        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            mock_inst.place_limit_order.return_value = None

            result = _live_order(
                trade_id="trd-003",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-003",
            )

        assert result.status == "unknown_side_effect"
        assert result.command_state == "REVIEW_REQUIRED"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert result.command_id == command_ids_seen[0]
        assert result.idempotency_key == cmd["idempotency_key"]
        assert cmd["state"] == "REVIEW_REQUIRED", (
            f"Expected state=REVIEW_REQUIRED after None return, got {cmd['state']!r}"
        )

    def test_submit_missing_order_id_rejects_without_submit_acked(self, mem_conn):
        """place_limit_order dict without order id -> REJECTED, not ACKED."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command, list_events

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, success=True, status="LIVE")
            )

            result = _live_order(
                trade_id="trd-missing-order-id",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-missing-order-id",
            )

        assert result.status == "rejected"
        assert result.reason == "missing_order_id"
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert result.command_id == command_ids_seen[0]
        assert result.command_state == "REJECTED"
        assert result.idempotency_key == cmd["idempotency_key"]
        assert cmd["state"] == "REJECTED"
        event_types = [event["event_type"] for event in list_events(mem_conn, command_ids_seen[0])]
        assert "SUBMIT_REJECTED" in event_types
        assert "SUBMIT_ACKED" not in event_types

    def test_submit_success_false_rejects_without_submit_acked(self, mem_conn):
        """place_limit_order success=false -> REJECTED with venue error code."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command, list_events

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = lambda **kwargs: _final_submit_result(
                bound,
                success=False,
                status="rejected",
                error_code="INSUFFICIENT_BALANCE",
                error_message="not enough funds",
            )

            result = _live_order(
                trade_id="trd-success-false",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-success-false",
            )

        assert result.status == "rejected"
        assert result.reason == "INSUFFICIENT_BALANCE"
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert result.command_id == command_ids_seen[0]
        assert result.command_state == "REJECTED"
        assert result.idempotency_key == cmd["idempotency_key"]
        assert cmd["state"] == "REJECTED"
        event_types = [event["event_type"] for event in list_events(mem_conn, command_ids_seen[0])]
        assert "SUBMIT_REJECTED" in event_types
        assert "SUBMIT_ACKED" not in event_types
        rejected = [event for event in list_events(mem_conn, command_ids_seen[0]) if event["event_type"] == "SUBMIT_REJECTED"][0]
        payload = json.loads(rejected["payload_json"])
        assert payload["final_submission_envelope_id"]

    def test_submit_acked_writes_event_with_state_acked(self, mem_conn):
        """place_limit_order returns orderID -> state=ACKED, venue_order_id set."""
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-acked-001")
            )

            result = _live_order(
                trade_id="trd-004",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-004",
            )

        assert result.status == "pending"  # OrderResult status is 'pending' until fill
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "ACKED", (
            f"Expected state=ACKED after successful ack, got {cmd['state']!r}"
        )
        order_fact = mem_conn.execute(
            "SELECT venue_order_id, command_id, state, remaining_size, matched_size, source "
            "FROM venue_order_facts WHERE command_id = ?",
            (command_ids_seen[0],),
        ).fetchone()
        assert dict(order_fact) == {
            "venue_order_id": "ord-acked-001",
            "command_id": command_ids_seen[0],
            "state": "LIVE",
            "remaining_size": "18.19",
            "matched_size": "0",
            "source": "REST",
        }

    def test_entry_submit_result_returns_submit_and_ack_times(self, mem_conn, monkeypatch):
        """Entry submit facts must flow back to cycle_runtime after the SDK boundary."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )
        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-timing-001")
            )

            result = _live_order(
                trade_id="trd-entry-timing",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-timing",
            )

            assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
            assert result.zeus_submit_intent_time
            assert result.venue_ack_time
            submit_at = datetime.fromisoformat(result.zeus_submit_intent_time)
            ack_at = datetime.fromisoformat(result.venue_ack_time)
            assert submit_at <= ack_at
            events = list_events(mem_conn, command_ids_seen[0])
            submit_requested = [event for event in events if event["event_type"] == "SUBMIT_REQUESTED"][0]
            submit_acked = [event for event in events if event["event_type"] == "SUBMIT_ACKED"][0]
            assert [event["event_type"] for event in events][-1] == "SUBMIT_ACKED"
            assert events[-1]["occurred_at"] == result.venue_ack_time

            mock_inst.place_limit_order.side_effect = AssertionError(
                "existing-command path should not resubmit"
            )
            retry_result = _live_order(
                trade_id="trd-entry-timing",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-timing",
            )

            assert retry_result.zeus_submit_intent_time == submit_requested["occurred_at"]
            assert retry_result.venue_ack_time == submit_acked["occurred_at"]
            assert len(command_ids_seen) == 1

    def test_matched_submit_records_fill_truth_instead_of_resting_ack(self, mem_conn, monkeypatch):
        """A matched FOK submit response is a fill boundary, not a resting ACK.

        The fill must also become visible to position/redecision immediately;
        the periodic recovery loop is only a crash backstop, not the first
        consumer of a known matched submit.
        """
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command, list_events

        monkeypatch.setattr(
            executor_module,
            "_entry_control_pause_component",
            lambda *args, **kwargs: {
                "component": "entries_pause_control_override",
                "allowed": True,
                "reason": "not_paused",
            },
        )
        certificate_hash = "d" * 64
        intent = _make_entry_intent(
            mem_conn,
            limit_price=0.34,
            actionable_certificate_hash=certificate_hash,
        )
        _insert_actionable_certificate_for_intent(
            mem_conn,
            intent,
            certificate_hash=certificate_hash,
        )
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        projection_calls: list[str] = []

        def _project_now(_conn, *, command_id: str, client=None):
            projection_calls.append(command_id)
            assert client is mock_inst
            return {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            monkeypatch.setattr(
                "src.execution.command_recovery.ensure_live_entry_projection_for_command",
                _project_now,
            )
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(
                    bound,
                    order_id="ord-matched-001",
                    status="matched",
                    success=True,
                    raw_extra={
                        "makingAmount": "1.70",
                        "takingAmount": "5",
                        "transactionsHashes": ["0xhash-matched"],
                    },
                )
            )
            mock_inst.get_order.return_value = {
                "id": "ord-matched-001",
                "status": "MATCHED",
                "size_matched": "5",
                "price": "0.34",
                "associate_trades": ["trade-matched-001"],
            }

            result = _live_order(
                trade_id="trd-matched",
                intent=intent,
                shares=5.0,
                conn=mem_conn,
                decision_id="dec-matched",
            )

        assert result.status == "filled"
        assert result.command_state == "FILLED"
        assert result.fill_price == pytest.approx(0.34)
        assert result.shares == pytest.approx(5.0)
        assert len(command_ids_seen) == 1
        command_id = command_ids_seen[0]
        assert projection_calls == [command_id]
        cmd = get_command(mem_conn, command_id)
        assert cmd is not None
        assert cmd["state"] == "FILLED"
        event_types = [event["event_type"] for event in list_events(mem_conn, command_id)]
        assert event_types[-2:] == ["SUBMIT_ACKED", "FILL_CONFIRMED"]
        order_fact = mem_conn.execute(
            """
            SELECT venue_order_id, state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
        assert dict(order_fact) == {
            "venue_order_id": "ord-matched-001",
            "state": "MATCHED",
            "remaining_size": "0",
            "matched_size": "5",
            "source": "REST",
        }
        trade_fact = mem_conn.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
            """,
            (command_id,),
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-matched-001",
            "venue_order_id": "ord-matched-001",
            "state": "MATCHED",
            "filled_size": "5",
            "fill_price": "0.34",
            "tx_hash": "0xhash-matched",
        }

    def test_matched_submit_without_fill_evidence_requires_review(self, mem_conn, monkeypatch):
        """Matched venue status without fill size/price/trade proof is unresolved."""
        import src.execution.executor as executor_module
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command, list_events

        monkeypatch.setattr(
            executor_module,
            "_entry_control_pause_component",
            lambda *args, **kwargs: {
                "component": "entries_pause_control_override",
                "allowed": True,
                "reason": "not_paused",
            },
        )
        certificate_hash = "e" * 64
        intent = _make_entry_intent(
            mem_conn,
            limit_price=0.34,
            actionable_certificate_hash=certificate_hash,
        )
        _insert_actionable_certificate_for_intent(
            mem_conn,
            intent,
            certificate_hash=certificate_hash,
        )
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(
                    bound,
                    order_id="ord-matched-missing-fill",
                    status="matched",
                    success=True,
                )
            )
            mock_inst.get_order.return_value = {
                "id": "ord-matched-missing-fill",
                "status": "MATCHED",
            }

            result = _live_order(
                trade_id="trd-matched-missing-fill",
                intent=intent,
                shares=5.0,
                conn=mem_conn,
                decision_id="dec-matched-missing-fill",
            )

        assert result.status == "unknown_side_effect"
        assert result.reason == "matched_submit_missing_fill_size"
        assert result.command_state == "REVIEW_REQUIRED"
        command_id = command_ids_seen[0]
        cmd = get_command(mem_conn, command_id)
        assert cmd is not None
        assert cmd["state"] == "REVIEW_REQUIRED"
        assert cmd["venue_order_id"] == "ord-matched-missing-fill"
        event_types = [event["event_type"] for event in list_events(mem_conn, command_id)]
        assert "REVIEW_REQUIRED" in event_types
        assert "SUBMIT_ACKED" not in event_types
        assert (
            mem_conn.execute(
                "SELECT COUNT(*) FROM venue_order_facts WHERE command_id = ?",
                (command_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            mem_conn.execute(
                "SELECT COUNT(*) FROM venue_trade_facts WHERE command_id = ?",
                (command_id,),
            ).fetchone()[0]
            == 0
        )

    def test_idempotency_key_collision_raises_before_submit(self, mem_conn, monkeypatch):
        """Duplicate idempotency key: place_limit_order must NOT be called.

        Insert a command with a known idempotency_key first, then run a second
        _live_order with inputs that hash to the same key. The second call must
        return a rejected OrderResult without calling place_limit_order.
        """
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command
        import src.execution.executor as executor_module

        intent = _make_entry_intent(mem_conn, token_id="tok-idem" + "0" * 33)
        monkeypatch.setattr(
            executor_module,
            "_entry_actionable_certificate_payload_and_component",
            lambda *args, **kwargs: (
                {"component": "entry_actionable_certificate", "allowed": True, "reason": "test"},
                {"actionable_certificate_hash": "hash-test"},
            ),
        )

        # Pre-insert a command with the key that _live_order will derive
        idem = IdempotencyKey.from_inputs(
            decision_id="dec-collision",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-existing-cmd",
            snapshot_id=intent.executable_snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=intent.token_id,
                price=intent.limit_price,
                size=18.19,
            ),
            position_id="trd-pre",
            decision_id="dec-collision",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-005",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-collision",  # same decision_id => same key
            )

        assert result.status == "rejected", (
            f"Expected rejected on idempotency collision, got {result.status!r}"
        )
        assert result.reason is not None and "idempotency_collision" in result.reason, (
            f"Expected reason containing 'idempotency_collision', got {result.reason!r}"
        )
        assert result.idempotency_key == idem.value
        # Most importantly: place_limit_order was never reached
        mock_inst.place_limit_order.assert_not_called()

    def test_v2_preflight_failure_writes_rejected_event(self, mem_conn, monkeypatch):
        """V2 preflight raises V2PreflightError -> state=REJECTED, place_limit_order not called.

        The command is already persisted (SUBMITTING) when preflight runs.
        On preflight failure, a SUBMIT_REJECTED event must be appended so
        the row reaches a terminal state.
        """
        from src.execution.executor import _live_order
        from src.data.polymarket_client import V2PreflightError
        from src.state.venue_command_repo import get_command
        import src.execution.executor as executor_module

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []
        monkeypatch.setattr(
            executor_module,
            "_entry_actionable_certificate_payload_and_component",
            lambda *args, **kwargs: (
                {"component": "entry_actionable_certificate", "allowed": True, "reason": "test"},
                {"actionable_certificate_hash": "hash-test"},
            ),
        )
        monkeypatch.setattr(
            executor_module,
            "_entry_control_pause_component",
            lambda *args, **kwargs: {
                "component": "entries_pause_control_override",
                "allowed": True,
                "reason": "not_paused",
            },
        )
        monkeypatch.setattr(
            executor_module,
            "_entry_duplicate_same_token_component",
            lambda *args, **kwargs: {
                "component": "entry_duplicate_same_token",
                "allowed": True,
                "reason": "no_duplicate",
            },
        )
        monkeypatch.setattr(
            executor_module,
            "_entry_same_token_cooldown_component",
            lambda *args, **kwargs: {
                "component": "entry_same_token_cooldown",
                "allowed": True,
                "reason": "not_in_cooldown",
            },
        )
        monkeypatch.setattr(
            executor_module,
            "_entry_decision_source_component",
            lambda *args, **kwargs: {
                "component": "entry_decision_source",
                "allowed": True,
                "reason": "valid",
            },
        )

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.side_effect = V2PreflightError("endpoint down")

            result = _live_order(
                trade_id="trd-006",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-006",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "v2_preflight_failed" in result.reason
        mock_inst.place_limit_order.assert_not_called()

        # Command row must be REJECTED (not stuck in SUBMITTING)
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REJECTED", (
            f"Expected state=REJECTED after v2_preflight failure, got {cmd['state']!r}"
        )
        assert result.command_id == command_ids_seen[0]
        assert result.command_state == "REJECTED"
        assert result.idempotency_key == cmd["idempotency_key"]

    def test_entry_client_init_failure_returns_durable_command_id(self, mem_conn, monkeypatch):
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command

        _allow_entry_submit_until_client(monkeypatch)
        intent = _make_entry_intent(mem_conn)

        with patch("src.data.polymarket_client.PolymarketClient", side_effect=RuntimeError("client down")):
            result = _live_order(
                trade_id="trd-entry-client-init-failed",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-client-init-failed",
            )

        assert result.status == "rejected"
        assert result.command_id
        assert result.command_state == "REJECTED"
        assert result.idempotency_key
        cmd = get_command(mem_conn, result.command_id)
        assert cmd is not None
        assert cmd["state"] == "REJECTED"
        assert cmd["idempotency_key"] == result.idempotency_key

    def test_entry_generic_v2_preflight_exception_returns_durable_command_id(
        self,
        mem_conn,
        monkeypatch,
    ):
        from src.execution.executor import _live_order
        from src.state.venue_command_repo import get_command

        _allow_entry_submit_until_client(monkeypatch)
        intent = _make_entry_intent(mem_conn)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.side_effect = RuntimeError("generic preflight failure")
            result = _live_order(
                trade_id="trd-entry-generic-v2-failed",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-entry-generic-v2-failed",
            )

        assert result.status == "rejected"
        assert result.command_id
        assert result.command_state == "REJECTED"
        cmd = get_command(mem_conn, result.command_id)
        assert cmd is not None
        assert cmd["state"] == "REJECTED"

    def test_pre_sdk_collateral_reservation_failure_writes_rejected_event(
        self,
        mem_conn,
        monkeypatch,
    ):
        """Collateral reservation failure after SUBMIT_REQUESTED is terminal."""
        from src.execution.executor import _live_order
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.collateral_ledger import CollateralInsufficient
        from src.state.venue_command_repo import get_command, list_events

        intent = _make_entry_intent(mem_conn)
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        def fail_reservation(*args, **kwargs):
            raise CollateralInsufficient("pusd_allowance_insufficient")

        monkeypatch.setattr(
            "src.execution.executor._entry_actionable_certificate_component",
            lambda *args, **kwargs: {
                "component": "entry_actionable_certificate",
                "allowed": True,
                "reason": "allowed",
            },
        )
        monkeypatch.setattr(
            "src.execution.executor._entry_control_pause_component",
            lambda *args, **kwargs: {
                "component": "entries_pause_control_override",
                "allowed": True,
                "reason": "not_paused",
            },
        )
        monkeypatch.setattr(
            "src.execution.executor._reserve_collateral_for_buy",
            fail_reservation,
        )

        with patch(
            "src.state.venue_command_repo.insert_command",
            side_effect=capturing_insert,
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            result = _live_order(
                trade_id="trd-pre-sdk-collateral",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-pre-sdk-collateral",
            )

        assert result.status == "rejected"
        assert result.command_state == "REJECTED"
        assert result.reason is not None
        assert "pre_submit_collateral_reservation_failed" in result.reason
        MockClient.assert_not_called()

        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "REJECTED"
        events = list_events(mem_conn, command_ids_seen[0])
        event_types = [event["event_type"] for event in events]
        assert "SUBMIT_REQUESTED" in event_types
        assert "SUBMIT_REJECTED" in event_types
        assert "REVIEW_REQUIRED" not in event_types
        rejected = [event for event in events if event["event_type"] == "SUBMIT_REJECTED"][0]
        payload = json.loads(rejected["payload_json"])
        assert payload["reason"] == "pre_submit_collateral_reservation_failed"
        assert payload["side_effect_boundary_crossed"] is False
        assert payload["sdk_submit_attempted"] is False
        unknown_count, unknown_markets = count_unknown_side_effects(mem_conn)
        assert unknown_count == 0
        assert unknown_markets == ()

    def test_pre_command_collateral_failure_does_not_append_unknown_command_event(
        self,
        mem_conn,
        monkeypatch,
    ):
        """Collateral preflight before insert_command has no command row to annotate."""
        from src.execution.executor import _live_order
        from src.state.collateral_ledger import CollateralInsufficient

        intent = _make_entry_intent(mem_conn)

        def fail_preflight(*args, **kwargs):
            raise CollateralInsufficient("pusd_allowance_insufficient")

        monkeypatch.setattr(
            "src.execution.executor._assert_collateral_allows_buy",
            fail_preflight,
        )
        monkeypatch.setattr(
            "src.execution.executor._entry_actionable_certificate_component",
            lambda *args, **kwargs: {
                "component": "entry_actionable_certificate",
                "allowed": True,
                "reason": "allowed",
            },
        )
        monkeypatch.setattr(
            "src.execution.executor._entry_control_pause_component",
            lambda *args, **kwargs: {
                "component": "entries_pause_control_override",
                "allowed": True,
                "reason": "not_paused",
            },
        )

        with patch("src.state.venue_command_repo.append_event") as append_event_mock, patch(
            "src.data.polymarket_client.PolymarketClient"
        ) as MockClient:
            result = _live_order(
                trade_id="trd-pre-command-collateral",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-pre-command-collateral",
            )

        assert result.status == "rejected"
        assert result.command_state == "REJECTED"
        assert result.reason is not None
        assert "pre_submit_collateral_reservation_failed" in result.reason
        append_event_mock.assert_not_called()
        MockClient.assert_not_called()
        assert mem_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
        assert mem_conn.execute("SELECT COUNT(*) FROM venue_command_events").fetchone()[0] == 0

    def test_executionprice_validation_runs_before_persist(self, mem_conn):
        """NaN limit_price: ExecutionPrice rejects before any DB write.

        No venue_commands row should be inserted when the price is malformed.
        """
        from src.execution.executor import _live_order
        import math

        # Build an intent with NaN limit_price; bypass the constructor if needed
        # by constructing manually via dataclass replace equivalent
        from src.contracts.execution_intent import ExecutionIntent
        from src.contracts import Direction
        import dataclasses

        base_intent = _make_entry_intent(mem_conn)
        nan_intent = dataclasses.replace(base_intent, limit_price=float("nan"))

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst

            result = _live_order(
                trade_id="trd-007",
                intent=nan_intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-007",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "malformed_limit_price" in result.reason

        # No command row should have been persisted
        row_count = mem_conn.execute(
            "SELECT COUNT(*) FROM venue_commands"
        ).fetchone()[0]
        assert row_count == 0, (
            f"Expected no venue_commands rows after ExecutionPrice rejection, found {row_count}"
        )
        mock_inst.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# TestExitOrderCommandSplit — exit path (execute_exit_order / IntentKind.EXIT)
# ---------------------------------------------------------------------------

class TestExitOrderCommandSplit:
    """INV-30: execute_exit_order must persist before it submits."""

    def test_exit_submit_checks_live_venue_runtime_gate(self, mem_conn, monkeypatch):
        """Exit submit must be blocked by the same live runtime gate as entry."""
        import src.architecture.gate_runtime as gate_runtime
        from src.execution.executor import execute_exit_order

        checked: list[str] = []

        def _spy_check(capability: str) -> None:
            checked.append(capability)

        monkeypatch.setattr(gate_runtime, "check", _spy_check)
        intent = _make_exit_intent(mem_conn)

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-exit-gate")
            )

            execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-gate",
            )

        assert checked[:2] == ["live_venue_submit", "settlement_write"]

    def test_exit_persist_precedes_submit(self, mem_conn):
        """insert_command must run before place_limit_order (exit path)."""
        from src.execution.executor import execute_exit_order

        call_log: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def spy_insert(*args, **kwargs):
            call_log.append("insert_command")
            return _real_insert(*args, **kwargs)

        intent = _make_exit_intent(mem_conn)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=spy_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)

            def spy_place(**kwargs):
                call_log.append("place_limit_order")
                return _final_submit_result(bound, order_id="ord-exit-001")

            mock_inst.place_limit_order.side_effect = spy_place

            execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-001",
            )

        assert "insert_command" in call_log, "insert_command must have been called"
        assert "place_limit_order" in call_log, "place_limit_order must have been called"
        assert call_log.index("insert_command") < call_log.index("place_limit_order"), (
            f"INV-30: insert_command must precede place_limit_order; call order was {call_log}"
        )

    def test_exit_client_init_failure_returns_durable_command_id(self, mem_conn, monkeypatch):
        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import get_command

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )
        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-client-init-failed")

        with patch("src.data.polymarket_client.PolymarketClient", side_effect=RuntimeError("client down")):
            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-client-init-failed",
            )

        assert result.status == "rejected"
        assert result.command_id
        assert result.command_state == "REJECTED"
        assert result.idempotency_key
        cmd = get_command(mem_conn, result.command_id)
        assert cmd is not None
        assert cmd["state"] == "REJECTED"
        assert cmd["idempotency_key"] == result.idempotency_key

    def test_exit_caller_connection_commits_submit_boundaries(self, tmp_path, monkeypatch):
        """Caller-owned exit conn must not hold write locks across SDK contact."""
        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.state.db import get_connection, init_schema

        token_id = "tok-" + "7" * 36
        db_path = tmp_path / "exit-caller-conn-durable.db"
        setup_conn = get_connection(db_path)
        init_schema(setup_conn)
        intent = _make_exit_intent(
            setup_conn,
            trade_id="trd-exit-caller-commit",
            token_id=token_id,
        )
        setup_conn.commit()
        setup_conn.close()

        submit_conn = get_connection(db_path)
        init_schema(submit_conn)
        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        observed = {}

        class DurableVisibilityClient:
            def bind_submission_envelope(self, envelope):
                observed["bound_envelope"] = envelope

            def place_limit_order(self, **kwargs):
                read_conn = get_connection(db_path)
                init_schema(read_conn)
                try:
                    command_rows = read_conn.execute(
                        """
                        SELECT command_id, snapshot_id, envelope_id, state
                        FROM venue_commands
                        WHERE snapshot_id = ?
                        """,
                        (intent.executable_snapshot_id,),
                    ).fetchall()
                    envelope_count = read_conn.execute(
                        "SELECT COUNT(*) FROM venue_submission_envelopes"
                    ).fetchone()[0]
                    requested_count = read_conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM venue_command_events
                        WHERE command_id = ? AND event_type = 'SUBMIT_REQUESTED'
                        """,
                        (command_rows[0]["command_id"] if command_rows else "",),
                    ).fetchone()[0]
                finally:
                    read_conn.close()

                observed["durable_command_count_before_sdk"] = len(command_rows)
                observed["durable_command_state_before_sdk"] = command_rows[0]["state"] if command_rows else None
                observed["durable_envelope_count_before_sdk"] = envelope_count
                observed["durable_submit_requested_count_before_sdk"] = requested_count
                final = observed["bound_envelope"].with_updates(
                    raw_response_json='{"orderID":"ord-exit-durable"}',
                    order_id="ord-exit-durable",
                )
                return {
                    "orderID": "ord-exit-durable",
                    "status": "LIVE",
                    "_venue_submission_envelope": final.to_dict(),
                }

        try:
            with patch("src.data.polymarket_client.PolymarketClient", return_value=DurableVisibilityClient()):
                result = execute_exit_order(
                    intent=intent,
                    conn=submit_conn,
                    decision_id="dec-exit-caller-commit",
                )
            assert not submit_conn.in_transaction

            read_conn = get_connection(db_path)
            init_schema(read_conn)
            try:
                command = read_conn.execute(
                    """
                    SELECT command_id, state, venue_order_id
                    FROM venue_commands
                    WHERE position_id = ?
                    """,
                    ("trd-exit-caller-commit",),
                ).fetchone()
                ack_count = read_conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM venue_command_events
                    WHERE command_id = ? AND event_type = 'SUBMIT_ACKED'
                    """,
                    (command["command_id"] if command else "",),
                ).fetchone()[0]
                fact_count = read_conn.execute(
                    "SELECT COUNT(*) FROM venue_order_facts WHERE command_id = ?",
                    (command["command_id"] if command else "",),
                ).fetchone()[0]
            finally:
                read_conn.close()
        finally:
            submit_conn.close()

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        assert observed["durable_command_count_before_sdk"] == 1
        assert observed["durable_command_state_before_sdk"] == "SUBMITTING"
        assert observed["durable_envelope_count_before_sdk"] == 1
        assert observed["durable_submit_requested_count_before_sdk"] == 1
        assert command["state"] == "ACKED"
        assert command["venue_order_id"] == "ord-exit-durable"
        assert ack_count == 1
        assert fact_count == 1

    def test_exit_submit_requested_persists_execution_capability_proof(self, mem_conn, monkeypatch):
        """Exit SUBMIT_REQUESTED carries one pre-submit capability proof."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "FOK")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-capability")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-exit-capability")
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-capability",
            )

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        row = mem_conn.execute(
            "SELECT command_id FROM venue_commands WHERE position_id = ?",
            ("trd-exit-capability",),
        ).fetchone()
        events = list_events(mem_conn, row["command_id"])
        requested = [event for event in events if event["event_type"] == "SUBMIT_REQUESTED"]
        payload = json.loads(requested[0]["payload_json"])
        capability = payload["execution_capability"]

        assert capability["action"] == "EXIT"
        assert capability["intent_kind"] == "EXIT"
        assert capability["order_type"] == "FAK"
        assert capability["venue_order_type"] == "FAK"
        assert capability["risk_allocator_selected_order_type"] == "FOK"
        assert capability["allowed"] is True
        assert len(capability["capability_id"]) == 32
        assert capability["command_id"] == row["command_id"]
        assert capability["executable_snapshot_id"] == intent.executable_snapshot_id
        components_by_name = {component["component"]: component for component in capability["components"]}
        assert {component["component"] for component in capability["components"]} >= {
            "cutover_guard",
            "risk_allocator",
            "order_type_selection",
            "heartbeat_supervisor",
            "ws_gap_guard",
            "collateral_ledger",
            "replacement_sell_guard",
            "decision_source_integrity",
            "executable_snapshot_gate",
        }
        assert components_by_name["decision_source_integrity"]["allowed"] is True
        assert components_by_name["decision_source_integrity"]["reason"] == "not_applicable_reduce_only"
        order_type_selection = components_by_name["order_type_selection"]["details"]
        assert order_type_selection["selected_order_type"] == "FOK"
        assert order_type_selection["order_type"] == "FAK"

    def test_exit_collateral_refresh_precedes_sell_preflight_and_proof(self, mem_conn, monkeypatch):
        """Exit sell collateral truth must refresh before the CTF sell preflight."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import list_events

        call_order: list[str] = []

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        def refresh(conn, *, token_id, shares):
            call_order.append("refresh")
            return {
                "component": "collateral_snapshot_refresh",
                "allowed": True,
                "reason": "refreshed_before_exit_submit",
                "token_id": token_id,
                "shares": shares,
            }

        def assert_sell(token_id, shares, *, conn):
            call_order.append("assert_sell")
            assert call_order == ["refresh", "assert_sell"]
            return {
                "component": "collateral_ledger",
                "allowed": True,
                "reason": "ctf_tokens_available",
                "token_id": token_id,
                "shares": shares,
            }

        monkeypatch.setattr(executor_module, "_refresh_exit_collateral_snapshot_for_submit", refresh)
        monkeypatch.setattr(executor_module, "_assert_collateral_allows_sell", assert_sell)

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-refresh-before-preflight")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-exit-refresh-before-preflight")
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-refresh-before-preflight",
            )

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        assert call_order == ["refresh", "assert_sell"]
        command = mem_conn.execute(
            "SELECT command_id FROM venue_commands WHERE position_id = ?",
            ("trd-exit-refresh-before-preflight",),
        ).fetchone()
        requested = [
            event for event in list_events(mem_conn, command["command_id"])
            if event["event_type"] == "SUBMIT_REQUESTED"
        ][0]
        capability = json.loads(requested["payload_json"])["execution_capability"]
        component_names = [component["component"] for component in capability["components"]]
        assert component_names.index("collateral_snapshot_refresh") < component_names.index("collateral_ledger")

    def test_exit_pre_sdk_collateral_reservation_failure_writes_rejected_event(
        self,
        mem_conn,
        monkeypatch,
    ):
        """Exit collateral reservation failure is terminal before SDK contact."""
        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.collateral_ledger import CollateralInsufficient
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "FOK")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        def fail_reservation(*args, **kwargs):
            raise CollateralInsufficient("ctf_allowance_insufficient")

        monkeypatch.setattr(
            executor_module,
            "_reserve_collateral_for_sell",
            fail_reservation,
        )

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-pre-sdk-collateral")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-pre-sdk-collateral",
            )

        assert result.status == "rejected"
        assert result.command_state == "REJECTED"
        assert result.reason is not None
        assert "pre_submit_collateral_reservation_failed" in result.reason
        MockClient.assert_not_called()

        command = mem_conn.execute(
            "SELECT command_id, state FROM venue_commands WHERE position_id = ?",
            ("trd-exit-pre-sdk-collateral",),
        ).fetchone()
        assert command["state"] == "REJECTED"
        events = list_events(mem_conn, command["command_id"])
        event_types = [event["event_type"] for event in events]
        assert "SUBMIT_REQUESTED" in event_types
        assert "SUBMIT_REJECTED" in event_types
        assert "REVIEW_REQUIRED" not in event_types
        rejected = [event for event in events if event["event_type"] == "SUBMIT_REJECTED"][0]
        payload = json.loads(rejected["payload_json"])
        assert payload["reason"] == "pre_submit_collateral_reservation_failed"
        assert payload["side_effect_boundary_crossed"] is False
        assert payload["sdk_submit_attempted"] is False
        unknown_count, unknown_markets = count_unknown_side_effects(mem_conn)
        assert unknown_count == 0
        assert unknown_markets == ()
        mutex = mem_conn.execute(
            "SELECT released_at, release_reason FROM exit_mutex_holdings WHERE command_id = ?",
            (command["command_id"],),
        ).fetchone()
        assert mutex is not None
        assert mutex["released_at"] is not None
        assert mutex["release_reason"] == "REJECTED"

    def test_exit_binds_pre_submit_and_persists_final_submit_envelope(self, mem_conn, monkeypatch):
        """Exit ACK uses the U1 pre-submit envelope and appends final SDK facts."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "FOK")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-final-envelope")

        class FinalEnvelopeClient:
            def __init__(self):
                self.bound_envelope = None

            def bind_submission_envelope(self, envelope):
                self.bound_envelope = envelope

            def place_limit_order(self, **kwargs):
                assert self.bound_envelope is not None
                assert self.bound_envelope.side == "SELL"
                final = self.bound_envelope.with_updates(
                    raw_response_json='{"orderID":"ord-exit-final"}',
                    order_id="ord-exit-final",
                )
                return {
                    "orderID": "ord-exit-final",
                    "status": "LIVE",
                    "_venue_submission_envelope": final.to_dict(),
                }

        with patch("src.data.polymarket_client.PolymarketClient", return_value=FinalEnvelopeClient()):
            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-final-envelope",
            )

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        command = mem_conn.execute(
            "SELECT command_id, envelope_id FROM venue_commands WHERE position_id = ?",
            ("trd-exit-final-envelope",),
        ).fetchone()
        rows = mem_conn.execute(
            "SELECT envelope_id, order_id, side FROM venue_submission_envelopes"
        ).fetchall()
        assert len(rows) == 2
        final_rows = [row for row in rows if row["order_id"] == "ord-exit-final"]
        assert len(final_rows) == 1
        assert final_rows[0]["side"] == "SELL"
        assert final_rows[0]["envelope_id"] != command["envelope_id"]

        ack = [e for e in list_events(mem_conn, command["command_id"]) if e["event_type"] == "SUBMIT_ACKED"][0]
        payload = json.loads(ack["payload_json"])
        assert payload["final_submission_envelope_id"] == final_rows[0]["envelope_id"]
        assert payload["final_submission_envelope_stage"] == "post_submit_result"

    def test_exit_submit_rejected_persists_final_submit_envelope(self, mem_conn, monkeypatch):
        """Exit SUBMIT_REJECTED must cite the final SDK envelope row."""
        import json

        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import list_events

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "FOK")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-final-rejected")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = lambda **kwargs: _final_submit_result(
                bound,
                success=False,
                status="rejected",
                error_code="POST_ONLY_REJECTED",
                error_message="would cross spread",
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-final-rejected",
            )

        assert result.status == "rejected"
        assert result.reason == "POST_ONLY_REJECTED"
        command = mem_conn.execute(
            "SELECT command_id, idempotency_key, state FROM venue_commands WHERE position_id = ?",
            ("trd-exit-final-rejected",),
        ).fetchone()
        assert command["state"] == "REJECTED"
        assert result.command_id == command["command_id"]
        assert result.command_state == "REJECTED"
        assert result.idempotency_key == command["idempotency_key"]
        rejected = [
            event for event in list_events(mem_conn, command["command_id"])
            if event["event_type"] == "SUBMIT_REJECTED"
        ][0]
        payload = json.loads(rejected["payload_json"])
        assert payload["final_submission_envelope_id"]
        row = mem_conn.execute(
            "SELECT error_code FROM venue_submission_envelopes WHERE envelope_id = ?",
            (payload["final_submission_envelope_id"],),
        ).fetchone()
        assert row["error_code"] == "POST_ONLY_REJECTED"

    def test_exit_final_submission_envelope_failure_returns_command_truth(self, mem_conn, monkeypatch):
        """Exit final-envelope persistence failure is post-persist REVIEW_REQUIRED."""
        import src.execution.executor as executor_module
        from src.execution.executor import (
            FinalSubmissionEnvelopePersistenceError,
            execute_exit_order,
        )

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        def fail_final_envelope(*args, **kwargs):
            raise FinalSubmissionEnvelopePersistenceError("simulated final envelope failure")

        monkeypatch.setattr(
            executor_module,
            "_persist_final_submission_envelope_payload",
            fail_final_envelope,
        )
        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-final-envelope-failed")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = lambda **kwargs: _final_submit_result(
                bound,
                order_id="ord-final-envelope-failed",
                status="LIVE",
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-final-envelope-failed",
            )

        command = mem_conn.execute(
            "SELECT command_id, idempotency_key, state FROM venue_commands WHERE position_id = ?",
            ("trd-exit-final-envelope-failed",),
        ).fetchone()
        assert result.status == "unknown_side_effect"
        assert result.command_id == command["command_id"]
        assert result.command_state == "REVIEW_REQUIRED"
        assert result.idempotency_key == command["idempotency_key"]
        assert command["state"] == "REVIEW_REQUIRED"

    def test_exit_submit_rejected_returns_canonical_idempotency_key(self, mem_conn, monkeypatch):
        """Exit success=false ACK returns the derived command idempotency key."""
        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-success-false-idem")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = lambda **kwargs: _final_submit_result(
                bound,
                success=False,
                status="rejected",
                error_code="CLOB_REJECTED",
                error_message="rejected",
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-success-false-idem",
            )

        command = mem_conn.execute(
            "SELECT command_id, idempotency_key, state FROM venue_commands WHERE position_id = ?",
            ("trd-exit-success-false-idem",),
        ).fetchone()
        assert result.status == "rejected"
        assert result.command_id == command["command_id"]
        assert result.command_state == "REJECTED"
        assert result.idempotency_key == command["idempotency_key"]
        assert result.idempotency_key != intent.idempotency_key
        assert command["state"] == "REJECTED"

    def test_exit_missing_order_id_returns_canonical_idempotency_key(self, mem_conn, monkeypatch):
        """Exit missing-order-id ACK returns the derived command idempotency key."""
        import src.execution.executor as executor_module
        from src.execution.executor import execute_exit_order

        monkeypatch.setattr(executor_module, "_assert_risk_allocator_allows_exit_submit", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor_module, "_select_risk_allocator_order_type", lambda *args, **kwargs: "GTC")
        monkeypatch.setattr(
            executor_module,
            "_assert_ws_gap_allows_submit",
            lambda *args, **kwargs: {"component": "ws_gap_guard", "allowed": True, "reason": "allowed"},
        )

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-missing-order-idem")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id=None, status="LIVE")
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-missing-order-idem",
            )

        command = mem_conn.execute(
            "SELECT command_id, idempotency_key, state FROM venue_commands WHERE position_id = ?",
            ("trd-exit-missing-order-idem",),
        ).fetchone()
        assert result.status == "rejected"
        assert result.reason == "missing_order_id"
        assert result.command_id == command["command_id"]
        assert result.command_state == "REJECTED"
        assert result.idempotency_key == command["idempotency_key"]
        assert result.idempotency_key != intent.idempotency_key
        assert command["state"] == "REJECTED"

    def test_exit_submit_unknown_writes_event_with_side_effect_unknown(self, mem_conn):
        """Crash-injection drill (exit path): place_limit_order raises.

        M2: state must reach SUBMIT_UNKNOWN_SIDE_EFFECT via
        SUBMIT_TIMEOUT_UNKNOWN.
        """
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import find_unresolved_commands, list_events

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-002")

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.place_limit_order.side_effect = RuntimeError("simulated exit crash")

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-002",
            )

        assert result.status == "unknown_side_effect"
        assert result.reason is not None and "submit_unknown_side_effect" in result.reason
        assert result.command_state == "SUBMIT_UNKNOWN_SIDE_EFFECT"

        unresolved = find_unresolved_commands(mem_conn)
        assert len(unresolved) == 1, (
            f"Expected 1 unresolved command (SUBMIT_UNKNOWN_SIDE_EFFECT exit), found {len(unresolved)}"
        )
        assert unresolved[0]["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT"
        assert unresolved[0]["intent_kind"] == "EXIT"

        events = list_events(mem_conn, unresolved[0]["command_id"])
        event_types = [e["event_type"] for e in events]
        assert "SUBMIT_TIMEOUT_UNKNOWN" in event_types

    def test_exit_submit_rejected_writes_event_with_state_rejected(self, mem_conn):
        """place_limit_order returns None (exit path) -> REVIEW_REQUIRED."""
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import get_command

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-003")
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.place_limit_order.return_value = None

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-003",
            )

        assert result.status == "unknown_side_effect"
        assert result.command_state == "REVIEW_REQUIRED"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert result.command_id == command_ids_seen[0]
        assert result.idempotency_key == cmd["idempotency_key"]
        assert cmd["state"] == "REVIEW_REQUIRED"

    def test_exit_submit_acked_writes_event_with_state_acked(self, mem_conn):
        """place_limit_order returns orderID (exit path) -> state=ACKED."""
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import get_command

        intent = _make_exit_intent(mem_conn, trade_id="trd-exit-004")
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(bound, order_id="ord-exit-acked-001")
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-004",
            )

        assert result.status == "pending", f"reason={getattr(result, 'reason', None)}"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "ACKED"
        order_fact = mem_conn.execute(
            "SELECT venue_order_id, command_id, state, remaining_size, matched_size, source "
            "FROM venue_order_facts WHERE command_id = ?",
            (command_ids_seen[0],),
        ).fetchone()
        assert dict(order_fact) == {
            "venue_order_id": "ord-exit-acked-001",
            "command_id": command_ids_seen[0],
            "state": "LIVE",
            "remaining_size": "10.0",
            "matched_size": "0",
            "source": "REST",
        }

    def test_exit_matched_submit_uses_sell_making_amount_as_share_size(self, mem_conn):
        """SELL submit responses report shares in makingAmount and proceeds in takingAmount."""
        from src.execution.executor import execute_exit_order
        from src.state.venue_command_repo import get_command

        intent = _make_exit_intent(
            mem_conn,
            trade_id="trd-exit-matched-sell",
            shares=15.5,
            current_price=0.7,
        )
        command_ids_seen: list[str] = []

        import src.state.venue_command_repo as _repo
        _real_insert = _repo.insert_command

        def capturing_insert(*args, **kwargs):
            command_ids_seen.append(kwargs["command_id"])
            return _real_insert(*args, **kwargs)

        with patch(
            "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
        ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            bound = _capture_bound_submission_envelope(mock_inst)
            mock_inst.place_limit_order.side_effect = (
                lambda **kwargs: _final_submit_result(
                    bound,
                    order_id="ord-exit-matched-sell",
                    status="matched",
                    success=True,
                    raw_extra={
                        "makingAmount": "15.5",
                        "takingAmount": "10.85",
                        "transactionsHashes": ["0xhash-exit-matched"],
                    },
                )
            )

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-matched-sell",
            )

        assert result.status == "filled"
        assert len(command_ids_seen) == 1
        cmd = get_command(mem_conn, command_ids_seen[0])
        assert cmd is not None
        assert cmd["state"] == "FILLED"
        order_fact = mem_conn.execute(
            """
            SELECT venue_order_id, state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
            """,
            (command_ids_seen[0],),
        ).fetchone()
        assert dict(order_fact) == {
            "venue_order_id": "ord-exit-matched-sell",
            "state": "MATCHED",
            "remaining_size": "0",
            "matched_size": "15.5",
            "source": "REST",
        }
        trade_fact = mem_conn.execute(
            """
            SELECT venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
            """,
            (command_ids_seen[0],),
        ).fetchone()
        assert dict(trade_fact) == {
            "venue_order_id": "ord-exit-matched-sell",
            "state": "MATCHED",
            "filled_size": "15.5",
            "fill_price": "0.7",
            "tx_hash": "0xhash-exit-matched",
        }

    def test_exit_idempotency_key_collision_raises_before_submit(self, mem_conn):
        """Duplicate idempotency key (exit path): place_limit_order not called."""
        from src.execution.executor import execute_exit_order, create_exit_order_intent
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command
        from src.contracts.tick_size import TickSize

        token_id = "tok-exit-idem" + "0" * 27
        shares = 10.0
        current_price = 0.55

        # Derive the limit_price the same way execute_exit_order will
        tick = TickSize.for_market(token_id=token_id)
        limit_price = tick.clamp_to_valid_range(current_price - tick.value)
        effective_shares = __import__("math").floor(shares * 100 + 1e-9) / 100.0

        idem = IdempotencyKey.from_inputs(
            decision_id="dec-exit-collision",
            token_id=token_id,
            side="SELL",
            price=limit_price,
            size=effective_shares,
            intent_kind=IntentKind.EXIT,
        )
        snapshot_id = _ensure_snapshot(mem_conn, token_id=token_id)
        insert_command(
            mem_conn,
            command_id="pre-exit-cmd",
            snapshot_id=snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=token_id,
                side="SELL",
                price=limit_price,
                size=effective_shares,
            ),
            position_id="trd-exit-pre",
            decision_id="dec-exit-collision",
            idempotency_key=idem.value,
            intent_kind="EXIT",
            market_id=token_id,
            token_id=token_id,
            side="SELL",
            size=effective_shares,
            price=limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        mem_conn.commit()

        intent = create_exit_order_intent(
            trade_id="trd-exit-005",
            token_id=token_id,
            shares=shares,
            current_price=current_price,
        )

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst

            result = execute_exit_order(
                intent=intent,
                conn=mem_conn,
                decision_id="dec-exit-collision",
            )

        assert result.status == "rejected"
        assert result.reason is not None and "idempotency_collision" in result.reason
        assert result.idempotency_key == idem.value
        mock_inst.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# Idempotency collision retry (MEDIUM-1) — both paths
# ---------------------------------------------------------------------------

class TestIdempotencyCollisionRetry:
    """Collision retry: second call with same key returns existing state, not raw exc."""

    def test_idempotency_collision_returns_existing_state_acked(self, mem_conn):
        """Insert command in ACKED state, attempt second insert with same key.

        OrderResult.status must be 'pending' and reason includes 'prior attempt acked'.
        """
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command, append_event

        intent = _make_entry_intent(mem_conn, token_id="tok-coll-acked" + "0" * 27)

        # Pre-insert a command and advance it to ACKED state
        idem = IdempotencyKey.from_inputs(
            decision_id="dec-coll-acked",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-cmd-acked",
            snapshot_id=intent.executable_snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=intent.token_id,
                price=intent.limit_price,
                size=18.19,
            ),
            position_id="trd-pre-acked",
            decision_id="dec-coll-acked",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        # Advance to ACKED via SUBMIT_REQUESTED -> SUBMIT_ACKED
        append_event(
            mem_conn,
            command_id="pre-cmd-acked",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:00+00:00",
            payload=_submit_requested_payload("pre-cmd-acked"),
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-acked",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-04-26T00:00:01+00:00",
            payload={"venue_order_id": "acked-ord-001"},
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-collision-acked",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-coll-acked",
            )

        assert result.status == "pending", (
            f"Expected pending for ACKED collision, got {result.status!r}"
        )
        assert result.order_id == "acked-ord-001"
        assert result.external_order_id == "acked-ord-001"
        assert result.reason is not None and "prior attempt acked" in result.reason, (
            f"Expected reason to contain 'prior attempt acked', got {result.reason!r}"
        )
        mock_inst.place_limit_order.assert_not_called()

    def test_idempotency_collision_with_filled_state_returns_pending(self, mem_conn):
        """Insert command in FILLED state; collision must return status=pending."""
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command, append_event

        intent = _make_entry_intent(mem_conn, token_id="tok-coll-filled" + "0" * 25)

        idem = IdempotencyKey.from_inputs(
            decision_id="dec-coll-filled",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-cmd-filled",
            snapshot_id=intent.executable_snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=intent.token_id,
                price=intent.limit_price,
                size=18.19,
            ),
            position_id="trd-pre-filled",
            decision_id="dec-coll-filled",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-filled",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:00+00:00",
            payload=_submit_requested_payload("pre-cmd-filled"),
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-filled",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-04-26T00:00:01+00:00",
            payload={"venue_order_id": "fill-ord-001"},
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-filled",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:00:02+00:00",
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-collision-filled",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-coll-filled",
            )

        assert result.status == "pending", (
            f"Expected pending for FILLED collision, got {result.status!r}"
        )
        assert result.order_id == "fill-ord-001"
        assert result.external_order_id == "fill-ord-001"
        assert result.reason is not None and "prior attempt filled" in result.reason
        mock_inst.place_limit_order.assert_not_called()

    def test_idempotency_collision_with_rejected_state_returns_rejected(self, mem_conn):
        """Insert command in REJECTED state; collision must return status=rejected."""
        from src.execution.executor import _live_order
        from src.execution.command_bus import IdempotencyKey, IntentKind
        from src.state.venue_command_repo import insert_command, append_event

        intent = _make_entry_intent(mem_conn, token_id="tok-coll-rejected" + "0" * 23)

        idem = IdempotencyKey.from_inputs(
            decision_id="dec-coll-rejected",
            token_id=intent.token_id,
            side="BUY",
            price=intent.limit_price,
            size=18.19,
            intent_kind=IntentKind.ENTRY,
        )
        insert_command(
            mem_conn,
            command_id="pre-cmd-rejected",
            snapshot_id=intent.executable_snapshot_id,
            envelope_id=_ensure_envelope(
                mem_conn,
                token_id=intent.token_id,
                price=intent.limit_price,
                size=18.19,
            ),
            position_id="trd-pre-rejected",
            decision_id="dec-coll-rejected",
            idempotency_key=idem.value,
            intent_kind="ENTRY",
            market_id="mkt-test-001",
            token_id=intent.token_id,
            side="BUY",
            size=18.19,
            price=intent.limit_price,
            created_at="2026-04-26T00:00:00+00:00",
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-rejected",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:00+00:00",
            payload=_submit_requested_payload("pre-cmd-rejected"),
        )
        append_event(
            mem_conn,
            command_id="pre-cmd-rejected",
            event_type="SUBMIT_REJECTED",
            occurred_at="2026-04-26T00:00:01+00:00",
            payload={"reason": "v2_preflight_failed"},
        )
        mem_conn.commit()

        with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            mock_inst.v2_preflight.return_value = None

            result = _live_order(
                trade_id="trd-collision-rejected",
                intent=intent,
                shares=18.19,
                conn=mem_conn,
                decision_id="dec-coll-rejected",
            )

        assert result.status == "rejected", (
            f"Expected rejected for REJECTED collision, got {result.status!r}"
        )
        assert result.reason is not None and "prior attempt" in result.reason
        mock_inst.place_limit_order.assert_not_called()


# ---------------------------------------------------------------------------
# Max slippage budget enforcement
# ---------------------------------------------------------------------------

def test_create_execution_intent_rejects_reprice_above_max_slippage():
    from src.contracts import EdgeContext, EntryMethod
    from src.execution.executor import create_execution_intent
    from src.types.market import Bin, BinEdge
    import numpy as np

    edge = BinEdge(
        bin=Bin(low=39, high=40, label="39-40°F", unit="F"),
        direction="buy_yes",
        edge=0.20,
        ci_lower=0.03,
        ci_upper=0.31,
        p_model=0.70,
        p_market=0.50,
        p_posterior=0.70,
        entry_price=0.50,
        p_value=0.01,
        vwmp=0.50,
        forward_edge=0.20,
    )
    edge_context = EdgeContext(
        p_raw=np.array([0.70]),
        p_cal=np.array([0.70]),
        p_market=np.array([0.50]),
        p_posterior=0.70,
        forward_edge=0.20,
        alpha=1.0,
        confidence_band_upper=0.31,
        confidence_band_lower=0.03,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="test-snap",
        n_edges_found=1,
        n_edges_after_fdr=1,
    )

    with pytest.raises(ValueError, match="MAX_SLIPPAGE_EXCEEDED"):
        create_execution_intent(
            edge_context=edge_context,
            edge=edge,
            size_usd=5.0,
            mode="opening_hunt",
            market_id="m1",
            token_id="yes-token",
            no_token_id="no-token",
            repriced_limit_price=0.511,
            executable_snapshot_id="snap-limit",
            executable_snapshot_min_tick_size=Decimal("0.01"),
            executable_snapshot_min_order_size=Decimal("0.01"),
            executable_snapshot_neg_risk=False,
        )


# ---------------------------------------------------------------------------
# MAJOR-2 WARNING: synthetic decision_id emits warning and fails closed
# ---------------------------------------------------------------------------

def test_synthetic_decision_id_emits_warning(mem_conn, caplog):
    """When decision_id is empty, executor emits WARNING and rejects live submit."""
    from src.execution.executor import _live_order
    import logging

    intent = _make_entry_intent(mem_conn)

    with patch("src.data.polymarket_client.PolymarketClient") as MockClient:
        mock_inst = MagicMock()
        MockClient.return_value = mock_inst
        mock_inst.v2_preflight.return_value = None
        bound = _capture_bound_submission_envelope(mock_inst)
        mock_inst.place_limit_order.side_effect = (
            lambda **kwargs: _final_submit_result(bound, order_id="ord-synth-001")
        )

        with patch("src.execution.executor.alert_trade", lambda **kw: None):
            with caplog.at_level(logging.WARNING, logger="src.execution.executor"):
                result = _live_order(
                    trade_id="trd-synth",
                    intent=intent,
                    shares=18.19,
                    conn=mem_conn,
                    decision_id="",  # empty => synthetic
                )

    assert result.status == "rejected"
    assert result.reason == "entry_decision_identity:missing_durable_live_entry_decision_id"
    assert result.order_id is None
    mock_inst.place_limit_order.assert_not_called()
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    synth_warnings = [m for m in warning_messages if "synthetic decision_id" in m]
    assert len(synth_warnings) >= 1, (
        f"Expected WARNING about synthetic decision_id, got: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# MEDIUM-3 payload shape: v2_preflight SUBMIT_REJECTED payload
# ---------------------------------------------------------------------------

def test_v2_preflight_payload_shape(mem_conn, monkeypatch):
    """V2 preflight failure must write SUBMIT_REJECTED with payload {{reason: v2_preflight_failed}}."""
    from src.execution.executor import _live_order
    from src.data.polymarket_client import V2PreflightError
    from src.state.venue_command_repo import get_command, list_events
    import src.execution.executor as executor_module

    intent = _make_entry_intent(mem_conn)
    command_ids_seen: list[str] = []
    monkeypatch.setattr(
        executor_module,
        "_entry_actionable_certificate_payload_and_component",
        lambda *args, **kwargs: (
            {"component": "entry_actionable_certificate", "allowed": True, "reason": "test"},
            {"actionable_certificate_hash": "hash-test"},
        ),
    )
    monkeypatch.setattr(
        executor_module,
        "_entry_control_pause_component",
        lambda *args, **kwargs: {
            "component": "entries_pause_control_override",
            "allowed": True,
            "reason": "not_paused",
        },
    )
    monkeypatch.setattr(
        executor_module,
        "_entry_duplicate_same_token_component",
        lambda *args, **kwargs: {
            "component": "entry_duplicate_same_token",
            "allowed": True,
            "reason": "no_duplicate",
        },
    )
    monkeypatch.setattr(
        executor_module,
        "_entry_same_token_cooldown_component",
        lambda *args, **kwargs: {
            "component": "entry_same_token_cooldown",
            "allowed": True,
            "reason": "not_in_cooldown",
        },
    )
    monkeypatch.setattr(
        executor_module,
        "_entry_decision_source_component",
        lambda *args, **kwargs: {
            "component": "entry_decision_source",
            "allowed": True,
            "reason": "valid",
        },
    )

    import src.state.venue_command_repo as _repo
    _real_insert = _repo.insert_command

    def capturing_insert(*args, **kwargs):
        command_ids_seen.append(kwargs["command_id"])
        return _real_insert(*args, **kwargs)

    with patch(
        "src.state.venue_command_repo.insert_command", side_effect=capturing_insert
    ), patch("src.data.polymarket_client.PolymarketClient") as MockClient:
        mock_inst = MagicMock()
        MockClient.return_value = mock_inst
        mock_inst.v2_preflight.side_effect = V2PreflightError("endpoint down")

        result = _live_order(
            trade_id="trd-v2pf-payload",
            intent=intent,
            shares=18.19,
            conn=mem_conn,
            decision_id="dec-v2pf-payload",
        )

    assert result.status == "rejected"
    assert len(command_ids_seen) == 1

    events = list_events(mem_conn, command_ids_seen[0])
    rejected_events = [e for e in events if e["event_type"] == "SUBMIT_REJECTED"]
    assert len(rejected_events) == 1

    import json
    payload = rejected_events[0].get("payload_json") or "{}"
    payload_dict = json.loads(payload)
    assert payload_dict.get("reason") == "v2_preflight_failed", (
        f"Expected payload {{\"reason\": \"v2_preflight_failed\"}}, got {payload_dict}"
    )


# ---------------------------------------------------------------------------
# INV-30 manifest test — all enforced_by.tests entries must be collect-able
# ---------------------------------------------------------------------------

def test_inv30_manifest_registered():
    """INV-30 must be in architecture/invariants.yaml with non-empty enforced_by.tests."""
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "architecture/invariants.yaml").read_text())
    by_id = {item["id"]: item for item in manifest["invariants"]}
    assert "INV-30" in by_id, "INV-30 missing from architecture/invariants.yaml"
    enforced_tests = (by_id["INV-30"].get("enforced_by") or {}).get("tests") or []
    assert len(enforced_tests) >= 5, (
        f"INV-30 must cite at least 5 enforcing tests, found {len(enforced_tests)}"
    )
