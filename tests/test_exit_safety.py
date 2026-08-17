# Created: 2026-04-27
# Last reused/audited: 2026-08-14
# Lifecycle: created=2026-04-27; last_reviewed=2026-08-14; last_reused=2026-08-14
# Authority basis: docs/operations/current/finite_evidence_probability_symmetry/PLAN.md
# Purpose: Lock R3 M4 cancel/replace exit mutex, typed cancel outcomes, replacement gates, and CTF preflight.
# Reuse: Run when exit_safety, executor exit submit, exit_lifecycle cancel retry, venue command transitions, or collateral sell preflight changes.
"""R3 M4 exit-safety antibodies for cancel/replace and exit mutex behavior."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import pytest

_NOW = datetime(2026, 4, 27, tzinfo=timezone.utc)
YES_TOKEN = "yes-token-001"
NO_TOKEN = f"{YES_TOKEN}-no"
_CTF_SCALE = 1_000_000


@pytest.fixture
def conn():
    from src.state.db import init_schema, init_schema_trade_only
    from src.state.collateral_ledger import init_collateral_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    init_schema_trade_only(c)
    init_collateral_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def allow_cancel_cutover_for_exit_safety_tests(monkeypatch):
    from src.control.cutover_guard import CutoverDecision, CutoverState

    monkeypatch.setattr(
        "src.execution.exit_safety.gate_for_intent",
        lambda _intent_kind: CutoverDecision(False, True, False, None, CutoverState.LIVE_ENABLED),
    )


def _ctf_units(shares: float) -> int:
    return int(round(float(shares) * _CTF_SCALE))


def _fresh_exit_collateral_payload(
    *,
    token_id: str = YES_TOKEN,
    shares: float = 50.0,
) -> dict[str, object]:
    units = _ctf_units(shares)
    return {
        "pusd_balance_micro": 1_000_000_000,
        "pusd_allowance_micro": 1_000_000_000,
        "usdc_e_legacy_balance_micro": 0,
        "ctf_token_balances_units": {token_id: units},
        "ctf_token_allowances_units": {token_id: units},
        "authority_tier": "CHAIN",
    }


def _execution_facts(conn, position_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT venue_status, terminal_exec_status, fill_price, shares, command_id
            FROM execution_fact
            WHERE position_id = ?
            ORDER BY intent_id
            """,
            (position_id,),
        ).fetchall()
    )


def _fake_submit_result(bound_envelope, *, order_id: str, status: str = "LIVE") -> dict:
    raw_payload = {"status": status, "orderID": order_id, "success": True}
    final = bound_envelope.with_updates(
        raw_response_json=json.dumps(raw_payload, sort_keys=True, separators=(",", ":")),
        order_id=order_id,
    )
    return {
        "success": True,
        "status": status,
        "orderID": order_id,
        "_venue_submission_envelope": final.to_dict(),
    }


def _snapshot(
    *,
    pusd: int = 100_000_000,
    ctf: dict[str, int | float] | None = None,
    captured_at: datetime | None = None,
):
    from src.state.collateral_ledger import CollateralSnapshot

    ctf_units = {token: _ctf_units(float(shares)) for token, shares in (ctf or {}).items()}
    return CollateralSnapshot(
        pusd_balance_micro=pusd,
        pusd_allowance_micro=pusd,
        usdc_e_legacy_balance_micro=0,
        ctf_token_balances=ctf_units,
        ctf_token_allowances=dict(ctf_units),
        reserved_pusd_for_buys_micro=0,
        reserved_tokens_for_sells={},
        captured_at=captured_at or datetime.now(timezone.utc),
        authority_tier="CHAIN",
    )


def _allow_risk_allocator_for_exit_tests() -> None:
    from src.control.heartbeat_supervisor import HeartbeatHealth
    from src.risk_allocator import GovernorState, RiskAllocator, configure_global_allocator

    configure_global_allocator(
        RiskAllocator(),
        GovernorState(
            current_drawdown_pct=0.0,
            heartbeat_health=HeartbeatHealth.HEALTHY,
            ws_gap_active=False,
            ws_gap_seconds=0,
            unknown_side_effect_count=0,
            reconcile_finding_count=0,
        ),
    )


def _enable_exit_submit_prereqs(c, monkeypatch, *, ctf_shares: float = 50.0) -> None:
    from src.state.collateral_ledger import CollateralLedger, configure_global_ledger

    ledger = CollateralLedger(c)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000, ctf={YES_TOKEN: ctf_shares}))
    configure_global_ledger(ledger)
    _allow_risk_allocator_for_exit_tests()
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.ws_gap_guard.assert_ws_allows_submit", lambda *args, **kwargs: None)


def _clear_exit_submit_prereqs() -> None:
    from src.risk_allocator import clear_global_allocator
    from src.state.collateral_ledger import configure_global_ledger

    clear_global_allocator()
    configure_global_ledger(None)


def _ensure_snapshot(
    c,
    *,
    token_id: str = YES_TOKEN,
    no_token_id: str | None = None,
    selected_outcome_token_id: str | None = None,
    outcome_label: str | None = None,
    snapshot_id: str | None = None,
    raw_orderbook_hash: str = "c" * 64,
    captured_at: datetime = _NOW,
    freshness_deadline: datetime | None = None,
    min_tick_size: Decimal | str = Decimal("0.01"),
    min_order_size: Decimal | str = Decimal("0.01"),
    active: bool = True,
    closed: bool = False,
    accepting_orders: bool | None = True,
    enable_orderbook: bool = True,
    orderbook_top_bid: Decimal | str | None = Decimal("0.49"),
    orderbook_top_ask: Decimal | str | None = Decimal("0.51"),
) -> str:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    snapshot_id = snapshot_id or f"snap-{token_id}"
    if get_snapshot(c, snapshot_id) is not None:
        return snapshot_id
    no_token = no_token_id or f"{token_id}-no"
    selected_token = selected_outcome_token_id or token_id
    selected_label = outcome_label or ("NO" if selected_token == no_token else "YES")
    insert_snapshot(
        c,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-test",
            event_id="event-test",
            event_slug="event-test",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=no_token,
            selected_outcome_token_id=selected_token,
            outcome_label=selected_label,
            enable_orderbook=enable_orderbook,
            active=active,
            closed=closed,
            accepting_orders=accepting_orders,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal(str(min_tick_size)),
            min_order_size=Decimal(str(min_order_size)),
            fee_details={
                "source": "test",
                "token_id": selected_token,
                "fee_rate_fraction": 0.0,
                "fee_rate_bps": 0.0,
                "fee_rate_source_field": "fee_rate_fraction",
                "fee_rate_raw_unit": "fraction",
            },
            token_map_raw={"YES": token_id, "NO": no_token},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=(
                Decimal(str(orderbook_top_bid)) if orderbook_top_bid is not None else None
            ),
            orderbook_top_ask=(
                Decimal(str(orderbook_top_ask)) if orderbook_top_ask is not None else None
            ),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash=raw_orderbook_hash,
            authority_tier="CLOB",
            captured_at=captured_at,
            freshness_deadline=freshness_deadline or captured_at + timedelta(days=365),
        ),
    )
    return snapshot_id


def _snapshot_hash(c, snapshot_id: str) -> str:
    from src.state.snapshot_repo import get_snapshot

    snapshot = get_snapshot(c, snapshot_id)
    assert snapshot is not None
    return snapshot.executable_snapshot_hash


def _ensure_envelope(
    c,
    *,
    token_id: str = YES_TOKEN,
    envelope_id: str | None = None,
    side: str = "SELL",
    price: float | Decimal = 0.49,
    size: float | Decimal = 10.0,
    order_type: str = "GTC",
    post_only: bool = True,
    order_id: str | None = None,
    signed_order_hash: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    price_dec = Decimal(str(price))
    size_dec = Decimal(str(size))
    envelope_id = envelope_id or hashlib.sha256(
        f"{token_id}:{side}:{price_dec}:{size_dec}:{order_type}:{post_only}".encode()
    ).hexdigest()
    if c.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone():
        return envelope_id
    insert_submission_envelope(
        c,
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
            order_type=order_type,
            post_only=post_only,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            neg_risk=False,
            fee_details={},
            canonical_pre_sign_payload_hash="d" * 64,
            signed_order=None,
            signed_order_hash=signed_order_hash,
            raw_request_hash="e" * 64,
            raw_response_json=None,
            order_id=order_id,
            trade_ids=(),
            transaction_hashes=(),
            error_code=error_code,
            error_message=error_message,
            captured_at=_NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    return envelope_id


def _insert_exit_command(
    c,
    *,
    command_id: str = "cmd-exit-1",
    position_id: str = "pos-1",
    token_id: str = YES_TOKEN,
    size: float = 10.0,
    price: float = 0.49,
    venue_order_id: str | None = None,
    created_at: datetime | None = None,
    order_type: str = "GTC",
    post_only: bool = True,
) -> None:
    from src.state.venue_command_repo import insert_command

    insert_command(
        c,
        command_id=command_id,
        snapshot_id=_ensure_snapshot(c, token_id=token_id),
        envelope_id=_ensure_envelope(
            c,
            token_id=token_id,
            side="SELL",
            price=price,
            size=size,
            order_type=order_type,
            post_only=post_only,
        ),
        position_id=position_id,
        decision_id=f"dec-{command_id}",
        idempotency_key=f"idem-{command_id}",
        intent_kind="EXIT",
        market_id=token_id,
        token_id=token_id,
        side="SELL",
        size=size,
        price=price,
        created_at=(created_at or _NOW).isoformat(),
        venue_order_id=venue_order_id,
    )


def _seed_exit_intent_event(
    c,
    *,
    position_id: str,
    shares: float,
    close_position: bool,
    occurred_at: datetime | None = None,
    order_id: str | None = None,
    reason: str = "",
) -> None:
    sequence_no = c.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events "
        "WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0]
    c.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, order_id, env
        ) VALUES (?, ?, 1, ?, 'EXIT_INTENT', ?, 'active', 'pending_exit',
                  'opening_inertia', 'tests.test_exit_safety', ?, ?, 'live')
        """,
        (
            f"{position_id}:exit_intent:{sequence_no}",
            position_id,
            sequence_no,
            (occurred_at or (_NOW - timedelta(microseconds=1))).isoformat(),
            json.dumps(
                {
                    "exit_intent_close_position": close_position,
                    "exit_intent_shares": shares,
                    **({"exit_intent_reason": reason} if reason else {}),
                },
                sort_keys=True,
            ),
            order_id,
        ),
    )


def _seed_canonical_position_identity(
    c,
    *,
    position_id: str,
    token_id: str,
    shares: float,
    direction: str | None = "buy_yes",
    no_token_id: str | None = None,
    exit_reason: str | None = None,
) -> None:
    c.execute(
        """
        INSERT OR IGNORE INTO position_current (
            position_id, phase, direction, token_id, no_token_id, shares,
            strategy_key, updated_at, temperature_metric, chain_state, exit_reason
        ) VALUES (?, 'day0_window', ?, ?, ?, ?, 'center_buy', ?, 'high', 'synced', ?)
        """,
        (
            position_id,
            direction,
            token_id,
            no_token_id if no_token_id is not None else f"{token_id}-no",
            shares,
            _NOW.isoformat(),
            exit_reason,
        ),
    )


def _seed_red_monitor_provenance(
    c,
    *,
    position_id: str,
    token_id: str = YES_TOKEN,
    shares: float = 10.0,
    direction: str = "buy_yes",
    no_token_id: str | None = None,
) -> None:
    _seed_canonical_position_identity(
        c,
        position_id=position_id,
        token_id=token_id,
        shares=shares,
        direction=direction,
        no_token_id=no_token_id,
    )
    c.execute(
        "UPDATE position_current SET exit_reason = 'red_force_exit' "
        "WHERE position_id = ?",
        (position_id,),
    )
    sequence_no = c.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events "
        "WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0]
    c.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'MONITOR_REFRESHED', ?, 'day0_window',
                  'day0_window', 'center_buy', 'src.engine.cycle_runtime', ?, 'live')
        """,
        (
            f"{position_id}:monitor_red:{sequence_no}",
            position_id,
            sequence_no,
            _NOW.isoformat(),
            json.dumps(
                {
                    "exit_decision_should_exit": True,
                    "exit_decision_reason": "RED_FORCE_EXIT",
                    "exit_decision_trigger": "RED_FORCE_EXIT",
                    "applied_validations": [
                        "red_force_exit",
                        "dt2_red_force_exit_sweep_actuated",
                    ],
                },
                sort_keys=True,
            ),
        ),
    )


def _seed_canonical_red_intent(
    c,
    *,
    position_id: str,
    token_id: str,
    shares: float,
    decision_id: str = "decision-red-intent",
    env: str = "live",
) -> None:
    _seed_canonical_position_identity(
        c,
        position_id=position_id,
        token_id=token_id,
        shares=shares,
    )
    c.execute(
        "UPDATE position_current SET exit_reason = 'red_force_exit' "
        "WHERE position_id = ?",
        (position_id,),
    )
    sequence_no = c.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events "
        "WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0]
    c.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            decision_id, source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'EXIT_INTENT', ?, 'day0_window',
                  'pending_exit', 'center_buy', ?,
                  'src.execution.exit_lifecycle', ?, ?)
        """,
        (
            f"{position_id}:exit_intent_red:{sequence_no}",
            position_id,
            sequence_no,
            _NOW.isoformat(),
            decision_id,
            json.dumps(
                {
                    "exit_intent_close_position": True,
                    "exit_intent_decision_id": decision_id,
                    "exit_intent_reason": "RED_FORCE_EXIT",
                    "exit_intent_shares": shares,
                    "exit_intent_token_id": token_id,
                },
                sort_keys=True,
            ),
            env,
        ),
    )


def _seed_hold_monitor_rows(c, *, position_id: str, count: int) -> None:
    sequence_no = c.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events "
        "WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0]
    rows = []
    payload = json.dumps(
        {
            "exit_decision_should_exit": False,
            "exit_decision_reason": "HOLD",
            "exit_decision_trigger": "MONITOR_REFRESH",
        },
        sort_keys=True,
    )
    for offset in range(count):
        rows.append(
            (
                f"{position_id}:monitor_hold:{sequence_no + offset}",
                position_id,
                sequence_no + offset,
                (_NOW + timedelta(microseconds=offset + 1)).isoformat(),
                payload,
            )
        )
    c.executemany(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'MONITOR_REFRESHED', ?, 'pending_exit',
                  'pending_exit', 'center_buy', 'src.engine.cycle_runtime', ?, 'live')
        """,
        rows,
    )


def _ack_exit(c, command_id: str = "cmd-exit-1", venue_order_id: str = "ord-1") -> None:
    from src.state.venue_command_repo import append_event

    append_event(
        c,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at=_NOW.isoformat(),
    )
    append_event(
        c,
        command_id=command_id,
        event_type="SUBMIT_ACKED",
        occurred_at=_NOW.isoformat(),
        payload={"venue_order_id": venue_order_id},
    )


def _seed_pending_exit_reprice_case(
    c,
    *,
    trade_id: str,
    command_id: str,
    order_id: str,
    reason: str,
    capital_certificate: dict[str, object] | None,
    created_at: datetime = _NOW,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        city="Singapore",
        cluster="Asia",
        target_date="2026-08-03",
        bin_label="33C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=3.6,
        entry_price=0.60,
        shares=6.0,
        chain_shares=6.0,
        cost_basis_usd=3.6,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        unit="C",
        env="live",
        order_status="filled",
    )
    intent = exit_lifecycle.ExitIntent(
        trade_id=trade_id,
        reason=reason,
        token_id=YES_TOKEN,
        shares=6.0,
        current_market_price=0.31,
        best_bid=0.30,
        exact_limit_price=0.31,
        submit_order_type="GTC",
        capital_certificate=capital_certificate,
    )
    exit_lifecycle._record_exit_intent_before_execution_gates(c, position, intent)
    _insert_exit_command(
        c,
        command_id=command_id,
        position_id=trade_id,
        token_id=YES_TOKEN,
        size=6.0,
        price=0.31,
        venue_order_id=order_id,
        created_at=created_at,
    )
    _ack_exit(c, command_id=command_id, venue_order_id=order_id)
    position.last_exit_order_id = order_id
    position.exit_state = "sell_pending"
    position.order_status = "sell_pending_confirmation"
    assert exit_lifecycle._dual_write_canonical_pending_exit_if_available(
        c,
        position,
        reason=reason,
        error="",
        event_type="EXIT_ORDER_POSTED",
    )
    return position


def test_global_maker_rest_pending_exit_ignores_bid_gap_before_deadline(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle

    monkeypatch.setattr(
        exit_lifecycle,
        "_utcnow",
        lambda: _NOW + timedelta(minutes=10),
    )
    position = _seed_pending_exit_reprice_case(
        conn,
        trade_id="pos-global-maker-reprice-before-deadline",
        command_id="cmd-global-maker-reprice-before-deadline",
        order_id="ord-global-maker-reprice-before-deadline",
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        capital_certificate={
            "execution_mode": "MAKER_REST",
            "fill_probability_source": "posterior_predictive_mean",
            "rest_deadline_minutes": 20.0,
        },
    )

    class FakeClob:
        def get_orderbook(self, token_id):
            assert token_id == YES_TOKEN
            return {
                "bids": [{"price": "0.30", "size": "6"}],
                "asks": [{"price": "0.31", "size": "6"}],
            }

        def cancel_order(self, _order_id):
            raise AssertionError("certified global maker rest must not cancel early")

    assert exit_lifecycle._is_canonical_global_maker_rest_exit(
        conn,
        position,
        order_id=position.last_exit_order_id,
        command_id="cmd-global-maker-reprice-before-deadline",
    )
    assert exit_lifecycle._cancel_stale_pending_exit_for_reprice(
        conn=conn,
        position=position,
        clob=FakeClob(),
        token_id=YES_TOKEN,
    ) is False
    assert position.exit_state == "sell_pending"
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0] == 1


def test_global_maker_rest_pending_exit_releases_at_deadline(conn, monkeypatch):
    from src.execution import exit_lifecycle

    position = _seed_pending_exit_reprice_case(
        conn,
        trade_id="pos-global-maker-reprice-at-deadline",
        command_id="cmd-global-maker-reprice-at-deadline",
        order_id="ord-global-maker-reprice-at-deadline",
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        capital_certificate={
            "execution_mode": "MAKER_REST",
            "fill_probability_source": "posterior_predictive_mean",
            "rest_deadline_minutes": 20.0,
        },
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_utcnow",
        lambda: _NOW + timedelta(minutes=20),
    )
    canceled = []

    class FakeClob:
        def get_orderbook(self, _token_id):
            return {
                "bids": [{"price": "0.30", "size": "6"}],
                "asks": [{"price": "0.31", "size": "6"}],
            }

        def cancel_order(self, order_id):
            canceled.append(order_id)
            return {"canceled": [order_id], "not_canceled": []}

    assert exit_lifecycle._cancel_stale_pending_exit_for_reprice(
        conn=conn,
        position=position,
        clob=FakeClob(),
        token_id=YES_TOKEN,
    ) is True
    assert canceled == [position.last_exit_order_id]
    assert position.exit_state == "retry_pending"
    rejected = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'EXIT_ORDER_REJECTED'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert "GLOBAL_SELL_REST_DEADLINE_ELAPSED" in rejected["payload_json"]


def test_non_global_pending_exit_still_reprices_on_same_bid_gap(conn):
    from src.execution import exit_lifecycle

    position = _seed_pending_exit_reprice_case(
        conn,
        trade_id="pos-generic-reprice-bid-gap",
        command_id="cmd-generic-reprice-bid-gap",
        order_id="ord-generic-reprice-bid-gap",
        reason="EXIT_PROBABILITY_DECAY",
        capital_certificate=None,
    )
    canceled = []

    class FakeClob:
        def get_orderbook(self, _token_id):
            return {
                "bids": [{"price": "0.30", "size": "6"}],
                "asks": [{"price": "0.31", "size": "6"}],
            }

        def cancel_order(self, order_id):
            canceled.append(order_id)
            return {"canceled": [order_id], "not_canceled": []}

    assert exit_lifecycle._cancel_stale_pending_exit_for_reprice(
        conn=conn,
        position=position,
        clob=FakeClob(),
        token_id=YES_TOKEN,
    ) is True
    assert canceled == [position.last_exit_order_id]
    rejected = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ? AND event_type = 'EXIT_ORDER_REJECTED'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert "SELL_REPRICE_BID_MOVED_AWAY" in rejected["payload_json"]


def test_global_maker_rest_terminal_fill_keeps_existing_terminal_path(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState

    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: _NOW)
    position = _seed_pending_exit_reprice_case(
        conn,
        trade_id="pos-global-maker-terminal-fill",
        command_id="cmd-global-maker-terminal-fill",
        order_id="ord-global-maker-terminal-fill",
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        capital_certificate={
            "execution_mode": "MAKER_REST",
            "fill_probability_source": "posterior_predictive_mean",
            "rest_deadline_minutes": 20.0,
        },
        created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
    )

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == position.last_exit_order_id
            return {
                "status": "CONFIRMED",
                "matched_size": "6.0",
                "remaining_size": "0",
                "avgPrice": "0.31",
            }

        def cancel_order(self, _order_id):
            raise AssertionError("terminal fill path must not cancel")

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        FakeClob(),
        conn=conn,
    )

    assert stats["filled"] == 1
    assert position.state == "economically_closed"


def test_cancel_canceled_array_success_creates_CANCEL_CONFIRMED(conn):
    from src.execution.exit_safety import parse_cancel_response, request_cancel_for_command
    from src.state.venue_command_repo import get_command, list_events

    raw = {"canceled": ["ord-1"], "not_canceled": []}
    parsed = parse_cancel_response(raw)
    assert parsed.status == "CANCELED"
    assert parsed.raw_response == raw

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)
    outcome = request_cancel_for_command(conn, "cmd-exit-1", lambda order_id: raw)

    assert outcome.status == "CANCELED"
    assert get_command(conn, "cmd-exit-1")["state"] == "CANCELLED"
    events = [event["event_type"] for event in list_events(conn, "cmd-exit-1")]
    assert "CANCEL_REQUESTED" in events
    assert "CANCEL_ACKED" in events


def test_cancel_order_id_string_response_creates_CANCEL_ACKED(conn):
    from src.execution.exit_safety import parse_cancel_response, request_cancel_for_command
    from src.state.venue_command_repo import get_command, list_events

    parsed = parse_cancel_response("ord-1")
    assert parsed.status == "CANCELED"
    assert parsed.raw_response == {"orderID": "ord-1", "status": "CANCELED"}

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)

    outcome = request_cancel_for_command(conn, "cmd-exit-1", lambda order_id: order_id)

    assert outcome.status == "CANCELED"
    assert get_command(conn, "cmd-exit-1")["state"] == "CANCELLED"
    assert [event["event_type"] for event in list_events(conn, "cmd-exit-1")][-2:] == [
        "CANCEL_REQUESTED",
        "CANCEL_ACKED",
    ]


def test_cancel_requested_persists_execution_capability_before_cancel_callable(conn):
    from src.execution.exit_safety import request_cancel_for_command

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)
    seen: list[str] = []

    def cancel(order_id: str):
        row = conn.execute(
            """
            SELECT payload_json
              FROM venue_command_events
             WHERE command_id = ?
               AND event_type = 'CANCEL_REQUESTED'
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            ("cmd-exit-1",),
        ).fetchone()
        assert row is not None
        payload = json.loads(row["payload_json"])
        capability = payload["execution_capability"]
        assert order_id == "ord-1"
        assert capability["schema_version"] == 1
        assert capability["action"] == "CANCEL"
        assert capability["intent_kind"] == "CANCEL"
        assert capability["mode"] == "cancel"
        assert capability["allowed"] is True
        assert len(capability["capability_id"]) == 32
        assert capability["command_id"] == "cmd-exit-1"
        assert capability["venue_order_id"] == "ord-1"
        assert {component["component"] for component in capability["components"]} >= {
            "cutover_guard",
            "cancel_command_identity",
            "venue_order_cancelability",
        }
        seen.append(capability["capability_id"])
        return {"canceled": [order_id], "not_canceled": []}

    outcome = request_cancel_for_command(conn, "cmd-exit-1", cancel)

    assert outcome.status == "CANCELED"
    assert len(seen) == 1


def test_cancel_caller_connection_commits_requested_before_cancel_callable(tmp_path, monkeypatch):
    from src.execution.exit_safety import request_cancel_for_command
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import get_connection, init_schema, init_schema_trade_only

    monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", "100")
    db_path = tmp_path / "cancel-caller-conn-durable.db"
    setup_conn = get_connection(db_path)
    init_schema(setup_conn)
    init_schema_trade_only(setup_conn)
    init_collateral_schema(setup_conn)
    _insert_exit_command(setup_conn, venue_order_id="ord-1")
    _ack_exit(setup_conn)
    setup_conn.commit()
    setup_conn.close()

    submit_conn = get_connection(db_path)
    init_schema(submit_conn)
    init_schema_trade_only(submit_conn)
    observed = {}

    def cancel(order_id: str):
        read_conn = get_connection(db_path)
        init_schema(read_conn)
        init_schema_trade_only(read_conn)
        try:
            row = read_conn.execute(
                """
                SELECT vc.state, vce.payload_json
                FROM venue_commands vc
                JOIN venue_command_events vce ON vce.command_id = vc.command_id
                WHERE vc.command_id = ?
                  AND vce.event_type = 'CANCEL_REQUESTED'
                ORDER BY vce.sequence_no DESC
                LIMIT 1
                """,
                ("cmd-exit-1",),
            ).fetchone()
        finally:
            read_conn.close()
        observed["row"] = row
        assert order_id == "ord-1"
        return {"canceled": [order_id], "not_canceled": []}

    try:
        outcome = request_cancel_for_command(submit_conn, "cmd-exit-1", cancel)
        assert not submit_conn.in_transaction
    finally:
        submit_conn.close()

    assert outcome.status == "CANCELED"
    assert observed["row"] is not None
    assert observed["row"]["state"] == "CANCEL_PENDING"
    payload = json.loads(observed["row"]["payload_json"])
    assert payload["venue_order_id"] == "ord-1"
    assert payload["execution_capability"]["action"] == "CANCEL"


def test_cancel_guard_blocks_before_cancel_callable_and_command_transition(conn, monkeypatch):
    from src.control.cutover_guard import CutoverDecision, CutoverPending, CutoverState
    from src.execution.exit_safety import request_cancel_for_command
    from src.state.venue_command_repo import list_events

    monkeypatch.setattr(
        "src.execution.exit_safety.gate_for_intent",
        lambda _intent_kind: CutoverDecision(False, False, False, "BLOCKED:CANCEL", CutoverState.BLOCKED),
    )
    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)

    with pytest.raises(CutoverPending, match="BLOCKED:CANCEL"):
        request_cancel_for_command(
            conn,
            "cmd-exit-1",
            lambda _order_id: (_ for _ in ()).throw(AssertionError("must not call cancel")),
        )

    assert [event["event_type"] for event in list_events(conn, "cmd-exit-1")] == [
        "INTENT_CREATED",
        "SUBMIT_REQUESTED",
        "SUBMIT_ACKED",
    ]


def test_cancel_not_canceled_dict_creates_CANCEL_FAILED_or_REVIEW_REQUIRED(conn):
    from src.execution.exit_safety import parse_cancel_response, request_cancel_for_command
    from src.state.venue_command_repo import get_command, list_events

    raw = {"canceled": [], "not_canceled": {"ord-1": "not found"}}
    parsed = parse_cancel_response(raw)
    assert parsed.status == "NOT_CANCELED"
    assert "ord-1" in (parsed.reason or "")

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)
    outcome = request_cancel_for_command(conn, "cmd-exit-1", lambda order_id: raw)

    assert outcome.status == "NOT_CANCELED"
    assert get_command(conn, "cmd-exit-1")["state"] == "REVIEW_REQUIRED"
    assert "CANCEL_FAILED" in [event["event_type"] for event in list_events(conn, "cmd-exit-1")]


def test_cancel_already_canceled_not_canceled_dict_is_terminal_cancel(conn):
    from src.execution.exit_safety import parse_cancel_response, request_cancel_for_command
    from src.state.venue_command_repo import get_command, list_events

    raw = {"canceled": [], "not_canceled": {"ord-1": "the order is already canceled"}}
    parsed = parse_cancel_response(raw)
    assert parsed.status == "CANCELED"
    assert parsed.reason == "already_canceled_terminal"

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)
    outcome = request_cancel_for_command(conn, "cmd-exit-1", lambda order_id: raw)

    assert outcome.status == "CANCELED"
    assert get_command(conn, "cmd-exit-1")["state"] == "CANCELLED"
    events = [event["event_type"] for event in list_events(conn, "cmd-exit-1")]
    assert "CANCEL_ACKED" in events
    assert "CANCEL_FAILED" not in events


def test_cancel_already_canceled_or_matched_dict_stays_review_required(conn):
    from src.execution.exit_safety import parse_cancel_response, request_cancel_for_command
    from src.state.venue_command_repo import get_command, list_events

    raw = {
        "canceled": [],
        "not_canceled": {"ord-1": "order can't be found - already canceled or matched"},
    }
    parsed = parse_cancel_response(raw)
    assert parsed.status == "NOT_CANCELED"
    assert "matched" in (parsed.reason or "")

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)
    outcome = request_cancel_for_command(conn, "cmd-exit-1", lambda order_id: raw)

    assert outcome.status == "NOT_CANCELED"
    assert get_command(conn, "cmd-exit-1")["state"] == "REVIEW_REQUIRED"
    events = [event["event_type"] for event in list_events(conn, "cmd-exit-1")]
    assert "CANCEL_FAILED" in events
    assert "CANCEL_ACKED" not in events


def test_cancel_network_timeout_creates_CANCEL_UNKNOWN(conn):
    from src.execution.exit_safety import can_submit_replacement_sell, request_cancel_for_command
    from src.state.venue_command_repo import get_command, list_events

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)

    def timeout(_order_id: str):
        raise TimeoutError("cancel timed out")

    outcome = request_cancel_for_command(conn, "cmd-exit-1", timeout)

    assert outcome.status == "UNKNOWN"
    assert get_command(conn, "cmd-exit-1")["state"] == "REVIEW_REQUIRED"
    events = list_events(conn, "cmd-exit-1")
    event_types = [event["event_type"] for event in events]
    requested_payload = json.loads(
        next(event["payload_json"] for event in events if event["event_type"] == "CANCEL_REQUESTED")
    )
    assert requested_payload["execution_capability"]["allowed"] is True
    assert requested_payload["execution_capability"]["venue_order_id"] == "ord-1"
    assert event_types[-2:] == ["CANCEL_REQUESTED", "CANCEL_REPLACE_BLOCKED"]
    allowed, reason = can_submit_replacement_sell(conn, "pos-1", YES_TOKEN)
    assert allowed is False
    assert "cancel_unknown_requires_m5" in (reason or "")


def test_cancel_pending_without_capability_fails_closed_without_duplicate_request(conn):
    from src.execution.exit_safety import request_cancel_for_command
    from src.state.venue_command_repo import append_event, get_command, list_events

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)
    append_event(
        conn,
        command_id="cmd-exit-1",
        event_type="CANCEL_REQUESTED",
        occurred_at=_NOW.isoformat(),
        payload={"venue_order_id": "ord-1"},
    )

    outcome = request_cancel_for_command(
        conn,
        "cmd-exit-1",
        lambda _order_id: (_ for _ in ()).throw(AssertionError("must not call cancel without proof")),
    )

    events = [event["event_type"] for event in list_events(conn, "cmd-exit-1")]
    assert outcome.status == "UNKNOWN"
    assert outcome.reason == "missing_cancel_capability_proof"
    assert events.count("CANCEL_REQUESTED") == 1
    assert events[-1] == "CANCEL_REPLACE_BLOCKED"
    assert get_command(conn, "cmd-exit-1")["state"] == "REVIEW_REQUIRED"


def test_review_required_cancel_request_is_blocked_without_illegal_event(conn):
    from src.execution.exit_safety import request_cancel_for_command
    from src.state.venue_command_repo import append_event, get_command, list_events

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)
    append_event(
        conn,
        command_id="cmd-exit-1",
        event_type="CANCEL_REQUESTED",
        occurred_at=_NOW.isoformat(),
        payload={"venue_order_id": "ord-1"},
    )
    append_event(
        conn,
        command_id="cmd-exit-1",
        event_type="CANCEL_FAILED",
        occurred_at=_NOW.isoformat(),
        payload={
            "venue_order_id": "ord-1",
            "reason": "matched orders can't be canceled",
            "cancel_outcome": {
                "status": "NOT_CANCELED",
                "errorMessage": "matched orders can't be canceled",
            },
        },
    )
    before_events = [event["event_type"] for event in list_events(conn, "cmd-exit-1")]

    outcome = request_cancel_for_command(
        conn,
        "cmd-exit-1",
        lambda _order_id: (_ for _ in ()).throw(AssertionError("must not cancel REVIEW_REQUIRED")),
    )

    after_events = [event["event_type"] for event in list_events(conn, "cmd-exit-1")]
    assert outcome.status == "UNKNOWN"
    assert outcome.reason == "state_not_cancel_requestable:REVIEW_REQUIRED"
    assert after_events == before_events
    assert get_command(conn, "cmd-exit-1")["state"] == "REVIEW_REQUIRED"


def test_CANCEL_UNKNOWN_blocks_replacement(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.execution.exit_safety import request_cancel_for_command
    from src.state.collateral_ledger import CollateralLedger, configure_global_ledger

    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000, ctf={YES_TOKEN: 50}))
    configure_global_ledger(ledger)
    _allow_risk_allocator_for_exit_tests()
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.ws_gap_guard.assert_ws_allows_submit", lambda *args, **kwargs: None)

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("replacement must block before SDK construction")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientShouldNotBeConstructed)
    try:
        _insert_exit_command(conn, venue_order_id="ord-1")
        _ack_exit(conn)
        request_cancel_for_command(
            conn,
            "cmd-exit-1",
            lambda _order_id: (_ for _ in ()).throw(TimeoutError("cancel timed out")),
        )

        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-1",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
            ),
            conn=conn,
            decision_id="replacement-after-unknown",
        )
        assert result.status == "rejected"
        assert "cancel_unknown_requires_m5" in (result.reason or "")
        assert conn.execute("SELECT COUNT(*) FROM venue_commands WHERE position_id = ?", ("pos-1",)).fetchone()[0] == 1
    finally:
        from src.risk_allocator import clear_global_allocator

        clear_global_allocator()
        configure_global_ledger(None)


def test_partial_fill_plus_cancel_remainder_updates_remaining_shares(conn):
    from src.execution.exit_safety import remaining_exit_shares, request_cancel_for_command
    from src.state.venue_command_repo import append_event, append_order_fact, get_command

    _insert_exit_command(conn, venue_order_id="ord-1")
    _ack_exit(conn)
    append_event(
        conn,
        command_id="cmd-exit-1",
        event_type="PARTIAL_FILL_OBSERVED",
        occurred_at=_NOW.isoformat(),
        payload={"filled_size": "4.00", "remaining_size": "6.00", "venue_order_id": "ord-1"},
    )
    append_order_fact(
        conn,
        venue_order_id="ord-1",
        command_id="cmd-exit-1",
        state="PARTIALLY_MATCHED",
        remaining_size="6.00",
        matched_size="4.00",
        source="FAKE_VENUE",
        observed_at=_NOW,
        raw_payload_hash="f" * 64,
        raw_payload_json={"remaining_size": "6.00", "matched_size": "4.00"},
    )

    assert remaining_exit_shares(conn, "cmd-exit-1") == Decimal("6.00")
    outcome = request_cancel_for_command(conn, "cmd-exit-1", lambda _order_id: {"canceled": ["ord-1"]})
    assert outcome.status == "CANCELED"
    assert get_command(conn, "cmd-exit-1")["state"] == "CANCELLED"
    assert remaining_exit_shares(conn, "cmd-exit-1") == Decimal("6.00")


def test_exit_lifecycle_partial_fill_reduces_open_position_exposure(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-partial-exit",
        market_id="mkt-partial-exit",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-partial-exit",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-partial-exit",
        last_monitor_market_price=0.45,
        last_monitor_best_bid=0.44,
    )
    portfolio = PortfolioState(positions=[position])
    _insert_exit_command(
        conn,
        command_id="cmd-partial-exit",
        position_id=position.trade_id,
        size=20.0,
        price=0.44,
        venue_order_id="ord-partial-exit",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-partial-exit"
            return {
                "status": "PARTIALLY_MATCHED",
                "remaining_size": "12.00",
                "matched_size": "8.00",
                "avgPrice": "0.44",
            }

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert position.state == "pending_exit"
    assert position.exit_state == "sell_pending"
    assert position.shares == pytest.approx(12.0)
    assert position.size_usd == pytest.approx(6.0)
    assert position.cost_basis_usd == pytest.approx(6.0)
    assert position.nested_fills[-1]["type"] == "partial_exit_fill"
    assert position.nested_fills[-1]["filled_shares"] == pytest.approx(8.0)
    assert position.nested_fills[-1]["remaining_shares"] == pytest.approx(12.0)
    assert position.nested_fills[-1]["realized_pnl"] == pytest.approx(-0.48)
    facts = _execution_facts(conn, position.trade_id)
    assert len(facts) == 1
    assert facts[0]["venue_status"] == "PARTIALLY_MATCHED"
    assert facts[0]["terminal_exec_status"] == "PARTIALLY_MATCHED"
    assert facts[0]["fill_price"] == pytest.approx(0.44)
    assert facts[0]["shares"] == pytest.approx(8.0)
    assert facts[0]["command_id"] == "cmd-partial-exit"
    current = conn.execute(
        """
        SELECT shares, size_usd, cost_basis_usd, phase
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert current is not None
    assert current["shares"] == pytest.approx(12.0)
    assert current["size_usd"] == pytest.approx(6.0)
    assert current["cost_basis_usd"] == pytest.approx(6.0)
    assert current["phase"] == "pending_exit"
    event = conn.execute(
        """
        SELECT event_type, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert event is not None
    assert event["event_type"] == "MONITOR_REFRESHED"
    payload = json.loads(event["payload_json"])
    assert payload["semantic_event"] == "CAPITAL_REDUCTION_FILLED"
    assert payload["economic_fill_identity"] == (
        "status-fill:v1:pos-partial-exit:ord-partial-exit"
    )


@pytest.mark.parametrize(
    ("payload", "intended_shares", "expected"),
    (
        ({"size_matched": "2.50"}, "6", Decimal("2.50")),
        (
            {"original_size": "6", "remaining_size": "3.75"},
            "6",
            Decimal("2.25"),
        ),
        ({"size_matched": "7"}, "6", None),
        ({"status": "CONFIRMED"}, "6", None),
    ),
)
def test_confirmed_reduction_fill_size_requires_exact_venue_quantity(
    payload, intended_shares, expected
):
    from src.execution import exit_lifecycle

    assert exit_lifecycle._confirmed_reduction_fill_shares(
        payload,
        intended_shares=Decimal(intended_shares),
    ) == expected


def test_direct_order_intent_binding_fails_closed_on_conflicting_authority(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-direct-intent-conflict",
        market_id="mkt-direct-intent-conflict",
        city="Beijing",
        cluster="Asia",
        target_date="2026-07-27",
        bin_label="34C",
        direction="buy_no",
        size_usd=12.0,
        shares=20.0,
        cost_basis_usd=12.0,
        entry_price=0.60,
        p_posterior=0.20,
        state="pending_exit",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-direct-intent-conflict",
        unit="C",
        env="live",
        strategy_key="center_buy",
    )
    order_id = "ord-direct-intent-conflict"
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=6.0,
        close_position=False,
        order_id=order_id,
    )
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=20.0,
        close_position=True,
        order_id=order_id,
    )

    assert exit_lifecycle._canonical_reduction_intent_shares(
        conn,
        position,
        order_id=order_id,
    ) is None
    assert exit_lifecycle._canonical_full_exit_intent_shares(
        conn,
        position,
        order_id=order_id,
    ) is None


@pytest.mark.parametrize("filled_size", [10.0, 11.0])
def test_full_exit_fill_cannot_close_larger_current_holding(conn, filled_size):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_trade_fact

    position = Position(
        trade_id=f"pos-full-exit-holding-mismatch-{filled_size:g}",
        market_id="mkt-full-exit-holding-mismatch",
        city="Beijing",
        cluster="Asia",
        target_date="2026-07-27",
        bin_label="34C",
        direction="buy_no",
        size_usd=12.0,
        shares=20.0,
        chain_shares=20.0,
        cost_basis_usd=12.0,
        entry_price=0.60,
        p_posterior=0.20,
        state="pending_exit",
        exit_state="sell_pending",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-full-exit-holding-mismatch",
        unit="C",
        env="live",
        strategy_key="center_buy",
        order_status="sell_pending",
    )
    order_id = f"ord-full-exit-holding-mismatch-{filled_size:g}"
    command_id = f"cmd-full-exit-holding-mismatch-{filled_size:g}"
    position.last_exit_order_id = order_id
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=10.0,
        close_position=True,
    )
    _insert_exit_command(
        conn,
        command_id=command_id,
        position_id=position.trade_id,
        token_id=YES_TOKEN,
        size=10.0,
        price=0.50,
        venue_order_id=order_id,
    )
    _ack_exit(conn, command_id=command_id, venue_order_id=order_id)
    append_trade_fact(
        conn,
        trade_id=f"trade-full-exit-holding-mismatch-{filled_size:g}",
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size=str(filled_size),
        fill_price="0.50",
        source="REST",
        observed_at=_NOW.isoformat(),
        raw_payload_hash="f" * 64,
        raw_payload_json={"matched_size": str(filled_size)},
        tx_hash=f"0xholdingmismatch{filled_size:g}",
    )

    assert exit_lifecycle._exit_trade_fact_close_candidate(
        conn,
        position,
        exit_order_id=order_id,
    ) is None

    class FakeClob:
        def get_order_status(self, observed_order_id):
            assert observed_order_id == order_id
            return {
                "status": "CONFIRMED",
                "remaining_size": "0",
                "matched_size": str(filled_size),
                "avgPrice": "0.50",
            }

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        FakeClob(),
        conn=conn,
    )

    assert stats["filled"] == 0
    assert stats["unchanged"] == 1
    assert position.state == "pending_exit"
    assert position.shares == pytest.approx(20.0)


@pytest.mark.parametrize(
    ("condition_yes_won", "expected_pnl"),
    ((False, 4.4), (True, -11.6)),
)
def test_madrid_partial_exit_realized_pnl_is_canonical_and_settlement_adds_residual(
    conn, condition_yes_won, expected_pnl
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_trade_fact

    position = Position(
        trade_id="pos-capital-reduction",
        market_id="mkt-capital-reduction",
        city="Madrid",
        cluster="asia",
        target_date="2026-07-16",
        bin_label="30C",
        direction="buy_no",
        strategy_key="center_buy",
        size_usd=14.0,
        entry_price=0.70,
        shares=20.0,
        cost_basis_usd=14.0,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-capital-reduction",
        env="live",
        last_monitor_market_price=0.60,
    )
    intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=NO_TOKEN,
        shares=6.0,
        current_market_price=0.60,
        best_bid=0.60,
        close_position=False,
    )
    exit_lifecycle._record_exit_intent_before_execution_gates(conn, position, intent)
    intent_occurred_at = conn.execute(
        """
        SELECT occurred_at
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_INTENT'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()[0]
    command_created_at = (
        datetime.fromisoformat(intent_occurred_at.replace("Z", "+00:00"))
        + timedelta(microseconds=1)
    )
    _insert_exit_command(
        conn,
        command_id="cmd-capital-reduction",
        position_id=position.trade_id,
        token_id=NO_TOKEN,
        size=6.0,
        price=0.60,
        venue_order_id="ord-capital-reduction",
        created_at=command_created_at,
    )
    _ack_exit(
        conn,
        command_id="cmd-capital-reduction",
        venue_order_id="ord-capital-reduction",
    )
    position.last_exit_order_id = "ord-capital-reduction"
    position.exit_state = "sell_placed"
    position.order_status = "sell_placed"
    assert exit_lifecycle._dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=intent.reason,
        error="",
        event_type="EXIT_ORDER_POSTED",
    )
    assert exit_lifecycle._canonical_reduction_intent_shares(
        conn,
        position,
    ) == Decimal("6")
    assert exit_lifecycle._canonical_reduction_intent_shares(
        conn,
        position,
        order_id="ord-old-reduction",
    ) is None
    assert exit_lifecycle._canonical_reduction_intent_shares(
        conn,
        position,
        order_id="ord-capital-reduction",
    ) == Decimal("6")
    newer_full_close = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=NO_TOKEN,
        shares=20.0,
        current_market_price=0.60,
        best_bid=0.60,
        close_position=True,
    )
    exit_lifecycle._record_exit_intent_before_execution_gates(
        conn,
        position,
        newer_full_close,
    )
    assert exit_lifecycle._canonical_reduction_intent_shares(
        conn,
        position,
    ) is None
    assert exit_lifecycle._canonical_reduction_intent_shares(
        conn,
        position,
        order_id="ord-capital-reduction",
    ) == Decimal("6")
    append_trade_fact(
        conn,
        trade_id="trade-capital-reduction",
        venue_order_id="ord-capital-reduction",
        command_id="cmd-capital-reduction",
        state="CONFIRMED",
        filled_size="2.5",
        fill_price="0.60",
        source="REST",
        observed_at=datetime.now(timezone.utc).isoformat(),
        raw_payload_hash="a" * 64,
        raw_payload_json={"trade_id": "trade-capital-reduction"},
        tx_hash="0xcapitalreduction",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"must not poll confirmed reduction: {order_id}")

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        FakeClob(),
        conn=conn,
    )

    assert stats["reduced_from_trade_fact"] == 1
    assert position.state == "holding"
    assert position.exit_state == ""
    assert position.shares == pytest.approx(17.5)
    assert position.cost_basis_usd == pytest.approx(12.25)
    current = conn.execute(
        "SELECT phase, shares, cost_basis_usd FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    assert current["phase"] == "active"
    assert current["shares"] == pytest.approx(17.5)
    assert current["cost_basis_usd"] == pytest.approx(12.25)
    event = conn.execute(
        """
        SELECT event_type, phase_after, caused_by, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert event["event_type"] == "EXIT_RETRY_RELEASED"
    assert event["phase_after"] == "active"
    assert event["caused_by"] == "capital_reduction_filled"
    assert json.loads(event["payload_json"])["release_reason"] == (
        "CAPITAL_REDUCTION_FILLED"
    )
    reduction = conn.execute(
        """
        SELECT event_type, phase_after, payload_json
          FROM position_events
         WHERE position_id = ? AND caused_by = 'partial_exit_fill'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert reduction["event_type"] == "MONITOR_REFRESHED"
    assert reduction["phase_after"] == "pending_exit"
    reduction_payload = json.loads(reduction["payload_json"])
    assert reduction_payload["semantic_event"] == "CAPITAL_REDUCTION_FILLED"
    assert reduction_payload["fill_identity"] == (
        "economic-fill:v2:cmd-capital-reduction:ord-capital-reduction:"
        "trade-capital-reduction"
    )
    assert reduction_payload["economic_fill_identity"] == reduction_payload["fill_identity"]
    assert reduction_payload["economic_fill_cumulative_shares"] == "2.5"
    assert reduction_payload["filled_notional_usd"] == "1.5"
    assert reduction_payload["allocated_cost_basis_usd"] == "1.75"
    assert reduction_payload["realized_pnl_delta_usd"] == "-0.25"
    assert reduction_payload["cumulative_realized_pnl_usd"] == "-0.25"
    assert exit_lifecycle._complete_intentional_position_reduction(
        position,
        intended_shares=Decimal("6"),
        confirmed_filled_shares=Decimal("2.5"),
        fill_price=0.60,
        order_id="ord-capital-reduction",
        status="MATCHED",
        conn=conn,
    ) == Decimal("0")
    release_count = conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = ? AND caused_by = 'capital_reduction_filled'",
        (position.trade_id,),
    ).fetchone()[0]
    position.state = "pending_exit"
    position.pre_exit_state = "holding"
    position.exit_state = "retry_pending"
    position.order_status = "retry_pending"
    position.exit_reason = "DAY0_HARD_FACT_BIN_DEAD"
    position.last_exit_error = "exit_no_executable_bid"
    position.next_exit_retry_at = "2026-07-16T12:00:00+00:00"
    assert exit_lifecycle._complete_intentional_position_reduction(
        position,
        intended_shares=Decimal("6"),
        confirmed_filled_shares=Decimal("2.5"),
        fill_price=0.60,
        order_id="ord-capital-reduction",
        status="CONFIRMED",
        conn=conn,
    ) == Decimal("0")
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"
    assert position.last_exit_error == "exit_no_executable_bid"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = ? AND caused_by = 'capital_reduction_filled'",
        (position.trade_id,),
    ).fetchone()[0] == release_count
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = ? AND caused_by = 'partial_exit_fill'",
        (position.trade_id,),
    ).fetchone()[0] == 1
    assert exit_lifecycle._complete_intentional_position_reduction(
        position,
        intended_shares=Decimal("6"),
        confirmed_filled_shares=Decimal("4"),
        fill_price=0.60,
        order_id="ord-capital-reduction",
        status="CONFIRMED",
        conn=conn,
    ) == Decimal("1.5")
    partials = conn.execute(
        """
        SELECT payload_json FROM position_events
         WHERE position_id = ? AND caused_by = 'partial_exit_fill'
         ORDER BY sequence_no
        """,
        (position.trade_id,),
    ).fetchall()
    assert len(partials) == 2
    second_payload = json.loads(partials[-1]["payload_json"])
    assert second_payload["fill_identity"] == (
        "status-fill:v1:pos-capital-reduction:ord-capital-reduction"
    )
    assert second_payload["economic_fill_cumulative_shares"] == "4"
    assert second_payload["filled_notional_usd"] == "0.9"
    assert second_payload["allocated_cost_basis_usd"] == "1.05"
    assert second_payload["realized_pnl_delta_usd"] == "-0.15"
    assert second_payload["cumulative_realized_pnl_usd"] == "-0.4"
    assert position.shares == pytest.approx(16.0)
    assert position.cost_basis_usd == pytest.approx(11.2)
    open_head = conn.execute(
        "SELECT realized_pnl_usd FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    assert open_head["realized_pnl_usd"] == pytest.approx(-0.4)
    # A later monitor/restart projection has no close timestamp and therefore
    # builds NULL by default; it must preserve the partial-exit cumulative head.
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.db import append_many_and_project

    position.last_monitor_at = datetime.now(timezone.utc).isoformat()
    monitor_events, monitor_projection = build_monitor_refreshed_canonical_write(
        position,
        sequence_no=conn.execute(
            "SELECT MAX(sequence_no) + 1 FROM position_events WHERE position_id = ?",
            (position.trade_id,),
        ).fetchone()[0],
        phase_after="active",
        source_module="tests.test_exit_safety",
    )
    append_many_and_project(conn, monitor_events, monitor_projection)
    assert conn.execute(
        "SELECT realized_pnl_usd FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0] == pytest.approx(-0.4)
    assert conn.execute(
        """
        SELECT COUNT(*) FROM position_events
         WHERE position_id = ? AND event_type = 'EXIT_ORDER_FILLED'
        """,
        (position.trade_id,),
    ).fetchone()[0] == 0
    from src.execution.harvester import _settle_positions

    assert _settle_positions(
        conn,
        PortfolioState(positions=[position]),
        "Madrid",
        "2026-07-16",
        "30C",
        settlement_condition_id="condition-capital-reduction",
        settlement_condition_yes_won=condition_yes_won,
    ) == 1
    settled = conn.execute(
        "SELECT phase, shares, cost_basis_usd, realized_pnl_usd FROM position_current "
        "WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    assert settled["phase"] == "settled"
    assert settled["shares"] == pytest.approx(16.0)
    assert settled["cost_basis_usd"] == pytest.approx(11.2)
    assert settled["realized_pnl_usd"] == pytest.approx(expected_pnl)


def test_completed_partial_exit_does_not_block_residual_settlement(conn):
    """A 6/6 reduction fill is not a full-position close projection debt."""
    from src.execution import exit_lifecycle
    from src.execution.harvester import _settle_positions
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_trade_fact

    position = Position(
        trade_id="pos-complete-reduction-before-settlement",
        market_id="mkt-complete-reduction-before-settlement",
        city="Madrid",
        cluster="europe",
        target_date="2026-08-12",
        bin_label="30C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-complete-reduction-before-settlement",
        env="live",
        last_monitor_market_price=0.60,
    )
    intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=YES_TOKEN,
        shares=6.0,
        current_market_price=0.60,
        best_bid=0.60,
        close_position=False,
    )
    exit_lifecycle._record_exit_intent_before_execution_gates(conn, position, intent)
    occurred_at = conn.execute(
        "SELECT occurred_at FROM position_events WHERE position_id = ? "
        "AND event_type = 'EXIT_INTENT' ORDER BY sequence_no DESC LIMIT 1",
        (position.trade_id,),
    ).fetchone()[0]
    command_created_at = (
        datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        + timedelta(microseconds=1)
    )
    _insert_exit_command(
        conn,
        command_id="cmd-complete-reduction-before-settlement",
        position_id=position.trade_id,
        size=6.0,
        price=0.60,
        venue_order_id="ord-complete-reduction-before-settlement",
        created_at=command_created_at,
    )
    _ack_exit(
        conn,
        command_id="cmd-complete-reduction-before-settlement",
        venue_order_id="ord-complete-reduction-before-settlement",
    )
    position.last_exit_order_id = "ord-complete-reduction-before-settlement"
    position.exit_state = "sell_placed"
    position.order_status = "sell_placed"
    assert exit_lifecycle._dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=intent.reason,
        error="",
        event_type="EXIT_ORDER_POSTED",
    )
    append_trade_fact(
        conn,
        trade_id="trade-complete-reduction-before-settlement",
        venue_order_id=position.last_exit_order_id,
        command_id="cmd-complete-reduction-before-settlement",
        state="CONFIRMED",
        filled_size="6",
        fill_price="0.60",
        source="REST",
        observed_at=_NOW.isoformat(),
        raw_payload_hash="d" * 64,
        raw_payload_json={"size_matched": "6", "status": "CONFIRMED"},
    )

    class NoPoll:
        def get_order_status(self, order_id):
            raise AssertionError(f"confirmed reduction must not poll {order_id}")

    portfolio = PortfolioState(positions=[position])
    stats = exit_lifecycle.check_pending_exits(portfolio, NoPoll(), conn=conn)
    assert stats["reduced_from_trade_fact"] == 1
    assert position.state == "holding"
    assert position.shares == pytest.approx(14.0)
    candidate = exit_lifecycle._exit_trade_fact_close_candidate(conn, position)
    assert candidate is not None
    assert candidate["closes_position"] is False

    assert _settle_positions(
        conn,
        portfolio,
        "Madrid",
        "2026-08-12",
        "30C",
        settlement_condition_id=position.condition_id,
        settlement_condition_yes_won=False,
    ) == 1
    settled = conn.execute(
        "SELECT phase, shares FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    assert settled["phase"] == "settled"
    assert settled["shares"] == pytest.approx(14.0)


def test_settlement_accepts_chain_confirmed_partial_residual_over_stale_runtime(conn):
    """Chain may refine residual precision, not invent missing exit economics."""
    from src.engine.lifecycle_events import (
        build_chain_economics_observed_canonical_write,
        build_chain_size_corrected_canonical_write,
        build_position_current_projection,
    )
    from src.execution.harvester import _settle_positions
    from src.state.db import append_many_and_project
    from src.state.portfolio import PortfolioState, Position
    from src.state.projection import upsert_position_current

    stale_runtime = Position(
        trade_id="pos-chain-confirmed-partial-residual",
        market_id="mkt-chain-confirmed-partial-residual",
        city="Guangzhou",
        cluster="asia",
        target_date="2026-08-10",
        bin_label="35C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=5.0,
        entry_price=0.50,
        shares=10.0,
        cost_basis_usd=5.0,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-chain-confirmed-partial-residual",
        env="live",
        last_monitor_at=_NOW.isoformat(),
    )
    upsert_position_current(
        conn, build_position_current_projection(stale_runtime)
    )
    conn.execute(
        """INSERT INTO position_events (
               event_id, position_id, event_version, sequence_no, event_type,
               occurred_at, phase_before, phase_after, strategy_key,
               source_module, payload_json, order_id, caused_by, env
           ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, 'pending_exit', 'active',
                     'center_buy', 'tests.test_exit_safety', ?, ?,
                     'partial_exit_fill', 'live')""",
        (
            f"{stale_runtime.trade_id}:partial",
            stale_runtime.trade_id,
            _NOW.isoformat(),
            json.dumps(
                {
                    "semantic_event": "CAPITAL_REDUCTION_FILLED",
                    "economic_fill_identity": "chain-confirmed-partial-fill",
                    "filled_shares": "9.9955",
                    "filled_notional_usd": "5.9973",
                    "allocated_cost_basis_usd": "4.99775",
                    "realized_pnl_delta_usd": "0.99955",
                    "remaining_shares": "0.0045",
                    "remaining_cost_basis_usd": "0.00225",
                    "fill_price": "0.6",
                    "order_id": "ord-chain-confirmed-partial",
                },
                sort_keys=True,
            ),
            "ord-chain-confirmed-partial",
        ),
    )

    canonical = replace(
        stale_runtime,
        shares=0.004544,
        chain_shares=0.004544,
        size_usd=0.002272,
        cost_basis_usd=0.002272,
        chain_cost_basis_usd=0.002272,
        chain_state="synced",
        state="pending_exit",
        chain_verified_at=(_NOW + timedelta(seconds=1)).isoformat(),
    )
    events, projection = build_chain_size_corrected_canonical_write(
        canonical,
        local_shares_before=10.0,
        sequence_no=2,
        phase_after="pending_exit",
        source_module="src.state.chain_reconciliation",
    )
    append_many_and_project(conn, events, projection)

    rounded_projection = replace(
        canonical,
        shares=0.0045,
        chain_shares=0.0045,
        size_usd=0.00225,
        cost_basis_usd=0.00225,
        chain_cost_basis_usd=0.00225,
        chain_verified_at=(_NOW + timedelta(seconds=2)).isoformat(),
    )
    events, projection = build_chain_economics_observed_canonical_write(
        rounded_projection,
        chain_observed_at=rounded_projection.chain_verified_at,
        sequence_no=3,
        phase_after="pending_exit",
        chain_shares_before=0.004544,
        source_module="src.state.chain_reconciliation",
    )
    append_many_and_project(conn, events, projection)

    portfolio = PortfolioState(positions=[stale_runtime])
    assert _settle_positions(
        conn,
        portfolio,
        "Guangzhou",
        "2026-08-10",
        "35C",
        settlement_condition_id="condition-chain-confirmed-partial-residual",
        settlement_condition_yes_won=False,
    ) == 1

    settled = conn.execute(
        """SELECT phase, shares, cost_basis_usd, realized_pnl_usd
             FROM position_current
            WHERE position_id = ?""",
        (stale_runtime.trade_id,),
    ).fetchone()
    assert settled["phase"] == "settled"
    assert settled["shares"] == pytest.approx(0.004544)
    assert settled["cost_basis_usd"] == pytest.approx(0.002272)
    assert settled["realized_pnl_usd"] == pytest.approx(0.997278)
    assert portfolio.positions == []


def test_partial_exit_economic_fill_fold_dedups_tx_and_edli_aliases(conn):
    from src.state.fill_dedup import economic_exit_fills_for_position
    from src.state.venue_command_repo import append_trade_fact

    command_id = "cmd-partial-exit-alias"
    order_id = "ord-partial-exit-alias"
    position_id = "pos-partial-exit-alias"
    _insert_exit_command(
        conn,
        command_id=command_id,
        position_id=position_id,
        token_id=NO_TOKEN,
        size=3.0,
        price=0.61,
        venue_order_id=order_id,
    )
    common = {
        "venue_order_id": order_id,
        "command_id": command_id,
        "state": "CONFIRMED",
        "filled_size": "3",
        "fill_price": "0.61",
        "source": "REST",
        "observed_at": _NOW.isoformat(),
        "tx_hash": "0xpartial-exit-alias",
    }
    source_fact_id = append_trade_fact(
        conn,
        trade_id="child-trade",
        raw_payload_hash="1" * 64,
        raw_payload_json={"trade_id": "child-trade"},
        **common,
    )
    append_trade_fact(
        conn,
        trade_id="0xpartial-exit-alias",
        raw_payload_hash="2" * 64,
        raw_payload_json={"trade_id": "0xpartial-exit-alias"},
        **common,
    )
    append_trade_fact(
        conn,
        trade_id="edli-replay-alias",
        raw_payload_hash="3" * 64,
        raw_payload_json={
            "raw_fill_payload": {"source_trade_fact_id": source_fact_id}
        },
        **common,
    )

    fills = economic_exit_fills_for_position(conn, position_id)

    assert [(fill.trade_id, fill.quantity, fill.unit_price) for fill in fills] == [
        ("child-trade", Decimal("3"), Decimal("0.61"))
    ]


def test_partial_exit_economic_fill_requires_command_order_binding(conn):
    from src.state.fill_dedup import economic_exit_fills_for_position
    from src.state.venue_command_repo import append_trade_fact

    command_id = "cmd-partial-exit-order-binding"
    order_id = "ord-partial-exit-order-binding"
    position_id = "pos-partial-exit-order-binding"
    _insert_exit_command(
        conn,
        command_id=command_id,
        position_id=position_id,
        token_id=NO_TOKEN,
        size=3.0,
        price=0.61,
        venue_order_id=order_id,
    )
    for trade_id, observed_order_id in (
        ("trade-wrong-order", "ord-other"),
        ("trade-canonical-order", order_id),
    ):
        append_trade_fact(
            conn,
            trade_id=trade_id,
            venue_order_id=observed_order_id,
            command_id=command_id,
            state="CONFIRMED",
            filled_size="1",
            fill_price="0.61",
            source="REST",
            observed_at=_NOW.isoformat(),
            raw_payload_hash=("1" if observed_order_id == order_id else "2") * 64,
            raw_payload_json={"trade_id": trade_id},
            tx_hash=f"0x{trade_id}",
        )

    fills = economic_exit_fills_for_position(conn, position_id)

    assert [(fill.trade_id, fill.venue_order_id) for fill in fills] == [
        ("trade-canonical-order", order_id)
    ]
    assert fills[0].identity == (
        "economic-fill:v2:cmd-partial-exit-order-binding:"
        "ord-partial-exit-order-binding:trade-canonical-order"
    )


def test_partial_exit_fold_keeps_legacy_connection_settlement_compatible():
    from src.state.fill_dedup import partial_exit_realized_pnl_fold

    legacy_conn = sqlite3.connect(":memory:")
    try:
        assert partial_exit_realized_pnl_fold(legacy_conn, "legacy-position") == 0
    finally:
        legacy_conn.close()


@pytest.mark.parametrize("city", ["Madrid", "Paris"])
def test_partial_exit_decimal_fill_events_are_exact_and_batch_projected(
    conn, city, monkeypatch
):
    from src.execution import exit_lifecycle
    from src.state.fill_dedup import economic_exit_fills_for_position, partial_exit_realized_pnl_fold
    from src.state.portfolio import Position
    from src.state.venue_command_repo import append_trade_fact

    position_id = f"pos-decimal-batch-{city.lower()}"
    command_id = f"cmd-decimal-batch-{city.lower()}"
    order_id = f"ord-decimal-batch-{city.lower()}"
    position = Position(
        trade_id=position_id,
        market_id=f"mkt-{position_id}",
        city=city,
        cluster=city,
        target_date="2026-08-03",
        bin_label="30C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=1.0,
        entry_price=0.10,
        shares=10.0,
        cost_basis_usd=1.0,
        state="pending_exit",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id=f"condition-{position_id}",
        last_exit_order_id=order_id,
        last_monitor_at=_NOW.isoformat(),
    )
    _seed_exit_intent_event(
        conn,
        position_id=position_id,
        shares=2.0,
        close_position=False,
        order_id=order_id,
    )
    _insert_exit_command(
        conn,
        command_id=command_id,
        position_id=position_id,
        token_id=YES_TOKEN,
        size=2.0,
        price=0.65,
        venue_order_id=order_id,
    )
    append_trade_fact(
        conn,
        trade_id=f"trade-a-{city.lower()}",
        venue_order_id=order_id,
        command_id=command_id,
        state="MATCHED",
        filled_size="0.6",
        fill_price="0.6",
        source="REST",
        observed_at=_NOW.isoformat(),
        raw_payload_hash="a" * 64,
        raw_payload_json={"trade_id": f"trade-a-{city.lower()}"},
        tx_hash=f"0xa-{city.lower()}",
    )
    append_trade_fact(
        conn,
        trade_id=f"trade-b-{city.lower()}",
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size="0.7",
        fill_price="0.7",
        source="REST",
        observed_at=_NOW.isoformat(),
        raw_payload_hash="b" * 64,
        raw_payload_json={"trade_id": f"trade-b-{city.lower()}"},
        tx_hash=f"0xb-{city.lower()}",
    )
    fills = economic_exit_fills_for_position(conn, position_id, venue_order_id=order_id)
    append_calls: list[tuple[int, str]] = []
    from src.state import db as state_db

    real_append_many_and_project = state_db.append_many_and_project

    def append_many_and_project_once(conn_arg, events, projection):
        append_calls.append((len(events), str(projection["realized_pnl_usd"])))
        return real_append_many_and_project(conn_arg, events, projection)

    monkeypatch.setattr(
        state_db, "append_many_and_project", append_many_and_project_once
    )

    assert exit_lifecycle._complete_intentional_position_reduction(
        position,
        intended_shares=Decimal("2"),
        confirmed_filled_shares=Decimal("1.3"),
        fill_price=0.653846153846,
        order_id=order_id,
        status="MATCHED,CONFIRMED",
        conn=conn,
        economic_fills=fills,
    ) == Decimal("1.3")

    rows = conn.execute(
        "SELECT payload_json FROM position_events "
        "WHERE position_id = ? AND caused_by = 'partial_exit_fill' "
        "ORDER BY sequence_no",
        (position_id,),
    ).fetchall()
    assert len(rows) == 2
    payloads = [json.loads(row[0]) for row in rows]
    assert [(p["filled_shares"], p["filled_notional_usd"]) for p in payloads] == [
        ("0.6", "0.36"),
        ("0.7", "0.49"),
    ]
    assert [p["cumulative_realized_pnl_usd"] for p in payloads] == ["0.3", "0.72"]
    assert partial_exit_realized_pnl_fold(conn, position_id) == Decimal("0.72")
    assert append_calls == [(3, "0.72")]
    current = conn.execute(
        "SELECT shares, realized_pnl_usd FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    assert current["shares"] == pytest.approx(8.7)
    assert Decimal(str(current["realized_pnl_usd"])) == Decimal("0.72")


def test_status_first_receipt_is_reconciled_against_canonical_fill(conn):
    from src.execution import exit_lifecycle
    from src.state.fill_dedup import partial_exit_realized_pnl_fold
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_trade_fact

    position_id = "pos-status-first-canonical"
    command_id = "cmd-status-first-canonical"
    order_id = "ord-status-first-canonical"
    position = Position(
        trade_id=position_id,
        market_id="mkt-status-first-canonical",
        city="Madrid",
        cluster="Madrid",
        target_date="2026-08-03",
        bin_label="30C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=1.0,
        entry_price=0.10,
        shares=10.0,
        cost_basis_usd=1.0,
        state="pending_exit",
        exit_state="sell_pending",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-status-first-canonical",
        last_exit_order_id=order_id,
        last_monitor_at=_NOW.isoformat(),
        last_monitor_market_price=0.60,
    )
    _seed_exit_intent_event(
        conn,
        position_id=position_id,
        shares=2.0,
        close_position=False,
        order_id=order_id,
    )
    _insert_exit_command(
        conn,
        command_id=command_id,
        position_id=position_id,
        token_id=YES_TOKEN,
        size=2.0,
        price=0.60,
        venue_order_id=order_id,
    )
    _ack_exit(conn, command_id=command_id, venue_order_id=order_id)
    portfolio = PortfolioState(positions=[position])

    class StatusFirstClob:
        polls = 0

        def get_order_status(self, seen_order_id):
            assert seen_order_id == order_id
            self.polls += 1
            return {
                "status": "PARTIAL",
                "matched_size": "0.6",
                "remaining_size": "1.4",
                "avgPrice": 0.60,
            }

    clob = StatusFirstClob()
    first = exit_lifecycle.check_pending_exits(portfolio, clob, conn=conn)
    assert first["unchanged"] == 1
    assert clob.polls == 1
    assert position.shares == pytest.approx(9.4)
    before = conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = ? AND caused_by = 'partial_exit_fill'",
        (position_id,),
    ).fetchone()[0]
    assert before == 1
    status_payload = json.loads(
        conn.execute(
            "SELECT payload_json FROM position_events "
            "WHERE position_id = ? AND caused_by = 'partial_exit_fill'",
            (position_id,),
        ).fetchone()[0]
    )
    assert status_payload["economic_fill_identity"] == (
        f"status-fill:v1:{position_id}:{order_id}"
    )
    assert status_payload["filled_shares"] == "0.6"
    assert status_payload["filled_notional_usd"] == "0.36"
    append_trade_fact(
        conn,
        trade_id="trade-status-first-canonical",
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size="0.6",
        fill_price="0.60",
        source="REST",
        observed_at=_NOW.isoformat(),
        raw_payload_hash="c" * 64,
        raw_payload_json={"trade_id": "trade-status-first-canonical"},
        tx_hash="0xstatusfirstcanonical",
    )
    second = exit_lifecycle.check_pending_exits(portfolio, clob, conn=conn)
    assert second.get("reduced_from_trade_fact", 0) == 0
    assert clob.polls == 1
    assert position.state == "holding"
    assert position.exit_state == ""
    assert position.shares == pytest.approx(9.4)
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = ? AND caused_by = 'partial_exit_fill'",
        (position_id,),
    ).fetchone()[0] == before
    assert partial_exit_realized_pnl_fold(conn, position_id) == Decimal("0.3")
    economics_count = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? "
        "AND caused_by = 'partial_exit_fill'",
        (position_id,),
    ).fetchone()[0]
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? "
        "AND event_type = 'EXIT_RETRY_RELEASED'",
        (position_id,),
    ).fetchone()[0] == 1
    third = exit_lifecycle.check_pending_exits(portfolio, clob, conn=conn)
    assert third["pending_exit_scan_candidates"] == 0
    assert clob.polls == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? "
        "AND caused_by = 'partial_exit_fill'",
        (position_id,),
    ).fetchone()[0] == economics_count
    assert partial_exit_realized_pnl_fold(conn, position_id) == Decimal("0.3")


def test_status_prefix_multi_fill_growth_is_exactly_once_on_replay(conn):
    """A command-wide status prefix must not be re-consumed per trade id."""
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution import exit_lifecycle
    from src.state.fill_dedup import (
        EconomicExitFill,
        partial_exit_realized_pnl_fold,
        recorded_partial_exit_fill_cursors,
    )
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    position_id = "pos-status-multi-fill-growth"
    order_id = "ord-status-multi-fill-growth"
    position = Position(
        trade_id=position_id,
        market_id="mkt-status-multi-fill-growth",
        city="Madrid",
        cluster="Madrid",
        target_date="2026-08-03",
        bin_label="30C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=0.94,
        entry_price=0.10,
        shares=9.4,
        cost_basis_usd=0.94,
        state="pending_exit",
        exit_state="sell_pending",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-status-multi-fill-growth",
        last_exit_order_id=order_id,
        last_monitor_at=_NOW.isoformat(),
    )
    projection = build_position_current_projection(position)
    projection["realized_pnl_usd"] = "0.3"
    upsert_position_current(conn, projection)
    conn.execute(
        """INSERT INTO position_events (
               event_id, position_id, event_version, sequence_no, event_type,
               occurred_at, phase_before, phase_after, strategy_key,
               source_module, payload_json, order_id, caused_by, env
           ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, 'pending_exit',
                     'pending_exit', 'center_buy', 'tests.test_exit_safety',
                     ?, ?, 'partial_exit_fill', 'live')""",
        (
            f"{position_id}:status-prefix",
            position_id,
            _NOW.isoformat(),
            json.dumps(
                {
                    "semantic_event": "CAPITAL_REDUCTION_FILLED",
                    "economic_fill_identity": (
                        f"status-fill:v1:{position_id}:{order_id}"
                    ),
                    "economic_fill_cumulative_shares": "0.6",
                    "economic_fill_cumulative_notional_usd": "0.36",
                    "filled_shares": "0.6",
                    "filled_notional_usd": "0.36",
                    "allocated_cost_basis_usd": "0.06",
                    "realized_pnl_delta_usd": "0.3",
                    "remaining_shares": "9.4",
                    "remaining_cost_basis_usd": "0.94",
                    "fill_price": "0.6",
                },
                sort_keys=True,
            ),
            order_id,
        ),
    )
    fill_a = EconomicExitFill(
        identity="economic-fill:v2:cmd-status:ord-status:trade-a",
        command_id="cmd-status",
        venue_order_id=order_id,
        trade_id="trade-a",
        quantity=Decimal("0.4"),
        unit_price=Decimal("0.5"),
        notional=Decimal("0.2"),
    )
    fill_b = EconomicExitFill(
        identity="economic-fill:v2:cmd-status:ord-status:trade-b",
        command_id="cmd-status",
        venue_order_id=order_id,
        trade_id="trade-b",
        quantity=Decimal("0.2"),
        unit_price=Decimal("0.8"),
        notional=Decimal("0.16"),
    )

    assert exit_lifecycle._complete_intentional_position_reduction(
        position,
        intended_shares=Decimal("2"),
        confirmed_filled_shares=Decimal("0.6"),
        fill_price=Decimal("0.6"),
        order_id=order_id,
        status="MATCHED",
        conn=conn,
        economic_fills=[fill_a, fill_b],
        intent_holding_shares=Decimal("10"),
    ) == Decimal("0")
    assert position.state == "holding"
    grown_b = replace(
        fill_b,
        quantity=Decimal("0.4"),
        notional=Decimal("0.32"),
    )
    assert exit_lifecycle._complete_intentional_position_reduction(
        position,
        intended_shares=Decimal("2"),
        confirmed_filled_shares=Decimal("0.8"),
        fill_price=Decimal("0.65"),
        order_id=order_id,
        status="CONFIRMED",
        conn=conn,
        economic_fills=[fill_a, grown_b],
        intent_holding_shares=Decimal("10"),
    ) == Decimal("0.2")
    assert position.shares == pytest.approx(9.2)
    assert partial_exit_realized_pnl_fold(conn, position_id) == Decimal("0.44")
    assert recorded_partial_exit_fill_cursors(conn, position_id)[grown_b.identity] == (
        Decimal("0.4"),
        Decimal("0.32"),
    )
    economics_count = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? "
        "AND caused_by = 'partial_exit_fill'",
        (position_id,),
    ).fetchone()[0]
    assert exit_lifecycle._complete_intentional_position_reduction(
        position,
        intended_shares=Decimal("2"),
        confirmed_filled_shares=Decimal("0.8"),
        fill_price=Decimal("0.65"),
        order_id=order_id,
        status="CONFIRMED",
        conn=conn,
        economic_fills=[fill_a, grown_b],
        intent_holding_shares=Decimal("10"),
    ) == Decimal("0")
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? "
        "AND caused_by = 'partial_exit_fill'",
        (position_id,),
    ).fetchone()[0] == economics_count
    assert partial_exit_realized_pnl_fold(conn, position_id) == Decimal("0.44")


def test_status_first_projection_failure_leaves_local_and_canonical_unchanged(
    conn, monkeypatch
):
    from src.execution import exit_lifecycle
    from src.state import db as state_db
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-status-projection-crash",
        market_id="mkt-status-projection-crash",
        city="Paris",
        cluster="Paris",
        target_date="2026-08-03",
        bin_label="30C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=1.0,
        entry_price=0.10,
        shares=10.0,
        cost_basis_usd=1.0,
        state="pending_exit",
        exit_state="sell_pending",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-status-projection-crash",
        last_exit_order_id="ord-status-projection-crash",
        last_monitor_market_price=0.60,
    )
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=2.0,
        close_position=False,
        order_id=position.last_exit_order_id,
    )
    _insert_exit_command(
        conn,
        command_id="cmd-status-projection-crash",
        position_id=position.trade_id,
        token_id=YES_TOKEN,
        size=2.0,
        price=0.60,
        venue_order_id=position.last_exit_order_id,
    )

    class PartialClob:
        def get_order_status(self, _order_id):
            return {
                "status": "PARTIAL",
                "matched_size": "0.6",
                "remaining_size": "1.4",
                "avgPrice": "0.60",
            }

    before = dict(position.__dict__)

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("injected append_many_and_project crash")

    monkeypatch.setattr(state_db, "append_many_and_project", fail_projection)
    with pytest.raises(RuntimeError, match="injected append_many_and_project crash"):
        exit_lifecycle.check_pending_exits(
            PortfolioState(positions=[position]), PartialClob(), conn=conn
        )

    assert position.__dict__ == before
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = ? AND caused_by = 'partial_exit_fill'",
        (position.trade_id,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0] == 0
    assert _execution_facts(conn, position.trade_id) == []


def test_mixed_legacy_and_new_partial_exit_repair_is_cumulative_and_atomic(conn):
    from src.execution import exit_lifecycle
    from src.execution.harvester import _repair_legacy_partial_exit_economics
    from src.state.fill_dedup import (
        PartialExitEconomicDebtError,
        partial_exit_realized_pnl_fold,
    )
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.venue_command_repo import append_trade_fact

    position_id = "pos-mixed-legacy-new"
    position = Position(
        trade_id=position_id,
        market_id="mkt-mixed-legacy-new",
        city="Paris",
        cluster="Paris",
        target_date="2026-08-03",
        bin_label="30C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=8.3,
        entry_price=1.0,
        shares=8.3,
        cost_basis_usd=8.3,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-mixed-legacy-new",
        last_monitor_at=_NOW.isoformat(),
    )
    upsert_position_current(conn, build_position_current_projection(position))
    _insert_exit_command(
        conn,
        command_id="cmd-mixed-new",
        position_id=position_id,
        token_id=YES_TOKEN,
        size=0.5,
        price=0.90,
        venue_order_id="ord-mixed-new",
    )
    append_trade_fact(
        conn,
        trade_id="trade-mixed-new",
        venue_order_id="ord-mixed-new",
        command_id="cmd-mixed-new",
        state="CONFIRMED",
        filled_size="0.5",
        fill_price="0.90",
        source="REST",
        observed_at=_NOW.isoformat(),
        raw_payload_hash="d" * 64,
        raw_payload_json={"trade_id": "trade-mixed-new"},
        tx_hash="0xmixednew",
    )
    assert exit_lifecycle._dual_write_partial_exit_projection_if_available(
        conn,
        position,
        filled_shares=Decimal("0.5"),
        remaining_shares=Decimal("8.3"),
        fill_price=Decimal("0.90"),
        order_id="ord-mixed-new",
        status="CONFIRMED",
        fill_identity="economic-fill:v1:cmd-mixed-new:trade-mixed-new",
        economic_fill_identity="economic-fill:v1:cmd-mixed-new:trade-mixed-new",
        economic_fill_cumulative_shares=Decimal("0.5"),
        economic_fill_cumulative_notional_usd=Decimal("0.45"),
        filled_notional_usd=Decimal("0.45"),
        allocated_cost_basis_usd=Decimal("0.5"),
        realized_pnl_delta_usd=Decimal("-0.05"),
        cumulative_realized_pnl_usd=Decimal("-0.05"),
        semantic_event="CAPITAL_REDUCTION_FILLED",
    )
    sequence_no = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO position_events (
               event_id, position_id, event_version, sequence_no, event_type,
               occurred_at, phase_before, phase_after, strategy_key,
               source_module, payload_json, order_id, caused_by, env
           ) VALUES (?, ?, 1, ?, 'MONITOR_REFRESHED', ?, 'pending_exit', 'pending_exit',
                     'center_buy', 'tests.test_exit_safety', ?, ?, 'partial_exit_fill', 'live')""",
        (
            f"{position_id}:legacy-partial",
            position_id,
            sequence_no,
            _NOW.isoformat(),
            json.dumps(
                {
                    "semantic_event": "CAPITAL_REDUCTION_FILLED",
                    "filled_shares": "1.2",
                    "remaining_shares": "8.3",
                    "fill_price": "0.8",
                    "order_id": "ord-mixed-legacy",
                },
                sort_keys=True,
            ),
            "ord-mixed-legacy",
        ),
    )
    _insert_exit_command(
        conn,
        command_id="cmd-mixed-legacy",
        position_id=position_id,
        token_id=YES_TOKEN,
        size=1.2,
        price=0.80,
        venue_order_id="ord-mixed-legacy",
    )
    append_trade_fact(
        conn,
        trade_id="trade-mixed-legacy",
        venue_order_id="ord-mixed-legacy",
        command_id="cmd-mixed-legacy",
        state="CONFIRMED",
        filled_size="1.2",
        fill_price="0.80",
        source="REST",
        observed_at=_NOW.isoformat(),
        raw_payload_hash="e" * 64,
        raw_payload_json={"trade_id": "trade-mixed-legacy"},
        tx_hash="0xmixedlegacy",
    )

    _repair_legacy_partial_exit_economics(conn, position)

    assert partial_exit_realized_pnl_fold(conn, position_id) == Decimal("-0.29")
    repair_rows = conn.execute(
        "SELECT payload_json FROM position_events "
        "WHERE position_id = ? AND caused_by = 'partial_exit_economics_repair'",
        (position_id,),
    ).fetchall()
    assert len(repair_rows) == 1
    repair_payload = json.loads(repair_rows[0][0])
    assert repair_payload["cumulative_realized_pnl_usd"] == "-0.29"
    assert repair_payload["repaired_legacy_event_id"] == (
        f"{position_id}:legacy-partial"
    )
    assert Decimal(str(conn.execute(
        "SELECT realized_pnl_usd FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0])) == Decimal("-0.29")
    _repair_legacy_partial_exit_economics(conn, position)
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = ? AND caused_by = 'partial_exit_economics_repair'",
        (position_id,),
    ).fetchone()[0] == 1

    # A prior repair covers only its named legacy event. A later identity-less
    # event remains debt until its own canonical fill proves it.
    sequence_no = conn.execute(
        "SELECT MAX(sequence_no) + 1 FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO position_events (
               event_id, position_id, event_version, sequence_no, event_type,
               occurred_at, phase_before, phase_after, strategy_key,
               source_module, payload_json, order_id, caused_by, env
           ) VALUES (?, ?, 1, ?, 'MONITOR_REFRESHED', ?, 'pending_exit', 'pending_exit',
                     'center_buy', 'tests.test_exit_safety', ?, ?, 'partial_exit_fill', 'live')""",
        (
            f"{position_id}:later-legacy-partial",
            position_id,
            sequence_no,
            (_NOW + timedelta(seconds=1)).isoformat(),
            json.dumps(
                {
                    "semantic_event": "CAPITAL_REDUCTION_FILLED",
                    "filled_shares": "0.2",
                    "remaining_shares": "8.1",
                    "fill_price": "0.7",
                    "order_id": "ord-mixed-later-legacy",
                },
                sort_keys=True,
            ),
            "ord-mixed-later-legacy",
        ),
    )
    with pytest.raises(PartialExitEconomicDebtError, match="later-legacy-partial"):
        partial_exit_realized_pnl_fold(conn, position_id)
    with pytest.raises(PartialExitEconomicDebtError, match="later-legacy-partial"):
        _repair_legacy_partial_exit_economics(conn, position)
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = ? AND caused_by = 'partial_exit_economics_repair'",
        (position_id,),
    ).fetchone()[0] == 1
    assert Decimal(
        str(
            conn.execute(
                "SELECT realized_pnl_usd FROM position_current WHERE position_id = ?",
                (position_id,),
            ).fetchone()[0]
        )
    ) == Decimal("-0.29")


def test_tel_aviv_chain_reflected_residual_keeps_exact_decimal_pnl(conn):
    from src.execution import exit_lifecycle
    from src.execution.harvester import _settle_positions
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_trade_fact

    initial_shares = Decimal("157")
    initial_cost = Decimal("59.908")
    partial_fill = Decimal("156.6")
    partial_price = Decimal("0.11")
    residual_shares = initial_shares - partial_fill
    expected_whole_pnl = (
        partial_fill * partial_price + residual_shares - initial_cost
    )
    order_id = "ord-tel-aviv-exact-residual"
    command_id = "cmd-tel-aviv-exact-residual"

    position = Position(
        trade_id="pos-tel-aviv-exact-residual",
        market_id="mkt-tel-aviv-exact-residual",
        city="Tel Aviv",
        cluster="Tel Aviv",
        target_date="2026-08-03",
        bin_label="30C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=float(initial_cost),
        entry_price=float(initial_cost / initial_shares),
        shares=float(initial_shares),
        cost_basis_usd=float(initial_cost),
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-tel-aviv-exact-residual",
        env="live",
        fill_authority="venue_position_observed",
        chain_shares=float(initial_shares),
        chain_avg_price=float(initial_cost / initial_shares),
        chain_cost_basis_usd=float(initial_cost),
        chain_verified_at=_NOW.isoformat(),
        last_monitor_market_price=float(partial_price),
    )
    intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=YES_TOKEN,
        shares=156.8,
        current_market_price=float(partial_price),
        best_bid=float(partial_price),
        close_position=False,
        capital_certificate={"held_shares": "157"},
    )
    exit_lifecycle._record_exit_intent_before_execution_gates(
        conn,
        position,
        intent,
    )
    intent_occurred_at = conn.execute(
        "SELECT occurred_at FROM position_events "
        "WHERE position_id = ? AND event_type = 'EXIT_INTENT' "
        "ORDER BY sequence_no DESC LIMIT 1",
        (position.trade_id,),
    ).fetchone()[0]
    _insert_exit_command(
        conn,
        command_id=command_id,
        position_id=position.trade_id,
        token_id=YES_TOKEN,
        size=156.8,
        price=float(partial_price),
        venue_order_id=order_id,
        created_at=(
            datetime.fromisoformat(intent_occurred_at.replace("Z", "+00:00"))
            + timedelta(microseconds=1)
        ),
    )
    _ack_exit(conn, command_id=command_id, venue_order_id=order_id)
    position.last_exit_order_id = order_id
    position.exit_state = "sell_placed"
    position.order_status = "sell_placed"
    assert exit_lifecycle._dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=intent.reason,
        error="",
        event_type="EXIT_ORDER_POSTED",
    )

    class StatusFirstClob:
        polls = 0

        def get_order_status(self, seen_order_id):
            assert seen_order_id == order_id
            self.polls += 1
            return {
                "status": "PARTIAL",
                "matched_size": str(partial_fill),
                "remaining_size": ".2",
                "avgPrice": str(partial_price),
            }

    portfolio = PortfolioState(positions=[position])
    clob = StatusFirstClob()
    first = exit_lifecycle.check_pending_exits(portfolio, clob, conn=conn)
    assert first["unchanged"] == 1
    assert clob.polls == 1
    assert Decimal(str(position.effective_shares)) == residual_shares
    assert Decimal(str(position.chain_shares)) == residual_shares

    append_trade_fact(
        conn,
        trade_id="trade-tel-aviv-exact-residual",
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size=str(partial_fill),
        fill_price=str(partial_price),
        source="REST",
        observed_at=_NOW.isoformat(),
        raw_payload_hash=hashlib.sha256(b"tel-aviv-exact-residual").hexdigest(),
        raw_payload_json={"size_matched": str(partial_fill), "status": "CONFIRMED"},
    )
    second = exit_lifecycle.check_pending_exits(portfolio, clob, conn=conn)
    assert second.get("reduced_from_trade_fact", 0) == 0
    assert clob.polls == 1
    assert position.state == "holding"

    assert _settle_positions(
        conn,
        portfolio,
        "Tel Aviv",
        "2026-08-03",
        "30C",
        settlement_condition_id="condition-tel-aviv-exact-residual",
        settlement_condition_yes_won=True,
    ) == 1
    row = conn.execute(
        "SELECT realized_pnl_usd FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    assert row[0] is not None, row
    assert expected_whole_pnl == Decimal("-42.282")
    assert Decimal(str(row[0])) == expected_whole_pnl
    settled_event = conn.execute(
        "SELECT payload_json FROM position_events WHERE position_id = ? AND event_type = 'SETTLED'",
        (position.trade_id,),
    ).fetchone()
    assert Decimal(json.loads(settled_event[0])["pnl"]) == expected_whole_pnl


def test_harvester_partial_exit_debt_rolls_back_whole_settlement_event(
    conn, monkeypatch
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from src.execution import harvester
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.portfolio import PortfolioState, Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-harvester-partial-debt",
        market_id="mkt-harvester-partial-debt",
        city="Paris",
        cluster="Paris",
        target_date="2026-08-03",
        bin_label="30C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=5.0,
        entry_price=0.50,
        shares=10.0,
        cost_basis_usd=5.0,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-harvester-partial-debt",
        env="live",
        last_monitor_at=_NOW.isoformat(),
    )
    portfolio = PortfolioState(positions=[position])
    upsert_position_current(conn, build_position_current_projection(position))
    conn.execute(
        """INSERT INTO position_events (
               event_id, position_id, event_version, sequence_no, event_type,
               occurred_at, phase_before, phase_after, strategy_key,
               source_module, payload_json, order_id, caused_by, env
           ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, 'pending_exit', 'active',
                     'center_buy', 'tests.test_exit_safety', ?, ?, 'partial_exit_fill', 'live')""",
        (
            f"{position.trade_id}:legacy-partial",
            position.trade_id,
            _NOW.isoformat(),
            json.dumps(
                {
                    "semantic_event": "CAPITAL_REDUCTION_FILLED",
                    "filled_shares": "1",
                    "remaining_shares": "10",
                    "fill_price": "0.6",
                    "order_id": "ord-harvester-partial-debt",
                },
                sort_keys=True,
            ),
            "ord-harvester-partial-debt",
        ),
    )
    conn.execute("CREATE TABLE event_atomic_probe (kind TEXT NOT NULL)")

    @contextmanager
    def use_test_connection(*_args, **_kwargs):
        yield conn

    event = {
        "title": "Paris high temperature on August 3",
        "slug": "paris-high-2026-08-03",
        "markets": [{"question": "Will Paris be 30C?"}],
    }
    city = SimpleNamespace(
        name="Paris", settlement_unit="C", settlement_source="wunderground"
    )
    outcome = SimpleNamespace(yes_won=True, range_low=30, range_high=30)

    monkeypatch.setattr(harvester, "load_portfolio", lambda: portfolio)
    monkeypatch.setattr(harvester, "_fetch_settled_events", lambda: [event])
    monkeypatch.setattr(
        harvester,
        "_supplement_held_position_settlement_events",
        lambda _portfolio, events: events,
    )
    monkeypatch.setattr(
        harvester, "forecasts_connection_with_trades_flocked", use_test_connection
    )
    monkeypatch.setattr(
        harvester,
        "_preflight_harvester_stage2_db_shape",
        lambda *_args: {
            "stage2_status": "ready",
            "stage2_missing_trade_tables": [],
            "stage2_missing_shared_tables": [],
        },
    )
    monkeypatch.setattr(harvester, "get_tracker", lambda: SimpleNamespace())
    monkeypatch.setattr(harvester, "_match_city", lambda *_args: city)
    monkeypatch.setattr(harvester, "_extract_target_date", lambda _event: "2026-08-03")
    monkeypatch.setattr(harvester, "infer_temperature_metric", lambda *_args: "high")
    monkeypatch.setattr(
        harvester, "_extract_resolved_market_outcomes", lambda _event: [outcome]
    )
    monkeypatch.setattr(harvester, "_canonical_bin_label", lambda *_args: "30C")
    monkeypatch.setattr(harvester, "_lookup_settlement_obs", lambda *_args, **_kwargs: {})

    def write_truth(*_args, **_kwargs):
        conn.execute("INSERT INTO event_atomic_probe VALUES ('settlement_truth')")
        return {
            "authority": "VERIFIED",
            "winning_bin": "30C",
            "settlement_value": 30,
        }

    monkeypatch.setattr(harvester, "_write_settlement_truth", write_truth)
    monkeypatch.setattr(harvester, "_extract_all_bin_labels", lambda _event: ["30C"])
    monkeypatch.setattr(
        harvester,
        "_snapshot_contexts_for_market",
        lambda *_args: ([{
            "learning_snapshot_ready": True,
            "authority_level": "frozen_decision",
            "temperature_metric": "high",
        }], []),
    )
    monkeypatch.setattr(
        harvester, "_log_snapshot_context_resolution", lambda *_args, **_kwargs: None
    )

    def write_learning(*_args, **_kwargs):
        conn.execute("INSERT INTO event_atomic_probe VALUES ('learning_pair')")
        return 1

    monkeypatch.setattr(harvester, "maybe_write_learning_pair", write_learning)
    monkeypatch.setattr(
        harvester,
        "maybe_refit_bucket",
        lambda *_args: conn.execute(
            "INSERT INTO event_atomic_probe VALUES ('learning_refit')"
        ),
    )
    monkeypatch.setattr(harvester, "record_settlement_result", lambda *_args: 0)
    monkeypatch.setattr(
        harvester,
        "rediscover_disputed_settlements",
        lambda: {"status": "skipped_test"},
    )

    result = harvester.run_harvester()

    assert result["pairs_created"] == 0
    assert result["positions_settled"] == 0
    assert conn.execute("SELECT COUNT(*) FROM event_atomic_probe").fetchone()[0] == 0
    assert portfolio.positions[0].state == "holding"
    assert conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0] == "active"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? "
        "AND event_type = 'SETTLED'",
        (position.trade_id,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? "
        "AND caused_by = 'partial_exit_economics_repair'",
        (position.trade_id,),
    ).fetchone()[0] == 0


def test_resolver_partial_exit_debt_rolls_back_whole_settlement_row(
    conn, monkeypatch, caplog
):
    """A later debt must not partially settle an earlier position in the row."""
    from src.execution import harvester_pnl_resolver
    from src.state import decision_chain
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.portfolio import PortfolioState, Position
    from src.state.projection import upsert_position_current

    first = Position(
        trade_id="resolver-row-first",
        market_id="resolver-row-market",
        city="Paris",
        cluster="Paris",
        target_date="2026-08-03",
        bin_label="30C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=5.0,
        entry_price=0.50,
        shares=10.0,
        cost_basis_usd=5.0,
        state="holding",
        token_id="resolver-row-first-token",
        no_token_id="resolver-row-first-no-token",
        condition_id="resolver-row-first-condition",
        env="live",
        last_monitor_at=_NOW.isoformat(),
    )
    second = replace(
        first,
        trade_id="resolver-row-second",
        market_id="resolver-row-market-2",
        token_id="resolver-row-second-token",
        no_token_id="resolver-row-second-no-token",
        condition_id="resolver-row-second-condition",
    )
    portfolio = PortfolioState(positions=[first, second])
    for position in portfolio.positions:
        upsert_position_current(conn, build_position_current_projection(position))
    conn.execute(
        """INSERT INTO position_events (
               event_id, position_id, event_version, sequence_no, event_type,
               occurred_at, phase_before, phase_after, strategy_key,
               source_module, payload_json, order_id, caused_by, env
           ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, 'pending_exit', 'active',
                         'center_buy', 'tests.test_exit_safety', ?, ?,
                     'partial_exit_fill', 'live')""",
        (
            "resolver-row-second:legacy-partial",
            second.trade_id,
            _NOW.isoformat(),
            json.dumps(
                {
                    "semantic_event": "CAPITAL_REDUCTION_FILLED",
                    "economic_fill_identity": "resolver-row-debt-fill-1",
                    "filled_shares": "1",
                    "filled_notional_usd": "0.6",
                    "allocated_cost_basis_usd": "0.5",
                    "realized_pnl_delta_usd": "0.1",
                    "remaining_shares": "9",
                    "remaining_cost_basis_usd": "4.5",
                    "fill_price": "0.6",
                    "order_id": "resolver-row-debt-order",
                },
                sort_keys=True,
            ),
            "resolver-row-debt-order",
        ),
    )
    conn.execute(
        """INSERT INTO position_events (
               event_id, position_id, event_version, sequence_no, event_type,
               occurred_at, phase_before, phase_after, strategy_key,
               source_module, payload_json, order_id, caused_by, env
           ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, 'pending_exit', 'active',
                     'center_buy', 'tests.test_exit_safety', '{}', NULL,
                     'resolver-row-seed', 'live')""",
        (
            "resolver-row-first:seed",
            first.trade_id,
            _NOW.isoformat(),
        ),
    )
    conn.commit()

    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.row_factory = sqlite3.Row
    forecasts_conn.execute(
        """CREATE TABLE settlement_outcomes (
               city TEXT, target_date TEXT, market_slug TEXT, winning_bin TEXT,
               temperature_metric TEXT, authority TEXT, settlement_source TEXT,
               settlement_value REAL, settled_at TEXT
           )"""
    )
    forecasts_conn.execute(
        """INSERT INTO settlement_outcomes (
               city, target_date, market_slug, winning_bin, temperature_metric,
               authority, settlement_source, settlement_value, settled_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "Paris", "2026-08-03", "paris-high-2026-08-03", "30C", "high",
            "VERIFIED", "wu_icao", 30.0, "2026-08-03T18:00:00Z",
        ),
    )
    forecasts_conn.commit()

    class Tracker:
        def __init__(self):
            self.settlement_count = 0

        def record_settlement(self, _position):
            self.settlement_count += 1

    tracker = Tracker()
    monkeypatch.setattr(
        "src.state.portfolio.load_portfolio", lambda *args, **kwargs: portfolio
    )
    monkeypatch.setattr("src.state.portfolio.save_portfolio", lambda *a, **kw: None)
    monkeypatch.setattr("src.state.strategy_tracker.get_tracker", lambda: tracker)
    monkeypatch.setattr("src.state.strategy_tracker.save_tracker", lambda *a, **kw: None)
    monkeypatch.setattr(
        "src.state.canonical_write.commit_then_export",
        lambda _conn, *, db_op, json_exports: db_op(),
    )

    try:
        first_attempt = harvester_pnl_resolver.resolve_pnl_for_settled_markets(
            conn, forecasts_conn
        )

        assert first_attempt["positions_settled"] == 0
        assert first_attempt["decision_log_rows_written"] == 0
        assert first_attempt["errors"] == 1
        assert any(
            "partial EXIT residual shares conflict" in record.getMessage()
            for record in caplog.records
        )
        assert tracker.settlement_count == 0
        assert [position.state for position in portfolio.positions] == [
            "holding", "holding"
        ]
        assert portfolio.ignored_tokens == []
        for trade_id in (first.trade_id, second.trade_id):
            assert conn.execute(
                "SELECT phase FROM position_current WHERE position_id = ?",
                (trade_id,),
            ).fetchone()[0] == "active"
            assert conn.execute(
                "SELECT COUNT(*) FROM position_events "
                "WHERE position_id = ? AND event_type = 'SETTLED'",
                (trade_id,),
            ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM decision_log WHERE mode = 'settlement'"
        ).fetchone()[0] == 0

        conn.execute(
            """INSERT INTO position_events (
                   event_id, position_id, event_version, sequence_no, event_type,
                   occurred_at, phase_before, phase_after, strategy_key,
                   source_module, payload_json, order_id, caused_by, env
               ) VALUES (?, ?, 1, 2, 'MONITOR_REFRESHED', ?, 'pending_exit', 'active',
                             'center_buy', 'tests.test_exit_safety', ?, ?,
                         'partial_exit_fill', 'live')""",
            (
                "resolver-row-second:canonical-correction",
                second.trade_id,
                _NOW.isoformat(),
                json.dumps(
                    {
                        "semantic_event": "CAPITAL_REDUCTION_FILLED",
                        "economic_fill_identity": "resolver-row-debt-fill-2",
                        "filled_shares": "0.1",
                        "filled_notional_usd": "0.06",
                        "allocated_cost_basis_usd": "0.05",
                        "realized_pnl_delta_usd": "0.01",
                        "remaining_shares": "10",
                        "remaining_cost_basis_usd": "5",
                        "fill_price": "0.6",
                        "order_id": "resolver-row-debt-order-2",
                    },
                    sort_keys=True,
                ),
                "resolver-row-debt-order-2",
            ),
        )
        conn.commit()

        real_store_settlement_records = decision_chain.store_settlement_records

        def write_then_fail(conn_arg, records, *, source):
            real_store_settlement_records(conn_arg, records, source=source)
            raise RuntimeError("forced settlement record failure")

        monkeypatch.setattr(
            decision_chain, "store_settlement_records", write_then_fail
        )
        failed_record_write = harvester_pnl_resolver.resolve_pnl_for_settled_markets(
            conn, forecasts_conn
        )
        assert failed_record_write["positions_settled"] == 0
        assert failed_record_write["decision_log_rows_written"] == 0
        assert failed_record_write["errors"] == 1
        assert tracker.settlement_count == 0
        assert [position.state for position in portfolio.positions] == [
            "holding", "holding"
        ]
        for trade_id in (first.trade_id, second.trade_id):
            assert conn.execute(
                "SELECT phase FROM position_current WHERE position_id = ?",
                (trade_id,),
            ).fetchone()[0] == "active"
            assert conn.execute(
                "SELECT COUNT(*) FROM position_events "
                "WHERE position_id = ? AND event_type = 'SETTLED'",
                (trade_id,),
            ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM decision_log WHERE mode = 'settlement'"
        ).fetchone()[0] == 0

        monkeypatch.setattr(
            decision_chain, "store_settlement_records", real_store_settlement_records
        )
        retry = harvester_pnl_resolver.resolve_pnl_for_settled_markets(
            conn, forecasts_conn
        )
        assert retry["positions_settled"] == 2
        assert retry["decision_log_rows_written"] == 2
        assert retry["errors"] == 0
        assert tracker.settlement_count == 2
        for trade_id in (first.trade_id, second.trade_id):
            phase_row = conn.execute(
                "SELECT phase FROM position_current WHERE position_id = ?",
                (trade_id,),
            ).fetchone()
            assert phase_row[0] == "settled", (
                trade_id,
                phase_row,
                conn.execute(
                    "SELECT event_type, phase_before, phase_after FROM position_events "
                    "WHERE position_id = ? ORDER BY sequence_no",
                    (trade_id,),
                ).fetchall(),
            )
            assert conn.execute(
                "SELECT COUNT(*) FROM position_events "
                "WHERE position_id = ? AND event_type = 'SETTLED'",
                (trade_id,),
            ).fetchone()[0] == 1
        settlement_logs = conn.execute(
            "SELECT artifact_json FROM decision_log WHERE mode = 'settlement'"
        ).fetchall()
        assert len(settlement_logs) == 1
        assert len(json.loads(settlement_logs[0][0])["settlements"]) == 2

        exactly_once = harvester_pnl_resolver.resolve_pnl_for_settled_markets(
            conn, forecasts_conn
        )
        assert exactly_once["status"] == "awaiting_truth_writer"
        assert conn.execute(
            "SELECT COUNT(*) FROM decision_log WHERE mode = 'settlement'"
        ).fetchone()[0] == 1
    finally:
        forecasts_conn.close()


def test_confirmed_partial_reduction_trade_fact_reopens_exact_remaining_claim(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_trade_fact

    position = Position(
        trade_id="pos-capital-reduction-fact",
        market_id="mkt-capital-reduction-fact",
        city="Seoul",
        cluster="asia",
        target_date="2026-07-16",
        bin_label="30C",
        direction="buy_no",
        strategy_key="center_buy",
        size_usd=14.0,
        entry_price=0.70,
        shares=20.0,
        cost_basis_usd=14.0,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-capital-reduction-fact",
        last_monitor_market_price=0.60,
    )
    intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=NO_TOKEN,
        shares=6.0,
        current_market_price=0.60,
        best_bid=0.60,
        close_position=False,
    )
    exit_lifecycle._record_exit_intent_before_execution_gates(conn, position, intent)
    position.last_exit_order_id = "ord-capital-reduction-fact"
    position.exit_state = "sell_placed"
    position.order_status = "sell_placed"
    assert exit_lifecycle._dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=intent.reason,
        error="",
        event_type="EXIT_ORDER_POSTED",
    )
    intent_occurred_at = conn.execute(
        """
        SELECT occurred_at
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_INTENT'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()[0]
    _insert_exit_command(
        conn,
        command_id="cmd-capital-reduction-fact",
        position_id=position.trade_id,
        token_id=NO_TOKEN,
        size=6.0,
        price=0.60,
        venue_order_id=position.last_exit_order_id,
        created_at=(
            datetime.fromisoformat(intent_occurred_at.replace("Z", "+00:00"))
            + timedelta(microseconds=1)
        ),
    )
    _ack_exit(
        conn,
        command_id="cmd-capital-reduction-fact",
        venue_order_id=position.last_exit_order_id,
    )
    append_trade_fact(
        conn,
        trade_id="trade-capital-reduction-fact",
        venue_order_id=position.last_exit_order_id,
        command_id="cmd-capital-reduction-fact",
        state="CONFIRMED",
        filled_size="2.5",
        fill_price="0.60",
        source="REST",
        observed_at="2026-07-16T00:00:00+00:00",
        raw_payload_hash=hashlib.sha256(b"capital-reduction-fact").hexdigest(),
        raw_payload_json={"size_matched": "2.5", "status": "CONFIRMED"},
    )

    class NoVenuePoll:
        def get_order_status(self, order_id):  # pragma: no cover - tripwire
            raise AssertionError(f"confirmed trade fact must win before poll: {order_id}")

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        NoVenuePoll(),
        conn=conn,
    )

    assert stats["reduced"] == 1
    assert stats["reduced_from_trade_fact"] == 1
    assert position.state == "holding"
    assert position.exit_state == ""
    assert position.shares == pytest.approx(17.5)
    current = conn.execute(
        "SELECT phase, shares FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    assert current["phase"] == "active"
    assert current["shares"] == pytest.approx(17.5)


@pytest.mark.parametrize(
    ("chain_reflected_shares", "chain_reflected_cost"),
    ((16.0, 11.2), (18.0, 12.6)),
)
def test_confirmed_reduction_records_fill_already_reflected_by_chain(
    conn,
    chain_reflected_shares,
    chain_reflected_cost,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_trade_fact

    position = Position(
        trade_id="pos-chain-reflected-reduction",
        market_id="mkt-chain-reflected-reduction",
        city="Seoul",
        cluster="asia",
        target_date="2026-07-16",
        bin_label="30C",
        direction="buy_no",
        strategy_key="center_buy",
        size_usd=14.0,
        entry_price=0.70,
        shares=20.0,
        cost_basis_usd=14.0,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-chain-reflected-reduction",
        last_monitor_market_price=0.60,
    )
    intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=NO_TOKEN,
        shares=4.0,
        current_market_price=0.60,
        best_bid=0.60,
        close_position=False,
        capital_certificate={"held_shares": "20"},
    )
    exit_lifecycle._record_exit_intent_before_execution_gates(
        conn,
        position,
        intent,
    )
    reduction_order_id = "ord-chain-reflected-reduction"
    position.last_exit_order_id = reduction_order_id
    position.exit_state = "sell_placed"
    position.order_status = "sell_placed"
    assert exit_lifecycle._dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=intent.reason,
        error="",
        event_type="EXIT_ORDER_POSTED",
    )
    intent_occurred_at = conn.execute(
        """
        SELECT occurred_at
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_INTENT'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()[0]
    _insert_exit_command(
        conn,
        command_id="cmd-chain-reflected-reduction",
        position_id=position.trade_id,
        token_id=NO_TOKEN,
        size=4.0,
        price=0.60,
        venue_order_id=position.last_exit_order_id,
        created_at=(
            datetime.fromisoformat(intent_occurred_at.replace("Z", "+00:00"))
            + timedelta(microseconds=1)
        ),
    )
    _ack_exit(
        conn,
        command_id="cmd-chain-reflected-reduction",
        venue_order_id=position.last_exit_order_id,
    )
    append_trade_fact(
        conn,
        trade_id="trade-chain-reflected-reduction",
        venue_order_id=position.last_exit_order_id,
        command_id="cmd-chain-reflected-reduction",
        state="CONFIRMED",
        filled_size="4",
        fill_price="0.60",
        source="REST",
        observed_at="2026-07-16T00:00:00+00:00",
        raw_payload_hash=hashlib.sha256(
            b"chain-reflected-reduction"
        ).hexdigest(),
        raw_payload_json={"size_matched": "4", "status": "CONFIRMED"},
    )

    position.shares = chain_reflected_shares
    position.size_usd = chain_reflected_cost
    position.cost_basis_usd = chain_reflected_cost
    position.chain_shares = chain_reflected_shares
    conn.execute(
        """
        UPDATE position_current
           SET shares = ?,
               size_usd = ?,
               cost_basis_usd = ?,
               chain_shares = ?
         WHERE position_id = ?
        """,
        (
            chain_reflected_shares,
            chain_reflected_cost,
            chain_reflected_cost,
            chain_reflected_shares,
            position.trade_id,
        ),
    )

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        object(),
        conn=conn,
    )

    assert stats["reduced_from_trade_fact"] == 1
    assert position.shares == pytest.approx(16.0)
    current = conn.execute(
        "SELECT phase, shares, chain_shares FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    assert dict(current) == {
        "phase": "active",
        "shares": 16.0,
        "chain_shares": chain_reflected_shares,
    }
    assert exit_lifecycle._recorded_reduction_fill_shares(
        conn,
        position_id=position.trade_id,
        order_id=reduction_order_id,
    ) == Decimal("4.0")
    if chain_reflected_shares == 18.0:
        assert position.nested_fills[-1]["filled_shares"] == pytest.approx(2.0)


def test_pending_exit_fill_poller_skips_retry_without_order_id(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-retry-no-order",
        market_id="mkt-retry-no-order",
        city="Manila",
        cluster="Asia",
        target_date="2026-07-01",
        bin_label="29C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=0.15,
        entry_price=0.015,
        shares=9.7,
        cost_basis_usd=0.15,
        state="pending_exit",
        pre_exit_state="entered",
        exit_state="retry_pending",
        order_status="sell_pending_confirmation",
        last_exit_order_id="",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-retry-no-order",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"retry_pending without order id must not be polled: {order_id}")

    stats = exit_lifecycle.check_pending_exits(PortfolioState(positions=[position]), FakeClob(), conn=conn)

    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert position.exit_state == "retry_pending"
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_ID_MISSING'
        """,
        (position.trade_id,),
    ).fetchone()[0] == 0


def test_pending_exit_fill_poller_releases_expired_retry_without_order_id(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-retry-expired-no-order",
        market_id="mkt-retry-expired-no-order",
        city="Manila",
        cluster="Asia",
        target_date="2026-07-02",
        bin_label="32C",
        direction="buy_yes",
        strategy_key="forecast_qkernel_entry",
        size_usd=17.71,
        entry_price=0.44,
        shares=40.25,
        cost_basis_usd=17.71,
        state="pending_exit",
        pre_exit_state="day0_window",
        day0_entered_at="2026-07-02T00:48:30+00:00",
        entered_at="2026-07-02T00:11:43+00:00",
        chain_state="synced",
        env="live",
        exit_state="retry_pending",
        order_status="retry_pending",
        last_exit_order_id="",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-retry-expired-no-order",
        exit_retry_count=1,
        next_exit_retry_at="2026-07-02T02:22:35+00:00",
        last_exit_error="exit_executable_snapshot_unavailable",
        exit_reason="DAY0_HARD_FACT_BIN_DEAD",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"expired retry without order id must not be polled: {order_id}")

    stats = exit_lifecycle.check_pending_exits(PortfolioState(positions=[position]), FakeClob(), conn=conn)

    assert stats["retried"] == 1
    assert stats["released_retry"] == 1
    assert position.state == "day0_window"
    assert position.exit_state == ""
    assert position.order_status == "filled"
    assert position.next_exit_retry_at == ""
    event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert event["event_type"] == "EXIT_RETRY_RELEASED"
    assert event["phase_before"] == "pending_exit"
    assert event["phase_after"] == "day0_window"
    assert event["venue_status"] == "ready"
    assert payload["release_reason"] == "EXIT_RETRY_COOLDOWN_EXPIRED"
    assert payload["previous_retry_count"] == 1


def test_legacy_reduce_only_freshness_error_stays_retry_classified():
    from src.execution.exit_lifecycle import _is_runtime_submit_gate_block_error

    assert _is_runtime_submit_gate_block_error(
        "[gate_runtime] BLOCKED cap='reduce_only_exit_submit': condition "
        "'reduce_only_exit_deployment_freshness_mismatch' is active"
    )


def test_runtime_submit_gate_block_holds_retry_until_gate_recovers(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    error = (
        "[gate_runtime] BLOCKED cap='live_venue_submit': condition "
        "'deployment_freshness_mismatch' is active"
    )
    position = Position(
        trade_id="pos-runtime-gate-block",
        market_id="mkt-runtime-gate-block",
        city="Taipei",
        cluster="Asia",
        target_date="2026-07-09",
        bin_label="36C",
        direction="buy_no",
        strategy_key="forecast_qkernel_entry",
        size_usd=11.0,
        entry_price=0.57,
        shares=19.0,
        cost_basis_usd=11.0,
        state="pending_exit",
        pre_exit_state="day0_window",
        day0_entered_at="2026-07-09T00:30:00+00:00",
        entered_at="2026-07-08T15:38:27+00:00",
        exit_state="",
        order_status="filled",
        token_id=NO_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-runtime-gate-block",
        exit_retry_count=0,
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
    )

    exit_lifecycle._mark_exit_retry(
        position,
        reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        error=error,
        conn=conn,
    )

    assert position.exit_state == "retry_pending"
    assert position.order_status == "retry_pending"
    assert position.exit_retry_count == 0
    first_retry_at = position.next_exit_retry_at
    event = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert payload["status"] == "runtime_submit_gate_blocked"
    assert payload["runtime_submit_gate_block"] is True

    reloaded = Position(
        trade_id=position.trade_id,
        market_id=position.market_id,
        city=position.city,
        cluster=position.cluster,
        target_date=position.target_date,
        bin_label=position.bin_label,
        direction=position.direction,
        strategy_key=position.strategy_key,
        size_usd=position.size_usd,
        entry_price=position.entry_price,
        shares=position.shares,
        cost_basis_usd=position.cost_basis_usd,
        state="pending_exit",
        pre_exit_state="day0_window",
        day0_entered_at=position.day0_entered_at,
        entered_at=position.entered_at,
        exit_state="retry_pending",
        order_status="retry_pending",
        token_id=position.token_id,
        no_token_id=position.no_token_id,
        condition_id=position.condition_id,
        exit_retry_count=0,
        next_exit_retry_at="2000-01-01T00:00:00+00:00",
        exit_reason=position.exit_reason,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_runtime_submit_gate_currently_allows_submit",
        lambda: False,
    )
    assert exit_lifecycle.check_pending_retries(reloaded, conn=conn) is False
    assert reloaded.state == "pending_exit"
    assert reloaded.exit_state == "retry_pending"
    assert reloaded.order_status == "retry_pending"
    assert reloaded.next_exit_retry_at != "2000-01-01T00:00:00+00:00"
    latest = conn.execute(
        """
        SELECT event_type, phase_after, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    latest_payload = json.loads(latest["payload_json"])
    assert latest["event_type"] == "EXIT_ORDER_REJECTED"
    assert latest["phase_after"] == "pending_exit"
    assert latest_payload["status"] == "runtime_submit_gate_blocked"
    assert latest_payload["runtime_submit_gate_block"] is True
    assert "deployment_freshness_mismatch" in latest_payload["error"]

    assert exit_lifecycle.check_pending_retries(position, conn=conn) is False
    assert position.exit_state == "retry_pending"
    assert position.exit_retry_count == 0
    assert position.next_exit_retry_at == first_retry_at
    assert exit_lifecycle.is_exit_cooldown_active(position) is True

    monkeypatch.setattr(
        exit_lifecycle,
        "_runtime_submit_gate_currently_allows_submit",
        lambda: True,
    )
    assert exit_lifecycle.check_pending_retries(reloaded, conn=conn) is True
    assert reloaded.state == "day0_window"
    assert reloaded.exit_state == ""
    assert reloaded.order_status == "filled"
    assert reloaded.next_exit_retry_at == ""
    released = conn.execute(
        """
        SELECT event_type, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    released_payload = json.loads(released["payload_json"])
    assert released["event_type"] == "EXIT_RETRY_RELEASED"
    assert "deployment_freshness_mismatch" in released_payload["error"]


def test_monitor_refresh_cannot_overwrite_pending_exit_dust_hold_projection(conn):
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.execution import exit_lifecycle
    from src.state.db import append_many_and_project
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-dust-hold-monitor-overwrite",
        market_id="mkt-dust-hold-monitor-overwrite",
        city="Kuala Lumpur",
        cluster="Asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        strategy_key="forecast_qkernel_entry",
        size_usd=0.64,
        entry_price=0.64,
        shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        pre_exit_state="day0_window",
        env="live",
        day0_entered_at="2026-07-08T00:00:00+00:00",
        entered_at="2026-07-07T00:47:43+00:00",
        exit_state="",
        order_status="filled",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-dust-hold-monitor-overwrite",
        last_monitor_prob=0.01,
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.13,
        last_monitor_market_price_is_fresh=True,
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
    )
    dust_error = "executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5"
    exit_lifecycle._mark_exit_dust_hold(
        position,
        reason=f"FAMILY_DIRECT_SELL_DOMINATES_HOLD [DUST: {dust_error}]",
        error=dust_error,
        conn=conn,
    )

    stale_monitor_position = Position(
        trade_id=position.trade_id,
        market_id=position.market_id,
        city=position.city,
        cluster=position.cluster,
        target_date=position.target_date,
        bin_label=position.bin_label,
        direction=position.direction,
        strategy_key=position.strategy_key,
        size_usd=position.size_usd,
        entry_price=position.entry_price,
        shares=position.shares,
        cost_basis_usd=position.cost_basis_usd,
        state="day0_window",
        env="live",
        day0_entered_at=position.day0_entered_at,
        entered_at=position.entered_at,
        exit_state="",
        order_status="filled",
        token_id=position.token_id,
        no_token_id=position.no_token_id,
        condition_id=position.condition_id,
        last_monitor_prob=0.0,
        last_monitor_prob_is_fresh=True,
        last_monitor_edge=-1.0,
        last_monitor_market_price=None,
        last_monitor_market_price_is_fresh=False,
        exit_reason="",
    )
    sequence_no = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0]
    events, projection = build_monitor_refreshed_canonical_write(
        stale_monitor_position,
        sequence_no=sequence_no,
        phase_after="day0_window",
        occurred_at="2026-07-08T06:14:44+00:00",
    )
    append_many_and_project(conn, events, projection)

    current = conn.execute(
        """
        SELECT phase, order_status, exit_reason, exit_retry_count,
               next_exit_retry_at, last_monitor_prob, last_monitor_prob_is_fresh
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "backoff_exhausted"
    assert "[DUST:" in current["exit_reason"]
    assert current["exit_retry_count"] == 0
    assert current["next_exit_retry_at"] in ("", None)
    assert current["last_monitor_prob"] == pytest.approx(0.0)
    assert current["last_monitor_prob_is_fresh"] == 1


def test_chain_size_correction_cannot_overwrite_pending_exit_dust_hold_projection(conn):
    from src.engine.lifecycle_events import build_chain_size_corrected_canonical_write
    from src.execution import exit_lifecycle
    from src.state.db import append_many_and_project
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-dust-hold-chain-overwrite",
        market_id="mkt-dust-hold-chain-overwrite",
        city="Taipei",
        cluster="Asia",
        target_date="2026-07-09",
        bin_label="35C",
        direction="buy_no",
        strategy_key="forecast_qkernel_entry",
        size_usd=2.432,
        entry_price=0.64,
        shares=3.8,
        chain_shares=3.8,
        chain_avg_price=0.64,
        chain_cost_basis_usd=2.432,
        cost_basis_usd=2.432,
        state="day0_window",
        pre_exit_state="day0_window",
        env="live",
        day0_entered_at="2026-07-09T00:00:00+00:00",
        entered_at="2026-07-08T12:04:04+00:00",
        exit_state="",
        order_status="filled",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-dust-hold-chain-overwrite",
        last_monitor_prob=0.7672,
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.41,
        last_monitor_market_price_is_fresh=True,
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
    )
    dust_error = "executable_snapshot_gate: size 3.8 is below snapshot min_order_size 5"
    exit_lifecycle._mark_exit_dust_hold(
        position,
        reason=f"FAMILY_DIRECT_SELL_DOMINATES_HOLD [DUST: {dust_error}]",
        error=dust_error,
        conn=conn,
    )

    stale_chain_position = Position(
        trade_id=position.trade_id,
        market_id=position.market_id,
        city=position.city,
        cluster=position.cluster,
        target_date=position.target_date,
        bin_label=position.bin_label,
        direction=position.direction,
        strategy_key=position.strategy_key,
        size_usd=position.size_usd,
        entry_price=position.entry_price,
        shares=position.shares,
        chain_shares=3.8,
        chain_avg_price=0.64,
        chain_cost_basis_usd=2.432,
        cost_basis_usd=position.cost_basis_usd,
        state="day0_window",
        env="live",
        day0_entered_at=position.day0_entered_at,
        entered_at=position.entered_at,
        exit_state="",
        order_status="partial",
        token_id=position.token_id,
        no_token_id=position.no_token_id,
        condition_id=position.condition_id,
        chain_state="synced",
        chain_verified_at="2026-07-09T04:10:32+00:00",
    )
    sequence_no = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0]
    events, projection = build_chain_size_corrected_canonical_write(
        stale_chain_position,
        local_shares_before=3.8,
        sequence_no=sequence_no,
        phase_after="day0_window",
    )
    append_many_and_project(conn, events, projection)

    current = conn.execute(
        """
        SELECT phase, order_status, exit_reason, exit_retry_count,
               next_exit_retry_at, chain_state, chain_shares
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "backoff_exhausted"
    assert "[DUST:" in current["exit_reason"]
    assert current["exit_retry_count"] == 0
    assert current["next_exit_retry_at"] in ("", None)
    assert current["chain_state"] == "synced"
    assert current["chain_shares"] == pytest.approx(3.8)


def test_live_dust_rejection_sequence_stays_pending_exit_through_monitor_and_chain(conn):
    from src.engine.lifecycle_events import (
        build_chain_size_corrected_canonical_write,
        build_monitor_refreshed_canonical_write,
    )
    from src.execution import exit_lifecycle
    from src.state.db import append_many_and_project
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-live-dust-sequence",
        market_id="mkt-live-dust-sequence",
        city="Taipei",
        cluster="Asia",
        target_date="2026-07-09",
        bin_label="35C",
        direction="buy_no",
        strategy_key="forecast_qkernel_entry",
        size_usd=2.432,
        entry_price=0.64,
        shares=3.8,
        chain_shares=3.8,
        chain_avg_price=0.64,
        chain_cost_basis_usd=2.432,
        cost_basis_usd=2.432,
        state="day0_window",
        pre_exit_state="day0_window",
        env="live",
        day0_entered_at="2026-07-09T00:00:00+00:00",
        entered_at="2026-07-08T12:04:04+00:00",
        exit_state="",
        order_status="partial",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-live-dust-sequence",
        last_monitor_prob=0.9461,
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.36,
        last_monitor_market_price_is_fresh=True,
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
    )
    dust_error = "executable_snapshot_gate: size 3.8 is below snapshot min_order_size 5"
    exit_lifecycle._mark_exit_dust_hold(
        position,
        reason=f"FAMILY_DIRECT_SELL_DOMINATES_HOLD [DUST: {dust_error}]",
        error=dust_error,
        conn=conn,
    )

    stale_monitor_position = Position(
        trade_id=position.trade_id,
        market_id=position.market_id,
        city=position.city,
        cluster=position.cluster,
        target_date=position.target_date,
        bin_label=position.bin_label,
        direction=position.direction,
        strategy_key=position.strategy_key,
        size_usd=position.size_usd,
        entry_price=position.entry_price,
        shares=position.shares,
        chain_shares=position.chain_shares,
        chain_avg_price=position.chain_avg_price,
        chain_cost_basis_usd=position.chain_cost_basis_usd,
        cost_basis_usd=position.cost_basis_usd,
        state="day0_window",
        env="live",
        day0_entered_at=position.day0_entered_at,
        entered_at=position.entered_at,
        exit_state="",
        order_status="partial",
        token_id=position.token_id,
        no_token_id=position.no_token_id,
        condition_id=position.condition_id,
        last_monitor_prob=0.9461,
        last_monitor_prob_is_fresh=True,
        last_monitor_edge=-0.4,
        last_monitor_market_price=0.001,
        last_monitor_market_price_is_fresh=True,
        exit_reason="",
    )
    sequence_no = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0]
    events, projection = build_monitor_refreshed_canonical_write(
        stale_monitor_position,
        sequence_no=sequence_no,
        phase_after="day0_window",
        occurred_at="2026-07-09T04:02:44+00:00",
    )
    append_many_and_project(conn, events, projection)

    stale_chain_position = Position(
        trade_id=position.trade_id,
        market_id=position.market_id,
        city=position.city,
        cluster=position.cluster,
        target_date=position.target_date,
        bin_label=position.bin_label,
        direction=position.direction,
        strategy_key=position.strategy_key,
        size_usd=position.size_usd,
        entry_price=position.entry_price,
        shares=position.shares,
        chain_shares=3.8,
        chain_avg_price=0.64,
        chain_cost_basis_usd=2.432,
        cost_basis_usd=position.cost_basis_usd,
        state="day0_window",
        env="live",
        day0_entered_at=position.day0_entered_at,
        entered_at=position.entered_at,
        exit_state="",
        order_status="partial",
        token_id=position.token_id,
        no_token_id=position.no_token_id,
        condition_id=position.condition_id,
        chain_state="synced",
        chain_verified_at="2026-07-09T04:04:43+00:00",
    )
    sequence_no = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0]
    events, projection = build_chain_size_corrected_canonical_write(
        stale_chain_position,
        local_shares_before=3.8,
        sequence_no=sequence_no,
        phase_after="day0_window",
    )
    append_many_and_project(conn, events, projection)

    current = conn.execute(
        """
        SELECT phase, order_status, exit_reason, exit_retry_count,
               next_exit_retry_at, chain_state, chain_shares, last_monitor_prob
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "backoff_exhausted"
    assert "[DUST:" in current["exit_reason"]
    assert current["exit_retry_count"] == 0
    assert current["next_exit_retry_at"] in ("", None)
    assert current["chain_state"] == "synced"
    assert current["chain_shares"] == pytest.approx(3.8)
    assert current["last_monitor_prob"] == pytest.approx(0.9461)


def test_monitor_refresh_cannot_overwrite_retry_pending_exit_projection(conn):
    from src.engine.lifecycle_events import (
        build_monitor_refreshed_canonical_write,
        build_position_current_projection,
    )
    from src.state.db import append_many_and_project
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    pending_exit = Position(
        trade_id="pos-retry-pending-monitor-overwrite",
        market_id="mkt-retry-pending-monitor-overwrite",
        city="Taipei",
        cluster="Asia",
        target_date="2026-07-09",
        bin_label="36C",
        direction="buy_no",
        strategy_key="forecast_qkernel_entry",
        size_usd=7.44,
        entry_price=0.64,
        shares=11.627905,
        chain_shares=11.6279,
        chain_avg_price=0.64,
        chain_cost_basis_usd=7.44,
        cost_basis_usd=7.44,
        state="pending_exit",
        pre_exit_state="day0_window",
        env="live",
        day0_entered_at="2026-07-09T00:00:00+00:00",
        entered_at="2026-07-08T12:04:04+00:00",
        exit_state="retry_pending",
        order_status="retry_pending",
        exit_retry_count=2,
        next_exit_retry_at="2026-07-09T14:55:24+00:00",
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-retry-pending-monitor-overwrite",
        last_monitor_prob=0.72,
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.41,
        last_monitor_market_price_is_fresh=True,
    )
    upsert_position_current(conn, build_position_current_projection(pending_exit))

    stale_monitor_position = Position(
        trade_id=pending_exit.trade_id,
        market_id=pending_exit.market_id,
        city=pending_exit.city,
        cluster=pending_exit.cluster,
        target_date=pending_exit.target_date,
        bin_label=pending_exit.bin_label,
        direction=pending_exit.direction,
        strategy_key=pending_exit.strategy_key,
        size_usd=pending_exit.size_usd,
        entry_price=pending_exit.entry_price,
        shares=pending_exit.shares,
        chain_shares=pending_exit.chain_shares,
        chain_avg_price=pending_exit.chain_avg_price,
        chain_cost_basis_usd=pending_exit.chain_cost_basis_usd,
        cost_basis_usd=pending_exit.cost_basis_usd,
        state="day0_window",
        env="live",
        day0_entered_at=pending_exit.day0_entered_at,
        entered_at=pending_exit.entered_at,
        exit_state="",
        order_status="partial",
        token_id=pending_exit.token_id,
        no_token_id=pending_exit.no_token_id,
        condition_id=pending_exit.condition_id,
        last_monitor_prob=0.18,
        last_monitor_prob_is_fresh=True,
        last_monitor_edge=-0.9,
        last_monitor_market_price=0.01,
        last_monitor_market_price_is_fresh=True,
        exit_reason="",
    )
    events, projection = build_monitor_refreshed_canonical_write(
        stale_monitor_position,
        sequence_no=1,
        phase_after="day0_window",
        occurred_at="2026-07-09T14:58:00+00:00",
    )
    append_many_and_project(conn, events, projection)

    current = conn.execute(
        """
        SELECT phase, order_status, exit_reason, exit_retry_count,
               next_exit_retry_at, last_monitor_prob, last_monitor_prob_is_fresh
          FROM position_current
         WHERE position_id = ?
        """,
        (pending_exit.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "retry_pending"
    assert current["exit_reason"] == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
    assert current["exit_retry_count"] == 2
    assert current["next_exit_retry_at"] == "2026-07-09T14:55:24+00:00"
    assert current["last_monitor_prob"] == pytest.approx(0.18)
    assert current["last_monitor_prob_is_fresh"] == 1


def test_chain_size_correction_cannot_overwrite_exit_intent_projection(conn):
    from src.engine.lifecycle_events import (
        build_chain_size_corrected_canonical_write,
        build_position_current_projection,
    )
    from src.state.db import append_many_and_project
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    pending_exit = Position(
        trade_id="pos-exit-intent-chain-overwrite",
        market_id="mkt-exit-intent-chain-overwrite",
        city="Shenzhen",
        cluster="Asia",
        target_date="2026-07-09",
        bin_label="32C",
        direction="buy_no",
        strategy_key="forecast_qkernel_entry",
        size_usd=12.39,
        entry_price=0.62,
        shares=19.98,
        chain_shares=19.98,
        chain_avg_price=0.62,
        chain_cost_basis_usd=12.39,
        cost_basis_usd=12.39,
        state="pending_exit",
        pre_exit_state="day0_window",
        env="live",
        day0_entered_at="2026-07-09T00:00:00+00:00",
        entered_at="2026-07-08T12:04:04+00:00",
        exit_state="exit_intent",
        order_status="exit_intent",
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-exit-intent-chain-overwrite",
    )
    upsert_position_current(conn, build_position_current_projection(pending_exit))

    stale_chain_position = Position(
        trade_id=pending_exit.trade_id,
        market_id=pending_exit.market_id,
        city=pending_exit.city,
        cluster=pending_exit.cluster,
        target_date=pending_exit.target_date,
        bin_label=pending_exit.bin_label,
        direction=pending_exit.direction,
        strategy_key=pending_exit.strategy_key,
        size_usd=pending_exit.size_usd,
        entry_price=pending_exit.entry_price,
        shares=pending_exit.shares,
        chain_shares=19.98,
        chain_avg_price=0.62,
        chain_cost_basis_usd=12.39,
        cost_basis_usd=pending_exit.cost_basis_usd,
        state="day0_window",
        env="live",
        day0_entered_at=pending_exit.day0_entered_at,
        entered_at=pending_exit.entered_at,
        exit_state="",
        order_status="partial",
        token_id=pending_exit.token_id,
        no_token_id=pending_exit.no_token_id,
        condition_id=pending_exit.condition_id,
        chain_state="synced",
        chain_verified_at="2026-07-09T14:59:00+00:00",
    )
    events, projection = build_chain_size_corrected_canonical_write(
        stale_chain_position,
        local_shares_before=19.98,
        sequence_no=1,
        phase_after="day0_window",
    )
    append_many_and_project(conn, events, projection)

    current = conn.execute(
        """
        SELECT phase, order_status, exit_reason, exit_retry_count,
               next_exit_retry_at, chain_state, chain_shares
          FROM position_current
         WHERE position_id = ?
        """,
        (pending_exit.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "exit_intent"
    assert current["exit_reason"] == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
    assert current["exit_retry_count"] == 0
    assert current["next_exit_retry_at"] in ("", None)
    assert current["chain_state"] == "synced"
    assert current["chain_shares"] == pytest.approx(19.98)


def test_pending_exit_without_order_releases_for_favorable_bid_redecision(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.execution.exit_lifecycle import release_pending_exit_without_order_if_retryable
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-pending-no-order-release",
        market_id="mkt-pending-no-order-release",
        city="Manila",
        cluster="Asia",
        target_date="2026-07-01",
        bin_label="29C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=0.15,
        entry_price=0.015,
        shares=9.7,
        cost_basis_usd=0.15,
        state="pending_exit",
        pre_exit_state="entered",
        entered_at="2026-07-01T00:10:00+00:00",
        exit_state="",
        order_status="filled",
        last_exit_order_id="",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-pending-no-order-release",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_exit_reject_error",
        lambda *_args, **_kwargs: "exit_no_in_band_bid",
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_exit_snapshot_context",
        lambda *_args, **_kwargs: {
            "executable_snapshot_orderbook_top_bid": "0.999",
        },
    )

    assert release_pending_exit_without_order_if_retryable(position, conn=conn) is True
    assert position.state == "entered"
    assert position.pre_exit_state == ""
    assert position.exit_state == ""
    assert position.order_status == "filled"
    current = conn.execute(
        """
        SELECT phase, order_status, exit_retry_count, next_exit_retry_at
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert dict(current) == {
        "phase": "active",
        "order_status": "filled",
        "exit_retry_count": 0,
        "next_exit_retry_at": "",
    }
    event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert event["event_type"] == "EXIT_RETRY_RELEASED"
    assert event["phase_before"] == "pending_exit"
    assert event["phase_after"] == "active"
    assert event["venue_status"] == "ready"
    assert payload["release_reason"] == "PENDING_EXIT_NO_ORDER_RELEASED"
    assert payload["release_reason"] == "PENDING_EXIT_NO_ORDER_RELEASED"


def test_pending_exit_phantom_sell_projection_releases_before_no_order_retry(conn):
    from src.execution import exit_lifecycle
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.portfolio import PortfolioState, Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-phantom-sell-projection",
        market_id="mkt-phantom-sell-projection",
        city="Miami",
        cluster="US",
        target_date="2026-06-30",
        bin_label="96-97F",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=4.34,
        entry_price=0.051,
        shares=85.17,
        cost_basis_usd=4.34,
        state="pending_exit",
        pre_exit_state="entered",
        entered_at=_NOW.isoformat(),
        exit_state="sell_placed",
        order_status="sell_placed",
        order_id="0xphantom-exit-order",
        last_exit_order_id="",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-phantom-sell-projection",
    )
    upsert_position_current(conn, build_position_current_projection(position))

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"phantom sell projection must be released, not polled: {order_id}")

    stats = exit_lifecycle.check_pending_exits(PortfolioState(positions=[position]), FakeClob(), conn=conn)

    assert stats["retried"] == 1
    assert stats["released_no_order"] == 1
    assert position.state == "entered"
    assert position.exit_state == ""
    assert position.order_status == "filled"
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_ID_MISSING'
        """,
        (position.trade_id,),
    ).fetchone()[0] == 0


def test_pending_exit_intent_without_exit_command_releases_for_redecision(conn):
    from src.execution import exit_lifecycle
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.portfolio import PortfolioState, Position, _position_from_projection_row
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-exit-intent-no-command",
        market_id="mkt-exit-intent-no-command",
        city="Shenzhen",
        cluster="Asia",
        target_date="2026-07-09",
        bin_label="32C",
        direction="buy_no",
        strategy_key="center_buy",
        size_usd=12.39,
        entry_price=0.62,
        shares=19.98,
        cost_basis_usd=12.39,
        state="pending_exit",
        pre_exit_state="day0_window",
        day0_entered_at="2026-07-09T00:30:00+00:00",
        chain_state="synced",
        chain_shares=19.98,
        order_status="exit_intent",
        exit_state="",
        last_exit_order_id="",
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-exit-intent-no-command",
        env="live",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    row = conn.execute(
        "SELECT * FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    runtime_position = _position_from_projection_row(dict(row), current_mode="live")
    runtime_position.pre_exit_state = "day0_window"
    assert runtime_position.state == "pending_exit"
    assert runtime_position.exit_state == "exit_intent"
    assert runtime_position.order_status == "exit_intent"

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"stranded exit intent must release, not poll: {order_id}")

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[runtime_position]),
        FakeClob(),
        conn=conn,
    )

    assert stats["retried"] == 1
    assert stats["released_no_order"] == 1
    assert runtime_position.state == "day0_window"
    assert runtime_position.exit_state == ""
    assert runtime_position.order_status == "filled"
    current = conn.execute(
        """
        SELECT phase, order_status, exit_retry_count, next_exit_retry_at
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert dict(current) == {
        "phase": "day0_window",
        "order_status": "filled",
        "exit_retry_count": 0,
        "next_exit_retry_at": "",
    }
    event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert event["event_type"] == "EXIT_RETRY_RELEASED"
    assert event["phase_before"] == "pending_exit"
    assert event["phase_after"] == "day0_window"
    assert event["venue_status"] == "ready"
    assert payload["release_reason"] == "PENDING_EXIT_NO_ORDER_RELEASED"


def test_retrying_pending_exit_posted_without_command_releases_before_poll(conn):
    from src.execution import exit_lifecycle
    from src.state.db import transition_phase
    from src.state.portfolio import PortfolioState, Position

    trade_id = "pos-stale-posted-exit-without-command"
    posted = Position(
        trade_id=trade_id,
        market_id="mkt-stale-posted-exit-without-command",
        city="Miami",
        cluster="US",
        target_date="2026-06-30",
        bin_label="96-97F",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=4.34,
        entry_price=0.051,
        shares=85.17,
        cost_basis_usd=4.34,
        state="pending_exit",
        pre_exit_state="entered",
        exit_state="sell_placed",
        order_status="sell_placed",
        order_id="0xstale-posted-exit",
        last_exit_order_id="0xstale-posted-exit",
        exit_retry_count=2,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-stale-posted-exit-without-command",
    )
    assert transition_phase(
        conn,
        posted,
        event_type="EXIT_ORDER_POSTED",
        reason="ENTRY_SELECTION_GUARD_INVALID_EXIT",
        error="",
    )

    runtime_position = Position(
        trade_id=trade_id,
        market_id="mkt-stale-posted-exit-without-command",
        city="Miami",
        cluster="US",
        target_date="2026-06-30",
        bin_label="96-97F",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=4.34,
        entry_price=0.051,
        shares=85.17,
        cost_basis_usd=4.34,
        state="pending_exit",
        pre_exit_state="entered",
        exit_state="sell_placed",
        order_status="sell_placed",
        order_id="0xstale-posted-exit",
        last_exit_order_id="",
        exit_retry_count=2,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-stale-posted-exit-without-command",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"stale posted exit without command must release: {order_id}")

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[runtime_position]),
        FakeClob(),
        conn=conn,
    )

    assert stats["retried"] == 1
    assert stats["released_no_order"] == 1
    assert runtime_position.state == "entered"
    assert runtime_position.exit_state == ""
    assert runtime_position.order_status == "filled"


def test_pending_exit_status_poll_releases_db_transaction_before_local_scan_and_venue_io(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-pending-exit-lock-boundary",
        market_id="mkt-pending-exit-lock-boundary",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-pending-exit-lock-boundary",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-pending-exit-lock-boundary",
        last_monitor_market_price=0.45,
        last_monitor_best_bid=0.44,
    )
    portfolio = PortfolioState(positions=[position])
    _insert_exit_command(
        conn,
        command_id="cmd-pending-exit-lock-boundary",
        position_id=position.trade_id,
        size=20.0,
        price=0.44,
        venue_order_id="ord-pending-exit-lock-boundary",
    )
    conn.execute(
        "UPDATE venue_commands SET price = price WHERE command_id = ?",
        ("cmd-pending-exit-lock-boundary",),
    )
    assert conn.in_transaction

    original_close_candidate = exit_lifecycle._exit_trade_fact_close_candidate

    def assert_unlocked_before_trade_fact_scan(*args, **kwargs):
        assert conn.in_transaction is False
        return original_close_candidate(*args, **kwargs)

    monkeypatch.setattr(
        exit_lifecycle,
        "_exit_trade_fact_close_candidate",
        assert_unlocked_before_trade_fact_scan,
    )

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-pending-exit-lock-boundary"
            assert conn.in_transaction is False
            return {"status": "LIVE"}

        def get_orderbook(self, token_id):
            assert token_id == YES_TOKEN
            assert conn.in_transaction is False
            return {"bids": [{"price": "0.44", "size": "10"}], "asks": []}

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1


def test_pending_exit_status_poll_is_bounded_and_rotates(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    exit_lifecycle._PENDING_EXIT_SCAN_CURSOR = 0
    positions = []
    for idx in range(4):
        position = Position(
            trade_id=f"pos-pending-exit-budget-{idx}",
            market_id=f"mkt-pending-exit-budget-{idx}",
            city="NYC",
            cluster="US-Northeast",
            target_date="2026-04-27",
            bin_label="50-51°F",
            direction="buy_yes",
            strategy_key="center_buy",
            size_usd=10.0,
            entry_price=0.50,
            shares=20.0,
            cost_basis_usd=10.0,
            state="pending_exit",
            exit_state="sell_pending",
            last_exit_order_id=f"ord-pending-exit-budget-{idx}",
            token_id=YES_TOKEN,
            no_token_id=NO_TOKEN,
            condition_id=f"condition-pending-exit-budget-{idx}",
            last_monitor_market_price=0.45,
            last_monitor_best_bid=0.44,
        )
        _insert_exit_command(
            conn,
            command_id=f"cmd-pending-exit-budget-{idx}",
            position_id=position.trade_id,
            size=20.0,
            price=0.44,
            venue_order_id=position.last_exit_order_id,
        )
        positions.append(position)

    class FakeClob:
        def __init__(self):
            self.order_status_calls = []

        def get_order_status(self, order_id):
            self.order_status_calls.append(order_id)
            return {"status": "LIVE"}

        def get_orderbook(self, token_id):
            assert token_id == YES_TOKEN
            return {"bids": [{"price": "0.44", "size": "10"}], "asks": []}

    clob = FakeClob()
    portfolio = PortfolioState(positions=positions)

    first = exit_lifecycle.check_pending_exits(
        portfolio,
        clob,
        conn=conn,
        max_positions=2,
        cycle_budget_seconds=100.0,
    )
    second = exit_lifecycle.check_pending_exits(
        portfolio,
        clob,
        conn=conn,
        max_positions=2,
        cycle_budget_seconds=100.0,
    )

    assert first["pending_exit_scan_candidates"] == 4
    assert first["pending_exit_positions_scanned"] == 2
    assert first["pending_exit_positions_deferred"] == 2
    assert first["pending_exit_defer_reason"] == "max_positions"
    assert second["pending_exit_positions_scanned"] == 2
    assert clob.order_status_calls == [
        "ord-pending-exit-budget-0",
        "ord-pending-exit-budget-1",
        "ord-pending-exit-budget-2",
        "ord-pending-exit-budget-3",
    ]


def test_pending_exit_reduction_precondition_does_not_abort_later_retry(
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    malformed = Position(
        trade_id="pos-malformed-reduction",
        market_id="mkt-malformed-reduction",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=5.0,
        entry_price=0.50,
        shares=10.0,
        cost_basis_usd=5.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-malformed-reduction",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
    )
    retryable = Position(
        trade_id="pos-retry-after-malformed",
        market_id="mkt-retry-after-malformed",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="52-53°F",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=5.0,
        entry_price=0.50,
        shares=10.0,
        cost_basis_usd=5.0,
        state="pending_exit",
        exit_state="retry_pending",
        next_exit_retry_at="2000-01-01T00:00:00+00:00",
        token_id=f"{YES_TOKEN}-retry",
        no_token_id=f"{NO_TOKEN}-retry",
    )

    def trade_fact(_conn, position, *, exit_order_id=None):
        if position.trade_id != malformed.trade_id:
            return None
        return {
            "closes_position": False,
            "intended_reduction_shares": "10",
            "filled_size": "10",
            "fill_price": "0.40",
            "venue_order_id": exit_order_id or malformed.last_exit_order_id,
            "fill_states": "CONFIRMED",
        }

    released = []
    monkeypatch.setattr(
        exit_lifecycle,
        "_exit_trade_fact_close_candidate",
        trade_fact,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_pending_retries",
        lambda position, **_kwargs: released.append(position.trade_id) or True,
    )
    exit_lifecycle._PENDING_EXIT_SCAN_CURSOR = 0

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[malformed, retryable]),
        object(),
        max_positions=2,
        cycle_budget_seconds=100.0,
    )

    assert stats["pending_exit_position_errors"] == 1
    assert stats["pending_exit_position_error_ids"] == [malformed.trade_id]
    assert stats["unchanged"] == 1
    assert stats["retried"] == 1
    assert released == [retryable.trade_id]
    assert stats["pending_exit_positions_scanned"] == 2


def test_pending_exit_reduction_isolation_rejects_post_mutation_error():
    from types import SimpleNamespace

    from src.execution import exit_lifecycle

    stats = {"unchanged": 0}
    assert (
        exit_lifecycle._isolate_pending_exit_reduction_precondition(
            stats,
            SimpleNamespace(trade_id="pos-post-mutation-error"),
            RuntimeError("confirmed reduction canonical projection failed"),
        )
        is False
    )
    assert stats == {"unchanged": 0}


def test_exit_lifecycle_skips_inactive_position_before_order_status_check(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-terminal-exit-residue",
        market_id="mkt-terminal-exit-residue",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="settled",
        exit_state="sell_pending",
        order_status="sell_pending_confirmation",
        last_exit_order_id="ord-terminal-exit-residue",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
    )
    portfolio = PortfolioState(positions=[position])

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"inactive position should not query venue order {order_id}")

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 0
    assert stats["skipped_inactive"] == 1


def test_exit_lifecycle_does_not_treat_closed_string_as_terminal(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-closed-string-pending-exit",
        market_id="mkt-closed-string-pending-exit",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        order_status="sell_pending_confirmation",
        last_exit_order_id="ord-closed-string-pending-exit",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
    )
    position.state = "closed"
    portfolio = PortfolioState(positions=[position])

    class FakeClob:
        calls = 0

        def get_order_status(self, order_id):
            assert order_id == "ord-closed-string-pending-exit"
            self.calls += 1
            return {"status": "LIVE"}

    clob = FakeClob()
    stats = exit_lifecycle.check_pending_exits(portfolio, clob, conn=conn)

    assert clob.calls == 1
    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert "skipped_inactive" not in stats


def test_pending_exit_does_not_poll_entry_order_as_exit_order(conn):
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-entry-order-not-exit",
        market_id="mkt-entry-order-not-exit",
        city="Paris",
        cluster="Paris",
        target_date="2026-06-20",
        bin_label="19C",
        direction="buy_no",
        strategy_key="opening_inertia",
        size_usd=3.8,
        entry_price=0.75,
        shares=5.06,
        cost_basis_usd=3.8,
        state="pending_exit",
        exit_state="sell_pending",
        order_id="entry-order-filled",
        order_status="filled",
        last_exit_order_id=None,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-entry-order-not-exit",
        entered_at=_NOW.isoformat(),
    )
    upsert_position_current(conn, build_position_current_projection(position))
    portfolio = PortfolioState(positions=[position])

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"entry order must not be polled as exit order: {order_id}")

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats["retried"] == 1
    assert stats["released_no_order"] == 1
    assert stats["unchanged"] == 0
    assert position.exit_state == ""
    assert position.order_status == "filled"
    events = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no
        """,
        (position.trade_id,),
    ).fetchall()
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "EXIT_RETRY_RELEASED"
    assert event["phase_before"] == "pending_exit"
    assert event["phase_after"] == "active"
    assert event["venue_status"] == "ready"


def test_pending_exit_releases_terminal_exit_order_without_polling_it(conn):
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position
    from src.state.projection import upsert_position_current
    from src.state.venue_command_repo import append_event

    position = Position(
        trade_id="pos-terminal-exit-order-release",
        market_id="mkt-terminal-exit-order-release",
        city="Paris",
        cluster="Paris",
        target_date="2026-06-20",
        bin_label="19C",
        direction="buy_no",
        strategy_key="opening_inertia",
        size_usd=0.01,
        entry_price=0.75,
        shares=0.006614,
        chain_shares=0.006614,
        cost_basis_usd=0.005,
        state="pending_exit",
        exit_state="sell_pending",
        order_id="ord-terminal-exit-order-release",
        order_status="sell_pending_confirmation",
        last_exit_order_id="ord-terminal-exit-order-release",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-terminal-exit-order-release",
        entered_at=_NOW.isoformat(),
    )
    upsert_position_current(conn, build_position_current_projection(position))
    _insert_exit_command(
        conn,
        command_id="cmd-terminal-exit-order-release",
        position_id=position.trade_id,
        venue_order_id=position.last_exit_order_id,
        size=37.2,
        price=0.95,
    )
    _ack_exit(
        conn,
        command_id="cmd-terminal-exit-order-release",
        venue_order_id=position.last_exit_order_id,
    )
    append_event(
        conn,
        command_id="cmd-terminal-exit-order-release",
        event_type="EXPIRED",
        occurred_at=_NOW.isoformat(),
        payload={"reason": "terminal_partial_remainder"},
    )

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"terminal EXIT order must not be polled: {order_id}")

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        FakeClob(),
        conn=conn,
    )

    assert stats["retried"] == 1
    assert stats["released_no_order"] == 1
    assert position.state == "holding"
    assert position.exit_state == ""
    assert position.order_status == "filled"


def test_exit_lifecycle_full_fill_logs_commanded_execution_fact(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-full-exit",
        market_id="mkt-full-exit",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-full-exit",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        last_monitor_market_price=0.45,
        last_monitor_best_bid=0.44,
    )
    portfolio = PortfolioState(positions=[position])
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=20.0,
        close_position=True,
    )
    _insert_exit_command(
        conn,
        command_id="cmd-full-exit",
        position_id=position.trade_id,
        size=20.0,
        price=0.44,
        venue_order_id="ord-full-exit",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-full-exit"
            return {
                "status": "CONFIRMED",
                "remaining_size": "0.00",
                "matched_size": "20.00",
                "avgPrice": "0.44",
            }

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 1
    assert stats["retried"] == 0
    assert len(stats["filled_positions"]) == 1
    facts = _execution_facts(conn, position.trade_id)
    assert len(facts) == 1
    assert facts[0]["venue_status"] == "CONFIRMED"
    assert facts[0]["terminal_exec_status"] == "CONFIRMED"
    assert facts[0]["fill_price"] == pytest.approx(0.44)
    assert facts[0]["shares"] == pytest.approx(20.0)
    assert facts[0]["command_id"] == "cmd-full-exit"


@pytest.mark.parametrize("realized_fill_price", ["0.049", "0.951"])
def test_pending_exit_existing_confirmed_trade_fact_closes_before_retry_or_cancel(
    conn, realized_fill_price
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_event, append_trade_fact, get_command

    position = Position(
        trade_id="pos-filled-exit-fact",
        market_id="condition-test",
        city="Paris",
        cluster="Paris",
        target_date="2026-07-08",
        bin_label="Will the highest temperature in Paris be 34C on July 8?",
        direction="buy_yes",
        unit="C",
        size_usd=13.0415,
        entry_price=0.52,
        p_posterior=0.857866666666667,
        shares=25.0799,
        cost_basis_usd=13.0415,
        state="pending_exit",
        pre_exit_state="day0_window",
        exit_state="retry_pending",
        exit_retry_count=1,
        next_exit_retry_at="2026-07-08T14:44:50+00:00",
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        chain_state="synced",
        chain_shares=25.0799,
        chain_avg_price=0.52,
        chain_cost_basis_usd=13.0415,
        strategy_key="settlement_capture",
        strategy="settlement_capture",
        edge_source="settlement_capture",
        env="live",
        entered_at="2026-07-08T09:40:58+00:00",
        order_id="ord-entry",
        order_status="retry_pending",
        last_exit_order_id="ord-exit-filled",
        last_monitor_market_price=0.61,
        last_monitor_best_bid=0.61,
    )
    portfolio = PortfolioState(positions=[position])
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=25.0799,
        close_position=True,
    )
    _insert_exit_command(
        conn,
        command_id="cmd-exit-filled",
        position_id=position.trade_id,
        token_id=YES_TOKEN,
        size=25.0799,
        price=0.60,
        venue_order_id="ord-exit-filled",
    )
    _ack_exit(conn, command_id="cmd-exit-filled", venue_order_id="ord-exit-filled")
    append_event(
        conn,
        command_id="cmd-exit-filled",
        event_type="FILL_CONFIRMED",
        occurred_at="2026-07-08T14:42:59+00:00",
        payload={
            "reason": "place_exit_order_matched_submit",
            "venue_order_id": "ord-exit-filled",
            "trade_id": "trade-exit-filled",
            "filled_size": "25.0799",
            "fill_price": realized_fill_price,
            "tx_hash": "0xexitfilled",
        },
    )
    append_trade_fact(
        conn,
        trade_id="trade-exit-filled",
        venue_order_id="ord-exit-filled",
        command_id="cmd-exit-filled",
        state="CONFIRMED",
        filled_size="25.0799",
        fill_price=realized_fill_price,
        source="REST",
        observed_at="2026-07-08T14:42:59+00:00",
        raw_payload_hash="f" * 64,
        raw_payload_json={
            "source": "place_exit_order_matched_submit",
            "filled_size": "25.0799",
            "fill_price": realized_fill_price,
        },
        tx_hash="0xexitfilled",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"must not poll filled order: {order_id}")

        def cancel_order(self, order_id):
            raise AssertionError(f"must not cancel filled order: {order_id}")

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 1
    assert stats["filled_from_trade_fact"] == 1
    assert stats["retried"] == 0
    assert get_command(conn, "cmd-exit-filled")["state"] == "FILLED"

    current = conn.execute(
        """
        SELECT phase, order_status, exit_price, chain_shares,
               exit_retry_count, next_exit_retry_at
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert dict(current) == {
        "phase": "economically_closed",
        "order_status": "sell_filled",
        "exit_price": float(realized_fill_price),
        "chain_shares": 0.0,
        "exit_retry_count": 0,
        "next_exit_retry_at": "",
    }

    fill_event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, order_id, command_id, venue_status
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_FILLED'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert dict(fill_event) == {
        "event_type": "EXIT_ORDER_FILLED",
        "phase_before": "pending_exit",
        "phase_after": "economically_closed",
        "order_id": "ord-exit-filled",
        "command_id": "cmd-exit-filled",
        "venue_status": "sell_filled",
    }


def test_pending_exit_confirmed_tx_alias_cannot_fake_full_close(conn):
    from src.execution.exit_lifecycle import _exit_trade_fact_close_candidate
    from src.state.portfolio import Position
    from src.state.venue_command_repo import append_trade_fact

    position = Position(
        trade_id="pos-partial-exit-alias",
        market_id="condition-partial-exit-alias",
        city="Paris",
        cluster="Paris",
        target_date="2026-07-08",
        bin_label="Will the highest temperature in Paris be 34C on July 8?",
        direction="buy_yes",
        unit="C",
        size_usd=50.0,
        entry_price=0.5,
        shares=100.0,
        cost_basis_usd=50.0,
        state="pending_exit",
        exit_state="retry_pending",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        chain_state="synced",
        chain_shares=100.0,
        last_exit_order_id="ord-partial-exit-alias",
    )
    _insert_exit_command(
        conn,
        command_id="cmd-partial-exit-alias",
        position_id=position.trade_id,
        size=100.0,
        price=0.5,
        venue_order_id="ord-partial-exit-alias",
    )
    tx_hash = "0xpartial-exit-alias"
    for trade_id in ("trade-partial-exit-alias", tx_hash):
        append_trade_fact(
            conn,
            trade_id=trade_id,
            venue_order_id="ord-partial-exit-alias",
            command_id="cmd-partial-exit-alias",
            state="CONFIRMED",
            filled_size="50",
            fill_price="0.5",
            source="WS_USER" if trade_id != tx_hash else "REST",
            observed_at="2026-07-08T14:42:59+00:00",
            raw_payload_hash=hashlib.sha256(trade_id.encode()).hexdigest(),
            raw_payload_json={"trade_id": trade_id, "tx_hash": tx_hash},
            tx_hash=tx_hash,
        )

    assert _exit_trade_fact_close_candidate(conn, position) is None


@pytest.mark.parametrize("state", ["MATCHED", "MINED"])
def test_pending_exit_nonfinal_trade_fact_waits_for_confirmation(conn, state):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_trade_fact, get_command

    position = Position(
        trade_id="pos-nonfinal-exit-fact",
        market_id="condition-test",
        city="Paris",
        cluster="Paris",
        target_date="2026-07-08",
        bin_label="Will the highest temperature in Paris be 34C on July 8?",
        direction="buy_yes",
        unit="C",
        size_usd=13.0415,
        entry_price=0.52,
        p_posterior=0.857866666666667,
        shares=25.0799,
        cost_basis_usd=13.0415,
        state="pending_exit",
        pre_exit_state="day0_window",
        exit_state="retry_pending",
        exit_retry_count=1,
        next_exit_retry_at="2026-07-08T14:44:50+00:00",
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        chain_state="synced",
        chain_shares=25.0799,
        chain_avg_price=0.52,
        chain_cost_basis_usd=13.0415,
        strategy_key="settlement_capture",
        strategy="settlement_capture",
        edge_source="settlement_capture",
        env="live",
        entered_at="2026-07-08T09:40:58+00:00",
        order_id="ord-entry",
        order_status="retry_pending",
        last_exit_order_id="ord-exit-nonfinal",
        last_monitor_market_price=0.61,
        last_monitor_best_bid=0.61,
    )
    portfolio = PortfolioState(positions=[position])
    _insert_exit_command(
        conn,
        command_id="cmd-exit-nonfinal",
        position_id=position.trade_id,
        token_id=YES_TOKEN,
        size=25.07,
        price=0.60,
        venue_order_id="ord-exit-nonfinal",
    )
    _ack_exit(conn, command_id="cmd-exit-nonfinal", venue_order_id="ord-exit-nonfinal")
    append_trade_fact(
        conn,
        trade_id=f"trade-exit-nonfinal-{state.lower()}",
        venue_order_id="ord-exit-nonfinal",
        command_id="cmd-exit-nonfinal",
        state=state,
        filled_size="25.07",
        fill_price="0.61",
        source="REST",
        observed_at="2026-07-08T14:42:59+00:00",
        raw_payload_hash="e" * 64,
        raw_payload_json={
            "source": "place_exit_order_matched_submit",
            "filled_size": "25.07",
            "fill_price": "0.61",
        },
        tx_hash="0xexitnonfinal",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"nonfinal trade fact must not close or poll: {order_id}")

        def cancel_order(self, order_id):
            raise AssertionError(f"nonfinal trade fact must not cancel: {order_id}")

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats.get("filled_from_trade_fact", 0) == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert stats.get("exit_confirmation_pending", 0) == 1
    assert position.exit_state == "retry_pending"
    assert position.order_status == "retry_pending"
    assert get_command(conn, "cmd-exit-nonfinal")["state"] == "ACKED"
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_ORDER_FILLED'
            """,
            (position.trade_id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_RETRY_RELEASED'
            """,
            (position.trade_id,),
        ).fetchone()[0]
        == 0
    )


def test_exit_lifecycle_confirmed_without_explicit_fill_price_stays_pending(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-confirmed-no-fill-price",
        market_id="mkt-confirmed-no-fill-price",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-confirmed-no-fill-price",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        last_monitor_market_price=0.45,
        last_monitor_best_bid=0.44,
    )
    portfolio = PortfolioState(positions=[position])

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-confirmed-no-fill-price"
            return {
                "status": "CONFIRMED",
                "price": "0.44",
            }

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert stats["filled_positions"] == []
    assert position.state == "pending_exit"
    assert position.exit_state == "sell_pending"
    assert position.last_exit_error == "missing_exit_fill_price"
    assert position.shares == pytest.approx(20.0)
    assert position.size_usd == pytest.approx(10.0)
    assert position.cost_basis_usd == pytest.approx(10.0)
    assert position.nested_fills == []
    assert _execution_facts(conn, position.trade_id) == []


def test_exit_lifecycle_partial_without_explicit_fill_price_does_not_reduce_exposure(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-partial-no-fill-price",
        market_id="mkt-partial-no-fill-price",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-partial-no-fill-price",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        last_monitor_market_price=0.45,
        last_monitor_best_bid=0.44,
    )
    portfolio = PortfolioState(positions=[position])

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-partial-no-fill-price"
            return {
                "status": "PARTIALLY_MATCHED",
                "remaining_size": "12.00",
                "matched_size": "8.00",
                "price": "0.44",
            }

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert position.state == "pending_exit"
    assert position.exit_state == "sell_pending"
    assert position.last_exit_error == "missing_exit_fill_price"
    assert position.shares == pytest.approx(20.0)
    assert position.size_usd == pytest.approx(10.0)
    assert position.cost_basis_usd == pytest.approx(10.0)
    assert position.nested_fills == []
    assert _execution_facts(conn, position.trade_id) == []


@pytest.mark.parametrize("field", ["remaining_size", "matched_size"])
@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_exit_lifecycle_partial_nonfinite_size_does_not_reduce_exposure(conn, field, value):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id=f"pos-partial-nonfinite-{field}-{value}",
        market_id="mkt-partial-nonfinite-size",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-partial-nonfinite-size",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        last_monitor_market_price=0.45,
        last_monitor_best_bid=0.44,
    )
    portfolio = PortfolioState(positions=[position])
    payload = {
        "status": "PARTIALLY_MATCHED",
        "remaining_size": "12.00",
        "matched_size": "8.00",
        "avgPrice": "0.44",
    }
    payload[field] = value
    if field == "matched_size":
        payload.pop("remaining_size")

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-partial-nonfinite-size"
            return payload

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert position.state == "pending_exit"
    assert position.exit_state == "sell_pending"
    assert position.shares == pytest.approx(20.0)
    assert position.size_usd == pytest.approx(10.0)
    assert position.cost_basis_usd == pytest.approx(10.0)
    assert position.nested_fills == []
    assert _execution_facts(conn, position.trade_id) == []


@pytest.mark.parametrize("status", ["CONFIRMED", "PARTIALLY_MATCHED"])
@pytest.mark.parametrize("field", ["avgPrice", "fillPrice"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "1.2"])
def test_exit_lifecycle_invalid_explicit_fill_price_does_not_mutate(conn, status, field, value):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id=f"pos-nonfinite-fill-price-{status}-{field}-{value}",
        market_id="mkt-nonfinite-fill-price",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-nonfinite-fill-price",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        last_monitor_market_price=0.45,
        last_monitor_best_bid=0.44,
    )
    portfolio = PortfolioState(positions=[position])
    payload = {
        "status": status,
        "remaining_size": "12.00",
        "matched_size": "8.00",
        field: value,
    }

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-nonfinite-fill-price"
            return payload

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert stats["filled_positions"] == []
    assert position.state == "pending_exit"
    assert position.exit_state == "sell_pending"
    assert position.last_exit_error == "missing_exit_fill_price"
    assert position.shares == pytest.approx(20.0)
    assert position.size_usd == pytest.approx(10.0)
    assert position.cost_basis_usd == pytest.approx(10.0)
    assert position.nested_fills == []


def test_exit_lifecycle_cancel_after_partial_only_retries_remaining_exposure(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-partial-cancel",
        market_id="mkt-partial-cancel",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-27",
        bin_label="50-51°F",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-partial-cancel",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-partial-cancel",
        env="live",
        last_monitor_market_price=0.45,
        last_monitor_best_bid=0.44,
    )
    portfolio = PortfolioState(positions=[position])
    _insert_exit_command(
        conn,
        command_id="cmd-partial-cancel",
        position_id=position.trade_id,
        size=20.0,
        price=0.44,
        venue_order_id="ord-partial-cancel",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-partial-cancel"
            return {
                "status": "CANCELLED",
                "remaining_size": "12.00",
                "matched_size": "8.00",
                "avgPrice": "0.44",
            }

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == 0
    assert stats["retried"] == 1
    assert position.exit_state == "retry_pending"
    assert position.shares == pytest.approx(12.0)
    assert position.size_usd == pytest.approx(6.0)
    assert position.cost_basis_usd == pytest.approx(6.0)
    assert position.nested_fills[-1]["filled_shares"] == pytest.approx(8.0)
    assert position.nested_fills[-1]["remaining_shares"] == pytest.approx(12.0)
    facts = _execution_facts(conn, position.trade_id)
    assert len(facts) == 1
    assert facts[0]["venue_status"] == "CANCELLED"
    assert facts[0]["terminal_exec_status"] == "CANCELLED"
    assert facts[0]["fill_price"] == pytest.approx(0.44)
    assert facts[0]["shares"] == pytest.approx(8.0)
    assert facts[0]["command_id"] == "cmd-partial-cancel"


def test_two_exit_requests_for_same_position_collapse_into_one_durable_chain(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.collateral_ledger import CollateralLedger, configure_global_ledger

    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000, ctf={YES_TOKEN: 50}))
    configure_global_ledger(ledger)
    _allow_risk_allocator_for_exit_tests()
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.ws_gap_guard.assert_ws_allows_submit", lambda *args, **kwargs: None)

    calls: list[dict] = []

    class FakeClient:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def bind_signed_submission_identity_persister(self, persister):
            self.signed_identity_persister = persister

        def get_collateral_payload(self):
            return _fresh_exit_collateral_payload()

        def place_limit_order(self, **kwargs):
            calls.append(kwargs)
            return _fake_submit_result(self.bound_envelope, order_id="ord-1")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    try:
        first = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-1",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=_ensure_snapshot(conn),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-a",
        )
        second = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-1",
                token_id=YES_TOKEN,
                shares=4.0,
                current_price=0.51,
                best_bid=0.50,
                executable_snapshot_id=_ensure_snapshot(conn),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-b",
        )
        assert first.status == "pending"
        assert second.status == "rejected"
        assert "active_prior_exit_sell" in (second.reason or "")
        assert len(calls) == 1
        assert conn.execute("SELECT COUNT(*) FROM venue_commands WHERE position_id = ?", ("pos-1",)).fetchone()[0] == 1
    finally:
        from src.risk_allocator import clear_global_allocator

        clear_global_allocator()
        configure_global_ledger(None)


def test_execute_exit_order_uses_snapshot_tick_for_sell_price_planning(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.collateral_ledger import CollateralLedger, configure_global_ledger

    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000, ctf={YES_TOKEN: 50}))
    configure_global_ledger(ledger)
    _allow_risk_allocator_for_exit_tests()
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.ws_gap_guard.assert_ws_allows_submit", lambda *args, **kwargs: None)

    calls: list[dict] = []

    class FakeClient:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def bind_signed_submission_identity_persister(self, persister):
            self.signed_identity_persister = persister

        def get_collateral_payload(self):
            return _fresh_exit_collateral_payload()

        def place_limit_order(self, **kwargs):
            calls.append(kwargs)
            return _fake_submit_result(self.bound_envelope, order_id="ord-tick")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    snapshot_id = _ensure_snapshot(
        conn,
        snapshot_id="snap-exit-dynamic-tick",
        min_tick_size=Decimal("0.001"),
    )
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-dynamic-tick",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.533323782234957,
                best_bid=None,
                executable_snapshot_id=snapshot_id,
                executable_snapshot_min_tick_size=Decimal("0.001"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-dynamic-tick",
            q_version="posterior-9",
        )
        command_row = conn.execute(
            "SELECT price, state, decision_id, q_version "
            "FROM venue_commands WHERE position_id = ?",
            ("pos-dynamic-tick",),
        ).fetchone()

        assert result.status == "pending"
        assert result.submitted_price == pytest.approx(0.532)
        assert calls[0]["price"] == pytest.approx(0.532)
        assert command_row["price"] == pytest.approx(0.532)
        assert Decimal(str(command_row["price"])) % Decimal("0.001") == 0
        assert command_row["state"] == "ACKED"
        assert command_row["decision_id"] == "exit-dynamic-tick"
        assert command_row["q_version"] == "posterior-9"
    finally:
        from src.risk_allocator import clear_global_allocator

        clear_global_allocator()
        configure_global_ledger(None)


def test_exit_collateral_network_fetch_precedes_lease_and_persists_atomically(conn, monkeypatch):
    from src.execution import executor
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    _enable_exit_submit_prereqs(conn, monkeypatch)
    snapshot_id = _ensure_snapshot(conn, snapshot_id="snap-exit-prepared-collateral")
    conn.commit()
    events: list[str] = []
    lease_transaction_states: list[bool] = []
    submitted: list[dict] = []

    class FakeClient:
        def _ensure_v2_adapter(self):
            return self

        def get_ctf_collateral_payload(self, *, token_ids):
            assert token_ids == [YES_TOKEN]
            events.append("network")
            return {
                "authority_tier": "CHAIN",
                "ctf_token_balances": {YES_TOKEN: 50},
                "ctf_token_allowances": {YES_TOKEN: 50},
            }

        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def bind_signed_submission_identity_persister(self, persister):
            self.signed_identity_persister = persister

        def place_limit_order(self, **kwargs):
            submitted.append(kwargs)
            return _fake_submit_result(self.bound_envelope, order_id="ord-prepared-collateral")

    @contextmanager
    def recording_lease(lease_conn, **_kwargs):
        assert events == ["network"]
        lease_transaction_states.append(lease_conn.in_transaction)
        events.append("lease")
        yield

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    monkeypatch.setattr(executor, "_trade_writer_lease_required", lambda _conn: True)
    monkeypatch.setattr(executor, "_canonical_trade_write_lease", recording_lease)
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-prepared-collateral",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=snapshot_id,
            ),
            conn=conn,
            decision_id="prepared-collateral",
        )

        assert result.status == "pending"
        assert events == ["network", "lease"]
        assert lease_transaction_states == [False]
        assert submitted
        assert conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone()[0] == 2
        assert conn.execute(
            "SELECT command_id, reservation_type, token_id, amount FROM collateral_reservations"
        ).fetchone()[1:] == ("CTF_SELL", YES_TOKEN, _ctf_units(5.0))
        assert conn.in_transaction is False
    finally:
        _clear_exit_submit_prereqs()


def test_exit_writer_lease_timeout_has_no_venue_call_or_partial_rows(conn, monkeypatch):
    from src.execution import executor
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.write_coordinator import WriteLeaseTimeout

    _enable_exit_submit_prereqs(conn, monkeypatch)
    snapshot_id = _ensure_snapshot(conn, snapshot_id="snap-exit-lease-timeout")
    conn.commit()
    events: list[str] = []
    initial_snapshot_rows = conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone()[0]

    class FakeClient:
        def _ensure_v2_adapter(self):
            return self

        def get_ctf_collateral_payload(self, *, token_ids):
            assert token_ids == [YES_TOKEN]
            events.append("network")
            return {
                "authority_tier": "CHAIN",
                "ctf_token_balances": {YES_TOKEN: 50},
                "ctf_token_allowances": {YES_TOKEN: 50},
            }

        def place_limit_order(self, **_kwargs):  # pragma: no cover - tripwire
            raise AssertionError("lease timeout must prevent venue submit")

    @contextmanager
    def timed_out_lease(*_args, **_kwargs):
        raise WriteLeaseTimeout("test TRADE lease timeout")
        yield  # pragma: no cover - contextmanager protocol

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    monkeypatch.setattr(executor, "_trade_writer_lease_required", lambda _conn: True)
    monkeypatch.setattr(executor, "_canonical_trade_write_lease", timed_out_lease)
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-lease-timeout",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=snapshot_id,
            ),
            conn=conn,
            decision_id="exit-lease-timeout",
        )

        assert result.status == "rejected"
        assert result.reason and result.reason.startswith("pre_submit_db_locked_transient:")
        assert events == ["network"]
        assert conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone()[0] == initial_snapshot_rows
        assert conn.execute("SELECT COUNT(*) FROM collateral_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
        assert conn.in_transaction is False
    finally:
        _clear_exit_submit_prereqs()


def test_exit_ctf_reservation_failure_rolls_back_snapshot_command_event_and_reservation(conn, monkeypatch):
    from src.execution import executor
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.collateral_ledger import CollateralInsufficient, CollateralLedger

    _enable_exit_submit_prereqs(conn, monkeypatch)
    snapshot_id = _ensure_snapshot(conn, snapshot_id="snap-exit-ctf-rollback")
    conn.commit()
    baseline = {
        "snapshots": conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone()[0],
        "commands": conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0],
        "events": conn.execute("SELECT COUNT(*) FROM venue_command_events").fetchone()[0],
        "reservations": conn.execute("SELECT COUNT(*) FROM collateral_reservations").fetchone()[0],
    }
    trace: list[str] = []

    class FakeClient:
        def _ensure_v2_adapter(self):
            return self

        def get_ctf_collateral_payload(self, *, token_ids):
            assert token_ids == [YES_TOKEN]
            return {
                "authority_tier": "CHAIN",
                "ctf_token_balances": {YES_TOKEN: 50},
                "ctf_token_allowances": {YES_TOKEN: 50},
            }

        def place_limit_order(self, **_kwargs):  # pragma: no cover - tripwire
            raise AssertionError("CTF reservation failure must prevent venue submit")

    original_cas = CollateralLedger._cas_insert_ctf_reservation

    def fail_after_ctf_insert(ledger_conn, command_id, token_id, amount, now):
        original_cas(ledger_conn, command_id, token_id, amount, now)
        raise CollateralInsufficient("injected CTF reservation failure")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    monkeypatch.setattr(executor, "_trade_writer_lease_required", lambda _conn: True)
    monkeypatch.setattr(CollateralLedger, "_cas_insert_ctf_reservation", fail_after_ctf_insert)
    conn.set_trace_callback(trace.append)
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-ctf-rollback",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=snapshot_id,
            ),
            conn=conn,
            decision_id="exit-ctf-rollback",
        )
        trace_before_assertions = list(trace)

        assert result.status == "rejected"
        assert result.reason and result.reason.startswith("pre_submit_collateral_reservation_failed:")
        assert conn.in_transaction is False
        assert not any(statement.strip().upper() == "COMMIT" for statement in trace_before_assertions)
        assert any(statement.strip().upper() == "ROLLBACK" for statement in trace_before_assertions)
        assert conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone()[0] == baseline["snapshots"]
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == baseline["commands"]
        assert conn.execute("SELECT COUNT(*) FROM venue_command_events").fetchone()[0] == baseline["events"]
        assert conn.execute("SELECT COUNT(*) FROM collateral_reservations").fetchone()[0] == baseline["reservations"]
    finally:
        conn.set_trace_callback(None)
        _clear_exit_submit_prereqs()


def test_exit_unexpected_prevenue_failure_rolls_back_caller_transaction(conn, monkeypatch):
    from src.execution import executor
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    _enable_exit_submit_prereqs(conn, monkeypatch)
    snapshot_id = _ensure_snapshot(conn, snapshot_id="snap-exit-unexpected-rollback")
    conn.commit()
    baseline = {
        "snapshots": conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone()[0],
        "commands": conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0],
        "events": conn.execute("SELECT COUNT(*) FROM venue_command_events").fetchone()[0],
        "reservations": conn.execute("SELECT COUNT(*) FROM collateral_reservations").fetchone()[0],
    }

    class FakeClient:
        def _ensure_v2_adapter(self):
            return self

        def get_ctf_collateral_payload(self, *, token_ids):
            assert token_ids == [YES_TOKEN]
            return {
                "authority_tier": "CHAIN",
                "ctf_token_balances": {YES_TOKEN: 50},
                "ctf_token_allowances": {YES_TOKEN: 50},
            }

        def place_limit_order(self, **_kwargs):  # pragma: no cover - tripwire
            raise AssertionError("unexpected pre-venue failure must prevent submit")

    def fail_after_command_insert(*_args, **_kwargs):
        raise RuntimeError("injected unexpected pre-venue failure")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    monkeypatch.setattr(executor, "_trade_writer_lease_required", lambda _conn: True)
    monkeypatch.setattr("src.state.venue_command_repo.append_event", fail_after_command_insert)
    try:
        with pytest.raises(RuntimeError, match="injected unexpected pre-venue failure"):
            execute_exit_order(
                create_exit_order_intent(
                    trade_id="pos-exit-unexpected-rollback",
                    token_id=YES_TOKEN,
                    shares=5.0,
                    current_price=0.50,
                    best_bid=0.49,
                    executable_snapshot_id=snapshot_id,
                ),
                conn=conn,
                decision_id="exit-unexpected-rollback",
            )

        assert conn.in_transaction is False
        assert conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone()[0] == baseline["snapshots"]
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == baseline["commands"]
        assert conn.execute("SELECT COUNT(*) FROM venue_command_events").fetchone()[0] == baseline["events"]
        assert conn.execute("SELECT COUNT(*) FROM collateral_reservations").fetchone()[0] == baseline["reservations"]
    finally:
        _clear_exit_submit_prereqs()


def test_exit_refuses_caller_transaction_before_lease_without_rollback(conn, monkeypatch):
    from src.execution import executor
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    _enable_exit_submit_prereqs(conn, monkeypatch)
    snapshot_id = _ensure_snapshot(conn, snapshot_id="snap-exit-caller-transaction")
    conn.commit()
    conn.execute(
        """
        INSERT INTO collateral_reservations (
          command_id, reservation_type, token_id, amount, converted_amount, created_at
        ) VALUES (?, 'CTF_SELL', ?, ?, 0, ?)
        """,
        ("caller-owned-reservation", YES_TOKEN, 1, datetime.now(timezone.utc).isoformat()),
    )
    assert conn.in_transaction is True

    class ClientShouldNotBeConstructed:
        def __init__(self, *_args, **_kwargs):  # pragma: no cover - tripwire
            raise AssertionError("caller transaction must fail before collateral network fetch")

    @contextmanager
    def lease_must_not_be_acquired(*_args, **_kwargs):
        raise AssertionError("caller transaction must fail before writer lease")
        yield  # pragma: no cover - contextmanager protocol

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientShouldNotBeConstructed)
    monkeypatch.setattr(executor, "_trade_writer_lease_required", lambda _conn: True)
    monkeypatch.setattr(executor, "_canonical_trade_write_lease", lease_must_not_be_acquired)
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-caller-transaction",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=snapshot_id,
            ),
            conn=conn,
            decision_id="exit-caller-transaction",
        )

        assert result.status == "rejected"
        assert result.reason and result.reason.startswith("pre_submit_db_locked_transient:")
        assert conn.in_transaction is True
        assert conn.execute(
            "SELECT command_id FROM collateral_reservations WHERE command_id = ?",
            ("caller-owned-reservation",),
        ).fetchone()[0] == "caller-owned-reservation"
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    finally:
        conn.rollback()
        _clear_exit_submit_prereqs()


def test_exit_authority_deadline_is_rechecked_at_final_venue_seam(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    _enable_exit_submit_prereqs(conn, monkeypatch)
    venue_calls = []

    class Client:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def bind_signed_submission_identity_persister(self, persister):
            self.signed_identity_persister = persister

        def get_collateral_payload(self):
            return _fresh_exit_collateral_payload()

        def place_limit_order(self, **kwargs):
            venue_calls.append(kwargs)
            raise AssertionError("expired authority reached the venue")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    snapshot_id = _ensure_snapshot(conn, snapshot_id="snap-exit-expired-authority")
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-expired-authority",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=snapshot_id,
                executable_snapshot_hash=_snapshot_hash(conn, snapshot_id),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
                execution_authority_deadline_utc="2000-01-01T00:00:00+00:00",
            ),
            conn=conn,
            decision_id="exit-expired-authority",
        )

        assert result.status == "rejected"
        assert result.reason == "exit_execution_authority_expired_before_venue_submit"
        assert result.venue_call_started is False
        assert venue_calls == []
        command = conn.execute(
            "SELECT state FROM venue_commands WHERE position_id = ?",
            ("pos-expired-authority",),
        ).fetchone()
        assert command["state"] == "REJECTED"
    finally:
        _clear_exit_submit_prereqs()


def test_exit_snapshot_deadline_cannot_be_extended_by_caller(conn, monkeypatch):
    from types import SimpleNamespace

    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state import snapshot_repo

    _enable_exit_submit_prereqs(conn, monkeypatch)
    venue_calls = []
    snapshot_id = _ensure_snapshot(conn, snapshot_id="snap-exit-canonical-deadline")
    bound = {"value": False}
    real_get_snapshot = snapshot_repo.get_snapshot

    def current_snapshot(*args, **kwargs):
        snapshot = real_get_snapshot(*args, **kwargs)
        if snapshot is not None and bound["value"]:
            return SimpleNamespace(
                freshness_deadline=datetime(2000, 1, 1, tzinfo=timezone.utc)
            )
        return snapshot

    monkeypatch.setattr(snapshot_repo, "get_snapshot", current_snapshot)

    class Client:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope
            bound["value"] = True

        def bind_signed_submission_identity_persister(self, persister):
            self.signed_identity_persister = persister

        def get_collateral_payload(self):
            return _fresh_exit_collateral_payload()

        def place_limit_order(self, **kwargs):
            venue_calls.append(kwargs)
            raise AssertionError("expired canonical snapshot reached the venue")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Client)
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-canonical-deadline",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=snapshot_id,
                executable_snapshot_hash=_snapshot_hash(conn, snapshot_id),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
                execution_authority_deadline_utc="2099-01-01T00:00:00+00:00",
            ),
            conn=conn,
            decision_id="exit-canonical-deadline",
        )

        assert result.status == "rejected"
        assert result.reason == "exit_execution_authority_expired_before_venue_submit"
        assert result.venue_call_started is False
        assert venue_calls == []
    finally:
        _clear_exit_submit_prereqs()


def test_execute_exit_order_rejects_submit_connection_snapshot_hash_drift(monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.db import init_schema, init_schema_trade_only

    decision_conn = sqlite3.connect(":memory:")
    decision_conn.row_factory = sqlite3.Row
    decision_conn.execute("PRAGMA foreign_keys=ON")
    init_schema(decision_conn)
    init_schema_trade_only(decision_conn)
    submit_conn = sqlite3.connect(":memory:")
    submit_conn.row_factory = sqlite3.Row
    submit_conn.execute("PRAGMA foreign_keys=ON")
    init_schema(submit_conn)
    init_schema_trade_only(submit_conn)
    snapshot_id = "snap-exit-drift"
    _ensure_snapshot(decision_conn, snapshot_id=snapshot_id, raw_orderbook_hash="c" * 64)
    _ensure_snapshot(submit_conn, snapshot_id=snapshot_id, raw_orderbook_hash="d" * 64)
    _enable_exit_submit_prereqs(submit_conn, monkeypatch)

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("snapshot identity must block before SDK construction")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientShouldNotBeConstructed)
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-drift",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=snapshot_id,
                executable_snapshot_hash=_snapshot_hash(decision_conn, snapshot_id),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=submit_conn,
            decision_id="exit-drift",
        )

        assert result.status == "rejected"
        assert result.reason == "exit_snapshot_identity:snapshot_hash_mismatch"
        assert submit_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    finally:
        _clear_exit_submit_prereqs()
        decision_conn.close()
        submit_conn.close()


def test_execute_exit_order_rejects_existing_idempotent_command_with_old_exit_snapshot_identity(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    _enable_exit_submit_prereqs(conn, monkeypatch)
    calls: list[dict] = []

    class FakeClient:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def bind_signed_submission_identity_persister(self, persister):
            self.signed_identity_persister = persister

        def get_collateral_payload(self):
            units = _ctf_units(50.0)
            return {
                "pusd_balance_micro": 1_000_000_000,
                "pusd_allowance_micro": 1_000_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances_units": {YES_TOKEN: units},
                "ctf_token_allowances_units": {YES_TOKEN: units},
                "authority_tier": "CHAIN",
            }

        def place_limit_order(self, **kwargs):
            calls.append(kwargs)
            return _fake_submit_result(self.bound_envelope, order_id=f"ord-{len(calls)}")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    try:
        old_snapshot = _ensure_snapshot(conn, snapshot_id="snap-exit-old")
        new_snapshot = _ensure_snapshot(conn, snapshot_id="snap-exit-new", raw_orderbook_hash="d" * 64)
        first = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-idem",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=old_snapshot,
                executable_snapshot_hash=_snapshot_hash(conn, old_snapshot),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-idem-stable",
        )
        second = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-idem",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=new_snapshot,
                executable_snapshot_hash=_snapshot_hash(conn, new_snapshot),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-idem-stable",
        )

        assert first.status == "pending"
        assert second.status == "rejected"
        assert second.reason is not None
        assert second.reason.startswith("active_prior_exit_sell:")
        assert len(calls) == 1
        assert conn.execute("SELECT COUNT(*) FROM venue_commands WHERE position_id = ?", ("pos-exit-idem",)).fetchone()[0] == 1
    finally:
        _clear_exit_submit_prereqs()


def test_execute_exit_order_retries_after_no_side_effect_reject_with_new_exit_snapshot(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    _enable_exit_submit_prereqs(conn, monkeypatch)
    calls: list[dict] = []

    class PolyApiException(Exception):
        pass

    class RetryClient:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def bind_signed_submission_identity_persister(self, persister):
            self.signed_identity_persister = persister

        def get_collateral_payload(self):
            return _fresh_exit_collateral_payload()

        def place_limit_order(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise PolyApiException(
                    "PolyApiException[status_code=400, "
                    "error_message={'error': 'invalid POLY_GNOSIS_SAFE signature'}]"
                )
            return _fake_submit_result(self.bound_envelope, order_id="ord-retry-2")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", RetryClient)
    try:
        old_snapshot = _ensure_snapshot(conn, snapshot_id="snap-exit-retry-old")
        new_snapshot = _ensure_snapshot(
            conn,
            snapshot_id="snap-exit-retry-new",
            raw_orderbook_hash="d" * 64,
        )
        first = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-retry",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=old_snapshot,
                executable_snapshot_hash=_snapshot_hash(conn, old_snapshot),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-retry-stable",
        )
        second = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-retry",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=new_snapshot,
                executable_snapshot_hash=_snapshot_hash(conn, new_snapshot),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-retry-stable",
        )

        assert first.status == "rejected"
        assert "venue_auth_invalid_signature_400" in (first.reason or "")
        assert second.status == "pending"
        assert len(calls) == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE position_id = ?",
            ("pos-exit-retry",),
        ).fetchone()[0] == 2
    finally:
        _clear_exit_submit_prereqs()


def test_execute_exit_order_rejects_economic_unknown_with_old_exit_snapshot_identity(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    _enable_exit_submit_prereqs(conn, monkeypatch)
    calls: list[dict] = []

    class TimeoutClient:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def bind_signed_submission_identity_persister(self, persister):
            self.signed_identity_persister = persister

        def get_collateral_payload(self):
            return _fresh_exit_collateral_payload()

        def place_limit_order(self, **kwargs):
            calls.append(kwargs)
            raise TimeoutError("submit timed out")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", TimeoutClient)
    try:
        old_snapshot = _ensure_snapshot(conn, snapshot_id="snap-exit-unknown-old")
        new_snapshot = _ensure_snapshot(conn, snapshot_id="snap-exit-unknown-new", raw_orderbook_hash="d" * 64)
        first = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-unknown",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=old_snapshot,
                executable_snapshot_hash=_snapshot_hash(conn, old_snapshot),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-unknown-a",
        )
        second = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-unknown",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=new_snapshot,
                executable_snapshot_hash=_snapshot_hash(conn, new_snapshot),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-unknown-b",
        )

        assert first.status == "unknown_side_effect"
        assert second.status == "rejected"
        assert second.reason == "exit_snapshot_identity:existing_command_snapshot_id_mismatch"
        assert len(calls) == 1
        assert conn.execute("SELECT COUNT(*) FROM venue_commands WHERE position_id = ?", ("pos-exit-unknown",)).fetchone()[0] == 1
    finally:
        _clear_exit_submit_prereqs()


def test_execute_exit_order_rejects_idempotency_race_with_old_exit_snapshot_identity(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    import src.state.venue_command_repo as venue_command_repo

    _enable_exit_submit_prereqs(conn, monkeypatch)
    calls: list[dict] = []

    class FakeClient:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def bind_signed_submission_identity_persister(self, persister):
            self.signed_identity_persister = persister

        def get_collateral_payload(self):
            units = _ctf_units(50.0)
            return {
                "pusd_balance_micro": 1_000_000_000,
                "pusd_allowance_micro": 1_000_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances_units": {YES_TOKEN: units},
                "ctf_token_allowances_units": {YES_TOKEN: units},
                "authority_tier": "CHAIN",
            }

        def place_limit_order(self, **kwargs):
            calls.append(kwargs)
            return _fake_submit_result(self.bound_envelope, order_id=f"ord-race-{len(calls)}")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", FakeClient)
    try:
        old_snapshot = _ensure_snapshot(conn, snapshot_id="snap-exit-race-old")
        new_snapshot = _ensure_snapshot(conn, snapshot_id="snap-exit-race-new", raw_orderbook_hash="d" * 64)
        first = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-race",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=old_snapshot,
                executable_snapshot_hash=_snapshot_hash(conn, old_snapshot),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-race-stable",
        )
        assert first.status == "pending"

        real_find = venue_command_repo.find_command_by_idempotency_key
        find_calls = {"n": 0}

        def racing_find(c, idem):
            find_calls["n"] += 1
            if find_calls["n"] == 1:
                return None
            return real_find(c, idem)

        def racing_insert(*args, **kwargs):
            raise sqlite3.IntegrityError("UNIQUE constraint failed: venue_commands.idempotency_key")

        monkeypatch.setattr(venue_command_repo, "find_command_by_idempotency_key", racing_find)
        monkeypatch.setattr(venue_command_repo, "insert_command", racing_insert)
        second = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-exit-race",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
                executable_snapshot_id=new_snapshot,
                executable_snapshot_hash=_snapshot_hash(conn, new_snapshot),
                executable_snapshot_min_tick_size=Decimal("0.01"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-race-stable",
        )

        assert second.status == "rejected"
        assert second.reason is not None
        assert second.reason.startswith("active_prior_exit_sell:")
        assert find_calls["n"] == 1
        assert len(calls) == 1
    finally:
        _clear_exit_submit_prereqs()


def test_exit_lifecycle_resolves_latest_fresh_snapshot_for_executor(conn, monkeypatch):
    from src.execution import exit_lifecycle

    captured = {}
    snapshot_id = _ensure_snapshot(conn, token_id=YES_TOKEN, snapshot_id="snap-exit-lifecycle")

    def fake_execute_exit_order(intent):
        captured.update(
            snapshot_id=intent.executable_snapshot_id,
            snapshot_hash=intent.executable_snapshot_hash,
            min_tick=intent.executable_snapshot_min_tick_size,
            min_order=intent.executable_snapshot_min_order_size,
            neg_risk=intent.executable_snapshot_neg_risk,
        )
        return exit_lifecycle.OrderResult(trade_id=intent.trade_id, status="pending")

    monkeypatch.setattr(exit_lifecycle, "execute_exit_order", fake_execute_exit_order)

    result = exit_lifecycle.place_sell_order(
        trade_id="pos-1",
        token_id=YES_TOKEN,
        shares=5.0,
        current_price=0.50,
        best_bid=0.49,
        execution_proof_verified=True,
        **exit_lifecycle._latest_exit_snapshot_context(conn, YES_TOKEN, now=_NOW),
    )

    assert result.status == "pending"
    assert captured == {
        "snapshot_id": snapshot_id,
        "snapshot_hash": _snapshot_hash(conn, snapshot_id),
        "min_tick": "0.01",
        "min_order": "0.01",
        "neg_risk": False,
    }


def test_direct_sell_adapter_requires_verified_execution_proof(monkeypatch):
    from src.execution import exit_lifecycle

    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unverified direct adapter call must not reach the executor")
        ),
    )

    result = exit_lifecycle.place_sell_order(
        trade_id="pos-unverified-direct-sell",
        token_id=YES_TOKEN,
        shares=5.0,
        current_price=0.50,
        best_bid=0.49,
    )

    assert result.status == "rejected"
    assert result.reason == "exit_execution_proof_required"


def test_direct_sell_adapter_requires_time_bounded_execution_proof(monkeypatch):
    from src.execution import exit_lifecycle

    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unbounded direct adapter proof must not reach the executor")
        ),
    )

    result = exit_lifecycle.place_sell_order(
        trade_id="pos-unbounded-direct-sell",
        token_id=YES_TOKEN,
        shares=5.0,
        current_price=0.50,
        best_bid=0.49,
        execution_proof_verified=True,
    )

    assert result.status == "rejected"
    assert result.reason == "exit_execution_authority_deadline_required"


def test_exit_lifecycle_requires_snapshot_selected_token_for_native_side(conn):
    from src.execution import exit_lifecycle

    no_snapshot_id = _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=NO_TOKEN,
        outcome_label="NO",
        snapshot_id="snap-exit-no-selected",
        captured_at=_NOW,
    )
    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-exit-yes-selected-newer",
        captured_at=_NOW + timedelta(minutes=1),
    )

    context = exit_lifecycle._latest_exit_snapshot_context(
        conn,
        NO_TOKEN,
        now=_NOW + timedelta(minutes=2),
    )

    assert context["executable_snapshot_id"] == no_snapshot_id
    assert context["executable_snapshot_hash"] == _snapshot_hash(conn, no_snapshot_id)


def test_exit_snapshot_context_rejects_later_market_invalidation(conn):
    from src.execution import exit_lifecycle
    from src.state.snapshot_repo import record_snapshot_invalidation

    snapshot_id = _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        snapshot_id="snap-exit-invalidated",
        captured_at=_NOW,
        freshness_deadline=_NOW + timedelta(minutes=5),
    )
    record_snapshot_invalidation(
        conn,
        condition_id="condition-test",
        token_id=YES_TOKEN,
        reason="tick_size_change",
        invalidated_at=_NOW + timedelta(seconds=1),
    )
    checked_at = _NOW + timedelta(seconds=2)

    assert (
        exit_lifecycle._latest_exit_snapshot_context(
            conn,
            YES_TOKEN,
            now=checked_at,
        )
        == {}
    )
    assert (
        exit_lifecycle._exact_exit_snapshot_context(
            conn,
            YES_TOKEN,
            snapshot_id,
            "c" * 64,
            now=checked_at,
        )
        == {}
    )


def test_exit_snapshot_helpers_fail_closed_on_malformed_decimal(conn, monkeypatch):
    from src.execution import exit_lifecycle

    snapshot_id = _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        snapshot_id="snap-exit-malformed-decimal",
        freshness_deadline=_NOW + timedelta(minutes=5),
    )

    def malformed_snapshot(*_args, **_kwargs):
        raise InvalidOperation("malformed snapshot decimal")

    monkeypatch.setattr("src.state.snapshot_repo.get_snapshot", malformed_snapshot)

    assert exit_lifecycle._positive_decimal("NaN") is None
    assert (
        exit_lifecycle._latest_fresh_snapshot_min_order_for_token(
            YES_TOKEN,
            conn=conn,
            now=_NOW,
        )
        is None
    )
    assert exit_lifecycle._latest_exit_snapshot_context(conn, YES_TOKEN, now=_NOW) == {}
    assert (
        exit_lifecycle._exact_exit_snapshot_context(
            conn,
            YES_TOKEN,
            snapshot_id,
            "c" * 64,
            now=_NOW,
        )
        == {}
    )


def test_live_exit_captures_snapshot_for_held_position_before_sell(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    position = Position(
        trade_id="pos-exit-refresh",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
        cost_basis_usd=10.0,
        strategy_key="center_buy",
        env="test",
    )
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="EDGE_REVERSAL",
        current_market_price=0.50,
        current_market_price_is_fresh=True,
        best_bid=0.49,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(exit_lifecycle, "check_sell_collateral", lambda *args, **kwargs: (True, ""))
    monkeypatch.setattr(exit_lifecycle, "_refresh_exit_collateral_snapshot_for_submit", lambda *args, **kwargs: None)
    sibling = {
        "market_id": "condition-test",
        "condition_id": "condition-test",
        "question_id": "question-test",
        "token_id": YES_TOKEN,
        "no_token_id": NO_TOKEN,
        "title": "Will NYC high temp be 50-51°F?",
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "enable_orderbook": True,
        "range_low": 50,
        "range_high": 51,
        "token_map_raw": {"YES": YES_TOKEN, "NO": NO_TOKEN},
        "raw_gamma_payload_hash": "a" * 64,
        "gamma_market_raw": {
            "id": "gamma-test",
            "conditionId": "condition-test",
            "questionID": "question-test",
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
            "clobTokenIds": [YES_TOKEN, NO_TOKEN],
        },
    }
    monkeypatch.setattr("src.data.market_scanner.get_sibling_outcomes", lambda market_id: [sibling])
    monkeypatch.setattr("src.data.market_scanner.get_last_scan_authority", lambda: "VERIFIED")

    def fake_capture_snapshot(
        conn_arg,
        *,
        market,
        decision,
        clob,
        captured_at,
        scan_authority,
        execution_side,
        **_kwargs,
    ):
        assert scan_authority == "VERIFIED"
        assert execution_side == "SELL"
        assert market["outcomes"] == [sibling]
        assert decision.tokens["market_id"] == "condition-test"
        assert decision.edge.direction == "buy_yes"
        snapshot_id = _ensure_snapshot(
            conn_arg,
            token_id=YES_TOKEN,
            no_token_id=NO_TOKEN,
            selected_outcome_token_id=YES_TOKEN,
            outcome_label="YES",
            snapshot_id="snap-exit-captured",
            captured_at=captured_at,
        )
        return {
            "executable_snapshot_id": snapshot_id,
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "0.01",
            "executable_snapshot_neg_risk": False,
        }

    monkeypatch.setattr("src.data.market_scanner.capture_executable_market_snapshot", fake_capture_snapshot)

    def fake_execute_exit_order(intent, decision_id=""):
        captured.update(
            decision_id=decision_id,
            snapshot_id=intent.executable_snapshot_id,
            snapshot_hash=intent.executable_snapshot_hash,
            min_tick=intent.executable_snapshot_min_tick_size,
            min_order=intent.executable_snapshot_min_order_size,
            neg_risk=intent.executable_snapshot_neg_risk,
        )
        return exit_lifecycle.OrderResult(
            trade_id=intent.trade_id,
            status="pending",
            order_id="ord-exit-refresh",
            external_order_id="ord-exit-refresh",
        )

    monkeypatch.setattr(exit_lifecycle, "execute_exit_order", fake_execute_exit_order)

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-exit-refresh"
            return {"status": "OPEN"}

    result = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=FakeClob(),
        conn=conn,
    )

    assert result == "sell_pending: order=ord-exit-refresh, status=OPEN"
    assert str(captured.pop("decision_id")).startswith(
        "exit:pos-exit-refresh:"
    )
    assert captured == {
        "snapshot_id": "snap-exit-captured",
        "snapshot_hash": _snapshot_hash(conn, "snap-exit-captured"),
        "min_tick": "0.01",
        "min_order": "0.01",
        "neg_risk": False,
    }


def test_live_exit_uses_expired_snapshot_identity_when_static_topology_lacks_no_token(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    stale_snapshot_id = _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-expired-identity-seed",
        captured_at=_NOW - timedelta(minutes=10),
        freshness_deadline=_NOW - timedelta(minutes=9),
        accepting_orders=False,
    )
    assert stale_snapshot_id == "snap-expired-identity-seed"

    position = Position(
        trade_id="pos-exit-static-topology",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
    )

    monkeypatch.setattr(
        "src.data.market_scanner.get_sibling_outcomes",
        lambda market_id: [
            {
                "market_id": market_id,
                "condition_id": market_id,
                "token_id": YES_TOKEN,
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "gamma_market_raw": {
                    "id": "gamma-test-current",
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                },
            }
        ],
    )
    monkeypatch.setattr("src.data.market_scanner.get_last_scan_authority", lambda: "VERIFIED")

    def fake_capture_snapshot(
        conn_arg,
        *,
        market,
        decision,
        clob,
        captured_at,
        scan_authority,
        execution_side,
        **_kwargs,
    ):
        assert execution_side == "SELL"
        assert scan_authority == "VERIFIED"
        assert len(market["outcomes"]) == 1
        seeded = market["outcomes"][0]
        assert seeded["condition_id"] == "condition-test"
        assert seeded["question_id"] == "question-test"
        assert seeded["token_id"] == YES_TOKEN
        assert seeded["no_token_id"] == NO_TOKEN
        assert seeded["active"] is True
        assert seeded["accepting_orders"] is True
        assert seeded["gamma_market_raw"]["acceptingOrders"] is True
        assert seeded["source_contract"]["source"] == "executable_market_snapshots_identity_seed"
        assert decision.tokens["token_id"] == YES_TOKEN
        return {
            "executable_snapshot_id": _ensure_snapshot(
                conn_arg,
                token_id=YES_TOKEN,
                no_token_id=NO_TOKEN,
                selected_outcome_token_id=YES_TOKEN,
                outcome_label="YES",
                snapshot_id="snap-exit-refreshed-from-seed",
                captured_at=captured_at,
            ),
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "0.01",
            "executable_snapshot_neg_risk": False,
        }

    monkeypatch.setattr("src.data.market_scanner.capture_executable_market_snapshot", fake_capture_snapshot)

    context = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        object(),
        position,
        YES_TOKEN,
        now=_NOW,
    )

    assert context["executable_snapshot_id"] == "snap-exit-refreshed-from-seed"
    assert context["executable_snapshot_hash"] == _snapshot_hash(conn, "snap-exit-refreshed-from-seed")


def test_live_exit_static_topology_identity_seed_marks_clob_reconstructed_tradability(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=NO_TOKEN,
        outcome_label="NO",
        snapshot_id="snap-expired-static-identity-seed",
        captured_at=_NOW - timedelta(minutes=10),
        freshness_deadline=_NOW - timedelta(minutes=9),
        accepting_orders=True,
    )
    position = Position(
        trade_id="pos-exit-static-reconstructed",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_no",
        token_id="",
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
    )

    monkeypatch.setattr(
        "src.data.market_scanner.get_sibling_outcomes",
        lambda market_id: [
            {
                "market_id": market_id,
                "condition_id": market_id,
                "token_id": YES_TOKEN,
            }
        ],
    )
    monkeypatch.setattr("src.data.market_scanner.get_last_scan_authority", lambda: "VERIFIED")

    def fake_capture_snapshot(
        conn_arg,
        *,
        market,
        decision,
        clob,
        captured_at,
        scan_authority,
        execution_side,
        **_kwargs,
    ):
        seeded = market["outcomes"][0]
        assert seeded["no_token_id"] == NO_TOKEN
        assert seeded["gamma_market_raw"]["tradability_authority"] == "persisted_snapshot_reconstruction"
        assert "accepting_orders" not in seeded
        assert "acceptingOrders" not in seeded["gamma_market_raw"]
        return {
            "executable_snapshot_id": _ensure_snapshot(
                conn_arg,
                token_id=YES_TOKEN,
                no_token_id=NO_TOKEN,
                selected_outcome_token_id=NO_TOKEN,
                outcome_label="NO",
                snapshot_id="snap-exit-static-reconstructed",
                captured_at=captured_at,
            ),
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "0.01",
            "executable_snapshot_neg_risk": False,
        }

    monkeypatch.setattr("src.data.market_scanner.capture_executable_market_snapshot", fake_capture_snapshot)

    context = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        object(),
        position,
        NO_TOKEN,
        now=_NOW,
    )

    assert context["executable_snapshot_id"] == "snap-exit-static-reconstructed"


def test_live_exit_skips_fresh_snapshot_without_sell_bid_and_captures_new_one(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-fresh-missing-bid",
        captured_at=_NOW,
        freshness_deadline=_NOW + timedelta(minutes=5),
        orderbook_top_bid=None,
        orderbook_top_ask=Decimal("0.05"),
    )
    position = Position(
        trade_id="pos-exit-refresh-missing-bid",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.04,
        size_usd=10.0,
        shares=250.0,
    )

    monkeypatch.setattr(
        "src.data.market_scanner.get_sibling_outcomes",
        lambda market_id: [
            {
                "market_id": market_id,
                "condition_id": market_id,
                "question_id": "question-test",
                "token_id": YES_TOKEN,
                "no_token_id": NO_TOKEN,
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "gamma_market_raw": {
                    "id": "gamma-test-current",
                    "conditionId": market_id,
                    "questionID": "question-test",
                    "clobTokenIds": [YES_TOKEN, NO_TOKEN],
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                },
            }
        ],
    )
    monkeypatch.setattr("src.data.market_scanner.get_last_scan_authority", lambda: "VERIFIED")

    def fake_capture_snapshot(
        conn_arg,
        *,
        market,
        decision,
        clob,
        captured_at,
        scan_authority,
        execution_side,
        **_kwargs,
    ):
        assert execution_side == "SELL"
        return {
            "executable_snapshot_id": _ensure_snapshot(
                conn_arg,
                token_id=YES_TOKEN,
                no_token_id=NO_TOKEN,
                selected_outcome_token_id=YES_TOKEN,
                outcome_label="YES",
                snapshot_id="snap-exit-with-bid",
                captured_at=captured_at,
                orderbook_top_bid=Decimal("0.03"),
                orderbook_top_ask=Decimal("0.05"),
            ),
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "0.01",
            "executable_snapshot_neg_risk": False,
        }

    monkeypatch.setattr("src.data.market_scanner.capture_executable_market_snapshot", fake_capture_snapshot)

    context = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        object(),
        position,
        YES_TOKEN,
        now=_NOW,
    )

    assert context["executable_snapshot_id"] == "snap-exit-with-bid"


def test_live_exit_global_sell_capture_reuses_exact_prefetched_jit_book(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    jit_hash = "e" * 64
    jit_book = {
        "asset_id": YES_TOKEN,
        "tick_size": "0.01",
        "min_order_size": "5",
        "neg_risk": False,
        "bids": [{"price": "0.49", "size": "20"}],
        "asks": [{"price": "0.51", "size": "20"}],
    }
    position = Position(
        trade_id="pos-exit-prefetched-jit",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
    )
    monkeypatch.setattr(
        "src.data.market_scanner.get_sibling_outcomes",
        lambda market_id: [
            {
                "market_id": market_id,
                "condition_id": market_id,
                "question_id": "question-test",
                "token_id": YES_TOKEN,
                "no_token_id": NO_TOKEN,
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "gamma_market_raw": {
                    "id": "gamma-test-current",
                    "conditionId": market_id,
                    "questionID": "question-test",
                    "clobTokenIds": [YES_TOKEN, NO_TOKEN],
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                },
            }
        ],
    )
    monkeypatch.setattr(
        "src.data.market_scanner.get_last_scan_authority",
        lambda: "VERIFIED",
    )
    captured = []

    def fake_capture_snapshot(conn_arg, *, prefetched_orderbook, captured_at, **_kwargs):
        captured.append((prefetched_orderbook, captured_at))
        return {
            "executable_snapshot_id": _ensure_snapshot(
                conn_arg,
                token_id=YES_TOKEN,
                no_token_id=NO_TOKEN,
                selected_outcome_token_id=YES_TOKEN,
                outcome_label="YES",
                snapshot_id="snap-exit-prefetched-jit",
                captured_at=captured_at,
                raw_orderbook_hash=jit_hash,
                orderbook_top_bid=Decimal("0.49"),
                orderbook_top_ask=Decimal("0.51"),
            ),
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "5",
            "executable_snapshot_neg_risk": False,
        }

    monkeypatch.setattr(
        "src.data.market_scanner.capture_executable_market_snapshot",
        fake_capture_snapshot,
    )
    captured_at = _NOW - timedelta(seconds=1)
    context = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        object(),
        position,
        YES_TOKEN,
        now=captured_at,
        required_raw_orderbook_hash=jit_hash,
        prefetched_orderbook=jit_book,
    )

    assert context["executable_snapshot_id"] == "snap-exit-prefetched-jit"
    assert captured == [(jit_book, captured_at)]


def test_live_exit_recovers_missing_clob_for_fresh_snapshot_capture_idempotently(
    conn,
    monkeypatch,
):
    """A missing caller transport must not strand a sell with current authority."""
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-exit-stale-before-owned-client-recovery",
        captured_at=_NOW - timedelta(minutes=10),
        freshness_deadline=_NOW - timedelta(minutes=9),
    )
    position = Position(
        trade_id="pos-exit-owned-client-recovery",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.04,
        size_usd=10.0,
        shares=250.0,
    )
    owned_clob = object()
    captures: list[object] = []

    monkeypatch.setattr(exit_lifecycle, "_held_monitor_clob_client", lambda: owned_clob)
    monkeypatch.setattr(
        "src.data.market_scanner.get_sibling_outcomes",
        lambda market_id: [
            {
                "market_id": market_id,
                "condition_id": market_id,
                "question_id": "question-test",
                "token_id": YES_TOKEN,
                "no_token_id": NO_TOKEN,
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "gamma_market_raw": {
                    "id": "gamma-test-current",
                    "conditionId": market_id,
                    "questionID": "question-test",
                    "clobTokenIds": [YES_TOKEN, NO_TOKEN],
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                },
            }
        ],
    )
    monkeypatch.setattr("src.data.market_scanner.get_last_scan_authority", lambda: "VERIFIED")

    def fake_capture_snapshot(conn_arg, *, clob, captured_at, **_kwargs):
        captures.append(clob)
        return {
            "executable_snapshot_id": _ensure_snapshot(
                conn_arg,
                token_id=YES_TOKEN,
                no_token_id=NO_TOKEN,
                selected_outcome_token_id=YES_TOKEN,
                outcome_label="YES",
                snapshot_id="snap-exit-owned-client-recovered",
                captured_at=captured_at,
                freshness_deadline=captured_at + timedelta(minutes=5),
                orderbook_top_bid=Decimal("0.06"),
                orderbook_top_ask=Decimal("0.07"),
            ),
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "0.01",
            "executable_snapshot_neg_risk": False,
        }

    monkeypatch.setattr("src.data.market_scanner.capture_executable_market_snapshot", fake_capture_snapshot)

    first = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        None,
        position,
        YES_TOKEN,
        now=_NOW,
    )
    second = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        None,
        position,
        YES_TOKEN,
        now=_NOW,
    )

    assert first["executable_snapshot_id"] == "snap-exit-owned-client-recovered"
    assert second["executable_snapshot_id"] == "snap-exit-owned-client-recovered"
    assert captures == [owned_clob]


def test_live_exit_missing_clob_fails_closed_when_owned_transport_is_unavailable(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-exit-owned-client-unavailable",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.04,
        size_usd=10.0,
        shares=250.0,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_held_monitor_clob_client",
        lambda: (_ for _ in ()).throw(RuntimeError("transport unavailable")),
    )
    monkeypatch.setattr(
        "src.data.market_scanner.capture_executable_market_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unavailable transport must not capture")
        ),
    )

    context = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        None,
        position,
        YES_TOKEN,
        now=_NOW,
    )

    assert context == {}


def test_live_exit_identity_seed_does_not_reuse_stale_accepting_orders_as_tradability(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-expired-stale-tradability",
        captured_at=_NOW - timedelta(minutes=10),
        freshness_deadline=_NOW - timedelta(minutes=9),
        accepting_orders=True,
    )
    position = Position(
        trade_id="pos-exit-stale-tradability",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
    )
    current_non_tradable = {
        "market_id": "condition-test",
        "condition_id": "condition-test",
        "token_id": YES_TOKEN,
        "active": True,
        "closed": False,
        "accepting_orders": False,
        "enable_orderbook": True,
        "gamma_market_raw": {
            "id": "gamma-test-current",
            "active": True,
            "closed": False,
            "acceptingOrders": False,
            "enableOrderBook": True,
        },
    }
    monkeypatch.setattr(
        "src.data.market_scanner.get_sibling_outcomes",
        lambda market_id: [current_non_tradable],
    )
    monkeypatch.setattr("src.data.market_scanner.get_last_scan_authority", lambda: "VERIFIED")

    context = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        object(),
        position,
        YES_TOKEN,
        now=_NOW,
    )

    assert context == {}


def test_live_exit_quick_confirmed_without_explicit_fill_price_does_not_close(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    position = Position(
        trade_id="pos-quick-confirmed-no-fill-price",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
        cost_basis_usd=10.0,
        strategy_key="center_buy",
        env="test",
    )
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="EDGE_REVERSAL",
        current_market_price=0.50,
        current_market_price_is_fresh=True,
        best_bid=0.49,
    )

    monkeypatch.setattr(exit_lifecycle, "check_sell_collateral", lambda *args, **kwargs: (True, ""))
    monkeypatch.setattr(exit_lifecycle, "_refresh_exit_collateral_snapshot_for_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *_args, **_kwargs: {
            "executable_snapshot_id": "snap-quick-confirmed-no-fill-price",
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "0.01",
            "executable_snapshot_neg_risk": False,
            "executable_snapshot_orderbook_top_bid": "0.49",
        },
    )

    def fake_place_sell_order(**kwargs):
        return exit_lifecycle.OrderResult(
            trade_id=kwargs["trade_id"],
            status="pending",
            order_id="ord-quick-confirmed-no-fill-price",
            external_order_id="ord-quick-confirmed-no-fill-price",
        )

    monkeypatch.setattr(exit_lifecycle, "place_sell_order", fake_place_sell_order)

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-quick-confirmed-no-fill-price"
            return {
                "status": "CONFIRMED",
                "price": "0.49",
            }

    result = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=FakeClob(),
        conn=conn,
    )

    assert result == "sell_pending: order=ord-quick-confirmed-no-fill-price, status=CONFIRMED, missing_fill_price"
    assert position.state == "pending_exit"
    assert position.exit_state == "sell_pending"
    assert position.last_exit_error == "missing_exit_fill_price"
    assert position.shares == pytest.approx(20.0)
    assert position.size_usd == pytest.approx(10.0)
    assert position.cost_basis_usd == pytest.approx(10.0)
    assert portfolio.positions == [position]


def test_live_exit_delegates_collateral_authority_to_executor(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    calls = []
    position = Position(
        trade_id="pos-refresh-before-collateral",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
        cost_basis_usd=10.0,
        state="holding",
        strategy_key="opening_inertia",
        env="test",
    )
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="EDGE_REVERSAL",
        current_market_price=0.50,
        current_market_price_is_fresh=True,
        best_bid=0.49,
    )

    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
            lambda *args, **kwargs: {
                "executable_snapshot_id": "snap-exit-collateral",
                "executable_snapshot_min_order_size": "5",
                "executable_snapshot_orderbook_top_bid": "0.49",
            },
        )
    monkeypatch.setattr(
        exit_lifecycle,
        "_refresh_exit_collateral_snapshot_for_submit",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("lifecycle must not run the executor's fetch-only collateral seam")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_sell_collateral",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("lifecycle must not re-check a different collateral snapshot")
        ),
    )

    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **kwargs: (
            calls.append(kwargs["token_id"])
            or exit_lifecycle.OrderResult(
                trade_id=kwargs["trade_id"],
                status="rejected",
                reason=(
                    "ctf_tokens_insufficient: token_id=yes-token-001 "
                    "required=20000000 available=0"
                ),
            )
        ),
    )
    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome.startswith("sell_error: ctf_tokens_insufficient")
    assert calls == [YES_TOKEN]
    assert position.exit_state == "retry_pending"


def test_live_exit_executor_collateral_refresh_failure_retries(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    position = Position(
        trade_id="pos-refresh-failed",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
        cost_basis_usd=10.0,
        state="holding",
        strategy_key="opening_inertia",
        env="test",
    )
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="EDGE_REVERSAL",
        current_market_price=0.50,
        current_market_price_is_fresh=True,
        best_bid=0.49,
    )

    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
            lambda *args, **kwargs: {
                "executable_snapshot_id": "snap-exit-collateral-refresh-failed",
                "executable_snapshot_min_order_size": "5",
                "executable_snapshot_orderbook_top_bid": "0.49",
            },
        )
    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **kwargs: exit_lifecycle.OrderResult(
            trade_id=kwargs["trade_id"],
            status="rejected",
            reason="collateral_refresh_failed: network",
        ),
    )

    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome == "sell_error: collateral_refresh_failed: network"
    assert position.exit_state == "retry_pending"
    assert position.last_exit_error == "collateral_refresh_failed: network"


def test_live_exit_missing_executable_snapshot_retries_before_executor(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    position = Position(
        trade_id="pos-missing-exit-snapshot",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
        cost_basis_usd=10.0,
        state="holding",
        strategy_key="opening_inertia",
        env="test",
    )
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="EDGE_REVERSAL",
        current_market_price=0.50,
        current_market_price_is_fresh=True,
        best_bid=0.49,
    )

    monkeypatch.setattr(exit_lifecycle, "_latest_or_capture_exit_snapshot_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        exit_lifecycle,
        "_refresh_exit_collateral_snapshot_for_submit",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot gate must preempt collateral refresh")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_sell_collateral",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot gate must preempt collateral check")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot gate must preempt executor")
        ),
    )

    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome == "exit_blocked: executable_snapshot_unavailable"
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"
    assert position.exit_retry_count == 0
    assert position.last_exit_error == "exit_executable_snapshot_unavailable"
    event = conn.execute(
        """
        SELECT event_type, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert event["event_type"] == "EXIT_ORDER_REJECTED"
    assert event["phase_after"] == "pending_exit"
    assert event["venue_status"] == "retry_pending"
    assert json.loads(event["payload_json"])["error"] == "exit_executable_snapshot_unavailable"
    lifecycle_events = conn.execute(
        """
        SELECT event_type
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no
        """,
        (position.trade_id,),
    ).fetchall()
    assert [row["event_type"] for row in lifecycle_events][-2:] == [
        "EXIT_INTENT",
        "EXIT_ORDER_REJECTED",
    ]


def test_live_exit_snapshot_capture_exception_retries_after_intent(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, PortfolioState, Position

    position = Position(
        trade_id="pos-exit-snapshot-exception",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
        cost_basis_usd=10.0,
        state="holding",
        strategy_key="opening_inertia",
    )
    position.exit_reason = "red_force_exit"
    _seed_red_monitor_provenance(conn, position_id=position.trade_id, shares=20.0)
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="RED_FORCE_EXIT",
        current_market_price=0.50,
        current_market_price_is_fresh=True,
        best_bid=0.49,
    )
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.RED,
    )

    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("snapshot db locked")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_refresh_exit_collateral_snapshot_for_submit",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot exception must preempt collateral refresh")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_sell_collateral",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot exception must preempt collateral check")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("snapshot exception must preempt executor")
        ),
    )

    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome == "exit_blocked: executable_snapshot_error"
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"
    assert position.last_exit_error.startswith(
        "exit_executable_snapshot_error:RuntimeError:snapshot db locked"
    )
    lifecycle_events = conn.execute(
        """
        SELECT event_type, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no
        """,
        (position.trade_id,),
    ).fetchall()
    assert [row["event_type"] for row in lifecycle_events][-2:] == [
        "EXIT_INTENT",
        "EXIT_ORDER_REJECTED",
    ]
    rejected = lifecycle_events[-1]
    assert rejected["phase_after"] == "pending_exit"
    assert rejected["venue_status"] == "retry_pending"
    assert json.loads(rejected["payload_json"])["error"].startswith(
        "exit_executable_snapshot_error:RuntimeError:snapshot db locked"
    )


def test_live_exit_with_fresh_snapshot_but_no_bid_records_liquidity_block(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    fresh_now = datetime.now(timezone.utc)
    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-exit-fresh-no-bid",
        captured_at=fresh_now,
        freshness_deadline=fresh_now + timedelta(minutes=5),
        orderbook_top_bid=None,
        orderbook_top_ask=Decimal("0.02"),
    )
    position = Position(
        trade_id="pos-exit-fresh-no-bid",
        market_id="condition-test",
        condition_id="condition-test",
        city="Manila",
        cluster="asia",
        target_date="2026-07-02",
        bin_label="32C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.71,
        size_usd=10.0,
        shares=10.0,
        cost_basis_usd=7.10,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
    )
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="DAY0_HARD_FACT_BIN_DEAD",
        current_market_price=0.02,
        current_market_price_is_fresh=True,
        best_bid=None,
        day0_active=True,
    )

    monkeypatch.setattr(
        exit_lifecycle,
        "_held_monitor_clob_client",
        lambda: (_ for _ in ()).throw(
            AssertionError("fresh no-bid must not trigger transport recovery")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_refresh_exit_collateral_snapshot_for_submit",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no-bid liquidity block must preempt collateral refresh")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_sell_collateral",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no-bid liquidity block must preempt collateral check")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("no-bid liquidity block must preempt executor")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_hard_fact_sell_authority_valid",
        lambda *args, **kwargs: True,
    )

    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=None,
        conn=conn,
        hard_fact_authority=object(),
    )

    assert outcome == "exit_blocked: no_executable_bid"
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"
    assert position.exit_retry_count == 0
    assert position.last_exit_error == "exit_no_executable_bid"
    event = conn.execute(
        """
        SELECT event_type, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert event["event_type"] == "EXIT_ORDER_REJECTED"
    assert event["phase_after"] == "pending_exit"
    assert event["venue_status"] == "retry_pending"
    payload = json.loads(event["payload_json"])
    assert payload["error"] == "exit_no_executable_bid"
    assert payload["status"] == "liquidity_wait"
    lifecycle_events = conn.execute(
        """
        SELECT event_type
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no
        """,
        (position.trade_id,),
    ).fetchall()
    assert [row["event_type"] for row in lifecycle_events][-2:] == [
        "EXIT_INTENT",
        "EXIT_ORDER_REJECTED",
    ]


def test_exit_liquidity_classification_uses_snapshot_bid_truth():
    from src.execution.exit_lifecycle import ExitIntent, _exit_sell_liquidity_error

    intent = ExitIntent(
        trade_id="pos-no-bid-snapshot",
        reason="DAY0_HARD_FACT_BIN_DEAD",
        token_id="yes-token",
        shares=10.0,
        current_market_price=0.001,
        best_bid=0.001,
    )

    assert (
        _exit_sell_liquidity_error(
            intent,
            {"executable_snapshot_orderbook_top_bid": "ABSENT"},
        )
        == "exit_no_executable_bid"
    )
    assert (
        _exit_sell_liquidity_error(
            intent,
            {"executable_snapshot_orderbook_top_bid": "0.001"},
        )
        == "exit_no_in_band_bid"
    )
    assert (
        _exit_sell_liquidity_error(
            intent,
            {"executable_snapshot_orderbook_top_bid": "0.05"},
        )
        == "exit_no_in_band_bid"
    )
    in_band_intent = replace(intent, current_market_price=0.05, best_bid=0.05)
    assert (
        _exit_sell_liquidity_error(
            in_band_intent,
            {"executable_snapshot_orderbook_top_bid": "0.05"},
        )
        == ""
    )
    above_band_intent = replace(
        intent,
        current_market_price=0.999,
        best_bid=0.999,
    )
    assert (
        _exit_sell_liquidity_error(
            above_band_intent,
            {"executable_snapshot_orderbook_top_bid": "0.95"},
        )
        == ""
    )
    assert (
        _exit_sell_liquidity_error(
            in_band_intent,
            {"executable_snapshot_orderbook_top_bid": "0.999"},
        )
        == ""
    )


def test_live_exit_sub_floor_bid_waits_without_submit_or_retry_budget(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    now = datetime.now(timezone.utc)
    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-exit-sub-floor-bid",
        captured_at=now,
        freshness_deadline=now + timedelta(minutes=5),
        orderbook_top_bid=Decimal("0.01"),
        orderbook_top_ask=Decimal("0.011"),
    )
    position = Position(
        trade_id="pos-exit-sub-floor-bid",
        market_id="condition-test",
        condition_id="condition-test",
        city="Manila",
        cluster="asia",
        target_date="2026-07-02",
        bin_label="32C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.71,
        size_usd=10.0,
        shares=10.0,
        cost_basis_usd=7.10,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_refresh_exit_collateral_snapshot_for_submit",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sub-floor liquidity wait must preempt collateral")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sub-floor liquidity wait must not submit")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_hard_fact_sell_authority_valid",
        lambda *args, **kwargs: True,
    )

    outcome = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]),
        position,
        ExitContext(
            exit_reason="DAY0_HARD_FACT_BIN_DEAD",
            current_market_price=0.01,
            current_market_price_is_fresh=True,
            best_bid=0.01,
            day0_active=True,
        ),
        clob=None,
        conn=conn,
        hard_fact_authority=object(),
    )

    assert outcome == "exit_blocked: no_in_band_bid"
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"
    assert position.exit_retry_count == 0
    assert position.last_exit_error == "exit_no_in_band_bid"
    payload = json.loads(
        conn.execute(
            """
            SELECT payload_json FROM position_events
             WHERE position_id = ? ORDER BY sequence_no DESC LIMIT 1
            """,
            (position.trade_id,),
        ).fetchone()[0]
    )
    assert payload["status"] == "liquidity_wait"


def test_historical_sub_floor_rejection_becomes_liquidity_wait(conn):
    from src.execution.exit_lifecycle import _mark_exit_retry
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-historical-sub-floor-reject",
        market_id="condition-test",
        city="Seoul",
        cluster="asia",
        target_date="2026-07-22",
        bin_label="28C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        entry_price=0.51,
        size_usd=5.1,
        shares=10.0,
        cost_basis_usd=5.1,
        state="pending_exit",
        pre_exit_state="day0_window",
        exit_state="retry_pending",
        exit_retry_count=4,
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    historical_error = (
        "live order unit price outside absolute inclusive [0.05, 0.95] "
        "submit band: price=0.001"
    )

    _mark_exit_retry(
        position,
        reason="EDGE_REVERSAL [SELL_ERROR]",
        error=historical_error,
        conn=conn,
    )

    assert position.exit_retry_count == 4
    assert position.exit_state == "retry_pending"
    assert position.last_exit_error == "exit_no_in_band_bid"
    payload = json.loads(
        conn.execute(
            "SELECT payload_json FROM position_events WHERE position_id = ?",
            (position.trade_id,),
        ).fetchone()[0]
    )
    assert payload["status"] == "liquidity_wait"
    assert payload["original_error"] == historical_error


def test_backoff_exhausted_sub_floor_rejection_reenters_liquidity_wait(conn):
    from src.execution.exit_lifecycle import check_pending_retries
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-backoff-sub-floor-recover",
        market_id="condition-test",
        condition_id="condition-test",
        city="Seoul",
        cluster="asia",
        target_date="2026-07-22",
        bin_label="28C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.51,
        size_usd=5.1,
        shares=10.0,
        cost_basis_usd=5.1,
        state="pending_exit",
        pre_exit_state="day0_window",
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_retry_count=5,
        last_exit_error=(
            "live order unit price outside absolute inclusive [0.05, 0.95] "
            "submit band: price=0.001"
        ),
        strategy_key="forecast_qkernel_entry",
        env="live",
    )

    assert check_pending_retries(position, conn=conn) is False
    assert position.exit_state == "retry_pending"
    assert position.order_status == "retry_pending"
    assert position.exit_retry_count == 5
    assert position.last_exit_error == "exit_no_in_band_bid"


def test_backoff_exhausted_legacy_favorable_bid_reenters_global_auction(conn):
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-backoff-legacy-favorable-bid",
        market_id="condition-test",
        condition_id="condition-test",
        city="Shanghai",
        cluster="asia",
        target_date="2026-08-12",
        temperature_metric="high",
        bin_label="27C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.07,
        size_usd=1.4,
        shares=20.0,
        chain_shares=20.0,
        cost_basis_usd=1.4,
        chain_cost_basis_usd=1.4,
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_retry_count=46,
        exit_reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        last_exit_error=(
            "live_order_executable_price_out_of_bounds: best_bid=0.999"
        ),
        strategy_key="forecast_qkernel_entry",
        entered_at=_NOW.isoformat(),
        env="live",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=position.shares,
        close_position=True,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
    )

    assert not exit_lifecycle.release_backoff_exhausted_pending_exit_for_redecision(
        position,
        conn=conn,
    )
    assert position.exit_state == "backoff_exhausted"
    assert exit_lifecycle.check_pending_retries(position, conn=conn) is False
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"
    assert position.order_status == "retry_pending"
    assert position.exit_retry_count == 46
    assert position.last_exit_error.startswith(
        "global_sell_exit_executable_snapshot_error: "
        "legacy_favorable_bid_rejection:"
    )

    conn.commit()
    assert exit_lifecycle.check_pending_retries(
        position,
        conn=conn,
        global_sell_reauction_requester=lambda *_args: True,
    ) is True
    if conn.in_transaction:
        conn.commit()
    requested = []
    assert exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position,
        conn=conn,
        requester=lambda released, force_new: (
            requested.append((released.trade_id, force_new)) or True
        ),
    ) is True
    assert requested == [(position.trade_id, True)]


def test_backoff_exhausted_impossible_legacy_bid_stays_fail_closed(conn):
    from src.execution.exit_lifecycle import check_pending_retries
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-backoff-impossible-legacy-bid",
        market_id="condition-test",
        condition_id="condition-test",
        city="Shanghai",
        cluster="asia",
        target_date="2026-08-12",
        bin_label="27C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.07,
        size_usd=1.4,
        shares=20.0,
        chain_shares=20.0,
        cost_basis_usd=1.4,
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_retry_count=46,
        last_exit_error=(
            "live_order_executable_price_out_of_bounds: best_bid=1.001"
        ),
        strategy_key="forecast_qkernel_entry",
        env="live",
    )

    assert check_pending_retries(position, conn=conn) is False
    assert position.exit_state == "backoff_exhausted"
    assert position.order_status == "backoff_exhausted"
    assert position.exit_retry_count == 46


def test_live_exit_below_min_order_rejection_enters_dust_hold_not_retry(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, PortfolioState, Position

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        min_order_size="5",
        snapshot_id="snap-live-exit-below-min-order",
    )
    position = Position(
        trade_id="pos-dust-below-min-order",
        market_id="condition-test",
        condition_id="condition-test",
        city="Karachi",
        cluster="asia",
        target_date="2026-05-17",
        bin_label="37C+",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.37,
        size_usd=0.5873,
        shares=1.5873,
        cost_basis_usd=0.5873,
        state="day0_window",
        strategy_key="opening_inertia",
    )
    position.exit_reason = "red_force_exit"
    _seed_red_monitor_provenance(conn, position_id=position.trade_id, shares=1.5873)
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="RED_FORCE_EXIT",
        current_market_price=0.99,
        current_market_price_is_fresh=True,
        best_bid=0.99,
        hours_to_settlement=1.0,
        position_state="day0_window",
        day0_active=True,
    )
    error = "executable_snapshot_gate: size 1.5873 is below snapshot min_order_size 5"

    monkeypatch.setattr(exit_lifecycle, "check_sell_collateral", lambda *args, **kwargs: (True, ""))
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.RED,
    )
    monkeypatch.setattr(exit_lifecycle, "_refresh_exit_collateral_snapshot_for_submit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *args, **kwargs: {"executable_snapshot_min_order_size": "5"},
    )

    def fake_execute_exit_order(intent, decision_id=""):
        raise AssertionError("dust hold must not call executor")

    monkeypatch.setattr(exit_lifecycle, "execute_exit_order", fake_execute_exit_order)

    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome == f"sell_blocked_dust: {error}"
    assert position.state == "pending_exit"
    assert position.exit_state == "backoff_exhausted"
    assert position.next_exit_retry_at in ("", None)
    assert position.last_exit_error == error
    assert exit_lifecycle.check_pending_retries(position, conn=conn) is False
    assert (
        exit_lifecycle.release_backoff_exhausted_pending_exit_for_redecision(
            position,
            conn=conn,
        )
        is False
    )
    assert position.state == "pending_exit"
    assert position.exit_state == "backoff_exhausted"
    released = conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_RETRY_RELEASED'
        """,
        (position.trade_id,),
    ).fetchone()[0]
    assert released == 0
    current = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    event = conn.execute(
        """
        SELECT event_type, phase_after, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert event["event_type"] == "EXIT_ORDER_REJECTED"
    assert event["phase_after"] == "pending_exit"
    assert payload["status"] == "backoff_exhausted"
    assert payload["exit_block_class"] == "snapshot_min_order_dust"
    assert payload["exit_order_submitted"] is False
    assert payload["operator_action_required"] is True
    assert payload["held_to_settlement_unless_aggregate_exit_available"] is True
    assert payload["blocked_shares"] == "1.5873"
    assert payload["snapshot_min_order_size"] == "5"
    from src.state.db import query_position_current_status_view

    status_view = query_position_current_status_view(conn)
    assert status_view["exit_state_counts"]["backoff_exhausted"] == 1
    facts = _execution_facts(conn, position.trade_id)
    assert facts[-1]["venue_status"] == "backoff_exhausted"
    assert facts[-1]["terminal_exec_status"] == "backoff_exhausted"


def test_existing_canonical_dust_hold_suppresses_duplicate_exit_intent(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    error = "executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5"
    reason = f"DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES [DUST: {error}]"
    canonical = Position(
        trade_id="pos-dust-existing-canonical",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    exit_lifecycle._mark_exit_dust_hold(
        canonical,
        reason=reason,
        error=error,
        conn=conn,
    )
    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=NO_TOKEN,
        min_order_size="5",
        snapshot_id="snap-dust-existing-canonical-fresh",
    )
    before_events = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ?",
        (canonical.trade_id,),
    ).fetchone()[0]
    before_intents = conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_INTENT'
        """,
        (canonical.trade_id,),
    ).fetchone()[0]

    stale_runtime = Position(
        trade_id=canonical.trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    exit_context = ExitContext(
        exit_reason="DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES",
        current_market_price=0.001,
        current_market_price_is_fresh=True,
        best_bid=0.001,
        best_ask=0.002,
        hours_to_settlement=5.0,
        position_state="day0_window",
        day0_active=True,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("canonical dust hold must preempt snapshot capture")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("canonical dust hold must not submit a sell")
        ),
    )

    outcome = exit_lifecycle.execute_exit(
        PortfolioState(positions=[stale_runtime]),
        stale_runtime,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome == f"sell_blocked_dust: existing_canonical_dust_hold: {error}"
    assert stale_runtime.state == "pending_exit"
    assert stale_runtime.exit_state == "backoff_exhausted"
    assert stale_runtime.order_status == "backoff_exhausted"
    assert stale_runtime.exit_reason == reason
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ?",
        (canonical.trade_id,),
    ).fetchone()[0] == before_events
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_INTENT'
        """,
        (canonical.trade_id,),
    ).fetchone()[0] == before_intents


def test_existing_dust_hold_without_chain_evidence_does_not_emit_chain_correction(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    error = "executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5"
    reason = f"DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES [DUST: {error}]"
    canonical = Position(
        trade_id="pos-dust-existing-no-chain-correction",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    exit_lifecycle._mark_exit_dust_hold(
        canonical,
        reason=reason,
        error=error,
        conn=conn,
    )
    before_events = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ?",
        (canonical.trade_id,),
    ).fetchone()[0]

    stale_runtime = Position(
        trade_id=canonical.trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    exit_lifecycle._mark_exit_dust_hold(
        stale_runtime,
        reason=reason,
        error=error,
        conn=conn,
    )

    assert stale_runtime.state == "pending_exit"
    assert stale_runtime.exit_state == "backoff_exhausted"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ?",
        (canonical.trade_id,),
    ).fetchone()[0] == before_events
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'CHAIN_SIZE_CORRECTED'
        """,
        (canonical.trade_id,),
    ).fetchone()[0] == 0


def test_existing_dust_hold_chain_correction_is_idempotent(conn):
    from decimal import Decimal

    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    reason = "EXIT_CHAIN_DUST_STILL_HELD"
    error = "chain_balance_units=0;chain_balance_shares=0;asset_id=no-token"
    canonical = Position(
        trade_id="pos-dust-chain-correction-idempotent",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    exit_lifecycle._mark_exit_dust_hold(
        canonical,
        reason=reason,
        error=error,
        conn=conn,
    )

    for index in range(2):
        stale_runtime = Position(
            trade_id=canonical.trade_id,
            market_id="condition-test",
            condition_id="condition-test",
            city="Kuala Lumpur",
            cluster="asia",
            target_date="2026-07-08",
            bin_label="33C",
            direction="buy_no",
            token_id=YES_TOKEN,
            no_token_id=NO_TOKEN,
            entry_price=0.64,
            size_usd=0.64,
            shares=1.0,
            chain_shares=1.0,
            cost_basis_usd=0.64,
            state="day0_window",
            strategy_key="forecast_qkernel_entry",
            env="live",
        )
        exit_lifecycle._mark_exit_dust_hold(
            stale_runtime,
            reason=reason,
            error=error,
            conn=conn,
            chain_balance_units=0,
            chain_balance_shares=Decimal("0"),
            asset_id=NO_TOKEN,
        )
        assert stale_runtime.state == "pending_exit", index
        assert stale_runtime.exit_state == "backoff_exhausted", index

    start_sequence = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
        (canonical.trade_id,),
    ).fetchone()[0]
    for offset in range(1, 56):
        sequence_no = int(start_sequence) + offset
        payload = {
            "source": "exit_lifecycle",
            "reason": "chain_dust_projection_corrected",
            "chain_balance_units": offset,
            "chain_balance_shares": str(offset),
            "asset_id": f"other-asset-{offset}",
        }
        conn.execute(
            """
            INSERT INTO position_events (
                event_id,
                position_id,
                sequence_no,
                event_type,
                occurred_at,
                phase_before,
                phase_after,
                strategy_key,
                caused_by,
                idempotency_key,
                venue_status,
                source_module,
                env,
                payload_json
            ) VALUES (?, ?, ?, 'CHAIN_SIZE_CORRECTED', ?, 'pending_exit', 'pending_exit',
                      'forecast_qkernel_entry', 'chain_dust_projection_corrected', ?,
                      NULL, 'tests.test_exit_safety', 'live', ?)
            """,
            (
                f"{canonical.trade_id}:other-chain-dust:{offset}",
                canonical.trade_id,
                sequence_no,
                f"2026-07-08T12:{offset % 60:02d}:00+00:00",
                f"{canonical.trade_id}:other-chain-dust:{offset}",
                json.dumps(payload, sort_keys=True),
            ),
        )

    stale_runtime = Position(
        trade_id=canonical.trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    exit_lifecycle._mark_exit_dust_hold(
        stale_runtime,
        reason=reason,
        error=error,
        conn=conn,
        chain_balance_units=0,
        chain_balance_shares=Decimal("0"),
        asset_id=NO_TOKEN,
    )

    rows = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'CHAIN_SIZE_CORRECTED'
        """,
        (canonical.trade_id,),
    ).fetchall()
    matching_rows = [
        row
        for row in rows
        if (
            (payload := json.loads(row["payload_json"]))
            and payload.get("reason") == "chain_dust_projection_corrected"
            and payload.get("chain_balance_units") == 0
            and payload.get("chain_balance_shares") == "0"
            and payload.get("asset_id") == NO_TOKEN
        )
    ]
    assert len(matching_rows) == 1
    payload = json.loads(matching_rows[0]["payload_json"])
    assert payload["reason"] == "chain_dust_projection_corrected"
    assert payload["chain_balance_units"] == 0
    assert payload["chain_balance_shares"] == "0"
    assert payload["asset_id"] == NO_TOKEN


def test_existing_canonical_dust_hold_requires_fresh_snapshot_evidence(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    error = "executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5"
    reason = f"DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES [DUST: {error}]"
    position = Position(
        trade_id="pos-dust-existing-stale-snapshot",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    exit_lifecycle._mark_exit_dust_hold(
        position,
        reason=reason,
        error=error,
        conn=conn,
    )
    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=NO_TOKEN,
        min_order_size="5",
        snapshot_id="snap-dust-existing-canonical-stale",
        captured_at=_NOW,
        freshness_deadline=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )

    assert (
        exit_lifecycle._canonical_non_executable_dust_hold(
            position,
            conn=conn,
            now=datetime(2026, 7, 8, tzinfo=timezone.utc),
        )
        is None
    )


def test_existing_canonical_dust_hold_rejects_invalidated_snapshot(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position
    from src.state.snapshot_repo import record_snapshot_invalidation

    error = "executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5"
    position = Position(
        trade_id="pos-dust-existing-invalidated-snapshot",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    exit_lifecycle._mark_exit_dust_hold(
        position,
        reason=f"DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES [DUST: {error}]",
        error=error,
        conn=conn,
    )
    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=NO_TOKEN,
        min_order_size="5",
        snapshot_id="snap-dust-existing-canonical-invalidated",
        captured_at=_NOW,
        freshness_deadline=_NOW + timedelta(minutes=5),
    )
    record_snapshot_invalidation(
        conn,
        condition_id="condition-test",
        token_id=NO_TOKEN,
        reason="min_order_change",
        invalidated_at=_NOW + timedelta(seconds=1),
    )

    assert (
        exit_lifecycle._canonical_non_executable_dust_hold(
            position,
            conn=conn,
            now=_NOW + timedelta(seconds=2),
        )
        is None
    )


def test_backoff_release_blocks_snapshot_min_order_dust_without_reason_marker(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=NO_TOKEN,
        min_order_size="5",
        snapshot_id="snap-no-dust-min-order",
    )
    position = Position(
        trade_id="pos-dust-structural-min-order",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        strategy_key="forecast_qkernel_entry",
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_reason="DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES",
        last_exit_error="venue_rejected_sub_minimum_size",
        env="live",
    )

    assert (
        exit_lifecycle.release_backoff_exhausted_pending_exit_for_redecision(
            position,
            conn=conn,
        )
        is False
    )
    assert position.state == "pending_exit"
    assert position.exit_state == "backoff_exhausted"
    assert position.order_status == "backoff_exhausted"
    released = conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_RETRY_RELEASED'
        """,
        (position.trade_id,),
    ).fetchone()[0]
    assert released == 0


def test_backoff_release_ignores_historical_dust_without_current_snapshot(conn):
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-historical-dust-needs-current-authority",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        strategy_key="forecast_qkernel_entry",
        exit_state="backoff_exhausted",
        order_status="backoff_exhausted",
        exit_reason="EXIT_CHAIN_DUST_STILL_HELD",
        last_exit_error=(
            "executable_snapshot_gate: size 1 is below snapshot min_order_size 5"
        ),
        entered_at=_NOW.isoformat(),
        env="live",
    )
    upsert_position_current(conn, build_position_current_projection(position))

    assert exit_lifecycle.release_backoff_exhausted_pending_exit_for_redecision(
        position,
        conn=conn,
    )
    assert position.state == "day0_window"
    assert position.exit_state == ""


def test_retry_pending_snapshot_min_order_dust_becomes_hold_not_release(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=NO_TOKEN,
        min_order_size="5",
        orderbook_top_bid=None,
        orderbook_top_ask="0.001",
        snapshot_id="snap-retry-pending-dust-min-order",
        captured_at=_NOW,
        freshness_deadline=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    position = Position(
        trade_id="pos-retry-pending-dust-hold",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        strategy_key="forecast_qkernel_entry",
        exit_state="retry_pending",
        order_status="retry_pending",
        exit_reason="DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES",
        last_exit_error="exit_executable_snapshot_unavailable",
        next_exit_retry_at="2026-07-08T00:00:00+00:00",
        env="live",
    )

    assert exit_lifecycle.check_pending_retries(position, conn=conn) is False
    assert position.state == "pending_exit"
    assert position.exit_state == "backoff_exhausted"
    assert position.order_status == "backoff_exhausted"
    assert position.last_exit_error == (
        "executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5"
    )
    released = conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_RETRY_RELEASED'
        """,
        (position.trade_id,),
    ).fetchone()[0]
    assert released == 0
    rejected = conn.execute(
        """
        SELECT event_type, phase_after, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert rejected["event_type"] == "EXIT_ORDER_REJECTED"
    assert rejected["phase_after"] == "pending_exit"
    payload = json.loads(rejected["payload_json"])
    assert payload["status"] == "backoff_exhausted"
    assert payload["error"] == position.last_exit_error


def test_live_exit_snapshot_min_order_dust_hold_preempts_stale_collateral(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, PortfolioState, Position

    position = Position(
        trade_id="pos-dust-before-collateral",
        market_id="condition-test",
        condition_id="condition-test",
        city="Karachi",
        cluster="asia",
        target_date="2026-05-17",
        bin_label="37C+",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.37,
        size_usd=1.83,
        shares=4.95,
        cost_basis_usd=1.83,
        state="day0_window",
        strategy_key="opening_inertia",
    )
    position.exit_reason = "red_force_exit"
    _seed_red_monitor_provenance(conn, position_id=position.trade_id, shares=4.95)
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="RED_FORCE_EXIT",
        current_market_price=0.99,
        current_market_price_is_fresh=True,
        best_bid=0.99,
        hours_to_settlement=1.0,
        position_state="day0_window",
        day0_active=True,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *args, **kwargs: {"executable_snapshot_min_order_size": "5"},
    )
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.RED,
    )

    def stale_collateral(*args, **kwargs):
        raise AssertionError("collateral freshness must not override deterministic dust hold")

    monkeypatch.setattr(exit_lifecycle, "check_sell_collateral", stale_collateral)
    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sell should not be attempted")),
    )

    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome == "sell_blocked_dust: executable_snapshot_gate: size 4.95 is below snapshot min_order_size 5"
    assert position.state == "pending_exit"
    assert position.exit_state == "backoff_exhausted"
    assert position.next_exit_retry_at in ("", None)
    assert position.last_exit_error == "executable_snapshot_gate: size 4.95 is below snapshot min_order_size 5"
    current = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    from src.state.db import query_portfolio_loader_view

    loader_view = query_portfolio_loader_view(conn)
    loaded = next(row for row in loader_view["positions"] if row["trade_id"] == position.trade_id)
    assert loaded["state"] == "pending_exit"
    assert loaded["exit_state"] == "backoff_exhausted"


def test_live_exit_no_bid_snapshot_still_enforces_min_order_dust(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, PortfolioState, Position

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=NO_TOKEN,
        min_order_size="5",
        orderbook_top_bid=None,
        orderbook_top_ask="0.001",
        snapshot_id="snap-no-bid-dust-min-order",
        captured_at=_NOW,
        freshness_deadline=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    position = Position(
        trade_id="pos-no-bid-dust",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="asia",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    position.exit_reason = "red_force_exit"
    _seed_red_monitor_provenance(
        conn,
        position_id=position.trade_id,
        token_id=YES_TOKEN,
        shares=1.0,
        direction="buy_no",
        no_token_id=NO_TOKEN,
    )
    exit_context = ExitContext(
        exit_reason="RED_FORCE_EXIT",
        current_market_price=0.0,
        current_market_price_is_fresh=True,
        best_bid=0.0,
        best_ask=0.001,
        hours_to_settlement=1.0,
        position_state="day0_window",
        day0_active=True,
    )
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.RED,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dust must not submit")),
    )
    exit_intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=NO_TOKEN,
        shares=position.effective_shares,
        current_market_price=0.0,
        best_bid=0.0,
        exact_limit_price=0.001,
        submit_order_type="FAK",
        capital_certificate={
            "action": "SELL",
            "candidate_id": "global-dust-candidate",
            "actuation_identity": "global-dust-actuation",
            "economic_identity": "global-dust-economic",
            "probability_witness_identity": "global-dust-witness",
            "robust_delta_log_wealth": "0.001",
            "robust_ev_usd": "0.01",
            "held_shares": str(position.effective_shares),
            "selected_shares": str(position.effective_shares),
            "exact_limit_price": "0.001",
        },
    )

    outcome = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]),
        position,
        exit_context,
        clob=None,
        conn=conn,
        exit_intent=exit_intent,
    )

    error = "executable_snapshot_gate: size 1.0 is below snapshot min_order_size 5"
    assert outcome == f"sell_blocked_dust: {error}"
    assert position.state == "pending_exit"
    assert position.exit_state == "backoff_exhausted"
    assert position.order_status == "backoff_exhausted"
    assert position.last_exit_error == error
    rejected = conn.execute(
        """
        SELECT event_type, phase_after, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert rejected["event_type"] == "EXIT_ORDER_REJECTED"
    assert rejected["phase_after"] == "pending_exit"
    assert json.loads(rejected["payload_json"])["status"] == "backoff_exhausted"


def test_market_closed_pending_exit_backoff_repairs_to_day0_hold(conn):
    from src.execution.exit_lifecycle import (
        mark_market_closed_hold_to_settlement,
        release_market_closed_pending_exit_hold,
    )
    from src.contracts.semantic_types import ExitState
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-market-closed-hold",
        market_id="condition-test",
        city="Chicago",
        cluster="Chicago",
        target_date="2026-06-24",
        bin_label="88F",
        direction="buy_no",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-test",
        state="pending_exit",
        chain_state="synced",
        shares=12.0,
        chain_shares=12.0,
        cost_basis_usd=8.4,
        chain_cost_basis_usd=8.4,
        strategy_key="center_buy",
        env="live",
        entered_at="2026-06-24T10:00:00+00:00",
        order_status=ExitState.BACKOFF_EXHAUSTED,
        exit_state="backoff_exhausted",
        exit_reason="MARKET_CLOSED_AWAITING_SETTLEMENT",
        exit_retry_count=3,
    )

    assert release_market_closed_pending_exit_hold(position, conn=conn) is True

    assert position.state == "day0_window"
    assert position.exit_state == ""
    assert position.order_status == "filled"
    assert position.exit_reason == "MARKET_CLOSED_AWAITING_SETTLEMENT"
    assert position.exit_retry_count == 0

    current = conn.execute(
        """
        SELECT phase, order_status, exit_reason, exit_retry_count, next_exit_retry_at
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert dict(current) == {
        "phase": "day0_window",
        "order_status": "filled",
        "exit_reason": "MARKET_CLOSED_AWAITING_SETTLEMENT",
        "exit_retry_count": 0,
        "next_exit_retry_at": "",
    }
    event = conn.execute(
        """
        SELECT event_type, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert event["event_type"] == "MONITOR_REFRESHED"
    assert event["phase_after"] == "day0_window"
    assert event["venue_status"] is None
    assert payload["semantic_event"] == "MARKET_CLOSED_HOLD_TO_SETTLEMENT"
    assert payload["exit_order_submitted"] is False
    assert payload["exit_failure"] is False

    for sequence_no in range(2, 34):
        conn.execute(
            """
            INSERT INTO position_events (
                event_id,
                position_id,
                sequence_no,
                event_type,
                occurred_at,
                phase_before,
                phase_after,
                strategy_key,
                caused_by,
                idempotency_key,
                venue_status,
                source_module,
                env,
                payload_json
            ) VALUES (?, ?, ?, 'MONITOR_REFRESHED', ?, 'day0_window', 'day0_window',
                      'center_buy', 'test_normal_monitor', ?, 'filled',
                      'tests.test_exit_safety', 'live', '{}')
            """,
            (
                f"{position.trade_id}:normal-monitor:{sequence_no}",
                position.trade_id,
                sequence_no,
                f"2026-06-24T11:{sequence_no:02d}:00+00:00",
                f"{position.trade_id}:normal-monitor:{sequence_no}",
            ),
        )

    mark_market_closed_hold_to_settlement(
        position,
        reason="MARKET_CLOSED_AWAITING_SETTLEMENT",
        error="legacy_pending_exit_projection_repaired",
        conn=conn,
    )

    hold_payloads = [
        json.loads(row["payload_json"])
        for row in conn.execute(
            """
            SELECT payload_json
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'MONITOR_REFRESHED'
             ORDER BY sequence_no
            """,
            (position.trade_id,),
        ).fetchall()
    ]
    semantic_hold_count = sum(
        1
        for item in hold_payloads
        if item.get("semantic_event") == "MARKET_CLOSED_HOLD_TO_SETTLEMENT"
    )
    assert semantic_hold_count == 2
    hold_keys = [
        row["idempotency_key"]
        for row in conn.execute(
            """
            SELECT idempotency_key, payload_json
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'MONITOR_REFRESHED'
             ORDER BY sequence_no
            """,
            (position.trade_id,),
        ).fetchall()
        if json.loads(row["payload_json"]).get("semantic_event")
        == "MARKET_CLOSED_HOLD_TO_SETTLEMENT"
    ]
    assert len(set(hold_keys)) == 2

    mark_market_closed_hold_to_settlement(
        position,
        reason="MARKET_CLOSED_AWAITING_SETTLEMENT",
        error="legacy_pending_exit_projection_repaired",
        conn=conn,
    )
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'MONITOR_REFRESHED'
               AND json_extract(payload_json, '$.semantic_event')
                   = 'MARKET_CLOSED_HOLD_TO_SETTLEMENT'
            """,
            (position.trade_id,),
        ).fetchone()[0]
        == 2
    )


def test_after_settlement_stale_market_price_marks_closed_hold_not_retry(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    position = Position(
        trade_id="pos-after-settlement-stale-price",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        chain_state="synced",
        order_status="partial",
        strategy_key="forecast_qkernel_entry",
        entry_method="qkernel_spine",
        selected_method="qkernel_spine",
        last_monitor_prob=0.0,
        last_monitor_prob_is_fresh=True,
        env="live",
    )
    exit_context = ExitContext(
        exit_reason="DAY0_HARD_FACT_BIN_DEAD (final high extreme 33.0 resolved inside bin [33.0,33.0])",
        fresh_prob=0.0,
        fresh_prob_is_fresh=True,
        current_market_price=0.0,
        current_market_price_is_fresh=False,
        best_bid=0.0,
        hours_to_settlement=-0.5,
        position_state="day0_window",
        day0_active=True,
    )

    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("closed-market hold must not submit an exit order")
        ),
    )

    outcome = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]),
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome == "exit_blocked: market_closed_hold_to_settlement"
    assert position.state == "day0_window"
    assert position.exit_state == ""
    assert position.exit_retry_count == 0
    assert position.next_exit_retry_at in ("", None)
    assert position.exit_reason == "DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED"
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_ORDER_REJECTED'
            """,
            (position.trade_id,),
        ).fetchone()[0]
        == 0
    )
    event = conn.execute(
        """
        SELECT event_type, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert event["event_type"] == "MONITOR_REFRESHED"
    assert event["phase_after"] == "day0_window"
    assert event["venue_status"] is None
    assert payload["semantic_event"] == "MARKET_CLOSED_HOLD_TO_SETTLEMENT"
    assert payload["hold_reason"] == "DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED"
    assert payload["market_closed_error"] == "stale_current_market_price_after_settlement"
    assert payload["exit_order_submitted"] is False
    assert payload["exit_failure"] is False


def test_pre_settlement_stale_market_price_still_enters_retry(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    position = Position(
        trade_id="pos-pre-settlement-stale-price",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=0.64,
        shares=1.0,
        chain_shares=1.0,
        cost_basis_usd=0.64,
        state="day0_window",
        chain_state="synced",
        order_status="partial",
        strategy_key="forecast_qkernel_entry",
        env="live",
    )
    exit_context = ExitContext(
        exit_reason="DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES",
        fresh_prob=0.0,
        fresh_prob_is_fresh=True,
        current_market_price=0.0,
        current_market_price_is_fresh=False,
        best_bid=0.0,
        hours_to_settlement=0.5,
        position_state="day0_window",
        day0_active=True,
    )

    outcome = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]),
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome == "exit_blocked: stale_market_price"
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"
    event = conn.execute(
        """
        SELECT event_type, phase_after, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert event["event_type"] == "EXIT_ORDER_REJECTED"
    assert event["phase_after"] == "pending_exit"
    assert payload["error"] == "stale_current_market_price"
    assert payload["status"] == "retry_pending"


def test_red_stale_retry_preserves_intent_then_sells_after_green_risk_recovery(
    conn, monkeypatch
):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, PortfolioState, Position

    trade_id = "pos-red-obligation-retry"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=6.4,
        shares=10.0,
        chain_shares=10.0,
        cost_basis_usd=6.4,
        state="day0_window",
        chain_state="synced",
        strategy_key="center_buy",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_red_monitor_provenance(conn, position_id=trade_id)
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.GREEN,
    )

    class Clob:
        @staticmethod
        def get_order_status(_order_id):
            return {"status": "OPEN"}

    stale = ExitContext(
        exit_reason="RED_FORCE_EXIT",
        current_market_price=0.45,
        current_market_price_is_fresh=False,
        best_bid=0.45,
    )
    outcome = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]),
        position,
        stale,
        clob=Clob(),
        conn=conn,
    )
    assert outcome == "exit_blocked: stale_market_price"
    events = conn.execute(
        "SELECT event_type FROM position_events WHERE position_id = ? "
        "ORDER BY sequence_no",
        (trade_id,),
    ).fetchall()
    assert [row["event_type"] for row in events][-2:] == [
        "EXIT_INTENT",
        "EXIT_ORDER_REJECTED",
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ? "
        "AND intent_kind = 'EXIT' AND side = 'SELL'",
        (trade_id,),
    ).fetchone()[0] == 0

    position.next_exit_retry_at = (_NOW - timedelta(minutes=1)).isoformat()
    assert exit_lifecycle.check_pending_retries(position, conn=conn) is True
    assert position.state == "day0_window"
    assert position.exit_reason == "red_force_exit"

    def persist_and_return_pending(**kwargs):
        _insert_exit_command(
            conn,
            command_id="cmd-red-obligation-retry",
            position_id=trade_id,
            token_id=YES_TOKEN,
            size=10.0,
            price=0.45,
            venue_order_id="ord-red-obligation-retry",
        )
        return exit_lifecycle.OrderResult(
            trade_id=trade_id,
            status="pending",
            order_id="ord-red-obligation-retry",
            external_order_id="ord-red-obligation-retry",
        )

    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *_args, **_kwargs: {
            "executable_snapshot_id": "snapshot-red-obligation-retry",
            "executable_snapshot_orderbook_top_bid": 0.45,
            "executable_snapshot_min_order_size": 0.01,
        },
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_sell_collateral",
        lambda *_args, **_kwargs: (True, ""),
    )
    monkeypatch.setattr(exit_lifecycle, "place_sell_order", persist_and_return_pending)
    fresh = replace(stale, current_market_price_is_fresh=True)
    retry_outcome = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]),
        position,
        fresh,
        clob=Clob(),
        conn=conn,
    )
    assert retry_outcome.startswith("sell_pending:")
    command = conn.execute(
        "SELECT intent_kind, side, state FROM venue_commands "
        "WHERE command_id = 'cmd-red-obligation-retry'"
    ).fetchone()
    assert dict(command) == {
        "intent_kind": "EXIT",
        "side": "SELL",
        "state": "INTENT_CREATED",
    }


def test_red_intent_survives_partial_fill_and_nonterminal_override(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, Position

    trade_id = "pos-red-partial-residual"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=2.56,
        shares=4.0,
        chain_shares=4.0,
        cost_basis_usd=2.56,
        state="day0_window",
        chain_state="synced",
        strategy_key="center_buy",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_canonical_red_intent(
        conn,
        position_id=trade_id,
        token_id=YES_TOKEN,
        shares=10.0,
    )
    sequence_no = conn.execute(
        "SELECT MAX(sequence_no) + 1 FROM position_events WHERE position_id = ?",
        (trade_id,),
    ).fetchone()[0]
    conn.executemany(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, ?, ?, 'pending_exit', 'pending_exit',
                  'center_buy', 'tests.test_exit_safety', ?, 'live')
        """,
        [
            (
                f"{trade_id}:partial_fill:{sequence_no}",
                trade_id,
                sequence_no,
                "EXIT_ORDER_FILLED",
                (_NOW + timedelta(microseconds=1)).isoformat(),
                json.dumps({"filled_shares": 6.0}, sort_keys=True),
            ),
            (
                f"{trade_id}:manual_override:{sequence_no + 1}",
                trade_id,
                sequence_no + 1,
                "MANUAL_OVERRIDE_APPLIED",
                (_NOW + timedelta(microseconds=2)).isoformat(),
                json.dumps({"reason": "operator_review"}, sort_keys=True),
            ),
        ],
    )
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.GREEN,
    )

    assert exit_lifecycle._red_force_exit_authorized(
        position,
        ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
        conn=conn,
    ) is True

    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'SETTLED', ?, 'pending_exit', 'settled',
                  'center_buy', 'tests.test_exit_safety', ?, 'live')
        """,
        (
            f"{trade_id}:settled:{sequence_no + 2}",
            trade_id,
            sequence_no + 2,
            (_NOW + timedelta(microseconds=3)).isoformat(),
            json.dumps({"reason": "terminal"}, sort_keys=True),
        ),
    )
    assert exit_lifecycle._red_force_exit_authorized(
        position,
        ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
        conn=conn,
    ) is False


def test_red_obligation_survives_many_hold_monitor_rows(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, Position

    trade_id = "pos-red-many-holds"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=6.4,
        shares=10.0,
        chain_shares=10.0,
        cost_basis_usd=6.4,
        state="day0_window",
        chain_state="synced",
        strategy_key="center_buy",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_red_monitor_provenance(conn, position_id=trade_id)
    _seed_hold_monitor_rows(conn, position_id=trade_id, count=6000)
    red_monitor = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'MONITOR_REFRESHED'
           AND json_extract(payload_json, '$.exit_decision_should_exit') = 1
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (trade_id,),
    ).fetchone()
    assert "selected_outcome_token_id" not in json.loads(red_monitor["payload_json"])
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.GREEN,
    )

    assert exit_lifecycle._red_force_exit_authorized(
        position,
        ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
        conn=conn,
    ) is True

    intent = exit_lifecycle.ExitIntent(
        trade_id=trade_id,
        reason="RED_FORCE_EXIT",
        token_id=YES_TOKEN,
        shares=10.0,
        current_market_price=0.45,
        best_bid=0.45,
        decision_id="decision-red-many-holds",
    )
    assert exit_lifecycle._record_exit_intent_before_execution_gates(
        conn, position, intent
    ) is True
    trace: list[str] = []
    conn.set_trace_callback(trace.append)
    try:
        assert exit_lifecycle._red_force_exit_authorized(
            position,
            ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
            conn=conn,
        ) is True
    finally:
        conn.set_trace_callback(None)
    assert any(
        "INDEXED BY idx_position_events_position_type_sequence" in statement
        and "event_type = 'EXIT_INTENT'" in statement
        for statement in trace
    )
    assert not any("event_type = 'MONITOR_REFRESHED'" in statement for statement in trace)


def test_non_red_intent_persistence_failure_blocks_venue_sell(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

    position = Position(
        trade_id="pos-non-red-intent-persist-failure",
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=6.4,
        shares=10.0,
        chain_shares=10.0,
        state="holding",
        strategy_key="center_buy",
        env="test",
        exit_reason="EDGE_REVERSAL",
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_dual_write_canonical_pending_exit_if_available",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **_kwargs: pytest.fail("failed non-RED EXIT_INTENT reached venue"),
    )

    outcome = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]),
        position,
        ExitContext(
            exit_reason="EDGE_REVERSAL",
            current_market_price=0.45,
            current_market_price_is_fresh=True,
            best_bid=0.45,
        ),
        clob=object(),
        conn=conn,
    )

    assert outcome == "exit_blocked: exit_intent_persistence_failed"
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ? AND side = 'SELL'",
        (position.trade_id,),
    ).fetchone()[0] == 0


@pytest.mark.parametrize("failure", ("transition_false", "commit_exception"))
def test_red_intent_persistence_failure_blocks_venue_sell(conn, monkeypatch, failure):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, PortfolioState, Position

    trade_id = f"pos-red-intent-persist-{failure}"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=6.4,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        strategy_key="center_buy",
        chain_state="synced",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_canonical_position_identity(
        conn,
        position_id=trade_id,
        token_id=YES_TOKEN,
        shares=10.0,
    )
    _seed_red_monitor_provenance(conn, position_id=trade_id)
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.RED,
    )
    if failure == "transition_false":
        monkeypatch.setattr(
            exit_lifecycle,
            "_dual_write_canonical_pending_exit_if_available",
            lambda *_args, **_kwargs: False,
        )
    else:
        real_commit = exit_lifecycle._commit_exit_write_boundary

        def fail_intent_commit(boundary_conn, *, stage, deadline_monotonic=None):
            if stage == "exit_intent":
                raise RuntimeError("injected exit intent commit failure")
            return real_commit(
                boundary_conn,
                stage=stage,
                deadline_monotonic=deadline_monotonic,
            )

        monkeypatch.setattr(
            exit_lifecycle,
            "_commit_exit_write_boundary",
            fail_intent_commit,
        )
    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **_kwargs: pytest.fail("failed EXIT_INTENT persistence reached venue"),
    )

    outcome = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]),
        position,
        ExitContext(
            exit_reason="RED_FORCE_EXIT",
            current_market_price=0.45,
            current_market_price_is_fresh=True,
            best_bid=0.45,
        ),
        clob=object(),
        conn=conn,
    )

    assert outcome == "exit_blocked: exit_intent_persistence_failed"
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ? AND side = 'SELL'",
        (trade_id,),
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("canonical_phase", "canonical_shares", "risk_level"),
    (
        ("settled", 10.0, "RED"),
        ("settled", 10.0, "GREEN"),
        ("day0_window", 0.0, "RED"),
    ),
)
def test_red_authority_cannot_reopen_terminal_or_zero_canonical_position(
    conn,
    monkeypatch,
    canonical_phase,
    canonical_shares,
    risk_level,
):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, Position

    trade_id = f"pos-red-canonical-close-{canonical_phase}-{risk_level}"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=6.4,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_canonical_red_intent(
        conn,
        position_id=trade_id,
        token_id=YES_TOKEN,
        shares=10.0,
    )
    conn.execute(
        "UPDATE position_current SET phase = ?, shares = ?, chain_shares = ? "
        "WHERE position_id = ?",
        (canonical_phase, canonical_shares, canonical_shares, trade_id),
    )
    conn.commit()
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: getattr(RiskLevel, risk_level),
    )

    assert exit_lifecycle._red_force_exit_authorized(
        position,
        ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
        conn=conn,
    ) is False


def test_non_live_red_provenance_cannot_authorize_live_handoff(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, Position

    trade_id = "pos-red-replay-provenance"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=6.4,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_canonical_red_intent(
        conn,
        position_id=trade_id,
        token_id=YES_TOKEN,
        shares=10.0,
        env="test",
    )
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.GREEN,
    )

    assert exit_lifecycle._red_force_exit_authorized(
        position,
        ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
        conn=conn,
    ) is False


def test_red_provenance_rejects_canonical_token_mismatch(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, Position

    trade_id = "pos-red-canonical-token-mismatch"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=6.4,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_canonical_red_intent(
        conn,
        position_id=trade_id,
        token_id=NO_TOKEN,
        shares=10.0,
    )
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.GREEN,
    )

    assert exit_lifecycle._red_force_exit_authorized(
        position,
        ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
        conn=conn,
    ) is False


def test_red_authority_rejects_zero_canonical_chain_residual(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, Position

    trade_id = "pos-red-canonical-chain-zero"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=6.4,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_canonical_red_intent(
        conn,
        position_id=trade_id,
        token_id=YES_TOKEN,
        shares=10.0,
    )
    conn.execute(
        "UPDATE position_current SET shares = 10.0, chain_shares = 0.0, "
        "chain_state = 'synced' WHERE position_id = ?",
        (trade_id,),
    )
    conn.commit()
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.RED,
    )

    assert exit_lifecycle._red_force_exit_authorized(
        position,
        ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
        conn=conn,
    ) is False


@pytest.mark.parametrize(
    (
        "canonical_direction",
        "runtime_direction",
        "runtime_token",
        "canonical_yes",
        "canonical_no",
        "expected",
    ),
    (
        ("buy_yes", "buy_yes", YES_TOKEN, YES_TOKEN, NO_TOKEN, True),
        ("buy_no", "buy_no", NO_TOKEN, YES_TOKEN, NO_TOKEN, True),
        ("buy_yes", "buy_yes", NO_TOKEN, YES_TOKEN, NO_TOKEN, False),
        ("buy_no", "buy_no", YES_TOKEN, YES_TOKEN, NO_TOKEN, False),
        (None, "buy_yes", YES_TOKEN, YES_TOKEN, NO_TOKEN, False),
        ("unknown", "buy_yes", YES_TOKEN, YES_TOKEN, NO_TOKEN, False),
        ("buy_yes", "unknown", YES_TOKEN, YES_TOKEN, NO_TOKEN, False),
        ("buy_yes", "buy_yes", YES_TOKEN, "", NO_TOKEN, False),
        ("buy_no", "buy_no", NO_TOKEN, YES_TOKEN, "", False),
    ),
)
def test_red_runtime_identity_binds_directional_canonical_token(
    conn,
    canonical_direction,
    runtime_direction,
    runtime_token,
    canonical_yes,
    canonical_no,
    expected,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    trade_id = (
        "pos-red-directional-token-"
        f"{canonical_direction}-{runtime_direction}-{expected}"
    )
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction=runtime_direction,
        token_id=runtime_token if runtime_direction == "buy_yes" else canonical_yes,
        no_token_id=runtime_token if runtime_direction == "buy_no" else canonical_no,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        chain_state="synced",
        env="unknown_env",
        exit_reason="red_force_exit",
    )
    _seed_canonical_position_identity(
        conn,
        position_id=trade_id,
        token_id=canonical_yes,
        no_token_id=canonical_no,
        shares=10.0,
        direction=canonical_direction,
    )

    assert exit_lifecycle._red_runtime_position_open(
        conn, position, require_canonical=True
    ) is expected


def test_unknown_runtime_env_uses_live_canonical_red_provenance(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, Position

    trade_id = "pos-red-unknown-runtime-env"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        chain_state="synced",
        env="unknown_env",
        exit_reason="red_force_exit",
    )
    _seed_red_monitor_provenance(conn, position_id=trade_id)
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level", lambda: RiskLevel.RED
    )

    assert exit_lifecycle._red_force_exit_authorized(
        position,
        ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
        conn=conn,
    ) is True


def test_current_red_without_canonical_row_cannot_authorize(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, Position

    position = Position(
        trade_id="pos-red-no-canonical-row",
        market_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        chain_state="synced",
        env="unknown_env",
        exit_reason="red_force_exit",
    )
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level", lambda: RiskLevel.RED
    )

    assert exit_lifecycle._red_force_exit_authorized(
        position,
        ExitContext(exit_reason="RED_FORCE_EXIT", current_market_price=0.45),
        conn=conn,
    ) is False


def test_exit_intent_commit_failure_rolls_back_real_connection_boundary(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    trade_id = "pos-red-real-commit-rollback"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        chain_state="synced",
        strategy_key="center_buy",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_canonical_position_identity(
        conn,
        position_id=trade_id,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        shares=10.0,
    )
    conn.commit()
    conn.execute("BEGIN")

    class CommitFailingConnection:
        def __init__(self, delegate):
            self.delegate = delegate
            self.commit_calls = 0
            self.rollback_calls = 0

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        def commit(self):
            self.commit_calls += 1
            raise sqlite3.OperationalError("injected commit failure")

        def rollback(self):
            self.rollback_calls += 1
            return self.delegate.rollback()

    wrapped = CommitFailingConnection(conn)
    intent = exit_lifecycle.ExitIntent(
        trade_id=trade_id,
        reason="RED_FORCE_EXIT",
        token_id=YES_TOKEN,
        shares=10.0,
        current_market_price=0.45,
        best_bid=0.45,
        decision_id="decision-real-rollback",
    )

    assert exit_lifecycle._record_exit_intent_before_execution_gates(
        wrapped, position, intent
    ) is False
    assert wrapped.commit_calls == 1
    assert wrapped.rollback_calls >= 1
    assert conn.in_transaction is False
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = ? AND event_type = 'EXIT_INTENT'",
        (trade_id,),
    ).fetchone()[0] == 0
    projection_after_failure = conn.execute(
        "SELECT phase, order_id FROM position_current WHERE position_id = ?",
        (trade_id,),
    ).fetchone()
    assert projection_after_failure["phase"] == "day0_window"
    assert projection_after_failure["order_id"] is None
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ? AND side = 'SELL'",
        (trade_id,),
    ).fetchone()[0] == 0


def test_repeated_red_execute_adopts_same_active_sell_without_duplicate(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, PortfolioState, Position

    trade_id = "pos-red-repeated-adoption"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.64,
        size_usd=6.4,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        strategy_key="center_buy",
        chain_state="synced",
        env="live",
        exit_reason="red_force_exit",
    )
    _seed_canonical_position_identity(
        conn,
        position_id=trade_id,
        token_id=YES_TOKEN,
        shares=10.0,
    )
    _seed_red_monitor_provenance(conn, position_id=trade_id)
    _insert_exit_command(
        conn,
        command_id="cmd-red-repeated-adoption",
        position_id=trade_id,
        token_id=YES_TOKEN,
        size=10.0,
        price=0.45,
        venue_order_id="ord-red-repeated-adoption",
    )
    _ack_exit(
        conn,
        command_id="cmd-red-repeated-adoption",
        venue_order_id="ord-red-repeated-adoption",
    )
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: RiskLevel.RED,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **_kwargs: pytest.fail("active RED SELL was duplicated"),
    )
    context = ExitContext(
        exit_reason="RED_FORCE_EXIT",
        current_market_price=0.45,
        current_market_price_is_fresh=True,
        best_bid=0.45,
    )

    first = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]), position, context, clob=None, conn=conn
    )
    first_order = position.last_exit_order_id
    second = exit_lifecycle.execute_exit(
        PortfolioState(positions=[position]), position, context, clob=None, conn=conn
    )

    assert first.startswith("sell_pending: active_prior_exit_sell")
    assert second.startswith("sell_pending: active_prior_exit_sell")
    assert first_order == "ord-red-repeated-adoption"
    assert position.last_exit_order_id == first_order
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ? AND side = 'SELL'",
        (trade_id,),
    ).fetchone()[0] == 1


def test_red_intent_preserves_existing_order_projection(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    trade_id = "pos-red-intent-preserves-order"
    position = Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Kuala Lumpur",
        cluster="Kuala Lumpur",
        target_date="2026-07-08",
        bin_label="33C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        shares=10.0,
        chain_shares=10.0,
        state="day0_window",
        chain_state="synced",
        strategy_key="center_buy",
        env="live",
        exit_reason="red_force_exit",
        last_exit_order_id="ord-red-preserved",
        order_status="sell_pending",
    )
    _seed_canonical_position_identity(
        conn,
        position_id=trade_id,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        shares=10.0,
    )
    _insert_exit_command(
        conn,
        command_id="cmd-red-preserved",
        position_id=trade_id,
        token_id=YES_TOKEN,
        size=10.0,
        price=0.45,
        venue_order_id="ord-red-preserved",
    )
    intent = exit_lifecycle.ExitIntent(
        trade_id=trade_id,
        reason="RED_FORCE_EXIT",
        token_id=YES_TOKEN,
        shares=10.0,
        current_market_price=0.45,
        best_bid=0.45,
        decision_id="decision-red-preserved",
    )

    assert exit_lifecycle._record_exit_intent_before_execution_gates(
        conn, position, intent
    ) is True
    projection = conn.execute(
        "SELECT order_id, order_status FROM position_current WHERE position_id = ?",
        (trade_id,),
    ).fetchone()
    assert projection["order_id"] == "ord-red-preserved"
    assert projection["order_status"] == "sell_pending"
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ? AND side = 'SELL'",
        (trade_id,),
    ).fetchone()[0] == 1


def test_market_closed_hold_preserves_last_fresh_monitor_values(conn):
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution.exit_lifecycle import mark_market_closed_hold_to_settlement
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    persisted = Position(
        trade_id="pos-market-closed-preserve-monitor",
        market_id="condition-test",
        city="Chicago",
        cluster="Chicago",
        target_date="2026-06-24",
        bin_label="88F",
        direction="buy_no",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-test",
        state="day0_window",
        chain_state="synced",
        shares=12.0,
        chain_shares=12.0,
        cost_basis_usd=8.4,
        chain_cost_basis_usd=8.4,
        strategy_key="center_buy",
        env="live",
        entered_at="2026-06-24T10:00:00+00:00",
        order_status="filled",
        last_monitor_prob=0.91,
        last_monitor_prob_is_fresh=True,
        last_monitor_edge=0.16,
        last_monitor_market_price=0.75,
        last_monitor_market_price_is_fresh=True,
        last_monitor_best_bid=0.74,
        last_monitor_best_ask=0.76,
        last_monitor_market_vig=0.02,
    )
    upsert_position_current(conn, build_position_current_projection(persisted))

    stale_in_memory = Position(
        trade_id=persisted.trade_id,
        market_id=persisted.market_id,
        city=persisted.city,
        cluster=persisted.cluster,
        target_date=persisted.target_date,
        bin_label=persisted.bin_label,
        direction=persisted.direction,
        token_id=persisted.token_id,
        no_token_id=persisted.no_token_id,
        condition_id=persisted.condition_id,
        state="day0_window",
        chain_state="synced",
        shares=12.0,
        chain_shares=12.0,
        cost_basis_usd=8.4,
        chain_cost_basis_usd=8.4,
        strategy_key="center_buy",
        env="live",
        entered_at="2026-06-24T10:00:00+00:00",
        order_status="filled",
        last_monitor_prob=0.0,
        last_monitor_prob_is_fresh=True,
        last_monitor_edge=0.0,
        last_monitor_market_price=0.0,
        last_monitor_market_price_is_fresh=True,
    )

    mark_market_closed_hold_to_settlement(stale_in_memory, conn=conn)

    current = conn.execute(
        """
        SELECT last_monitor_prob, last_monitor_prob_is_fresh, last_monitor_edge,
               last_monitor_market_price, last_monitor_market_price_is_fresh,
               last_monitor_best_bid, last_monitor_best_ask, last_monitor_market_vig
          FROM position_current
         WHERE position_id = ?
        """,
        (persisted.trade_id,),
    ).fetchone()
    assert current["last_monitor_prob"] == pytest.approx(0.91)
    assert current["last_monitor_prob_is_fresh"] == 1
    assert current["last_monitor_edge"] == pytest.approx(0.16)
    assert current["last_monitor_market_price"] == pytest.approx(0.75)
    assert current["last_monitor_market_price_is_fresh"] == 1
    assert float(current["last_monitor_best_bid"]) == pytest.approx(0.74)
    assert float(current["last_monitor_best_ask"]) == pytest.approx(0.76)
    assert float(current["last_monitor_market_vig"]) == pytest.approx(0.02)

    event = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (persisted.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert payload["semantic_event"] == "MARKET_CLOSED_HOLD_TO_SETTLEMENT"
    assert payload["last_monitor_prob"] == pytest.approx(0.91)
    assert payload["last_monitor_market_price"] == pytest.approx(0.75)
    assert payload["last_monitor_prob_is_fresh"] is True
    assert payload["last_monitor_market_price_is_fresh"] is True
    assert "closed_market_hold_preserved_monitor_evidence" in payload["applied_validations"]


def test_position_projection_round_trips_zero_monitor_bid(conn):
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.portfolio import Position, _position_from_projection_row
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-zero-monitor-bid-roundtrip",
        market_id="condition-test",
        city="Manila",
        cluster="asia",
        target_date="2026-07-02",
        bin_label="32C",
        direction="buy_yes",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-test",
        state="day0_window",
        chain_state="synced",
        shares=10.0,
        chain_shares=10.0,
        cost_basis_usd=4.4,
        strategy_key="forecast_qkernel_entry",
        env="live",
        entered_at="2026-07-02T00:00:00+00:00",
        last_monitor_at="2026-07-02T01:00:00+00:00",
        order_status="filled",
        last_monitor_prob=0.0,
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.0,
        last_monitor_market_price_is_fresh=True,
        last_monitor_best_bid=0.0,
        last_monitor_best_ask=0.001,
        last_monitor_market_vig=None,
    )
    upsert_position_current(conn, build_position_current_projection(position))

    row = conn.execute(
        """
        SELECT *
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()

    restored = _position_from_projection_row(dict(row), current_mode="live")
    assert restored.last_monitor_market_price == pytest.approx(0.0)
    assert restored.last_monitor_market_price_is_fresh is True
    assert restored.last_monitor_best_bid == pytest.approx(0.0)
    assert restored.last_monitor_best_ask == pytest.approx(0.001)


def test_market_closed_hold_preserves_chain_backed_open_phase(conn):
    """BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md,
    post-T5-migration): this used to construct the position with
    state='quarantined'/chain_state='entry_authority_quarantined', relying on
    Position.__post_init__'s mixed-epoch bridge to remap those to their TRUE
    values (holding/synced) before mark_market_closed_hold_to_settlement ever
    saw them. The T5 schema migration has run, the DB CHECK no longer admits
    those literals, and the remap has been deleted — Position construction
    would now raise. Per REPLACEMENT PHASE LAW a confirmed-fill/chain-absence
    dispute keeps its TRUE phase directly, so this constructs the position
    with that TRUE shape (holding/synced) up front — same real assertion:
    the held-to-settlement hold folds a holding position to day0_window like
    any other open position, never a quarantine scar.
    """
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution.exit_lifecycle import mark_market_closed_hold_to_settlement
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-chain-backed-quarantine-hold",
        market_id="condition-test",
        city="Munich",
        cluster="Munich",
        target_date="2026-06-30",
        bin_label="30C",
        direction="buy_no",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-test",
        state="holding",
        chain_state="synced",
        shares=29.14,
        chain_shares=29.14,
        cost_basis_usd=21.27,
        chain_cost_basis_usd=21.27,
        strategy_key="opening_inertia",
        env="live",
        entered_at="2026-06-29T08:55:00+00:00",
        order_status="filled",
        exit_reason="entry_authority_chain_absence_conflict",
    )
    upsert_position_current(conn, build_position_current_projection(position))

    mark_market_closed_hold_to_settlement(position, conn=conn)

    assert position.state == "day0_window"
    current = conn.execute(
        """
        SELECT phase, chain_state, order_status, exit_reason
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert dict(current) == {
        "phase": "day0_window",
        "chain_state": "synced",
        "order_status": "filled",
        "exit_reason": "MARKET_CLOSED_AWAITING_SETTLEMENT",
    }
    event = conn.execute(
        """
        SELECT phase_after, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert event["phase_after"] == "day0_window"
    payload = json.loads(event["payload_json"])
    assert payload["semantic_event"] == "MARKET_CLOSED_HOLD_TO_SETTLEMENT"


def test_day0_monitor_projection_clears_stale_backoff_order_status(conn):
    from src.contracts.semantic_types import ExitState
    from src.engine.lifecycle_events import (
        build_monitor_refreshed_canonical_write,
        build_position_current_projection,
    )
    from src.state.portfolio import Position

    held = Position(
        trade_id="pos-day0-held-stale-backoff",
        market_id="condition-test",
        city="Chicago",
        cluster="Chicago",
        target_date="2026-06-24",
        bin_label="88F",
        direction="buy_no",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-test",
        state="day0_window",
        chain_state="synced",
        shares=12.0,
        chain_shares=12.0,
        cost_basis_usd=8.4,
        chain_cost_basis_usd=8.4,
        strategy_key="center_buy",
        env="live",
        entered_at="2026-06-24T10:00:00+00:00",
        order_status=ExitState.BACKOFF_EXHAUSTED,
        exit_state="",
        exit_reason="",
    )
    assert build_position_current_projection(held)["order_status"] == "filled"
    events, projection = build_monitor_refreshed_canonical_write(
        held,
        sequence_no=1,
        phase_after="day0_window",
        source_module="test",
    )
    assert projection["order_status"] == "filled"
    assert events[0]["venue_status"] == "filled"
    from src.state.db import append_many_and_project
    from src.state.projection import upsert_position_current

    stale_projection = dict(projection)
    stale_projection["order_status"] = "backoff_exhausted"
    upsert_position_current(conn, stale_projection)
    append_many_and_project(conn, events, projection)
    current = conn.execute(
        "SELECT order_status FROM position_current WHERE position_id = ?",
        (held.trade_id,),
    ).fetchone()
    assert current["order_status"] == "filled"

    pending_exit = Position(
        trade_id="pos-pending-exit-real-backoff",
        market_id="condition-test",
        city="Chicago",
        cluster="Chicago",
        target_date="2026-06-24",
        bin_label="88F",
        direction="buy_no",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-test",
        state="pending_exit",
        chain_state="synced",
        shares=12.0,
        chain_shares=12.0,
        cost_basis_usd=8.4,
        chain_cost_basis_usd=8.4,
        strategy_key="center_buy",
        env="live",
        entered_at="2026-06-24T10:00:00+00:00",
        order_status=ExitState.BACKOFF_EXHAUSTED,
        exit_state=ExitState.BACKOFF_EXHAUSTED,
        exit_reason="EXIT_CHAIN_DUST_STILL_HELD",
    )
    assert build_position_current_projection(pending_exit)["order_status"] == "backoff_exhausted"


def test_monitor_refreshed_explicit_time_overrides_stale_position_monitor_time():
    from src.engine.lifecycle_events import build_monitor_refreshed_canonical_write
    from src.state.portfolio import Position

    pos = Position(
        trade_id="pos-monitor-explicit-time",
        market_id="condition-test",
        city="Chicago",
        cluster="Chicago",
        target_date="2026-06-24",
        bin_label="88F",
        direction="buy_no",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-test",
        state="active",
        chain_state="synced",
        shares=12.0,
        chain_shares=12.0,
        cost_basis_usd=8.4,
        chain_cost_basis_usd=8.4,
        strategy_key="center_buy",
        env="live",
        entered_at="2026-06-24T10:00:00+00:00",
        last_monitor_at="2026-06-24T10:05:00+00:00",
    )

    events, projection = build_monitor_refreshed_canonical_write(
        pos,
        sequence_no=9,
        phase_after="active",
        source_module="test",
        occurred_at="2026-07-02T20:10:00+00:00",
    )

    assert events[0]["occurred_at"] == "2026-07-02T20:10:00+00:00"
    assert projection["updated_at"] == "2026-07-02T20:10:00+00:00"


def test_check_pending_retries_persists_day0_redecision_release(conn):
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution.exit_lifecycle import check_pending_retries
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-day0-retry-release",
        market_id="condition-test",
        city="Wellington",
        cluster="Wellington",
        target_date="2026-07-02",
        bin_label="12C",
        direction="buy_yes",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-test",
        state="pending_exit",
        pre_exit_state="day0_window",
        day0_entered_at="2026-07-02T00:48:30+00:00",
        chain_state="synced",
        shares=15.0,
        chain_shares=15.0,
        cost_basis_usd=7.50,
        chain_cost_basis_usd=7.50,
        strategy_key="center_buy",
        env="live",
        entered_at="2026-07-02T00:11:43+00:00",
        order_status="retry_pending",
        exit_state="retry_pending",
        exit_retry_count=1,
        next_exit_retry_at="2026-07-02T02:22:35+00:00",
        last_exit_error="exit_executable_snapshot_unavailable",
        exit_reason="DAY0_HARD_FACT_BIN_DEAD",
    )
    upsert_position_current(conn, build_position_current_projection(position))

    assert check_pending_retries(position, conn=conn) is True

    assert getattr(position.state, "value", position.state) == "day0_window"
    assert getattr(position.exit_state, "value", position.exit_state) == ""
    assert position.exit_retry_count == 0
    assert position.next_exit_retry_at == ""
    assert position.order_status == "filled"

    current = conn.execute(
        """
        SELECT phase, order_status, exit_retry_count, next_exit_retry_at
          FROM position_current
         WHERE position_id = ?
        """,
        (position.trade_id,),
    ).fetchone()
    assert dict(current) == {
        "phase": "day0_window",
        "order_status": "filled",
        "exit_retry_count": 0,
        "next_exit_retry_at": "",
    }
    event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    payload = json.loads(event["payload_json"])
    assert event["event_type"] == "EXIT_RETRY_RELEASED"
    assert event["phase_before"] == "pending_exit"
    assert event["phase_after"] == "day0_window"
    assert event["venue_status"] == "ready"
    assert payload["status"] == "ready"
    assert payload["previous_retry_count"] == 1
    assert payload["release_reason"] == "EXIT_RETRY_COOLDOWN_EXPIRED"


@pytest.mark.parametrize(
    ("reason", "detail", "expected"),
    (
        (
            "venue_rejected_400",
            "PolyApiException[status_code=400, error_message={'error': "
            "'invalid post-only order: order crosses book'}]",
            "global_sell_exit_post_only_cross_reauction:venue_rejected_400",
        ),
        (
            "venue_rejected_400",
            "not enough balance / allowance: sum of active orders",
            "",
        ),
        ("venue_rejected_400", "other validation failure", ""),
    ),
)
def test_global_sell_post_only_cross_rejection_reauctions_without_backoff(
    conn,
    monkeypatch,
    reason,
    detail,
    expected,
):
    from src.execution import exit_lifecycle
    from src.execution.executor import OrderResult
    from src.state.portfolio import Position
    from src.state.venue_command_repo import append_event

    command_id = "cmd-post-only-cross"
    _insert_exit_command(
        conn,
        command_id=command_id,
        position_id="pos-post-only-cross",
        token_id=NO_TOKEN,
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-08-02T07:04:45+00:00",
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REJECTED",
        occurred_at="2026-08-02T07:04:46+00:00",
        payload={"reason": reason, "detail": detail},
    )
    sell_result = OrderResult(
        trade_id="pos-post-only-cross",
        status="rejected",
        reason=reason,
        command_id=command_id,
        command_state="REJECTED",
    )

    classified = exit_lifecycle._global_sell_post_only_cross_reauction_error(
        conn,
        sell_result,
    )
    assert classified == expected
    if not expected:
        return

    now = datetime(2026, 8, 2, 7, 4, 47, tzinfo=timezone.utc)
    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: now)
    position = Position(
        trade_id="pos-post-only-cross",
        market_id="condition-post-only-cross",
        city="Singapore",
        cluster="Singapore",
        target_date="2026-08-02",
        temperature_metric="high",
        bin_label="32C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-post-only-cross",
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        shares=173.31,
        chain_shares=173.31,
        cost_basis_usd=81.50,
        chain_cost_basis_usd=81.50,
        strategy_key="forecast_qkernel_entry",
        env="live",
        entered_at="2026-08-01T07:04:34+00:00",
        order_status="exit_intent",
        exit_state="exit_intent",
        exit_retry_count=4,
        exit_reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
    )
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=position.shares,
        close_position=True,
        occurred_at=now - timedelta(seconds=1),
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
    )

    exit_lifecycle._mark_exit_retry(
        position,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL [SELL_ERROR]",
        error=classified,
        post_only_cross_command_id=command_id,
        conn=conn,
    )

    assert position.exit_state == "retry_pending"
    assert position.order_status == "retry_pending"
    assert position.exit_retry_count == 4
    assert position.next_exit_retry_at == now.isoformat()
    assert exit_lifecycle.has_global_sell_snapshot_reauction_retry(position, conn)
    payload = json.loads(
        conn.execute(
            "SELECT payload_json FROM position_events WHERE position_id = ? "
            "ORDER BY sequence_no DESC LIMIT 1",
            (position.trade_id,),
        ).fetchone()[0]
    )
    assert payload["status"] == "global_sell_snapshot_reauction_pending"
    assert payload["retry_count"] == 4
    assert payload["next_retry_at"] == now.isoformat()
    assert exit_lifecycle.check_pending_retries(
        position,
        conn=conn,
        global_sell_reauction_requester=lambda *_args: pytest.fail(
            "check_pending_retries must not publish the requester"
        ),
    )
    conn.commit()
    assert exit_lifecycle.needs_global_sell_snapshot_reauction(position, conn)
    assert exit_lifecycle._relinquished_global_sell_command_id(conn, position) == command_id
    conn.execute(
        "UPDATE venue_commands SET token_id = ? WHERE command_id = ?",
        (YES_TOKEN, command_id),
    )
    assert exit_lifecycle._relinquished_global_sell_command_id(conn, position) == ""
    assert (
        exit_lifecycle._canonical_global_sell_command_ownership(
            conn, position, require_pending_exit=False
        )
        == "COMMAND_OWNED"
    )
    conn.execute(
        "UPDATE venue_commands SET token_id = ? WHERE command_id = ?",
        (NO_TOKEN, command_id),
    )
    conn.commit()
    _insert_exit_command(
        conn,
        command_id="cmd-post-only-cross-active-before-recovery",
        position_id=position.trade_id,
        token_id=NO_TOKEN,
    )
    conn.commit()
    blocked_requests = []
    assert not exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position,
        conn=conn,
        requester=lambda *_args: blocked_requests.append(True) or True,
    )
    assert blocked_requests == []
    conn.execute(
        "UPDATE venue_commands SET state = 'REJECTED' WHERE command_id = ?",
        ("cmd-post-only-cross-active-before-recovery",),
    )
    conn.commit()
    assert not exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position,
        conn=conn,
        requester=lambda *_args: blocked_requests.append(True) or True,
    )
    assert blocked_requests == []
    conn.execute(
        "DELETE FROM venue_commands WHERE command_id = ?",
        ("cmd-post-only-cross-active-before-recovery",),
    )
    conn.commit()
    assert (
        exit_lifecycle._canonical_global_sell_command_ownership(
            conn, position, require_pending_exit=False
        )
        == "GLOBAL_NO_COMMAND"
    )
    requests = []
    requested_obligations = []

    def request_reauction(released, force_new):
        requests.append((released.trade_id, force_new))
        requested_obligations.append(
            dict(getattr(released, "_held_sell_reauction_obligation", {}))
        )
        return True

    assert exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position,
        conn=conn,
        requester=request_reauction,
    )
    assert requests == [(position.trade_id, True)]
    assert requested_obligations[0]["schema_version"] == 4
    assert requested_obligations[0]["held_token_id"] == NO_TOKEN
    assert requested_obligations[0]["book_state"] == "UNKNOWN"
    assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 1
    assert not exit_lifecycle.needs_global_sell_snapshot_reauction(position, conn)
    assert not exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position,
        conn=conn,
        requester=request_reauction,
    )
    assert requests == [(position.trade_id, True)]
    assert len(requested_obligations) == 1


@pytest.mark.parametrize(
    "failure",
    (
        "no_receipt",
        "unknown_side_effect",
        "review_history",
        "unknown_result_state",
        "active_sell",
    ),
)
def test_global_sell_post_only_cross_requires_complete_command_proof(conn, failure):
    from src.execution import exit_lifecycle
    from src.execution.executor import OrderResult
    from src.state.venue_command_repo import append_event

    command_id = f"cmd-post-only-cross-{failure}"
    position_id = f"pos-post-only-cross-{failure}"
    _insert_exit_command(
        conn,
        command_id=command_id,
        position_id=position_id,
        token_id=YES_TOKEN,
    )
    if failure != "no_receipt":
        append_event(
            conn,
            command_id=command_id,
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-08-02T07:04:45+00:00",
        )
        if failure in {"unknown_side_effect", "review_history"}:
            history_event_type = (
                "SUBMIT_TIMEOUT_UNKNOWN"
                if failure == "unknown_side_effect"
                else "REVIEW_REQUIRED"
            )
            append_event(
                conn,
                command_id=command_id,
                event_type=history_event_type,
                occurred_at="2026-08-02T07:04:46+00:00",
            )
            conn.execute(
                """
                INSERT INTO venue_command_events (
                    event_id, command_id, sequence_no, event_type,
                    occurred_at, payload_json, state_after
                ) VALUES (?, ?, 4, 'SUBMIT_REJECTED', ?, ?, 'REJECTED')
                """,
                (
                    f"event-{command_id}-rejected",
                    command_id,
                    "2026-08-02T07:04:47+00:00",
                    json.dumps(
                        {
                            "reason": "venue_rejected_400",
                            "detail": "invalid post-only order: order crosses book",
                        }
                    ),
                ),
            )
            conn.execute(
                "UPDATE venue_commands SET state = 'REJECTED' WHERE command_id = ?",
                (command_id,),
            )
        else:
            append_event(
                conn,
                command_id=command_id,
                event_type="SUBMIT_REJECTED",
                occurred_at="2026-08-02T07:04:46+00:00",
                payload={
                    "reason": "venue_rejected_400",
                    "detail": "invalid post-only order: order crosses book",
                },
            )
    if failure == "active_sell":
        _insert_exit_command(
            conn,
            command_id="cmd-post-only-cross-active",
            position_id=position_id,
            token_id=YES_TOKEN,
        )

    result_state = "UNKNOWN" if failure == "unknown_result_state" else "REJECTED"
    result = OrderResult(
        trade_id=position_id,
        status="rejected",
        reason="venue_rejected_400",
        command_id=command_id,
        command_state=result_state,
    )
    assert exit_lifecycle._global_sell_post_only_cross_reauction_error(conn, result) == ""


def test_global_sell_post_only_cross_prefix_is_generic_at_retry_boundary(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    now = datetime(2026, 8, 2, 7, 4, 47, tzinfo=timezone.utc)
    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: now)
    position = Position(
        trade_id="pos-post-only-cross-unbound",
        market_id="condition-post-only-cross-unbound",
        city="Singapore",
        cluster="Singapore",
        target_date="2026-08-02",
        temperature_metric="high",
        bin_label="32C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-post-only-cross-unbound",
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        shares=10.0,
        chain_shares=10.0,
        order_status="exit_intent",
        exit_state="exit_intent",
        exit_retry_count=4,
    )

    exit_lifecycle._mark_exit_retry(
        position,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL [SELL_ERROR]",
        error="global_sell_exit_post_only_cross_reauction:venue_rejected_400",
        conn=conn,
    )

    assert position.exit_retry_count == 5
    assert position.next_exit_retry_at != now.isoformat()
    assert not exit_lifecycle.has_global_sell_snapshot_reauction_retry(position, conn)


def test_global_sell_snapshot_failure_releases_to_new_global_auction(
    conn,
    monkeypatch,
):
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-global-sell-snapshot-reauction",
        market_id="condition-test",
        city="San Francisco",
        cluster="San Francisco",
        target_date="2026-07-28",
        temperature_metric="high",
        bin_label="70-71F",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        state="holding",
        chain_state="synced",
        shares=8.3,
        chain_shares=8.3,
        cost_basis_usd=4.98,
        chain_cost_basis_usd=4.98,
        strategy_key="forecast_qkernel_entry",
        env="live",
        entered_at="2026-07-28T10:00:00+00:00",
        order_status="filled",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=position.shares,
        close_position=True,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
    )

    exit_lifecycle._mark_exit_retry(
        position,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL [EXECUTABLE_SNAPSHOT_UNAVAILABLE]",
        error="global_sell_exit_executable_snapshot_unavailable",
        conn=conn,
    )
    conn.commit()

    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"
    assert position.exit_retry_count == 0
    assert datetime.fromisoformat(position.next_exit_retry_at) <= (
        datetime.now(timezone.utc) + timedelta(seconds=1)
    )

    assert exit_lifecycle.check_pending_retries(position, conn=conn) is False
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"

    requested = []
    requested_obligations = []

    with monkeypatch.context() as scoped:
        scoped.setattr(
            exit_lifecycle,
            "_dual_write_exit_retry_released_if_available",
            lambda *_args, **_kwargs: False,
        )
        assert exit_lifecycle.check_pending_retries(
            position,
            conn=conn,
            global_sell_reauction_requester=lambda released, force_new: (
                requested.append(released.trade_id) or True
            ),
        ) is False
    assert requested == []
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"

    def request_reauction(released, force_new_generation):
        latest_event = conn.execute(
            """
            SELECT event_type, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (released.trade_id,),
        ).fetchone()
        projection = conn.execute(
            """
            SELECT phase
              FROM position_current
             WHERE position_id = ?
            """,
            (released.trade_id,),
        ).fetchone()
        assert conn.in_transaction is False
        assert force_new_generation is True
        requested_obligations.append(
            dict(getattr(released, "_held_sell_reauction_obligation", {}))
        )
        assert latest_event["event_type"] == "EXIT_RETRY_RELEASED"
        assert projection["phase"] == "active"
        requested.append(released.trade_id)
        return True

    assert exit_lifecycle.check_pending_retries(
        position,
        conn=conn,
        global_sell_reauction_requester=request_reauction,
    ) is True

    assert requested == []
    assert position.last_exit_error == (
        "global_sell_exit_executable_snapshot_unavailable"
    )
    if conn.in_transaction:
        conn.commit()
    assert exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position,
        conn=conn,
        requester=request_reauction,
    ) is True
    assert requested == [position.trade_id]
    released_event = conn.execute(
        """
        SELECT event_type, payload_json
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_RETRY_RELEASED'
               AND COALESCE(
                   json_extract(
                       payload_json,
                       '$.global_sell_reauction_status'
                   ),
                   ''
               ) != 'durable_wake_reserved'
               AND json_extract(payload_json, '$.error') IS NOT NULL
             ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert released_event["event_type"] == "EXIT_RETRY_RELEASED"
    release_payload = json.loads(released_event["payload_json"])
    assert (
        release_payload["release_reason"]
        == "GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED"
    )
    assert release_payload["error"] == (
        "global_sell_exit_executable_snapshot_unavailable"
    )
    obligation = release_payload["held_sell_reauction_obligation"]
    assert obligation["schema_version"] == 4
    assert obligation["book_state"] == "UNKNOWN"
    assert obligation["held_token_id"] == NO_TOKEN
    assert requested_obligations == [obligation]
    assert position.state == "holding"
    assert position.order_status == "filled"
    assert position.last_exit_error == ""
    reserved_event = conn.execute(
        """
        SELECT event_type, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert reserved_event["event_type"] == "EXIT_RETRY_RELEASED"
    assert json.loads(reserved_event["payload_json"])[
        "global_sell_reauction_status"
    ] == "durable_wake_reserved"
    assert (
        exit_lifecycle.needs_global_sell_snapshot_reauction(position, conn)
        is False
    )

    failed_wake = replace(
        position,
        trade_id="pos-global-sell-snapshot-wake-failed",
        market_id="condition-wake-failed",
        condition_id="condition-wake-failed",
        token_id="yes-token-wake-failed",
        no_token_id="no-token-wake-failed",
        last_exit_error="",
    )
    upsert_position_current(
        conn,
        build_position_current_projection(failed_wake),
    )
    _seed_exit_intent_event(
        conn,
        position_id=failed_wake.trade_id,
        shares=failed_wake.shares,
        close_position=True,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
    )
    exit_lifecycle._mark_exit_retry(
        failed_wake,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL [EXECUTABLE_SNAPSHOT_UNAVAILABLE]",
        error="global_sell_exit_executable_snapshot_unavailable",
        conn=conn,
    )
    conn.commit()

    assert exit_lifecycle.check_pending_retries(
        failed_wake,
        conn=conn,
        global_sell_reauction_requester=lambda _released, _force_new: False,
    ) is True
    conn.commit()
    assert failed_wake.state == "holding"
    assert failed_wake.last_exit_error == (
        "global_sell_exit_executable_snapshot_unavailable"
    )
    assert (
        exit_lifecycle.needs_global_sell_snapshot_reauction(
            failed_wake,
            conn,
        )
        is True
    )
    failed_projection = conn.execute(
        """
        SELECT phase
          FROM position_current
         WHERE position_id = ?
        """,
        (failed_wake.trade_id,),
    ).fetchone()
    assert failed_projection["phase"] == "active"
    failed_wake.last_exit_error = ""
    assert (
        exit_lifecycle.needs_global_sell_snapshot_reauction(
            failed_wake,
            conn,
        )
        is True
    )
    recovery_requests = []

    def recover_reauction(released, force_new_generation):
        recovery_requests.append(
            (released.trade_id, force_new_generation)
        )
        return True

    assert exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        failed_wake,
        conn=conn,
        requester=recover_reauction,
    ) is True
    assert recovery_requests == [(failed_wake.trade_id, True)]
    assert (
        exit_lifecycle.needs_global_sell_snapshot_reauction(
            failed_wake,
            conn,
        )
        is False
    )

    runtime_only = replace(
        failed_wake,
        trade_id="pos-global-sell-runtime-only-debt",
        market_id="condition-runtime-only",
        condition_id="condition-runtime-only",
        token_id="yes-token-runtime-only",
        no_token_id="no-token-runtime-only",
        state="holding",
        exit_state="",
        order_status="filled",
        last_exit_error=(
            "global_sell_exit_executable_snapshot_unavailable"
        ),
    )
    upsert_position_current(
        conn,
        {
            **build_position_current_projection(runtime_only),
            "phase": "pending_exit",
        },
    )
    conn.commit()
    runtime_only_requests = []
    assert (
        exit_lifecycle.needs_global_sell_snapshot_reauction(
            runtime_only,
            conn,
        )
        is False
    )
    assert exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        runtime_only,
        conn=conn,
        requester=lambda *_args: runtime_only_requests.append(True) or True,
    ) is False
    assert runtime_only_requests == []
    assert conn.execute(
        """
        SELECT phase
          FROM position_current
         WHERE position_id = ?
        """,
        (runtime_only.trade_id,),
    ).fetchone()["phase"] == "pending_exit"


def test_restart_republishes_unbound_v4_residual_with_same_generation_until_terminal(
    conn,
    monkeypatch,
    tmp_path,
):
    """A committed UNKNOWN residual survives restart and waits for fresh q/book."""
    from types import SimpleNamespace

    from src.engine.lifecycle_events import build_position_current_projection
    from src.events import reactor
    from src.execution import exit_lifecycle
    from src.runtime import reactor_wake
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="karachi-residual-restart",
        market_id="condition-karachi-residual",
        condition_id="condition-karachi-residual",
        city="Karachi",
        cluster="Karachi",
        target_date="2026-07-29",
        temperature_metric="high",
        bin_label="35C",
        direction="buy_yes",
        token_id="token-karachi-residual",
        no_token_id="token-karachi-residual-no",
        state="holding",
        chain_state="synced",
        shares=4.0,
        chain_shares=4.0,
        order_status="filled",
        strategy_key="forecast_qkernel_entry",
        env="test",
        entered_at="2026-07-29T10:00:00+00:00",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=position.shares,
        close_position=True,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
    )
    exit_lifecycle._mark_exit_retry(
        position,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL [EXECUTABLE_SNAPSHOT_UNAVAILABLE]",
        error="global_sell_exit_executable_snapshot_unavailable",
        conn=conn,
    )
    conn.commit()
    assert exit_lifecycle.check_pending_retries(
        position,
        conn=conn,
        global_sell_reauction_requester=lambda _position, _force: False,
    ) is True
    conn.commit()

    stored = conn.execute(
        """
        SELECT payload_json FROM position_events
         WHERE position_id = ? AND event_type = 'EXIT_RETRY_RELEASED'
         ORDER BY sequence_no DESC LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    obligation = json.loads(stored["payload_json"])["held_sell_reauction_obligation"]
    assert obligation["book_state"] == "UNKNOWN"
    assert obligation["probability_content_identity"] == ""
    assert conn.in_transaction is False

    # Fresh process/runtime object: only canonical event state supplies the debt.
    restarted = replace(position, last_exit_error="")
    restarted._day0_monitor_probability_receipt = {
        "probability_content_identity": "q-karachi-restarted-current"
    }
    wake_path = tmp_path / "wake.json"
    published = []
    real_publish = reactor_wake.publish_reactor_wake

    def publish(**kwargs):
        assert kwargs["path"] == wake_path
        wake = real_publish(**kwargs)
        published.append(wake)
        return wake

    monkeypatch.setattr(reactor_wake, "publish_reactor_wake", publish)
    reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()
    try:
        def publish_after_restart(released, force_new_generation):
            restored = getattr(released, "_held_sell_reauction_obligation")
            return reactor.request_global_auction_completion(
                reason="GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED",
                position_id=released.trade_id,
                family=(released.city, released.target_date, released.temperature_metric),
                probability_content_identity=(
                    restored["probability_content_identity"]
                    or released._day0_monitor_probability_receipt[
                        "probability_content_identity"
                    ]
                ),
                held_token_id=restored["held_token_id"],
                held_best_bid=restored["held_best_bid"],
                bid_observed_at=restored["bid_observed_at"],
                book_state=restored["book_state"],
                generation=restored["generation"],
                scope_identity=restored["scope_identity"],
                wake_path=wake_path,
                force_new_generation=force_new_generation,
            )

        assert exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
            restarted,
            conn=conn,
            requester=publish_after_restart,
        ) is True
        assert len(published) == 1
        request = published[0].held_sell_reauction_requests[0]
        assert request.generation == obligation["generation"]
        assert request.scope_identity == obligation["scope_identity"]
        assert request.book_state == "UNKNOWN"
        assert not reactor_wake.held_sell_reauction_requests_completed(
            (request,), path=wake_path
        )

        coverage = SimpleNamespace(
            position_id=request.position_id,
            token_id=request.held_token_id,
            status="EVALUATED",
            probability_content_identity="q-karachi-restarted-current",
            selection_epoch_identity="epoch-karachi-restarted",
            sell_book_witness_identity="book-karachi-restarted",
        )
        cut = reactor.GlobalHeldSellCompletionCut(
            holding_coverage=(coverage,),
            economic_cut_completed=True,
            outcome="CAPITAL_REJECTED",
            terminal_no_trade_reason="GLOBAL_AUCTION_NO_TRADE:CASH_DOMINATES",
        )
        receipts = reactor._held_sell_reauction_receipts_from_global_cut(
            requests=(request,),
            result=reactor.ReactorResult(
                global_auction_completed_non_cancelled=1,
                global_held_sell_completion_cuts=[cut],
            ),
        )
        assert receipts[0].answered_probability_content_identity == "q-karachi-restarted-current"
        assert reactor_wake.persist_held_sell_reauction_receipts(
            receipts, path=wake_path
        )
        assert reactor_wake.held_sell_reauction_requests_completed(
            (request,), path=wake_path
        )
    finally:
        reactor._GLOBAL_AUCTION_MONITOR_COMPLETION_DUE.clear()


@pytest.mark.parametrize(
    ("pre_exit_state", "day0_entered_at"),
    (("holding", ""), ("day0_window", "2026-07-28T00:00:00+00:00")),
)
def test_global_fak_zero_fill_reauctions_immediately_with_durable_proof(
    conn,
    monkeypatch,
    tmp_path,
    pre_exit_state,
    day0_entered_at,
):
    from src.events import reactor
    from src.execution import exit_lifecycle
    from src.execution.executor import OrderResult
    from src.runtime import reactor_wake
    from src.state.portfolio import Position
    from src.state.venue_command_repo import append_event

    command_id = f"cmd-global-fak-zero-{pre_exit_state}"
    position_id = f"pos-global-fak-zero-{pre_exit_state}"
    token_id = f"yes-token-{pre_exit_state}"
    _insert_exit_command(
        conn,
        command_id=command_id,
        position_id=position_id,
        token_id=token_id,
        price=0.95,
        order_type="FAK",
        post_only=False,
    )
    venue_order_id = f"0x{'a' * 64}"
    conn.execute(
        "UPDATE venue_commands SET venue_order_id = ? WHERE command_id = ?",
        (venue_order_id, command_id),
    )
    final_envelope_id = _ensure_envelope(
        conn,
        token_id=token_id,
        envelope_id=f"final-{command_id}",
        price=0.95,
        order_type="FAK",
        post_only=False,
        order_id=venue_order_id,
        signed_order_hash="b" * 64,
        error_code="venue_fak_no_match_400",
        error_message="no orders found to match with FAK order",
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-08-02T07:04:45+00:00",
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REJECTED",
        occurred_at="2026-08-02T07:04:46+00:00",
        payload={
            "reason": "venue_fak_no_match_400",
            "detail": "no orders found to match with FAK order",
            "proof_class": "deterministic_venue_fak_no_match_400",
            "terminal_no_fill": True,
            "exposure_created": False,
            "venue_order_id": venue_order_id,
            "required_predicates": {
                "structured_v2_fak_no_match": True,
                "final_envelope_command_matches": True,
                "final_envelope_is_fak": True,
                "deterministic_order_id_matches": True,
            },
            "final_submission_envelope_id": final_envelope_id,
            "final_submission_envelope_command_id": command_id,
        },
    )

    position = Position(
        trade_id=position_id,
        market_id=f"condition-{pre_exit_state}",
        city="Paris",
        cluster="Paris",
        target_date="2026-07-28",
        temperature_metric="high",
        bin_label="28C",
        direction="buy_yes",
        token_id=token_id,
        no_token_id=f"no-token-{pre_exit_state}",
        condition_id=f"condition-{pre_exit_state}",
        state="pending_exit",
        pre_exit_state=pre_exit_state,
        day0_entered_at=day0_entered_at,
        chain_state="synced",
        shares=10.0,
        chain_shares=10.0,
        cost_basis_usd=6.0,
        chain_cost_basis_usd=6.0,
        strategy_key="forecast_qkernel_entry",
        env="live",
        entered_at="2026-07-27T10:00:00+00:00",
        order_status="exit_intent",
        exit_state="exit_intent",
        exit_retry_count=3,
    )

    classified = exit_lifecycle._global_sell_fak_no_fill_reauction_error(
        conn,
        OrderResult(
            trade_id=position_id,
            status="rejected",
            reason="venue_fak_no_match_400",
            command_id=command_id,
            command_state="REJECTED",
        ),
    )
    assert classified == (
        "global_sell_exit_fak_no_fill_reauction:venue_fak_no_match_400"
    )
    now = datetime(2026, 8, 2, 7, 4, 47, tzinfo=timezone.utc)
    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: now)
    _seed_exit_intent_event(
        conn,
        position_id=position.trade_id,
        shares=position.shares,
        close_position=True,
        occurred_at=now - timedelta(seconds=1),
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
    )
    exit_lifecycle._mark_exit_retry(
        position,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL [SELL_ERROR]",
        error=classified,
        fak_no_fill_command_id=command_id,
        conn=conn,
    )

    assert position.exit_state == "retry_pending"
    assert position.exit_retry_count == 3
    assert position.last_exit_error == classified
    assert position.next_exit_retry_at == now.isoformat()
    assert exit_lifecycle.has_global_sell_snapshot_reauction_retry(position, conn)
    assert exit_lifecycle._relinquished_global_sell_command_id(conn, position) == command_id

    wake_path = tmp_path / f"{position_id}-wake.json"
    requests = []

    def publish_current_reauction(released, force_new_generation):
        obligation = exit_lifecycle.latest_held_sell_reauction_obligation(
            conn,
            released,
        )
        assert obligation is not None
        result = reactor.request_global_auction_completion(
            reason="GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED",
            position_id=released.trade_id,
            family=(released.city, released.target_date, released.temperature_metric),
            probability_content_identity="q-after-fak-no-fill",
            held_token_id=token_id,
            held_best_bid=0.06,
            bid_observed_at=(now + timedelta(seconds=1)).isoformat(),
            probability_observed_at=(now + timedelta(seconds=1)).isoformat(),
            completion_deadline_at=(now - timedelta(seconds=1)).isoformat(),
            book_state="EXECUTABLE",
            generation=str(obligation["generation"]),
            scope_identity=str(obligation["scope_identity"]),
            wake_path=wake_path,
            force_new_generation=force_new_generation,
            return_request=True,
        )
        accepted, request = result
        if request is not None:
            requests.append(request)
        return bool(accepted)

    # The rejected FAK's canonical retry and outbox are still uncommitted here.
    # The production seam must commit them and publish in this same turn;
    # waiting for a later pending-retry scan recreates the Seoul 10m36s gap.
    assert exit_lifecycle._drain_same_turn_global_sell_reauction_after_no_fill(
        position,
        conn=conn,
        requester=publish_current_reauction,
        deadline_monotonic=exit_lifecycle._time_module.monotonic() + 5.0,
    ) is True
    assert len(requests) == 1
    request = requests[0]
    assert request.probability_content_identity == "q-after-fak-no-fill"
    assert request.book_state == "EXECUTABLE"
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,),
        path=wake_path,
    )

    day0 = reactor_wake.publish_reactor_wake(
        source="day0",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id=f"day0-after-{position_id}",
    )
    selected = reactor_wake.read_reactor_wake(path=wake_path)
    assert selected is not None
    assert selected != day0
    assert selected.held_sell_reauction_requests == (request,)


def test_same_turn_reauction_release_commit_failure_restores_runtime(monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    position = Position(
        trade_id="same-turn-release-commit-failure",
        market_id="condition-release-failure",
        city="Paris",
        cluster="Paris",
        target_date="2026-08-13",
        temperature_metric="high",
        bin_label="28C",
        direction="buy_yes",
        token_id="same-turn-release-token",
        no_token_id="same-turn-release-no-token",
        condition_id="condition-release-failure",
        state="pending_exit",
        pre_exit_state="holding",
        chain_state="synced",
        shares=10.0,
        chain_shares=10.0,
        exit_state="retry_pending",
        order_status="retry_pending",
        last_exit_error="global_sell_exit_fak_no_fill_reauction:venue_fak_no_match_400",
        next_exit_retry_at="2026-08-13T00:00:00+00:00",
    )
    before = copy.deepcopy(position.__dict__)

    class Conn:
        in_transaction = True
        commits = 0

        def commit(self):
            self.commits += 1
            if self.commits == 2:
                raise sqlite3.OperationalError("release commit failed")
            self.in_transaction = False

        def rollback(self):
            self.in_transaction = False

    conn = Conn()
    monkeypatch.setattr(
        exit_lifecycle,
        "has_proven_sync_no_side_effect_sell_reauction",
        lambda *_args, **_kwargs: True,
    )

    def release(*_args, **_kwargs):
        position.state = "holding"
        position.exit_state = ""
        position.order_status = "filled"
        position.next_exit_retry_at = ""
        conn.in_transaction = True
        return True

    monkeypatch.setattr(exit_lifecycle, "check_pending_retries", release)
    monkeypatch.setattr(
        exit_lifecycle,
        "recover_global_sell_snapshot_reauction_debt",
        lambda *_args, **_kwargs: pytest.fail("uncommitted release must not publish"),
    )

    assert not exit_lifecycle._drain_same_turn_global_sell_reauction_after_no_fill(
        position,
        conn=conn,
        requester=lambda *_args: True,
    )
    assert position.__dict__ == before


def test_same_turn_reauction_rejects_ordinary_snapshot_debt(monkeypatch):
    from src.execution import exit_lifecycle
    from types import SimpleNamespace

    position = SimpleNamespace(
        trade_id="ordinary-snapshot-debt",
        last_exit_error="global_sell_exit_executable_snapshot_unavailable",
    )
    published = []
    monkeypatch.setattr(
        exit_lifecycle,
        "check_pending_retries",
        lambda *_args, **_kwargs: pytest.fail("ordinary debt must not fast-release"),
    )

    assert not exit_lifecycle._drain_same_turn_global_sell_reauction_after_no_fill(
        position,
        conn=SimpleNamespace(),
        requester=lambda *_args: published.append(True) or True,
    )
    assert published == []


def test_reauction_deadline_expires_before_external_publish(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from src.execution import executor, exit_lifecycle

    position = SimpleNamespace(trade_id="deadline-before-publish")

    class Conn:
        in_transaction = False

        def commit(self):
            pass

        def rollback(self):
            pass

    conn = Conn()
    monotonic = iter((0.0, 0.0, 0.0, 0.0, 0.0, 6.0))
    monkeypatch.setattr(
        exit_lifecycle._time_module,
        "monotonic",
        lambda: next(monotonic),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "needs_global_sell_snapshot_reauction",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "latest_held_sell_reauction_obligation",
        lambda *_args, **_kwargs: {
            "schema_version": 4,
            "generation": "deadline-generation",
        },
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_pending_exit_no_order_waits_for_liquidity",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_canonical_global_sell_command_ownership",
        lambda *_args, **_kwargs: "GLOBAL_NO_COMMAND",
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_record_global_sell_reauction_publish_claim",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        executor,
        "_canonical_trade_write_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    published = []

    assert not exit_lifecycle.recover_global_sell_snapshot_reauction_debt(
        position,
        conn=conn,
        requester=lambda *_args: published.append(True) or True,
        deadline_monotonic=5.0,
    )
    assert published == []

def test_persisted_exit_envelope_rejects_non_maker_non_fak_mode(conn):
    from src.state.venue_command_repo import insert_command

    token_id = "yes-token-invalid-mode"
    with pytest.raises(
        ValueError,
        match="persisted taker-capable order is not a legal live execution mode",
    ):
        insert_command(
            conn,
            command_id="cmd-invalid-mode",
            snapshot_id=_ensure_snapshot(conn, token_id=token_id),
            envelope_id=_ensure_envelope(
                conn,
                token_id=token_id,
                order_type="FOK",
                post_only=False,
            ),
            position_id="pos-invalid-mode",
            decision_id="dec-invalid-mode",
            idempotency_key="idem-invalid-mode",
            intent_kind="EXIT",
            market_id=token_id,
            token_id=token_id,
            side="SELL",
            size=10.0,
            price=0.49,
            created_at=_NOW.isoformat(),
        )

@pytest.mark.parametrize("position_env", ("live", "unknown_env"))
def test_live_global_sell_rejects_fak_for_maker_authority_before_snapshot_or_venue(
    conn,
    monkeypatch,
    position_env,
):
    from types import SimpleNamespace

    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-global-fak-rejected",
        market_id="condition-global-fak-rejected",
        condition_id="condition-global-fak-rejected",
        city="Beijing",
        cluster="Beijing",
        target_date="2026-07-31",
        temperature_metric="high",
        bin_label="32C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        state="holding",
        chain_state="synced",
        shares=10.0,
        chain_shares=10.0,
        cost_basis_usd=6.0,
        strategy_key="forecast_qkernel_entry",
        env=position_env,
        entered_at="2026-07-31T10:00:00+00:00",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    exit_context = ExitContext(
        exit_reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        current_market_price=0.50,
        current_market_price_is_fresh=True,
        best_bid=0.50,
    )
    exit_intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=YES_TOKEN,
        shares=10.0,
        current_market_price=0.50,
        best_bid=0.50,
        exact_limit_price=0.50,
        submit_order_type="FAK",
    )
    authority = object.__new__(exit_lifecycle.GlobalSellExecutionAuthority)
    object.__setattr__(authority, "actuation", object())
    object.__setattr__(
        authority,
        "jit_candidate",
        SimpleNamespace(
            book_captured_at_utc=datetime.now(timezone.utc),
            execution_mode="MAKER_REST",
            executable_sell_curve=SimpleNamespace(
                book_hash="book-global-fak-rejected",
                quote_ttl=timedelta(seconds=30),
            ),
        ),
    )
    object.__setattr__(authority, "authority_identity", "test-authority")
    monkeypatch.setattr(
        exit_lifecycle.GlobalSellExecutionAuthority,
        "__post_init__",
        lambda _self: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("global FAK must be rejected before snapshot capture")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("global FAK must be rejected before venue submit")
        ),
    )

    result = exit_lifecycle._execute_live_exit(
        PortfolioState(positions=[position]),
        position,
        exit_context,
        exit_intent,
        object(),
        conn=conn,
        execution_evidence=exit_lifecycle.ExitExecutionEvidence(),
        is_red_force_exit=False,
        global_sell_authority=authority,
        hard_fact_authority=None,
    )

    assert result == "exit_blocked: global_sell_order_type_mismatch"
    assert position.state == "holding"
    assert position.exit_retry_count == 0

    position.last_exit_order_id = "stale-prior-exit"
    result = exit_lifecycle._execute_live_exit(
        PortfolioState(positions=[position]),
        position,
        exit_context,
        exit_intent,
        object(),
        conn=conn,
        execution_evidence=exit_lifecycle.ExitExecutionEvidence(),
        is_red_force_exit=False,
        global_sell_authority=object(),
        hard_fact_authority=None,
    )

    assert result == "exit_blocked: global_sell_execution_authority_invalid"
    assert position.exit_retry_count == 0


@pytest.mark.parametrize(
    ("held_shares", "planned_shares", "min_order_size", "close_position", "expected"),
    (
        ("20.999023", "9.33", "5", False, ""),
        (
            "15",
            "10.17",
            "5",
            False,
            "global_sell_partial_residual_below_snapshot_min_order_size",
        ),
        ("15", "10.17", "4", False, ""),
        (
            "15",
            "10.17",
            None,
            False,
            "global_sell_partial_residual_snapshot_min_order_size_unavailable",
        ),
        ("15", "15.0000000005", "5", True, ""),
    ),
)
def test_global_sell_partial_residual_uses_fresh_snapshot_minimum(
    held_shares,
    planned_shares,
    min_order_size,
    close_position,
    expected,
):
    from types import SimpleNamespace

    from src.execution import exit_lifecycle

    authority = SimpleNamespace(
        jit_candidate=SimpleNamespace(held_shares=Decimal(held_shares))
    )
    intent = exit_lifecycle.ExitIntent(
        trade_id="global-sell-residual",
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=YES_TOKEN,
        shares=float(planned_shares),
        current_market_price=0.50,
        best_bid=0.50,
        close_position=close_position,
    )

    error = exit_lifecycle._global_sell_partial_residual_min_order_error(
        intent,
        authority,
        {"executable_snapshot_min_order_size": min_order_size},
    )

    if expected:
        assert error.startswith(expected)
    else:
        assert error == ""


@pytest.mark.parametrize(
    ("authority_error", "planned_shares", "close_position", "expected_error"),
    (
        (
            None,
            10.17,
            False,
            "global_sell_partial_residual_below_snapshot_min_order_size",
        ),
        (
            "global_sell_capital_certificate_expired",
            15.0,
            True,
            "global_sell_capital_certificate_expired",
        ),
    ),
)
def test_live_post_intent_gate_rejection_is_retryable_without_venue_command(
    conn,
    monkeypatch,
    authority_error,
    planned_shares,
    close_position,
    expected_error,
):
    from types import SimpleNamespace

    from src.execution import exit_lifecycle
    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.portfolio import ExitContext, PortfolioState, Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="chongqing-global-partial-residual",
        market_id="condition-global-partial-residual",
        condition_id="condition-global-partial-residual",
        city="Chongqing",
        cluster="China",
        target_date="2026-08-10",
        temperature_metric="high",
        bin_label="35C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        state="holding",
        chain_state="synced",
        shares=15.0,
        chain_shares=15.0,
        cost_basis_usd=9.0,
        strategy_key="forecast_qkernel_entry",
        decision_law_id="predicted_bin_ev_v1",
        env="live",
        entered_at="2026-08-10T00:00:00+00:00",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=YES_TOKEN,
        shares=planned_shares,
        current_market_price=0.50,
        best_bid=0.50,
        exact_limit_price=0.50,
        submit_order_type="GTC",
        close_position=close_position,
    )
    authority = SimpleNamespace(
        jit_candidate=SimpleNamespace(
            execution_mode="MAKER_REST",
            held_shares=Decimal("15"),
            book_captured_at_utc=_NOW,
            executable_sell_curve=SimpleNamespace(book_hash="fresh-chongqing-book"),
        )
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_global_sell_execution_authority_shape_error",
        lambda _authority: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_global_sell_receipt_closure_error",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *_args, **_kwargs: {
            "executable_snapshot_id": "fresh-chongqing-snapshot",
            "executable_snapshot_min_order_size": "5",
        },
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_global_sell_capital_certificate_error",
        lambda *_args, **_kwargs: authority_error,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("dust residual must not reach the venue")
        ),
    )

    result = exit_lifecycle._execute_live_exit(
        PortfolioState(positions=[position]),
        position,
        ExitContext(
            exit_reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
            current_market_price=0.50,
            current_market_price_is_fresh=True,
            best_bid=0.50,
        ),
        intent,
        object(),
        conn=conn,
        execution_evidence=exit_lifecycle.ExitExecutionEvidence(),
        is_red_force_exit=False,
        global_sell_authority=authority,
        hard_fact_authority=None,
    )

    assert result.startswith(f"exit_blocked: {expected_error}")
    assert position.exit_state == "retry_pending"
    assert position.order_status == "retry_pending"
    assert position.exit_retry_count == 0
    assert position.next_exit_retry_at
    event_rows = conn.execute(
        "SELECT event_type, payload_json FROM position_events "
        "WHERE position_id = ? ORDER BY sequence_no",
        (position.trade_id,),
    ).fetchall()
    assert [row["event_type"] for row in event_rows][-2:] == [
        "EXIT_INTENT",
        "EXIT_ORDER_REJECTED",
    ]
    assert expected_error in event_rows[-1]["payload_json"]
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0] == 0


def test_live_global_maker_rest_reaches_submit_when_bid_is_below_floor(
    conn,
    monkeypatch,
):
    from types import SimpleNamespace

    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution import exit_lifecycle
    from src.execution.executor import OrderResult
    from src.state.portfolio import ExitContext, PortfolioState, Position
    from src.state.projection import upsert_position_current

    position = Position(
        trade_id="pos-global-maker-sub-floor-bid",
        market_id="condition-global-maker-sub-floor-bid",
        condition_id="condition-global-maker-sub-floor-bid",
        city="Tel Aviv",
        cluster="Asia",
        target_date="2026-08-09",
        temperature_metric="high",
        bin_label="33C",
        direction="buy_no",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        state="holding",
        chain_state="synced",
        shares=41.7,
        chain_shares=41.7,
        cost_basis_usd=12.51,
        strategy_key="forecast_qkernel_entry",
        env="live",
        entered_at="2026-08-09T00:00:00+00:00",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    exit_context = ExitContext(
        exit_reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        current_market_price=0.05,
        current_market_price_is_fresh=True,
        best_bid=0.04,
    )
    exit_intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=NO_TOKEN,
        shares=41.7,
        current_market_price=0.05,
        best_bid=0.04,
        exact_limit_price=0.05,
        submit_order_type="GTC",
        capital_certificate={"execution_mode": "MAKER_REST"},
    )
    authority = object.__new__(exit_lifecycle.GlobalSellExecutionAuthority)
    object.__setattr__(authority, "actuation", object())
    object.__setattr__(
        authority,
        "jit_candidate",
        SimpleNamespace(
            book_captured_at_utc=datetime.now(timezone.utc),
            execution_mode="MAKER_REST",
            executable_sell_curve=SimpleNamespace(
                book_hash="book-global-maker-sub-floor-bid",
                quote_ttl=timedelta(seconds=30),
            ),
        ),
    )
    object.__setattr__(authority, "authority_identity", "test-authority")
    monkeypatch.setattr(
        exit_lifecycle,
        "_global_sell_execution_authority_shape_error",
        lambda _authority: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_global_sell_receipt_closure_error",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_global_sell_capital_certificate_error",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
        lambda *_args, **_kwargs: {
            "executable_snapshot_id": "snap-global-maker-sub-floor-bid",
            "executable_snapshot_min_order_size": "5",
            "executable_snapshot_orderbook_top_bid": "0.04",
            "executable_snapshot_orderbook_top_ask": "0.05",
        },
    )
    submitted = []

    def place_sell_order(**kwargs):
        submitted.append(kwargs)
        return OrderResult(
            trade_id=position.trade_id,
            status="pending",
            order_id="ord-global-maker-sub-floor-bid",
        )

    monkeypatch.setattr(exit_lifecycle, "place_sell_order", place_sell_order)

    result = exit_lifecycle._execute_live_exit(
        PortfolioState(positions=[position]),
        position,
        exit_context,
        exit_intent,
        None,
        conn=conn,
        execution_evidence=exit_lifecycle.ExitExecutionEvidence(),
        is_red_force_exit=False,
        global_sell_authority=authority,
        hard_fact_authority=None,
    )

    assert result == "sell_placed: order=ord-global-maker-sub-floor-bid"
    assert len(submitted) == 1
    assert submitted[0]["best_bid"] == 0.04
    assert submitted[0]["exact_limit_price"] == 0.05
    assert submitted[0]["submit_order_type"] == "GTC"
    assert position.exit_state == "sell_pending"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? "
        "AND event_type = 'EXIT_ORDER_POSTED'",
        (position.trade_id,),
    ).fetchone()[0] == 1


def test_no_bid_retry_releases_on_favorable_above_submit_band_bid(conn):
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution.exit_lifecycle import check_pending_retries
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    now = datetime.now(timezone.utc)
    position = Position(
        trade_id="pos-no-bid-liquidity-wait",
        market_id="condition-test",
        city="London",
        cluster="London",
        target_date="2026-07-02",
        bin_label="14C",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        state="pending_exit",
        pre_exit_state="day0_window",
        day0_entered_at="2026-07-02T00:48:30+00:00",
        chain_state="synced",
        shares=5.0,
        chain_shares=5.0,
        cost_basis_usd=2.50,
        chain_cost_basis_usd=2.50,
        strategy_key="forecast_qkernel_entry",
        env="live",
        entered_at="2026-07-02T00:11:43+00:00",
        order_status="retry_pending",
        exit_state="retry_pending",
        exit_retry_count=3,
        next_exit_retry_at="2000-01-01T00:00:00+00:00",
        last_exit_error="exit_no_executable_bid",
        exit_reason="DAY0_HARD_FACT_BIN_DEAD",
    )
    upsert_position_current(conn, build_position_current_projection(position))
    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-no-bid-liquidity-wait",
        captured_at=now,
        freshness_deadline=now + timedelta(minutes=5),
        orderbook_top_bid=None,
        orderbook_top_ask=Decimal("0.001"),
    )

    assert check_pending_retries(position, conn=conn) is False
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"
    assert position.exit_retry_count == 3
    assert position.next_exit_retry_at == "2000-01-01T00:00:00+00:00"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ?",
        (position.trade_id,),
    ).fetchone()[0] == 0

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-positive-bid-liquidity-wake",
        captured_at=now + timedelta(seconds=1),
        freshness_deadline=now + timedelta(minutes=5),
        orderbook_top_bid=Decimal("0.001"),
        orderbook_top_ask=Decimal("0.002"),
    )

    assert check_pending_retries(position, conn=conn) is False
    assert position.state == "pending_exit"
    assert position.exit_state == "retry_pending"

    _ensure_snapshot(
        conn,
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=YES_TOKEN,
        outcome_label="YES",
        snapshot_id="snap-favorable-bid-liquidity-wake",
        captured_at=now + timedelta(seconds=2),
        freshness_deadline=now + timedelta(minutes=5),
        orderbook_top_bid=Decimal("0.999"),
        orderbook_top_ask=Decimal("1.0"),
    )

    assert check_pending_retries(position, conn=conn) is True
    assert position.state == "day0_window"
    assert position.exit_state == ""
    assert position.exit_retry_count == 0
    assert position.next_exit_retry_at == ""
    event = conn.execute(
        """
        SELECT event_type, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position.trade_id,),
    ).fetchone()
    assert event["event_type"] == "EXIT_RETRY_RELEASED"
    assert json.loads(event["payload_json"])["error"] == "exit_no_executable_bid"


def test_monitor_refreshed_projection_updated_at_tracks_event_time(monkeypatch):
    from src.engine import lifecycle_events
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-monitor-clock",
        market_id="condition-test",
        city="Chicago",
        cluster="Chicago",
        target_date="2026-06-24",
        bin_label="88F",
        direction="buy_no",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-test",
        state="holding",
        chain_state="synced",
        shares=12.0,
        chain_shares=12.0,
        cost_basis_usd=8.4,
        chain_cost_basis_usd=8.4,
        strategy_key="center_buy",
        env="live",
        entered_at="2026-06-24T10:00:00+00:00",
        order_status="filled",
    )
    position.last_monitor_at = "2026-06-24T12:00:00+00:00"

    real_project = lifecycle_events.build_position_current_projection

    def stale_project(pos):
        projection = real_project(pos)
        projection["updated_at"] = "2026-06-24T10:00:00+00:00"
        return projection

    monkeypatch.setattr(lifecycle_events, "build_position_current_projection", stale_project)

    events, projection = lifecycle_events.build_monitor_refreshed_canonical_write(
        position,
        sequence_no=7,
        phase_after="active",
        source_module="test",
    )

    assert events[0]["occurred_at"] == "2026-06-24T12:00:00+00:00"
    assert projection["updated_at"] == "2026-06-24T12:00:00+00:00"


def test_exit_snapshot_capture_fails_closed_on_unverified_market_scan(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-exit-stale-scan",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
    )

    monkeypatch.setattr("src.data.market_scanner.get_sibling_outcomes", lambda market_id: [{"market_id": market_id}])
    monkeypatch.setattr("src.data.market_scanner.get_last_scan_authority", lambda: "STALE")
    monkeypatch.setattr(
        "src.data.market_scanner.capture_executable_market_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stale scan must not capture snapshot")),
    )

    context = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        object(),
        position,
        YES_TOKEN,
        now=_NOW,
    )

    assert context == {}


def test_exit_snapshot_capture_fails_closed_when_capture_returns_no_id(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-exit-no-snapshot-id",
        market_id="condition-test",
        condition_id="condition-test",
        city="NYC",
        cluster="northeast",
        target_date="2026-04-28",
        bin_label="50-51°F",
        direction="buy_yes",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.50,
        size_usd=10.0,
        shares=20.0,
    )

    monkeypatch.setattr(
        "src.data.market_scanner.get_sibling_outcomes",
        lambda market_id: [
            {
                "market_id": market_id,
                "condition_id": market_id,
                "question_id": "question-test",
                "token_id": YES_TOKEN,
                "no_token_id": NO_TOKEN,
            }
        ],
    )
    monkeypatch.setattr("src.data.market_scanner.get_last_scan_authority", lambda: "VERIFIED")
    monkeypatch.setattr(
        "src.data.market_scanner.capture_executable_market_snapshot",
        lambda *args, **kwargs: {
            "executable_snapshot_id": "",
            "executable_snapshot_min_tick_size": "0.01",
            "executable_snapshot_min_order_size": "0.01",
            "executable_snapshot_neg_risk": False,
        },
    )

    context = exit_lifecycle._latest_or_capture_exit_snapshot_context(
        conn,
        object(),
        position,
        YES_TOKEN,
        now=_NOW,
    )

    assert context == {}


def test_exit_preflight_uses_token_balance_not_pusd(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.collateral_ledger import CollateralLedger, configure_global_ledger

    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000_000, ctf={YES_TOKEN: 0}))
    configure_global_ledger(ledger)
    _allow_risk_allocator_for_exit_tests()
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.ws_gap_guard.assert_ws_allows_submit", lambda *args, **kwargs: None)

    class ClientWithNoCtfInventory:
        def _ensure_v2_adapter(self):
            return self

        def get_ctf_collateral_payload(self, *, token_ids):
            assert token_ids == [YES_TOKEN]
            return {
                "authority_tier": "CHAIN",
                "ctf_token_balances": {YES_TOKEN: 0},
                "ctf_token_allowances": {YES_TOKEN: 0},
            }

        def place_limit_order(self, **_kwargs):  # pragma: no cover - tripwire
            raise AssertionError("CTF preflight must block venue submit")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientWithNoCtfInventory)
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="pos-token-block",
                token_id=YES_TOKEN,
                shares=5.0,
                current_price=0.50,
                best_bid=0.49,
            ),
            conn=conn,
            decision_id="token-block",
        )
        assert result.reason and "ctf_tokens_insufficient" in result.reason
        assert "pusd" not in result.reason.lower()
        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0
    finally:
        from src.risk_allocator import clear_global_allocator

        clear_global_allocator()
        configure_global_ledger(None)


def test_mutex_held_blocks_concurrent_exit(conn):
    from src.execution.exit_safety import ExitMutex

    _insert_exit_command(conn, command_id="cmd-a")
    _insert_exit_command(conn, command_id="cmd-b", position_id="pos-2")
    mutex = ExitMutex(conn)

    assert mutex.acquire("pos-1", YES_TOKEN, "cmd-a") is True
    assert mutex.acquire("pos-1", YES_TOKEN, "cmd-b") is False
    assert mutex.acquire("pos-2", YES_TOKEN, "cmd-b") is True
    assert conn.execute("SELECT COUNT(*) FROM exit_mutex_holdings WHERE released_at IS NULL").fetchone()[0] == 2


def test_exit_order_posted_projection_uses_exit_order_not_entry_order(conn):
    from src.state.db import transition_phase
    from src.state.portfolio import Position

    pos = Position(
        trade_id="pos-projection-exit",
        market_id="mkt-1",
        city="Manila",
        cluster="Asia",
        target_date="2026-07-01",
        bin_label="29C",
        direction="buy_yes",
        size_usd=1.0,
        shares=9.7,
        cost_basis_usd=0.15,
        entry_price=0.015,
        p_posterior=0.1,
        state="pending_exit",
        pre_exit_state="entered",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        unit="C",
        env="live",
        strategy_key="center_buy",
        order_id="ord-entry-old",
        order_status="partial",
        exit_state="sell_placed",
        last_exit_order_id="ord-exit-live",
        exit_reason="ENTRY_SELECTION_GUARD_INVALID_EXIT",
    )

    assert transition_phase(
        conn,
        pos,
        event_type="EXIT_ORDER_POSTED",
        reason=pos.exit_reason,
        error="",
    )

    row = conn.execute(
        "SELECT phase, order_id, order_status FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    assert dict(row) == {
        "phase": "pending_exit",
        "order_id": "ord-exit-live",
        "order_status": "sell_placed",
    }


def test_execute_exit_adopts_active_prior_sell_without_new_submit(conn, monkeypatch):
    from src.execution.exit_lifecycle import execute_exit
    from src.state.portfolio import ExitContext, PortfolioState, Position

    _insert_exit_command(
        conn,
        command_id="cmd-active-exit",
        position_id="pos-active-exit",
        venue_order_id="ord-active-exit",
        size=9.7,
        price=0.50,
    )
    _ack_exit(conn, command_id="cmd-active-exit", venue_order_id="ord-active-exit")

    pos = Position(
        trade_id="pos-active-exit",
        market_id="mkt-1",
        city="Chongqing",
        cluster="Asia",
        target_date="2026-07-01",
        bin_label="24C",
        direction="buy_yes",
        size_usd=1.0,
        shares=9.7,
        cost_basis_usd=0.15,
        entry_price=0.015,
        p_posterior=0.1,
        state="entered",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        unit="C",
        env="live",
        strategy_key="center_buy",
        order_id="ord-entry-old",
        order_status="partial",
    )

    def no_new_sell(**_kwargs):
        raise AssertionError("active prior exit sell must be adopted, not duplicated")

    monkeypatch.setattr("src.execution.exit_lifecycle.place_sell_order", no_new_sell)

    result = execute_exit(
        PortfolioState(positions=[pos]),
        pos,
        ExitContext(
            exit_reason="ENTRY_SELECTION_GUARD_INVALID_EXIT",
            current_market_price=0.50,
            current_market_price_is_fresh=True,
            best_bid=0.49,
            position_state="active",
        ),
        clob=None,
        conn=conn,
    )

    assert result.startswith("sell_pending: active_prior_exit_sell")
    assert pos.last_exit_order_id == "ord-active-exit"
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ? AND intent_kind = 'EXIT'",
        (pos.trade_id,),
    ).fetchone()[0] == 1
    current = conn.execute(
        "SELECT phase, order_id, order_status FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_id"] == "ord-active-exit"
    assert current["order_status"] == "sell_placed"
    posted_count = conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_POSTED'
           AND order_id = ?
        """,
        (pos.trade_id, "ord-active-exit"),
    ).fetchone()[0]
    assert posted_count == 1

    conn.execute(
        """
        UPDATE position_current
           SET order_status = 'retry_pending'
         WHERE position_id = ?
        """,
        (pos.trade_id,),
    )
    pos.order_status = "retry_pending"
    pos.exit_state = "retry_pending"
    pos.last_exit_order_id = ""

    result = execute_exit(
        PortfolioState(positions=[pos]),
        pos,
        ExitContext(
            exit_reason="ENTRY_SELECTION_GUARD_INVALID_EXIT",
            current_market_price=0.50,
            current_market_price_is_fresh=True,
            best_bid=0.49,
            position_state="active",
        ),
        clob=None,
        conn=conn,
    )

    assert result.startswith("sell_pending: active_prior_exit_sell")
    posted_count = conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_POSTED'
           AND order_id = ?
        """,
        (pos.trade_id, "ord-active-exit"),
    ).fetchone()[0]
    assert posted_count == 1


@pytest.mark.parametrize(
    "order",
    [
        {"price": "0.023", "order_type": "GTC", "post_only": True},
        {"price": "0.50", "order_type": "FAK", "post_only": False},
        {"price": "0.50"},
    ],
)
def test_unproved_venue_open_sell_is_canceled_not_adopted(order):
    from src.execution.exit_lifecycle import _venue_open_exit_sell_order

    class FakeClob:
        def __init__(self):
            self.canceled = []

        def get_open_orders(self):
            return [
                {
                    "id": "ord-unsafe-open-exit",
                    "asset_id": YES_TOKEN,
                    "side": "SELL",
                    "status": "LIVE",
                    "original_size": "9.7",
                    "size_matched": "0",
                    **order,
                }
            ]

        def cancel_order(self, order_id):
            self.canceled.append(order_id)
            return {"canceled": [order_id]}

    clob = FakeClob()

    unsafe = _venue_open_exit_sell_order(
        clob,
        token_id=YES_TOKEN,
        expected_shares=9.7,
    )
    assert unsafe["unsafe_open_exit_order"] is True
    assert unsafe["venue_order_id"] == "ord-unsafe-open-exit"
    assert clob.canceled == ["ord-unsafe-open-exit"]


@pytest.mark.parametrize("price", ["0.049", "0.951", "0.999"])
def test_exit_fill_receipt_preserves_realized_out_of_band_price(price):
    from src.execution.exit_lifecycle import _extract_fill_price

    assert _extract_fill_price({"avgPrice": price}) == pytest.approx(float(price))


@pytest.mark.parametrize("price", ["0.049", "0.951", "0.999"])
def test_reconcile_accepts_realized_out_of_band_fill_economics(price):
    from src.execution.exchange_reconcile import _missing_trade_fill_economics

    assert _missing_trade_fill_economics(
        state="CONFIRMED",
        filled_size="1",
        fill_price=price,
    ) == ()


@pytest.mark.parametrize("price", ["0", "-0.01", "1.001", "NaN"])
def test_reconcile_rejects_invalid_fill_economics(price):
    from src.execution.exchange_reconcile import _missing_trade_fill_economics

    assert _missing_trade_fill_economics(
        state="CONFIRMED",
        filled_size="1",
        fill_price=price,
    ) == ("fill_price",)


@pytest.mark.parametrize("price", ["0.049", "0.951", "0.999"])
def test_recovery_projects_realized_out_of_band_exit_fill(
    monkeypatch,
    caplog,
    price,
):
    from src.execution import command_recovery

    projected = []
    monkeypatch.setattr(
        command_recovery._exchange_reconcile,
        "_ensure_exit_fill_position_event",
        lambda *args, **kwargs: projected.append(kwargs) or True,
    )

    class Conn:
        def execute(self, *args, **kwargs):
            return None

    with caplog.at_level("CRITICAL"):
        assert command_recovery._append_exit_order_fill_projection(
            Conn(),
            command={
                "command_id": "cmd-realized-fill",
                "position_id": "pos-realized-fill",
                "intent_kind": "EXIT",
            },
            venue_order_id="order-bad-fill",
            matched_size="1",
            fill_price=price,
            observed_at="2026-08-01T18:00:00Z",
            event_type="FILL_CONFIRMED",
        )
    assert projected[0]["fill_price"] == price
    if Decimal(price) > Decimal("0.95"):
        assert "projecting realized out-of-band exit fill" not in caplog.text


def test_execute_exit_adopts_matching_venue_open_sell_without_local_command(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.execution.exit_lifecycle import execute_exit
    from src.state.portfolio import ExitContext, PortfolioState, Position

    pos = Position(
        trade_id="pos-venue-open-exit",
        market_id="mkt-1",
        city="Manila",
        cluster="Asia",
        target_date="2026-07-01",
        bin_label="29C",
        direction="buy_yes",
        size_usd=1.0,
        shares=9.7,
        cost_basis_usd=0.15,
        entry_price=0.015,
        p_posterior=0.1,
        state="entered",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        unit="C",
        env="live",
        strategy_key="center_buy",
        order_id="ord-entry-old",
        order_status="partial",
    )

    class FakeClob:
        def get_open_orders(self):
            return [
                {
                    "id": "ord-venue-open-exit",
                    "asset_id": YES_TOKEN,
                    "side": "SELL",
                    "status": "LIVE",
                    "price": "0.50",
                    "order_type": "GTC",
                    "post_only": True,
                    "original_size": "9.7",
                    "size_matched": "0",
                }
            ]

    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("matching venue open sell must be adopted, not duplicated")
        ),
    )

    result = execute_exit(
        PortfolioState(positions=[pos]),
        pos,
        ExitContext(
            exit_reason="ENTRY_SELECTION_GUARD_INVALID_EXIT",
            current_market_price=0.50,
            current_market_price_is_fresh=True,
            best_bid=0.49,
            position_state="active",
        ),
        clob=FakeClob(),
        conn=conn,
    )

    assert result.startswith("sell_pending: active_prior_exit_sell")
    assert pos.last_exit_order_id == "ord-venue-open-exit"
    command = conn.execute(
        """
        SELECT command_id, state, venue_order_id, price, size, review_required_reason
          FROM venue_commands
         WHERE position_id = ?
           AND intent_kind = 'EXIT'
        """,
        (pos.trade_id,),
    ).fetchone()
    assert command is not None
    assert command["command_id"].startswith("adopted_exit_")
    assert command["state"] == "ACKED"
    assert command["venue_order_id"] == "ord-venue-open-exit"
    assert command["price"] == 0.50
    assert command["size"] == 9.7
    assert command["review_required_reason"] == "adopted_from_clob_open_orders;venue_state=LIVE"
    current = conn.execute(
        "SELECT phase, order_id, order_status FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_id"] == "ord-venue-open-exit"
    assert current["order_status"] == "sell_placed"
    event = conn.execute(
        """
        SELECT command_id
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_POSTED'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (pos.trade_id,),
    ).fetchone()
    assert event["command_id"] == command["command_id"]


def test_transition_phase_links_exit_order_to_existing_command(conn):
    from src.state.db import transition_phase
    from src.state.portfolio import Position

    trade_id = "pos-direct-exit-command-link"
    _insert_exit_command(
        conn,
        command_id="cmd-direct-exit-link",
        position_id=trade_id,
        venue_order_id="ord-direct-exit-link",
        size=9.7,
        price=0.05,
    )
    _ack_exit(conn, command_id="cmd-direct-exit-link", venue_order_id="ord-direct-exit-link")
    position = Position(
        trade_id=trade_id,
        market_id="mkt-1",
        city="Miami",
        cluster="US",
        target_date="2026-06-30",
        bin_label="96-97F",
        direction="buy_yes",
        size_usd=4.34,
        shares=85.17,
        cost_basis_usd=4.34,
        entry_price=0.051,
        p_posterior=0.34,
        state="pending_exit",
        pre_exit_state="entered",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        unit="F",
        env="live",
        strategy_key="center_buy",
        order_status="sell_placed",
        exit_state="sell_placed",
        last_exit_order_id="ord-direct-exit-link",
        exit_reason="ENTRY_SELECTION_GUARD_INVALID_EXIT",
    )

    assert transition_phase(
        conn,
        position,
        event_type="EXIT_ORDER_POSTED",
        reason=position.exit_reason,
        error="",
    )
    event = conn.execute(
        """
        SELECT command_id, order_id
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_POSTED'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (trade_id,),
    ).fetchone()
    assert event["order_id"] == "ord-direct-exit-link"
    assert event["command_id"] == "cmd-direct-exit-link"


def test_check_pending_exits_recovers_adopted_open_sell_from_canonical_event(
    conn,
    monkeypatch,
):
    from src.execution import exit_lifecycle
    from src.execution.exit_lifecycle import check_pending_exits
    from src.state.db import transition_phase
    from src.state.portfolio import PortfolioState, Position

    trade_id = "pos-adopted-open-sell-scan"
    posted = Position(
        trade_id=trade_id,
        market_id="mkt-1",
        city="Miami",
        cluster="US",
        target_date="2026-06-30",
        bin_label="96-97F",
        direction="buy_yes",
        size_usd=4.34,
        shares=85.17,
        cost_basis_usd=4.34,
        entry_price=0.051,
        p_posterior=0.34,
        state="pending_exit",
        pre_exit_state="entered",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        unit="F",
        env="live",
        strategy_key="center_buy",
        order_id="ord-entry-old",
        order_status="partial",
        exit_state="sell_placed",
        last_exit_order_id="ord-adopted-open-sell",
        exit_reason="ENTRY_SELECTION_GUARD_INVALID_EXIT",
    )
    assert transition_phase(
        conn,
        posted,
        event_type="EXIT_ORDER_POSTED",
        reason=posted.exit_reason,
        error="ACTIVE_EXIT_SELL_IN_FLIGHT",
    )

    stale_runtime = Position(
        trade_id=trade_id,
        market_id="mkt-1",
        city="Miami",
        cluster="US",
        target_date="2026-06-30",
        bin_label="96-97F",
        direction="buy_yes",
        size_usd=4.34,
        shares=85.17,
        cost_basis_usd=4.34,
        entry_price=0.051,
        p_posterior=0.34,
        state="pending_exit",
        pre_exit_state="entered",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        unit="F",
        env="live",
        strategy_key="center_buy",
        order_id="",
        order_status="sell_placed",
        exit_state="sell_pending",
        last_exit_order_id="",
        exit_reason="ENTRY_SELECTION_GUARD_INVALID_EXIT",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-adopted-open-sell"
            return {"status": "LIVE", "orderID": order_id}

    monkeypatch.setattr(
        exit_lifecycle,
        "_cancel_stale_pending_exit_for_reprice",
        lambda **_kwargs: False,
    )

    stats = check_pending_exits(PortfolioState(positions=[stale_runtime]), FakeClob(), conn=conn)

    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert stale_runtime.last_exit_order_id == "ord-adopted-open-sell"
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_REJECTED'
           AND payload_json LIKE '%no_order_id%'
        """,
        (trade_id,),
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    (
        "order_size",
        "current_shares_after_adoption",
        "expected_shares",
        "expected_filled",
        "expected_reduced",
    ),
    [
        (6.0, 20.0, 14.0, 0, 1),
        (20.0, 20.0, 20.0, 1, 0),
        (20.0, 19.0, 19.0, 0, 0),
    ],
)
def test_adopted_external_sell_fill_obeys_immutable_adoption_size(
    conn,
    order_size,
    current_shares_after_adoption,
    expected_shares,
    expected_filled,
    expected_reduced,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    trade_id = f"pos-adopted-external-fill-{order_size:g}"
    position = Position(
        trade_id=trade_id,
        market_id="mkt-adopted",
        city="Beijing",
        cluster="Asia",
        target_date="2026-07-27",
        bin_label="34C",
        direction="buy_no",
        size_usd=12.0,
        shares=20.0,
        cost_basis_usd=12.0,
        entry_price=0.60,
        p_posterior=0.20,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-adopted",
        unit="C",
        env="live",
        strategy_key="center_buy",
        order_status="filled",
    )
    order_id = f"ord-adopted-external-{order_size:g}"
    adopted_row = {
        "command_id": "",
        "state": "LIVE",
        "venue_order_id": order_id,
        "price": 0.50,
        "size": order_size,
        "updated_at": _NOW.isoformat(),
        "created_at": _NOW.isoformat(),
    }
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, order_id,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, 1, 'EXIT_ORDER_POSTED', ?, 'active', 'pending_exit',
                  'center_buy', ?, 'tests.test_exit_safety', '{}', 'live')
        """,
        (
            f"{trade_id}:generic_exit_posted:1",
            trade_id,
            (_NOW - timedelta(seconds=1)).isoformat(),
            order_id,
        ),
    )

    exit_lifecycle._adopt_active_exit_sell(
        position,
        adopted_row,
        conn=conn,
        reason="ACTIVE_EXIT_SELL_IN_FLIGHT",
    )

    authority = conn.execute(
        """
        SELECT json_extract(payload_json, '$.adopted_order_size'),
               json_extract(payload_json, '$.position_shares_at_adoption')
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_POSTED'
           AND order_id = ?
           AND json_extract(
                   payload_json,
                   '$.exit_intent_authority'
               ) = 'ADOPTED_EXTERNAL_SELL'
         ORDER BY sequence_no
         LIMIT 1
        """,
        (trade_id, order_id),
    ).fetchone()
    assert tuple(authority) == (str(Decimal(str(order_size))), "20.0")
    position.shares = current_shares_after_adoption
    position.chain_shares = current_shares_after_adoption

    class FakeClob:
        def get_order_status(self, observed_order_id):
            assert observed_order_id == order_id
            return {
                "status": "CONFIRMED",
                "remaining_size": "0",
                "matched_size": str(order_size),
                "avgPrice": "0.50",
            }

    portfolio = PortfolioState(positions=[position])
    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    assert stats["filled"] == expected_filled
    assert stats.get("reduced", 0) == expected_reduced
    if expected_filled:
        assert stats["filled_positions"] == [position]
        assert position.state == "economically_closed"
    else:
        assert position.state == (
            "holding" if expected_reduced else "pending_exit"
        )
        assert position.shares == pytest.approx(expected_shares)
        assert conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_ORDER_FILLED'
            """,
            (trade_id,),
        ).fetchone()[0] == 0


def test_adopted_external_sell_without_exact_size_has_no_close_authority(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-adopted-external-missing-size",
        market_id="mkt-adopted-missing-size",
        city="Beijing",
        cluster="Asia",
        target_date="2026-07-27",
        bin_label="34C",
        direction="buy_no",
        size_usd=12.0,
        shares=20.0,
        cost_basis_usd=12.0,
        entry_price=0.60,
        p_posterior=0.20,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-adopted-missing-size",
        unit="C",
        env="live",
        strategy_key="center_buy",
        order_status="filled",
    )
    order_id = "ord-adopted-external-missing-size"

    exit_lifecycle._adopt_active_exit_sell(
        position,
        {
            "command_id": "",
            "state": "LIVE",
            "venue_order_id": order_id,
            "price": 0.50,
            "updated_at": _NOW.isoformat(),
            "created_at": _NOW.isoformat(),
        },
        conn=conn,
        reason="ACTIVE_EXIT_SELL_IN_FLIGHT",
    )

    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM venue_commands
         WHERE position_id = ?
           AND venue_order_id = ?
        """,
        (position.trade_id, order_id),
    ).fetchone()[0] == 0
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND order_id = ?
           AND json_extract(
                   payload_json,
                   '$.exit_intent_authority'
               ) = 'ADOPTED_EXTERNAL_SELL'
        """,
        (position.trade_id, order_id),
    ).fetchone()[0] == 0


@pytest.mark.parametrize("authority_path", ["global", "hard_fact", "red"])
def test_execute_exit_preserves_adopted_order_when_bid_is_sub_floor(
    conn,
    monkeypatch,
    authority_path,
):
    from src.execution import exit_lifecycle
    from src.execution.exit_lifecycle import execute_exit
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import ExitContext, PortfolioState, Position

    reason = {
        "global": "GLOBAL_CAPITAL_OPTIMAL_SELL",
        "hard_fact": "DAY0_HARD_FACT_BIN_DEAD",
        "red": "RED_FORCE_EXIT",
    }[authority_path]

    pos = Position(
        trade_id="pos-adopted-cancel",
        market_id="mkt-1",
        city="Manila",
        cluster="Asia",
        target_date="2026-07-01",
        bin_label="29C",
        direction="buy_yes",
        size_usd=1.0,
        shares=9.7,
        cost_basis_usd=0.15,
        entry_price=0.015,
        p_posterior=0.1,
        state="pending_exit",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        unit="C",
        env="live",
        strategy_key="center_buy",
        last_exit_order_id="ord-venue-open-exit",
        exit_retry_count=1,
        exit_state="retry_pending",
        order_status="retry_pending",
    )
    hard_fact_authority = None
    if authority_path == "hard_fact":
        hard_fact_authority = object()
        monkeypatch.setattr(
            exit_lifecycle,
            "_hard_fact_sell_authority_valid",
            lambda *args, **kwargs: True,
        )
    elif authority_path == "red":
        pos.exit_reason = "red_force_exit"
        monkeypatch.setattr(
            "src.riskguard.riskguard.get_current_level",
            lambda: RiskLevel.RED,
        )

    class FakeClob:
        def cancel_order(self, order_id):
            raise AssertionError(f"valid resting exit must not be canceled: {order_id}")

        def get_order_status(self, order_id):
            assert order_id == "ord-venue-open-exit"
            return {
                "status": "CONFIRMED",
                "remaining_size": "0.00",
                "matched_size": "9.70",
                "avgPrice": "0.05",
            }

    monkeypatch.setattr(
        exit_lifecycle,
        "_latest_or_capture_exit_snapshot_context",
            lambda *args, **kwargs: {
                "executable_snapshot_id": "snap-adopted-cancel",
                "executable_snapshot_min_order_size": "5",
                "executable_snapshot_orderbook_top_bid": "0.01",
            },
        )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_sell_collateral",
        lambda *args, **kwargs: (True, "ok"),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "_refresh_exit_collateral_snapshot_for_submit",
        lambda *args, **kwargs: {"component": "collateral_snapshot_refresh", "allowed": True},
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "place_sell_order",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("cancel-for-reprice returns to retry before a replacement submit")
        ),
    )
    exit_intent = exit_lifecycle.ExitIntent(
        trade_id=pos.trade_id,
        reason=reason,
        token_id=YES_TOKEN,
        shares=pos.effective_shares,
        current_market_price=0.02,
        best_bid=0.01,
        exact_limit_price=0.02,
        submit_order_type="FAK",
        capital_certificate={
            "action": "SELL",
            "candidate_id": "global-reprice-candidate",
            "actuation_identity": "global-reprice-actuation",
            "economic_identity": "global-reprice-economic",
            "probability_witness_identity": "global-reprice-witness",
            "robust_delta_log_wealth": "0.001",
            "robust_ev_usd": "0.01",
            "held_shares": str(pos.effective_shares),
            "selected_shares": str(pos.effective_shares),
            "exact_limit_price": "0.02",
        },
    )

    portfolio = PortfolioState(positions=[pos])
    result = execute_exit(
        portfolio,
        pos,
        ExitContext(
            exit_reason=reason,
            current_market_price=0.02,
            current_market_price_is_fresh=True,
            best_bid=0.01,
            position_state="pending_exit",
        ),
        clob=FakeClob(),
        conn=conn,
        exit_intent=exit_intent,
        hard_fact_authority=hard_fact_authority,
    )

    assert pos.last_exit_order_id == "ord-venue-open-exit"
    assert pos.exit_retry_count == 1
    if authority_path == "global":
        assert result == "exit_blocked: global_sell_execution_authority_required"
        assert pos.exit_state == "retry_pending"
        assert pos.order_status == "retry_pending"
        assert pos.last_exit_error in ("", None)
        assert conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ?",
            (pos.trade_id,),
        ).fetchone()[0] == 0
    else:
        assert result == "exit_blocked: no_in_band_bid"
        assert pos.exit_state == "sell_pending"
        assert pos.order_status == "sell_pending_confirmation"
        assert pos.last_exit_error == "exit_no_in_band_bid"
        assert pos.next_exit_retry_at == ""
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM position_events WHERE position_id = ? "
                "ORDER BY sequence_no DESC LIMIT 1",
                (pos.trade_id,),
            ).fetchone()[0]
        )
        assert payload["status"] == "resting_exit_liquidity_wait"
        assert payload["resting_exit_order_preserved"] is True

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    if authority_path == "global":
        assert stats["filled"] == 0
        assert pos.state == "pending_exit"
        assert pos.exit_state == "retry_pending"
    else:
        assert stats["filled"] == 1
        assert pos.state == "economically_closed"
        assert pos.exit_state == "sell_filled"


def test_exit_active_order_lock_retry_does_not_consume_backoff_budget(conn):
    from src.execution.exit_lifecycle import _mark_exit_retry
    from src.state.portfolio import Position

    pos = Position(
        trade_id="pos-active-lock",
        market_id="mkt-1",
        city="Manila",
        cluster="Asia",
        target_date="2026-07-01",
        bin_label="29C",
        direction="buy_yes",
        size_usd=1.0,
        shares=9.7,
        cost_basis_usd=0.15,
        entry_price=0.015,
        p_posterior=0.1,
        state="pending_exit",
        pre_exit_state="entered",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-test",
        unit="C",
        env="live",
        strategy_key="center_buy",
        exit_state="retry_pending",
        exit_retry_count=3,
        exit_reason="ENTRY_SELECTION_GUARD_INVALID_EXIT",
    )

    _mark_exit_retry(
        pos,
        reason="ENTRY_SELECTION_GUARD_INVALID_EXIT [SELL_ERROR]",
        error=(
            "venue_rejected_400: not enough balance / allowance: "
            "sum of active orders: 9700000"
        ),
        conn=conn,
    )

    assert pos.exit_retry_count == 3
    assert pos.exit_state == "retry_pending"
    assert pos.next_exit_retry_at
    current = conn.execute(
        "SELECT phase, order_status, exit_retry_count, next_exit_retry_at FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "retry_pending"
    assert current["exit_retry_count"] == 3
    assert current["next_exit_retry_at"]


def test_exit_pre_submit_db_lock_retries_next_cycle_without_budget(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    now = datetime(2026, 7, 26, 22, 7, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(exit_lifecycle, "_utcnow", lambda: now)
    pos = Position(
        trade_id="pos-pre-submit-db-lock",
        market_id="mkt-db-lock",
        city="Tel Aviv",
        cluster="Asia",
        target_date="2026-07-27",
        bin_label="33C",
        direction="buy_yes",
        size_usd=26.5,
        shares=30.6,
        cost_basis_usd=26.5,
        entry_price=0.86,
        p_posterior=0.124,
        state="pending_exit",
        pre_exit_state="day0_window",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-db-lock",
        unit="C",
        env="live",
        strategy_key="forecast_qkernel_entry",
        exit_state="exit_intent",
        exit_retry_count=4,
        exit_reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
    )

    exit_lifecycle._mark_exit_retry(
        pos,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL [SELL_ERROR]",
        error="pre_submit_db_locked_transient: database is locked",
        conn=conn,
    )

    assert pos.exit_state == "retry_pending"
    assert pos.exit_retry_count == 4
    assert pos.next_exit_retry_at == now.isoformat()
    row = conn.execute(
        "SELECT exit_retry_count, next_exit_retry_at FROM position_current "
        "WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    assert tuple(row) == (4, now.isoformat())
    payload = json.loads(
        conn.execute(
            "SELECT payload_json FROM position_events WHERE position_id = ? "
            "ORDER BY sequence_no DESC LIMIT 1",
            (pos.trade_id,),
        ).fetchone()[0]
    )
    assert payload["status"] == "pre_submit_db_lock"
    assert payload["side_effect_boundary_crossed"] is False
    assert exit_lifecycle._is_pre_submit_db_locked_error(
        "pre_submit_db_locked_transient: database is locked "
        "(writer lease timeout: DB write lease timed out)"
    )


def test_mutex_reacquire_released_row_fails_closed_on_stale_compare(conn):
    from src.execution.exit_safety import ExitMutex

    class StaleSelectCursor:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class InterleavingConnection:
        def __init__(self, inner):
            self.inner = inner
            self.interleaved = False

        def execute(self, sql, params=()):
            if (
                not self.interleaved
                and "SELECT command_id, released_at" in sql
                and "FROM exit_mutex_holdings" in sql
                and "WHERE mutex_key = ?" in sql
            ):
                stale_row = self.inner.execute(sql, params).fetchone()
                assert stale_row["released_at"] is not None
                self.inner.execute(
                    """
                    UPDATE exit_mutex_holdings
                       SET command_id = ?, acquired_at = ?, released_at = NULL, release_reason = NULL
                     WHERE mutex_key = ?
                       AND released_at IS NOT NULL
                    """,
                    ("cmd-b", _NOW.isoformat(), params[0]),
                )
                self.interleaved = True
                return StaleSelectCursor(stale_row)
            return self.inner.execute(sql, params)

    _insert_exit_command(conn, command_id="cmd-a")
    _insert_exit_command(conn, command_id="cmd-b")
    _insert_exit_command(conn, command_id="cmd-c")
    mutex = ExitMutex(conn)
    assert mutex.acquire("pos-1", YES_TOKEN, "cmd-a") is True
    mutex.release("pos-1", YES_TOKEN, "cmd-a", reason="test_release")

    raced_conn = InterleavingConnection(conn)
    raced_mutex = ExitMutex(raced_conn)  # type: ignore[arg-type]
    assert raced_mutex.acquire("pos-1", YES_TOKEN, "cmd-c") is False
    assert raced_conn.interleaved is True

    row = conn.execute(
        "SELECT command_id, released_at FROM exit_mutex_holdings WHERE mutex_key = ?",
        (f"pos-1:{YES_TOKEN}",),
    ).fetchone()
    assert row["command_id"] == "cmd-b"
    assert row["released_at"] is None


def test_mutex_released_on_cancel_confirmed_or_filled_or_expired(conn):
    from src.execution.exit_safety import ExitMutex
    from src.state.venue_command_repo import append_event

    _insert_exit_command(conn, command_id="cmd-a", venue_order_id="ord-1")
    _ack_exit(conn, command_id="cmd-a", venue_order_id="ord-1")
    mutex = ExitMutex(conn)
    assert mutex.acquire("pos-1", YES_TOKEN, "cmd-a") is True

    append_event(
        conn,
        command_id="cmd-a",
        event_type="CANCEL_REQUESTED",
        occurred_at=_NOW.isoformat(),
        payload={"venue_order_id": "ord-1"},
    )
    assert mutex.acquire("pos-1", YES_TOKEN, "cmd-a") is True
    append_event(
        conn,
        command_id="cmd-a",
        event_type="CANCEL_ACKED",
        occurred_at=_NOW.isoformat(),
        payload={"venue_order_id": "ord-1"},
    )

    row = conn.execute("SELECT released_at, release_reason FROM exit_mutex_holdings WHERE mutex_key = ?", (f"pos-1:{YES_TOKEN}",)).fetchone()
    assert row["released_at"] is not None
    assert row["release_reason"] == "CANCELLED"

    _insert_exit_command(conn, command_id="cmd-b", position_id="pos-1")
    assert mutex.acquire("pos-1", YES_TOKEN, "cmd-b") is True


def test_mutex_released_on_review_required_but_replacement_still_blocked(conn):
    from src.execution.exit_safety import ExitMutex, can_submit_replacement_sell
    from src.state.venue_command_repo import append_event

    _insert_exit_command(conn, command_id="cmd-review", venue_order_id="ord-review")
    mutex = ExitMutex(conn)
    assert mutex.acquire("pos-1", YES_TOKEN, "cmd-review") is True

    append_event(
        conn,
        command_id="cmd-review",
        event_type="REVIEW_REQUIRED",
        occurred_at=_NOW.isoformat(),
        payload={
            "reason": "final_submission_envelope_persistence_failed",
            "venue_order_id": "ord-review",
        },
    )

    row = conn.execute(
        "SELECT released_at, release_reason FROM exit_mutex_holdings WHERE mutex_key = ?",
        (f"pos-1:{YES_TOKEN}",),
    ).fetchone()
    assert row["released_at"] is not None
    assert row["release_reason"] == "REVIEW_REQUIRED"

    allowed, reason = can_submit_replacement_sell(conn, "pos-1", YES_TOKEN)
    assert allowed is False
    assert reason == "active_prior_exit_sell: state=REVIEW_REQUIRED command_id=cmd-review"


def test_review_required_recovery_releases_legacy_exit_mutex_only(conn):
    from src.execution.exit_safety import (
        ExitMutex,
        can_submit_replacement_sell,
        reconcile_review_required_exit_mutex_releases,
    )
    from src.state.venue_command_repo import append_event

    _insert_exit_command(conn, command_id="cmd-legacy-review", venue_order_id="ord-review")
    append_event(
        conn,
        command_id="cmd-legacy-review",
        event_type="REVIEW_REQUIRED",
        occurred_at=_NOW.isoformat(),
        payload={
            "reason": "matched orders cannot be canceled",
            "venue_order_id": "ord-review",
        },
    )

    mutex = ExitMutex(conn)
    assert mutex.acquire("pos-1", YES_TOKEN, "cmd-legacy-review") is True

    summary = reconcile_review_required_exit_mutex_releases(conn)

    assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
    row = conn.execute(
        "SELECT released_at, release_reason FROM exit_mutex_holdings WHERE mutex_key = ?",
        (f"pos-1:{YES_TOKEN}",),
    ).fetchone()
    assert row["released_at"] is not None
    assert row["release_reason"] == "REVIEW_REQUIRED_RECOVERY"

    allowed, reason = can_submit_replacement_sell(conn, "pos-1", YES_TOKEN)
    assert allowed is False
    assert reason == "active_prior_exit_sell: state=REVIEW_REQUIRED command_id=cmd-legacy-review"


@pytest.mark.parametrize("venue_status", ("CANCELED", "EXPIRED"))
def test_global_maker_rest_void_without_proof_stays_single_flight(
    conn,
    venue_status,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

    position = Position(
        trade_id="pos-singapore-maker-void",
        market_id="mkt-singapore-maker-void",
        city="Singapore",
        cluster="Asia",
        target_date="2026-08-03",
        bin_label="33C",
        direction="buy_yes",
        strategy_key="center_buy",
        size_usd=3.6,
        entry_price=0.60,
        shares=6.0,
        chain_shares=6.0,
        cost_basis_usd=3.6,
        state="holding",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-singapore-maker-void",
        unit="C",
        env="live",
        order_status="filled",
    )
    intent = exit_lifecycle.ExitIntent(
        trade_id=position.trade_id,
        reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
        token_id=YES_TOKEN,
        shares=6.0,
        current_market_price=0.06,
        best_bid=0.05,
        exact_limit_price=0.06,
        submit_order_type="GTC",
        capital_certificate={"execution_mode": "MAKER_REST"},
    )
    exit_lifecycle._record_exit_intent_before_execution_gates(conn, position, intent)
    _insert_exit_command(
        conn,
        command_id="cmd-singapore-maker-void",
        position_id=position.trade_id,
        token_id=YES_TOKEN,
        size=6.0,
        price=0.06,
        venue_order_id="ord-singapore-maker-void",
    )
    _ack_exit(
        conn,
        command_id="cmd-singapore-maker-void",
        venue_order_id="ord-singapore-maker-void",
    )
    position.last_exit_order_id = "ord-singapore-maker-void"
    position.exit_state = "sell_pending"
    position.order_status = "sell_pending_confirmation"
    assert exit_lifecycle._dual_write_canonical_pending_exit_if_available(
        conn,
        position,
        reason=intent.reason,
        error="",
        event_type="EXIT_ORDER_POSTED",
    )

    class FakeClob:
        def get_order_status(self, order_id):
            assert order_id == "ord-singapore-maker-void"
            return {"status": venue_status, "matched_size": "0"}

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        FakeClob(),
        conn=conn,
    )

    assert stats["retried"] == 0
    assert stats["unchanged"] == 1
    assert position.state == "pending_exit"
    assert position.exit_state == "sell_pending"
    assert position.order_status == "sell_pending_confirmation"
    assert position.exit_retry_count == 0
    assert not position.next_exit_retry_at
    assert position.last_exit_order_id == "ord-singapore-maker-void"
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE position_id = ? AND side = 'SELL'",
        (position.trade_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        """
        SELECT COUNT(*) FROM position_events
         WHERE position_id = ?
           AND event_type IN ('EXIT_ORDER_VOIDED', 'EXIT_ORDER_REJECTED',
                              'EXIT_RETRY_RELEASED')
        """,
        (position.trade_id,),
    ).fetchone()[0] == 0


def test_pending_exit_incomplete_order_truth_defers_without_state_mutation(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState

    position = _seed_pending_exit_reprice_case(
        conn,
        trade_id="pos-order-truth-incomplete",
        command_id="cmd-order-truth-incomplete",
        order_id="ord-order-truth-incomplete",
        reason="EXIT_PROBABILITY_DECAY",
        capital_certificate=None,
    )
    before = (
        position.state,
        position.exit_state,
        position.order_status,
        position.exit_retry_count,
        position.last_exit_order_id,
    )
    observed_deadlines = []

    class FakeClob:
        def get_order_status(self, order_id, *, deadline_monotonic):
            assert order_id == position.last_exit_order_id
            observed_deadlines.append(deadline_monotonic)
            return {"status": "FETCH_ERROR", "reason": "transport unavailable"}

    deadline = exit_lifecycle._time_module.monotonic() + 1.0
    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        FakeClob(),
        conn=conn,
        deadline_monotonic=deadline,
    )

    assert observed_deadlines == [pytest.approx(deadline)]
    assert stats["pending_exit_positions_deferred"] == 1
    assert stats["pending_exit_defer_reason"] == "order_truth_incomplete"
    assert stats["filled"] == 0
    assert stats["retried"] == 0
    assert (
        position.state,
        position.exit_state,
        position.order_status,
        position.exit_retry_count,
        position.last_exit_order_id,
    ) == before


@pytest.mark.parametrize(
    "incomplete_payload",
    [
        None,
        {},
        {"reason": "missing status"},
        [],
        ["MALFORMED"],
        7,
        False,
    ],
)
def test_pending_exit_empty_order_truth_never_accumulates_retry_or_reprices(
    conn,
    incomplete_payload,
):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState

    position = _seed_pending_exit_reprice_case(
        conn,
        trade_id="pos-empty-order-truth",
        command_id="cmd-empty-order-truth",
        order_id="ord-empty-order-truth",
        reason="EXIT_PROBABILITY_DECAY",
        capital_certificate=None,
    )
    before = (
        position.state,
        position.exit_state,
        position.order_status,
        position.exit_retry_count,
        position.next_exit_retry_at,
    )

    class FakeClob:
        def get_order_status(self, _order_id, *, deadline_monotonic):
            assert deadline_monotonic > exit_lifecycle._time_module.monotonic()
            return incomplete_payload

        def cancel_order(self, _order_id):
            raise AssertionError("incomplete order truth cannot authorize cancel/reprice")

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        FakeClob(),
        conn=conn,
    )

    assert stats["pending_exit_defer_reason"] == "order_truth_incomplete"
    assert stats["retried"] == 0
    assert (
        position.state,
        position.exit_state,
        position.order_status,
        position.exit_retry_count,
        position.next_exit_retry_at,
    ) == before


def test_pending_exit_fallback_order_id_is_not_projected_on_unknown_truth(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState

    position = _seed_pending_exit_reprice_case(
        conn,
        trade_id="pos-fallback-order-unknown",
        command_id="cmd-fallback-order-unknown",
        order_id="ord-fallback-order-unknown",
        reason="EXIT_PROBABILITY_DECAY",
        capital_certificate=None,
    )
    position.last_exit_order_id = ""

    class FakeClob:
        def get_order_status(self, order_id, *, deadline_monotonic):
            assert order_id == "ord-fallback-order-unknown"
            assert deadline_monotonic > exit_lifecycle._time_module.monotonic()
            return {"status": "FETCH_ERROR"}

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[position]),
        FakeClob(),
        conn=conn,
    )

    assert stats["pending_exit_defer_reason"] == "order_truth_incomplete"
    assert position.last_exit_order_id == ""
    assert position.exit_retry_count == 0
