# Created: 2026-04-27
# Last reused/audited: 2026-07-11
# Lifecycle: created=2026-04-27; last_reviewed=2026-07-11; last_reused=2026-07-11
# Authority basis: docs/operations/task_2026-05-08_object_invariance_remaining_mainline/PLAN.md
# Purpose: R3 M5 exchange reconciliation sweep antibodies.
# Reuse: Run when exchange_reconcile, venue facts, findings, heartbeat/cutover reconciliation, or operator finding resolution changes.
"""R3 M5 exchange-reconciliation findings and trade-fact tests."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.venue.polymarket_v2_adapter import OrderState, PositionFact, TradeFact

NOW = datetime(2026, 4, 27, 19, 30, tzinfo=timezone.utc)
YES_TOKEN = "yes-token-m5"
_DEFAULT_FILL_PRICE = object()


@pytest.fixture
def conn():
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    # Single-connection fixture plays both DB roles; world init_schema no
    # longer creates trade-class collateral tables (K1 split, 1b51db387).
    init_collateral_schema(c)
    yield c
    c.close()


class FakeM5Adapter:
    def __init__(self, *, open_orders=None, trades=None, positions=None):
        self.open_orders = open_orders or []
        self.trades = trades or []
        self.positions = positions or []
        self.read_freshness = {"open_orders": True, "trades": True, "positions": True}
        self.calls = []

    def get_open_orders(self):
        self.calls.append(("get_open_orders", (), {}))
        return self.open_orders

    def get_trades(self):
        self.calls.append(("get_trades", (), {}))
        return self.trades

    def get_positions(self):
        self.calls.append(("get_positions", (), {}))
        return self.positions


class FakeAdapterWithoutTrades:
    def __init__(self, *, open_orders=None, positions=None):
        self.open_orders = open_orders or []
        self.positions = positions or []
        self.read_freshness = {"open_orders": True, "positions": True}
        self.calls = []

    def get_open_orders(self):
        self.calls.append(("get_open_orders", (), {}))
        return self.open_orders

    def get_positions(self):
        self.calls.append(("get_positions", (), {}))
        return self.positions


class FakeAdapterWithoutPositions:
    def __init__(self, *, open_orders=None, trades=None):
        self.open_orders = open_orders or []
        self.trades = trades or []
        self.read_freshness = {"open_orders": True, "trades": True}

    def get_open_orders(self):
        return self.open_orders

    def get_trades(self):
        return self.trades


class FakeAdapterWithoutFreshness:
    def __init__(self, *, open_orders=None, trades=None, positions=None):
        self.open_orders = open_orders or []
        self.trades = trades or []
        self.positions = positions or []

    def get_open_orders(self):
        return self.open_orders

    def get_trades(self):
        return self.trades

    def get_positions(self):
        return self.positions


class FakeM5AdapterWithPointOrders(FakeM5Adapter):
    def __init__(self, *, orders_by_id=None, open_orders=None, trades=None, positions=None):
        super().__init__(open_orders=open_orders, trades=trades, positions=positions)
        self.orders_by_id = orders_by_id or {}

    def get_order(self, order_id):
        self.calls.append(("get_order", (order_id,), {}))
        return self.orders_by_id.get(order_id)


class FailingCommitConnection(sqlite3.Connection):
    fail_commit = False

    def commit(self):
        if self.fail_commit:
            raise sqlite3.OperationalError("injected commit failure")
        return super().commit()


def order(order_id="ord-m5", status="LIVE", **raw):
    payload = {"orderID": order_id, "status": status, **raw}
    return OrderState(order_id=order_id, status=status, raw=payload)


def trade(
    trade_id="trade-m5",
    order_id="ord-m5",
    size="5",
    price="0.50",
    status="MATCHED",
    include_price: bool = True,
    include_fill_price: bool = True,
    fill_price=_DEFAULT_FILL_PRICE,
    **raw,
):
    payload = {
        "id": trade_id,
        "trade_id": trade_id,
        "orderID": order_id,
        "order_id": order_id,
        "size": size,
        "status": status,
        **raw,
    }
    if include_price:
        payload["price"] = price
    if include_fill_price:
        payload["fill_price"] = price if fill_price is _DEFAULT_FILL_PRICE else fill_price
    return TradeFact(raw=payload)


def position(token_id=YES_TOKEN, size="10", **raw):
    payload = {"asset": token_id, "token_id": token_id, "size": size, **raw}
    return PositionFact(raw=payload)


def _ensure_snapshot(
    c,
    *,
    token_id: str = YES_TOKEN,
    no_token_id: str | None = None,
    selected_outcome_token_id: str | None = None,
    outcome_label: str = "YES",
    snapshot_id: str | None = None,
) -> str:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    no_token_id = no_token_id or f"{token_id}-no"
    selected_outcome_token_id = selected_outcome_token_id or token_id
    snapshot_id = snapshot_id or f"snap-{token_id}"
    if get_snapshot(c, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        c,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-m5",
            event_id="event-m5",
            event_slug="event-m5",
            condition_id="condition-m5",
            question_id="question-m5",
            yes_token_id=token_id,
            no_token_id=no_token_id,
            selected_outcome_token_id=selected_outcome_token_id,
            outcome_label=outcome_label,
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
            fee_details={},
            token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.51"),
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


def _ensure_envelope(
    c,
    *,
    token_id: str = YES_TOKEN,
    yes_token_id: str | None = None,
    no_token_id: str | None = None,
    envelope_id: str | None = None,
    side: str = "BUY",
    price: float | Decimal = 0.50,
    size: float | Decimal = 10.0,
) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    price_dec = Decimal(str(price))
    size_dec = Decimal(str(size))
    yes_token_id = yes_token_id or token_id
    no_token_id = no_token_id or f"{yes_token_id}-no"
    envelope_id = envelope_id or hashlib.sha256(
        f"{token_id}:{side}:{price_dec}:{size_dec}".encode()
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
            condition_id="condition-m5",
            question_id="question-m5",
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
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
            fee_details={},
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
            captured_at=NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    return envelope_id


def seed_command(
    c,
    *,
    command_id: str = "cmd-m5",
    venue_order_id: str = "ord-m5",
    state: str = "ACKED",
    position_id: str = "pos-m5",
    token_id: str = YES_TOKEN,
    side: str = "BUY",
    size: float = 10.0,
    price: float = 0.50,
    created_at: datetime = NOW,
    snapshot_token_id: str | None = None,
    snapshot_no_token_id: str | None = None,
    snapshot_selected_token_id: str | None = None,
    snapshot_outcome_label: str = "YES",
    envelope_yes_token_id: str | None = None,
    envelope_no_token_id: str | None = None,
    q_version: str | None = None,
) -> None:
    from src.state.venue_command_repo import append_event, insert_command

    snapshot_token_id = snapshot_token_id or token_id
    insert_command(
        c,
        command_id=command_id,
        snapshot_id=_ensure_snapshot(
            c,
            token_id=snapshot_token_id,
            no_token_id=snapshot_no_token_id,
            selected_outcome_token_id=snapshot_selected_token_id,
            outcome_label=snapshot_outcome_label,
        ),
        envelope_id=_ensure_envelope(
            c,
            token_id=token_id,
            yes_token_id=envelope_yes_token_id,
            no_token_id=envelope_no_token_id,
            side=side,
            price=price,
            size=size,
        ),
        position_id=position_id,
        decision_id=f"dec-{command_id}",
        idempotency_key=f"idem-{command_id}",
        intent_kind="ENTRY" if side == "BUY" else "EXIT",
        market_id=token_id,
        token_id=token_id,
        side=side,
        size=size,
        price=price,
        created_at=created_at.isoformat(),
        venue_order_id=venue_order_id,
        q_version=q_version,
    )
    if state in {"ACKED", "PARTIAL", "FILLED", "CANCEL_PENDING"}:
        append_event(
            c,
            command_id=command_id,
            event_type="SUBMIT_REQUESTED",
            occurred_at=created_at.isoformat(),
            payload={
                "execution_capability": {
                    "allowed": True,
                    "components": [
                        {
                            "component": "entry_economics",
                            "allowed": True,
                            "details": {
                                "q_live": 0.7,
                                "q_lcb_5pct": 0.6,
                                "expected_edge": 0.1,
                                "min_entry_price": 0.01,
                                "limit_price": price,
                                "submit_edge": 0.1,
                                "expected_profit_usd": 1.0,
                                "min_expected_profit_usd": 0.01,
                                "submit_edge_density": 0.1,
                                "min_submit_edge_density": 0.01,
                                "shares": size,
                                "qkernel_side": "buy_yes",
                            },
                        },
                        {
                            "component": "entry_actionable_certificate",
                            "allowed": True,
                        },
                    ],
                }
            },
        )
        append_event(
            c,
            command_id=command_id,
            event_type="SUBMIT_ACKED",
            occurred_at=created_at.isoformat(),
            payload={"venue_order_id": venue_order_id},
        )
    if state == "PARTIAL":
        append_event(
            c,
            command_id=command_id,
            event_type="PARTIAL_FILL_OBSERVED",
            occurred_at=created_at.isoformat(),
            payload={"venue_order_id": venue_order_id, "filled_size": "5"},
        )
    elif state == "FILLED":
        append_event(
            c,
            command_id=command_id,
            event_type="FILL_CONFIRMED",
            occurred_at=created_at.isoformat(),
            payload={"venue_order_id": venue_order_id, "filled_size": str(size)},
        )
    elif state == "CANCEL_PENDING":
        append_event(
            c,
            command_id=command_id,
            event_type="CANCEL_REQUESTED",
            occurred_at=created_at.isoformat(),
            payload={"venue_order_id": venue_order_id},
        )


def append_resting_order_fact(c, *, command_id="cmd-m5", venue_order_id="ord-m5"):
    from src.state.venue_command_repo import append_order_fact

    append_order_fact(
        c,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state="RESTING",
        remaining_size="10",
        matched_size="0",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(f"{venue_order_id}:RESTING".encode()).hexdigest(),
        raw_payload_json={"orderID": venue_order_id, "status": "RESTING"},
    )


def append_trade_fact(
    c,
    *,
    command_id="cmd-m5",
    venue_order_id="ord-m5",
    token_id=YES_TOKEN,
    trade_id="trade-local",
    size="10",
    fill_price="0.50",
    state="CONFIRMED",
    tx_hash=None,
):
    from src.state.venue_command_repo import append_trade_fact as append

    append(
        c,
        trade_id=trade_id,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state=state,
        filled_size=size,
        fill_price=fill_price,
        source="REST",
        observed_at=NOW,
        tx_hash=tx_hash,
        raw_payload_hash=hashlib.sha256(f"{trade_id}:{token_id}:{size}:{fill_price}:{state}".encode()).hexdigest(),
        raw_payload_json={
            "trade_id": trade_id,
            "order_id": venue_order_id,
            "size": size,
            "price": fill_price,
            "state": state,
        },
    )


def seed_trade_decision_runtime_alias(c, *, trade_id=7, runtime_trade_id="pos-m5") -> None:
    c.execute(
        """
        INSERT INTO trade_decisions (
            trade_id, market_id, bin_label, direction, size_usd, price,
            timestamp, p_raw, p_posterior, edge, ci_lower, ci_upper,
            kelly_fraction, status, runtime_trade_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_id,
            "condition-m5",
            "test-bin",
            "buy_yes",
            10.0,
            0.50,
            NOW.isoformat(),
            0.6,
            0.6,
            0.1,
            0.05,
            0.15,
            0.0,
            "pending",
            runtime_trade_id,
        ),
    )


def findings(c):
    return c.execute(
        "SELECT * FROM exchange_reconcile_findings ORDER BY recorded_at, finding_id"
    ).fetchall()


def command_count(c):
    return c.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0]


def configure_subscribed_m5_latch():
    from src.control import ws_gap_guard

    return ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=True,
            last_message_at=NOW,
            subscription_state="SUBSCRIBED",
            gap_reason="message_received",
            m5_reconcile_required=True,
            updated_at=NOW,
        )
    )


def event_types(c, command_id="cmd-m5"):
    return [
        row["event_type"]
        for row in c.execute(
            "SELECT event_type FROM venue_command_events WHERE command_id = ? ORDER BY sequence_no",
            (command_id,),
        )
    ]


def seed_position_baseline(c, *, position_id="pos-m5", order_id="ord-m5") -> None:
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import append_many_and_project

    position_obj = SimpleNamespace(
        trade_id=position_id,
        state="pending_tracked",
        exit_state="",
        chain_state="local_only",
        market_id="condition-m5",
        city="Karachi",
        cluster="Karachi",
        target_date="2026-05-17",
        bin_label="test-bin",
        direction="buy_yes",
        unit="C",
        size_usd=0,
        shares=0,
        cost_basis_usd=0,
        entry_price=0,
        p_posterior=0.5,
        last_monitor_prob=None,
        last_monitor_edge=None,
        last_monitor_market_price=None,
        decision_snapshot_id="snap-m5",
        entry_method="ens_member_counting",
        strategy_key="opening_inertia",
        edge_source="test",
        discovery_mode="test",
        token_id=YES_TOKEN,
        no_token_id=f"{YES_TOKEN}-no",
        condition_id="condition-m5",
        order_id=order_id,
        order_status="pending",
        temperature_metric="high",
        order_posted_at=NOW.isoformat(),
        entered_at="",
        env="live",
    )
    from src.state.lifecycle_manager import LifecyclePhase
    events, projection = build_entry_canonical_write(
        position_obj,
        phase_after=LifecyclePhase.PENDING_ENTRY.value,
        decision_id="dec-m5",
        source_module="tests/test_exchange_reconcile",
    )
    append_many_and_project(c, events, projection)


def test_init_schema_creates_exchange_reconcile_findings(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(exchange_reconcile_findings)")}
    assert {
        "finding_id",
        "kind",
        "subject_id",
        "context",
        "evidence_json",
        "recorded_at",
        "resolved_at",
        "resolution",
        "resolved_by",
    } <= cols


def test_day0_chain_confirmed_holding_clears_position_drift_without_journal(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "day0-chain-token"
    seed_position_baseline(conn, position_id="pos-day0-chain", order_id="ord-day0-chain")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'day0_window',
               chain_state = 'synced',
               chain_shares = 5.13,
               shares = 5.13,
               token_id = ?,
               order_id = 'ord-day0-chain',
               updated_at = ?
         WHERE position_id = 'pos-day0-chain'
        """,
        (token, NOW.isoformat()),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="5.13")]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert result == []
    assert findings(conn) == []


def test_exact_trade_split_replaces_point_order_aggregate_without_drift(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_trade_fact as append_venue_trade_fact

    seed_command(
        conn,
        command_id="cmd-split",
        venue_order_id="ord-split",
        state="FILLED",
        token_id=YES_TOKEN,
        size=5.13,
        price=0.72,
    )
    append_venue_trade_fact(
        conn,
        trade_id="trade-split-a",
        venue_order_id="ord-split",
        command_id="cmd-split",
        state="MATCHED",
        filled_size="5.13",
        fill_price="0.72",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"point-order-aggregate").hexdigest(),
        raw_payload_json={
            "proof_class": "point_order_matched_fill",
            "reason": "acked_order_point_order_matched",
            "matched_size": "5.13",
            "trade_id": "trade-split-a",
        },
    )

    exact = trade(
        trade_id="trade-split-a",
        order_id="external-taker",
        size="3.75",
        price="0.28",
        status="CONFIRMED",
        maker_orders=[
            {
                "order_id": "ord-split",
                "matched_amount": "3.75",
                "price": "0.72",
                "side": "BUY",
            }
        ],
    )
    result = run_reconcile_sweep(
        FakeM5Adapter(trades=[exact], positions=[position(token_id=YES_TOKEN, size="3.75")]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=1),
    )

    assert [finding.kind for finding in result] == []
    fact = conn.execute(
        """
        SELECT state, filled_size, fill_price
          FROM venue_trade_facts
         WHERE trade_id = 'trade-split-a'
         ORDER BY local_sequence DESC
         LIMIT 1
        """
    ).fetchone()
    assert dict(fact) == {"state": "CONFIRMED", "filled_size": "3.75", "fill_price": "0.72"}


def test_open_order_at_exchange_absent_locally_becomes_finding_not_command(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    adapter = FakeM5Adapter(open_orders=[order(order_id="ord-ghost", status="LIVE")])
    before = command_count(conn)

    result = run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    assert command_count(conn) == before
    assert len(result) == 1
    row = findings(conn)[0]
    assert row["kind"] == "exchange_ghost_order"
    assert row["subject_id"] == "ord-ghost"
    assert "ord-ghost" in row["evidence_json"]
    assert conn.execute("SELECT COUNT(*) FROM venue_command_events").fetchone()[0] == 0


def test_live_partial_ghost_sell_against_known_position_rebuilds_exit_journal(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "known-no-token"
    seed_command(
        conn,
        command_id="cmd-entry-known",
        venue_order_id="ord-entry-known",
        position_id="pos-known",
        token_id=token,
        side="BUY",
        size=18.682141,
        price=0.72,
        state="FILLED",
        # SCH-W1.2-ORDER-STATE: give the entry a real q_version so the recovered
        # exit's NULL below proves NULL-BY-RULE, not mere absence upstream.
        q_version="entry-q-version-must-not-propagate",
    )
    append_trade_fact(
        conn,
        command_id="cmd-entry-known",
        venue_order_id="ord-entry-known",
        token_id=token,
        trade_id="trade-entry-known",
        size="18.682141",
        fill_price="0.72",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-known", order_id="ord-entry-known")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               token_id = 'opposite-yes-token',
               no_token_id = ?,
               condition_id = 'condition-m5',
               market_id = 'condition-m5',
               direction = 'buy_no',
               shares = 18.682141,
               chain_shares = 18.682141,
               cost_basis_usd = 13.45114152,
               entry_price = 0.72,
               chain_state = 'synced',
               order_status = 'partial',
               updated_at = ?
         WHERE position_id = 'pos-known'
        """,
        (token, NOW.isoformat()),
    )
    ghost_order_id = "ord-live-ghost-sell"

    result = run_reconcile_sweep(
        FakeM5Adapter(
            open_orders=[
                order(
                    order_id=ghost_order_id,
                    status="LIVE",
                    asset_id=token,
                    side="SELL",
                    original_size="18.68",
                    size_matched="5.06",
                    price="0.048",
                    market="condition-m5",
                )
            ],
            trades=[
                trade(
                    trade_id="trade-live-ghost-sell",
                    order_id=ghost_order_id,
                    size="5.06",
                    price="0.048",
                    fill_price="0.048",
                    status="CONFIRMED",
                    asset_id=token,
                    side="BUY",
                    maker_orders=[
                        {
                            "order_id": ghost_order_id,
                            "asset_id": token,
                            "matched_amount": "5.06",
                            "price": "0.048",
                            "side": "SELL",
                        }
                    ],
                )
            ],
            positions=[position(token_id=token, size="13.622141")],
        ),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert not any(f.kind == "exchange_ghost_order" for f in result)
    recovered = conn.execute(
        """
        SELECT command_id, intent_kind, side, state, venue_order_id, token_id, q_version
          FROM venue_commands
         WHERE venue_order_id = ?
        """,
        (ghost_order_id,),
    ).fetchone()
    assert recovered is not None
    assert recovered["command_id"].startswith("recovered_exit:")
    # SCH-W1.2-ORDER-STATE: this row is written by a DIRECT INSERT
    # (exchange_reconcile.py:1152), not insert_command() — q_version is NULL
    # BY RULE ("not Zeus's decision basis"), never inherited from the entry.
    assert recovered["q_version"] is None
    assert dict(recovered) | {"command_id": recovered["command_id"]} == {
        "command_id": recovered["command_id"],
        "intent_kind": "EXIT",
        "side": "SELL",
        "state": "PARTIAL",
        "venue_order_id": ghost_order_id,
        "token_id": token,
        "q_version": None,
    }
    trade_fact = conn.execute(
        """
        SELECT state, filled_size, fill_price
          FROM venue_trade_facts
         WHERE trade_id = 'trade-live-ghost-sell'
        """
    ).fetchone()
    assert dict(trade_fact) == {
        "state": "CONFIRMED",
        "filled_size": "5.06",
        "fill_price": "0.048",
    }
    current = conn.execute(
        """
        SELECT phase, shares, chain_shares, order_id, order_status, exit_reason
          FROM position_current
         WHERE position_id = 'pos-known'
        """
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_id"] == ghost_order_id
    assert current["order_status"] == "sell_pending_confirmation"
    assert current["exit_reason"] == "M5_LIVE_GHOST_SELL_RECOVERY"
    assert abs(float(current["shares"]) - 13.622141) < 0.0001
    assert abs(float(current["chain_shares"]) - 13.622141) < 0.0001
    event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, order_id, command_id
          FROM position_events
         WHERE position_id = 'pos-known'
         ORDER BY sequence_no DESC
         LIMIT 1
        """
    ).fetchone()
    assert event["event_type"] == "EXIT_INTENT"
    assert event["phase_before"] == "voided"
    assert event["phase_after"] == "pending_exit"
    assert event["order_id"] == ghost_order_id
    assert event["command_id"] == recovered["command_id"]
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM exchange_reconcile_findings
         WHERE subject_id = ?
           AND kind = 'exchange_ghost_order'
           AND resolved_at IS NULL
        """,
        (ghost_order_id,),
    ).fetchone()[0] == 0


def test_live_partial_ghost_sell_stays_finding_when_position_conservation_fails(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "mismatched-known-token"
    seed_command(
        conn,
        command_id="cmd-entry-mismatched",
        venue_order_id="ord-entry-mismatched",
        position_id="pos-mismatched",
        token_id=token,
        side="BUY",
        size=18.682141,
        price=0.72,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-entry-mismatched",
        venue_order_id="ord-entry-mismatched",
        token_id=token,
        trade_id="trade-entry-mismatched",
        size="18.682141",
        fill_price="0.72",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-mismatched", order_id="ord-entry-mismatched")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               token_id = ?,
               shares = 18.682141,
               chain_shares = 18.682141,
               entry_price = 0.72,
               chain_state = 'synced',
               updated_at = ?
         WHERE position_id = 'pos-mismatched'
        """,
        (token, NOW.isoformat()),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(
            open_orders=[
                order(
                    order_id="ord-mismatched-ghost-sell",
                    status="LIVE",
                    asset_id=token,
                    side="SELL",
                    original_size="18.68",
                    size_matched="5.06",
                    price="0.048",
                    market="condition-m5",
                )
            ],
            trades=[],
            positions=[position(token_id=token, size="7.0")],
        ),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert any(f.kind == "exchange_ghost_order" for f in result)
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_commands WHERE command_id LIKE 'recovered_exit:%'"
    ).fetchone()[0] == 0


def test_ws_gap_ignores_account_wide_unlinked_trade_noise(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    unrelated = trade(
        trade_id="trade-unrelated-account-wide",
        order_id="ord-not-local",
        size="3.0",
        price="0.42",
        status="CONFIRMED",
    )

    ws_gap_result = run_reconcile_sweep(
        FakeM5Adapter(trades=[unrelated], positions=[]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )
    assert ws_gap_result == []
    assert conn.execute(
        "SELECT COUNT(*) FROM exchange_reconcile_findings WHERE kind='unrecorded_trade'"
    ).fetchone()[0] == 0

    periodic_result = run_reconcile_sweep(
        FakeM5Adapter(trades=[unrelated], positions=[]),
        conn,
        context="periodic",
        observed_at=NOW,
    )
    assert any(f.kind == "unrecorded_trade" for f in periodic_result)


def test_local_RESTING_absent_at_exchange_with_no_trade_marks_canceled_or_wiped_or_suspect(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    for context, expected_kind in [
        ("periodic", "local_orphan_order"),
        ("heartbeat_loss", "heartbeat_suspected_cancel"),
        ("cutover", "cutover_wipe"),
    ]:
        local = sqlite3.connect(":memory:")
        local.row_factory = sqlite3.Row
        from src.state.collateral_ledger import init_collateral_schema
        from src.state.db import init_schema

        init_schema(local)
        init_collateral_schema(local)
        seed_command(local)
        append_resting_order_fact(local)

        result = run_reconcile_sweep(
            FakeM5Adapter(open_orders=[], trades=[]),
            local,
            context=context,  # type: ignore[arg-type]
            observed_at=NOW,
        )

        assert [finding.kind for finding in result] == [expected_kind]
        assert command_count(local) == 1
        assert local.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"
        local.close()


def test_trade_at_exchange_missing_locally_emits_trade_fact_if_order_linkable_else_finding(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)
    adapter = FakeM5Adapter(
        trades=[
            trade(trade_id="trade-linked", order_id="ord-m5", size="5", price="0.51"),
            trade(trade_id="trade-ghost", order_id="ord-unknown", size="3", price="0.52"),
        ]
    )

    run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    trade_rows = conn.execute("SELECT * FROM venue_trade_facts ORDER BY trade_id").fetchall()
    assert [row["trade_id"] for row in trade_rows] == ["trade-linked"]
    assert event_types(conn)[-1] == "PARTIAL_FILL_OBSERVED"
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "PARTIAL"
    row = [row for row in findings(conn) if row["kind"] == "unrecorded_trade"][0]
    assert row["kind"] == "unrecorded_trade"
    assert row["subject_id"] == "trade-ghost"


def test_maker_order_trade_links_to_local_command_and_uses_maker_fill_economics(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=5.17, price=0.37)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    adapter = FakeM5Adapter(
        positions=[position(token_id=YES_TOKEN, size="1.5873")],
        trades=[
            TradeFact(
                raw={
                    "id": "trade-maker-linked",
                    "taker_order_id": "ord-other-taker",
                    "status": "CONFIRMED",
                    "size": "1.5873",
                    "price": "0.63",
                    "transaction_hash": "0xabc",
                    "maker_orders": [
                        {
                            "order_id": "ord-m5",
                            "matched_amount": "1.5873",
                            "price": "0.37",
                            "asset_id": YES_TOKEN,
                            "side": "BUY",
                        }
                    ],
                }
            )
        ]
    )

    run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    row = conn.execute(
        "SELECT * FROM venue_trade_facts WHERE trade_id = 'trade-maker-linked'"
    ).fetchone()
    assert row is not None
    assert row["venue_order_id"] == "ord-m5"
    assert row["state"] == "CONFIRMED"
    assert Decimal(row["filled_size"]) == Decimal("1.5873")
    assert Decimal(row["fill_price"]) == Decimal("0.37")
    assert row["tx_hash"] == "0xabc"
    assert event_types(conn)[-1] == "PARTIAL_FILL_OBSERVED"
    position_event = conn.execute(
        """
        SELECT event_type, order_id, phase_after, source_module
          FROM position_events
         WHERE position_id = 'pos-m5'
         ORDER BY sequence_no DESC
         LIMIT 1
        """
    ).fetchone()
    assert dict(position_event) == {
        "event_type": "ENTRY_ORDER_FILLED",
        "order_id": "ord-m5",
        "phase_after": "active",
        "source_module": "src.execution.exchange_reconcile",
    }
    projection = conn.execute(
        """
        SELECT phase, order_status, shares, entry_price, cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-m5'
        """
    ).fetchone()
    assert projection["phase"] == "active"
    assert projection["order_status"] == "partial"
    assert Decimal(str(projection["shares"])) == Decimal("1.5873")
    assert Decimal(str(projection["entry_price"])) == Decimal("0.37")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("0.587301")

    execution = conn.execute(
        """
        SELECT filled_at, fill_price, shares, venue_status, terminal_exec_status, command_id
          FROM execution_fact
         WHERE intent_id = 'pos-m5:entry'
        """
    ).fetchone()
    assert execution is not None
    assert execution["filled_at"] == NOW.isoformat()
    assert Decimal(str(execution["fill_price"])) == Decimal("0.37")
    assert Decimal(str(execution["shares"])) == Decimal("1.5873")
    assert execution["venue_status"] == "PARTIAL"
    assert execution["terminal_exec_status"] == "partial"
    assert execution["command_id"] == "cmd-m5"

    lot = conn.execute(
        """
        SELECT position_id, state, shares, entry_price_avg, source_trade_fact_id
          FROM position_lots
         ORDER BY lot_id DESC
         LIMIT 1
        """
    ).fetchone()
    assert lot is not None
    assert lot["position_id"] == 7
    assert lot["state"] == "CONFIRMED_EXPOSURE"
    assert Decimal(str(lot["shares"])) == Decimal("1.5873")
    assert Decimal(str(lot["entry_price_avg"])) == Decimal("0.37")
    assert lot["source_trade_fact_id"] == row["trade_fact_id"]

    conn.execute("UPDATE position_current SET order_status = 'filled' WHERE position_id = 'pos-m5'")
    run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)
    projection = conn.execute(
        "SELECT phase, order_status FROM position_current WHERE position_id = 'pos-m5'"
    ).fetchone()
    assert dict(projection) == {"phase": "active", "order_status": "partial"}
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = 'pos-m5'
               AND event_type = 'ENTRY_ORDER_FILLED'
            """
        ).fetchone()[0]
        == 1
    )
    assert findings(conn) == []


def test_maker_fill_materializes_missing_position_projection_after_cancel(conn):
    """A cancel terminalizes the remainder, not the already-filled shares."""

    from src.execution.exchange_reconcile import run_reconcile_sweep

    yes_token = "istanbul-yes-token"
    no_token = f"{yes_token}-no"
    seed_command(
        conn,
        command_id="cmd-missing-projection",
        venue_order_id="ord-missing-projection",
        position_id="pos-missing-projection",
        token_id=no_token,
        size=25.91,
        price=0.54,
        snapshot_token_id=yes_token,
        snapshot_no_token_id=no_token,
        snapshot_selected_token_id=no_token,
        snapshot_outcome_label="NO",
        envelope_yes_token_id=yes_token,
        envelope_no_token_id=no_token,
    )
    conn.execute(
        """
        CREATE TABLE market_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT,
            city TEXT,
            target_date TEXT,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            outcome TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO market_events (
            market_slug, city, target_date, condition_id, token_id, range_label, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "highest-temperature-in-istanbul-on-june-29-2026",
            "Istanbul",
            "2026-06-29",
            "condition-m5",
            yes_token,
            "Will the highest temperature in Istanbul be 29°C on June 29?",
            "Yes",
        ),
    )
    conn.execute(
        """
        UPDATE venue_commands
           SET state = 'CANCELLED'
         WHERE command_id = 'cmd-missing-projection'
        """
    )
    adapter = FakeM5Adapter(
        positions=[position(token_id=no_token, size="10.741738")],
        trades=[
            TradeFact(
                raw={
                    "id": "trade-missing-projection",
                    "status": "CONFIRMED",
                    "size": "10.741738",
                    "price": "0.46",
                    "asset_id": yes_token,
                    "maker_orders": [
                        {
                            "order_id": "ord-missing-projection",
                            "matched_amount": "10.741738",
                            "price": "0.54",
                            "asset_id": no_token,
                            "side": "BUY",
                            "outcome": "No",
                        }
                    ],
                }
            )
        ],
    )

    run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    projection = conn.execute(
        """
        SELECT phase, city, target_date, temperature_metric, direction,
               shares, entry_price, cost_basis_usd, order_status,
               token_id, no_token_id, condition_id
          FROM position_current
         WHERE position_id = 'pos-missing-projection'
        """
    ).fetchone()
    assert projection is not None
    assert projection["phase"] == "active"
    assert projection["city"] == "Istanbul"
    assert projection["target_date"] == "2026-06-29"
    assert projection["temperature_metric"] == "high"
    assert projection["direction"] == "buy_no"
    assert Decimal(str(projection["shares"])) == Decimal("10.741738")
    assert Decimal(str(projection["entry_price"])) == Decimal("0.54")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("5.80053852")
    assert projection["order_status"] == "partial"
    assert projection["token_id"] == yes_token
    assert projection["no_token_id"] == no_token
    assert projection["condition_id"] == "condition-m5"
    assert [
        row["event_type"]
        for row in conn.execute(
            """
            SELECT event_type
              FROM position_events
             WHERE position_id = 'pos-missing-projection'
             ORDER BY sequence_no
            """
        )
    ] == ["POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED", "ENTRY_ORDER_FILLED"]
    assert conn.execute(
        """
        SELECT state
          FROM venue_commands
         WHERE command_id = 'cmd-missing-projection'
        """
    ).fetchone()["state"] == "CANCELLED"


def test_live_tick_repairs_recorded_fill_when_position_projection_is_missing(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.venue_command_repo import append_trade_fact as append

    yes_token = "istanbul-live-tick-yes"
    no_token = f"{yes_token}-no"
    seed_command(
        conn,
        command_id="cmd-live-tick-missing-projection",
        venue_order_id="ord-live-tick-missing-projection",
        position_id="pos-live-tick-missing-projection",
        token_id=no_token,
        size=25.91,
        price=0.54,
        snapshot_token_id=yes_token,
        snapshot_no_token_id=no_token,
        snapshot_selected_token_id=no_token,
        snapshot_outcome_label="NO",
        envelope_yes_token_id=yes_token,
        envelope_no_token_id=no_token,
    )
    conn.execute(
        """
        CREATE TABLE market_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT,
            city TEXT,
            target_date TEXT,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            outcome TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO market_events (
            market_slug, city, target_date, condition_id, token_id, range_label, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "highest-temperature-in-istanbul-on-june-29-2026",
            "Istanbul",
            "2026-06-29",
            "condition-m5",
            yes_token,
            "Will the highest temperature in Istanbul be 29°C on June 29?",
            "Yes",
        ),
    )
    conn.execute(
        """
        UPDATE venue_commands
           SET state = 'CANCELLED'
         WHERE command_id = 'cmd-live-tick-missing-projection'
        """
    )
    raw = {
        "id": "trade-live-tick-missing-projection",
        "status": "CONFIRMED",
        "size": "10.741738",
        "price": "0.46",
        "asset_id": yes_token,
        "maker_orders": [
            {
                "order_id": "ord-live-tick-missing-projection",
                "matched_amount": "10.741738",
                "price": "0.54",
                "asset_id": no_token,
                "side": "BUY",
                "outcome": "No",
            }
        ],
    }
    append(
        conn,
        trade_id="trade-live-tick-missing-projection",
        venue_order_id="ord-live-tick-missing-projection",
        command_id="cmd-live-tick-missing-projection",
        state="CONFIRMED",
        filled_size="10.741738",
        fill_price="0.54",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
        raw_payload_json=raw,
    )

    summary = reconcile_recorded_maker_fill_economics(
        conn,
        observed_at=NOW + timedelta(seconds=1),
        live_tick_scope=True,
    )

    assert summary["scanned"] == 1
    assert summary["projected"] == 1
    projection = conn.execute(
        """
        SELECT phase, city, target_date, temperature_metric, direction,
               shares, entry_price, cost_basis_usd, order_status
          FROM position_current
         WHERE position_id = 'pos-live-tick-missing-projection'
        """
    ).fetchone()
    assert projection is not None
    assert projection["phase"] == "active"
    assert projection["city"] == "Istanbul"
    assert projection["temperature_metric"] == "high"
    assert projection["direction"] == "buy_no"
    assert Decimal(str(projection["shares"])) == Decimal("10.741738")
    assert Decimal(str(projection["entry_price"])) == Decimal("0.54")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("5.80053852")
    assert projection["order_status"] == "partial"


def test_live_tick_maker_fill_repair_is_bounded_to_missing_entry_projection(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.venue_command_repo import append_trade_fact as append

    def append_maker_fact(
        *,
        command_id: str,
        venue_order_id: str,
        trade_id: str,
        token_id: str,
        size: str = "4.2",
        price: str = "0.54",
    ) -> None:
        raw = {
            "id": trade_id,
            "status": "CONFIRMED",
            "size": size,
            "price": str(Decimal("1") - Decimal(price)),
            "asset_id": f"{token_id}-other-side",
            "maker_orders": [
                {
                    "order_id": venue_order_id,
                    "matched_amount": size,
                    "price": price,
                    "asset_id": token_id,
                    "side": "BUY",
                }
            ],
        }
        append(
            conn,
            trade_id=trade_id,
            venue_order_id=venue_order_id,
            command_id=command_id,
            state="CONFIRMED",
            filled_size=size,
            fill_price=price,
            source="REST",
            observed_at=NOW,
            raw_payload_hash=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
            raw_payload_json=raw,
        )

    for idx, phase in enumerate(("voided", "settled", "economically_closed")):
        token = f"history-maker-token-{idx}"
        command_id = f"cmd-history-maker-{idx}"
        order_id = f"ord-history-maker-{idx}"
        position_id = f"pos-history-maker-{idx}"
        seed_command(
            conn,
            command_id=command_id,
            venue_order_id=order_id,
            position_id=position_id,
            token_id=token,
            state="CANCELLED",
        )
        seed_position_baseline(conn, position_id=position_id, order_id=order_id)
        conn.execute(
            """
            UPDATE position_current
               SET phase = ?,
                   market_id = ?,
                   condition_id = ?
             WHERE position_id = ?
            """,
            (phase, f"history-condition-{idx}", f"history-condition-{idx}", position_id),
        )
        append_maker_fact(
            command_id=command_id,
            venue_order_id=order_id,
            trade_id=f"trade-history-maker-{idx}",
            token_id=token,
        )

    yes_token = "live-bounded-yes-token"
    no_token = f"{yes_token}-no"
    seed_command(
        conn,
        command_id="cmd-live-bounded-missing",
        venue_order_id="ord-live-bounded-missing",
        position_id="pos-live-bounded-missing",
        token_id=no_token,
        state="CANCELLED",
        snapshot_token_id=yes_token,
        snapshot_no_token_id=no_token,
        snapshot_selected_token_id=no_token,
        snapshot_outcome_label="NO",
        envelope_yes_token_id=yes_token,
        envelope_no_token_id=no_token,
    )
    conn.execute(
        """
        CREATE TABLE market_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT,
            city TEXT,
            target_date TEXT,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            outcome TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO market_events (
            market_slug, city, target_date, condition_id, token_id, range_label, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "highest-temperature-in-istanbul-on-june-29-2026",
            "Istanbul",
            "2026-06-29",
            "condition-m5",
            yes_token,
            "Will the highest temperature in Istanbul be 29°C on June 29?",
            "Yes",
        ),
    )
    append_maker_fact(
        command_id="cmd-live-bounded-missing",
        venue_order_id="ord-live-bounded-missing",
        trade_id="trade-live-bounded-missing",
        token_id=no_token,
    )

    summary = reconcile_recorded_maker_fill_economics(
        conn,
        observed_at=NOW + timedelta(seconds=1),
        live_tick_scope=True,
    )

    assert summary["scanned"] == 1
    assert summary["projected"] == 1
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_current
         WHERE position_id LIKE 'pos-history-maker-%'
           AND phase IN ('voided', 'settled', 'economically_closed')
        """
    ).fetchone()[0] == 3
    projection = conn.execute(
        "SELECT phase, city, direction, shares FROM position_current WHERE position_id = 'pos-live-bounded-missing'"
    ).fetchone()
    assert projection is not None
    assert projection["phase"] == "active"
    assert projection["city"] == "Istanbul"
    assert projection["direction"] == "buy_no"
    assert Decimal(str(projection["shares"])) == Decimal("4.2")


def test_maker_fill_economics_repair_uses_canonical_trade_fact_over_later_weaker_fact(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.venue_command_repo import append_trade_fact as append

    seed_command(conn, size=5.17, price=0.37)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    raw = {
        "id": "trade-maker-late-weaker",
        "taker_order_id": "ord-other-taker",
        "status": "CONFIRMED",
        "size": "1.5873",
        "price": "0.63",
        "transaction_hash": "0xabc",
        "maker_orders": [
            {
                "order_id": "ord-m5",
                "matched_amount": "1.5873",
                "price": "0.37",
                "asset_id": YES_TOKEN,
                "side": "BUY",
            }
        ],
    }
    append(
        conn,
        trade_id="trade-maker-late-weaker",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="1.5873",
        fill_price="0.63",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
        raw_payload_json=raw,
    )
    append(
        conn,
        trade_id="trade-maker-late-weaker",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="FAILED",
        filled_size="0",
        fill_price="0",
        source="REST",
        observed_at=NOW + timedelta(seconds=1),
        raw_payload_hash=hashlib.sha256(b"late-weaker-maker-fill-failed").hexdigest(),
        raw_payload_json={"id": "trade-maker-late-weaker", "status": "FAILED"},
    )

    summary = reconcile_recorded_maker_fill_economics(conn, observed_at=NOW + timedelta(seconds=2))

    assert summary["scanned"] == 1
    assert summary["corrected"] == 1
    assert summary["projected"] == 1
    projection = conn.execute(
        "SELECT phase, order_status, shares, entry_price, cost_basis_usd FROM position_current WHERE position_id = 'pos-m5'"
    ).fetchone()
    assert projection["phase"] == "active"
    assert projection["order_status"] == "partial"
    assert Decimal(str(projection["shares"])) == Decimal("1.5873")
    assert Decimal(str(projection["entry_price"])) == Decimal("0.37")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("0.587301")
    execution = conn.execute(
        "SELECT fill_price, shares, terminal_exec_status, command_id FROM execution_fact WHERE intent_id = 'pos-m5:entry'"
    ).fetchone()
    assert dict(execution) == {
        "fill_price": 0.37,
        "shares": 1.5873,
        "terminal_exec_status": "partial",
        "command_id": "cmd-m5",
    }


def test_live_tick_maker_fill_repair_skips_downstream_entry_positions(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.venue_command_repo import append_trade_fact as append

    seed_command(conn, size=5.17, price=0.37)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    conn.execute(
        "UPDATE position_current SET phase = 'settled' WHERE position_id = 'pos-m5'"
    )
    raw = {
        "id": "trade-maker-settled",
        "taker_order_id": "ord-other-taker",
        "status": "CONFIRMED",
        "size": "1.5873",
        "price": "0.63",
        "transaction_hash": "0xabc",
        "maker_orders": [
            {
                "order_id": "ord-m5",
                "matched_amount": "1.5873",
                "price": "0.37",
                "asset_id": YES_TOKEN,
                "side": "BUY",
            }
        ],
    }
    append(
        conn,
        trade_id="trade-maker-settled",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="1.5873",
        fill_price="0.63",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
        raw_payload_json=raw,
    )

    live_tick = reconcile_recorded_maker_fill_economics(
        conn,
        observed_at=NOW + timedelta(seconds=1),
        live_tick_scope=True,
    )
    full_sweep = reconcile_recorded_maker_fill_economics(
        conn,
        observed_at=NOW + timedelta(seconds=2),
    )

    assert live_tick["scanned"] == 0
    assert live_tick["corrected"] == 0
    assert full_sweep["scanned"] == 1
    assert full_sweep["corrected"] == 1


def test_maker_fill_projection_uses_canonical_position_when_old_command_position_voided(conn):
    """A repaired maker fill must not reopen an old voided command position.

    Regression for the 2026-06-17 Houston repair loop: the command still pointed
    at a voided short id, while the same order/token had a canonical open EDLI
    position. The repair must project against the canonical position_current row.
    """
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.venue_command_repo import append_trade_fact as append

    shared_order = "ord-houston-shared"
    no_token = "houston-no-token"
    seed_command(
        conn,
        command_id="cmd-old-voided",
        venue_order_id=shared_order,
        position_id="pos-old-voided",
        token_id=no_token,
        size=5.078125,
        price=0.64,
    )
    seed_position_baseline(conn, position_id="pos-old-voided", order_id=shared_order)
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               direction = 'buy_no',
               token_id = '',
               no_token_id = ?,
               order_status = 'partial',
               shares = 0,
               entry_price = 0,
               updated_at = ?
         WHERE position_id = 'pos-old-voided'
        """,
        (no_token, (NOW + timedelta(minutes=1)).isoformat()),
    )
    seed_position_baseline(conn, position_id="pos-canonical-open", order_id=shared_order)
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'day0_window',
               direction = 'buy_no',
               token_id = '',
               no_token_id = ?,
               order_status = 'filled',
               shares = 5.07,
               entry_price = 0.64,
               updated_at = ?
         WHERE position_id = 'pos-canonical-open'
        """,
        (no_token, (NOW + timedelta(minutes=2)).isoformat()),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, decision_id,
            snapshot_id, order_id, command_id, caused_by, idempotency_key,
            venue_status, source_module, payload_json, env
        )
        VALUES (?, ?, 1, 3, 'ENTRY_ORDER_FILLED', ?, 'active', 'active',
                'opening_inertia', 'dec-canonical', 'snap-m5', ?, ?,
                NULL, ?, 'FILLED', 'tests/test_exchange_reconcile', '{}', 'live')
        """,
        (
            "evt-canonical-entry-filled",
            "pos-canonical-open",
            (NOW + timedelta(minutes=3)).isoformat(),
            shared_order,
            "cmd-old-voided",
            "idem-canonical-entry-filled",
        ),
    )
    raw = {
        "id": "trade-houston-maker",
        "taker_order_id": "ord-taker",
        "status": "CONFIRMED",
        "size": "5.07",
        "price": "0.36",
        "transaction_hash": "0xabc",
        "maker_orders": [
            {
                "order_id": shared_order,
                "matched_amount": "5.07",
                "price": "0.64",
                "asset_id": no_token,
                "side": "BUY",
            }
        ],
    }
    append(
        conn,
        trade_id="trade-houston-maker",
        venue_order_id=shared_order,
        command_id="cmd-old-voided",
        state="CONFIRMED",
        filled_size="5.07",
        fill_price="0.36",
        source="REST",
        observed_at=NOW + timedelta(minutes=4),
        raw_payload_hash=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
        raw_payload_json=raw,
    )

    summary = reconcile_recorded_maker_fill_economics(
        conn,
        observed_at=NOW + timedelta(minutes=5),
    )

    assert summary["errors"] == 0
    assert summary["projected"] == 1
    old_row = conn.execute(
        "SELECT phase, shares FROM position_current WHERE position_id = 'pos-old-voided'"
    ).fetchone()
    assert dict(old_row) == {"phase": "voided", "shares": 0.0}
    canonical = conn.execute(
        """
        SELECT phase, order_status, shares, entry_price
          FROM position_current
         WHERE position_id = 'pos-canonical-open'
        """
    ).fetchone()
    assert dict(canonical) == {
        "phase": "day0_window",
        "order_status": "partial",
        "shares": 5.07,
        "entry_price": 0.64,
    }
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = 'pos-old-voided'
               AND event_type = 'ENTRY_ORDER_FILLED'
            """
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = 'pos-canonical-open'
               AND event_type = 'ENTRY_ORDER_FILLED'
            """
        ).fetchone()[0]
        == 1
    )


def test_entry_fill_economics_uses_canonical_trade_fact_over_later_weaker_fact(conn):
    from src.execution.exchange_reconcile import _entry_fill_economics_for_command

    seed_command(conn)
    append_trade_fact(
        conn,
        trade_id="trade-entry-canonical",
        size="2.5",
        fill_price="0.40",
        state="CONFIRMED",
    )
    append_trade_fact(
        conn,
        trade_id="trade-entry-canonical",
        size="0",
        fill_price="0.40",
        state="FAILED",
    )

    assert _entry_fill_economics_for_command(
        conn,
        command_id="cmd-m5",
        fallback_filled_size="1",
        fallback_fill_price="0.99",
    ) == (Decimal("2.5"), Decimal("0.40"), Decimal("1.000"))


def test_exit_fill_economics_uses_canonical_trade_fact_over_later_weaker_fact(conn):
    from src.execution.exchange_reconcile import _exit_fill_economics_for_command

    seed_command(
        conn,
        command_id="cmd-exit-canonical",
        venue_order_id="ord-exit-canonical",
        side="SELL",
        position_id="pos-exit-canonical",
    )
    append_trade_fact(
        conn,
        command_id="cmd-exit-canonical",
        venue_order_id="ord-exit-canonical",
        trade_id="trade-exit-canonical",
        size="3",
        fill_price="0.25",
        state="CONFIRMED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-exit-canonical",
        venue_order_id="ord-exit-canonical",
        trade_id="trade-exit-canonical",
        size="0",
        fill_price="0.25",
        state="FAILED",
    )

    assert _exit_fill_economics_for_command(
        conn,
        command_id="cmd-exit-canonical",
        fallback_filled_size="1",
        fallback_fill_price="0.99",
    ) == (Decimal("3"), Decimal("0.25"))


def test_exit_fill_economics_keeps_distinct_child_trades_in_one_transaction(conn):
    from src.execution.exchange_reconcile import _exit_fill_economics_for_command

    tx_hash = "0xshared-child-fill-transaction"
    seed_command(
        conn,
        command_id="cmd-exit-shared-tx",
        venue_order_id="ord-exit-shared-tx",
        side="SELL",
        position_id="pos-exit-shared-tx",
    )
    append_trade_fact(
        conn,
        command_id="cmd-exit-shared-tx",
        venue_order_id="ord-exit-shared-tx",
        trade_id="trade-child-a",
        size="3",
        fill_price="0.20",
        tx_hash=tx_hash,
    )
    append_trade_fact(
        conn,
        command_id="cmd-exit-shared-tx",
        venue_order_id="ord-exit-shared-tx",
        trade_id="trade-child-b",
        size="2",
        fill_price="0.30",
        tx_hash=tx_hash,
    )

    assert _exit_fill_economics_for_command(
        conn,
        command_id="cmd-exit-shared-tx",
        fallback_filled_size="1",
        fallback_fill_price="0.99",
    ) == (Decimal("5"), Decimal("0.24"))


def test_entry_fill_projection_aggregates_multiple_trade_facts(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10, price=0.50)
    seed_position_baseline(conn)

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-partial-a",
                    order_id="ord-m5",
                    size="3",
                    price="0.20",
                    status="CONFIRMED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )
    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-partial-b",
                    order_id="ord-m5",
                    size="4",
                    price="0.40",
                    status="CONFIRMED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    projection = conn.execute(
        """
        SELECT phase, order_status, shares, entry_price, cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-m5'
        """
    ).fetchone()
    assert projection["phase"] == "active"
    assert projection["order_status"] == "partial"
    assert Decimal(str(projection["shares"])) == Decimal("7")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("2.20")
    assert float(projection["entry_price"]) == pytest.approx(float(Decimal("2.20") / Decimal("7")))

    execution = conn.execute(
        """
        SELECT fill_price, shares, terminal_exec_status, command_id
          FROM execution_fact
         WHERE intent_id = 'pos-m5:entry'
        """
    ).fetchone()
    assert Decimal(str(execution["shares"])) == Decimal("7")
    assert Decimal(str(execution["fill_price"])) == Decimal("0.3142857142857143")
    assert execution["terminal_exec_status"] == "partial"
    assert execution["command_id"] == "cmd-m5"


def test_recorded_maker_fill_economic_drift_appends_correction_and_reprojects(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.venue_command_repo import append_trade_fact as append_venue_trade_fact

    seed_command(conn, state="FILLED", size=181.16, price=0.01)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    raw = {
        "id": "trade-maker-drift",
        "status": "CONFIRMED",
        "taker_order_id": "foreign-taker",
        "size": "100",
        "price": "0.99",
        "transaction_hash": "0xmakerdrift",
        "maker_orders": [
            {
                "order_id": "ord-m5",
                "matched_amount": "100",
                "price": "0.01",
                "asset_id": YES_TOKEN,
                "side": "BUY",
            }
        ],
    }
    bad_fact_id = append_venue_trade_fact(
        conn,
        trade_id="trade-maker-drift",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="100",
        fill_price="0.99",
        source="WS_USER",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"bad-maker-drift").hexdigest(),
        raw_payload_json=raw,
    )

    summary = reconcile_recorded_maker_fill_economics(conn, observed_at=NOW + timedelta(seconds=5))

    assert summary == {"scanned": 1, "corrected": 1, "projected": 1, "stayed": 0, "errors": 0}
    rows = conn.execute(
        """
        SELECT trade_fact_id, local_sequence, filled_size, fill_price, raw_payload_json
          FROM venue_trade_facts
         WHERE trade_id = 'trade-maker-drift'
         ORDER BY local_sequence
        """
    ).fetchall()
    assert [(r["local_sequence"], r["filled_size"], r["fill_price"]) for r in rows] == [
        (1, "100", "0.99"),
        (2, "100", "0.01"),
    ]
    repair_payload = rows[-1]["raw_payload_json"]
    assert "maker_leg_economics_selected_for_command_order" in repair_payload
    assert str(bad_fact_id) in repair_payload

    projection = conn.execute(
        """
        SELECT phase, order_status, shares, entry_price, cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-m5'
        """
    ).fetchone()
    assert projection["phase"] == "active"
    assert projection["order_status"] == "partial"
    assert Decimal(str(projection["shares"])) == Decimal("100")
    order_fact = conn.execute(
        """
        SELECT state, remaining_size, matched_size, source
          FROM venue_order_facts
         WHERE command_id = 'cmd-m5'
         ORDER BY local_sequence DESC
         LIMIT 1
        """
    ).fetchone()
    assert dict(order_fact) == {
        "state": "PARTIALLY_MATCHED",
        "remaining_size": "81.16",
        "matched_size": "100",
        "source": "WS_USER",
    }
    assert Decimal(str(projection["entry_price"])) == Decimal("0.01")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("1.00")
    execution = conn.execute(
        """
        SELECT command_id, shares, fill_price, terminal_exec_status
          FROM execution_fact
         WHERE intent_id = 'pos-m5:entry'
        """
    ).fetchone()
    assert execution is not None
    assert dict(execution) == {
        "command_id": "cmd-m5",
        "shares": 100.0,
        "fill_price": 0.01,
        "terminal_exec_status": "partial",
    }

    lot = conn.execute(
        """
        SELECT state, shares, entry_price_avg, source_trade_fact_id
          FROM position_lots
         ORDER BY lot_id DESC
         LIMIT 1
        """
    ).fetchone()
    assert lot["state"] == "CONFIRMED_EXPOSURE"
    assert Decimal(str(lot["shares"])) == Decimal("100")
    assert Decimal(str(lot["entry_price_avg"])) == Decimal("0.01")
    assert lot["source_trade_fact_id"] == rows[-1]["trade_fact_id"]


def test_local_order_open_uses_canonical_order_truth_over_later_weaker_fact(conn):
    """Relationship C: weaker later facts cannot reopen terminal order truth."""
    from src.execution.exchange_reconcile import _local_order_is_open
    from src.state.venue_command_repo import append_order_fact

    seed_command(conn, state="ACKED", size=5.0, price=0.34)
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="MATCHED",
        remaining_size="0",
        matched_size="5",
        source="REST",
        observed_at=NOW.isoformat(),
        raw_payload_hash=hashlib.sha256(b"terminal-filled").hexdigest(),
        raw_payload_json={"proof": "terminal-filled"},
    )
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="RESTING",
        remaining_size="0.01",
        matched_size="4.99",
        source="REST",
        observed_at=(NOW + timedelta(seconds=5)).isoformat(),
        raw_payload_hash=hashlib.sha256(b"later-weak-resting").hexdigest(),
        raw_payload_json={"proof": "later-weak-resting"},
    )
    command = conn.execute(
        "SELECT * FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()

    assert _local_order_is_open(conn, dict(command)) is False


def test_entry_fill_covers_command_uses_canonical_order_truth_over_later_weaker_fact(conn):
    """Relationship: later weak order facts cannot demote filled entry truth."""
    from src.execution.exchange_reconcile import _entry_fill_covers_command
    from src.state.venue_command_repo import append_order_fact

    seed_command(conn, state="ACKED", size=5.0, price=0.34)
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="MATCHED",
        remaining_size="0",
        matched_size="4.99",
        source="REST",
        observed_at=NOW.isoformat(),
        raw_payload_hash=hashlib.sha256(b"terminal-fill-normalized").hexdigest(),
        raw_payload_json={"proof": "terminal-fill-normalized"},
    )
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="RESTING",
        remaining_size="0.01",
        matched_size="4.99",
        source="REST",
        observed_at=(NOW + timedelta(seconds=5)).isoformat(),
        raw_payload_hash=hashlib.sha256(b"later-weak-entry-fill").hexdigest(),
        raw_payload_json={"proof": "later-weak-entry-fill"},
    )
    command = conn.execute(
        "SELECT * FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()

    assert _entry_fill_covers_command(conn, dict(command), Decimal("4.99")) is True


def test_failed_or_retrying_trade_fact_does_not_advance_command_fill_state(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    run_reconcile_sweep(
        FakeM5Adapter(trades=[trade(trade_id="trade-failed", order_id="ord-m5", size="10", status="FAILED")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert conn.execute("SELECT state FROM venue_trade_facts WHERE trade_id = 'trade-failed'").fetchone()["state"] == "FAILED"
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"
    assert "FILL_CONFIRMED" not in event_types(conn)

    run_reconcile_sweep(
        FakeM5Adapter(trades=[trade(trade_id="trade-retrying", order_id="ord-m5", size="5", status="RETRYING")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert conn.execute("SELECT state FROM venue_trade_facts WHERE trade_id = 'trade-retrying'").fetchone()["state"] == "RETRYING"
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"
    assert "PARTIAL_FILL_OBSERVED" not in event_types(conn)


def test_failed_trade_fact_rolls_back_existing_optimistic_lot(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_position_lot
    from src.state.venue_command_repo import append_trade_fact as append_venue_trade_fact

    seed_command(conn, size=10, position_id="123456789091")
    matched_fact_id = append_venue_trade_fact(
        conn,
        trade_id="trade-reconcile-failed",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="MATCHED",
        filled_size="7.5",
        fill_price="0.50",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"matched-reconcile-failed").hexdigest(),
        raw_payload_json={"status": "MATCHED"},
    )
    append_position_lot(
        conn,
        position_id=123456789091,
        state="OPTIMISTIC_EXPOSURE",
        shares="7.5",
        entry_price_avg="0.50",
        source_command_id="cmd-m5",
        source_trade_fact_id=matched_fact_id,
        captured_at=NOW,
        state_changed_at=NOW,
        source="REST",
        observed_at=NOW,
        raw_payload_json={"status": "MATCHED"},
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                TradeFact(
                    raw={
                        "id": "trade-reconcile-failed",
                        "trade_id": "trade-reconcile-failed",
                        "orderID": "ord-m5",
                        "order_id": "ord-m5",
                        "status": "FAILED",
                    }
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=5),
    )

    trade_rows = conn.execute(
        """
        SELECT trade_fact_id, state, filled_size, fill_price
          FROM venue_trade_facts
         WHERE trade_id = 'trade-reconcile-failed'
         ORDER BY local_sequence
        """
    ).fetchall()
    lot_rows = conn.execute(
        """
        SELECT state, shares, source_trade_fact_id
          FROM position_lots
         WHERE position_id = 123456789091
         ORDER BY lot_id
        """
    ).fetchall()

    assert [(r["state"], r["filled_size"], r["fill_price"]) for r in trade_rows] == [
        ("MATCHED", "7.5", "0.50"),
        ("FAILED", "0", "0"),
    ]
    assert [(r["state"], r["shares"]) for r in lot_rows] == [
        ("OPTIMISTIC_EXPOSURE", "7.5"),
        ("QUARANTINED", "7.5"),
    ]
    assert lot_rows[-1]["source_trade_fact_id"] == trade_rows[-1]["trade_fact_id"]
    assert "FILL_CONFIRMED" not in event_types(conn)


@pytest.mark.parametrize("status", ["MATCHED", "MINED"])
def test_nonconfirmed_full_size_trade_is_optimistic_exposure_not_fill_finality(conn, status):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    run_reconcile_sweep(
        FakeM5Adapter(trades=[trade(trade_id=f"trade-{status.lower()}", order_id="ord-m5", size="10", status=status)]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert conn.execute(
        "SELECT state FROM venue_trade_facts WHERE trade_id = ?",
        (f"trade-{status.lower()}",),
    ).fetchone()["state"] == status
    assert event_types(conn)[-1] == "PARTIAL_FILL_OBSERVED"
    assert "FILL_CONFIRMED" not in event_types(conn)
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "PARTIAL"


def test_confirmed_full_size_trade_is_required_for_fill_finality(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    run_reconcile_sweep(
        FakeM5Adapter(trades=[trade(trade_id="trade-confirmed", order_id="ord-m5", size="10", status="CONFIRMED")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert conn.execute("SELECT state FROM venue_trade_facts WHERE trade_id = 'trade-confirmed'").fetchone()["state"] == "CONFIRMED"
    assert event_types(conn)[-1] == "FILL_CONFIRMED"
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "FILLED"


def test_confirmed_exit_trade_economically_closes_active_position_projection(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "exit-confirmed-token"
    seed_position_baseline(conn, position_id="pos-exit-confirmed", order_id="ord-entry-exit-confirmed")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'active',
               token_id = ?,
               order_id = 'ord-entry-exit-confirmed',
               order_status = 'filled',
               shares = 35.6,
               chain_shares = 35.6,
               chain_avg_price = 0.15,
               chain_cost_basis_usd = 5.34,
               cost_basis_usd = 5.34,
               entry_price = 0.15,
               updated_at = ?
         WHERE position_id = 'pos-exit-confirmed'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-exit-confirmed",
        venue_order_id="ord-exit-confirmed",
        position_id="pos-exit-confirmed",
        token_id=token,
        side="SELL",
        size=35.6,
        price=0.13,
        state="ACKED",
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-exit-confirmed",
                    order_id="ord-exit-confirmed",
                    size="35.6",
                    price="0.14",
                    status="CONFIRMED",
                )
            ],
            positions=[position(token_id=token, size="0")],
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert result == []
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-exit-confirmed'"
    ).fetchone()["state"] == "FILLED"
    projection = conn.execute(
        """
        SELECT phase, order_id, order_status, shares, chain_shares,
               chain_avg_price, chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-exit-confirmed'
        """
    ).fetchone()
    assert dict(projection) == {
        "phase": "economically_closed",
        "order_id": "ord-entry-exit-confirmed",
        "order_status": "sell_filled",
        "shares": 35.6,
        "chain_shares": 0.0,
        "chain_avg_price": 0.0,
        "chain_cost_basis_usd": 0.0,
    }
    event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, order_id
          FROM position_events
         WHERE position_id = 'pos-exit-confirmed'
           AND event_type = 'EXIT_ORDER_FILLED'
        """
    ).fetchone()
    assert dict(event) == {
        "event_type": "EXIT_ORDER_FILLED",
        "phase_before": "pending_exit",
        "phase_after": "economically_closed",
        "order_id": "ord-exit-confirmed",
    }
    fact = conn.execute(
        """
        SELECT filled_at, fill_price, shares, venue_status, terminal_exec_status, command_id
          FROM execution_fact
         WHERE intent_id = 'pos-exit-confirmed:exit'
        """
    ).fetchone()
    assert dict(fact) == {
        "filled_at": NOW.isoformat(),
        "fill_price": 0.14,
        "shares": 35.6,
        "venue_status": "FILLED",
        "terminal_exec_status": "filled",
        "command_id": "cmd-exit-confirmed",
    }


def test_existing_confirmed_exit_trade_repairs_missing_economic_close_projection(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "exit-existing-confirmed-token"
    seed_position_baseline(conn, position_id="pos-exit-existing-confirmed", order_id="ord-entry-existing-exit")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'active',
               token_id = ?,
               order_id = 'ord-entry-existing-exit',
               order_status = 'filled',
               shares = 35.6,
               chain_shares = 35.6,
               chain_avg_price = 0.15,
               chain_cost_basis_usd = 5.34,
               cost_basis_usd = 5.34,
               entry_price = 0.15,
               updated_at = ?
         WHERE position_id = 'pos-exit-existing-confirmed'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-existing-exit-confirmed",
        venue_order_id="ord-existing-exit-confirmed",
        position_id="pos-exit-existing-confirmed",
        token_id=token,
        side="SELL",
        size=35.6,
        price=0.13,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-existing-exit-confirmed",
        venue_order_id="ord-existing-exit-confirmed",
        token_id=token,
        trade_id="trade-existing-exit-confirmed",
        size="35.6",
        state="CONFIRMED",
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-existing-exit-confirmed",
                    order_id="ord-existing-exit-confirmed",
                    size="35.6",
                    price="0.50",
                    status="CONFIRMED",
                )
            ],
            positions=[position(token_id=token, size="0")],
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert result == []
    assert conn.execute(
        """
        SELECT phase
          FROM position_current
         WHERE position_id = 'pos-exit-existing-confirmed'
        """
    ).fetchone()["phase"] == "economically_closed"
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = 'pos-exit-existing-confirmed'
           AND event_type = 'EXIT_ORDER_FILLED'
           AND order_id = 'ord-existing-exit-confirmed'
        """
    ).fetchone()[0] == 1


def test_recorded_confirmed_exit_trade_repair_hook_economically_closes_projection(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics

    token = "exit-recorded-confirmed-token"
    seed_position_baseline(conn, position_id="pos-exit-recorded-confirmed", order_id="ord-entry-recorded-exit")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'active',
               token_id = ?,
               order_id = 'ord-entry-recorded-exit',
               order_status = 'filled',
               shares = 35.6,
               cost_basis_usd = 5.34,
               entry_price = 0.15,
               updated_at = ?
         WHERE position_id = 'pos-exit-recorded-confirmed'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-recorded-exit-confirmed",
        venue_order_id="ord-recorded-exit-confirmed",
        position_id="pos-exit-recorded-confirmed",
        token_id=token,
        side="SELL",
        size=35.6,
        price=0.13,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-recorded-exit-confirmed",
        venue_order_id="ord-recorded-exit-confirmed",
        token_id=token,
        trade_id="trade-recorded-exit-confirmed",
        size="35.6",
        state="CONFIRMED",
    )

    summary = reconcile_recorded_maker_fill_economics(conn, observed_at=NOW)

    assert summary["exit_projected"] == 1
    current = conn.execute(
        """
        SELECT phase, order_status, shares, chain_shares,
               chain_avg_price, chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-exit-recorded-confirmed'
        """
    ).fetchone()
    assert dict(current) == {
        "phase": "economically_closed",
        "order_status": "sell_filled",
        "shares": 35.6,
        "chain_shares": 0.0,
        "chain_avg_price": 0.0,
        "chain_cost_basis_usd": 0.0,
    }
    fact = conn.execute(
        """
        SELECT filled_at, fill_price, shares, venue_status, terminal_exec_status, command_id
          FROM execution_fact
         WHERE intent_id = 'pos-exit-recorded-confirmed:exit'
        """
    ).fetchone()
    assert dict(fact) == {
        "filled_at": NOW.isoformat(),
        "fill_price": 0.5,
        "shares": 35.6,
        "venue_status": "FILLED",
        "terminal_exec_status": "filled",
        "command_id": "cmd-recorded-exit-confirmed",
    }


def test_recorded_confirmed_exit_trade_economically_closes_quarantined_projection(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics

    token = "exit-quarantined-confirmed-token"
    seed_position_baseline(conn, position_id="pos-exit-quarantined-confirmed", order_id="ord-entry-quarantine")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'quarantined',
               chain_state = 'size_mismatch_unresolved',
               token_id = ?,
               order_id = 'ord-entry-quarantine',
               order_status = 'filled',
               shares = 11.09,
               chain_shares = 11.09,
               cost_basis_usd = 6.10,
               chain_cost_basis_usd = 6.10,
               entry_price = 0.55,
               updated_at = ?
         WHERE position_id = 'pos-exit-quarantined-confirmed'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-quarantined-exit-confirmed",
        venue_order_id="ord-quarantined-exit-confirmed",
        position_id="pos-exit-quarantined-confirmed",
        token_id=token,
        side="SELL",
        size=11.09,
        price=0.53,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-quarantined-exit-confirmed",
        venue_order_id="ord-quarantined-exit-confirmed",
        token_id=token,
        trade_id="trade-quarantined-exit-confirmed",
        size="11.09",
        fill_price="0.54",
        state="CONFIRMED",
    )

    summary = reconcile_recorded_maker_fill_economics(conn, observed_at=NOW)

    assert summary["exit_projected"] == 1
    projection = conn.execute(
        """
        SELECT phase, order_status, exit_reason, shares, chain_shares,
               chain_avg_price, chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-exit-quarantined-confirmed'
        """
    ).fetchone()
    assert dict(projection) == {
        "phase": "economically_closed",
        "order_status": "sell_filled",
        "exit_reason": "M5_EXCHANGE_RECONCILE",
        "shares": 11.09,
        "chain_shares": 0.0,
        "chain_avg_price": 0.0,
        "chain_cost_basis_usd": 0.0,
    }
    event = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, order_id, command_id
          FROM position_events
         WHERE position_id = 'pos-exit-quarantined-confirmed'
           AND event_type = 'EXIT_ORDER_FILLED'
        """
    ).fetchone()
    assert dict(event) == {
        "event_type": "EXIT_ORDER_FILLED",
        "phase_before": "pending_exit",
        "phase_after": "economically_closed",
        "order_id": "ord-quarantined-exit-confirmed",
        "command_id": "cmd-quarantined-exit-confirmed",
    }


def test_recorded_confirmed_exit_trade_preserves_strategy_exit_reason(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics

    token = "exit-strategy-reason-token"
    strategy_reason = "DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES (entry=0.1218, current=0.0000)"
    seed_position_baseline(conn, position_id="pos-exit-strategy-reason", order_id="ord-entry-strategy")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               chain_state = 'synced',
               token_id = ?,
               order_id = 'ord-entry-strategy',
               order_status = 'retry_pending',
               shares = 10.01,
               chain_shares = 10.01,
               cost_basis_usd = 0.31,
               chain_cost_basis_usd = 0.31,
               entry_price = 0.031,
               exit_reason = ?,
               updated_at = ?
         WHERE position_id = 'pos-exit-strategy-reason'
        """,
        (token, strategy_reason, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-strategy-exit-confirmed",
        venue_order_id="ord-strategy-exit-confirmed",
        position_id="pos-exit-strategy-reason",
        token_id=token,
        side="SELL",
        size=10.01,
        price=0.01,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-strategy-exit-confirmed",
        venue_order_id="ord-strategy-exit-confirmed",
        token_id=token,
        trade_id="trade-strategy-exit-confirmed",
        size="10.01",
        fill_price="0.01",
        state="CONFIRMED",
    )

    summary = reconcile_recorded_maker_fill_economics(conn, observed_at=NOW)

    assert summary["exit_projected"] == 1
    projection = conn.execute(
        """
        SELECT phase, order_status, exit_reason
          FROM position_current
         WHERE position_id = 'pos-exit-strategy-reason'
        """
    ).fetchone()
    assert dict(projection) == {
        "phase": "economically_closed",
        "order_status": "sell_filled",
        "exit_reason": strategy_reason,
    }


def test_recorded_nonfinal_full_exit_trade_terminalizes_command_without_economic_close(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.venue_command_repo import append_order_fact

    token = "exit-recorded-matched-token"
    seed_position_baseline(conn, position_id="pos-exit-recorded-matched", order_id="ord-entry-recorded-matched")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               order_id = 'ord-exit-recorded-matched',
               order_status = 'sell_pending_confirmation',
               shares = 15.5,
               cost_basis_usd = 10.8425,
               entry_price = 0.6995,
               updated_at = ?
         WHERE position_id = 'pos-exit-recorded-matched'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-recorded-exit-matched",
        venue_order_id="ord-exit-recorded-matched",
        position_id="pos-exit-recorded-matched",
        token_id=token,
        side="SELL",
        size=15.5,
        price=0.69,
        state="PARTIAL",
    )
    append_order_fact(
        conn,
        command_id="cmd-recorded-exit-matched",
        venue_order_id="ord-exit-recorded-matched",
        state="MATCHED",
        matched_size="10.85",
        remaining_size="4.65",
        source="REST",
        observed_at=NOW,
        venue_timestamp=NOW,
        raw_payload_hash="d" * 64,
        raw_payload_json={
            "status": "matched",
            "makingAmount": "15.5",
            "takingAmount": "10.85",
        },
    )
    append_trade_fact(
        conn,
        command_id="cmd-recorded-exit-matched",
        venue_order_id="ord-exit-recorded-matched",
        token_id=token,
        trade_id="trade-recorded-exit-matched",
        size="15.5",
        fill_price="0.7",
        state="MATCHED",
    )

    summary = reconcile_recorded_maker_fill_economics(conn, observed_at=NOW)

    assert summary["exit_command_terminalized"] == 1
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-recorded-exit-matched'"
    ).fetchone()["state"] == "FILLED"
    latest_order_fact = conn.execute(
        """
        SELECT state, remaining_size, matched_size, source
          FROM venue_order_facts
         WHERE command_id = 'cmd-recorded-exit-matched'
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """
    ).fetchone()
    assert dict(latest_order_fact) == {
        "state": "MATCHED",
        "remaining_size": "0",
        "matched_size": "15.5",
        "source": "REST",
    }
    current = conn.execute(
        "SELECT phase, order_status FROM position_current WHERE position_id = 'pos-exit-recorded-matched'"
    ).fetchone()
    assert dict(current) == {
        "phase": "pending_exit",
        "order_status": "sell_pending_confirmation",
    }
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM execution_fact
             WHERE intent_id = 'pos-exit-recorded-matched:exit'
               AND terminal_exec_status = 'filled'
            """
        ).fetchone()[0]
        == 0
    )


def test_recorded_nonfinal_exit_tx_alias_cannot_fake_full_command_coverage(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics

    tx_hash = "0xrecorded-partial-exit"
    seed_command(
        conn,
        command_id="cmd-recorded-partial-exit",
        venue_order_id="ord-recorded-partial-exit",
        position_id="pos-recorded-partial-exit",
        token_id="recorded-partial-exit-token",
        side="SELL",
        size=15.5,
        price=0.69,
        state="PARTIAL",
    )
    for trade_id in ("trade-recorded-partial-exit", tx_hash):
        append_trade_fact(
            conn,
            command_id="cmd-recorded-partial-exit",
            venue_order_id="ord-recorded-partial-exit",
            trade_id=trade_id,
            size="7.75",
            fill_price="0.7",
            state="MATCHED",
            tx_hash=tx_hash,
        )

    summary = reconcile_recorded_maker_fill_economics(conn, observed_at=NOW)

    assert summary.get("exit_command_terminalized", 0) == 0
    assert summary["stayed"] >= 1
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-recorded-partial-exit'"
    ).fetchone()["state"] == "PARTIAL"


@pytest.mark.parametrize("status", ["MATCHED", "MINED"])
def test_nonconfirmed_exit_trade_does_not_economically_close_position(conn, status):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "exit-nonconfirmed-token"
    seed_position_baseline(conn, position_id="pos-exit-nonconfirmed", order_id="ord-entry-exit-nonconfirmed")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'active',
               token_id = ?,
               order_id = 'ord-entry-exit-nonconfirmed',
               order_status = 'filled',
               shares = 10,
               cost_basis_usd = 5,
               entry_price = 0.5,
               updated_at = ?
         WHERE position_id = 'pos-exit-nonconfirmed'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id=f"cmd-exit-{status.lower()}",
        venue_order_id=f"ord-exit-{status.lower()}",
        position_id="pos-exit-nonconfirmed",
        token_id=token,
        side="SELL",
        size=10,
        price=0.45,
        state="ACKED",
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id=f"trade-exit-{status.lower()}",
                    order_id=f"ord-exit-{status.lower()}",
                    size="10",
                    price="0.46",
                    status=status,
                )
            ],
            positions=[position(token_id=token, size="10")],
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert event_types(conn, f"cmd-exit-{status.lower()}")[-1] == "FILL_CONFIRMED"
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = ?",
        (f"cmd-exit-{status.lower()}",),
    ).fetchone()["state"] == "FILLED"
    assert conn.execute(
        """
        SELECT phase
          FROM position_current
         WHERE position_id = 'pos-exit-nonconfirmed'
        """
    ).fetchone()["phase"] == "active"
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = 'pos-exit-nonconfirmed'
           AND event_type = 'EXIT_ORDER_FILLED'
        """
    ).fetchone()[0] == 0


@pytest.mark.parametrize("status", ["MATCHED", "MINED"])
def test_full_size_nonconfirmed_exit_trade_leaves_actionable_finality_finding(conn, status):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "exit-finality-wait-token"
    seed_position_baseline(conn, position_id="pos-exit-finality-wait", order_id="ord-entry-finality-wait")
    seed_command(
        conn,
        command_id="cmd-entry-finality-wait",
        venue_order_id="ord-entry-finality-wait",
        position_id="pos-exit-finality-wait",
        token_id=token,
        side="BUY",
        size=17.3,
        price=0.21,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-entry-finality-wait",
        venue_order_id="ord-entry-finality-wait",
        token_id=token,
        trade_id="trade-entry-finality-wait",
        size="17.3",
        state="CONFIRMED",
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               order_id = 'ord-exit-finality-wait',
               order_status = 'sell_pending_confirmation',
               shares = 17.3,
               cost_basis_usd = 3.633,
               entry_price = 0.21,
               updated_at = ?
         WHERE position_id = 'pos-exit-finality-wait'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-exit-finality-wait",
        venue_order_id="ord-exit-finality-wait",
        position_id="pos-exit-finality-wait",
        token_id=token,
        side="SELL",
        size=17.3,
        price=0.19,
        state="ACKED",
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id=f"trade-exit-finality-wait-{status.lower()}",
                    order_id="ord-exit-finality-wait",
                    size="17.3",
                    price="0.19",
                    status=status,
                )
            ],
            positions=[position(token_id=token, size="0")],
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    finality_findings = [
        finding
        for finding in result
        if finding.kind == "unrecorded_trade"
        and f"trade-exit-finality-wait-{status.lower()}" in finding.subject_id
    ]
    assert len(finality_findings) == 1
    assert "exchange_trade_full_size_nonfinal_exit_fill_waiting_confirmation" in finality_findings[0].evidence_json
    current = conn.execute(
        "SELECT phase, order_status FROM position_current WHERE position_id = 'pos-exit-finality-wait'"
    ).fetchone()
    assert dict(current) == {
        "phase": "pending_exit",
        "order_status": "sell_pending_confirmation",
    }
    command_state = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-exit-finality-wait'"
    ).fetchone()["state"]
    assert command_state == "FILLED"
    assert event_types(conn, "cmd-exit-finality-wait")[-1] == "FILL_CONFIRMED"
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM execution_fact
             WHERE intent_id = 'pos-exit-finality-wait:exit'
               AND terminal_exec_status = 'filled'
            """
        ).fetchone()[0]
        == 0
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id=f"trade-exit-finality-wait-{status.lower()}",
                    order_id="ord-exit-finality-wait",
                    size="17.3",
                    price="0.19",
                    status="CONFIRMED",
                )
            ],
            positions=[position(token_id=token, size="0")],
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    resolved = conn.execute(
        """
        SELECT resolved_at, resolution
          FROM exchange_reconcile_findings
         WHERE subject_id = ?
        """,
        (f"finality:trade-exit-finality-wait-{status.lower()}",),
    ).fetchone()
    assert resolved["resolved_at"] == (NOW + timedelta(seconds=1)).isoformat()
    assert resolved["resolution"] == "trade_finality_confirmed"
    execution = conn.execute(
        """
        SELECT command_id, filled_at, fill_price, shares, venue_status, terminal_exec_status
          FROM execution_fact
         WHERE intent_id = 'pos-exit-finality-wait:exit'
        """
    ).fetchone()
    assert dict(execution) == {
        "command_id": "cmd-exit-finality-wait",
        "filled_at": (NOW + timedelta(seconds=1)).isoformat(),
        "fill_price": 0.19,
        "shares": 17.3,
        "venue_status": "FILLED",
        "terminal_exec_status": "filled",
    }


def test_partial_exit_fill_cannot_project_economic_close_even_with_fill_event(conn):
    """Final projection requires fill to cover the current position, not only the command."""
    from src.execution.exchange_reconcile import _ensure_exit_fill_position_event

    token = "exit-partial-terminal-guard-token"
    seed_position_baseline(
        conn,
        position_id="pos-exit-partial-terminal-guard",
        order_id="ord-entry-partial-terminal-guard",
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               order_id = 'ord-exit-partial-terminal-guard',
               order_status = 'sell_pending_confirmation',
               shares = 60.0,
               chain_shares = 60.0,
               cost_basis_usd = 12.0,
               entry_price = 0.2,
               updated_at = ?
         WHERE position_id = 'pos-exit-partial-terminal-guard'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-exit-partial-terminal-guard",
        venue_order_id="ord-exit-partial-terminal-guard",
        position_id="pos-exit-partial-terminal-guard",
        token_id=token,
        side="SELL",
        size=46.59,
        price=0.16,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-exit-partial-terminal-guard",
        venue_order_id="ord-exit-partial-terminal-guard",
        token_id=token,
        trade_id="trade-exit-partial-terminal-guard",
        size="46.59",
        fill_price="0.161646276024898",
        state="CONFIRMED",
        tx_hash="0xexit-partial-terminal-guard",
    )
    append_trade_fact(
        conn,
        command_id="cmd-exit-partial-terminal-guard",
        venue_order_id="ord-exit-partial-terminal-guard",
        token_id=token,
        trade_id="0xexit-partial-terminal-guard",
        size="46.59",
        fill_price="0.161646276024898",
        state="CONFIRMED",
        tx_hash="0xexit-partial-terminal-guard",
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = 'cmd-exit-partial-terminal-guard'"
        ).fetchone()
    )

    _ensure_exit_fill_position_event(
        conn,
        command=command,
        venue_order_id="ord-exit-partial-terminal-guard",
        filled_size="46.59",
        fill_price="0.161646276024898",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
    )

    current = conn.execute(
        """
        SELECT phase, order_status, chain_shares
          FROM position_current
         WHERE position_id = 'pos-exit-partial-terminal-guard'
        """
    ).fetchone()
    assert dict(current) == {
        "phase": "pending_exit",
        "order_status": "sell_pending_confirmation",
        "chain_shares": 60.0,
    }
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = 'pos-exit-partial-terminal-guard'
           AND event_type = 'EXIT_ORDER_FILLED'
        """
    ).fetchone()[0] == 0


def test_trade_lifecycle_update_appends_confirmed_after_matched_without_double_counting(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[trade(trade_id="trade-lifecycle", order_id="ord-m5", size="10", status="MATCHED")]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "PARTIAL"

    second = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-lifecycle",
                    order_id="ord-m5",
                    size="10.0",
                    status="CONFIRMED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    trade_rows = conn.execute(
        """
        SELECT state, filled_size, local_sequence
          FROM venue_trade_facts
         WHERE trade_id = 'trade-lifecycle'
         ORDER BY local_sequence
        """
    ).fetchall()
    assert [(row["state"], row["local_sequence"]) for row in trade_rows] == [
        ("MATCHED", 1),
        ("CONFIRMED", 2),
    ]
    assert [finding.kind for finding in second] == []
    assert event_types(conn)[-1] == "FILL_CONFIRMED"
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "FILLED"

    third = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-lifecycle",
                    order_id="ord-m5",
                    size="10",
                    status="CONFIRMED",
                )
            ],
            positions=[position(size="10")],
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=2),
    )

    assert conn.execute(
        "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id = 'trade-lifecycle'"
    ).fetchone()[0] == 2
    assert not any(finding.kind == "position_drift" for finding in third)


def test_confirmed_taker_exit_corrects_matched_point_order_price_without_drift(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_trade_fact

    seed_command(
        conn,
        command_id="cmd-exit-price",
        venue_order_id="ord-exit-price",
        position_id="pos-exit-price",
        side="SELL",
        state="PARTIAL",
        size=6,
        price=0.29,
    )
    append_trade_fact(
        conn,
        trade_id="trade-exit-price",
        venue_order_id="ord-exit-price",
        command_id="cmd-exit-price",
        state="MATCHED",
        filled_size="6",
        fill_price="0.29",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"trade-exit-price:matched:0.29").hexdigest(),
        raw_payload_json={
            "id": "trade-exit-price",
            "orderID": "ord-exit-price",
            "size": "6",
            "status": "MATCHED",
            "price": "0.29",
        },
    )

    result = run_reconcile_sweep(
        FakeAdapterWithoutPositions(
            trades=[
                trade(
                    trade_id="trade-exit-price",
                    order_id="ord-exit-price",
                    size="6",
                    price="0.30",
                    fill_price="0.30",
                    status="CONFIRMED",
                    taker_order_id="ord-exit-price",
                    trader_side="TAKER",
                    transaction_hash="0xconfirmed",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    trade_rows = conn.execute(
        """
        SELECT state, filled_size, fill_price, local_sequence, tx_hash
          FROM venue_trade_facts
         WHERE trade_id = 'trade-exit-price'
         ORDER BY local_sequence
        """
    ).fetchall()
    assert [finding.kind for finding in result] == []
    assert [row["state"] for row in trade_rows] == ["MATCHED", "CONFIRMED"]
    assert trade_rows[-1]["filled_size"] == "6"
    assert trade_rows[-1]["fill_price"] == "0.30"
    assert trade_rows[-1]["tx_hash"] == "0xconfirmed"
    assert event_types(conn, "cmd-exit-price")[-1] == "FILL_CONFIRMED"
    assert (
        conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-exit-price'").fetchone()["state"]
        == "FILLED"
    )


def test_unknown_trade_status_becomes_finding_not_matched_partial(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    result = run_reconcile_sweep(
        FakeM5Adapter(
            open_orders=[order(order_id="ord-m5")],
            trades=[
                trade(
                    trade_id="trade-unknown-state",
                    order_id="ord-m5",
                    size="10",
                    price="0.51",
                    status="SETTLED",
                )
            ],
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade"]
    assert "exchange_trade_unknown_trade_state" in result[0].evidence_json
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert "PARTIAL_FILL_OBSERVED" not in event_types(conn)
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_trade_lifecycle_regression_after_confirmed_becomes_finding_not_downgrade(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[trade(trade_id="trade-final", order_id="ord-m5", size="10", status="CONFIRMED")]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[trade(trade_id="trade-final", order_id="ord-m5", size="10", status="FAILED")]
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade"]
    assert "exchange_trade_lifecycle_regression_or_economic_drift" in result[0].evidence_json
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id = 'trade-final'"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT state FROM venue_trade_facts WHERE trade_id = 'trade-final'").fetchone()["state"] == "CONFIRMED"
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "FILLED"


def test_trade_lifecycle_forward_transition_requires_stable_fill_economics(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[trade(trade_id="trade-economic-drift", order_id="ord-m5", size="5", status="MATCHED")]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-economic-drift",
                    order_id="ord-m5",
                    size="10",
                    status="CONFIRMED",
                )
            ],
            positions=[position(size="5")],
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade", "position_drift"]
    assert "exchange_trade_lifecycle_regression_or_economic_drift" in result[0].evidence_json
    assert '"journal_size":"0"' in result[1].evidence_json
    assert '"optimistic_journal_size":"5"' in result[1].evidence_json
    assert '"reason":"exchange_position_differs_from_confirmed_trade_facts"' in result[1].evidence_json
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id = 'trade-economic-drift'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT state, filled_size FROM venue_trade_facts WHERE trade_id = 'trade-economic-drift'"
    ).fetchone()[:] == ("MATCHED", "5")
    assert "FILL_CONFIRMED" not in event_types(conn)
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "PARTIAL"


def test_linked_confirmed_trade_missing_fill_price_becomes_finding_not_fact(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-confirmed-no-price",
                    order_id="ord-m5",
                    size="10",
                    price=None,
                    status="CONFIRMED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade"]
    assert "exchange_trade_missing_fill_economics" in result[0].evidence_json
    assert "fill_price" in result[0].evidence_json
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert event_types(conn) == ["INTENT_CREATED", "SUBMIT_REQUESTED", "SUBMIT_ACKED"]
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_linked_confirmed_trade_generic_price_is_not_fill_authority(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-confirmed-generic-price",
                    order_id="ord-m5",
                    size="10",
                    price="0.51",
                    status="CONFIRMED",
                    include_fill_price=False,
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade"]
    assert "exchange_trade_missing_fill_economics" in result[0].evidence_json
    assert '"fill_price"' in result[0].evidence_json
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert event_types(conn) == ["INTENT_CREATED", "SUBMIT_REQUESTED", "SUBMIT_ACKED"]
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_linked_taker_confirmed_trade_top_level_price_records_fill_authority(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-confirmed-taker-price",
                    order_id="ord-m5",
                    size="10",
                    price="0.51",
                    status="CONFIRMED",
                    include_fill_price=False,
                    taker_order_id="ord-m5",
                    trader_side="TAKER",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert result == []
    latest = conn.execute(
        """
        SELECT state, filled_size, fill_price
          FROM venue_trade_facts
         WHERE trade_id = 'trade-confirmed-taker-price'
        """
    ).fetchone()
    assert latest[:] == ("CONFIRMED", "10", "0.51")
    assert "FILL_CONFIRMED" in event_types(conn)


def test_linked_confirmed_trade_explicit_fill_price_records_fill_authority(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-confirmed-explicit-fill",
                    order_id="ord-m5",
                    size="10",
                    price="0.49",
                    status="CONFIRMED",
                    fill_price="0.51",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert result == []
    assert conn.execute(
        "SELECT state, filled_size, fill_price FROM venue_trade_facts"
    ).fetchone()[:] == ("CONFIRMED", "10", "0.51")
    assert event_types(conn)[-1] == "FILL_CONFIRMED"
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "FILLED"


def test_linked_confirmed_trade_missing_venue_trade_id_becomes_finding_not_finality(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id=None,
                    order_id="ord-m5",
                    size="10",
                    price="0.51",
                    status="CONFIRMED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade"]
    assert "exchange_trade_missing_venue_trade_identity" in result[0].evidence_json
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert event_types(conn) == ["INTENT_CREATED", "SUBMIT_REQUESTED", "SUBMIT_ACKED"]
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_linked_matched_trade_missing_filled_size_becomes_finding_not_partial(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-matched-no-size",
                    order_id="ord-m5",
                    size=None,
                    price="0.51",
                    status="MATCHED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade"]
    assert "exchange_trade_missing_fill_economics" in result[0].evidence_json
    assert "filled_size" in result[0].evidence_json
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert "PARTIAL_FILL_OBSERVED" not in event_types(conn)
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


@pytest.mark.parametrize(
    ("size", "price", "missing_field"),
    [
        ("10", "NaN", "fill_price"),
        ("10", "Infinity", "fill_price"),
        ("NaN", "0.51", "filled_size"),
        ("Infinity", "0.51", "filled_size"),
    ],
)
def test_linked_confirmed_trade_nonfinite_economics_becomes_finding_not_fact(
    conn,
    size,
    price,
    missing_field,
):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)

    result = run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id=f"trade-confirmed-{missing_field}-{size}-{price}",
                    order_id="ord-m5",
                    size=size,
                    price=price,
                    status="CONFIRMED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade"]
    assert "exchange_trade_missing_fill_economics" in result[0].evidence_json
    assert missing_field in result[0].evidence_json
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0
    assert event_types(conn) == ["INTENT_CREATED", "SUBMIT_REQUESTED", "SUBMIT_ACKED"]
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_stale_or_unsuccessful_venue_reads_are_not_absence_proof(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    append_resting_order_fact(conn)
    adapter = FakeM5Adapter(open_orders=[], trades=[])
    adapter.read_freshness["open_orders"] = {"ok": False, "reason": "unauthorized"}

    with pytest.raises(ValueError, match="open_orders venue read is not fresh"):
        run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    assert findings(conn) == []
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_point_order_live_overrides_empty_global_open_order_enumeration(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    append_resting_order_fact(conn)
    adapter = FakeM5AdapterWithPointOrders(
        open_orders=[],
        trades=[],
        orders_by_id={"ord-m5": order(order_id="ord-m5", status="LIVE")},
    )

    result = run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    assert result == []
    assert findings(conn) == []
    assert ("get_order", ("ord-m5",), {}) in adapter.calls
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_point_order_terminal_keeps_local_orphan_finding_with_point_evidence(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    append_resting_order_fact(conn)
    adapter = FakeM5AdapterWithPointOrders(
        open_orders=[],
        trades=[],
        orders_by_id={"ord-m5": order(order_id="ord-m5", status="CANCELED")},
    )

    result = run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    assert [finding.kind for finding in result] == ["local_orphan_order"]
    assert ("get_order", ("ord-m5",), {}) in adapter.calls
    evidence = result[0].evidence_json
    assert '"point_order_status":"CANCELED"' in evidence
    assert '"point_order_surface":"get_order"' in evidence
    assert '"reason":"local_open_order_absent_from_exchange_open_orders"' in evidence


def test_explicit_fresh_false_is_not_absence_proof(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    append_resting_order_fact(conn)
    adapter = FakeM5Adapter(open_orders=[], trades=[])
    adapter.read_freshness["open_orders"] = {"ok": True, "fresh": False, "captured_at": NOW.isoformat()}

    with pytest.raises(ValueError, match="open_orders venue read is not fresh"):
        run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    assert findings(conn) == []
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_missing_read_freshness_is_not_absence_proof(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    append_resting_order_fact(conn)
    adapter = FakeAdapterWithoutFreshness(open_orders=[], trades=[])

    with pytest.raises(ValueError, match="open_orders venue read freshness is unavailable"):
        run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    assert findings(conn) == []
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_transport_ok_without_explicit_freshness_is_not_absence_proof(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    append_resting_order_fact(conn)
    adapter = FakeM5Adapter(open_orders=[], trades=[])
    adapter.read_freshness["open_orders"] = {"ok": True, "captured_at": NOW.isoformat()}

    with pytest.raises(ValueError, match="open_orders venue read is not fresh"):
        run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    assert findings(conn) == []
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_failed_trade_does_not_suppress_local_orphan_finding(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)
    append_resting_order_fact(conn)

    result = run_reconcile_sweep(
        FakeM5Adapter(open_orders=[], trades=[trade(trade_id="trade-failed", order_id="ord-m5", size="10", status="FAILED")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert conn.execute("SELECT state FROM venue_trade_facts WHERE trade_id = 'trade-failed'").fetchone()["state"] == "FAILED"
    assert any(finding.kind == "local_orphan_order" for finding in result)
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_ws_gap_partial_trade_does_not_suppress_local_orphan_when_order_absent(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)
    append_resting_order_fact(conn)

    result = run_reconcile_sweep(
        FakeM5Adapter(
            open_orders=[],
            trades=[trade(trade_id="trade-partial", order_id="ord-m5", size="4", status="CONFIRMED")],
        ),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert any(finding.kind == "local_orphan_order" for finding in result)
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "PARTIAL"


def test_real_adapter_missing_read_surface_is_not_absence_proof(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter, V2ReadUnavailable

    class ClientWithoutReads:
        pass

    seed_command(conn)
    append_resting_order_fact(conn)
    adapter = PolymarketV2Adapter(
        funder_address="0xfunder",
        signer_key="test-key",
        q1_egress_evidence_path=None,
        client_factory=lambda **_: ClientWithoutReads(),
    )

    with pytest.raises(ValueError, match="open_orders venue read freshness is unavailable"):
        run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    assert findings(conn) == []
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_sweep_idempotent_across_repeated_cycles(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    adapter = FakeM5Adapter(open_orders=[order(order_id="ord-ghost")])

    first = run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)
    second = run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW + timedelta(seconds=1))

    rows = findings(conn)
    assert len(rows) == 1
    assert first[0].finding_id == second[0].finding_id == rows[0]["finding_id"]


def test_sweep_does_not_create_new_venue_commands_rows(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    before = command_count(conn)
    insert_statements: list[str] = []
    conn.set_trace_callback(lambda sql: insert_statements.append(sql))

    run_reconcile_sweep(
        FakeM5Adapter(
            open_orders=[order(order_id="ord-ghost")],
            trades=[trade(trade_id="trade-ghost", order_id="ord-ghost")],
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    conn.set_trace_callback(None)
    assert command_count(conn) == before
    assert not any("INSERT INTO VENUE_COMMANDS" in sql.upper() for sql in insert_statements)


def test_position_drift_finding_distinguishes_legitimate_from_real(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    recent_token = "recent-fill-token"
    drift_token = "drift-token"
    seed_command(
        conn,
        command_id="cmd-recent",
        venue_order_id="ord-recent",
        token_id=recent_token,
        state="FILLED",
        created_at=NOW,
    )
    seed_command(conn, command_id="cmd-drift", venue_order_id="ord-drift", token_id=drift_token)
    append_trade_fact(conn, command_id="cmd-drift", venue_order_id="ord-drift", token_id=drift_token, trade_id="trade-drift", size="10")

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=recent_token, size="10"), position(token_id=drift_token, size="15")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    position_findings = [finding for finding in result if finding.kind == "position_drift"]
    assert [finding.subject_id for finding in position_findings] == [drift_token]
    assert "journal_size" in position_findings[0].evidence_json
    assert "exchange_size" in position_findings[0].evidence_json


def test_position_drift_honors_token_suppression_registry(conn):
    """ONE-TRUTH (rule 4): a token already in token_suppression (chain-only / settled holding,
    e.g. the operator's manual chain position) must NOT be re-flagged as a blocking position_drift.

    chain_reconciliation quarantines chain-only / operator-manual holdings into token_suppression
    ('chain_only_quarantined'); the harvester suppresses settled winners there ('settled_position').
    If exchange_reconcile ignores that registry and re-flags those tokens as position_drift, the M5
    submit latch stays CLOSED on positions the system never owned and live trading halts -- the
    multi-system-infighting zero-trade fault. A genuinely unsuppressed drift must still be flagged.
    """
    from src.execution.exchange_reconcile import run_reconcile_sweep

    suppressed = "operator-manual-chain-token"
    real_drift = "genuine-unsuppressed-drift-token"
    conn.execute(
        """
        INSERT INTO token_suppression (token_id, condition_id, suppression_reason, source_module, created_at, updated_at)
        VALUES (?, '0xcond', 'chain_only_quarantined', 'src.state.chain_reconciliation', ?, ?)
        """,
        (suppressed, NOW.isoformat(), NOW.isoformat()),
    )

    # Both are chain positions with no system trade facts -> both would normally be position_drift.
    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=suppressed, size="797"), position(token_id=real_drift, size="15")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    drift_subjects = [finding.subject_id for finding in result if finding.kind == "position_drift"]
    assert suppressed not in drift_subjects, "a suppressed (chain-only/manual) token must not gate the submit latch"
    assert real_drift in drift_subjects, "a genuinely unsuppressed drift must still be flagged"


def test_position_drift_compares_exchange_to_confirmed_not_optimistic(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    optimistic_token = "optimistic-position-token"
    seed_command(
        conn,
        command_id="cmd-optimistic",
        venue_order_id="ord-optimistic",
        token_id=optimistic_token,
    )
    append_trade_fact(
        conn,
        command_id="cmd-optimistic",
        venue_order_id="ord-optimistic",
        token_id=optimistic_token,
        trade_id="trade-optimistic",
        size="10",
        state="MATCHED",
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=optimistic_token, size="10")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    position_findings = [finding for finding in result if finding.kind == "position_drift"]
    assert [finding.subject_id for finding in position_findings] == [optimistic_token]
    evidence = position_findings[0].evidence_json
    assert '"exchange_size":"10"' in evidence
    assert '"journal_size":"0"' in evidence
    assert '"confirmed_journal_size":"0"' in evidence
    assert '"optimistic_journal_size":"10"' in evidence
    assert '"journal_evidence_class":"confirmed_trade_facts"' in evidence
    assert '"optimistic_evidence_class":"matched_or_mined_trade_facts"' in evidence
    assert '"reason":"exchange_position_differs_from_confirmed_trade_facts"' in evidence


def test_position_drift_treats_venue_live_sell_as_locked_wallet_tokens(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "live-sell-locked-token"
    seed_command(
        conn,
        command_id="cmd-live-sell-entry",
        venue_order_id="ord-live-sell-entry",
        token_id=token,
        state="FILLED",
        created_at=NOW - timedelta(minutes=10),
    )
    append_trade_fact(
        conn,
        command_id="cmd-live-sell-entry",
        venue_order_id="ord-live-sell-entry",
        token_id=token,
        trade_id="trade-live-sell-entry",
        size="10",
        state="CONFIRMED",
    )
    seed_command(
        conn,
        command_id="cmd-live-sell-exit",
        venue_order_id="ord-live-sell-exit",
        token_id=token,
        side="SELL",
        size=10.0,
        price=0.86,
        state="ACKED",
        created_at=NOW - timedelta(minutes=1),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(
            open_orders=[order(order_id="ord-live-sell-exit", status="LIVE")],
            positions=[],
        ),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert not any(finding.kind == "position_drift" for finding in result)


def test_exit_trade_alias_not_double_counted_in_locked_and_journal_views(conn):
    from src.execution.exchange_reconcile import (
        _canonical_filled_size_for_command,
        _journal_positions_by_token,
    )

    token = "exit-alias-balance-token"
    tx_hash = "0xexit-alias-balance"
    seed_command(
        conn,
        command_id="cmd-exit-alias-balance",
        venue_order_id="ord-exit-alias-balance",
        token_id=token,
        side="SELL",
        size=10.0,
        price=0.5,
        state="ACKED",
    )
    for trade_id in ("trade-exit-alias-balance", tx_hash):
        append_trade_fact(
            conn,
            command_id="cmd-exit-alias-balance",
            venue_order_id="ord-exit-alias-balance",
            token_id=token,
            trade_id=trade_id,
            size="5",
            fill_price="0.5",
            state="MATCHED",
            tx_hash=tx_hash,
        )

    assert _canonical_filled_size_for_command(
        conn, "cmd-exit-alias-balance"
    ) == Decimal("5")
    assert _journal_positions_by_token(
        conn, states=frozenset({"MATCHED"})
    ) == {token: Decimal("-5")}


def test_position_drift_requires_fresh_venue_live_sell_before_locking_wallet_tokens(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "missing-live-sell-token"
    seed_command(
        conn,
        command_id="cmd-missing-sell-entry",
        venue_order_id="ord-missing-sell-entry",
        token_id=token,
        state="FILLED",
        created_at=NOW - timedelta(minutes=10),
    )
    append_trade_fact(
        conn,
        command_id="cmd-missing-sell-entry",
        venue_order_id="ord-missing-sell-entry",
        token_id=token,
        trade_id="trade-missing-sell-entry",
        size="10",
        state="CONFIRMED",
    )
    seed_command(
        conn,
        command_id="cmd-missing-sell-exit",
        venue_order_id="ord-missing-sell-exit",
        token_id=token,
        side="SELL",
        size=10.0,
        price=0.86,
        state="ACKED",
        created_at=NOW - timedelta(minutes=1),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(open_orders=[], positions=[]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    position_findings = [finding for finding in result if finding.kind == "position_drift"]
    assert [finding.subject_id for finding in position_findings] == [token]
    assert '"open_sell_locked_size":"0"' in position_findings[0].evidence_json


def test_position_drift_tolerates_venue_precision_and_resolves_existing(conn):
    from src.execution.exchange_reconcile import record_finding, run_reconcile_sweep

    token = "rounded-position-token"
    seed_command(conn, command_id="cmd-rounded", venue_order_id="ord-rounded", token_id=token)
    append_trade_fact(
        conn,
        command_id="cmd-rounded",
        venue_order_id="ord-rounded",
        token_id=token,
        trade_id="trade-rounded",
        size="1.304337",
        state="CONFIRMED",
    )
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "stale_precision_probe"},
        recorded_at=NOW - timedelta(minutes=1),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="1.3043")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert not any(finding.kind == "position_drift" for finding in result)
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_cleared",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_position_drift_ignores_position_api_visibility_floor_dust(conn):
    from src.execution.exchange_reconcile import record_finding, run_reconcile_sweep

    token = "dust-position-token"
    seed_command(conn, command_id="cmd-dust", venue_order_id="ord-dust", token_id=token)
    append_trade_fact(
        conn,
        command_id="cmd-dust",
        venue_order_id="ord-dust",
        token_id=token,
        trade_id="trade-dust",
        size="0.007891",
        state="CONFIRMED",
    )
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "stale_dust_probe"},
        recorded_at=NOW - timedelta(minutes=1),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert not any(finding.kind == "position_drift" for finding in result)
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_below_position_api_visibility_floor",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_position_drift_visibility_floor_does_not_hide_material_drift(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "material-position-token"
    seed_command(
        conn,
        command_id="cmd-material",
        venue_order_id="ord-material",
        token_id=token,
    )
    append_trade_fact(
        conn,
        command_id="cmd-material",
        venue_order_id="ord-material",
        token_id=token,
        trade_id="trade-material",
        size="0.0101",
        state="CONFIRMED",
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    position_findings = [finding for finding in result if finding.kind == "position_drift"]
    assert [finding.subject_id for finding in position_findings] == [token]
    assert '"confirmed_journal_size":"0.0101"' in position_findings[0].evidence_json


def test_terminal_canonical_position_does_not_remain_current_journal_exposure(conn):
    from src.execution.exchange_reconcile import (
        record_finding,
        refresh_unresolved_reconcile_findings,
        run_reconcile_sweep,
    )

    token = "terminal-position-token"
    seed_command(
        conn,
        command_id="cmd-terminal",
        venue_order_id="ord-terminal",
        position_id="pos-terminal",
        token_id=token,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-terminal",
        venue_order_id="ord-terminal",
        token_id=token,
        trade_id="trade-terminal",
        size="10",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-terminal", order_id="ord-terminal")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               token_id = ?,
               shares = 10,
               order_id = 'ord-terminal',
               updated_at = ?
         WHERE position_id = 'pos-terminal'
        """,
        (token, NOW.isoformat()),
    )
    observed = NOW + timedelta(minutes=10)
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "terminal_position_probe"},
        recorded_at=observed - timedelta(minutes=1),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[]),
        conn,
        context="ws_gap",
        observed_at=observed,
    )
    refreshed = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[], trades=[], positions=[]),
        conn,
        observed_at=observed + timedelta(seconds=1),
    )

    assert not any(finding.kind == "position_drift" for finding in result)
    assert refreshed["status"] == "resolved"
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_cleared",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_terminal_local_row_does_not_hide_positive_exchange_exposure(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "terminal-row-exchange-positive-token"
    seed_command(
        conn,
        command_id="cmd-terminal-positive",
        venue_order_id="ord-terminal-positive",
        position_id="pos-terminal-positive",
        token_id=token,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-terminal-positive",
        venue_order_id="ord-terminal-positive",
        token_id=token,
        trade_id="trade-terminal-positive",
        size="10",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-terminal-positive", order_id="ord-terminal-positive")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               token_id = ?,
               shares = 10,
               order_id = 'ord-terminal-positive',
               updated_at = ?
         WHERE position_id = 'pos-terminal-positive'
        """,
        (token, NOW.isoformat()),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="10")]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=10),
    )

    position_findings = [finding for finding in result if finding.kind == "position_drift"]
    assert [finding.subject_id for finding in position_findings] == [token]
    assert '"exchange_size":"10"' in position_findings[0].evidence_json
    assert '"confirmed_journal_size":"0"' in position_findings[0].evidence_json


def test_settled_redeem_pending_token_holding_is_expected_wallet_balance(conn):
    from src.execution.exchange_reconcile import record_finding, run_reconcile_sweep

    token = "settled-redeem-pending-token"
    seed_command(
        conn,
        command_id="cmd-settled-redeem-pending",
        venue_order_id="ord-settled-redeem-pending",
        position_id="pos-settled-redeem-pending",
        token_id=token,
        state="FILLED",
        size=5.17,
        price=0.37,
    )
    append_trade_fact(
        conn,
        command_id="cmd-settled-redeem-pending",
        venue_order_id="ord-settled-redeem-pending",
        token_id=token,
        trade_id="trade-settled-redeem-pending",
        size="1.5873",
        state="CONFIRMED",
    )
    seed_position_baseline(
        conn,
        position_id="pos-settled-redeem-pending",
        order_id="ord-settled-redeem-pending",
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'settled',
               chain_state = 'synced',
               token_id = ?,
               condition_id = 'condition-m5',
               market_id = 'condition-m5',
               shares = 1.5873,
               order_id = 'ord-settled-redeem-pending',
               updated_at = ?
         WHERE position_id = 'pos-settled-redeem-pending'
        """,
        (token, NOW.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO settlement_commands (
            command_id, state, condition_id, market_id, payout_asset,
            pusd_amount_micro, token_amounts_json, requested_at, winning_index_set
        ) VALUES (?, 'REDEEM_INTENT_CREATED', 'condition-m5', 'condition-m5', 'pUSD',
                  1587297, ?, ?, ?)
        """,
        (
            "redeem-settled-redeem-pending",
            json.dumps({token: 1.5872972972972974}, separators=(",", ":")),
            NOW.isoformat(),
            json.dumps(["2"]),
        ),
    )
    observed = NOW + timedelta(minutes=10)
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "settled_redeem_pending_probe"},
        recorded_at=observed - timedelta(minutes=1),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="1.5873")]),
        conn,
        context="ws_gap",
        observed_at=observed,
    )

    assert not any(finding.kind == "position_drift" for finding in result)
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_settlement_command_token_holding",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_terminal_position_current_token_holding_is_expected_wallet_balance(conn):
    from src.execution.exchange_reconcile import record_finding, run_reconcile_sweep

    token = "terminal-position-current-token"
    seed_command(
        conn,
        command_id="cmd-terminal-position-current",
        venue_order_id="ord-terminal-position-current",
        position_id="pos-terminal-position-current",
        token_id=token,
        state="FILLED",
        size=12.5,
        price=0.11,
    )
    seed_position_baseline(
        conn,
        position_id="pos-terminal-position-current",
        order_id="ord-terminal-position-current",
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'settled',
               chain_state = 'synced',
               token_id = ?,
               condition_id = 'condition-m5',
               market_id = 'condition-m5',
               direction = 'buy_yes',
               shares = 12.5,
               order_id = 'ord-terminal-position-current',
               updated_at = ?
         WHERE position_id = 'pos-terminal-position-current'
        """,
        (token, NOW.isoformat()),
    )
    observed = NOW + timedelta(minutes=10)
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "terminal_position_current_probe"},
        recorded_at=observed - timedelta(minutes=1),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="12.5")]),
        conn,
        context="ws_gap",
        observed_at=observed,
    )

    assert not any(finding.kind == "position_drift" for finding in result)
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_closed_position_token_holding",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_duplicate_terminal_positions_same_order_count_holding_once(conn):
    """Two terminal position_current rows for the SAME on-chain fill (same order_id)
    must contribute the expected-wallet holding ONCE, not summed.

    Regression for the 2026-06-16 M5 latch freeze: token 9491..517 was booked under
    multiple position_ids (two voided), all 5.07 shares from ONE venue order 0x5ce1..,
    so `_closed_position_token_holdings_by_token` summed to expected_wallet 10.14 vs
    exchange 5.07 and re-recorded position_drift forever. RED on the pre-fix `sum()`.
    """
    from src.execution.exchange_reconcile import record_finding, run_reconcile_sweep

    no_token = "dup-terminal-no-token"
    shared_order = "ord-dup-terminal-shared"
    for position_id in ("pos-dup-terminal-a", "pos-dup-terminal-b"):
        seed_position_baseline(conn, position_id=position_id, order_id=shared_order)
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'voided',
                   chain_state = 'synced',
                   direction = 'buy_no',
                   no_token_id = ?,
                   token_id = '',
                   condition_id = 'condition-m5',
                   market_id = 'condition-m5',
                   shares = 5.07,
                   order_id = ?,
                   updated_at = ?
             WHERE position_id = ?
            """,
            (no_token, shared_order, NOW.isoformat(), position_id),
        )
    observed = NOW + timedelta(minutes=10)
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=no_token,
        context="ws_gap",
        evidence={"reason": "duplicate_terminal_position_probe"},
        recorded_at=observed - timedelta(minutes=1),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=no_token, size="5.07")]),
        conn,
        context="ws_gap",
        observed_at=observed,
    )

    assert not any(finding.kind == "position_drift" for finding in result)
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_closed_position_token_holding",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_distinct_orders_same_token_still_sum_terminal_holdings(conn):
    """Two terminal position_current rows on DISTINCT orders are distinct on-chain
    fills and MUST still sum (the dedupe collapses only same-order duplicates).

    Guards against over-collapsing: token 1139..946 holds 6+6 from two real orders =
    expected_wallet 12.0, which must match a 12.0 exchange position (no drift).
    """
    from src.execution.exchange_reconcile import record_finding, run_reconcile_sweep

    no_token = "distinct-orders-no-token"
    for position_id, order_id in (
        ("pos-distinct-a", "ord-distinct-a"),
        ("pos-distinct-b", "ord-distinct-b"),
    ):
        seed_position_baseline(conn, position_id=position_id, order_id=order_id)
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'settled',
                   chain_state = 'synced',
                   direction = 'buy_no',
                   no_token_id = ?,
                   token_id = '',
                   condition_id = 'condition-m5',
                   market_id = 'condition-m5',
                   shares = 6.0,
                   order_id = ?,
                   updated_at = ?
             WHERE position_id = ?
            """,
            (no_token, order_id, NOW.isoformat(), position_id),
        )
    observed = NOW + timedelta(minutes=10)
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=no_token,
        context="ws_gap",
        evidence={"reason": "distinct_orders_probe"},
        recorded_at=observed - timedelta(minutes=1),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=no_token, size="12.0")]),
        conn,
        context="ws_gap",
        observed_at=observed,
    )

    assert not any(finding.kind == "position_drift" for finding in result)
    resolved = conn.execute(
        "SELECT resolution FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert resolved["resolution"] == "position_drift_closed_position_token_holding"


def test_redeem_confirmed_settled_token_still_at_exchange_is_position_drift(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "redeem-confirmed-exchange-positive-token"
    seed_command(
        conn,
        command_id="cmd-redeem-confirmed",
        venue_order_id="ord-redeem-confirmed",
        position_id="pos-redeem-confirmed",
        token_id=token,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-redeem-confirmed",
        venue_order_id="ord-redeem-confirmed",
        token_id=token,
        trade_id="trade-redeem-confirmed",
        size="1.5873",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-redeem-confirmed", order_id="ord-redeem-confirmed")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'settled',
               token_id = ?,
               condition_id = 'condition-m5',
               market_id = 'condition-m5',
               shares = 1.5873,
               order_id = 'ord-redeem-confirmed',
               updated_at = ?
         WHERE position_id = 'pos-redeem-confirmed'
        """,
        (token, NOW.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO settlement_commands (
            command_id, state, condition_id, market_id, payout_asset,
            pusd_amount_micro, token_amounts_json, tx_hash, requested_at,
            submitted_at, terminal_at, winning_index_set
        ) VALUES (?, 'REDEEM_CONFIRMED', 'condition-m5', 'condition-m5', 'pUSD',
                  1587297, ?, '0xredeem', ?, ?, ?, ?)
        """,
        (
            "redeem-confirmed",
            json.dumps({token: 1.5873}, separators=(",", ":")),
            NOW.isoformat(),
            NOW.isoformat(),
            NOW.isoformat(),
            json.dumps(["2"]),
        ),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="1.5873")]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=10),
    )

    position_findings = [finding for finding in result if finding.kind == "position_drift"]
    assert [finding.subject_id for finding in position_findings] == [token]
    evidence = position_findings[0].evidence_json
    assert '"exchange_size":"1.5873"' in evidence
    assert '"settlement_command_token_size":"0"' in evidence
    assert '"expected_wallet_size":"0"' in evidence


@pytest.mark.parametrize("settlement_state", ["REDEEM_FAILED", "REDEEM_REVIEW_REQUIRED"])
def test_terminal_non_pending_redeem_state_does_not_mask_position_drift(conn, settlement_state):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = f"{settlement_state.lower()}-exchange-positive-token"
    seed_command(
        conn,
        command_id=f"cmd-{settlement_state.lower()}",
        venue_order_id=f"ord-{settlement_state.lower()}",
        position_id=f"pos-{settlement_state.lower()}",
        token_id=token,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id=f"cmd-{settlement_state.lower()}",
        venue_order_id=f"ord-{settlement_state.lower()}",
        token_id=token,
        trade_id=f"trade-{settlement_state.lower()}",
        size="1.5873",
        state="CONFIRMED",
    )
    seed_position_baseline(
        conn,
        position_id=f"pos-{settlement_state.lower()}",
        order_id=f"ord-{settlement_state.lower()}",
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'settled',
               token_id = ?,
               condition_id = 'condition-m5',
               market_id = 'condition-m5',
               shares = 1.5873,
               updated_at = ?
         WHERE position_id = ?
        """,
        (token, NOW.isoformat(), f"pos-{settlement_state.lower()}"),
    )
    conn.execute(
        """
        INSERT INTO settlement_commands (
            command_id, state, condition_id, market_id, payout_asset,
            pusd_amount_micro, token_amounts_json, requested_at,
            terminal_at, winning_index_set
        ) VALUES (?, ?, 'condition-m5', 'condition-m5', 'pUSD',
                  1587297, ?, ?, ?, ?)
        """,
        (
            f"redeem-{settlement_state.lower()}",
            settlement_state,
            json.dumps({token: 1.5873}, separators=(",", ":")),
            NOW.isoformat(),
            NOW.isoformat(),
            json.dumps(["2"]),
        ),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="1.5873")]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=10),
    )

    position_findings = [finding for finding in result if finding.kind == "position_drift"]
    assert [finding.subject_id for finding in position_findings] == [token]
    evidence = position_findings[0].evidence_json
    assert '"exchange_size":"1.5873"' in evidence
    assert '"settlement_command_token_size":"0"' in evidence
    assert '"expected_wallet_size":"0"' in evidence


def test_backoff_exhausted_chain_absent_pending_exit_admin_closes_canonical(conn):
    from src.execution.exit_lifecycle import handle_exit_pending_missing
    from src.state.portfolio import PortfolioState, Position

    token = "backoff-chain-absent-token"
    position_id = "pos-backoff-chain-absent"
    seed_position_baseline(conn, position_id=position_id, order_id="ord-backoff-exit")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               order_id = 'ord-backoff-exit',
               order_status = 'backoff_exhausted',
               shares = 4.95,
               updated_at = ?
         WHERE position_id = ?
        """,
        (token, NOW.isoformat(), position_id),
    )
    pos = Position(
        trade_id=position_id,
        market_id="condition-m5",
        city="Karachi",
        cluster="Karachi",
        target_date="2026-05-17",
        bin_label="test-bin",
        direction="buy_yes",
        unit="C",
        env="live",
        state="pending_exit",
        exit_state="backoff_exhausted",
        chain_state="exit_pending_missing",
        token_id=token,
        no_token_id=f"{token}-no",
        condition_id="condition-m5",
        order_id="ord-backoff-exit",
        order_status="backoff_exhausted",
        last_exit_order_id="ord-backoff-exit",
        last_exit_error="exit_pending_missing",
        shares=4.95,
        cost_basis_usd=1.0,
        entry_price=0.2,
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        edge_source="opening_inertia",
        discovery_mode="opening_hunt",
        decision_snapshot_id="snap-m5",
        entered_at=NOW.isoformat(),
    )
    portfolio = PortfolioState(positions=[pos])

    result = handle_exit_pending_missing(portfolio, pos, conn=conn)

    current = conn.execute(
        "SELECT phase, order_status FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    latest_event = conn.execute(
        """
        SELECT event_type, phase_after, source_module
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    assert result["action"] == "closed"
    assert result["position"].state == "admin_closed"
    assert portfolio.positions == []
    assert dict(current) == {"phase": "admin_closed", "order_status": "backoff_exhausted"}
    assert dict(latest_event) == {
        "event_type": "MANUAL_OVERRIDE_APPLIED",
        "phase_after": "admin_closed",
        "source_module": "src.execution.exit_lifecycle",
    }


def test_recoverable_exit_pending_missing_does_not_persist_admin_close(conn):
    from src.execution.exit_lifecycle import handle_exit_pending_missing
    from src.state.portfolio import PortfolioState, Position

    token = "recoverable-chain-absent-token"
    position_id = "pos-recoverable-chain-absent"
    seed_position_baseline(conn, position_id=position_id, order_id="ord-recoverable-exit")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               order_id = 'ord-recoverable-exit',
               order_status = 'retry_pending',
               shares = 4.95,
               updated_at = ?
         WHERE position_id = ?
        """,
        (token, NOW.isoformat(), position_id),
    )
    pos = Position(
        trade_id=position_id,
        market_id="condition-m5",
        city="Karachi",
        cluster="Karachi",
        target_date="2026-05-17",
        bin_label="test-bin",
        direction="buy_yes",
        unit="C",
        env="live",
        state="pending_exit",
        exit_state="retry_pending",
        chain_state="exit_pending_missing",
        token_id=token,
        no_token_id=f"{token}-no",
        condition_id="condition-m5",
        order_id="ord-recoverable-exit",
        order_status="retry_pending",
        last_exit_order_id="ord-recoverable-exit",
        last_exit_error="exit_pending_missing",
        shares=4.95,
        cost_basis_usd=1.0,
        entry_price=0.2,
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        edge_source="opening_inertia",
        discovery_mode="opening_hunt",
        decision_snapshot_id="snap-m5",
        entered_at=NOW.isoformat(),
    )

    result = handle_exit_pending_missing(PortfolioState(positions=[pos]), pos, conn=conn)

    current = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    admin_events = conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'MANUAL_OVERRIDE_APPLIED'
        """,
        (position_id,),
    ).fetchone()[0]
    assert result["action"] == "closed"
    assert current["phase"] == "pending_exit"
    assert admin_events == 0


def test_pending_exit_chain_missing_filled_order_is_not_current_journal_exposure(conn):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings

    token = "pending-exit-filled-missing-token"
    seed_command(
        conn,
        command_id="cmd-pending-exit-filled",
        venue_order_id="ord-pending-exit-filled",
        position_id="pos-pending-exit-filled",
        token_id=token,
        side="BUY",
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-pending-exit-filled",
        venue_order_id="ord-pending-exit-filled",
        token_id=token,
        trade_id="trade-pending-exit-filled",
        size="6",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-pending-exit-filled", order_id="ord-pending-exit-filled")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               chain_state = 'exit_pending_missing',
               token_id = ?,
               order_id = 'ord-pending-exit-filled',
               order_status = 'filled',
               shares = 6,
               updated_at = ?
         WHERE position_id = 'pos-pending-exit-filled'
        """,
        (token, NOW.isoformat()),
    )
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "pending_exit_filled_chain_missing_probe"},
        recorded_at=NOW - timedelta(minutes=1),
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[], trades=[], positions=[]),
        conn,
        observed_at=NOW,
    )

    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert result["status"] == "resolved"
    assert dict(resolved) == {
        "resolution": "position_drift_cleared",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_pending_exit_matched_sell_offsets_confirmed_position_without_drift(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "pending-exit-token"
    seed_command(
        conn,
        command_id="cmd-entry-pending-exit",
        venue_order_id="ord-entry-pending-exit",
        token_id=token,
        side="BUY",
        size=23.7,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-entry-pending-exit",
        venue_order_id="ord-entry-pending-exit",
        token_id=token,
        trade_id="trade-entry-pending-exit",
        size="23.7",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-pending-exit", order_id="ord-exit-pending")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               order_id = 'ord-exit-pending',
               order_status = 'sell_pending_confirmation',
               shares = 23.7,
               cost_basis_usd = 1.659,
               entry_price = 0.07,
               updated_at = ?
         WHERE position_id = 'pos-pending-exit'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-exit-pending",
        venue_order_id="ord-exit-pending",
        position_id="pos-pending-exit",
        token_id=token,
        side="SELL",
        size=23.7,
        price=0.04,
        state="PARTIAL",
    )
    append_trade_fact(
        conn,
        command_id="cmd-exit-pending",
        venue_order_id="ord-exit-pending",
        token_id=token,
        trade_id="trade-exit-pending",
        size="23.7",
        state="MATCHED",
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="0")]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert not any(finding.kind == "position_drift" for finding in result)


def test_unresolved_position_drift_refresh_resolves_late_confirmed_entry_without_broad_scan(conn):
    from src.execution.exchange_reconcile import (
        record_finding,
        refresh_unresolved_reconcile_findings,
    )

    token = "late-confirmed-entry-token"
    unrelated_token = "unrelated-confirmed-token"
    seed_command(
        conn,
        command_id="cmd-late-confirmed",
        venue_order_id="ord-late-confirmed",
        token_id=token,
        size=5,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-late-confirmed",
        venue_order_id="ord-late-confirmed",
        token_id=token,
        trade_id="trade-late-confirmed",
        size="5",
        state="MATCHED",
    )
    seed_command(
        conn,
        command_id="cmd-unrelated-confirmed",
        venue_order_id="ord-unrelated-confirmed",
        token_id=unrelated_token,
        size=9,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-unrelated-confirmed",
        venue_order_id="ord-unrelated-confirmed",
        token_id=unrelated_token,
        trade_id="trade-unrelated-confirmed",
        size="9",
        state="CONFIRMED",
    )
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "late_confirmed_entry_probe"},
        recorded_at=NOW - timedelta(minutes=1),
    )
    stale_trade = record_finding(
        conn,
        kind="unrecorded_trade",
        subject_id="trade-late-confirmed",
        context="ws_gap",
        evidence={"reason": "exchange_trade_missing_fill_economics"},
        recorded_at=NOW - timedelta(minutes=1),
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(
            open_orders=[],
            trades=[
                trade(
                    trade_id="trade-late-confirmed",
                    order_id="ord-late-confirmed",
                    size="5",
                    status="CONFIRMED",
                    include_fill_price=False,
                    taker_order_id="ord-late-confirmed",
                    trader_side="TAKER",
                )
            ],
            positions=[position(token_id=token, size="5")],
        ),
        conn,
        observed_at=NOW,
    )

    assert result["status"] == "resolved"
    assert result["remaining"] == 0
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_cleared",
        "resolved_by": "src.execution.exchange_reconcile",
    }
    resolved_trade = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale_trade.finding_id,),
    ).fetchone()
    assert dict(resolved_trade) == {
        "resolution": "unrecorded_trade_linked",
        "resolved_by": "src.execution.exchange_reconcile",
    }
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM exchange_reconcile_findings
         WHERE subject_id = ?
           AND kind = 'position_drift'
        """,
        (unrelated_token,),
    ).fetchone()[0] == 0
    latest = conn.execute(
        """
        SELECT state
          FROM venue_trade_facts
         WHERE trade_id = 'trade-late-confirmed'
         ORDER BY local_sequence DESC
         LIMIT 1
        """
    ).fetchone()
    assert latest["state"] == "CONFIRMED"


def test_unresolved_reconcile_refresh_resolves_unrecorded_trade_without_position_drift(conn):
    from src.execution.exchange_reconcile import (
        record_finding,
        refresh_unresolved_reconcile_findings,
    )

    seed_command(
        conn,
        command_id="cmd-unrecorded-only",
        venue_order_id="ord-unrecorded-only",
        token_id="unrecorded-only-token",
        size=7,
        state="FILLED",
    )
    stale = record_finding(
        conn,
        kind="unrecorded_trade",
        subject_id="trade-unrecorded-only",
        context="ws_gap",
        evidence={
            "reason": "exchange_trade_missing_fill_economics",
            "local_command": {"venue_order_id": "ord-unrecorded-only"},
        },
        recorded_at=NOW - timedelta(minutes=1),
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(
            open_orders=[],
            trades=[
                trade(
                    trade_id="trade-unrecorded-only",
                    order_id="ord-unrecorded-only",
                    size="7",
                    price="0.42",
                    status="CONFIRMED",
                    include_fill_price=False,
                    taker_order_id="ord-unrecorded-only",
                    trader_side="TAKER",
                )
            ],
            positions=[],
        ),
        conn,
        observed_at=NOW,
    )

    assert result["status"] == "resolved"
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "unrecorded_trade_linked",
        "resolved_by": "src.execution.exchange_reconcile",
    }
    latest = conn.execute(
        """
        SELECT state, filled_size, fill_price
          FROM venue_trade_facts
         WHERE trade_id = 'trade-unrecorded-only'
        """
    ).fetchone()
    assert latest[:] == ("CONFIRMED", "7", "0.42")


def test_unresolved_position_drift_refresh_resolves_pending_exit_offset_after_latch_clear(conn):
    from src.execution.exchange_reconcile import (
        record_finding,
        refresh_unresolved_reconcile_findings,
    )

    token = "refresh-pending-exit-token"
    seed_command(
        conn,
        command_id="cmd-refresh-entry",
        venue_order_id="ord-refresh-entry",
        token_id=token,
        side="BUY",
        size=23.7,
        price=0.07,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-refresh-entry",
        venue_order_id="ord-refresh-entry",
        token_id=token,
        trade_id="trade-refresh-entry",
        size="23.7",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-refresh-exit", order_id="ord-refresh-exit")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               order_id = 'ord-refresh-exit',
               order_status = 'sell_pending_confirmation',
               shares = 23.7,
               cost_basis_usd = 1.659,
               entry_price = 0.07,
               updated_at = ?
         WHERE position_id = 'pos-refresh-exit'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-refresh-exit",
        venue_order_id="ord-refresh-exit",
        position_id="pos-refresh-exit",
        token_id=token,
        side="SELL",
        size=23.7,
        price=0.04,
        state="PARTIAL",
    )
    append_trade_fact(
        conn,
        command_id="cmd-refresh-exit",
        venue_order_id="ord-refresh-exit",
        token_id=token,
        trade_id="trade-refresh-exit",
        size="23.7",
        state="MATCHED",
    )
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "pending_exit_offset_probe"},
        recorded_at=NOW - timedelta(minutes=1),
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[], trades=[], positions=[]),
        conn,
        observed_at=NOW,
    )

    assert result["status"] == "resolved"
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_pending_exit_offset",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_unresolved_position_drift_refresh_nets_confirmed_exit_on_closed_position(conn):
    from src.execution.exchange_reconcile import (
        record_finding,
        refresh_unresolved_reconcile_findings,
    )

    token = "refresh-closed-exit-token"
    seed_command(
        conn,
        command_id="cmd-refresh-closed-entry",
        venue_order_id="ord-refresh-closed-entry",
        position_id="legacy-entry-position",
        token_id=token,
        side="BUY",
        size=14.75,
        price=0.76,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-refresh-closed-entry",
        venue_order_id="ord-refresh-closed-entry",
        token_id=token,
        trade_id="trade-refresh-closed-entry",
        size="14.75",
        state="CONFIRMED",
    )
    seed_position_baseline(
        conn,
        position_id="pos-refresh-closed-exit",
        order_id="ord-refresh-closed-exit",
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'economically_closed',
               token_id = ?,
               order_id = 'ord-refresh-closed-exit',
               order_status = 'sell_filled',
               shares = 14.75,
               cost_basis_usd = 11.21,
               entry_price = 0.76,
               exit_price = 0.75,
               updated_at = ?
         WHERE position_id = 'pos-refresh-closed-exit'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-refresh-closed-exit",
        venue_order_id="ord-refresh-closed-exit",
        position_id="pos-refresh-closed-exit",
        token_id=token,
        side="SELL",
        size=14.75,
        price=0.75,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-refresh-closed-exit",
        venue_order_id="ord-refresh-closed-exit",
        token_id=token,
        trade_id="trade-refresh-closed-exit",
        size="14.75",
        fill_price="0.75",
        state="CONFIRMED",
    )
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "closed_exit_netting_probe"},
        recorded_at=NOW - timedelta(minutes=1),
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[], trades=[], positions=[]),
        conn,
        observed_at=NOW,
    )

    assert result["status"] == "resolved"
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_cleared",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_closed_position_exit_without_historical_entry_does_not_create_negative_wallet_drift(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "closed-exit-without-entry-token"
    seed_position_baseline(conn, position_id="pos-closed-exit-only", order_id="ord-closed-exit-only")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'settled',
               chain_state = 'local_only',
               token_id = ?,
               order_id = 'ord-closed-exit-only',
               order_status = 'sell_filled',
               shares = 35.6,
               updated_at = ?
         WHERE position_id = 'pos-closed-exit-only'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-closed-exit-only",
        venue_order_id="ord-closed-exit-only",
        position_id="pos-closed-exit-only",
        token_id=token,
        side="SELL",
        size=35.6,
        price=0.14,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-closed-exit-only",
        venue_order_id="ord-closed-exit-only",
        token_id=token,
        trade_id="trade-closed-exit-only",
        size="35.6",
        fill_price="0.14",
        state="CONFIRMED",
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert not any(finding.kind == "position_drift" for finding in result)


def test_closed_position_wallet_dust_uses_visibility_floor(conn):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings

    token = "closed-position-wallet-dust-token"
    seed_position_baseline(conn, position_id="pos-closed-wallet-dust", order_id="ord-closed-wallet-dust")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'settled',
               chain_state = 'synced',
               token_id = ?,
               order_id = 'ord-closed-wallet-dust',
               order_status = 'sell_filled',
               shares = 13.157891,
               updated_at = ?
         WHERE position_id = 'pos-closed-wallet-dust'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-closed-wallet-dust",
        venue_order_id="ord-closed-wallet-dust",
        position_id="pos-closed-wallet-dust",
        token_id=token,
        side="SELL",
        size=13.15,
        price=0.17,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-closed-wallet-dust",
        venue_order_id="ord-closed-wallet-dust",
        token_id=token,
        trade_id="trade-closed-wallet-dust",
        size="13.15",
        fill_price="0.17",
        state="CONFIRMED",
    )
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "closed_position_wallet_dust_probe"},
        recorded_at=NOW - timedelta(minutes=1),
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[], trades=[], positions=[]),
        conn,
        observed_at=NOW,
    )

    assert result["status"] == "resolved"
    resolved = conn.execute(
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_cleared",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_recorded_maker_fill_reprojection_does_not_regress_pending_exit_phase(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics, run_reconcile_sweep

    seed_command(conn, state="FILLED", size=23.7, price=0.07)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    maker_trade = TradeFact(
        raw={
            "id": "trade-entry-reprojection",
            "taker_order_id": "ord-other-taker",
            "status": "CONFIRMED",
            "size": "23.7",
            "price": "0.93",
            "transaction_hash": "0xentryreprojection",
            "maker_orders": [
                {
                    "order_id": "ord-m5",
                    "matched_amount": "23.7",
                    "price": "0.07",
                    "asset_id": YES_TOKEN,
                    "side": "BUY",
                }
            ],
        }
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            positions=[position(token_id=YES_TOKEN, size="23.7")],
            trades=[maker_trade],
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )
    assert (
        conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = 'pos-m5'
               AND event_type = 'ENTRY_ORDER_FILLED'
            """
        ).fetchone()[0]
        == 1
    )

    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               order_id = 'ord-exit-m5',
               order_status = 'sell_pending_confirmation',
               shares = 23.7,
               cost_basis_usd = 1.659,
               entry_price = 0.07,
               updated_at = ?
         WHERE position_id = 'pos-m5'
        """,
        ((NOW + timedelta(minutes=1)).isoformat(),),
    )

    summary = reconcile_recorded_maker_fill_economics(
        conn,
        observed_at=NOW + timedelta(minutes=2),
    )
    assert summary == {"scanned": 1, "corrected": 0, "projected": 1, "stayed": 0, "errors": 0}

    current = conn.execute(
        "SELECT phase, order_id, order_status FROM position_current WHERE position_id = 'pos-m5'"
    ).fetchone()
    assert dict(current) == {
        "phase": "pending_exit",
        "order_id": "ord-exit-m5",
        "order_status": "sell_pending_confirmation",
    }


def test_late_entry_fill_does_not_resurrect_terminal_order_remainder(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_order_fact

    seed_command(conn, state="PARTIAL", size=7.21, price=0.28)
    seed_position_baseline(conn)
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="EXPIRED",
        remaining_size="0",
        matched_size="2.11",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"terminal-partial-remainder").hexdigest(),
        raw_payload_json={
            "reason": "partial_remainder_absent_from_exchange_open_orders",
            "point_order_status": "CANCELED",
            "matched_size": "2.11",
        },
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            positions=[position(token_id=YES_TOKEN, size="4.95")],
            trades=[
                trade(
                    trade_id="trade-late-fill",
                    order_id="ord-m5",
                    size="4.95",
                    price="0.28",
                    status="CONFIRMED",
                )
            ],
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=1),
    )

    latest = conn.execute(
        """
        SELECT state, remaining_size, matched_size
          FROM venue_order_facts
         WHERE command_id = 'cmd-m5'
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """
    ).fetchone()
    assert dict(latest) == {
        "state": "EXPIRED",
        "remaining_size": "0",
        "matched_size": "4.95",
    }


def test_reconcile_repairs_historical_terminal_to_partial_order_fact_regression(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_order_fact

    seed_command(conn, state="PARTIAL", size=181.16, price=0.01)
    seed_position_baseline(conn)
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="EXPIRED",
        remaining_size="0",
        matched_size="100",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"historical-terminal-remainder").hexdigest(),
        raw_payload_json={
            "reason": "confirmed_fill_plus_point_order_terminal_remainder",
            "point_order_status": "CANCELED",
            "matched_size": "100",
            "remaining_size": "0",
        },
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            venue_order_id, command_id, state, remaining_size, matched_size,
            source, observed_at, venue_timestamp, local_sequence,
            raw_payload_hash, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ord-m5",
            "cmd-m5",
            "PARTIALLY_MATCHED",
            "81.16",
            "100",
            "REST",
            (NOW + timedelta(minutes=1)).isoformat(),
            (NOW + timedelta(minutes=1)).isoformat(),
            2,
            hashlib.sha256(b"historical-regression").hexdigest(),
            '{"reason":"historical_terminal_to_partial_regression"}',
        ),
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            positions=[position(token_id=YES_TOKEN, size="100")],
            trades=[
                trade(
                    trade_id="trade-historical-terminal-partial",
                    order_id="ord-m5",
                    size="100",
                    price="0.01",
                    status="CONFIRMED",
                )
            ],
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=2),
    )

    latest = conn.execute(
        """
        SELECT state, remaining_size, matched_size
          FROM venue_order_facts
         WHERE command_id = 'cmd-m5'
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """
    ).fetchone()
    assert dict(latest) == {
        "state": "EXPIRED",
        "remaining_size": "0",
        "matched_size": "100",
    }
    fact = conn.execute(
        """
        SELECT venue_status, terminal_exec_status
          FROM execution_fact
         WHERE intent_id = 'pos-m5:entry'
        """
    ).fetchone()
    if fact is not None:
        assert dict(fact) != {"venue_status": "FILLED", "terminal_exec_status": "filled"}


def test_reconcile_repairs_filled_command_terminal_to_partial_regression_without_filling_execfact(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.db import log_execution_fact
    from src.state.venue_command_repo import append_order_fact

    seed_command(conn, state="FILLED", size=181.16, price=0.01)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    append_trade_fact(
        conn,
        trade_id="trade-filled-command-partial",
        size="100",
        fill_price="0.01",
        state="CONFIRMED",
    )
    log_execution_fact(
        conn,
        intent_id="pos-m5:entry",
        position_id="pos-m5",
        decision_id="dec-cmd-m5",
        command_id="cmd-m5",
        order_role="entry",
        strategy_key="opening_inertia",
        posted_at=NOW.isoformat(),
        filled_at=NOW.isoformat(),
        submitted_price=0.01,
        fill_price=0.01,
        shares=100,
        venue_status="PARTIAL",
        terminal_exec_status="partial",
    )
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="EXPIRED",
        remaining_size="0",
        matched_size="100",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"filled-command-terminal-remainder").hexdigest(),
        raw_payload_json={
            "reason": "confirmed_fill_plus_point_order_terminal_remainder",
            "point_order_status": "CANCELED",
            "matched_size": "100",
            "remaining_size": "0",
        },
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            venue_order_id, command_id, state, remaining_size, matched_size,
            source, observed_at, venue_timestamp, local_sequence,
            raw_payload_hash, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ord-m5",
            "cmd-m5",
            "PARTIALLY_MATCHED",
            "81.16",
            "100",
            "WS_USER",
            (NOW + timedelta(minutes=1)).isoformat(),
            (NOW + timedelta(minutes=1)).isoformat(),
            2,
            hashlib.sha256(b"filled-command-regression").hexdigest(),
            '{"reason":"historical_terminal_to_partial_regression"}',
        ),
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            positions=[position(token_id=YES_TOKEN, size="100")],
            trades=[
                trade(
                    trade_id="trade-filled-command-partial",
                    order_id="ord-m5",
                    size="100",
                    price="0.01",
                    status="CONFIRMED",
                )
            ],
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=2),
    )

    latest = conn.execute(
        """
        SELECT state, remaining_size, matched_size
          FROM venue_order_facts
         WHERE command_id = 'cmd-m5'
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """
    ).fetchone()
    assert dict(latest) == {
        "state": "EXPIRED",
        "remaining_size": "0",
        "matched_size": "100",
    }
    fact = conn.execute(
        """
        SELECT venue_status, terminal_exec_status, shares
          FROM execution_fact
         WHERE intent_id = 'pos-m5:entry'
        """
    ).fetchone()
    assert dict(fact) == {
        "venue_status": "PARTIAL",
        "terminal_exec_status": "partial",
        "shares": 100.0,
    }


def test_completed_partial_order_fact_repairs_execution_fact_to_filled(conn):
    from src.execution.command_recovery import (
        reconcile_completed_partial_order_facts,
        reconcile_filled_entry_execution_fact_repairs,
    )
    from src.state.db import log_execution_fact
    from src.state.venue_command_repo import append_order_fact, append_position_lot

    seed_command(conn, state="PARTIAL", size=40.3, price=0.01)
    seed_trade_decision_runtime_alias(conn)
    append_trade_fact(conn, size="40.29", fill_price="0.01", state="CONFIRMED")
    append_position_lot(
        conn,
        position_id=1,
        state="CONFIRMED_EXPOSURE",
        shares="40.29",
        entry_price_avg="0.01",
        source_command_id="cmd-m5",
        source_trade_fact_id=1,
        captured_at=NOW.isoformat(),
        state_changed_at=NOW.isoformat(),
        source="REST",
        observed_at=NOW.isoformat(),
        raw_payload_hash=hashlib.sha256(b"completed-partial-lot").hexdigest(),
        raw_payload_json={"source": "test_completed_partial_order_fact"},
    )
    log_execution_fact(
        conn,
        intent_id="pos-m5:entry",
        position_id="pos-m5",
        decision_id="dec-cmd-m5",
        command_id="cmd-m5",
        order_role="entry",
        strategy_key="opening_inertia",
        posted_at=NOW.isoformat(),
        filled_at=NOW.isoformat(),
        submitted_price=0.01,
        fill_price=0.01,
        shares=40.29,
        venue_status="PARTIAL",
        terminal_exec_status="partial",
    )
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="MATCHED",
        remaining_size="0",
        matched_size="40.29",
        source="REST",
        observed_at=NOW + timedelta(minutes=1),
        raw_payload_hash=hashlib.sha256(b"completed-partial-order-fact").hexdigest(),
        raw_payload_json={"status": "MATCHED", "matched_size": "40.29", "remaining_size": "0"},
    )

    completion = reconcile_completed_partial_order_facts(conn)
    assert completion == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()[0] == "FILLED"

    repair = reconcile_filled_entry_execution_fact_repairs(conn)
    assert repair == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
    fact = conn.execute(
        """
        SELECT command_id, venue_status, terminal_exec_status, shares, fill_price, filled_at
          FROM execution_fact
         WHERE intent_id = 'pos-m5:entry'
        """
    ).fetchone()
    assert dict(fact) == {
        "command_id": "cmd-m5",
        "venue_status": "FILLED",
        "terminal_exec_status": "filled",
        "shares": 40.29,
        "fill_price": 0.01,
        "filled_at": NOW.isoformat(),
    }


def test_recorded_maker_reprojection_preserves_terminal_execution_fact(conn):
    from src.execution.command_recovery import (
        reconcile_completed_partial_order_facts,
        reconcile_filled_entry_execution_fact_repairs,
    )
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.db import log_execution_fact
    from src.state.venue_command_repo import (
        append_order_fact,
        append_position_lot,
        append_trade_fact as append_venue_trade_fact,
    )

    seed_command(conn, state="PARTIAL", size=40.3, price=0.01)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    raw = {
        "id": "trade-terminal-maker-fill",
        "status": "CONFIRMED",
        "taker_order_id": "foreign-taker",
        "size": "40.29",
        "price": "0.99",
        "maker_orders": [
            {
                "order_id": "ord-m5",
                "matched_amount": "40.29",
                "price": "0.01",
                "asset_id": YES_TOKEN,
                "side": "BUY",
            }
        ],
    }
    trade_fact_id = append_venue_trade_fact(
        conn,
        trade_id="trade-terminal-maker-fill",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="40.29",
        fill_price="0.01",
        source="WS_USER",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"terminal-maker-fill").hexdigest(),
        raw_payload_json=raw,
    )
    append_position_lot(
        conn,
        position_id=1,
        state="CONFIRMED_EXPOSURE",
        shares="40.29",
        entry_price_avg="0.01",
        source_command_id="cmd-m5",
        source_trade_fact_id=trade_fact_id,
        captured_at=NOW.isoformat(),
        state_changed_at=NOW.isoformat(),
        source="WS_USER",
        observed_at=NOW.isoformat(),
        raw_payload_hash=hashlib.sha256(b"terminal-maker-lot").hexdigest(),
        raw_payload_json={"source": "test_terminal_maker_lot"},
    )
    log_execution_fact(
        conn,
        intent_id="pos-m5:entry",
        position_id="pos-m5",
        decision_id="dec-cmd-m5",
        command_id="cmd-m5",
        order_role="entry",
        strategy_key="opening_inertia",
        posted_at=NOW.isoformat(),
        filled_at=NOW.isoformat(),
        submitted_price=0.01,
        fill_price=0.01,
        shares=40.29,
        venue_status="PARTIAL",
        terminal_exec_status="partial",
    )
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="MATCHED",
        remaining_size="0",
        matched_size="40.29",
        source="WS_USER",
        observed_at=NOW + timedelta(seconds=1),
        raw_payload_hash=hashlib.sha256(b"terminal-maker-order").hexdigest(),
        raw_payload_json={"status": "MATCHED", "matched_size": "40.29", "remaining_size": "0"},
    )

    assert reconcile_completed_partial_order_facts(conn) == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    assert reconcile_filled_entry_execution_fact_repairs(conn) == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    assert reconcile_recorded_maker_fill_economics(conn, observed_at=NOW + timedelta(seconds=5)) == {
        "scanned": 1,
        "corrected": 0,
        "projected": 1,
        "stayed": 0,
        "errors": 0,
    }

    fact = conn.execute(
        """
        SELECT command_id, venue_status, terminal_exec_status, shares, fill_price
          FROM execution_fact
         WHERE intent_id = 'pos-m5:entry'
        """
    ).fetchone()
    assert dict(fact) == {
        "command_id": "cmd-m5",
        "venue_status": "FILLED",
        "terminal_exec_status": "filled",
        "shares": 40.29,
        "fill_price": 0.01,
    }


def test_position_journal_ignores_confirmed_trade_without_fill_economics(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    malformed_token = "malformed-confirmed-token"
    seed_command(
        conn,
        command_id="cmd-malformed-confirmed",
        venue_order_id="ord-malformed-confirmed",
        token_id=malformed_token,
    )
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            trade_id, venue_order_id, command_id, state, filled_size,
            fill_price, source, observed_at, local_sequence,
            raw_payload_hash, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "trade-malformed-confirmed",
            "ord-malformed-confirmed",
            "cmd-malformed-confirmed",
            "CONFIRMED",
            "10",
            "0",
            "CHAIN",
            NOW.isoformat(),
            1,
            "f" * 64,
            '{"state":"CONFIRMED","source":"direct-sql-test"}',
        ),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=malformed_token, size="10")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    position_findings = [finding for finding in result if finding.kind == "position_drift"]
    assert [finding.subject_id for finding in position_findings] == [malformed_token]
    assert '"journal_size":"0"' in position_findings[0].evidence_json
    assert '"exchange_size":"10"' in position_findings[0].evidence_json


def test_heartbeat_suspected_cancel_finding_emitted_after_heartbeat_loss(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    append_resting_order_fact(conn)

    result = run_reconcile_sweep(
        FakeM5Adapter(open_orders=[], trades=[]),
        conn,
        context="heartbeat_loss",
        observed_at=NOW,
    )

    assert [finding.kind for finding in result] == ["heartbeat_suspected_cancel"]
    assert command_count(conn) == 1
    assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "ACKED"


def test_cutover_wipe_findings_emitted_in_POST_CUTOVER_RECONCILE_state(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    append_resting_order_fact(conn)

    result = run_reconcile_sweep(
        FakeM5Adapter(open_orders=[], trades=[]),
        conn,
        context="cutover",
        observed_at=NOW,
    )

    assert [finding.kind for finding in result] == ["cutover_wipe"]
    assert "local_open_order_absent" in result[0].evidence_json


def test_get_trades_sdk_method_used_when_available_else_position_diff_fallback(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn)
    adapter = FakeM5Adapter(trades=[trade(trade_id="trade-linked", order_id="ord-m5", size="10")])
    run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)
    assert any(call[0] == "get_trades" for call in adapter.calls)
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id = 'trade-linked'").fetchone()[0] == 1

    fallback = FakeAdapterWithoutTrades(positions=[position(token_id="unknown-position-token", size="4")])
    result = run_reconcile_sweep(fallback, conn, context="periodic", observed_at=NOW)
    assert not any(call[0] == "get_trades" for call in fallback.calls)
    assert any(finding.kind == "position_drift" for finding in result)
    assert not any(finding.kind == "unrecorded_trade" for finding in result)


def test_missing_positions_surface_is_not_position_absence_proof(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=10)
    append_trade_fact(conn, command_id="cmd-m5", venue_order_id="ord-m5", size="10")

    result = run_reconcile_sweep(
        FakeAdapterWithoutPositions(
            open_orders=[],
            trades=[trade(trade_id="trade-local", order_id="ord-m5", size="10", status="CONFIRMED")],
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert not any(finding.kind == "position_drift" for finding in result)


def test_ws_gap_m5_sweep_clears_latch_for_fresh_open_partial_order(conn):
    from src.control import ws_gap_guard
    from src.execution.exchange_reconcile import run_ws_gap_reconcile_and_clear

    seed_command(conn, size=5)
    conn.execute("UPDATE venue_commands SET state = 'PARTIAL' WHERE command_id = 'cmd-m5'")
    configure_subscribed_m5_latch()

    try:
        result = run_ws_gap_reconcile_and_clear(
            FakeM5Adapter(open_orders=[order(order_id="ord-m5")], trades=[], positions=[]),
            conn,
            observed_at=NOW,
        )

        assert result["status"] == "cleared"
        assert result["captured_surfaces"] == ["open_orders", "positions", "trades"]
        assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is True
        assert ws_gap_guard.status().m5_reconcile_required is False
        assert conn.execute("SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'").fetchone()["state"] == "PARTIAL"
        assert findings(conn) == []
    finally:
        ws_gap_guard.clear_for_test(observed_at=NOW)


def test_ws_gap_m5_sweep_filters_unrelated_wallet_trade_history(conn):
    from src.control import ws_gap_guard
    from src.execution.exchange_reconcile import run_ws_gap_reconcile_and_clear

    seed_command(conn, size=5)
    conn.execute("UPDATE venue_commands SET state = 'PARTIAL' WHERE command_id = 'cmd-m5'")
    configure_subscribed_m5_latch()

    try:
        result = run_ws_gap_reconcile_and_clear(
            FakeM5Adapter(
                open_orders=[order(order_id="ord-m5")],
                trades=[trade(trade_id="historical-unrelated", order_id="old-wallet-order")],
                positions=[],
            ),
            conn,
            observed_at=NOW,
        )

        assert result["status"] == "cleared"
        assert ws_gap_guard.status().m5_reconcile_required is False
        assert findings(conn) == []
    finally:
        ws_gap_guard.clear_for_test(observed_at=NOW)


def test_ws_gap_m5_sweep_keeps_latch_closed_when_findings_remain(conn):
    from src.control import ws_gap_guard
    from src.execution.exchange_reconcile import run_ws_gap_reconcile_and_clear

    seed_command(conn, size=5)
    append_resting_order_fact(conn)
    configure_subscribed_m5_latch()

    try:
        result = run_ws_gap_reconcile_and_clear(
            FakeM5Adapter(open_orders=[], trades=[], positions=[]),
            conn,
            observed_at=NOW,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "m5_findings_unresolved"
        assert result["findings"] == 1
        assert ws_gap_guard.status().m5_reconcile_required is True
        assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is False
        assert findings(conn)[0]["kind"] == "local_orphan_order"
    finally:
        ws_gap_guard.clear_for_test(observed_at=NOW)


def test_ws_gap_empty_global_open_orders_but_point_order_live_clears_latch(conn):
    from src.control import ws_gap_guard
    from src.execution.exchange_reconcile import run_ws_gap_reconcile_and_clear

    seed_command(conn, size=5)
    append_resting_order_fact(conn)
    configure_subscribed_m5_latch()
    adapter = FakeM5AdapterWithPointOrders(
        open_orders=[],
        trades=[],
        positions=[],
        orders_by_id={"ord-m5": order(order_id="ord-m5", status="LIVE")},
    )

    try:
        result = run_ws_gap_reconcile_and_clear(adapter, conn, observed_at=NOW)

        assert result["status"] == "cleared"
        assert result["captured_surfaces"] == ["open_orders", "point_orders", "positions", "trades"]
        assert ws_gap_guard.status().m5_reconcile_required is False
        assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is True
        assert findings(conn) == []
        assert ("get_order", ("ord-m5",), {}) in adapter.calls
    finally:
        ws_gap_guard.clear_for_test(observed_at=NOW)


def test_ws_gap_m5_sweep_requires_fresh_trade_enumeration_before_clear(conn):
    from src.control import ws_gap_guard
    from src.execution.exchange_reconcile import run_ws_gap_reconcile_and_clear

    seed_command(conn, size=5)
    conn.execute("UPDATE venue_commands SET state = 'PARTIAL' WHERE command_id = 'cmd-m5'")
    configure_subscribed_m5_latch()

    try:
        result = run_ws_gap_reconcile_and_clear(
            FakeAdapterWithoutTrades(open_orders=[order(order_id="ord-m5")], positions=[]),
            conn,
            observed_at=NOW,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "trades_read_unavailable"
        assert result["unavailable_surfaces"] == ["trades"]
        assert ws_gap_guard.status().m5_reconcile_required is True
        assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is False
        assert findings(conn) == []
    finally:
        ws_gap_guard.clear_for_test(observed_at=NOW)


def test_ws_gap_m5_sweep_does_not_clear_latch_when_durable_commit_fails():
    from src.control import ws_gap_guard
    from src.execution.exchange_reconcile import run_ws_gap_reconcile_and_clear
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:", factory=FailingCommitConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    init_collateral_schema(conn)
    seed_command(conn, size=5)
    append_resting_order_fact(conn)
    conn.commit()
    configure_subscribed_m5_latch()

    try:
        conn.fail_commit = True
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            run_ws_gap_reconcile_and_clear(
                FakeM5Adapter(
                    open_orders=[order(order_id="ord-m5")],
                    trades=[trade(trade_id="trade-m5", order_id="ord-m5", size="1.25")],
                    positions=[],
                ),
                conn,
                observed_at=NOW,
            )

        assert ws_gap_guard.status().m5_reconcile_required is True
        assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is False
    finally:
        ws_gap_guard.clear_for_test(observed_at=NOW)
        conn.close()


def test_live_heartbeat_runs_ws_gap_m5_sweep_without_closing_external_test_conn(conn):
    import src.main as main_module
    from src.control import ws_gap_guard

    seed_command(conn, size=5)
    conn.execute("UPDATE venue_commands SET state = 'PARTIAL' WHERE command_id = 'cmd-m5'")
    configure_subscribed_m5_latch()

    try:
        result = main_module._run_ws_gap_reconcile_if_required(
            FakeM5Adapter(open_orders=[order(order_id="ord-m5")], trades=[], positions=[]),
            conn_factory=lambda: conn,
            now=NOW,
        )

        assert result["status"] == "cleared"
        assert ws_gap_guard.status().m5_reconcile_required is False
        conn.execute("SELECT 1").fetchone()
    finally:
        ws_gap_guard.clear_for_test(observed_at=NOW)


def test_m5_clear_releases_ws_gap_blocked_exit_retry(conn):
    import src.main as main_module
    from src.control import ws_gap_guard

    seed_command(conn, size=5)
    conn.execute("UPDATE venue_commands SET state = 'PARTIAL' WHERE command_id = 'cmd-m5'")
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, bin_label, direction,
            shares, chain_shares, chain_state, strategy_key, updated_at,
            temperature_metric, exit_retry_count, next_exit_retry_at
        ) VALUES (?, 'pending_exit', 'Hong Kong', '2026-06-19', '21C', 'buy_no',
                  12.0, 12.0, 'synced', 'opening_inertia', ?, 'low', 4, ?)
        """,
        (
            "exit-ws-gap",
            (NOW - timedelta(minutes=1)).isoformat(),
            (NOW + timedelta(minutes=40)).isoformat(),
        ),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, source_module, payload_json, env
        ) VALUES (?, ?, 1, 'EXIT_ORDER_REJECTED', ?, 'active', 'pending_exit',
                  'opening_inertia', 'src.execution.exit_lifecycle', ?, 'live')
        """,
        (
            "exit-ws-gap-rejected",
            "exit-ws-gap",
            (NOW - timedelta(seconds=30)).isoformat(),
            json.dumps({"error": "ws_gap=SUBSCRIBED:message_received; m5_reconcile_required=True"}),
        ),
    )
    configure_subscribed_m5_latch()

    try:
        result = main_module._run_ws_gap_reconcile_if_required(
            FakeM5Adapter(open_orders=[order(order_id="ord-m5")], trades=[], positions=[]),
            conn_factory=lambda: conn,
            now=NOW,
        )

        retry_at = conn.execute(
            "SELECT next_exit_retry_at FROM position_current WHERE position_id = ?",
            ("exit-ws-gap",),
        ).fetchone()[0]
        assert result["status"] == "cleared"
        assert result["exit_retries_released"] == 1
        assert result["exit_retry_position_ids"] == ["exit-ws-gap"]
        assert retry_at == NOW.isoformat()
        release = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, venue_status, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            ("exit-ws-gap",),
        ).fetchone()
        assert release["event_type"] == "EXIT_RETRY_RELEASED"
        assert release["phase_before"] == "pending_exit"
        assert release["phase_after"] == "pending_exit"
        assert release["venue_status"] == "ready"
        payload = json.loads(release["payload_json"])
        assert payload["release_reason"] == "M5_WS_GAP_RECONCILE_CLEARED"
        assert payload["previous_next_retry_at"] > NOW.isoformat()
        assert payload["next_retry_at"] == NOW.isoformat()
    finally:
        ws_gap_guard.clear_for_test(observed_at=NOW)


def test_allocator_refresh_release_updates_db_and_loaded_position(conn):
    # R4-b (2026-07-08): moved from src.main to its owning module (single
    # caller was src.main._exit_monitor_cycle, also moved there).
    from src.execution import exit_lifecycle as main_module

    position = SimpleNamespace(
        trade_id="exit-allocator-config",
        next_exit_retry_at=(NOW + timedelta(minutes=40)).isoformat(),
    )
    portfolio = SimpleNamespace(positions=[position])
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, bin_label, direction,
            shares, chain_shares, chain_state, strategy_key, updated_at,
            temperature_metric, exit_retry_count, next_exit_retry_at
        ) VALUES (?, 'pending_exit', 'Chengdu', '2026-06-19', '33C', 'buy_no',
                  15.25, 15.25, 'synced', 'opening_inertia', ?, 'high', 5, ?)
        """,
        (
            "exit-allocator-config",
            (NOW - timedelta(minutes=1)).isoformat(),
            (NOW + timedelta(minutes=40)).isoformat(),
        ),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, source_module, payload_json, env
        ) VALUES (?, ?, 1, 'EXIT_ORDER_REJECTED', ?, 'active', 'pending_exit',
                  'opening_inertia', 'src.execution.exit_lifecycle', ?, 'live')
        """,
        (
            "exit-allocator-config-rejected",
            "exit-allocator-config",
            (NOW - timedelta(seconds=30)).isoformat(),
            json.dumps({"error": "allocator_not_configured"}),
        ),
    )

    result = main_module._release_allocator_config_blocked_exit_retries_after_refresh(
        conn,
        portfolio,
        observed_at=NOW,
    )

    retry_at = conn.execute(
        "SELECT next_exit_retry_at FROM position_current WHERE position_id = ?",
        ("exit-allocator-config",),
    ).fetchone()[0]
    assert result == {"released": 1, "position_ids": ["exit-allocator-config"]}
    assert retry_at == NOW.isoformat()
    assert position.next_exit_retry_at == NOW.isoformat()
    release = conn.execute(
        """
        SELECT event_type, phase_before, phase_after, venue_status, payload_json
          FROM position_events
         WHERE position_id = ?
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        ("exit-allocator-config",),
    ).fetchone()
    assert release["event_type"] == "EXIT_RETRY_RELEASED"
    assert release["phase_before"] == "pending_exit"
    assert release["phase_after"] == "pending_exit"
    assert release["venue_status"] == "ready"
    payload = json.loads(release["payload_json"])
    assert payload["release_reason"] == "ALLOCATOR_CONFIGURED_AFTER_REFRESH"
    assert payload["previous_next_retry_at"] > NOW.isoformat()
    assert payload["next_retry_at"] == NOW.isoformat()


def test_findings_actuator_loop_resolves_findings_via_operator_decision(conn):
    from src.execution.exchange_reconcile import (
        list_unresolved_findings,
        resolve_finding,
        run_reconcile_sweep,
    )

    [finding] = run_reconcile_sweep(
        FakeM5Adapter(open_orders=[order(order_id="ord-ghost")]),
        conn,
        context="operator",
        observed_at=NOW,
    )
    assert [row.finding_id for row in list_unresolved_findings(conn)] == [finding.finding_id]

    resolve_finding(
        conn,
        finding.finding_id,
        resolution="operator_acknowledged",
        resolved_by="operator-test",
        resolved_at=NOW,
    )

    assert list_unresolved_findings(conn) == []
    row = conn.execute(
        "SELECT resolved_at, resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
        (finding.finding_id,),
    ).fetchone()
    assert row["resolved_at"] is not None
    assert row["resolution"] == "operator_acknowledged"
    assert row["resolved_by"] == "operator-test"
    with pytest.raises(ValueError):
        resolve_finding(conn, "missing", resolution="operator_acknowledged", resolved_by="operator-test")


# ---------------------------------------------------------------------------- #
# M5 mutex-IO antibody (2026-06-04): the reconcile sweep performs BLOCKING venue
# reads. Holding the world write mutex across it is the lock-starvation disease
# (STEP-7 / #95 / the M5 wedge). RELATIONSHIP TEST across the (world-mutex,
# reconcile-venue-read) boundary: the sweep must fail loud, not wedge.
# ---------------------------------------------------------------------------- #


def test_run_reconcile_sweep_raises_when_world_mutex_held(conn):
    """When a caller holds the world write mutex and enters run_reconcile_sweep
    (which issues blocking venue reads), the guard raises WorldMutexIOViolation
    instead of letting the venue I/O wedge the held world txn."""
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.db import WorldMutexIOViolation, world_write_mutex

    adapter = FakeM5Adapter(open_orders=[order(order_id="ord-ghost", status="LIVE")])
    mutex = world_write_mutex()
    mutex.acquire()
    try:
        with pytest.raises(WorldMutexIOViolation):
            run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)
    finally:
        mutex.release()


def test_run_reconcile_sweep_proceeds_off_the_world_mutex(conn):
    """REGRESSION: the correct off-lock reconcile path (no world mutex held) runs
    the sweep normally — the antibody does not break legitimate reconcile."""
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.db import world_mutex_is_held

    assert world_mutex_is_held() is False
    adapter = FakeM5Adapter(open_orders=[order(order_id="ord-ghost", status="LIVE")])
    result = run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)
    assert len(result) == 1
    assert result[0].kind == "exchange_ghost_order"


# ---------------------------------------------------------------------------- #
# SCH-W1.1-CAS-LEDGER: type-aware A4 collateral identity checker + the
# terminalization-centrality invariant's write-gate carve-out guard.
# ---------------------------------------------------------------------------- #


def _insert_collateral_test_command(
    conn: sqlite3.Connection,
    command_id: str,
    *,
    token_id: str,
    state: str,
    intent_kind: str = "EXIT",
    side: str = "BUY",
    size: float = 10.0,
    price: float = 0.5,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id, f"snap-{command_id}", f"env-{command_id}", f"pos-{command_id}",
            f"dec-{command_id}", f"idem-{command_id}", intent_kind, "z4-market",
            token_id, side, size, price, state, now, now,
        ),
    )


def test_check_collateral_identity_orphan_sweep_records_finding(conn):
    """A4 orphan sweep: a live (unreleased, unconverted) reservation attached
    to a terminal command records a collateral_identity_mismatch finding —
    defense in depth if a terminalization path bypasses append_event."""
    from src.execution.exchange_reconcile import check_collateral_identity
    from src.state.collateral_ledger import init_collateral_schema

    init_collateral_schema(conn)
    command_id = "orphan-cmd"
    _insert_collateral_test_command(conn, command_id, token_id=YES_TOKEN, state="FILLED")
    conn.execute(
        """
        INSERT INTO collateral_reservations
          (command_id, reservation_type, token_id, amount, converted_amount, created_at)
        VALUES (?, 'PUSD_BUY', NULL, 5000000, 0, ?)
        """,
        (command_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    findings = check_collateral_identity(conn, context="periodic", observed_at=NOW)

    assert len(findings) == 1
    assert findings[0].kind == "collateral_identity_mismatch"
    assert findings[0].subject_id == command_id
    assert json.loads(findings[0].evidence_json)["reason"] == "orphan_reservation_on_terminal_command"


def test_check_collateral_identity_auto_resolves_on_clean_recheck(conn):
    """Auto-resolve (critic ruling 4): once the orphan reservation is released,
    the NEXT clean check resolves the prior finding via
    resolution='auto_clean_recheck' — a transient mismatch never becomes a
    sticky halt."""
    from src.execution.exchange_reconcile import (
        check_collateral_identity,
        list_unresolved_findings,
    )
    from src.state.collateral_ledger import init_collateral_schema

    init_collateral_schema(conn)
    command_id = "orphan-cmd-autoresolve"
    _insert_collateral_test_command(conn, command_id, token_id=YES_TOKEN, state="FILLED")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO collateral_reservations
          (command_id, reservation_type, token_id, amount, converted_amount, created_at)
        VALUES (?, 'PUSD_BUY', NULL, 5000000, 0, ?)
        """,
        (command_id, now),
    )
    conn.commit()

    findings = check_collateral_identity(conn, context="periodic", observed_at=NOW)
    assert len(findings) == 1
    unresolved = list_unresolved_findings(conn, kind="collateral_identity_mismatch")
    assert len(unresolved) == 1

    conn.execute(
        "UPDATE collateral_reservations SET released_at = ?, release_reason = 'FILLED' WHERE command_id = ?",
        (now, command_id),
    )
    conn.commit()

    findings2 = check_collateral_identity(conn, context="periodic", observed_at=NOW + timedelta(seconds=1))
    assert findings2 == []
    unresolved2 = list_unresolved_findings(conn, kind="collateral_identity_mismatch")
    assert unresolved2 == []


def test_check_collateral_identity_zero_findings_under_fill_cancel_settle_storm(conn):
    """IDENTITY STORM (acceptance): fill/partial-fill/cancel/settle storm —
    after every event the type-aware reconstruction holds; A4 checker zero
    findings."""
    from src.execution.exchange_reconcile import check_collateral_identity
    from src.state.collateral_ledger import CollateralLedger, init_collateral_schema
    from src.state.venue_command_repo import append_event, append_order_fact

    init_collateral_schema(conn)
    ledger = CollateralLedger(conn)
    balance_time = datetime.now(timezone.utc)
    ledger.set_snapshot(
        _collateral_storm_snapshot(pusd=200_000_000, captured_at=balance_time)
    )

    payload = {
        "execution_capability": {
            "allowed": True,
            "components": [
                {"component": "entry_economics", "allowed": True},
                {"component": "entry_actionable_certificate", "allowed": True},
            ],
        }
    }

    # Command A: fully filled.
    _insert_collateral_test_command(conn, "storm-a", token_id=YES_TOKEN, state="INTENT_CREATED")
    ledger.reserve_pusd_for_buy("storm-a", 5_000_000)
    now = datetime.now(timezone.utc).isoformat()
    append_event(conn, command_id="storm-a", event_type="SUBMIT_REQUESTED", occurred_at=now, payload=payload)
    append_event(conn, command_id="storm-a", event_type="SUBMIT_ACKED", occurred_at=now)
    append_order_fact(
        conn, venue_order_id="storm-a-vo", command_id="storm-a", state="MATCHED",
        remaining_size="0", matched_size="10", source="WS_USER",
        observed_at=datetime.now(timezone.utc), raw_payload_hash="1" * 64,
    )
    append_event(conn, command_id="storm-a", event_type="FILL_CONFIRMED", occurred_at=now)

    # Command B: partial fill then cancel.
    _insert_collateral_test_command(conn, "storm-b", token_id=YES_TOKEN, state="INTENT_CREATED")
    ledger.reserve_pusd_for_buy("storm-b", 5_000_000)
    append_event(conn, command_id="storm-b", event_type="SUBMIT_REQUESTED", occurred_at=now, payload=payload)
    append_event(conn, command_id="storm-b", event_type="SUBMIT_ACKED", occurred_at=now)
    append_order_fact(
        conn, venue_order_id="storm-b-vo", command_id="storm-b", state="PARTIALLY_MATCHED",
        remaining_size="6", matched_size="4", source="WS_USER",
        observed_at=datetime.now(timezone.utc), raw_payload_hash="2" * 64,
    )
    append_event(conn, command_id="storm-b", event_type="PARTIAL_FILL_OBSERVED", occurred_at=now)
    append_event(conn, command_id="storm-b", event_type="CANCEL_REQUESTED", occurred_at=now)
    append_event(conn, command_id="storm-b", event_type="CANCEL_ACKED", occurred_at=now)

    # Command C: rejected before any venue exposure (zero-fill).
    _insert_collateral_test_command(conn, "storm-c", token_id=YES_TOKEN, state="INTENT_CREATED")
    ledger.reserve_pusd_for_buy("storm-c", 5_000_000)
    append_event(conn, command_id="storm-c", event_type="SUBMIT_REQUESTED", occurred_at=now, payload=payload)
    append_event(conn, command_id="storm-c", event_type="SUBMIT_REJECTED", occurred_at=now)

    # Settle: a later balance snapshot clears the matured unsettled rows.
    settle_time = balance_time + timedelta(seconds=200)
    ledger.set_snapshot(_collateral_storm_snapshot(pusd=192_000_000, captured_at=settle_time))

    findings = check_collateral_identity(conn, context="periodic", observed_at=settle_time)
    assert findings == []


def _collateral_storm_snapshot(*, pusd: int, captured_at: datetime):
    from src.state.collateral_ledger import CollateralSnapshot

    return CollateralSnapshot(
        pusd_balance_micro=pusd,
        pusd_allowance_micro=pusd,
        usdc_e_legacy_balance_micro=0,
        ctf_token_balances={},
        ctf_token_allowances={},
        reserved_pusd_for_buys_micro=0,
        reserved_tokens_for_sells={},
        captured_at=captured_at,
        authority_tier="CHAIN",
    )


def test_external_operator_close_carve_out_guard_raises_on_live_reservation(conn):
    """Terminalization-centrality invariant (INV-42): the synthetic
    external-close direct write is the SOLE carve-out, guarded by a write-gate
    assertion that no live reservation exists for that command_id — an
    externally-closed foreign order was never reserve-backed by Zeus."""
    from hashlib import sha256

    from src.execution.exchange_reconcile import _book_external_operator_close_exit_fact
    from src.state.collateral_ledger import init_collateral_schema

    init_collateral_schema(conn)
    token_id = "carve-out-token"
    entry_command_id = "entry-cmd-carve-out"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'ENTRY', 'z4-market', ?, 'BUY', 10.0, 0.5, 'FILLED', ?, ?)
        """,
        (entry_command_id, "snap-carve", "env-carve", "pos-carve", "dec-carve", "idem-carve", token_id, now, now),
    )

    synthetic_command_id = "external_operator_close:" + sha256(token_id.encode()).hexdigest()[:24]
    conn.execute(
        """
        INSERT INTO collateral_reservations
          (command_id, reservation_type, token_id, amount, converted_amount, created_at)
        VALUES (?, 'CTF_SELL', ?, 1000000, 0, ?)
        """,
        (synthetic_command_id, token_id, now),
    )
    conn.commit()

    with pytest.raises(AssertionError, match="terminalization_centrality_violation"):
        _book_external_operator_close_exit_fact(
            conn,
            token_id=token_id,
            close_size=Decimal("10"),
            close_price=Decimal("0.5"),
            observed_at=datetime.now(timezone.utc),
        )


def test_external_operator_close_carve_out_proceeds_without_live_reservation(conn):
    """REGRESSION: the carve-out's normal path (no reserve-backed command for
    the synthetic id) is unaffected by the new write-gate guard."""
    from hashlib import sha256

    from src.execution.exchange_reconcile import _book_external_operator_close_exit_fact
    from src.state.collateral_ledger import init_collateral_schema

    init_collateral_schema(conn)
    token_id = "carve-out-token-clean"
    entry_command_id = "entry-cmd-carve-out-clean"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'ENTRY', 'z4-market', ?, 'BUY', 10.0, 0.5, ?, 'FILLED', ?, ?)
        """,
        (entry_command_id, "snap-c", "env-c", "pos-c", "dec-c", "idem-c", token_id, "vo-entry-c", now, now),
    )
    conn.commit()

    booked = _book_external_operator_close_exit_fact(
        conn,
        token_id=token_id,
        close_size=Decimal("10"),
        close_price=Decimal("0.5"),
        observed_at=datetime.now(timezone.utc),
    )
    assert booked is True

    synthetic_command_id = "external_operator_close:" + sha256(token_id.encode()).hexdigest()[:24]
    row = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = ?", (synthetic_command_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "FILLED"


def test_external_operator_close_synthetic_exit_command_has_null_q_version(conn):
    """SCH-W1.2-ORDER-STATE: _book_external_operator_close_exit_fact
    (exchange_reconcile.py:1660) writes venue_commands via a DIRECT INSERT, not
    insert_command() — its column list omits q_version, so the synthetic EXIT
    command is NULL BY RULE ("not Zeus's decision basis"), never inherited from
    the entry it reuses provenance FKs from."""
    from decimal import Decimal

    from src.execution.exchange_reconcile import _book_external_operator_close_exit_fact

    token = "external-close-token"
    seed_command(
        conn,
        command_id="cmd-entry-external-close",
        venue_order_id="ord-entry-external-close",
        position_id="pos-external-close",
        token_id=token,
        side="BUY",
        size=10.0,
        price=0.5,
        state="FILLED",
        q_version="entry-q-version-must-not-propagate",
    )

    booked = _book_external_operator_close_exit_fact(
        conn,
        token_id=token,
        close_size=Decimal("10.0"),
        close_price=Decimal("0.5"),
        observed_at=NOW,
    )
    assert booked is True

    row = conn.execute(
        """
        SELECT intent_kind, side, state, q_version
          FROM venue_commands
         WHERE command_id = ?
        """,
        ("external_operator_close:" + hashlib.sha256(token.encode()).hexdigest()[:24],),
    ).fetchone()
    assert row is not None
    assert row["intent_kind"] == "EXIT"
    assert row["side"] == "SELL"
    assert row["q_version"] is None
