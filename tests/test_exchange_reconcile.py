# Created: 2026-04-27
# Last reused/audited: 2026-09-03
# Lifecycle: created=2026-04-27; last_reviewed=2026-09-03; last_reused=2026-09-03
# Authority basis: first-principles command-scoped entry/exit fill aggregation
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
    from src.state.db import init_schema, init_schema_trade_only

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    init_schema_trade_only(c)
    # Single-connection fixture plays both DB roles; world init_schema no
    # longer creates trade-class collateral tables (K1 split, 1b51db387).
    init_collateral_schema(c)
    # Current ENTRY admission requires a sanctioned WORLD authority attachment
    # and an exact LIVE/VERIFIED ActionableTradeCertificate.  Keep this
    # in-memory fixture topology identical to the repository seam tests.
    c.execute("ATTACH DATABASE ':memory:' AS world")
    c.execute(
        """
        CREATE TABLE world.decision_certificates (
            certificate_hash TEXT PRIMARY KEY,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
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
    authenticated_point_reads_are_complete = True

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
    min_order_size: Decimal = Decimal("0.01"),
    captured_at: datetime = NOW,
    freshness_deadline: datetime | None = None,
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
            min_order_size=min_order_size,
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
            captured_at=captured_at,
            freshness_deadline=(
                freshness_deadline
                if freshness_deadline is not None
                else NOW + timedelta(days=365)
            ),
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
    order_type: str = "GTC",
    post_only: bool | None = None,
    persist: bool = True,
    return_envelope: bool = False,
):
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    price_dec = Decimal(str(price))
    size_dec = Decimal(str(size))
    yes_token_id = yes_token_id or token_id
    no_token_id = no_token_id or f"{yes_token_id}-no"
    envelope_id = envelope_id or hashlib.sha256(
        f"{token_id}:{side}:{price_dec}:{size_dec}:{order_type}:{post_only}".encode()
    ).hexdigest()
    if persist and c.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone():
        return envelope_id
    # Persist legal live execution modes: maker GTC/GTD, or certified taker
    # BUY FOK/FAK and SELL FAK.  Explicit overrides remain available only for
    # tests that intentionally exercise a lower-level invalid-envelope gate.
    if post_only is None:
        post_only = order_type.upper() in {"GTC", "GTD"}
    envelope = VenueSubmissionEnvelope(
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
            outcome_label="YES" if token_id == yes_token_id else "NO",
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
            signed_order_hash=None,
            raw_request_hash="e" * 64,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=NOW.isoformat(),
        )
    if persist:
        insert_submission_envelope(c, envelope, envelope_id=envelope_id)
        return envelope_id
    return (envelope_id, envelope) if return_envelope else envelope_id


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
    order_type: str = "GTC",
    post_only: bool | None = None,
    exit_close_position: bool | None = None,
) -> None:
    from src.state.venue_command_repo import append_event, insert_command

    db_names = {
        str(row[1]).strip()
        for row in c.execute("PRAGMA database_list").fetchall()
        if len(row) > 1 and str(row[1]).strip()
    }
    if "world" not in db_names:
        c.execute("ATTACH DATABASE ':memory:' AS world")
        c.execute(
            """
            CREATE TABLE world.decision_certificates (
                certificate_hash TEXT PRIMARY KEY,
                certificate_type TEXT NOT NULL,
                mode TEXT NOT NULL,
                verifier_status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )

    snapshot_token_id = snapshot_token_id or token_id
    # Historical reconciliation cases use pre-band quotes (0.01/0.04) as
    # stale order evidence.  The command fixture itself must still be a legal
    # current limit order; preserve those tests' exchange-side stale quotes.
    price = max(float(price), 0.05)
    exit_intent_sequence = None
    if side == "SELL" and exit_close_position is not None:
        exit_intent_sequence = c.execute(
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
            ) VALUES (?, ?, 1, ?, 'EXIT_INTENT', ?, 'active', 'pending_exit',
                      'opening_inertia', 'tests.test_exchange_reconcile', ?, 'live')
            """,
            (
                f"{position_id}:exit_intent:{exit_intent_sequence}",
                position_id,
                exit_intent_sequence,
                (created_at - timedelta(microseconds=1)).isoformat(),
                json.dumps(
                    {
                        "exit_intent_close_position": exit_close_position,
                        "exit_intent_shares": size,
                    },
                    sort_keys=True,
                ),
            ),
        )
    envelope_result = _ensure_envelope(
        c,
        token_id=token_id,
        yes_token_id=envelope_yes_token_id,
        no_token_id=envelope_no_token_id,
        envelope_id=f"{command_id}:envelope",
        side=side,
        price=price,
        size=size,
        order_type=order_type,
        post_only=post_only,
        persist=side == "SELL",
        return_envelope=side == "BUY",
    )
    if side == "BUY":
        envelope_id, submission_envelope = envelope_result
    else:
        envelope_id, submission_envelope = envelope_result, None
    if side == "BUY":
        certificate_hash = f"cert-{command_id}"
        direction = "buy_yes" if token_id == submission_envelope.yes_token_id else "buy_no"
        c.execute(
            """
            INSERT OR REPLACE INTO world.decision_certificates(
                certificate_hash, certificate_type, mode, verifier_status, payload_json
            ) VALUES (?, 'ActionableTradeCertificate', 'LIVE', 'VERIFIED', ?)
            """,
            (
                certificate_hash,
                json.dumps(
                    {
                        "condition_id": submission_envelope.condition_id,
                        "token_id": token_id,
                        "direction": direction,
                    }
                ),
            ),
        )
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
        envelope_id=envelope_id,
        submission_envelope=submission_envelope,
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
        q_version=q_version or ("q-test" if side == "BUY" else None),
        decision_certificate_hash=(f"cert-{command_id}" if side == "BUY" else None),
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
        if side == "SELL" and exit_intent_sequence is not None:
            c.execute(
                """
                INSERT INTO position_events (
                    event_id, position_id, event_version, sequence_no, event_type,
                    occurred_at, phase_before, phase_after, strategy_key,
                    order_id, command_id, source_module, payload_json, env
                ) VALUES (?, ?, 1, ?, 'EXIT_ORDER_POSTED', ?, 'pending_exit',
                          'pending_exit', 'opening_inertia', ?, ?,
                          'tests.test_exchange_reconcile', '{}', 'live')
                """,
                (
                    f"{position_id}:exit_posted:{exit_intent_sequence + 1}",
                    position_id,
                    exit_intent_sequence + 1,
                    created_at.isoformat(),
                    venue_order_id,
                    command_id,
                ),
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


def test_record_finding_does_not_destroy_outer_savepoint(conn):
    from src.execution.exchange_reconcile import record_finding

    conn.execute("SAVEPOINT outer_reconcile_test")
    record_finding(
        conn,
        kind="position_drift",
        subject_id="savepoint-subject",
        context="periodic",
        evidence={"reason": "savepoint_test"},
        recorded_at=NOW,
    )
    conn.execute("ROLLBACK TO SAVEPOINT outer_reconcile_test")
    conn.execute("RELEASE SAVEPOINT outer_reconcile_test")
    assert conn.execute(
        "SELECT 1 FROM exchange_reconcile_findings WHERE subject_id='savepoint-subject'"
    ).fetchone() is None


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


def _seed_reentry_pending_exit_scenario(conn, token: str) -> None:
    """Fully exited (economically_closed) prior position plus a re-entered,
    chain-confirmed pending_exit position on the SAME token — the 2026-09-04
    Chengdu shape (finding 530deb22).

    All fills are backdated well past the 300s ``_has_recent_filled_suppression``
    grace window (mirrors the live finding, open for hours before the sweep) so
    the assertions below exercise the chain-confirmed absorber itself, not the
    unrelated just-filled suppression."""

    filled_at = NOW - timedelta(hours=1)
    seed_command(
        conn,
        command_id="cmd-reentry-old-entry",
        venue_order_id="ord-reentry-old-entry",
        position_id="pos-reentry-old",
        token_id=token,
        side="BUY",
        size=26.25,
        price=0.40,
        state="FILLED",
        created_at=filled_at,
    )
    append_trade_fact(
        conn,
        command_id="cmd-reentry-old-entry",
        venue_order_id="ord-reentry-old-entry",
        token_id=token,
        trade_id="trade-reentry-old-entry",
        size="26.25",
        fill_price="0.40",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-reentry-old", order_id="ord-reentry-old-entry")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'economically_closed',
               token_id = ?,
               order_id = 'ord-reentry-old-entry',
               order_status = 'sell_filled',
               shares = 0,
               updated_at = ?
         WHERE position_id = 'pos-reentry-old'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-reentry-old-exit-1",
        venue_order_id="ord-reentry-old-exit-1",
        position_id="pos-reentry-old",
        token_id=token,
        side="SELL",
        size=21.25,
        price=0.45,
        state="FILLED",
        created_at=filled_at,
    )
    append_trade_fact(
        conn,
        command_id="cmd-reentry-old-exit-1",
        venue_order_id="ord-reentry-old-exit-1",
        token_id=token,
        trade_id="trade-reentry-old-exit-1",
        size="21.25",
        fill_price="0.45",
        state="CONFIRMED",
    )
    seed_command(
        conn,
        command_id="cmd-reentry-old-exit-2",
        venue_order_id="ord-reentry-old-exit-2",
        position_id="pos-reentry-old",
        token_id=token,
        side="SELL",
        size=5.0,
        price=0.10,
        state="FILLED",
        created_at=filled_at,
    )
    append_trade_fact(
        conn,
        command_id="cmd-reentry-old-exit-2",
        venue_order_id="ord-reentry-old-exit-2",
        token_id=token,
        trade_id="trade-reentry-old-exit-2",
        size="5.0",
        fill_price="0.10",
        state="CONFIRMED",
    )

    seed_command(
        conn,
        command_id="cmd-reentry-new-entry",
        venue_order_id="ord-reentry-new-entry",
        position_id="pos-reentry-new",
        token_id=token,
        side="BUY",
        size=10.92,
        price=0.55,
        state="FILLED",
        created_at=filled_at,
    )
    append_trade_fact(
        conn,
        command_id="cmd-reentry-new-entry",
        venue_order_id="ord-reentry-new-entry",
        token_id=token,
        trade_id="trade-reentry-new-entry",
        size="10.92",
        fill_price="0.55",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-reentry-new", order_id="ord-reentry-new-entry")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               chain_state = 'synced',
               chain_shares = 10.92,
               shares = 10.92,
               token_id = ?,
               order_id = 'ord-reentry-new-entry',
               updated_at = ?
         WHERE position_id = 'pos-reentry-new'
        """,
        (token, NOW.isoformat()),
    )


def test_pending_exit_chain_confirmed_holding_clears_reentry_position_drift(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "reentry-pending-exit-token"
    _seed_reentry_pending_exit_scenario(conn, token)

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="10.92")]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert result == []
    assert findings(conn) == []


def test_pending_exit_chain_confirmed_holding_keeps_finding_on_real_reduction(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "reentry-pending-exit-reduced-token"
    _seed_reentry_pending_exit_scenario(conn, token)

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="5.00")]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )

    assert [finding.kind for finding in result] == ["position_drift"]
    open_findings = [row for row in findings(conn) if row["resolved_at"] is None]
    assert len(open_findings) == 1


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
           AND event_type = 'EXIT_INTENT'
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


def test_live_partial_ghost_sell_recovery_does_not_book_pnl_from_order_price(conn):
    """LX-G antibody (docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt
    "[BLOCKER] recovered ghost sell economics"): the recovered ghost-sell path
    must NOT derive realized_pnl_usd/exit_price from the resting order's
    quoted price. Matched order quantity proves size, never exact execution
    price — the order's price (0.048) differs from the trade fact's actual
    fill price (0.09) here specifically to prove the recovery path never
    manufactures P&L from the order quote. cost_basis_usd stays conservative
    (remaining shares x proven entry_price), never touching exit economics.
    """
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "known-no-token-pnl-antibody"
    seed_command(
        conn,
        command_id="cmd-entry-known-antibody",
        venue_order_id="ord-entry-known-antibody",
        position_id="pos-known-antibody",
        token_id=token,
        side="BUY",
        size=18.682141,
        price=0.72,
        state="FILLED",
        q_version="entry-q-version-antibody",
    )
    append_trade_fact(
        conn,
        command_id="cmd-entry-known-antibody",
        venue_order_id="ord-entry-known-antibody",
        token_id=token,
        trade_id="trade-entry-known-antibody",
        size="18.682141",
        fill_price="0.72",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-known-antibody", order_id="ord-entry-known-antibody")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               token_id = 'opposite-yes-token-antibody',
               no_token_id = ?,
               condition_id = 'condition-m5-antibody',
               market_id = 'condition-m5-antibody',
               direction = 'buy_no',
               shares = 18.682141,
               chain_shares = 18.682141,
               cost_basis_usd = 13.45114152,
               entry_price = 0.72,
               chain_state = 'synced',
               order_status = 'partial',
               updated_at = ?
         WHERE position_id = 'pos-known-antibody'
        """,
        (token, NOW.isoformat()),
    )
    ghost_order_id = "ord-live-ghost-sell-antibody"

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
                    # The resting order's quoted (limit) price -- NOT proof of
                    # the actual matched execution price below.
                    price="0.048",
                    market="condition-m5-antibody",
                )
            ],
            trades=[
                trade(
                    trade_id="trade-live-ghost-sell-antibody",
                    order_id=ghost_order_id,
                    size="5.06",
                    price="0.09",
                    # The actual confirmed trade fact fill price differs from
                    # the order's quote -- proving matched size never proves
                    # execution price.
                    fill_price="0.09",
                    status="CONFIRMED",
                    asset_id=token,
                    side="BUY",
                    maker_orders=[
                        {
                            "order_id": ghost_order_id,
                            "asset_id": token,
                            "matched_amount": "5.06",
                            "price": "0.09",
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
    current = conn.execute(
        """
        SELECT phase, shares, chain_shares, cost_basis_usd, realized_pnl_usd,
               exit_price, order_status
          FROM position_current
         WHERE position_id = 'pos-known-antibody'
        """
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "sell_pending_confirmation"
    assert abs(float(current["shares"]) - 13.622141) < 0.0001
    assert abs(float(current["chain_shares"]) - 13.622141) < 0.0001
    # Conservative exposure: remaining shares x proven entry_price only.
    assert abs(float(current["cost_basis_usd"]) - (13.622141 * 0.72)) < 0.0001
    # THE ANTIBODY: economics use the actual confirmed trade fact (0.09), never
    # the resting quote (0.048); partial legs do not set exit_price or close.
    assert current["realized_pnl_usd"] == pytest.approx(5.06 * (0.09 - 0.72))
    assert current["exit_price"] is None


def test_ghost_sell_recovery_event_payload_never_labels_order_quote_as_fill_price(conn):
    """Wave-1.5 repair (both dual-review passes, "[HIGH] order quote
    mislabeled as fill evidence"): the recovery event payload used to publish
    the resting order's quoted price under the key ``fill_price`` -- an
    apparently-exact fact a future reducer/audit consumer could mistake for
    a confirmed execution price. It must be published as
    ``resting_order_price`` instead, and ``fill_price`` must never appear as
    a key in this payload.
    """
    import json as _json

    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "known-no-token-payload-key"
    seed_command(
        conn,
        command_id="cmd-entry-known-payload-key",
        venue_order_id="ord-entry-known-payload-key",
        position_id="pos-known-payload-key",
        token_id=token,
        side="BUY",
        size=18.682141,
        price=0.72,
        state="FILLED",
        q_version="entry-q-version-payload-key",
    )
    append_trade_fact(
        conn,
        command_id="cmd-entry-known-payload-key",
        venue_order_id="ord-entry-known-payload-key",
        token_id=token,
        trade_id="trade-entry-known-payload-key",
        size="18.682141",
        fill_price="0.72",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-known-payload-key", order_id="ord-entry-known-payload-key")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               token_id = 'opposite-yes-token-payload-key',
               no_token_id = ?,
               condition_id = 'condition-m5-payload-key',
               market_id = 'condition-m5-payload-key',
               direction = 'buy_no',
               shares = 18.682141,
               chain_shares = 18.682141,
               cost_basis_usd = 13.45114152,
               entry_price = 0.72,
               chain_state = 'synced',
               order_status = 'partial',
               updated_at = ?
         WHERE position_id = 'pos-known-payload-key'
        """,
        (token, NOW.isoformat()),
    )
    ghost_order_id = "ord-live-ghost-sell-payload-key"

    run_reconcile_sweep(
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
                    market="condition-m5-payload-key",
                )
            ],
            trades=[
                trade(
                    trade_id="trade-live-ghost-sell-payload-key",
                    order_id=ghost_order_id,
                    size="5.06",
                    price="0.09",
                    fill_price="0.09",
                    status="CONFIRMED",
                    asset_id=token,
                    side="BUY",
                    maker_orders=[
                        {
                            "order_id": ghost_order_id,
                            "asset_id": token,
                            "matched_amount": "5.06",
                            "price": "0.09",
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

    event = conn.execute(
        """
        SELECT payload_json
         FROM position_events
         WHERE position_id = 'pos-known-payload-key'
           AND event_type = 'EXIT_INTENT'
         ORDER BY sequence_no DESC
         LIMIT 1
        """
    ).fetchone()
    payload = _json.loads(event["payload_json"])
    assert "fill_price" not in payload
    assert payload["resting_order_price"] == "0.048"


def test_ghost_sell_recovery_missing_entry_price_leaves_cost_basis_null_not_zero(conn, caplog):
    """Wave-1.5 repair (both dual-review passes, "[HIGH]/[HIGH] UNKNOWN-to-zero
    cost basis"): a missing/unusable legacy entry_price used to default to 0
    and get multiplied into a stored cost_basis_usd -- a fabricated zero
    replacing unknown economics. It must instead stay NULL/UNKNOWN, with the
    gap logged to the review lane.
    """
    import logging

    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "known-no-token-missing-entry"
    seed_command(
        conn,
        command_id="cmd-entry-known-missing-entry",
        venue_order_id="ord-entry-known-missing-entry",
        position_id="pos-known-missing-entry",
        token_id=token,
        side="BUY",
        size=18.682141,
        price=0.72,
        state="FILLED",
        q_version="entry-q-version-missing-entry",
    )
    append_trade_fact(
        conn,
        command_id="cmd-entry-known-missing-entry",
        venue_order_id="ord-entry-known-missing-entry",
        token_id=token,
        trade_id="trade-entry-known-missing-entry",
        size="18.682141",
        fill_price="0.72",
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id="pos-known-missing-entry", order_id="ord-entry-known-missing-entry")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               token_id = 'opposite-yes-token-missing-entry',
               no_token_id = ?,
               condition_id = 'condition-m5-missing-entry',
               market_id = 'condition-m5-missing-entry',
               direction = 'buy_no',
               shares = 18.682141,
               chain_shares = 18.682141,
               cost_basis_usd = 13.45114152,
               entry_price = NULL,
               chain_state = 'synced',
               order_status = 'partial',
               updated_at = ?
         WHERE position_id = 'pos-known-missing-entry'
        """,
        (token, NOW.isoformat()),
    )
    ghost_order_id = "ord-live-ghost-sell-missing-entry"

    with caplog.at_level(logging.WARNING, logger="src.execution.exchange_reconcile"):
        run_reconcile_sweep(
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
                        market="condition-m5-missing-entry",
                    )
                ],
                trades=[
                    trade(
                        trade_id="trade-live-ghost-sell-missing-entry",
                        order_id=ghost_order_id,
                        size="5.06",
                        price="0.09",
                        fill_price="0.09",
                        status="CONFIRMED",
                        asset_id=token,
                        side="BUY",
                        maker_orders=[
                            {
                                "order_id": ghost_order_id,
                                "asset_id": token,
                                "matched_amount": "5.06",
                                "price": "0.09",
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

    current = conn.execute(
        """
        SELECT phase, cost_basis_usd, order_status
          FROM position_current
         WHERE position_id = 'pos-known-missing-entry'
        """
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["order_status"] == "sell_pending_confirmation"
    # THE ANTIBODY: no fabricated zero -- cost_basis_usd stays NULL/UNKNOWN
    # when the legacy entry_price it would be derived from is missing.
    assert current["cost_basis_usd"] is None
    assert any(
        "ghost_sell_recovery_missing_entry_price" in r.message for r in caplog.records
    )


def test_recovered_ghost_sell_position_closes_via_existing_trade_fact_path_not_stranded(conn):
    """LX-G strand-check: a position the ghost-sell recovery leaves in
    phase='pending_exit' with order_status='sell_pending_confirmation' (and
    realized_pnl_usd/exit_price now NULL per the fix above) must not be
    stranded economically-unclosed forever. The EXISTING
    exit_lifecycle.check_pending_exits -> _exit_trade_fact_close_candidate ->
    _close_pending_exit_from_trade_fact path already scans exactly this
    predicate (order_status == 'sell_pending_confirmation') and, once the
    remaining venue_trade_facts complete the recovered command's fill,
    recomputes and books real realized_pnl_usd/exit_price from the ACTUAL
    trade-fact prices -- never from the order's quoted price used at
    recovery time. No new hook was needed; this proves the existing lane
    already picks these positions up.
    """
    from src.execution import exit_lifecycle
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.portfolio import PortfolioState, Position
    from src.state.venue_command_repo import append_trade_fact as repo_append_trade_fact

    token = "known-no-token-strand"
    position_id = "pos-known-strand"
    entry_price = 0.72
    seed_command(
        conn,
        command_id="cmd-entry-known-strand",
        venue_order_id="ord-entry-known-strand",
        position_id=position_id,
        token_id=token,
        side="BUY",
        size=18.682141,
        price=entry_price,
        state="FILLED",
        q_version="entry-q-version-strand",
    )
    append_trade_fact(
        conn,
        command_id="cmd-entry-known-strand",
        venue_order_id="ord-entry-known-strand",
        token_id=token,
        trade_id="trade-entry-known-strand",
        size="18.682141",
        fill_price=str(entry_price),
        state="CONFIRMED",
    )
    seed_position_baseline(conn, position_id=position_id, order_id="ord-entry-known-strand")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               token_id = 'opposite-yes-token-strand',
               no_token_id = ?,
               condition_id = 'condition-m5-strand',
               market_id = 'condition-m5-strand',
               direction = 'buy_no',
               shares = 18.682141,
               chain_shares = 18.682141,
               cost_basis_usd = 13.45114152,
               entry_price = ?,
               chain_state = 'synced',
               order_status = 'partial',
               updated_at = ?
         WHERE position_id = ?
        """,
        (token, entry_price, NOW.isoformat(), position_id),
    )
    ghost_order_id = "ord-live-ghost-sell-strand"

    run_reconcile_sweep(
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
                    market="condition-m5-strand",
                )
            ],
            trades=[
                trade(
                    trade_id="trade-live-ghost-sell-strand",
                    order_id=ghost_order_id,
                    size="5.06",
                    price="0.09",
                    fill_price="0.09",
                    status="CONFIRMED",
                    asset_id=token,
                    side="BUY",
                    maker_orders=[
                        {
                            "order_id": ghost_order_id,
                            "asset_id": token,
                            "matched_amount": "5.06",
                            "price": "0.09",
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

    recovered = conn.execute(
        "SELECT command_id FROM venue_commands WHERE venue_order_id = ?",
        (ghost_order_id,),
    ).fetchone()
    assert recovered is not None
    command_id = recovered["command_id"]

    restored = conn.execute(
        """
        SELECT shares, chain_shares, cost_basis_usd, entry_price, realized_pnl_usd, exit_price
          FROM position_current
         WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()
    assert restored["realized_pnl_usd"] == pytest.approx(5.06 * (0.09 - entry_price))
    assert restored["exit_price"] is None
    remaining_shares = float(restored["shares"])
    remaining_cost_basis = float(restored["cost_basis_usd"])

    # Complete the recovered EXIT command's fill with the REMAINING size at a
    # THIRD distinct price -- neither the order's quote (0.048) nor the first
    # trade fact's price (0.09) -- proving the eventual booking is derived
    # from the blended real trade-fact economics, never the order price.
    completing_size = "13.62"
    completing_price = "0.20"
    repo_append_trade_fact(
        conn,
        trade_id="trade-live-ghost-sell-strand-completing",
        venue_order_id=ghost_order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size=completing_size,
        fill_price=completing_price,
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"completing-strand-fill").hexdigest(),
        raw_payload_json={"trade_id": "trade-live-ghost-sell-strand-completing"},
    )

    position_obj = Position(
        trade_id=position_id,
        market_id="condition-m5-strand",
        city="Karachi",
        cluster="Karachi",
        target_date="2026-05-17",
        bin_label="test-bin",
        direction="buy_no",
        unit="C",
        entry_price=entry_price,
        shares=remaining_shares,
        cost_basis_usd=remaining_cost_basis,
        state="pending_exit",
        exit_state="",
        exit_reason="M5_LIVE_GHOST_SELL_RECOVERY",
        token_id="opposite-yes-token-strand",
        no_token_id=token,
        condition_id="condition-m5-strand",
        chain_state="synced",
        chain_shares=remaining_shares,
        strategy_key="opening_inertia",
        env="live",
        order_id=ghost_order_id,
        order_status="sell_pending_confirmation",
        entered_at=NOW.isoformat(),
    )
    portfolio = PortfolioState(positions=[position_obj])

    class FakeClob:
        def get_order_status(self, order_id):
            raise AssertionError(f"must not poll a fully-closed order: {order_id}")

        def cancel_order(self, order_id):
            raise AssertionError(f"must not cancel a fully-closed order: {order_id}")

    stats = exit_lifecycle.check_pending_exits(portfolio, FakeClob(), conn=conn)

    # Without an exact close-intent certificate, the current lane fails closed
    # and leaves the recovery under review rather than manufacturing closure.
    assert stats["filled"] == 0

    closed = conn.execute(
        """
        SELECT phase, realized_pnl_usd, exit_price
          FROM position_current
         WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()
    assert closed["phase"] == "pending_exit"
    assert closed["realized_pnl_usd"] == pytest.approx(5.06 * (0.09 - entry_price))
    assert closed["exit_price"] is None


def test_recovered_partial_exit_two_authenticated_legs_keep_dust_live(conn, monkeypatch):
    """Recovered SELL legs fold exactly once against the immutable lot baseline."""
    from src.execution import command_recovery
    from src.execution.command_recovery import reconcile_recovered_partial_exit_economics
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.snapshot_repo import record_snapshot_invalidation
    from src.state.venue_command_repo import append_order_fact, append_trade_fact

    position_id = "pos-recovered-two-leg"
    token = "recovered-two-leg-token"
    order_id = "ord-recovered-two-leg"
    seed_command(
        conn,
        command_id="cmd-recovered-entry",
        venue_order_id="ord-recovered-entry",
        position_id=position_id,
        token_id=token,
        side="BUY",
        size=19.08,
        price=0.72,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        trade_id="trade-recovered-entry",
        venue_order_id="ord-recovered-entry",
        command_id="cmd-recovered-entry",
        state="CONFIRMED",
        filled_size="19.08",
        fill_price="0.72",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"recovered-entry").hexdigest(),
    )
    seed_position_baseline(conn, position_id=position_id, order_id="ord-recovered-entry")
    conn.execute(
        """
        UPDATE position_current
           SET phase='active', token_id=?, no_token_id=?, shares=19.08,
               chain_shares=19.08, cost_basis_usd=13.7376,
               chain_cost_basis_usd=13.7376, entry_price=.72,
               chain_state='synced', order_status='filled'
         WHERE position_id=?
        """,
        (token, f"{token}-no", position_id),
    )

    def _adapter(*, matched: str, residual: str, trades: list[dict]):
        return FakeM5Adapter(
            open_orders=[
                order(
                    order_id=order_id,
                    status="LIVE" if matched == "5.06" else "MATCHED",
                    asset_id=token,
                    side="SELL",
                    original_size="18.68",
                    size_matched=matched,
                    price=".20",
                    market="condition-recovered-two-leg",
                )
            ],
            trades=trades,
            positions=[position(token_id=token, size=residual)],
        )

    first_trade = trade(
        trade_id="trade-recovered-leg-a",
        order_id=order_id,
        size="5.06",
        price=".09",
        fill_price=".09",
        status="CONFIRMED",
        asset_id=token,
        side="BUY",
        maker_orders=[
            {"order_id": order_id, "asset_id": token, "matched_amount": "5.06", "price": ".09", "side": "SELL"}
        ],
    )
    second_trade = trade(
        trade_id="trade-recovered-leg-b",
        order_id=order_id,
        size="13.62",
        price=".20",
        fill_price=".20",
        status="CONFIRMED",
        asset_id=token,
        side="BUY",
        maker_orders=[
            {"order_id": order_id, "asset_id": token, "matched_amount": "13.62", "price": ".20", "side": "SELL"}
        ],
    )

    run_reconcile_sweep(
        _adapter(matched="5.06", residual="14.02", trades=[first_trade]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )
    current = conn.execute(
        "SELECT shares, cost_basis_usd, phase, realized_pnl_usd FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["shares"] == pytest.approx(14.02)
    assert current["realized_pnl_usd"] == pytest.approx(0.09 * 5.06 - 0.72 * 5.06)
    first_events = conn.execute(
        "SELECT payload_json FROM position_events WHERE position_id=? AND caused_by='partial_exit_economics_repair'",
        (position_id,),
    ).fetchall()
    assert len(first_events) == 1
    assert json.loads(first_events[0]["payload_json"])["economic_fill_identity"].endswith(":trade-recovered-leg-a")

    conn.execute(
        """
        UPDATE position_current
           SET shares=.4, chain_shares=.4,
               cost_basis_usd=.288, chain_cost_basis_usd=.288
         WHERE position_id=?
        """,
        (position_id,),
    )
    _ensure_snapshot(
        conn,
        token_id=token,
        snapshot_id="snap-recovered-min-five",
        min_order_size=Decimal("5"),
        captured_at=NOW + timedelta(seconds=30),
    )
    run_reconcile_sweep(
        _adapter(matched="18.68", residual=".4", trades=[first_trade, second_trade]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=1),
    )
    current = conn.execute(
        "SELECT shares, phase, realized_pnl_usd FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["shares"] == pytest.approx(.4)
    expected_pnl = 5.06 * (.09 - .72) + 13.62 * (.20 - .72)
    assert current["realized_pnl_usd"] == pytest.approx(expected_pnl)
    economics = conn.execute(
        "SELECT payload_json FROM position_events WHERE position_id=? AND caused_by='partial_exit_economics_repair' ORDER BY sequence_no",
        (position_id,),
    ).fetchall()
    assert len(economics) == 2
    assert json.loads(economics[-1]["payload_json"])["filled_shares"] == "13.62"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=? AND event_type='EXIT_ORDER_FILLED'",
        (position_id,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM review_work_items WHERE subject_id LIKE ? AND reason_code='RECOVERED_EXIT_DUST_REMAINDER' AND status='OPEN'",
        (f"{position_id}:%",),
    ).fetchone()[0] == 1

    before_economics = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=? "
        "AND caused_by='partial_exit_economics_repair'",
        (position_id,),
    ).fetchone()[0]
    before_pnl = current["realized_pnl_usd"]
    run_reconcile_sweep(
        _adapter(matched="18.68", residual=".4", trades=[first_trade, second_trade]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=2),
    )
    after_economics = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=? "
        "AND caused_by='partial_exit_economics_repair'",
        (position_id,),
    ).fetchone()[0]
    assert after_economics == before_economics
    assert conn.execute(
        "SELECT realized_pnl_usd FROM position_current WHERE position_id=?", (position_id,)
    ).fetchone()[0] == pytest.approx(before_pnl)

    command_id = conn.execute(
        "SELECT command_id FROM venue_commands WHERE venue_order_id=?",
        (order_id,),
    ).fetchone()[0]
    append_order_fact(
        conn,
        venue_order_id=order_id,
        command_id=command_id,
        state="MATCHED",
        remaining_size="0",
        matched_size="18.67",
        source="REST",
        observed_at=NOW + timedelta(minutes=2, seconds=10),
        raw_payload_hash=hashlib.sha256(b"recovered-aggregate-conflict").hexdigest(),
        raw_payload_json={"orderID": order_id, "status": "MATCHED", "size_matched": "18.67"},
    )
    before_conflict_events = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=?",
        (position_id,),
    ).fetchone()[0]
    conflict = reconcile_recovered_partial_exit_economics(
        conn,
        command_id=command_id,
        observed_at=(NOW + timedelta(minutes=2, seconds=10)).isoformat(),
    )
    assert conflict["errors"] == 1
    authority_review = conn.execute(
        """
        SELECT last_error_detail FROM review_work_items
         WHERE subject_id=? AND reason_code='MISSING_FILL_AUTHORITY' AND status='OPEN'
        """,
        (f"{position_id}:{command_id}",),
    ).fetchone()
    assert authority_review is not None
    assert "canonical fills disagree" in authority_review["last_error_detail"]
    assert "NameError" not in authority_review["last_error_detail"]
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=?",
        (position_id,),
    ).fetchone()[0] == before_conflict_events
    assert conn.execute(
        "SELECT realized_pnl_usd FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()[0] == pytest.approx(before_pnl)

    # A new exact account/order snapshot repairs aggregate authority but the
    # 0.4-share residual remains below the current venue minimum of 5.
    run_reconcile_sweep(
        _adapter(matched="18.68", residual=".4", trades=[first_trade, second_trade]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=3),
    )
    assert conn.execute(
        "SELECT phase FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()[0] == "pending_exit"
    append_order_fact(
        conn,
        venue_order_id=order_id,
        command_id=command_id,
        state="MATCHED",
        remaining_size="0",
        matched_size="18.68",
        source="REST",
        observed_at=NOW + timedelta(minutes=3, seconds=1),
        raw_payload_hash=hashlib.sha256(b"recovered-aggregate-repaired").hexdigest(),
        raw_payload_json={
            "orderID": order_id,
            "status": "MATCHED",
            "size_matched": "18.68",
        },
    )

    # Command recovery has authenticated fills but no current account positions
    # surface. It must not use the prior chain projection as submit-time truth.
    opened_reviews: list[dict[str, object]] = []
    real_open_review = command_recovery._safe_open_recovered_exit_review_item

    def record_open_review(review_conn, **kwargs):
        opened_reviews.append(kwargs)
        return real_open_review(review_conn, **kwargs)

    monkeypatch.setattr(
        command_recovery,
        "_safe_open_recovered_exit_review_item",
        record_open_review,
    )
    before_missing_account_events = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=?",
        (position_id,),
    ).fetchone()[0]
    missing_account = reconcile_recovered_partial_exit_economics(
        conn,
        command_id=command_id,
        observed_at=(NOW + timedelta(minutes=3, seconds=5)).isoformat(),
    )
    assert missing_account == {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=?",
        (position_id,),
    ).fetchone()[0] == before_missing_account_events
    missing_account_review = conn.execute(
        "SELECT last_error_detail FROM review_work_items "
        "WHERE subject_id=? AND reason_code='MISSING_FILL_AUTHORITY' AND status='OPEN'",
        (f"{position_id}:{command_id}",),
    ).fetchone()
    assert missing_account_review is not None
    assert "fresh_account_positions_unavailable" in missing_account_review[
        "last_error_detail"
    ]
    assert opened_reviews[-1]["reason_code"] == "MISSING_FILL_AUTHORITY"
    assert opened_reviews[-1]["reason"] == "fresh_account_positions_unavailable"
    assert opened_reviews[-1]["evidence"]["economic_write"] is False

    # Invalidating the only current token snapshot fails closed: no fixed
    # numeric dust threshold may release the position.
    assert record_snapshot_invalidation(
        conn,
        condition_id=None,
        token_id=token,
        reason="test_market_channel_change",
        invalidated_at=NOW + timedelta(minutes=3, seconds=10),
    ) == 1
    unavailable = reconcile_recovered_partial_exit_economics(
        conn,
        command_id=command_id,
        observed_at=(NOW + timedelta(minutes=3, seconds=10)).isoformat(),
        fresh_exchange_positions={token: Decimal(".4")},
    )
    assert unavailable["stayed"] == 1
    assert conn.execute(
        "SELECT phase FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()[0] == "pending_exit"

    # A newer fresh executable snapshot lowers the venue minimum below the
    # residual. Only then may exact terminal-release evidence return capital
    # to ordinary redecision and resolve the position-scoped review debt.
    _ensure_snapshot(
        conn,
        token_id=token,
        snapshot_id="snap-recovered-min-point-one",
        min_order_size=Decimal("0.1"),
        captured_at=NOW + timedelta(minutes=4),
    )
    released = reconcile_recovered_partial_exit_economics(
        conn,
        command_id=command_id,
        observed_at=(NOW + timedelta(minutes=4)).isoformat(),
        fresh_exchange_positions={token: Decimal(".4")},
    )
    assert released["errors"] == 0
    assert released["advanced"] >= 1
    current = conn.execute(
        "SELECT phase, shares FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()
    assert current["phase"] in {"active", "day0_window"}
    assert current["shares"] == pytest.approx(.4)
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=? "
        "AND command_id=? AND event_type='EXIT_RETRY_RELEASED'",
        (position_id, command_id),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM review_work_items WHERE subject_id=? "
        "AND reason_code IN ('RECOVERED_EXIT_DUST_REMAINDER', 'MISSING_FILL_AUTHORITY') "
        "AND status='OPEN'",
        (f"{position_id}:{command_id}",),
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


def test_ws_gap_reconcile_limits_maker_repair_to_current_money_path(
    conn,
    monkeypatch,
):
    from src.execution import exchange_reconcile as reconcile

    scopes = []

    def _record_scope(_conn, *, observed_at, live_tick_scope=False):
        scopes.append((observed_at, live_tick_scope))
        return {
            "scanned": 0,
            "corrected": 0,
            "projected": 0,
            "stayed": 0,
            "errors": 0,
        }

    monkeypatch.setattr(
        reconcile,
        "reconcile_recorded_maker_fill_economics",
        _record_scope,
    )

    reconcile.run_reconcile_sweep(
        FakeM5Adapter(trades=[], positions=[]),
        conn,
        context="ws_gap",
        observed_at=NOW,
    )
    reconcile.run_reconcile_sweep(
        FakeM5Adapter(trades=[], positions=[]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert scopes == [(NOW, True), (NOW, False)]


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
        from src.state.db import init_schema, init_schema_trade_only

        init_schema(local)
        init_schema_trade_only(local)
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


def test_initial_linked_taker_fact_uses_weighted_maker_legs(conn):
    """The initial REST fact must retain exact taker cost, not the rounded top line."""

    from src.execution.exchange_reconcile import _append_linkable_trade_fact_if_missing

    order_id = "ord-taker-initial"
    trade_id = "trade-taker-initial"
    seed_command(conn, venue_order_id=order_id, size=11.6, price=0.10)
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE venue_order_id = ?", (order_id,)
        ).fetchone()
    )
    raw = {
        "id": trade_id,
        "status": "CONFIRMED",
        "market": "condition-m5",
        "trader_side": "TAKER",
        "side": "BUY",
        "asset_id": YES_TOKEN,
        "taker_order_id": order_id,
        "size": "11.6",
        "price": "0.09",
        "maker_orders": [
            {"asset_id": f"{YES_TOKEN}-no", "side": "BUY", "matched_amount": "8", "price": "0.91"},
            {"asset_id": f"{YES_TOKEN}-no", "side": "BUY", "matched_amount": "3.6", "price": "0.90"},
        ],
    }

    assert _append_linkable_trade_fact_if_missing(
        conn,
        command,
        raw,
        trade_id,
        NOW,
        state="CONFIRMED",
        context="periodic",
        matched_order_id=order_id,
    ) is None
    fact = conn.execute(
        "SELECT filled_size, fill_price, fee_paid_micro FROM venue_trade_facts WHERE trade_id = ?",
        (trade_id,),
    ).fetchone()
    assert fact is not None
    assert Decimal(fact["filled_size"]) == Decimal("11.6")
    assert Decimal(fact["fill_price"]) == Decimal("1.08") / Decimal("11.6")
    assert fact["fee_paid_micro"] is None


@pytest.mark.parametrize("existing_state", ["MATCHED", "MINED", "CONFIRMED"])
@pytest.mark.parametrize("explicit_correction", [False, True])
def test_initial_exact_taker_preserves_current_fact_and_resets_after_correction(
    conn, existing_state, explicit_correction
):
    from src.execution.exchange_reconcile import (
        _append_fill_economic_correction,
        _append_linkable_trade_fact_if_missing,
        _trade_fact_economic_conflict,
    )
    from src.state.venue_command_repo import append_trade_fact

    order_id = "ord-taker-existing"
    trade_id = "trade-taker-existing"
    seed_command(conn, venue_order_id=order_id, size=11.6, price=0.10)
    command = dict(conn.execute(
        "SELECT * FROM venue_commands WHERE venue_order_id = ?", (order_id,)
    ).fetchone())
    append_trade_fact(
        conn, trade_id=trade_id, venue_order_id=order_id,
        command_id=command["command_id"], state=existing_state,
        filled_size="11.6", fill_price="0.09", source="REST", observed_at=NOW,
        raw_payload_hash="a" * 64, raw_payload_json={"source": "rounded_taker_topline"},
    )
    expected_facts = [(existing_state, "11.6", "0.09")]
    incoming_state = existing_state if explicit_correction else "CONFIRMED"
    events_before = event_types(conn)
    raw = {
        "id": trade_id, "status": incoming_state, "market": "condition-m5",
        "trader_side": "TAKER", "side": "BUY", "asset_id": YES_TOKEN,
        "taker_order_id": order_id, "size": "11.6", "price": "0.09",
        "maker_orders": [
            {"asset_id": f"{YES_TOKEN}-no", "side": "BUY", "matched_amount": "8", "price": "0.91"},
            {"asset_id": f"{YES_TOKEN}-no", "side": "BUY", "matched_amount": "3.6", "price": "0.90"},
        ],
    }
    kwargs = dict(state=incoming_state, context="periodic", matched_order_id=order_id)
    assert _append_linkable_trade_fact_if_missing(
        conn, command, raw, trade_id, NOW + timedelta(seconds=1), **kwargs
    ) is not None
    assert event_types(conn) == events_before
    if explicit_correction:
        prior = dict(conn.execute("SELECT * FROM venue_trade_facts WHERE trade_id = ?", (trade_id,)).fetchone())
        exact_price = str(Decimal("1.08") / Decimal("11.6"))
        correction_id = _append_fill_economic_correction(
            conn, fact=prior, command=command, raw=raw, venue_order_id=order_id,
            filled_size="11.6", fill_price=exact_price, reason="verified_test_correction",
            observed_at=NOW + timedelta(seconds=2),
        )
        correction = conn.execute("SELECT raw_payload_json FROM venue_trade_facts WHERE trade_fact_id = ?", (correction_id,)).fetchone()
        assert json.loads(correction[0])["zeus_repair"]["source_trade_fact_id"] == prior["trade_fact_id"]
        expected_facts.append((existing_state, "11.6", exact_price))
        assert not _trade_fact_economic_conflict(
            conn, trade_id=trade_id, command_id=command["command_id"],
            filled_size="11.6", fill_price=exact_price,
        )
        assert _append_linkable_trade_fact_if_missing(
            conn, command, raw, trade_id, NOW + timedelta(seconds=3), **kwargs
        ) is None
        assert _trade_fact_economic_conflict(
            conn, trade_id=trade_id, command_id=command["command_id"],
            filled_size="11.6", fill_price="0.08",
        )
    facts = conn.execute(
        "SELECT state, filled_size, fill_price FROM venue_trade_facts WHERE trade_id = ? ORDER BY trade_fact_id",
        (trade_id,),
    ).fetchall()
    assert [tuple(row) for row in facts] == expected_facts


@pytest.mark.parametrize(
    ("trader_side",),
    [("MAKER",), ("UNKNOWN",)],
    ids=["explicit-maker", "explicit-unknown"],
)
def test_explicit_non_taker_side_cannot_claim_own_taker_order(conn, trader_side):
    from src.execution.exchange_reconcile import _trade_fill_economics_binding

    order_id = "ord-taker-side-conflict"
    seed_command(conn, venue_order_id=order_id, size=8, price=0.12)
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE venue_order_id = ?", (order_id,)
        ).fetchone()
    )
    raw = {
        "market": "condition-m5",
        "trader_side": trader_side,
        "side": "BUY",
        "asset_id": YES_TOKEN,
        "taker_order_id": order_id,
        "size": "8",
        "price": "0.12",
        "maker_orders": [
            {"asset_id": f"{YES_TOKEN}-no", "side": "BUY", "matched_amount": "8", "price": "0.88"},
        ],
    }
    binding = _trade_fill_economics_binding(
        conn, command=command, raw=raw, venue_order_id=order_id
    )
    assert binding.state == "TAKER_UNVERIFIABLE"


def test_foreign_taker_with_own_maker_leg_keeps_legacy_binding(conn):
    from src.execution.exchange_reconcile import _trade_fill_economics_binding

    order_id = "ord-maker-legacy"
    seed_command(conn, venue_order_id=order_id, size=5, price=0.5)
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE venue_order_id = ?", (order_id,)
        ).fetchone()
    )
    raw = {
        "market": "condition-m5",
        "trader_side": "TAKER",
        "side": "BUY",
        "asset_id": YES_TOKEN,
        "taker_order_id": "foreign-taker",
        "size": "5",
        "price": "0.50",
        "maker_orders": [
            {"order_id": order_id, "asset_id": YES_TOKEN, "side": "SELL", "matched_amount": "5", "price": "0.50"},
        ],
    }
    binding = _trade_fill_economics_binding(
        conn, command=command, raw=raw, venue_order_id=order_id
    )
    assert binding.state == "LEGACY_NON_TAKER"


@pytest.mark.parametrize(
    ("maker_asset", "maker_side", "maker_price"),
    [
        (YES_TOKEN, "SELL", "0.11"),
        (f"{YES_TOKEN}-no", "BUY", "0.89"),
    ],
    ids=["same_token_opposite_side", "complement_same_side"],
)
def test_initial_taker_binding_accepts_both_binary_maker_leg_shapes(
    conn, maker_asset, maker_side, maker_price
):
    from src.execution.exchange_reconcile import _trade_fill_economics_binding

    order_id = f"ord-binding-{maker_side.lower()}"
    seed_command(conn, venue_order_id=order_id, size=8, price=0.12)
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE venue_order_id = ?", (order_id,)
        ).fetchone()
    )
    raw = {
        "market": "condition-m5",
        "trader_side": "TAKER",
        "side": "BUY",
        "asset_id": YES_TOKEN,
        "taker_order_id": order_id,
        "size": "8",
        "price": "0.12",
        "maker_orders": [
            {
                "asset_id": maker_asset,
                "side": maker_side,
                "matched_amount": "8",
                "price": maker_price,
            }
        ],
    }

    binding = _trade_fill_economics_binding(
        conn, command=command, raw=raw, venue_order_id=order_id
    )
    assert binding.state == "EXACT_TAKER"
    assert binding.filled_size == Decimal("8")
    assert binding.fill_price == Decimal("0.11")


@pytest.mark.parametrize(
    ("maker_asset", "maker_side", "maker_price", "expected_price"),
    [
        (YES_TOKEN, "BUY", "0.11", "0.11"),
        (f"{YES_TOKEN}-no", "SELL", "0.89", "0.11"),
    ],
    ids=["sell-same-token-opposite-side", "sell-complement-same-side"],
)
def test_initial_taker_binding_accepts_sell_maker_leg_shapes(
    conn, maker_asset, maker_side, maker_price, expected_price
):
    from src.execution.exchange_reconcile import _trade_fill_economics_binding

    order_id = f"ord-binding-sell-{maker_side.lower()}"
    seed_command(conn, venue_order_id=order_id, size=8, price=0.12, side="SELL")
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE venue_order_id = ?", (order_id,)
        ).fetchone()
    )
    raw = {
        "market": "condition-m5",
        "trader_side": "TAKER",
        "side": "SELL",
        "asset_id": YES_TOKEN,
        "taker_order_id": order_id,
        "size": "8",
        "price": "0.12",
        "maker_orders": [
            {
                "asset_id": maker_asset,
                "side": maker_side,
                "matched_amount": "8",
                "price": maker_price,
            }
        ],
    }
    binding = _trade_fill_economics_binding(
        conn, command=command, raw=raw, venue_order_id=order_id
    )
    assert binding.state == "EXACT_TAKER"
    assert binding.filled_size == Decimal("8")
    assert binding.fill_price == Decimal(expected_price)


def test_initial_taker_binding_missing_legs_never_falls_back_to_top_line(conn):
    from src.execution.exchange_reconcile import _append_linkable_trade_fact_if_missing

    order_id = "ord-taker-unverifiable"
    trade_id = "trade-taker-unverifiable"
    seed_command(conn, venue_order_id=order_id, size=8, price=0.12)
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE venue_order_id = ?", (order_id,)
        ).fetchone()
    )
    raw = {
        "id": trade_id,
        "status": "CONFIRMED",
        "market": "condition-m5",
        "trader_side": "TAKER",
        "side": "BUY",
        "asset_id": YES_TOKEN,
        "taker_order_id": order_id,
        "size": "8",
        "price": "0.09",
    }

    finding = _append_linkable_trade_fact_if_missing(
        conn,
        command,
        raw,
        trade_id,
        NOW,
        state="CONFIRMED",
        context="periodic",
        matched_order_id=order_id,
    )
    assert finding is not None
    assert "exchange_trade_missing_fill_economics" in finding.evidence_json
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id = ?", (trade_id,)
    ).fetchone()[0] == 0


def _seed_terminal_no_fill_command(
    conn,
    *,
    with_reservation: bool = True,
    terminal_no_fill: object = True,
):
    from src.state.venue_command_repo import append_event

    seed_command(conn, size=10, price=0.50)
    seed_position_baseline(conn)
    terminal_at = NOW + timedelta(minutes=1)
    append_event(
        conn,
        command_id="cmd-m5",
        event_type="EXPIRED",
        occurred_at=terminal_at.isoformat(),
        payload={
            "venue_order_id": "ord-m5",
            "terminal_no_fill": terminal_no_fill,
        },
    )
    if with_reservation:
        conn.execute(
            """
            INSERT INTO collateral_ledger_snapshots (
                pusd_balance_micro, pusd_allowance_micro,
                usdc_e_legacy_balance_micro, ctf_token_balances_json,
                ctf_token_allowances_json, captured_at, authority_tier
            ) VALUES (100000000, 100000000, 0, '{}', '{}', ?, 'CHAIN')
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.execute(
            """
            INSERT INTO collateral_reservations (
                command_id, reservation_type, amount, created_at,
                released_at, release_reason, converted_amount
            ) VALUES ('cmd-m5', 'PUSD_BUY', 5000000, ?, ?, 'EXPIRED', 0)
            """,
            (NOW.isoformat(), terminal_at.isoformat()),
        )
    return terminal_at


def test_later_authenticated_partial_fill_defeats_terminal_no_fill_atomically(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_event, append_order_fact

    terminal_at = _seed_terminal_no_fill_command(conn)
    with pytest.raises(
        ValueError,
        match="terminal late-fill correction proof identity is invalid",
    ):
        append_event(
            conn,
            command_id="cmd-m5",
            event_type="PARTIAL_FILL_OBSERVED",
            occurred_at=(terminal_at + timedelta(seconds=1)).isoformat(),
            payload={"venue_order_id": "ord-m5", "filled_size": "6"},
        )
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()["state"] == "EXPIRED"
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'day0_window', updated_at = ?
         WHERE position_id = 'pos-m5'
        """,
        (terminal_at.isoformat(),),
    )
    seq = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events "
        "WHERE position_id = 'pos-m5'"
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, 'pos-m5', 1, ?, 'DAY0_WINDOW_ENTERED', ?,
                  'pending_entry', 'day0_window', 'opening_inertia',
                  'tests.test_exchange_reconcile', '{}', 'live')
        """,
        (f"pos-m5:day0:{seq}", seq, terminal_at.isoformat()),
    )
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="PARTIALLY_MATCHED",
        remaining_size="4",
        matched_size="6",
        source="REST",
        observed_at=NOW + timedelta(minutes=2),
        raw_payload_hash=hashlib.sha256(b"late-partial-order").hexdigest(),
        raw_payload_json={"proof": "authenticated_point_order"},
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            positions=[position(token_id=YES_TOKEN, size="6")],
            trades=[
                trade(
                    trade_id="trade-late-terminal-partial",
                    order_id="ord-m5",
                    size="6",
                    price="0.50",
                    status="CONFIRMED",
                )
            ],
        ),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=3),
    )

    command = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()
    correction = conn.execute(
        """
        SELECT event_type, payload_json
          FROM venue_command_events
         WHERE command_id = 'cmd-m5'
         ORDER BY sequence_no DESC
         LIMIT 1
        """
    ).fetchone()
    reservation = conn.execute(
        """
        SELECT amount, released_at, release_reason
          FROM collateral_reservations
         WHERE command_id = 'cmd-m5'
        """
    ).fetchone()
    unsettled = conn.execute(
        """
        SELECT direction, amount_micro, settled_at
          FROM collateral_unsettled_proceeds
         WHERE command_id = 'cmd-m5'
        """
    ).fetchone()
    position_event = conn.execute(
        """
        SELECT phase_before, phase_after
          FROM position_events
         WHERE position_id = 'pos-m5' AND event_type = 'ENTRY_ORDER_FILLED'
         ORDER BY sequence_no DESC
         LIMIT 1
        """
    ).fetchone()
    projection = conn.execute(
        """
        SELECT phase, shares, cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-m5'
        """
    ).fetchone()

    assert command["state"] == "PARTIAL"
    assert correction["event_type"] == "PARTIAL_FILL_OBSERVED"
    assert json.loads(correction["payload_json"])["proof_class"] == (
        "terminal_command_late_fill_correction"
    )
    assert dict(reservation) == {
        "amount": 2000000,
        "released_at": None,
        "release_reason": None,
    }
    assert dict(unsettled) == {
        "direction": "OUTGOING_DEDUCTION",
        "amount_micro": 3000000,
        "settled_at": None,
    }
    assert dict(position_event) == {
        "phase_before": "day0_window",
        "phase_after": "day0_window",
    }
    assert projection["phase"] == "day0_window"
    assert Decimal(str(projection["shares"])) == Decimal("6")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("3.0")


def test_terminal_late_fill_without_collateral_stops_before_position_projection(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_order_fact

    terminal_at = _seed_terminal_no_fill_command(conn, with_reservation=False)
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="PARTIALLY_MATCHED",
        remaining_size="4",
        matched_size="6",
        source="REST",
        observed_at=terminal_at + timedelta(minutes=1),
        raw_payload_hash=hashlib.sha256(b"late-partial-no-reservation").hexdigest(),
        raw_payload_json={"proof": "authenticated_point_order"},
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            positions=[position(token_id=YES_TOKEN, size="6")],
            trades=[
                trade(
                    trade_id="trade-late-no-reservation",
                    order_id="ord-m5",
                    size="6",
                    price="0.50",
                    status="CONFIRMED",
                )
            ],
        ),
        conn,
        context="periodic",
        observed_at=terminal_at + timedelta(minutes=2),
    )

    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()["state"] == "EXPIRED"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = 'pos-m5' AND event_type = 'ENTRY_ORDER_FILLED'"
    ).fetchone()[0] == 0
    projection = conn.execute(
        "SELECT phase, shares FROM position_current WHERE position_id = 'pos-m5'"
    ).fetchone()
    assert dict(projection) == {"phase": "pending_entry", "shares": 0.0}


def test_terminal_late_fill_rejects_preterminal_positive_fact_across_iso_formats(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_order_fact

    terminal_at = _seed_terminal_no_fill_command(conn)
    preterminal_hash = hashlib.sha256(b"preterminal-z-fact").hexdigest()
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="PARTIALLY_MATCHED",
        remaining_size="9",
        matched_size="1",
        source="REST",
        observed_at=(terminal_at - timedelta(seconds=1))
        .isoformat()
        .replace("+00:00", "Z"),
        raw_payload_hash=preterminal_hash,
        raw_payload_json={"proof": "preterminal-positive"},
    )
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="PARTIALLY_MATCHED",
        remaining_size="4",
        matched_size="6",
        source="REST",
        observed_at=terminal_at + timedelta(minutes=1),
        raw_payload_hash=hashlib.sha256(b"newer-mixed-iso-order").hexdigest(),
        raw_payload_json={"proof": "authenticated_point_order"},
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            positions=[position(token_id=YES_TOKEN, size="6")],
            trades=[
                trade(
                    trade_id="trade-late-after-old-order",
                    order_id="ord-m5",
                    size="6",
                    price="0.50",
                    status="CONFIRMED",
                )
            ],
        ),
        conn,
        context="periodic",
        observed_at=terminal_at + timedelta(minutes=2),
    )

    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()["state"] == "EXPIRED"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = 'pos-m5' AND event_type = 'ENTRY_ORDER_FILLED'"
    ).fetchone()[0] == 0


def test_terminal_full_fill_requires_authenticated_terminal_order_state(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_order_fact

    terminal_at = _seed_terminal_no_fill_command(conn)
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="LIVE",
        remaining_size="0",
        matched_size="10",
        source="REST",
        observed_at=terminal_at + timedelta(minutes=1),
        raw_payload_hash=hashlib.sha256(b"live-zero-remainder-order").hexdigest(),
        raw_payload_json={"proof": "authenticated_point_order"},
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            positions=[position(token_id=YES_TOKEN, size="10")],
            trades=[
                trade(
                    trade_id="trade-late-live-order",
                    order_id="ord-m5",
                    size="10",
                    price="0.50",
                    status="CONFIRMED",
                )
            ],
        ),
        conn,
        context="periodic",
        observed_at=terminal_at + timedelta(minutes=2),
    )

    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()["state"] == "EXPIRED"
    assert conn.execute(
        "SELECT COUNT(*) FROM collateral_unsettled_proceeds "
        "WHERE command_id = 'cmd-m5'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events "
        "WHERE position_id = 'pos-m5' AND event_type = 'ENTRY_ORDER_FILLED'"
    ).fetchone()[0] == 0


def test_authenticated_terminal_full_fill_converts_all_collateral_atomically(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.venue_command_repo import append_order_fact

    terminal_at = _seed_terminal_no_fill_command(conn)
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="MATCHED",
        remaining_size="0",
        matched_size="10",
        source="REST",
        observed_at=terminal_at + timedelta(minutes=1),
        raw_payload_hash=hashlib.sha256(b"matched-full-late-order").hexdigest(),
        raw_payload_json={"proof": "authenticated_point_order"},
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            positions=[position(token_id=YES_TOKEN, size="10")],
            trades=[
                trade(
                    trade_id="trade-late-terminal-full",
                    order_id="ord-m5",
                    size="10",
                    price="0.50",
                    status="CONFIRMED",
                )
            ],
        ),
        conn,
        context="periodic",
        observed_at=terminal_at + timedelta(minutes=2),
    )

    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()["state"] == "FILLED"
    reservation = conn.execute(
        """
        SELECT amount, converted_amount, released_at
          FROM collateral_reservations
         WHERE command_id = 'cmd-m5'
        """
    ).fetchone()
    assert reservation["amount"] == 5000000
    assert reservation["converted_amount"] == 5000000
    assert reservation["released_at"] is not None
    unsettled = conn.execute(
        """
        SELECT direction, amount_micro
          FROM collateral_unsettled_proceeds
         WHERE command_id = 'cmd-m5'
        """
    ).fetchone()
    assert dict(unsettled) == {
        "direction": "OUTGOING_DEDUCTION",
        "amount_micro": 5000000,
    }
    projection = conn.execute(
        "SELECT phase, shares FROM position_current WHERE position_id = 'pos-m5'"
    ).fetchone()
    assert projection["phase"] == "active"
    assert Decimal(str(projection["shares"])) == Decimal("10")


def test_terminal_partial_later_confirmed_remainder_reprojects_cumulative_fill(conn):
    """Late facts after terminal partial restore the full capital atomically."""
    from src.execution.command_recovery import reconcile_authenticated_entry_trade_facts
    from src.execution.exchange_reconcile import (
        persisted_terminal_late_entry_fill_command_ids,
        reconcile_persisted_terminal_late_entry_fills,
    )
    from src.state.venue_command_repo import append_event, append_order_fact, append_trade_fact

    seed_command(conn, size=10, price=0.50)
    seed_position_baseline(conn)
    conn.execute(
        """
        INSERT INTO collateral_ledger_snapshots (
            pusd_balance_micro, pusd_allowance_micro,
            usdc_e_legacy_balance_micro, ctf_token_balances_json,
            ctf_token_allowances_json, captured_at, authority_tier
        ) VALUES (100000000, 100000000, 0, '{}', '{}', ?, 'CHAIN')
        """,
        (NOW.isoformat(),),
    )
    conn.execute(
        """
        INSERT INTO collateral_reservations (
            command_id, reservation_type, amount, created_at,
            released_at, release_reason, converted_amount
        ) VALUES ('cmd-m5', 'PUSD_BUY', 5000000, ?, NULL, NULL, 0)
        """,
        (NOW.isoformat(),),
    )
    first_fill_at = NOW + timedelta(minutes=1)
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="PARTIALLY_MATCHED",
        remaining_size="8",
        matched_size="2",
        source="WS_USER",
        observed_at=first_fill_at,
        raw_payload_hash=hashlib.sha256(b"terminal-partial-prefix-order").hexdigest(),
        raw_payload_json={"proof": "authenticated_prefix"},
    )
    append_trade_fact(
        conn,
        trade_id="trade-terminal-partial-prefix",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="2",
        fill_price="0.50",
        source="WS_USER",
        observed_at=first_fill_at,
        raw_payload_hash=hashlib.sha256(b"terminal-partial-prefix-trade").hexdigest(),
        raw_payload_json={"proof": "authenticated_prefix"},
    )
    assert reconcile_authenticated_entry_trade_facts(
        conn, command_id="cmd-m5"
    ) == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}

    terminal_at = first_fill_at + timedelta(minutes=1)
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="PARTIALLY_MATCHED",
        remaining_size="0",
        matched_size="2",
        source="REST",
        observed_at=terminal_at,
        raw_payload_hash=hashlib.sha256(b"terminal-partial-terminal-order").hexdigest(),
        raw_payload_json={"proof": "terminal_partial_remainder"},
    )
    append_event(
        conn,
        command_id="cmd-m5",
        event_type="EXPIRED",
        occurred_at=terminal_at.isoformat(),
        payload={
            "reason": "partial_remainder_absent_from_exchange_open_orders",
            "proof_class": "confirmed_fill_plus_point_order_terminal_remainder",
            "command_id": "cmd-m5",
            "venue_order_id": "ord-m5",
            "positive_fill_size": "2",
        },
    )
    conn.execute(
        """
        UPDATE collateral_reservations
           SET converted_amount = 1000000,
               release_reason = 'CONVERTED_ON_FILL'
         WHERE command_id = 'cmd-m5'
        """
    )
    conn.execute(
        """
        UPDATE collateral_unsettled_proceeds
           SET settled_at = ?, settle_reason = 'BALANCE_REFRESH_OBSERVED'
         WHERE command_id = 'cmd-m5'
        """,
        (terminal_at.isoformat(),),
    )
    assert persisted_terminal_late_entry_fill_command_ids(conn) == []

    late_fill_at = terminal_at + timedelta(minutes=1)
    append_trade_fact(
        conn,
        trade_id="trade-terminal-partial-remainder",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="7.9995",
        fill_price="0.50",
        source="WS_USER",
        observed_at=late_fill_at,
        raw_payload_hash=hashlib.sha256(b"terminal-partial-remainder-trade").hexdigest(),
        raw_payload_json={"proof": "authenticated_late_remainder"},
    )

    assert persisted_terminal_late_entry_fill_command_ids(conn) == ["cmd-m5"]
    summary = reconcile_persisted_terminal_late_entry_fills(
        conn,
        command_id="cmd-m5",
        observed_at=late_fill_at + timedelta(seconds=1),
    )

    assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()["state"] == "FILLED"
    position = conn.execute(
        "SELECT shares, cost_basis_usd FROM position_current WHERE position_id = 'pos-m5'"
    ).fetchone()
    assert Decimal(str(position["shares"])) == Decimal("9.9995")
    assert Decimal(str(position["cost_basis_usd"])) == Decimal("4.99975")
    execution = conn.execute(
        "SELECT shares, fill_price FROM execution_fact "
        "WHERE command_id = 'cmd-m5' AND order_role = 'entry'"
    ).fetchone()
    assert Decimal(str(execution["shares"])) == Decimal("9.9995")
    assert Decimal(str(execution["fill_price"])) == Decimal("0.5")
    reservation = conn.execute(
        "SELECT amount, converted_amount, released_at FROM collateral_reservations "
        "WHERE command_id = 'cmd-m5'"
    ).fetchone()
    assert reservation["amount"] == 5000000
    assert reservation["converted_amount"] == 4999750
    assert reservation["released_at"] is not None
    unsettled = conn.execute(
        "SELECT amount_micro, settled_at FROM collateral_unsettled_proceeds "
        "WHERE command_id = 'cmd-m5'"
    ).fetchone()
    assert dict(unsettled) == {"amount_micro": 4999750, "settled_at": None}


def test_resolved_obligation_still_drains_persisted_terminal_late_fill(conn):
    from src.execution.command_recovery import (
        reconcile_terminal_entry_exposure_obligations,
    )
    from src.state.entry_exposure_obligation import open_entry_exposure_obligation
    from src.state.schema.entry_exposure_obligations_schema import ensure_table
    from src.state.venue_command_repo import append_order_fact, append_trade_fact

    terminal_at = _seed_terminal_no_fill_command(conn)
    ensure_table(conn)
    open_entry_exposure_obligation(
        conn,
        command_id="cmd-m5",
        owner_domain="trade",
        token_id=YES_TOKEN,
        condition_id="condition-m5",
        shares=10.0,
        cost_basis_usd=5.0,
        now=NOW.isoformat(),
    )
    conn.execute(
        "UPDATE entry_exposure_obligations SET status = 'RESOLVED', "
        "resolved_at = ?, updated_at = ? WHERE command_id = 'cmd-m5'",
        (terminal_at.isoformat(), terminal_at.isoformat()),
    )
    conn.execute(
        "UPDATE position_current SET phase = 'day0_window', shares = 6, "
        "cost_basis_usd = 3, size_usd = 3 WHERE position_id = 'pos-m5'"
    )
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="PARTIALLY_MATCHED",
        remaining_size="4",
        matched_size="6",
        source="REST",
        observed_at=terminal_at + timedelta(minutes=1),
        raw_payload_hash=hashlib.sha256(b"durable-drain-order").hexdigest(),
        raw_payload_json={"proof": "authenticated_point_order"},
    )
    append_trade_fact(
        conn,
        trade_id="trade-durable-terminal-drain",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="6",
        fill_price="0.50",
        source="REST",
        observed_at=terminal_at + timedelta(minutes=1),
        raw_payload_hash=hashlib.sha256(b"durable-drain-trade").hexdigest(),
        raw_payload_json={"proof": "authenticated_trade"},
    )

    summary = reconcile_terminal_entry_exposure_obligations(conn)

    assert summary["terminal_late_fill_corrections"] == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    assert summary["scanned"] == 0
    assert summary["stayed"] == 0
    assert summary["errors"] == 0
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = 'cmd-m5'"
    ).fetchone()["state"] == "PARTIAL"
    reservation = conn.execute(
        """
        SELECT amount, released_at, converted_amount
          FROM collateral_reservations
         WHERE command_id = 'cmd-m5'
        """
    ).fetchone()
    assert dict(reservation) == {
        "amount": 2000000,
        "released_at": None,
        "converted_amount": 0,
    }


def test_numeric_terminal_no_fill_claim_is_not_scheduled_as_boolean_truth(conn):
    from src.execution.exchange_reconcile import (
        persisted_terminal_late_entry_fill_command_ids,
    )
    from src.state.venue_command_repo import append_order_fact, append_trade_fact

    terminal_at = _seed_terminal_no_fill_command(conn, terminal_no_fill=1)
    conn.execute(
        "UPDATE position_current SET phase = 'active', shares = 6, "
        "cost_basis_usd = 3, size_usd = 3 WHERE position_id = 'pos-m5'"
    )
    append_order_fact(
        conn,
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="PARTIALLY_MATCHED",
        remaining_size="4",
        matched_size="6",
        source="REST",
        observed_at=terminal_at + timedelta(minutes=1),
        raw_payload_hash=hashlib.sha256(b"numeric-no-fill-order").hexdigest(),
        raw_payload_json={"proof": "authenticated_point_order"},
    )
    append_trade_fact(
        conn,
        trade_id="trade-numeric-no-fill",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="6",
        fill_price="0.50",
        source="REST",
        observed_at=terminal_at + timedelta(minutes=1),
        raw_payload_hash=hashlib.sha256(b"numeric-no-fill-trade").hexdigest(),
        raw_payload_json={"proof": "authenticated_trade"},
    )

    assert persisted_terminal_late_entry_fill_command_ids(conn) == []


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


def test_fok_increment_aggregates_into_one_canonical_position(conn):
    """One position owns many fills; a top-up never opens or overwrites a lot."""

    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=24, price=0.67)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    first = trade(
        trade_id="trade-initial",
        order_id="ord-m5",
        size="24",
        price="0.67",
        status="CONFIRMED",
    )
    run_reconcile_sweep(
        FakeM5Adapter(trades=[first]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    seed_command(
        conn,
        command_id="cmd-top-up",
        venue_order_id="ord-top-up",
        position_id="pos-m5",
        size=19.25,
        price=0.64,
        created_at=NOW + timedelta(minutes=1),
        order_type="FOK",
    )
    # Simulate the real Chain > Chronicler writer shape: chain mirror publishes
    # the new token quantity before the local command/event fold but retains the
    # old cost basis because chain inventory does not carry acquisition cost.
    # Reconciliation must derive $28.40 from command fills, not reuse $16.08.
    conn.execute(
        """
        UPDATE position_current
           SET chain_shares = 43.25,
               chain_cost_basis_usd = 16.08,
               chain_avg_price = 0.67,
               chain_state = 'synced'
         WHERE position_id = 'pos-m5'
        """
    )
    increment = trade(
        trade_id="trade-top-up",
        order_id="ord-top-up",
        size="19.25",
        price="0.64",
        status="CONFIRMED",
    )
    run_reconcile_sweep(
        FakeM5Adapter(trades=[increment]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=1),
    )

    projection = conn.execute(
        """
        SELECT position_id, phase, order_id, shares, cost_basis_usd, entry_price,
               chain_shares, chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-m5'
        """
    ).fetchone()
    assert projection["phase"] == "active"
    assert projection["order_id"] == "ord-m5"
    assert Decimal(str(projection["shares"])) == Decimal("43.25")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("28.40")
    assert Decimal(str(projection["chain_shares"])) == Decimal("43.25")
    assert Decimal(str(projection["chain_cost_basis_usd"])) == Decimal("16.08")
    assert float(projection["entry_price"]) == pytest.approx(
        float(Decimal("28.40") / Decimal("43.25")), abs=1e-15
    )

    entry_events = conn.execute(
        """
        SELECT event_id, order_id, command_id, phase_before, phase_after
          FROM position_events
         WHERE position_id = 'pos-m5'
           AND event_type = 'ENTRY_ORDER_FILLED'
         ORDER BY sequence_no
        """
    ).fetchall()
    assert [(row["order_id"], row["command_id"]) for row in entry_events] == [
        ("ord-m5", "cmd-m5"),
        ("ord-top-up", "cmd-top-up"),
    ]
    assert entry_events[-1]["phase_before"] == "active"
    assert entry_events[-1]["phase_after"] == "active"

    execution_rows = conn.execute(
        """
        SELECT intent_id, command_id, shares, fill_price
          FROM execution_fact
         WHERE position_id = 'pos-m5'
         ORDER BY intent_id
        """
    ).fetchall()
    assert [(row["intent_id"], row["command_id"]) for row in execution_rows] == [
        ("pos-m5:entry", "cmd-m5"),
        ("pos-m5:entry:cmd-top-up", "cmd-top-up"),
    ]

    run_reconcile_sweep(
        FakeM5Adapter(trades=[increment]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=2),
    )
    unchanged = conn.execute(
        "SELECT shares, cost_basis_usd FROM position_current WHERE position_id='pos-m5'"
    ).fetchone()
    assert Decimal(str(unchanged["shares"])) == Decimal("43.25")
    assert Decimal(str(unchanged["cost_basis_usd"])) == Decimal("28.40")
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id='pos-m5' "
        "AND event_type='ENTRY_ORDER_FILLED'"
    ).fetchone()[0] == 2

    from src.state.db import query_portfolio_loader_view

    conn.execute(
        """
        INSERT INTO execution_fact (
            intent_id, position_id, command_id, order_role, posted_at,
            fill_price, shares, venue_status, terminal_exec_status
        ) VALUES (
            'legacy-duplicate-cmd-m5', 'pos-m5', 'cmd-m5', 'entry', ?,
            0.67, 24.0, 'CONFIRMED', 'filled'
        )
        """,
        (NOW.isoformat(),),
    )
    conn.execute(
        "UPDATE position_current SET fill_authority='venue_confirmed_full' "
        "WHERE position_id='pos-m5'"
    )
    runtime = query_portfolio_loader_view(conn, runtime_exposure_only=True)
    assert runtime["status"] == "ok"
    assert len(runtime["positions"]) == 1
    loaded = runtime["positions"][0]
    assert Decimal(str(loaded["shares"])) == Decimal("43.25")
    assert Decimal(str(loaded["cost_basis_usd"])) == Decimal("28.40")
    assert loaded["entry_economics_source"] == "execution_fact"


def test_partial_fak_increment_projects_known_fill_and_keeps_remainder_obligation_open(conn):
    """A known top-up fill is canonical exposure even while its remainder is unresolved."""

    from src.execution.exchange_reconcile import run_reconcile_sweep
    from src.state.entry_exposure_obligation import open_entry_exposure_obligation
    from src.state.schema.entry_exposure_obligations_schema import ensure_table

    seed_command(conn, size=24, price=0.67)
    ensure_table(conn)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-initial",
                    order_id="ord-m5",
                    size="24",
                    price="0.67",
                    status="CONFIRMED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    seed_command(
        conn,
        command_id="cmd-partial-top-up",
        venue_order_id="ord-partial-top-up",
        position_id="pos-m5",
        size=15,
        price=0.08,
        created_at=NOW + timedelta(minutes=1),
        order_type="FAK",
    )
    open_entry_exposure_obligation(
        conn,
        command_id="cmd-partial-top-up",
        owner_domain="trade",
        token_id=YES_TOKEN,
        condition_id="condition-m5",
        shares=15,
        cost_basis_usd=1.2,
        now=(NOW + timedelta(minutes=1)).isoformat(),
    )
    partial = trade(
        trade_id="trade-partial-top-up",
        order_id="ord-partial-top-up",
        size="11",
        price="0.058",
        status="CONFIRMED",
    )

    run_reconcile_sweep(
        FakeM5Adapter(trades=[partial]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=1),
    )

    projection = conn.execute(
        "SELECT shares, cost_basis_usd, entry_price FROM position_current WHERE position_id='pos-m5'"
    ).fetchone()
    assert Decimal(str(projection["shares"])) == Decimal("35")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("16.718")
    assert float(projection["entry_price"]) == pytest.approx(float(Decimal("16.718") / Decimal("35")))
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id='pos-m5' "
        "AND event_type='ENTRY_ORDER_FILLED' AND command_id='cmd-partial-top-up'"
    ).fetchone()[0] == 1
    execution = conn.execute(
        "SELECT shares, fill_price, venue_status, terminal_exec_status FROM execution_fact "
        "WHERE command_id='cmd-partial-top-up'"
    ).fetchone()
    assert dict(execution) == {
        "shares": 11.0,
        "fill_price": 0.058,
        "venue_status": "PARTIAL",
        "terminal_exec_status": "partial",
    }
    obligation = conn.execute(
        "SELECT status, shares, cost_basis_usd FROM entry_exposure_obligations "
        "WHERE command_id='cmd-partial-top-up'"
    ).fetchone()
    assert dict(obligation) == {"status": "OPEN", "shares": 15.0, "cost_basis_usd": 1.2}

    run_reconcile_sweep(
        FakeM5Adapter(trades=[partial]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=2),
    )
    unchanged = conn.execute(
        "SELECT shares, cost_basis_usd FROM position_current WHERE position_id='pos-m5'"
    ).fetchone()
    assert Decimal(str(unchanged["shares"])) == Decimal("35")
    assert Decimal(str(unchanged["cost_basis_usd"])) == Decimal("16.718")
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id='pos-m5' "
        "AND event_type='ENTRY_ORDER_FILLED' AND command_id='cmd-partial-top-up'"
    ).fetchone()[0] == 1

    later_fill = trade(
        trade_id="trade-partial-top-up-2",
        order_id="ord-partial-top-up",
        size="2",
        price="0.06",
        status="CONFIRMED",
    )
    run_reconcile_sweep(
        FakeM5Adapter(trades=[partial, later_fill]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=3),
    )
    advanced = conn.execute(
        "SELECT shares, cost_basis_usd FROM position_current WHERE position_id='pos-m5'"
    ).fetchone()
    assert Decimal(str(advanced["shares"])) == Decimal("37")
    assert Decimal(str(advanced["cost_basis_usd"])) == Decimal("16.838")
    advanced_execution = conn.execute(
        "SELECT shares, fill_price, terminal_exec_status FROM execution_fact "
        "WHERE command_id='cmd-partial-top-up'"
    ).fetchone()
    assert advanced_execution["shares"] == 13.0
    assert advanced_execution["fill_price"] == pytest.approx(float(Decimal("0.758") / Decimal("13")))
    assert advanced_execution["terminal_exec_status"] == "partial"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id='pos-m5' "
        "AND event_type='ENTRY_ORDER_FILLED' AND command_id='cmd-partial-top-up'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT status FROM entry_exposure_obligations WHERE command_id='cmd-partial-top-up'"
    ).fetchone()[0] == "OPEN"


def test_late_confirmed_exit_trade_leg_respects_close_intent_and_aggregates_once(conn):
    """Independent SELL legs never invent close intent and close once when exact intent exists."""

    from src.execution.exchange_reconcile import run_reconcile_sweep

    def seed_exit_case(*, position_id, command_id, order_id, token, close_intent):
        seed_position_baseline(conn, position_id=position_id, order_id=f"{order_id}-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active', token_id = ?, order_id = ?, order_status = 'filled',
                   shares = 4, chain_shares = 4, cost_basis_usd = 0.8,
                   chain_cost_basis_usd = 0.8, entry_price = 0.2, updated_at = ?
             WHERE position_id = ?
            """,
            (token, f"{order_id}-entry", NOW.isoformat(), position_id),
        )
        seed_command(
            conn,
            command_id=command_id,
            venue_order_id=order_id,
            position_id=position_id,
            token_id=token,
            side="SELL",
            size=4,
            price=0.2,
            state="ACKED",
            exit_close_position=close_intent,
        )

    # No EXIT_INTENT: authenticated legs are retained but cannot close the position.
    no_intent = {
        "position_id": "pos-late-exit-no-intent",
        "command_id": "cmd-late-exit-no-intent",
        "order_id": "ord-late-exit-no-intent",
        "token": "late-exit-no-intent-token",
    }
    seed_exit_case(**no_intent, close_intent=None)
    first = trade(
        trade_id="trade-late-exit-no-intent-a",
        order_id=no_intent["order_id"],
        size="2",
        price="0.20",
        status="CONFIRMED",
    )
    second = trade(
        trade_id="trade-late-exit-no-intent-b",
        order_id=no_intent["order_id"],
        size="2",
        price="0.30",
        status="CONFIRMED",
    )
    run_reconcile_sweep(FakeM5Adapter(trades=[first]), conn, context="periodic", observed_at=NOW)
    run_reconcile_sweep(
        FakeM5Adapter(trades=[first, second]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=1),
    )
    assert conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (no_intent["position_id"],),
    ).fetchone()["phase"] == "active"
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'EXIT_ORDER_FILLED'",
        (no_intent["position_id"],),
    ).fetchone()[0] == 0

    # Exact EXIT_INTENT: the late independent leg supplies the aggregate and one close event.
    with_intent = {
        "position_id": "pos-late-exit-with-intent",
        "command_id": "cmd-late-exit-with-intent",
        "order_id": "ord-late-exit-with-intent",
        "token": "late-exit-with-intent-token",
    }
    seed_exit_case(**with_intent, close_intent=True)
    first_intent = trade(
        trade_id="trade-late-exit-with-intent-a",
        order_id=with_intent["order_id"],
        size="2",
        price="0.20",
        status="CONFIRMED",
    )
    second_intent = trade(
        trade_id="trade-late-exit-with-intent-b",
        order_id=with_intent["order_id"],
        size="2",
        price="0.30",
        status="CONFIRMED",
    )
    run_reconcile_sweep(
        FakeM5Adapter(trades=[first_intent]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=2),
    )
    run_reconcile_sweep(
        FakeM5Adapter(trades=[first_intent, second_intent]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(minutes=3),
    )
    assert conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (with_intent["position_id"],),
    ).fetchone()["phase"] == "economically_closed"
    assert conn.execute(
        "SELECT shares, fill_price FROM execution_fact WHERE intent_id = ?",
        (f"{with_intent['position_id']}:exit:{with_intent['command_id']}",),
    ).fetchone()[:] == pytest.approx((4.0, 0.25))
    # The canonical command aggregate promotes the terminal transition once;
    # re-observing either leg cannot append a duplicate confirmation.
    assert event_types(conn, with_intent["command_id"]).count("FILL_CONFIRMED") == 1
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = ?",
        (with_intent["command_id"],),
    ).fetchone()["state"] == "FILLED"


def test_entry_fill_projection_reuses_writer_for_terminal_obligation(monkeypatch):
    """The fill writer releases proven capital without opening another connection."""

    from src.execution import command_recovery, exchange_reconcile
    from src.state import db as state_db
    from src.state import projection as state_projection

    calls = []

    class RecordingConnection:
        def execute(self, sql, *_args):
            calls.append(("sql", sql.strip().split()[0]))
            return SimpleNamespace()

    conn = RecordingConnection()
    monkeypatch.setattr(
        state_projection,
        "upsert_position_current",
        lambda used_conn, _projection: calls.append(("projection", used_conn)),
    )
    monkeypatch.setattr(
        state_db,
        "log_execution_fact",
        lambda used_conn, **_kwargs: calls.append(("execution", used_conn)),
    )
    monkeypatch.setattr(
        exchange_reconcile,
        "_append_entry_position_lots_for_command",
        lambda used_conn, **_kwargs: calls.append(("lots", used_conn)),
    )

    def release(used_conn, *, command_id):
        calls.append(("obligation", used_conn, command_id))
        return {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}

    monkeypatch.setattr(
        command_recovery,
        "reconcile_terminal_entry_exposure_obligations",
        release,
    )
    exchange_reconcile._apply_entry_fill_projection_and_execution_fact(
        conn,
        events=[],
        projection={"position_id": "pos-terminal"},
        position=SimpleNamespace(
            trade_id="pos-terminal",
            strategy_key="forecast_qkernel_entry",
            order_posted_at=NOW.isoformat(),
        ),
        command={
            "command_id": "cmd-terminal",
            "decision_id": "dec-terminal",
            "created_at": NOW.isoformat(),
            "price": 0.29,
        },
        observed_at=NOW,
        order_status="partial",
        shares=Decimal("9.992813"),
        entry_price=Decimal("0.29"),
        upsert_only=True,
        incremental=False,
    )

    assert calls[-2:] == [
        ("sql", "RELEASE"),
        ("obligation", conn, "cmd-terminal"),
    ]
    assert all(
        call[1] is conn
        for call in calls
        if call[0] in {"projection", "execution", "lots", "obligation"}
    )


def test_maker_fill_materializes_missing_position_projection_after_cancel(
    monkeypatch, conn
):
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
    forecasts = sqlite3.connect(":memory:")
    forecasts.row_factory = sqlite3.Row
    forecasts.execute(
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
    forecasts.execute(
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
    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        lambda: forecasts,
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
               token_id, no_token_id, condition_id, entry_method,
               fill_authority
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
    assert projection["entry_method"] == "qkernel_spine"
    assert projection["fill_authority"] == "venue_confirmed_partial"
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


def test_linked_edli_fill_projects_immutable_entry_probability(monkeypatch, conn):
    """Fill recovery consumes the BUY certificate before writing the position."""

    from src.execution import command_recovery
    from src.execution.exchange_reconcile import run_reconcile_sweep

    yes_token = "manila-edli-yes"
    seed_command(
        conn,
        command_id="cmd-edli-missing-projection",
        venue_order_id="ord-edli-missing-projection",
        position_id="pos-edli-missing-projection",
        token_id=yes_token,
        size=20.6,
        price=0.15,
        snapshot_token_id=yes_token,
        snapshot_no_token_id=f"{yes_token}-no",
    )
    conn.execute(
        """
        UPDATE venue_commands
           SET decision_id = 'edli_exec_cmd:event-manila:intent-manila:manila-edli-yes'
         WHERE command_id = 'cmd-edli-missing-projection'
        """
    )
    monkeypatch.setattr(
        command_recovery,
        "_hydrate_command_execution_identity",
        lambda _conn, command: dict(command),
    )
    monkeypatch.setattr(
        command_recovery,
        "_decision_log_trade_case_for_command",
        lambda _conn, _command: (
            {
                "p_posterior": 0.593664633019502,
                "entry_method": "qkernel_spine",
                "strategy_key": "forecast_qkernel_entry",
                "edge_source": "forecast_qkernel_entry",
                "discovery_mode": "update_reaction",
                "decision_snapshot_id": "rmf-Manila|2026-09-04|high|2026-09-02",
            },
            None,
        ),
    )
    forecasts = sqlite3.connect(":memory:")
    forecasts.row_factory = sqlite3.Row
    forecasts.execute(
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
    forecasts.execute(
        """
        INSERT INTO market_events (
            market_slug, city, target_date, condition_id, token_id, range_label, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "highest-temperature-in-manila-on-september-4-2026",
            "Manila",
            "2026-09-04",
            "condition-m5",
            yes_token,
            "Will the highest temperature in Manila be 29°C on September 4?",
            "Yes",
        ),
    )
    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        lambda: forecasts,
    )
    adapter = FakeM5Adapter(
        positions=[position(token_id=yes_token, size="20.6")],
        trades=[
            trade(
                trade_id="trade-edli-missing-projection",
                order_id="ord-edli-missing-projection",
                size="20.6",
                price="0.15",
                status="CONFIRMED",
                asset_id=yes_token,
            )
        ],
    )

    run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    projection = conn.execute(
        """
        SELECT p_posterior, entry_method, strategy_key, edge_source,
               discovery_mode, decision_snapshot_id
          FROM position_current
         WHERE position_id = 'pos-edli-missing-projection'
        """
    ).fetchone()
    assert dict(projection) == {
        "p_posterior": pytest.approx(0.593664633019502),
        "entry_method": "qkernel_spine",
        "strategy_key": "forecast_qkernel_entry",
        "edge_source": "forecast_qkernel_entry",
        "discovery_mode": "update_reaction",
        "decision_snapshot_id": "rmf-Manila|2026-09-04|high|2026-09-02",
    }


def test_fill_recovery_reads_exact_bin_from_canonical_forecasts(monkeypatch, conn):
    """The event slug is family identity, never the settlement outcome key."""

    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(
        conn,
        command_id="cmd-canonical-bin",
        venue_order_id="ord-canonical-bin",
        position_id="pos-canonical-bin",
        token_id=YES_TOKEN,
        state="FILLED",
    )
    conn.execute(
        """
        CREATE TABLE market_events (
            event_id INTEGER PRIMARY KEY, market_slug TEXT, city TEXT,
            target_date TEXT, temperature_metric TEXT, condition_id TEXT,
            token_id TEXT, range_label TEXT, outcome TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO market_events VALUES (
            1, 'highest-temperature-in-singapore-on-july-24-2026',
            'Singapore', '2026-07-24', 'high', 'condition-m5', ?, ?, ?
        )
        """,
        (
            YES_TOKEN,
            "Will the highest temperature in Singapore be 31°C on July 24?",
            "Will the highest temperature in Singapore be 31°C on July 24?",
        ),
    )
    forecasts = sqlite3.connect(":memory:")
    forecasts.row_factory = sqlite3.Row
    forecasts.execute(
        """
        CREATE TABLE market_events (
            event_id INTEGER PRIMARY KEY,
            market_slug TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            outcome TEXT
        )
        """
    )
    forecasts.execute(
        """
        INSERT INTO market_events (
            event_id, market_slug, city, target_date, temperature_metric,
            condition_id, token_id, range_label, outcome
        ) VALUES (1, ?, ?, ?, 'high', 'condition-m5', ?, ?, ?)
        """,
        (
            "highest-temperature-in-singapore-on-july-24-2026",
            "Singapore",
            "2026-07-24",
            YES_TOKEN,
            "Will the highest temperature in Singapore be 30°C on July 24?",
            "Will the highest temperature in Singapore be 30°C on July 24?",
        ),
    )
    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        lambda: forecasts,
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-canonical-bin",
                    order_id="ord-canonical-bin",
                    size="10",
                    price="0.50",
                    status="CONFIRMED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    projection = conn.execute(
        "SELECT bin_label FROM position_current WHERE position_id='pos-canonical-bin'"
    ).fetchone()
    assert projection["bin_label"] == (
        "Will the highest temperature in Singapore be 30°C on July 24?"
    )


def test_fill_recovery_rejects_condition_match_with_wrong_yes_token(monkeypatch, conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(
        conn,
        command_id="cmd-wrong-market-token",
        venue_order_id="ord-wrong-market-token",
        position_id="pos-wrong-market-token",
        token_id=YES_TOKEN,
        state="FILLED",
    )
    forecasts = sqlite3.connect(":memory:")
    forecasts.row_factory = sqlite3.Row
    forecasts.execute(
        """
        CREATE TABLE market_events (
            event_id INTEGER PRIMARY KEY, market_slug TEXT, city TEXT,
            target_date TEXT, temperature_metric TEXT, condition_id TEXT,
            token_id TEXT, range_label TEXT, outcome TEXT
        )
        """
    )
    forecasts.execute(
        """
        INSERT INTO market_events VALUES (
            1, 'highest-temperature-in-singapore-on-july-24-2026',
            'Singapore', '2026-07-24', 'high', 'condition-m5',
            'different-yes-token', ?, ?
        )
        """,
        (
            "Will the highest temperature in Singapore be 30°C on July 24?",
            "Will the highest temperature in Singapore be 30°C on July 24?",
        ),
    )
    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        lambda: forecasts,
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id="trade-wrong-market-token",
                    order_id="ord-wrong-market-token",
                    size="10",
                    price="0.50",
                    status="CONFIRMED",
                )
            ]
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert conn.execute(
        "SELECT 1 FROM position_current WHERE position_id='pos-wrong-market-token'"
    ).fetchone() is None


def test_entry_identity_db_error_persists_finding_and_programming_error_escapes(
    monkeypatch, conn
):
    from src.execution.exchange_reconcile import _ensure_entry_fill_position_event

    seed_command(
        conn,
        command_id="cmd-identity-read-error",
        venue_order_id="ord-identity-read-error",
        position_id="pos-identity-read-error",
        token_id=YES_TOKEN,
        state="FILLED",
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id='cmd-identity-read-error'"
        ).fetchone()
    )

    def db_error():
        raise sqlite3.OperationalError("forecast identity unavailable")

    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        db_error,
    )
    _ensure_entry_fill_position_event(
        conn,
        command=command,
        venue_order_id="ord-identity-read-error",
        filled_size="10",
        fill_price="0.50",
        observed_at=NOW,
        context="ws_gap",
    )
    finding = conn.execute(
        """
        SELECT evidence_json
          FROM exchange_reconcile_findings
         WHERE kind='position_drift'
           AND subject_id='entry_identity:cmd-identity-read-error'
           AND context='ws_gap'
           AND resolved_at IS NULL
        """
    ).fetchone()
    assert json.loads(finding["evidence_json"])["reason"] == (
        "canonical_entry_identity_read_error"
    )
    assert conn.execute(
        "SELECT 1 FROM position_current WHERE position_id='pos-identity-read-error'"
    ).fetchone() is None

    def programming_error():
        raise RuntimeError("unexpected identity bug")

    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        programming_error,
    )
    with pytest.raises(RuntimeError, match="unexpected identity bug"):
        _ensure_entry_fill_position_event(
            conn,
            command=command,
            venue_order_id="ord-identity-read-error",
            filled_size="10",
            fill_price="0.50",
            observed_at=NOW,
            context="ws_gap",
        )


def test_fill_reconcile_repairs_slug_projection_without_touching_economics(
    monkeypatch, conn
):
    """A recovered slug projection regains the posterior key on next reconcile."""

    from src.execution.exchange_reconcile import (
        _ensure_entry_fill_position_event,
        run_reconcile_sweep,
    )

    seed_command(conn, state="FILLED")
    seed_position_baseline(conn)
    append_trade_fact(conn, trade_id="trade-bin-repair")
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id='cmd-m5'"
        ).fetchone()
    )
    _ensure_entry_fill_position_event(
        conn,
        command=command,
        venue_order_id="ord-m5",
        filled_size="10",
        fill_price="0.50",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
    )
    conn.execute(
        """
        UPDATE position_current
           SET city='Singapore', cluster='Singapore', target_date='2026-07-24',
               bin_label='highest-temperature-in-singapore-on-july-24-2026',
               condition_id='condition-m5', market_id='condition-m5',
               temperature_metric='high', token_id=?, no_token_id=?,
               strategy_key='forecast_qkernel_entry', entry_method='qkernel_spine',
               last_monitor_prob=0.31, last_monitor_edge=0.23,
               last_monitor_market_price=0.08, last_monitor_prob_is_fresh=1,
               exit_reason='TEST_HOLD'
         WHERE position_id='pos-m5'
        """,
        (YES_TOKEN, f"{YES_TOKEN}-no"),
    )
    canonical_label = "Will the highest temperature in Singapore be 30°C on July 24?"
    def forecasts_connection():
        forecasts = sqlite3.connect(":memory:")
        forecasts.row_factory = sqlite3.Row
        forecasts.execute(
            """
            CREATE TABLE market_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, market_slug TEXT,
                city TEXT, target_date TEXT, temperature_metric TEXT,
                condition_id TEXT, token_id TEXT, range_label TEXT, outcome TEXT
            )
            """
        )
        forecasts.execute(
            """
            INSERT INTO market_events (
                market_slug, city, target_date, temperature_metric, condition_id,
                token_id, range_label, outcome
            ) VALUES (?, 'Singapore', '2026-07-24', 'high', 'condition-m5', ?, ?, ?)
            """,
            (
                "highest-temperature-in-singapore-on-july-24-2026",
                YES_TOKEN,
                canonical_label,
                canonical_label,
            ),
        )
        return forecasts

    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        forecasts_connection,
    )
    adapter = FakeM5Adapter(
        trades=[
            trade(
                trade_id="trade-bin-repair",
                order_id="ord-m5",
                size="10",
                price="0.50",
                status="CONFIRMED",
            )
        ]
    )
    preserved_columns = (
        "shares, cost_basis_usd, entry_price, phase, order_status, "
        "last_monitor_prob, last_monitor_edge, last_monitor_market_price, "
        "last_monitor_prob_is_fresh, exit_reason"
    )
    before = dict(
        conn.execute(
            f"SELECT {preserved_columns} FROM position_current WHERE position_id='pos-m5'"
        ).fetchone()
    )

    run_reconcile_sweep(adapter, conn, context="periodic", observed_at=NOW)

    projection = conn.execute(
        f"SELECT bin_label, {preserved_columns} FROM position_current "
        "WHERE position_id='pos-m5'"
    ).fetchone()
    assert projection["bin_label"] == canonical_label
    assert {column: projection[column] for column in before} == before
    repair = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id='pos-m5'
           AND event_type='MANUAL_OVERRIDE_APPLIED'
           AND source_module='src.execution.exchange_reconcile'
        """
    ).fetchone()
    assert json.loads(repair["payload_json"]) == {
        "condition_id": "condition-m5",
        "market_metadata_authority": "canonical_forecast_market_events",
        "new_bin_label": canonical_label,
        "old_bin_label": "highest-temperature-in-singapore-on-july-24-2026",
        "reason": "entry_bin_identity_projection_repair",
        "token_id": YES_TOKEN,
    }
    first_sequence = conn.execute(
        "SELECT MAX(sequence_no) FROM position_events WHERE position_id='pos-m5'"
    ).fetchone()[0]
    run_reconcile_sweep(
        adapter,
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id='pos-m5'
           AND event_type='MANUAL_OVERRIDE_APPLIED'
           AND source_module='src.execution.exchange_reconcile'
        """
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT MAX(sequence_no) FROM position_events WHERE position_id='pos-m5'"
    ).fetchone()[0] == first_sequence


def test_live_tick_repairs_recorded_fill_when_position_projection_is_missing(
    monkeypatch, conn
):
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
    forecasts = sqlite3.connect(":memory:")
    forecasts.row_factory = sqlite3.Row
    forecasts.execute(
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
    forecasts.execute(
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
    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        lambda: forecasts,
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


def test_live_tick_maker_fill_repair_is_bounded_to_missing_entry_projection(
    monkeypatch, conn
):
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
    forecasts = sqlite3.connect(":memory:")
    forecasts.row_factory = sqlite3.Row
    forecasts.execute(
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
    forecasts.execute(
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
    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only",
        lambda: forecasts,
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


@pytest.mark.parametrize(
    "phase", ["settled", "economically_closed", "admin_closed", "voided"]
)
def test_full_sweep_repairs_terminal_taker_buy_without_reprojecting(
    conn, phase
):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.venue_command_repo import append_trade_fact as append

    command_id = f"cmd-terminal-taker-{phase}"
    order_id = f"ord-terminal-taker-{phase}"
    position_id = f"pos-terminal-taker-{phase}"
    trade_id = f"trade-terminal-taker-{phase}"
    no_token = f"{YES_TOKEN}-no"
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=YES_TOKEN,
        size=11.6,
        price=0.09,
        state="FILLED",
        snapshot_token_id=YES_TOKEN,
        snapshot_no_token_id=no_token,
        snapshot_selected_token_id=YES_TOKEN,
        snapshot_outcome_label="YES",
        envelope_yes_token_id=YES_TOKEN,
        envelope_no_token_id=no_token,
        order_type="FAK",
        post_only=False,
    )
    seed_position_baseline(conn, position_id=position_id, order_id=order_id)
    conn.execute(
        "UPDATE position_current SET phase = ? WHERE position_id = ?",
        (phase, position_id),
    )
    before_position = dict(
        conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, realized_pnl_usd
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
    )
    before_events = [
        tuple(row)
        for row in conn.execute(
            """
            SELECT event_id, event_type, phase_before, phase_after, order_id, command_id
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no
            """,
            (position_id,),
        ).fetchall()
    ]
    before_execution = [
        tuple(row)
        for row in conn.execute(
            """
            SELECT intent_id, fill_price, shares, terminal_exec_status, command_id
              FROM execution_fact
             WHERE intent_id LIKE ?
             ORDER BY intent_id
            """,
            (f"{position_id}%",),
        ).fetchall()
    ]
    before_order_facts = [
        tuple(row)
        for row in conn.execute(
            """
            SELECT fact_id, venue_order_id, command_id, state, remaining_size,
                   matched_size, source, observed_at, venue_timestamp,
                   local_sequence, raw_payload_hash, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = ? AND venue_order_id = ?
             ORDER BY local_sequence, fact_id
            """,
            (command_id, order_id),
        ).fetchall()
    ]
    raw = {
        "id": trade_id,
        "status": "CONFIRMED",
        "market": "condition-m5",
        "trader_side": "TAKER",
        "side": "BUY",
        "asset_id": YES_TOKEN,
        "taker_order_id": order_id,
        "size": "11.6",
        "price": "0.09",
        "transaction_hash": "0xterminal-taker",
        "maker_orders": [
            {
                "asset_id": no_token,
                "side": "BUY",
                "matched_amount": "8",
                "price": "0.91",
            },
            {
                "asset_id": no_token,
                "side": "BUY",
                "matched_amount": "3.6",
                "price": "0.90",
            },
        ],
    }
    append(
        conn,
        trade_id=trade_id,
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size="11.6",
        fill_price="0.09",
        source="REST",
        observed_at=NOW,
        fee_paid_micro=None,
        tx_hash="0xterminal-taker",
        block_number=123456,
        raw_payload_hash=hashlib.sha256(
            json.dumps(raw, sort_keys=True).encode()
        ).hexdigest(),
        raw_payload_json=raw,
    )

    live_tick = reconcile_recorded_maker_fill_economics(
        conn, observed_at=NOW + timedelta(seconds=1), live_tick_scope=True
    )
    assert live_tick["scanned"] == 0
    assert live_tick["corrected"] == 0

    full_sweep = reconcile_recorded_maker_fill_economics(
        conn, observed_at=NOW + timedelta(seconds=2)
    )
    assert full_sweep["scanned"] == 1
    assert full_sweep["corrected"] == 1
    exact_price = Decimal("1.08") / Decimal("11.6")
    facts = conn.execute(
        """
        SELECT trade_fact_id, filled_size, fill_price, fee_paid_micro,
               tx_hash, block_number, raw_payload_json
          FROM venue_trade_facts
         WHERE trade_id = ?
         ORDER BY local_sequence
        """,
        (trade_id,),
    ).fetchall()
    assert len(facts) == 2
    assert facts[0]["filled_size"] == "11.6"
    assert Decimal(facts[0]["fill_price"]) == Decimal("0.09")
    assert facts[0]["fee_paid_micro"] is None
    assert facts[0]["tx_hash"] == "0xterminal-taker"
    assert facts[0]["block_number"] == 123456
    assert Decimal(facts[1]["filled_size"]) == Decimal("11.6")
    assert Decimal(facts[1]["fill_price"]) == exact_price
    assert facts[1]["fee_paid_micro"] is None
    assert facts[1]["tx_hash"] == "0xterminal-taker"
    assert facts[1]["block_number"] == 123456
    repair = json.loads(facts[1]["raw_payload_json"])["zeus_repair"]
    assert repair["source_trade_fact_id"] == facts[0]["trade_fact_id"]
    assert repair["reason"] == "taker_maker_legs_selected_token_quote_cost"

    assert dict(
        conn.execute(
            "SELECT phase, shares, cost_basis_usd, realized_pnl_usd FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()
    ) == before_position
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT event_id, event_type, phase_before, phase_after, order_id, command_id FROM position_events WHERE position_id = ? ORDER BY sequence_no",
            (position_id,),
        ).fetchall()
    ] == before_events
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT intent_id, fill_price, shares, terminal_exec_status, command_id FROM execution_fact WHERE intent_id LIKE ? ORDER BY intent_id",
            (f"{position_id}%",),
        ).fetchall()
    ] == before_execution
    assert [
        tuple(row)
        for row in conn.execute(
            """
            SELECT fact_id, venue_order_id, command_id, state, remaining_size,
                   matched_size, source, observed_at, venue_timestamp,
                   local_sequence, raw_payload_hash, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = ? AND venue_order_id = ?
             ORDER BY local_sequence, fact_id
            """,
            (command_id, order_id),
        ).fetchall()
    ] == before_order_facts

    repeat = reconcile_recorded_maker_fill_economics(
        conn, observed_at=NOW + timedelta(seconds=3)
    )
    assert repeat["corrected"] == 0


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


def test_taker_fak_mixed_maker_legs_preserve_exact_cash_and_reproject(conn):
    from src.execution.command_recovery import _confirmed_entry_trade_fact_summary
    from src.execution.exchange_reconcile import (
        _ensure_entry_fill_position_event,
        reconcile_recorded_maker_fill_economics,
    )
    from src.state.venue_command_repo import append_trade_fact as append

    command_id = "cmd-taker-fak-exact-cost"
    order_id = "ord-taker-fak-exact-cost"
    position_id = "pos-taker-fak-exact-cost"
    yes_token = "taker-fak-yes"
    no_token = "taker-fak-no"
    filled = "154.634357"
    exact_cost = (
        Decimal("24.74") * Decimal("0.38")
        + Decimal("129.894357")
        * (Decimal("1") - Decimal("0.6100000017706697"))
    )
    exact_price = exact_cost / Decimal(filled)
    snapshot_id = _ensure_snapshot(
        conn,
        token_id=yes_token,
        no_token_id=no_token,
        selected_outcome_token_id=no_token,
        outcome_label="NO",
        snapshot_id="snap-taker-fak-exact-cost",
    )
    envelope_id = _ensure_envelope(
        conn,
        token_id=no_token,
        yes_token_id=yes_token,
        no_token_id=no_token,
        side="BUY",
        price=0.39,
        size=154.0,
        order_type="FAK",
    )
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at, q_version
        ) VALUES (?, ?, ?, ?, ?, ?, 'ENTRY', ?, ?, 'BUY', ?, ?, ?, 'FILLED', ?, ?, ?)
        """,
        (
            command_id,
            snapshot_id,
            envelope_id,
            position_id,
            "dec-taker-fak-exact-cost",
            "idem-taker-fak-exact-cost",
            "condition-m5",
            no_token,
            154.0,
            0.39,
            order_id,
            NOW.isoformat(),
            NOW.isoformat(),
            "q-taker-fak-exact-cost",
        ),
    )
    seed_position_baseline(conn, position_id=position_id, order_id=order_id)
    conn.execute(
        """
        UPDATE position_current
           SET direction = 'buy_no', token_id = ?, no_token_id = ?
         WHERE position_id = ?
        """,
        (yes_token, no_token, position_id),
    )
    append(
        conn,
        trade_id="trade-taker-fak-exact-cost",
        venue_order_id=order_id,
        command_id=command_id,
        state="MATCHED",
        filled_size=filled,
        fill_price=str(exact_price),
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(b"exact-fak-point-order").hexdigest(),
        raw_payload_json={
            "reason": "authenticated_fak_point_order_exact_economics",
            "filled_size": filled,
            "fill_price": str(exact_price),
        },
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?", (command_id,)
        ).fetchone()
    )
    _ensure_entry_fill_position_event(
        conn,
        command=command,
        venue_order_id=order_id,
        filled_size=filled,
        fill_price=str(exact_price),
        observed_at=NOW,
    )

    raw = {
        "id": "trade-taker-fak-exact-cost",
        "status": "CONFIRMED",
        "trader_side": "TAKER",
        "side": "BUY",
        "asset_id": no_token,
        "taker_order_id": order_id,
        "size": filled,
        "price": "0.39",
        "maker_orders": [
            {
                "asset_id": no_token,
                "matched_amount": "24.74",
                "price": "0.38",
                "side": "SELL",
            },
            {
                "asset_id": yes_token,
                "matched_amount": "129.894357",
                "price": "0.6100000017706697",
                "side": "BUY",
            },
        ],
    }
    append(
        conn,
        trade_id="trade-taker-fak-exact-cost",
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size=filled,
        fill_price="0.39",
        source="REST",
        observed_at=NOW + timedelta(seconds=1),
        raw_payload_hash=hashlib.sha256(
            json.dumps(raw, sort_keys=True).encode()
        ).hexdigest(),
        raw_payload_json=raw,
    )

    confirmed = _confirmed_entry_trade_fact_summary(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
    )
    assert Decimal(confirmed["filled_size"]) == Decimal(filled)
    assert Decimal(confirmed["fill_price"]) == exact_price

    summary = reconcile_recorded_maker_fill_economics(
        conn,
        observed_at=NOW + timedelta(seconds=2),
        live_tick_scope=True,
    )

    assert summary["corrected"] == 1
    assert summary["projected"] == 1
    projection = conn.execute(
        """
        SELECT shares, entry_price, cost_basis_usd
          FROM position_current
         WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()
    assert Decimal(str(projection["shares"])) == Decimal(filled)
    assert abs(Decimal(str(projection["entry_price"])) - exact_price) < Decimal(
        "0.000000000000001"
    )
    assert abs(Decimal(str(projection["cost_basis_usd"])) - exact_cost) < Decimal(
        "0.00000000000001"
    )
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'ENTRY_ORDER_FILLED'
           AND order_id = ?
        """,
        (position_id, order_id),
    ).fetchone()[0] == 1
    assert reconcile_recorded_maker_fill_economics(
        conn,
        observed_at=NOW + timedelta(seconds=3),
        live_tick_scope=True,
    ) == {
        "scanned": 0,
        "corrected": 0,
        "projected": 0,
        "stayed": 0,
        "errors": 0,
    }


@pytest.mark.parametrize(
    "order_fact_source",
    ["REST", "WS_USER", "WS_MARKET", "DATA_API", "CHAIN"],
)
def test_entry_trade_fill_does_not_claim_chain_position_authority(
    conn,
    order_fact_source,
):
    from src.execution.exchange_reconcile import _ensure_entry_fill_position_event
    from src.state.db import query_portfolio_loader_view

    seed_command(conn, size=10, price=0.50)
    seed_position_baseline(conn)
    append_trade_fact(conn, size="10", fill_price="0.50")
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = 'cmd-m5'"
        ).fetchone()
    )

    _ensure_entry_fill_position_event(
        conn,
        command=command,
        venue_order_id="ord-m5",
        filled_size="10",
        fill_price="0.50",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
        order_fact_source=order_fact_source,
    )

    projection = conn.execute(
        """
        SELECT chain_state, chain_shares, chain_avg_price, chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-m5'
        """
    ).fetchone()
    assert projection["chain_state"] == "unknown"
    assert projection["chain_shares"] is None
    assert projection["chain_avg_price"] is None
    assert projection["chain_cost_basis_usd"] is None

    # The fixture predates the durable fill-authority column.  Supply the
    # authority already present on the live recovery path so this assertion
    # isolates whether missing chain economics can poison global sizing.
    conn.execute(
        "UPDATE position_current SET fill_authority = 'venue_confirmed_full' "
        "WHERE position_id = 'pos-m5'"
    )
    runtime = query_portfolio_loader_view(conn, runtime_exposure_only=True)
    assert runtime["status"] == "ok"
    assert len(runtime["positions"]) == 1
    assert runtime["positions"][0]["chain_state"] == "unknown"
    assert runtime["positions"][0]["entry_economics_source"] == "execution_fact"


def test_entry_fill_position_event_backfills_law_identity_when_unstamped(conn):
    """ultimate_alpha 2026-07-25: _ensure_entry_fill_position_event's SimpleNamespace
    inherits decision_law_id/position_origin from the existing row via `**current`.
    A row projected before the law-identity stamp existed (seed_position_baseline
    builds one with neither attribute set, matching every pre-fix recovery/rescue
    projection) must backfill to the single current law/origin here rather than
    carry NULL forward -- this is the in-memory-hop half of the fix; the DB-side
    half is projection.py's write-once COALESCE.
    """
    from src.execution.exchange_reconcile import _ensure_entry_fill_position_event

    seed_command(conn, size=10, price=0.50)
    seed_position_baseline(conn)
    append_trade_fact(conn, size="10", fill_price="0.50")
    baseline = conn.execute(
        "SELECT decision_law_id, position_origin FROM position_current WHERE position_id = 'pos-m5'"
    ).fetchone()
    assert baseline["decision_law_id"] is None
    assert baseline["position_origin"] is None

    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = 'cmd-m5'"
        ).fetchone()
    )
    _ensure_entry_fill_position_event(
        conn,
        command=command,
        venue_order_id="ord-m5",
        filled_size="10",
        fill_price="0.50",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
        order_fact_source="REST",
    )

    row = conn.execute(
        "SELECT decision_law_id, position_origin FROM position_current WHERE position_id = 'pos-m5'"
    ).fetchone()
    assert row["decision_law_id"] == "predicted_bin_ev_v1"
    assert row["position_origin"] == "zeus_decision"


def test_entry_fill_only_recovery_binds_command_id_to_filled_event(conn):
    from src.execution.exchange_reconcile import _ensure_entry_fill_position_event

    # Recovery consumes an already-persisted legacy command; seed it through a
    # legal SELL FAK then make its historical ENTRY shape explicit.
    seed_command(conn, side="SELL", size=10, price=0.50, order_type="FAK", q_version="q-test")
    conn.execute(
        "UPDATE venue_commands SET intent_kind = 'ENTRY', side = 'BUY' WHERE command_id = 'cmd-m5'"
    )
    seed_position_baseline(conn)
    append_trade_fact(conn, size="10", fill_price="0.50")
    assert conn.execute(
        "SELECT 1 FROM position_events WHERE position_id = 'pos-m5' AND event_type = 'ENTRY_ORDER_FILLED'"
    ).fetchone() is None
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = 'cmd-m5'"
        ).fetchone()
    )

    _ensure_entry_fill_position_event(
        conn,
        command=command,
        venue_order_id="ord-m5",
        filled_size="10",
        fill_price="0.50",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
    )

    assert conn.execute(
        """
        SELECT command_id FROM position_events
         WHERE position_id = 'pos-m5' AND event_type = 'ENTRY_ORDER_FILLED'
        """
    ).fetchone()["command_id"] == "cmd-m5"


def test_entry_reobservation_repairs_edli_alias_double_projection(conn):
    from src.execution.exchange_reconcile import _ensure_entry_fill_position_event
    from src.state.venue_command_repo import append_trade_fact as append

    seed_command(conn, size=31.6, price=0.6)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    source_fact_id = append(
        conn,
        trade_id="tokyo-venue-trade",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="31.6",
        fill_price="0.6",
        source="WS_USER",
        observed_at=NOW,
        venue_timestamp=NOW,
        raw_payload_hash="3" * 64,
        raw_payload_json={"status": "CONFIRMED"},
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = 'cmd-m5'"
        ).fetchone()
    )
    _ensure_entry_fill_position_event(
        conn,
        command=command,
        venue_order_id="ord-m5",
        filled_size="31.6",
        fill_price="0.6",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
        order_fact_source="WS_USER",
    )
    append(
        conn,
        trade_id="edli:tokyo-venue-trade",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="31.6",
        fill_price="0.6",
        source="WS_USER",
        observed_at=NOW + timedelta(minutes=1),
        raw_payload_hash="4" * 64,
        raw_payload_json={
            "source_module": "src.events.edli_position_bridge",
            "raw_fill_payload": {"source_trade_fact_id": source_fact_id},
        },
    )
    append(
        conn,
        trade_id="tokyo-venue-trade",
        venue_order_id="ord-m5",
        command_id="cmd-m5",
        state="CONFIRMED",
        filled_size="31.6",
        fill_price="0.6",
        source="REST",
        observed_at=NOW + timedelta(minutes=1, seconds=1),
        raw_payload_hash="6" * 64,
        raw_payload_json={"status": "CONFIRMED", "source": "later_rest"},
    )
    conn.execute(
        """
        UPDATE position_current
           SET shares = 63.2,
               cost_basis_usd = 37.92,
               entry_price = 0.6,
               chain_state = 'synced',
               chain_shares = 31.6,
               chain_cost_basis_usd = 18.96,
               chain_avg_price = 0.6
         WHERE position_id = 'pos-m5'
        """
    )

    _ensure_entry_fill_position_event(
        conn,
        command=command,
        venue_order_id="ord-m5",
        filled_size="31.6",
        fill_price="0.6",
        observed_at=NOW + timedelta(minutes=2),
        command_event="FILL_CONFIRMED",
        order_fact_source="REST",
    )

    projection = conn.execute(
        """
        SELECT shares, cost_basis_usd, entry_price, chain_shares
          FROM position_current
         WHERE position_id = 'pos-m5'
        """
    ).fetchone()
    assert Decimal(str(projection["shares"])) == Decimal("31.6")
    assert Decimal(str(projection["cost_basis_usd"])) == Decimal("18.96")
    assert Decimal(str(projection["entry_price"])) == Decimal("0.6")
    assert Decimal(str(projection["chain_shares"])) == Decimal("31.6")
    execution = conn.execute(
        """
        SELECT shares, fill_price
          FROM execution_fact
         WHERE intent_id = 'pos-m5:entry'
        """
    ).fetchone()
    assert Decimal(str(execution["shares"])) == Decimal("31.6")
    assert Decimal(str(execution["fill_price"])) == Decimal("0.6")


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
        ("ECONOMICALLY_CLOSED_OPTIMISTIC", "7.5"),
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
        exit_close_position=True,
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
         WHERE intent_id = 'pos-exit-confirmed:exit:cmd-exit-confirmed'
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


@pytest.mark.parametrize("filled_size", ["10", "11"])
def test_confirmed_exit_fill_cannot_close_larger_current_holding(conn, filled_size):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = f"exit-holding-mismatch-token-{filled_size}"
    position_id = f"pos-exit-holding-mismatch-{filled_size}"
    command_id = f"cmd-exit-holding-mismatch-{filled_size}"
    order_id = f"ord-exit-holding-mismatch-{filled_size}"
    seed_position_baseline(
        conn,
        position_id=position_id,
        order_id=f"ord-entry-holding-mismatch-{filled_size}",
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'active',
               token_id = ?,
               order_status = 'filled',
               shares = 20,
               chain_shares = 20,
               chain_avg_price = 0.60,
               chain_cost_basis_usd = 12,
               cost_basis_usd = 12,
               entry_price = 0.60,
               updated_at = ?
         WHERE position_id = ?
        """,
        (token, NOW.isoformat(), position_id),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=token,
        side="SELL",
        size=10,
        price=0.50,
        state="ACKED",
        exit_close_position=True,
    )

    run_reconcile_sweep(
        FakeM5Adapter(
            trades=[
                trade(
                    trade_id=f"trade-exit-holding-mismatch-{filled_size}",
                    order_id=order_id,
                    size=filled_size,
                    price="0.50",
                    status="CONFIRMED",
                )
            ],
            positions=[position(token_id=token, size="20")],
        ),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    assert conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()["phase"] != "economically_closed"
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_FILLED'
           AND order_id = ?
        """,
        (position_id, order_id),
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("city", "direction"),
    [("Paris", "buy_yes"), ("Amsterdam", "buy_no")],
)
def test_chain_zero_authenticated_full_exit_closes_stale_residual_once(
    conn, city, direction
):
    """Chain-zero precedes local residual cleanup; canonical child fills close once."""
    from src.execution.exchange_reconcile import _ensure_exit_fill_position_event

    position_id = f"pos-{city.lower()}-chain-zero"
    command_id = f"cmd-{city.lower()}-chain-zero"
    order_id = f"ord-{city.lower()}-chain-zero"
    yes_token = f"{city.lower()}-yes"
    no_token = f"{city.lower()}-no"
    held_token = yes_token if direction == "buy_yes" else no_token
    target = Decimal("60")
    seed_position_baseline(conn, position_id=position_id, order_id="ord-entry")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit', city = ?, direction = ?, token_id = ?, no_token_id = ?,
               order_id = ?, order_status = 'sell_pending_confirmation',
               shares = 20, cost_basis_usd = 12, entry_price = 0.60,
               chain_state = 'chain_confirmed_zero', chain_shares = 0,
               chain_avg_price = 0, chain_cost_basis_usd = 999, updated_at = ?
         WHERE position_id = ?
        """,
        (city, direction, yes_token, no_token, order_id, NOW.isoformat(), position_id),
    )
    sequence_no = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events "
        "WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, order_id,
            caused_by, source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'MONITOR_REFRESHED', ?, 'pending_exit',
                  'pending_exit', 'opening_inertia', 'ord-prior-partial',
                  'partial_exit_fill', 'src.execution.exit_lifecycle', ?, 'live')
        """,
        (
            f"{position_id}:partial-exit:prior",
            position_id,
            sequence_no,
            NOW.isoformat(),
            json.dumps(
                {
                    "economic_fill_identity": f"trade:{position_id}:prior",
                    "economic_fill_cumulative_shares": "5",
                    "economic_fill_cumulative_notional_usd": "3",
                    "filled_shares": "5",
                    "filled_notional_usd": "3",
                    "allocated_cost_basis_usd": "1.75",
                    "realized_pnl_delta_usd": "1.25",
                    "cumulative_realized_pnl_usd": "1.25",
                    "remaining_shares": "20",
                    "remaining_cost_basis_usd": "12",
                },
                sort_keys=True,
            ),
        ),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=held_token,
        side="SELL",
        size=float(target),
        price=0.25,
        state="FILLED",
        order_type="FAK",
        exit_close_position=True,
        snapshot_token_id=yes_token,
        snapshot_no_token_id=no_token,
        snapshot_selected_token_id=held_token,
        snapshot_outcome_label="YES" if direction == "buy_yes" else "NO",
        envelope_yes_token_id=yes_token,
        envelope_no_token_id=no_token,
    )
    append_trade_fact(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        token_id=held_token,
        trade_id=f"trade-{city.lower()}-a",
        size="20",
        fill_price="0.20",
        state="CONFIRMED",
    )
    # A weaker revision of the same trade must not be counted a second time.
    append_trade_fact(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        token_id=held_token,
        trade_id=f"trade-{city.lower()}-a",
        size="20",
        fill_price="0.20",
        state="MATCHED",
    )
    append_trade_fact(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        token_id=held_token,
        trade_id=f"trade-{city.lower()}-b",
        size="39",
        fill_price="0.30",
        state="CONFIRMED",
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?", (command_id,)
        ).fetchone()
    )

    assert not _ensure_exit_fill_position_event(
        conn,
        command=command,
        venue_order_id=order_id,
        filled_size=str(target),
        fill_price="0.30",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'EXIT_ORDER_FILLED'",
        (position_id,),
    ).fetchone()[0] == 0
    append_trade_fact(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        token_id=held_token,
        trade_id=f"trade-{city.lower()}-b",
        size="40",
        fill_price="0.30",
        state="CONFIRMED",
    )

    assert _ensure_exit_fill_position_event(
        conn,
        command=command,
        venue_order_id=order_id,
        filled_size=str(target),
        fill_price="0.30",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
    )
    projection = conn.execute(
        """
        SELECT phase, shares, chain_state, chain_shares, realized_pnl_usd
          FROM position_current WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()
    assert dict(projection) == {
        "phase": "economically_closed",
        "shares": 20.0,
        "chain_state": "chain_confirmed_zero",
        "chain_shares": 0.0,
        # 20 residual shares at $0.60 imply $36 immutable close cost; the
        # deliberately absurd chain cost basis is not exit-cost authority.
        "realized_pnl_usd": -18.75,
    }
    fact = conn.execute(
        "SELECT shares, fill_price FROM execution_fact WHERE intent_id = ?",
        (f"{position_id}:exit:{command_id}",),
    ).fetchone()
    assert dict(fact) == {
        "shares": 60.0,
        "fill_price": pytest.approx(float(Decimal("0.2666666666666666666666666667"))),
    }
    assert conn.execute(
        """
        SELECT COUNT(*) FROM position_events
         WHERE position_id = ? AND event_type = 'EXIT_ORDER_FILLED' AND order_id = ?
        """,
        (position_id, order_id),
    ).fetchone()[0] == 1

    # Replay must repair an already-closed projection that carries the old
    # terminal-leg-only value; it must not append a second close event.
    conn.execute(
        "UPDATE position_current SET realized_pnl_usd = -20 WHERE position_id = ?",
        (position_id,),
    )
    assert _ensure_exit_fill_position_event(
        conn,
        command=command,
        venue_order_id=order_id,
        filled_size=str(target),
        fill_price="0.30",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
    )
    repaired = conn.execute(
        "SELECT realized_pnl_usd FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    assert repaired["realized_pnl_usd"] == pytest.approx(-18.75)
    assert conn.execute(
        """
        SELECT COUNT(*) FROM position_events
         WHERE position_id = ? AND event_type = 'EXIT_ORDER_FILLED' AND order_id = ?
        """,
        (position_id, order_id),
    ).fetchone()[0] == 1


def test_late_confirmed_exit_does_not_reopen_chain_zero_terminal_position(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_exit_fill_projections

    position_id = "pos-terminal-chain-zero"
    command_id = "cmd-terminal-chain-zero"
    order_id = "ord-terminal-chain-zero"
    token = "terminal-chain-zero-token"
    seed_position_baseline(conn, position_id=position_id, order_id="ord-entry")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided', token_id = ?, order_id = ?, shares = 10,
               chain_state = 'chain_confirmed_zero', chain_shares = 0,
               order_status = 'voided', updated_at = ?
         WHERE position_id = ?
        """,
        (token, order_id, NOW.isoformat(), position_id),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=token,
        side="SELL",
        size=10,
        price=0.20,
        state="FILLED",
        order_type="FAK",
        exit_close_position=True,
    )
    append_trade_fact(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        token_id=token,
        trade_id="trade-terminal-chain-zero",
        size="10",
        fill_price="0.20",
        state="CONFIRMED",
    )

    assert reconcile_recorded_exit_fill_projections(conn, observed_at=NOW) == {
        "scanned": 0,
        "projected": 0,
        "stayed": 0,
        "errors": 0,
    }
    assert dict(
        conn.execute(
            "SELECT phase, chain_state, chain_shares, order_status FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()
    ) == {
        "phase": "voided",
        "chain_state": "chain_confirmed_zero",
        "chain_shares": 0.0,
        "order_status": "voided",
    }
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id = ? AND event_type = 'EXIT_ORDER_FILLED'",
        (position_id,),
    ).fetchone()[0] == 0


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
        exit_close_position=True,
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
        exit_close_position=True,
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
         WHERE intent_id = 'pos-exit-recorded-confirmed:exit:cmd-recorded-exit-confirmed'
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

    from src.execution.exchange_reconcile import reconcile_recorded_exit_fill_projections

    changes_before_repeat = conn.total_changes
    repeat = reconcile_recorded_exit_fill_projections(conn, observed_at=NOW)

    assert repeat == {"scanned": 1, "projected": 0, "stayed": 1, "errors": 0}
    assert conn.total_changes == changes_before_repeat


def test_confirmed_taker_sell_uses_exact_complementary_maker_leg_proceeds(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.venue_command_repo import append_trade_fact as append

    yes_token = "taker-sell-yes"
    no_token = "taker-sell-no"
    position_id = "pos-taker-sell-exact"
    command_id = "cmd-taker-sell-exact"
    order_id = "ord-taker-sell-exact"
    seed_position_baseline(conn, position_id=position_id, order_id="ord-entry-taker-sell")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               no_token_id = ?,
               direction = 'buy_no',
               order_status = 'sell_pending_confirmation',
               shares = 12.99,
               chain_shares = 12.99,
               cost_basis_usd = 5.3259,
               chain_cost_basis_usd = 5.3259,
               entry_price = 0.41,
               updated_at = ?
         WHERE position_id = ?
        """,
        (yes_token, no_token, NOW.isoformat(), position_id),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=no_token,
        side="SELL",
        size=12.99,
        price=0.69,
        state="FILLED",
        snapshot_token_id=yes_token,
        snapshot_no_token_id=no_token,
        snapshot_selected_token_id=no_token,
        snapshot_outcome_label="NO",
        envelope_yes_token_id=yes_token,
        envelope_no_token_id=no_token,
        order_type="FAK",
        post_only=False,
        exit_close_position=True,
    )
    raw = {
        "id": "trade-taker-sell-exact",
        "taker_order_id": order_id,
        "asset_id": no_token,
        "side": "SELL",
        "size": "12.99",
        "price": "0.92",
        "status": "CONFIRMED",
        "trader_side": "TAKER",
        "maker_orders": [
            {
                "order_id": "maker-yes-1",
                "matched_amount": "7.27",
                "price": "0.06",
                "asset_id": yes_token,
                "side": "SELL",
            },
            {
                "order_id": "maker-yes-2",
                "matched_amount": "5",
                "price": "0.07",
                "asset_id": yes_token,
                "side": "SELL",
            },
            {
                "order_id": "maker-no",
                "matched_amount": "0.72",
                "price": "0.73",
                "asset_id": no_token,
                "side": "BUY",
            },
        ],
    }
    append(
        conn,
        trade_id=raw["id"],
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size="12.99",
        fill_price="0.92",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
        raw_payload_json=raw,
    )

    summary = reconcile_recorded_maker_fill_economics(
        conn, observed_at=NOW + timedelta(seconds=1)
    )

    exact_price = Decimal("12.0094") / Decimal("12.99")
    assert summary["corrected"] == 1
    assert summary["exit_projected"] == 1
    correction = conn.execute(
        """
        SELECT fill_price, json_extract(raw_payload_json, '$.zeus_repair.reason') AS reason
          FROM venue_trade_facts
         WHERE command_id = ?
         ORDER BY trade_fact_id DESC
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    assert Decimal(correction["fill_price"]) == exact_price
    assert correction["reason"] == "taker_maker_legs_selected_token_quote_proceeds"
    current = conn.execute(
        """
        SELECT phase, shares, chain_shares, realized_pnl_usd
          FROM position_current
         WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()
    assert current["phase"] == "economically_closed"
    assert current["shares"] == 12.99
    assert current["chain_shares"] == 0.0
    assert float(current["realized_pnl_usd"]) == pytest.approx(6.68)


def test_confirmed_taker_sell_corrects_already_booked_partial_exit_notional(conn):
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics
    from src.state.fill_dedup import partial_exit_realized_pnl_fold
    from src.state.venue_command_repo import append_trade_fact as append

    yes_token = "partial-correction-yes"
    no_token = "partial-correction-no"
    position_id = "pos-partial-correction"
    command_id = "cmd-partial-correction"
    order_id = "ord-partial-correction"
    trade_id = "trade-partial-correction"
    economic_identity = (
        f"economic-fill:v2:{command_id}:{order_id}:{trade_id}"
    )
    seed_position_baseline(conn, position_id=position_id, order_id="ord-entry-partial-correction")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               no_token_id = ?,
               direction = 'buy_no',
               order_status = 'backoff_exhausted',
               shares = 0.000168,
               chain_shares = 0.000168,
               cost_basis_usd = 0.00006888,
               chain_cost_basis_usd = 0.00006888,
               entry_price = 0.41,
               realized_pnl_usd = 6.62489988,
               updated_at = ?
         WHERE position_id = ?
        """,
        (yes_token, no_token, NOW.isoformat(), position_id),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=no_token,
        side="SELL",
        size=12.99,
        price=0.69,
        state="FILLED",
        snapshot_token_id=yes_token,
        snapshot_no_token_id=no_token,
        snapshot_selected_token_id=no_token,
        snapshot_outcome_label="NO",
        envelope_yes_token_id=yes_token,
        envelope_no_token_id=no_token,
        order_type="FAK",
        post_only=False,
        exit_close_position=False,
    )
    sequence_no = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, order_id,
            command_id, caused_by, source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'MONITOR_REFRESHED', ?, 'pending_exit',
                  'pending_exit', 'opening_inertia', ?, ?, 'partial_exit_fill',
                  'tests.test_exchange_reconcile', ?, 'live')
        """,
        (
            f"{position_id}:partial-exit:{sequence_no}",
            position_id,
            sequence_no,
            NOW.isoformat(),
            order_id,
            command_id,
            json.dumps(
                {
                    "semantic_event": "CAPITAL_REDUCTION_FILLED",
                    "economic_fill_identity": economic_identity,
                    "economic_fill_cumulative_shares": "12.99",
                    "economic_fill_cumulative_notional_usd": "11.9508",
                    "filled_shares": "12.99",
                    "filled_notional_usd": "11.9508",
                    "allocated_cost_basis_usd": "5.32590012",
                    "realized_pnl_delta_usd": "6.62489988",
                    "cumulative_realized_pnl_usd": "6.62489988",
                    "fill_price": "0.92",
                },
                sort_keys=True,
            ),
        ),
    )
    raw = {
        "id": trade_id,
        "taker_order_id": order_id,
        "asset_id": no_token,
        "side": "SELL",
        "size": "12.99",
        "price": "0.92",
        "status": "CONFIRMED",
        "trader_side": "TAKER",
        "maker_orders": [
            {"order_id": "m1", "matched_amount": "7.27", "price": "0.06", "asset_id": yes_token, "side": "SELL"},
            {"order_id": "m2", "matched_amount": "5", "price": "0.07", "asset_id": yes_token, "side": "SELL"},
            {"order_id": "m3", "matched_amount": "0.72", "price": "0.73", "asset_id": no_token, "side": "BUY"},
        ],
    }
    append(
        conn,
        trade_id=trade_id,
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size="12.99",
        fill_price="0.92",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
        raw_payload_json=raw,
    )

    summary = reconcile_recorded_maker_fill_economics(
        conn, observed_at=NOW + timedelta(seconds=1)
    )

    assert summary["corrected"] == 1
    assert summary["exit_partial_economics_corrected"] == 1
    correction = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ? AND caused_by = 'partial_exit_economics_repair'
         ORDER BY sequence_no DESC
         LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    payload = json.loads(correction["payload_json"])
    assert payload["economic_correction_only"] is True
    assert payload["filled_shares"] == "0"
    assert payload["filled_notional_usd"] == "0.0586"
    assert payload["realized_pnl_delta_usd"] == "0.0586"
    current = conn.execute(
        "SELECT phase, shares, chain_shares, realized_pnl_usd FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    assert current["phase"] == "pending_exit"
    assert current["shares"] == pytest.approx(0.000168)
    assert current["chain_shares"] == pytest.approx(0.000168)
    assert Decimal(str(current["realized_pnl_usd"])) == Decimal("6.68349988")
    assert partial_exit_realized_pnl_fold(conn, position_id) == Decimal("6.68349988")

    lower_raw = {
        **raw,
        "maker_orders": [
            {"order_id": "m1", "matched_amount": "7.27", "price": "0.08", "asset_id": yes_token, "side": "SELL"},
            {"order_id": "m2", "matched_amount": "5", "price": "0.09", "asset_id": yes_token, "side": "SELL"},
            {"order_id": "m3", "matched_amount": "0.72", "price": "0.70", "asset_id": no_token, "side": "BUY"},
        ],
    }
    append(
        conn,
        trade_id=trade_id,
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size="12.99",
        fill_price="0.90",
        source="REST",
        observed_at=NOW + timedelta(seconds=2),
        raw_payload_hash=hashlib.sha256(
            json.dumps(lower_raw, sort_keys=True).encode()
        ).hexdigest(),
        raw_payload_json=lower_raw,
    )
    lower_summary = reconcile_recorded_maker_fill_economics(
        conn, observed_at=NOW + timedelta(seconds=3)
    )

    assert lower_summary["corrected"] == 1
    assert lower_summary["exit_partial_economics_corrected"] == 1
    lower_payload = json.loads(
        conn.execute(
            """
            SELECT payload_json
              FROM position_events
             WHERE position_id = ? AND caused_by = 'partial_exit_economics_repair'
             ORDER BY sequence_no DESC LIMIT 1
            """,
            (position_id,),
        ).fetchone()["payload_json"]
    )
    assert lower_payload["filled_notional_usd"] == "-0.267"
    assert lower_payload["realized_pnl_delta_usd"] == "-0.267"
    assert partial_exit_realized_pnl_fold(conn, position_id) == Decimal("6.41649988")


def test_recorded_confirmed_exit_trade_economically_closes_disputed_chain_state_projection(conn):
    # BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md): this
    # test used to seed phase='quarantined' via a mixed-epoch downgrade
    # fixture to prove exit-projection zeroes chain_shares/chain_avg_price/
    # chain_cost_basis_usd even when the position carries an unresolved
    # chain-state dispute. That phase literal is retired and now rejected by
    # the position_current CHECK constraint; the real dispute-with-exposure
    # shape is a normal open-phase position (phase='active') carrying the
    # still-current chain_state='size_mismatch_unresolved', so the test is
    # rewritten to that current-schema equivalent rather than deleted.
    from src.execution.exchange_reconcile import reconcile_recorded_maker_fill_economics

    token = "exit-disputed-confirmed-token"
    seed_position_baseline(conn, position_id="pos-exit-disputed-confirmed", order_id="ord-entry-disputed")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'active',
               chain_state = 'size_mismatch_unresolved',
               token_id = ?,
               order_id = 'ord-entry-disputed',
               order_status = 'filled',
               shares = 11.09,
               chain_shares = 11.09,
               cost_basis_usd = 6.10,
               chain_cost_basis_usd = 6.10,
               entry_price = 0.55,
               updated_at = ?
         WHERE position_id = 'pos-exit-disputed-confirmed'
        """,
        (token, NOW.isoformat()),
    )
    seed_command(
        conn,
        command_id="cmd-disputed-exit-confirmed",
        venue_order_id="ord-disputed-exit-confirmed",
        position_id="pos-exit-disputed-confirmed",
        token_id=token,
        side="SELL",
        size=11.09,
        price=0.53,
        state="FILLED",
        exit_close_position=True,
    )
    append_trade_fact(
        conn,
        command_id="cmd-disputed-exit-confirmed",
        venue_order_id="ord-disputed-exit-confirmed",
        token_id=token,
        trade_id="trade-disputed-exit-confirmed",
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
         WHERE position_id = 'pos-exit-disputed-confirmed'
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
         WHERE position_id = 'pos-exit-disputed-confirmed'
           AND event_type = 'EXIT_ORDER_FILLED'
        """
    ).fetchone()
    assert dict(event) == {
        "event_type": "EXIT_ORDER_FILLED",
        "phase_before": "pending_exit",
        "phase_after": "economically_closed",
        "order_id": "ord-disputed-exit-confirmed",
        "command_id": "cmd-disputed-exit-confirmed",
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
        exit_close_position=True,
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
             WHERE intent_id = 'pos-exit-recorded-matched:exit:cmd-recorded-exit-matched'
               AND terminal_exec_status = 'filled'
            """
        ).fetchone()[0]
        == 0
    )


def test_recorded_matched_full_exit_closes_after_independent_chain_zero(conn):
    """A FILLED full-close plus Chain-zero drains before broad recovery work."""
    from src.execution.exchange_reconcile import reconcile_recorded_exit_fill_projections

    position_id = "pos-recorded-matched-chain-zero"
    command_id = "cmd-recorded-matched-chain-zero"
    order_id = "ord-recorded-matched-chain-zero"
    token = "recorded-matched-chain-zero-token"
    seed_position_baseline(conn, position_id=position_id, order_id="ord-entry")
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit', token_id = ?, order_id = ?,
               order_status = 'sell_pending_confirmation', shares = 5,
               cost_basis_usd = 0.6, entry_price = 0.12,
               chain_state = 'chain_confirmed_zero', chain_shares = 0,
               chain_avg_price = 0, chain_cost_basis_usd = 0, updated_at = ?
         WHERE position_id = ?
        """,
        (token, order_id, NOW.isoformat(), position_id),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=token,
        side="SELL",
        size=5,
        price=0.23,
        state="FILLED",
        order_type="FAK",
        exit_close_position=True,
    )
    append_trade_fact(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        token_id=token,
        trade_id="trade-recorded-matched-chain-zero",
        size="5",
        fill_price="0.23",
        state="MATCHED",
    )

    summary = reconcile_recorded_exit_fill_projections(
        conn,
        observed_at=NOW,
        command_ids=(command_id,),
    )

    assert summary == {"scanned": 1, "projected": 1, "stayed": 0, "errors": 0}
    assert dict(
        conn.execute(
            """
            SELECT phase, order_status, chain_shares, exit_price,
                   realized_pnl_usd
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
    ) == {
        "phase": "economically_closed",
        "order_status": "sell_filled",
        "chain_shares": 0.0,
        "exit_price": 0.23,
        "realized_pnl_usd": 0.55,
    }


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
        exit_close_position=True,
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
             WHERE intent_id = 'pos-exit-finality-wait:exit:cmd-exit-finality-wait'
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
         WHERE intent_id = 'pos-exit-finality-wait:exit:cmd-exit-finality-wait'
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


def test_filled_reduction_command_cannot_close_already_chain_reduced_position(conn):
    """Command finality cannot override an explicit partial-position intent."""
    from src.execution.exchange_reconcile import _ensure_exit_fill_position_event

    position_id = "pos-capital-reduction-terminal-guard"
    order_id = "ord-capital-reduction-terminal-guard"
    command_id = "cmd-capital-reduction-terminal-guard"
    token = "capital-reduction-terminal-guard-token"
    seed_position_baseline(
        conn,
        position_id=position_id,
        order_id="ord-entry-capital-reduction-terminal-guard",
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'pending_exit',
               token_id = ?,
               order_id = ?,
               order_status = 'sell_pending_confirmation',
               shares = 25.48,
               chain_shares = 25.48,
               cost_basis_usd = 17.836,
               entry_price = 0.70,
               updated_at = ?
         WHERE position_id = ?
        """,
        (token, order_id, NOW.isoformat(), position_id),
    )
    sequence_no = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, order_id,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'EXIT_INTENT', ?, 'day0_window', 'pending_exit',
                  'opening_inertia', NULL, 'src.execution.exit_lifecycle', ?, 'live')
        """,
        (
            f"{position_id}:exit_intent:{sequence_no}",
            position_id,
            sequence_no,
            (NOW - timedelta(microseconds=1)).isoformat(),
            json.dumps(
                {
                    "exit_intent_close_position": False,
                    "exit_intent_shares": 44.42,
                    "exit_intent_capital_certificate": {"held_shares": 69.90},
                },
                sort_keys=True,
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, order_id,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'EXIT_ORDER_POSTED', ?, 'pending_exit', 'pending_exit',
                  'opening_inertia', ?, 'src.execution.exit_lifecycle', '{}', 'live')
        """,
        (
            f"{position_id}:exit_posted:{sequence_no + 1}",
            position_id,
            sequence_no + 1,
            NOW.isoformat(),
            order_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, order_id,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'EXIT_INTENT', ?, 'pending_exit', 'pending_exit',
                  'opening_inertia', NULL, 'src.execution.exit_lifecycle', ?, 'live')
        """,
        (
            f"{position_id}:newer_full_exit_intent:{sequence_no + 2}",
            position_id,
            sequence_no + 2,
            (NOW + timedelta(seconds=1)).isoformat(),
            json.dumps(
                {
                    "exit_intent_close_position": True,
                    "exit_intent_shares": 44.42,
                },
                sort_keys=True,
            ),
        ),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=token,
        side="SELL",
        size=44.42,
        price=0.74,
        state="FILLED",
    )
    append_trade_fact(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        token_id=token,
        trade_id="trade-capital-reduction-terminal-guard",
        size="44.42",
        fill_price="0.75",
        state="CONFIRMED",
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    )

    _ensure_exit_fill_position_event(
        conn,
        command=command,
        venue_order_id=order_id,
        filled_size="44.42",
        fill_price="0.75",
        observed_at=NOW,
        command_event="FILL_CONFIRMED",
    )

    current = conn.execute(
        """
        SELECT phase, shares, chain_shares, cost_basis_usd, order_status
          FROM position_current
         WHERE position_id = ?
        """,
        (position_id,),
    ).fetchone()
    assert dict(current) == {
        "phase": "pending_exit",
        "shares": 25.48,
        "chain_shares": 25.48,
        "cost_basis_usd": 17.836,
        "order_status": "sell_pending_confirmation",
    }
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_FILLED'
        """,
        (position_id,),
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
                    taker_order_id="foreign-taker-exit-price",
                    trader_side="TAKER",
                    market="condition-m5",
                    asset_id=YES_TOKEN,
                    side="SELL",
                    maker_orders=[
                        {
                            "order_id": "ord-exit-price",
                            "asset_id": YES_TOKEN,
                            "side": "BUY",
                            "matched_amount": "6",
                            "price": "0.30",
                        }
                    ],
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


def test_confirmed_trade_wire_price_noise_is_same_economics(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=9.536229, price=0.70)
    first = trade(
        trade_id="trade-wire-price-noise",
        order_id="ord-m5",
        size="9.536229",
        price="0.6899999989513675",
        fill_price="0.6899999989513675",
        status="CONFIRMED",
    )
    assert run_reconcile_sweep(
        FakeM5Adapter(trades=[first], positions=[position(size="9.536229")]),
        conn,
        context="periodic",
        observed_at=NOW,
    ) == []

    second = trade(
        trade_id="trade-wire-price-noise",
        order_id="ord-m5",
        size="9.536229",
        price="0.69",
        fill_price="0.69",
        status="CONFIRMED",
    )
    result = run_reconcile_sweep(
        FakeM5Adapter(trades=[second], positions=[position(size="9.536229")]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    assert result == []
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_trade_facts "
        "WHERE trade_id = 'trade-wire-price-noise'"
    ).fetchone()[0] == 1


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


def test_point_order_split_weighted_leg_price_reproduces_local_fact_is_same_economics(conn):
    """Live shape (finding 6d822fe0-b06b-4d38-9970-a3491c1ab852): a taker fill
    split across two point-order legs on opposite outcome tokens reports only
    one leg's price ("0.39") in the trade's top-level price field, while the
    already-recorded local fact holds the correct size-weighted aggregate
    price across both legs (5.4 @ 0.39 direct-outcome + 4.135 @ (1-0.6)
    complement-outcome / 9.535 total = 0.3943366544310435...). This must NOT
    raise unrecorded_trade -- the local aggregate stays authoritative.
    """
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=9.535, price=0.39)

    first = trade(
        trade_id="trade-point-order-split",
        order_id="ord-m5",
        size="9.535",
        price="0.3943366544310435238594651285",
        fill_price="0.3943366544310435238594651285",
        status="CONFIRMED",
    )
    assert run_reconcile_sweep(
        FakeM5Adapter(trades=[first], positions=[position(size="9.535")]),
        conn,
        context="periodic",
        observed_at=NOW,
    ) == []

    second = trade(
        trade_id="trade-point-order-split",
        order_id="ord-m5",
        size="9.535",
        price="0.39",
        fill_price="0.39",
        status="CONFIRMED",
        asset_id=YES_TOKEN,
        side="BUY",
        trader_side="TAKER",
        market="condition-m5",
        taker_order_id="ord-m5",
        maker_orders=[
            {
                "asset_id": YES_TOKEN,
                "matched_amount": "5.4",
                "price": "0.39",
                "order_id": "maker-no-sell",
                "outcome": "No",
                "side": "SELL",
            },
            {
                "asset_id": f"{YES_TOKEN}-no",
                "matched_amount": "4.135",
                "price": "0.6",
                "order_id": "maker-yes-buy",
                "outcome": "Yes",
                "side": "BUY",
            },
        ],
    )
    result = run_reconcile_sweep(
        FakeM5Adapter(trades=[second], positions=[position(size="9.535")]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    assert result == []
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(fill_price) AS fill_price"
        " FROM venue_trade_facts WHERE trade_id = 'trade-point-order-split'"
    ).fetchone()
    assert row["n"] == 1
    assert row["fill_price"] == "0.3943366544310435238594651285"


def test_point_order_split_genuine_price_drift_still_becomes_finding(conn):
    """Same two-leg split shape, but the legs' weighted average does NOT
    reproduce the local fact's recorded price -- a real economic drift, not a
    single-leg artifact. Must still record unrecorded_trade."""
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=9.535, price=0.39)

    first = trade(
        trade_id="trade-point-order-genuine-drift",
        order_id="ord-m5",
        size="9.535",
        price="0.3943366544310435238594651285",
        fill_price="0.3943366544310435238594651285",
        status="CONFIRMED",
    )
    assert run_reconcile_sweep(
        FakeM5Adapter(trades=[first], positions=[position(size="9.535")]),
        conn,
        context="periodic",
        observed_at=NOW,
    ) == []

    second = trade(
        trade_id="trade-point-order-genuine-drift",
        order_id="ord-m5",
        size="9.535",
        price="0.39",
        fill_price="0.39",
        status="CONFIRMED",
        asset_id=YES_TOKEN,
        side="BUY",
        trader_side="TAKER",
        market="condition-m5",
        taker_order_id="ord-m5",
        maker_orders=[
            {
                "asset_id": YES_TOKEN,
                "matched_amount": "5.4",
                "price": "0.39",
                "order_id": "maker-no-sell",
                "outcome": "No",
                "side": "SELL",
            },
            {
                "asset_id": f"{YES_TOKEN}-no",
                "matched_amount": "4.135",
                "price": "0.8",
                "order_id": "maker-yes-buy",
                "outcome": "Yes",
                "side": "BUY",
            },
        ],
    )
    result = run_reconcile_sweep(
        FakeM5Adapter(trades=[second], positions=[position(size="9.535")]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade"]
    assert "exchange_trade_lifecycle_regression_or_economic_drift" in result[0].evidence_json
    assert conn.execute(
        "SELECT fill_price FROM venue_trade_facts WHERE trade_id = 'trade-point-order-genuine-drift'"
    ).fetchone()["fill_price"] == "0.3943366544310435238594651285"


def test_point_order_split_size_mismatch_still_becomes_finding(conn):
    """The incoming trade's filled_size does not match the local fact's
    filled_size -- fail closed even though the legs' weighted price would
    otherwise reproduce the local fact's price."""
    from src.execution.exchange_reconcile import run_reconcile_sweep

    seed_command(conn, size=9.535, price=0.39)

    first = trade(
        trade_id="trade-point-order-size-mismatch",
        order_id="ord-m5",
        size="9.535",
        price="0.3943366544310435238594651285",
        fill_price="0.3943366544310435238594651285",
        status="CONFIRMED",
    )
    assert run_reconcile_sweep(
        FakeM5Adapter(trades=[first], positions=[position(size="9.535")]),
        conn,
        context="periodic",
        observed_at=NOW,
    ) == []

    second = trade(
        trade_id="trade-point-order-size-mismatch",
        order_id="ord-m5",
        size="8",
        price="0.39",
        fill_price="0.39",
        status="CONFIRMED",
        asset_id=YES_TOKEN,
        side="BUY",
        trader_side="TAKER",
        market="condition-m5",
        taker_order_id="ord-m5",
        maker_orders=[
            {
                "asset_id": YES_TOKEN,
                "matched_amount": "5.4",
                "price": "0.39",
                "order_id": "maker-no-sell",
                "outcome": "No",
                "side": "SELL",
            },
            {
                "asset_id": f"{YES_TOKEN}-no",
                "matched_amount": "2.6",
                "price": "0.6",
                "order_id": "maker-yes-buy",
                "outcome": "Yes",
                "side": "BUY",
            },
        ],
    )
    result = run_reconcile_sweep(
        FakeM5Adapter(trades=[second], positions=[position(size="9.535")]),
        conn,
        context="periodic",
        observed_at=NOW + timedelta(seconds=1),
    )

    assert [finding.kind for finding in result] == ["unrecorded_trade"]
    assert "exchange_trade_lifecycle_regression_or_economic_drift" in result[0].evidence_json
    assert conn.execute(
        "SELECT fill_price FROM venue_trade_facts WHERE trade_id = 'trade-point-order-size-mismatch'"
    ).fetchone()["fill_price"] == "0.3943366544310435238594651285"


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
                    market="condition-m5",
                    asset_id=YES_TOKEN,
                    side="BUY",
                    maker_orders=[
                        {
                            "asset_id": YES_TOKEN,
                            "side": "SELL",
                            "matched_amount": "10",
                            "price": "0.51",
                        }
                    ],
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


@pytest.mark.parametrize(
    "suppression_reason",
    ("chain_only_quarantined", "operator_quarantine_clear", "settled_position"),
)
def test_position_drift_honors_token_suppression_registry(
    conn,
    suppression_reason,
):
    """Typed external reasons must not be re-flagged as blocking position drift.

    chain_reconciliation quarantines chain-only / operator-manual holdings into token_suppression
    ('chain_only_quarantined'); operator and settlement resolution use their own typed reasons.
    Re-flagging those tokens keeps the M5 submit latch closed on positions the system does not own.
    A genuinely unsuppressed drift must still be flagged.
    """
    from src.execution.exchange_reconcile import run_reconcile_sweep

    suppressed = f"external-suppressed-token-{suppression_reason}"
    real_drift = "genuine-unsuppressed-drift-token"
    conn.execute(
        """
        INSERT INTO token_suppression (token_id, condition_id, suppression_reason, source_module, created_at, updated_at)
        VALUES (?, '0xcond', ?, 'src.state.chain_reconciliation', ?, ?)
        """,
        (suppressed, suppression_reason, NOW.isoformat(), NOW.isoformat()),
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


def test_position_drift_reports_auto_resolved_match_token(conn):
    """An automatic local/chain match resolves review debt, not future drift."""
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token_id = "auto-resolved-match-future-drift"
    conn.execute(
        """
        INSERT INTO token_suppression (
            token_id, condition_id, suppression_reason, source_module, created_at, updated_at
        )
        VALUES (?, '0xcond', 'chain_only_auto_resolved_match',
                'src.state.chain_reconciliation', ?, ?)
        """,
        (token_id, NOW.isoformat(), NOW.isoformat()),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token_id, size="15")]),
        conn,
        context="periodic",
        observed_at=NOW,
    )

    drift_subjects = [finding.subject_id for finding in result if finding.kind == "position_drift"]
    assert token_id in drift_subjects


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


def test_position_drift_absorbs_chain_confirmed_non_executable_dust(conn):
    from src.execution.exchange_reconcile import record_finding, run_reconcile_sweep

    token = "chain-confirmed-non-executable-dust-token"
    _ensure_snapshot(
        conn,
        token_id=token,
        snapshot_id=f"snap-{token}",
        min_order_size=Decimal("5"),
    )
    seed_command(
        conn,
        command_id="cmd-chain-dust",
        venue_order_id="ord-chain-dust",
        position_id="pos-chain-dust",
        token_id=token,
        size=9.536229,
        price=0.69,
    )
    append_trade_fact(
        conn,
        command_id="cmd-chain-dust",
        venue_order_id="ord-chain-dust",
        token_id=token,
        trade_id="trade-chain-dust",
        size="9.536229",
        fill_price="0.69",
        state="CONFIRMED",
    )
    seed_command(
        conn,
        command_id="cmd-chain-dust-exit",
        venue_order_id="ord-chain-dust-exit",
        position_id="pos-chain-dust",
        token_id=token,
        side="SELL",
        size=9.52,
        price=0.95,
    )
    append_trade_fact(
        conn,
        command_id="cmd-chain-dust-exit",
        venue_order_id="ord-chain-dust-exit",
        token_id=token,
        trade_id="trade-chain-dust-exit",
        size="9.52",
        fill_price="0.98",
        state="CONFIRMED",
    )
    seed_position_baseline(
        conn,
        position_id="pos-chain-dust",
        order_id="ord-chain-dust",
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'day0_window',
               chain_state = 'synced',
               chain_shares = 0.016229,
               shares = 0.016229,
               token_id = ?,
               condition_id = 'condition-m5',
               updated_at = ?
         WHERE position_id = 'pos-chain-dust'
        """,
        (token, NOW.isoformat()),
    )
    stale = record_finding(
        conn,
        kind="position_drift",
        subject_id=token,
        context="ws_gap",
        evidence={"reason": "stale_chain_dust_probe"},
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
        "SELECT resolution, resolved_by FROM exchange_reconcile_findings "
        "WHERE finding_id = ?",
        (stale.finding_id,),
    ).fetchone()
    assert dict(resolved) == {
        "resolution": "position_drift_chain_confirmed_non_executable_dust",
        "resolved_by": "src.execution.exchange_reconcile",
    }


def test_fresh_reconcile_snapshot_captures_typed_point_order_absence():
    from src.execution.exchange_reconcile import fresh_reconcile_snapshot
    from src.venue.response_contracts import VenueOrderNotFound

    class Adapter:
        @staticmethod
        def get_open_orders():
            return []

        @staticmethod
        def get_order(order_id):
            raise VenueOrderNotFound(order_id)

        @staticmethod
        def get_trades():
            return []

        @staticmethod
        def get_positions():
            return []

    snapshot = fresh_reconcile_snapshot(
        Adapter(),
        observed_at=NOW,
        trade_order_ids={"missing-order"},
    )

    assert "point_orders" in snapshot.captured_surfaces
    assert snapshot.adapter.get_order("missing-order") is None
    assert snapshot.adapter.venue_reads_are_complete is True
    assert snapshot.adapter.point_order_reads["missing-order"] == {
        "query_complete": True,
        "authenticated_absent": True,
        "identity_match": False,
    }


def test_fresh_reconcile_snapshot_raw_none_is_not_absence_authority():
    from src.execution.exchange_reconcile import fresh_reconcile_snapshot

    class Adapter:
        @staticmethod
        def get_open_orders():
            return []

        @staticmethod
        def get_order(_order_id):
            return None

        @staticmethod
        def get_trades():
            return []

        @staticmethod
        def get_positions():
            return []

    snapshot = fresh_reconcile_snapshot(
        Adapter(),
        observed_at=NOW,
        trade_order_ids={"unknown-order"},
    )

    assert snapshot.adapter.venue_reads_are_complete is False
    assert snapshot.adapter.authenticated_point_reads_are_complete is False
    assert snapshot.adapter.point_order_reads["unknown-order"] == {
        "query_complete": False,
        "authenticated_absent": False,
        "identity_match": False,
    }


def test_fresh_reconcile_snapshot_untrusted_matching_payload_is_not_point_authority():
    from src.execution.exchange_reconcile import fresh_reconcile_snapshot

    class Adapter:
        @staticmethod
        def get_open_orders():
            return []

        @staticmethod
        def get_order(order_id):
            return order(order_id, status="CANCELED", size_matched="0")

        @staticmethod
        def get_trades():
            return []

        @staticmethod
        def get_positions():
            return []

    snapshot = fresh_reconcile_snapshot(
        Adapter(),
        observed_at=NOW,
        trade_order_ids={"shaped-but-untrusted"},
    )

    assert snapshot.adapter.venue_reads_are_complete is False
    assert snapshot.adapter.point_order_reads["shaped-but-untrusted"] == {
        "query_complete": False,
        "authenticated_absent": False,
        "identity_match": False,
    }


def test_fresh_reconcile_snapshot_keeps_unknown_point_order_failure_fail_closed():
    from src.execution.exchange_reconcile import fresh_reconcile_snapshot

    class Adapter:
        @staticmethod
        def get_open_orders():
            return []

        @staticmethod
        def get_order(order_id):
            raise RuntimeError(f"transport failed: {order_id}")

    with pytest.raises(RuntimeError, match="transport failed"):
        fresh_reconcile_snapshot(
            Adapter(),
            observed_at=NOW,
            trade_order_ids={"unknown-order"},
        )


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


def test_stuck_redeem_intent_row_does_not_mask_genuine_position_drift(conn):
    """A stale, permanently-non-progressing REDEEM_INTENT_CREATED settlement_commands
    row (the production shape: 139 rows stuck since redeem_submitter was deleted,
    2026-07-08 -- Zeus never submits on-chain redemption; Polymarket settles win/loss)
    must NOT mask a genuinely unexplained wallet mismatch.

    Before the settlement_commands-keyed branches were removed from
    _record_position_drift_findings, a pending settlement_commands row zeroed
    closed_position_size unconditionally (`closed_position_size = 0 if settlement_size
    > 0 else ...`), which starves _absorb_terminal_chain_closed_phantom's first gate
    (`if closed_position_size <= 0: return False`) forever for any token tied to a
    stuck intent row -- exactly the M5 submit-latch-freeze risk this change closes.

    This position has NO closed_position_current holding, NO chain-confirmed active
    holding, and NO token_suppression row -- it is a real, unexplained drift and must
    still surface as an open position_drift finding regardless of the stuck redeem row.
    """
    from src.execution.exchange_reconcile import run_reconcile_sweep

    token = "stuck-redeem-intent-genuine-drift-token"
    seed_command(
        conn,
        command_id="cmd-stuck-redeem-intent",
        venue_order_id="ord-stuck-redeem-intent",
        position_id="pos-stuck-redeem-intent",
        token_id=token,
        state="FILLED",
        size=3.0,
        price=0.42,
    )
    # No trade fact, no closed position_current row, no chain-confirmed holding, no
    # suppression -- the exchange position is entirely unexplained by any journal.
    conn.execute(
        """
        INSERT INTO settlement_commands (
            command_id, state, condition_id, market_id, payout_asset,
            pusd_amount_micro, token_amounts_json, requested_at, winning_index_set
        ) VALUES (?, 'REDEEM_INTENT_CREATED', 'condition-stuck', 'condition-stuck', 'pUSD',
                  3000000, ?, ?, ?)
        """,
        (
            "redeem-stuck-forever",
            json.dumps({token: 3.0}, separators=(",", ":")),
            NOW.isoformat(),
            json.dumps(["2"]),
        ),
    )

    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=token, size="3.0")]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=10),
    )

    position_findings = [finding for finding in result if finding.kind == "position_drift"]
    assert [finding.subject_id for finding in position_findings] == [token], (
        "a stuck REDEEM_INTENT_CREATED row must not suppress a genuinely unexplained "
        "position_drift finding"
    )


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
                    market="condition-m5",
                    asset_id=token,
                    side="BUY",
                    maker_orders=[
                        {
                            "asset_id": token,
                            "side": "SELL",
                            "matched_amount": "5",
                            "price": "0.50",
                        }
                    ],
                )
            ],
            positions=[position(token_id=token, size="5")],
        ),
        conn,
        observed_at=NOW,
    )

    # The two targeted debts clear, but the failed canonical entry projection
    # records blocking findings; the refresh must not report global green.
    assert result["status"] == "blocked"
    assert result["remaining"] == 0
    assert result["all_remaining"] == 2
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
                    market="condition-m5",
                    asset_id="unrecorded-only-token",
                    side="BUY",
                    maker_orders=[
                        {
                            "asset_id": "unrecorded-only-token",
                            "side": "SELL",
                            "matched_amount": "7",
                            "price": "0.42",
                        }
                    ],
                )
            ],
            positions=[],
        ),
        conn,
        observed_at=NOW,
    )

    # Linking this trade clears the requested finding, while the missing
    # canonical entry projection remains a distinct blocker alongside the
    # existing unrelated fixture debt.
    assert result["status"] == "blocked"
    assert result["remaining"] == 0
    assert result["all_remaining"] == 2
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


def test_unresolved_refresh_drains_review_required_local_orphan_atomically(conn):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings
    from src.state.venue_command_repo import append_event

    position_id = "pos-refresh-local-orphan"
    command_id = "cmd-refresh-local-orphan"
    order_id = "ord-refresh-local-orphan"
    seed_position_baseline(conn, position_id=position_id, order_id="prior-filled-order")
    conn.execute(
        "UPDATE position_current SET phase='day0_window', shares=9, chain_shares=9, "
        "cost_basis_usd=4.5, order_id='prior-filled-order', updated_at=? WHERE position_id=?",
        (NOW.isoformat(), position_id),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        state="ACKED",
        size=10,
        price=0.5,
    )
    conn.execute(
        "UPDATE venue_commands SET idempotency_key=? WHERE command_id=?",
        ("a" * 32, command_id),
    )
    append_resting_order_fact(conn, command_id=command_id, venue_order_id=order_id)
    append_event(
        conn,
        command_id=command_id,
        event_type="CANCEL_REQUESTED",
        occurred_at=(NOW + timedelta(seconds=1)).isoformat(),
        payload={"venue_order_id": order_id},
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="CANCEL_FAILED",
        occurred_at=(NOW + timedelta(seconds=2)).isoformat(),
        payload={
            "reason": "order can't be found - already canceled or matched",
            "cancel_outcome": {"orderID": order_id, "errorMessage": "already canceled or matched"},
        },
    )
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={
            "reason": "local_open_order_absent_from_exchange_open_orders",
            "trade_enumeration_available": True,
        },
        recorded_at=NOW + timedelta(seconds=3),
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5AdapterWithPointOrders(
            orders_by_id={
                order_id: order(
                    order_id,
                    status="CANCELED",
                    size_matched="0",
                    original_size="10",
                    side="BUY",
                    asset_id=YES_TOKEN,
                    price="0.5",
                )
            },
            open_orders=[],
            trades=[],
            positions=[position(token_id=YES_TOKEN, size="9")],
        ),
        conn,
        observed_at=NOW + timedelta(seconds=4),
    )

    command = conn.execute("SELECT state FROM venue_commands WHERE command_id=?", (command_id,)).fetchone()
    current = conn.execute(
        "SELECT phase, shares, chain_shares, cost_basis_usd, order_id FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()
    resolved = conn.execute(
        "SELECT resolution FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()
    terminal = conn.execute(
        "SELECT state, matched_size, remaining_size FROM venue_order_facts "
        "WHERE command_id=? ORDER BY local_sequence DESC LIMIT 1",
        (command_id,),
    ).fetchone()
    assert result["status"] == "resolved"
    assert command["state"] == "EXPIRED"
    assert current[:] == ("day0_window", 9.0, 9.0, 4.5, "prior-filled-order")
    assert resolved["resolution"] == "command_recovery_already_canceled_terminal_no_fill"
    assert terminal[:] == ("CANCEL_CONFIRMED", "0", "0")


def test_unresolved_refresh_resolves_local_orphan_after_terminal_fill(conn):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings

    command_id = "cmd-refresh-terminal-fill"
    order_id = "ord-refresh-terminal-fill"
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id="pos-refresh-terminal-fill",
        state="FILLED",
        size=11.85,
        price=0.20,
    )
    append_trade_fact(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        trade_id="trade-refresh-terminal-fill",
        size="11.85",
        fill_price="0.20",
    )
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={
            "reason": "local_open_order_absent_from_exchange_open_orders",
            "trade_enumeration_available": True,
        },
        recorded_at=NOW,
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[], trades=[], positions=[]),
        conn,
        observed_at=NOW + timedelta(seconds=1),
    )

    resolved = conn.execute(
        "SELECT resolved_at, resolution, resolved_by "
        "FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()
    assert result["status"] == "resolved"
    assert result["terminal_fill_local_orphan_summary"] == {"scanned": 1, "resolved": 1}
    assert resolved["resolved_at"] is not None
    assert resolved["resolution"] == "local_orphan_order_terminal_fill_confirmed"
    assert resolved["resolved_by"] == "src.execution.exchange_reconcile"


def test_unresolved_refresh_keeps_terminal_fill_without_full_trade_coverage(conn):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings
    from src.state.venue_command_repo import append_trade_fact as append_trade_revision

    command_id = "cmd-refresh-partial-trade"
    order_id = "ord-refresh-partial-trade"
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        state="FILLED",
        size=11.85,
        price=0.20,
    )
    append_trade_fact(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        trade_id="trade-refresh-partial-trade",
        size="5",
        fill_price="0.20",
    )
    append_trade_revision(
        conn,
        trade_id="trade-refresh-partial-trade",
        venue_order_id=order_id,
        command_id=command_id,
        state="CONFIRMED",
        filled_size="5",
        fill_price="0.20",
        source="REST",
        observed_at=NOW + timedelta(microseconds=1),
        raw_payload_hash=hashlib.sha256(b"trade-refresh-partial-trade:revision-2").hexdigest(),
        raw_payload_json={"revision": 2},
    )
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "local_open_order_absent_from_exchange_open_orders"},
        recorded_at=NOW,
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[], trades=[], positions=[]),
        conn,
        observed_at=NOW + timedelta(seconds=1),
    )

    assert result["status"] == "blocked"
    assert result["terminal_fill_local_orphan_summary"] == {"scanned": 0, "resolved": 0}
    assert conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()["resolved_at"] is None


@pytest.mark.parametrize(
    "command_state",
    [
        "SUBMITTING",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "ACKED",
        "POST_ACKED",
        "CANCEL_PENDING",
    ],
)
@pytest.mark.parametrize("point_response", ["terminal", "raw_none"])
def test_unresolved_refresh_acked_local_orphan_requires_typed_point_truth(
    conn,
    command_state,
    point_response,
):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings

    suffix = f"{command_state.lower()}-{point_response}"
    position_id = f"pos-refresh-{suffix}"
    command_id = f"cmd-refresh-{suffix}"
    order_id = f"ord-refresh-{suffix}"
    seed_position_baseline(conn, position_id=position_id, order_id="prior-filled-order")
    conn.execute(
        "UPDATE position_current SET phase='active', shares=9, chain_shares=9, "
        "cost_basis_usd=4.5, order_id='prior-filled-order', updated_at=? WHERE position_id=?",
        (NOW.isoformat(), position_id),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        state="ACKED",
        size=10,
        price=0.5,
    )
    if command_state != "ACKED":
        conn.execute(
            "UPDATE venue_commands SET state=? WHERE command_id=?",
            (command_state, command_id),
        )
    append_resting_order_fact(conn, command_id=command_id, venue_order_id=order_id)
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "local_open_order_absent", "trade_enumeration_available": True},
        recorded_at=NOW,
    )
    orders = (
        {
            order_id: order(
                order_id,
                status="CANCELED",
                size_matched="0",
                original_size="10",
                side="BUY",
                asset_id=YES_TOKEN,
                price="0.5",
            )
        }
        if point_response == "terminal"
        else {}
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5AdapterWithPointOrders(
            orders_by_id=orders,
            open_orders=[],
            trades=[],
            positions=[position(token_id=YES_TOKEN, size="9")],
        ),
        conn,
        observed_at=NOW + timedelta(seconds=1),
    )

    observed_command_state = conn.execute(
        "SELECT state FROM venue_commands WHERE command_id=?",
        (command_id,),
    ).fetchone()["state"]
    finding_row = conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()
    if point_response == "terminal":
        assert result["status"] == "resolved"
        assert observed_command_state == "EXPIRED"
        assert finding_row["resolved_at"] is not None
    else:
        assert result["status"] == "blocked"
        assert observed_command_state == command_state
        assert finding_row["resolved_at"] is None


@pytest.mark.parametrize("position_phase", ["active", "day0_window"])
def test_unresolved_refresh_acked_exit_terminal_no_fill_releases_redecision(
    conn,
    position_phase,
):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings

    position_id = f"pos-refresh-exit-{position_phase}"
    command_id = f"cmd-refresh-exit-{position_phase}"
    order_id = f"ord-refresh-exit-{position_phase}"
    seed_position_baseline(conn, position_id=position_id, order_id="entry-order")
    conn.execute(
        "UPDATE position_current SET phase=?, shares=9, chain_shares=9, "
        "cost_basis_usd=4.5, order_id='entry-order', updated_at=? WHERE position_id=?",
        (position_phase, NOW.isoformat(), position_id),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id=YES_TOKEN,
        side="SELL",
        state="ACKED",
        size=9,
        price=0.5,
        exit_close_position=True,
    )
    append_resting_order_fact(conn, command_id=command_id, venue_order_id=order_id)
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "local_open_order_absent", "trade_enumeration_available": True},
        recorded_at=NOW,
    )
    before_events = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=?",
        (position_id,),
    ).fetchone()[0]

    result = refresh_unresolved_reconcile_findings(
        FakeM5AdapterWithPointOrders(
            orders_by_id={
                order_id: order(
                    order_id,
                    status="CANCELED",
                    size_matched="0",
                    original_size="9",
                    side="SELL",
                    asset_id=YES_TOKEN,
                    price="0.5",
                )
            },
            open_orders=[],
            trades=[],
            positions=[position(token_id=YES_TOKEN, size="9")],
        ),
        conn,
        observed_at=NOW + timedelta(seconds=1),
    )

    assert result["status"] == "resolved"
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id=?",
        (command_id,),
    ).fetchone()["state"] == "EXPIRED"
    assert conn.execute(
        "SELECT phase,shares,order_id,exit_reason FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()[:] == (
        position_phase,
        9.0,
        "entry-order",
        "EXIT_ORDER_TERMINAL_NO_FILL_RELEASED",
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=?",
        (position_id,),
    ).fetchone()[0] == before_events + 1
    assert conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()["resolved_at"] is not None


def test_exact_terminal_no_fill_projects_terminal_entry_zero_exposure(conn):
    from src.execution.command_recovery import _terminalize_exact_local_orphan_no_fill
    from src.execution.exchange_reconcile import fresh_reconcile_snapshot, record_finding
    from src.venue.response_contracts import VenueOrderNotFound

    position_id = "pos-terminal-entry-zero"
    command_id = "cmd-terminal-entry-zero"
    order_id = "ord-terminal-entry-zero"
    seed_position_baseline(conn, position_id=position_id, order_id=order_id)
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        state="ACKED",
    )
    conn.execute(
        "UPDATE venue_commands SET state='SUBMIT_REJECTED' WHERE command_id=?",
        (command_id,),
    )
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "authenticated_submit_absence"},
        recorded_at=NOW,
    )

    class AuthenticatedAbsentAdapter(FakeM5Adapter):
        def get_order(self, queried_order_id):
            raise VenueOrderNotFound(queried_order_id)

    snapshot = fresh_reconcile_snapshot(
        AuthenticatedAbsentAdapter(open_orders=[], trades=[], positions=[]),
        observed_at=NOW + timedelta(seconds=1),
        trade_order_ids={order_id},
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
    )

    assert _terminalize_exact_local_orphan_no_fill(
        conn,
        command=command,
        client=snapshot.adapter,
        append_terminal_event=False,
        resolution="test_terminal_entry_zero",
    ) == "advanced"
    assert conn.execute(
        "SELECT phase,shares,cost_basis_usd FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()[:] == ("voided", 0.0, 0.0)
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id=?",
        (command_id,),
    ).fetchone()["state"] == "SUBMIT_REJECTED"
    assert conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()["resolved_at"] is not None


def test_exact_terminal_no_fill_rolls_back_fact_event_projection_and_finding(
    conn,
    monkeypatch,
):
    import src.execution.command_recovery as recovery
    from src.execution.exchange_reconcile import fresh_reconcile_snapshot, record_finding

    position_id = "pos-terminal-rollback"
    command_id = "cmd-terminal-rollback"
    order_id = "ord-terminal-rollback"
    seed_position_baseline(conn, position_id=position_id, order_id=order_id)
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        state="ACKED",
    )
    append_resting_order_fact(conn, command_id=command_id, venue_order_id=order_id)
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "atomicity_probe"},
        recorded_at=NOW,
    )
    snapshot = fresh_reconcile_snapshot(
        FakeM5AdapterWithPointOrders(
            orders_by_id={
                order_id: order(order_id, status="CANCELED", size_matched="0")
            },
            open_orders=[],
            trades=[],
            positions=[],
        ),
        observed_at=NOW + timedelta(seconds=1),
        trade_order_ids={order_id},
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
    )
    before_fact_count = conn.execute(
        "SELECT COUNT(*) FROM venue_order_facts WHERE command_id=?",
        (command_id,),
    ).fetchone()[0]
    before_event_count = conn.execute(
        "SELECT COUNT(*) FROM venue_command_events WHERE command_id=?",
        (command_id,),
    ).fetchone()[0]
    monkeypatch.setattr(
        recovery,
        "_append_entry_order_voided_projection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("projection failed")),
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        recovery._terminalize_exact_local_orphan_no_fill(
            conn,
            command=command,
            client=snapshot.adapter,
            append_terminal_event=True,
            resolution="test_atomic_rollback",
        )

    assert conn.execute(
        "SELECT COUNT(*) FROM venue_order_facts WHERE command_id=?",
        (command_id,),
    ).fetchone()[0] == before_fact_count
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_command_events WHERE command_id=?",
        (command_id,),
    ).fetchone()[0] == before_event_count
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id=?",
        (command_id,),
    ).fetchone()["state"] == "ACKED"
    assert conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()["resolved_at"] is None


def test_exact_terminal_no_fill_rechecks_late_positive_trade_inside_savepoint(
    conn,
    monkeypatch,
):
    import src.execution.command_recovery as recovery
    from src.execution.exchange_reconcile import fresh_reconcile_snapshot, record_finding

    position_id = "pos-terminal-late-trade"
    command_id = "cmd-terminal-late-trade"
    order_id = "ord-terminal-late-trade"
    seed_position_baseline(conn, position_id=position_id, order_id=order_id)
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        state="ACKED",
    )
    append_resting_order_fact(conn, command_id=command_id, venue_order_id=order_id)
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "late_trade_race"},
        recorded_at=NOW,
    )
    snapshot = fresh_reconcile_snapshot(
        FakeM5AdapterWithPointOrders(
            orders_by_id={order_id: order(order_id, status="CANCELED", size_matched="0")},
            open_orders=[],
            trades=[],
            positions=[],
        ),
        observed_at=NOW + timedelta(seconds=1),
        trade_order_ids={order_id},
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
    )
    calls = 0

    def trade_count_after_snapshot(_conn, _command_id):
        nonlocal calls
        calls += 1
        return 0 if calls == 1 else 1

    monkeypatch.setattr(recovery, "_trade_fact_count", trade_count_after_snapshot)

    with pytest.raises(
        recovery.PositiveOrderFactContradictionError,
        match="truth_changed_before_terminal_write",
    ):
        recovery._terminalize_exact_local_orphan_no_fill(
            conn,
            command=command,
            client=snapshot.adapter,
            append_terminal_event=True,
            resolution="test_late_trade_race",
        )

    assert conn.execute(
        "SELECT COUNT(*) FROM venue_order_facts WHERE command_id=?",
        (command_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id=?",
        (command_id,),
    ).fetchone()["state"] == "ACKED"
    assert conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()["resolved_at"] is None


@pytest.mark.parametrize(
    ("intent_kind", "side"),
    [("ENTRY", "SELL"), ("EXIT", "BUY")],
)
def test_exact_terminal_no_fill_rejects_malformed_intent_side_pair(
    conn,
    intent_kind,
    side,
):
    from src.execution.command_recovery import _terminalize_exact_local_orphan_no_fill
    from src.execution.exchange_reconcile import fresh_reconcile_snapshot, record_finding

    suffix = f"{intent_kind.lower()}-{side.lower()}"
    position_id = f"pos-malformed-{suffix}"
    command_id = f"cmd-malformed-{suffix}"
    order_id = f"ord-malformed-{suffix}"
    seed_position_baseline(conn, position_id=position_id, order_id="entry-order")
    conn.execute(
        "UPDATE position_current SET phase='active',shares=9,chain_shares=9,cost_basis_usd=4.5 "
        "WHERE position_id=?",
        (position_id,),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        state="ACKED",
    )
    conn.execute(
        "UPDATE venue_commands SET intent_kind=?,side=? WHERE command_id=?",
        (intent_kind, side, command_id),
    )
    append_resting_order_fact(conn, command_id=command_id, venue_order_id=order_id)
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "malformed_intent_side"},
        recorded_at=NOW,
    )
    snapshot = fresh_reconcile_snapshot(
        FakeM5AdapterWithPointOrders(
            orders_by_id={order_id: order(order_id, status="CANCELED", size_matched="0")},
            open_orders=[],
            trades=[],
            positions=[],
        ),
        observed_at=NOW + timedelta(seconds=1),
        trade_order_ids={order_id},
    )
    command = dict(
        conn.execute(
            "SELECT * FROM venue_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
    )

    assert _terminalize_exact_local_orphan_no_fill(
        conn,
        command=command,
        client=snapshot.adapter,
        append_terminal_event=True,
        resolution="test_malformed_pair",
    ) == "stayed"
    assert conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()["resolved_at"] is None


def test_unresolved_refresh_resolves_reappeared_open_local_orphan_without_mutation(conn):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings

    command_id = "cmd-refresh-reappeared"
    order_id = "ord-refresh-reappeared"
    seed_command(conn, command_id=command_id, venue_order_id=order_id, state="ACKED")
    append_resting_order_fact(conn, command_id=command_id, venue_order_id=order_id)
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "local_open_order_absent", "trade_enumeration_available": True},
        recorded_at=NOW,
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[order(order_id, status="LIVE")], trades=[], positions=[]),
        conn,
        observed_at=NOW + timedelta(seconds=1),
    )

    assert result["status"] == "resolved"
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id=?", (command_id,)
    ).fetchone()["state"] == "ACKED"
    assert conn.execute(
        "SELECT resolution FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()["resolution"] == "local_orphan_order_reappeared_open"


def test_unresolved_refresh_terminal_command_reappeared_open_stays_blocked(conn):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings

    command_id = "cmd-refresh-terminal-reappeared"
    order_id = "ord-refresh-terminal-reappeared"
    seed_command(conn, command_id=command_id, venue_order_id=order_id, state="ACKED")
    conn.execute(
        "UPDATE venue_commands SET state='EXPIRED' WHERE command_id=?",
        (command_id,),
    )
    append_resting_order_fact(conn, command_id=command_id, venue_order_id=order_id)
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "terminal_command_reappeared"},
        recorded_at=NOW,
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[order(order_id, status="LIVE")], trades=[], positions=[]),
        conn,
        observed_at=NOW + timedelta(seconds=1),
    )

    assert result["status"] == "blocked"
    assert conn.execute(
        "SELECT resolved_at FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()["resolved_at"] is None
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id=?",
        (command_id,),
    ).fetchone()["state"] == "EXPIRED"


def test_unresolved_refresh_keeps_duplicate_local_orphan_identity_fail_closed(conn):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings

    command_id = "cmd-refresh-duplicate-orphan"
    order_id = "ord-refresh-duplicate-orphan"
    seed_command(conn, command_id=command_id, venue_order_id=order_id, state="ACKED")
    append_resting_order_fact(conn, command_id=command_id, venue_order_id=order_id)
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "duplicate-a", "trade_enumeration_available": True},
        recorded_at=NOW,
    )
    conn.execute(
        "INSERT INTO exchange_reconcile_findings "
        "(finding_id,kind,subject_id,context,evidence_json,recorded_at) "
        "SELECT ?,kind,subject_id,'periodic',?,? FROM exchange_reconcile_findings "
        "WHERE finding_id=?",
        (
            f"{finding.finding_id}-duplicate",
            json.dumps({"reason": "duplicate-b", "trade_enumeration_available": True}),
            (NOW + timedelta(microseconds=1)).isoformat(),
            finding.finding_id,
        ),
    )

    result = refresh_unresolved_reconcile_findings(
        FakeM5Adapter(open_orders=[order(order_id, status="LIVE")], trades=[], positions=[]),
        conn,
        observed_at=NOW + timedelta(seconds=1),
    )

    assert result["status"] == "blocked"
    assert result["remaining"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM exchange_reconcile_findings "
        "WHERE subject_id=? AND resolved_at IS NULL",
        (order_id,),
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT state FROM venue_commands WHERE command_id=?", (command_id,)
    ).fetchone()["state"] == "ACKED"


@pytest.mark.parametrize("position_phase", ["settled", "active"])
def test_unresolved_refresh_resolves_authenticated_absent_terminal_exit_finding(
    conn,
    position_phase,
):
    from src.execution.exchange_reconcile import record_finding, refresh_unresolved_reconcile_findings

    position_id = "pos-refresh-terminal-exit"
    command_id = "cmd-refresh-terminal-exit"
    order_id = "ord-refresh-terminal-exit"
    seed_position_baseline(conn, position_id=position_id, order_id=order_id)
    conn.execute(
        "UPDATE position_current SET phase=?, shares=13, chain_shares=13, "
        "cost_basis_usd=5.33, settled_at=?, updated_at=? WHERE position_id=?",
        (position_phase, NOW.isoformat(), NOW.isoformat(), position_id),
    )
    seed_command(
        conn,
        command_id=command_id,
        venue_order_id=order_id,
        position_id=position_id,
        token_id="terminal-exit-token",
        side="SELL",
        state="ACKED",
        size=13,
        price=0.41,
        exit_close_position=True,
    )
    occurred_at = (NOW + timedelta(seconds=1)).isoformat()
    sequence = conn.execute(
        "SELECT MAX(sequence_no)+1 FROM venue_command_events WHERE command_id=?",
        (command_id,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE venue_commands SET state='SUBMIT_REJECTED', updated_at=? WHERE command_id=?",
        (occurred_at, command_id),
    )
    conn.execute(
        "INSERT INTO venue_command_events(event_id,command_id,sequence_no,event_type,occurred_at,payload_json,state_after) "
        "VALUES (?,?,?,'SUBMIT_REJECTED',?,?,'SUBMIT_REJECTED')",
        (
            "event-refresh-terminal-exit",
            command_id,
            sequence,
            occurred_at,
            json.dumps(
                {
                    "reason": "safe_replay_permitted_no_order_found",
                    "safe_replay_permitted": True,
                    "lookup_method": "authenticated_venue_absence",
                    "venue_absence_proof": {
                        "source": "authenticated_clob_user_read",
                        "owner_scope": "authenticated_funder",
                        "command_id": command_id,
                        "venue_order_id": order_id,
                        "open_orders_query_complete": True,
                        "trades_query_complete": True,
                        "point_order_query_complete": True,
                        "point_order_absent": True,
                        "matching_open_order_count": 0,
                        "matching_trade_count": 0,
                    },
                },
                sort_keys=True,
            ),
        ),
    )
    finding = record_finding(
        conn,
        kind="local_orphan_order",
        subject_id=order_id,
        context="ws_gap",
        evidence={"reason": "local_open_order_absent", "trade_enumeration_available": True},
        recorded_at=NOW + timedelta(seconds=2),
    )
    event_count = conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=?", (position_id,)
    ).fetchone()[0]

    class AuthenticatedAbsentAdapter(FakeM5Adapter):
        def get_order(self, queried_order_id):
            from src.venue.response_contracts import VenueOrderNotFound

            raise VenueOrderNotFound(queried_order_id)

    result = refresh_unresolved_reconcile_findings(
        AuthenticatedAbsentAdapter(open_orders=[], trades=[], positions=[]),
        conn,
        observed_at=NOW + timedelta(seconds=3),
    )

    assert result["status"] == "resolved"
    assert conn.execute(
        "SELECT resolution FROM exchange_reconcile_findings WHERE finding_id=?",
        (finding.finding_id,),
    ).fetchone()["resolution"] == "command_recovery_authenticated_submit_absence"
    assert conn.execute(
        "SELECT phase,shares,chain_shares FROM position_current WHERE position_id=?",
        (position_id,),
    ).fetchone()[:] == (position_phase, 13.0, 13.0)
    assert conn.execute(
        "SELECT COUNT(*) FROM position_events WHERE position_id=?", (position_id,)
    ).fetchone()[0] == event_count + (1 if position_phase == "active" else 0)
    assert conn.execute(
        "SELECT state,matched_size,remaining_size FROM venue_order_facts "
        "WHERE command_id=? ORDER BY local_sequence DESC LIMIT 1",
        (command_id,),
    ).fetchone()[:] == ("VENUE_WIPED", "0", "0")


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


def test_entry_reobservation_cannot_resurrect_post_reduction_exposure(conn):
    from src.execution.exchange_reconcile import (
        reconcile_recorded_maker_fill_economics,
        run_reconcile_sweep,
    )

    seed_command(conn, state="FILLED", size=23.7, price=0.07)
    seed_position_baseline(conn)
    seed_trade_decision_runtime_alias(conn)
    maker_trade = TradeFact(
        raw={
            "id": "trade-entry-before-reduction",
            "taker_order_id": "ord-other-taker",
            "status": "CONFIRMED",
            "size": "23.7",
            "price": "0.93",
            "transaction_hash": "0xentrybeforereduction",
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
    entry_sequence = conn.execute(
        """
        SELECT sequence_no
          FROM position_events
         WHERE position_id = 'pos-m5'
           AND event_type = 'ENTRY_ORDER_FILLED'
           AND order_id = 'ord-m5'
        """
    ).fetchone()[0]
    reduction_sequence = int(entry_sequence) + 1
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, order_id,
            command_id, caused_by, source_module, payload_json, env
        ) VALUES (?, 'pos-m5', 1, ?, 'MONITOR_REFRESHED', ?, 'day0_window',
                  'day0_window', 'opening_inertia', 'ord-exit-reduction',
                  'cmd-exit-reduction', 'partial_exit_fill',
                  'tests.test_exchange_reconcile', ?, 'live')
        """,
        (
            f"pos-m5:partial-exit:{reduction_sequence}",
            reduction_sequence,
            (NOW + timedelta(minutes=1)).isoformat(),
            json.dumps(
                {
                    "semantic_event": "CAPITAL_REDUCTION_FILLED",
                    "filled_shares": "20",
                    "remaining_shares": "3.7",
                    "remaining_cost_basis_usd": "0.259",
                },
                sort_keys=True,
            ),
        ),
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'day0_window',
               shares = 3.7,
               cost_basis_usd = 0.259,
               chain_shares = 3.7,
               chain_cost_basis_usd = 0.259,
               updated_at = ?
         WHERE position_id = 'pos-m5'
        """,
        ((NOW + timedelta(minutes=1)).isoformat(),),
    )

    summary = reconcile_recorded_maker_fill_economics(
        conn,
        observed_at=NOW + timedelta(minutes=2),
    )

    assert summary["errors"] == 0
    current = conn.execute(
        """
        SELECT phase, shares, cost_basis_usd, chain_shares,
               chain_cost_basis_usd
          FROM position_current
         WHERE position_id = 'pos-m5'
        """
    ).fetchone()
    assert dict(current) == pytest.approx(
        {
            "phase": "day0_window",
            "shares": 3.7,
            "cost_basis_usd": 0.259,
            "chain_shares": 3.7,
            "chain_cost_basis_usd": 0.259,
        }
    )


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
    from src.state.db import init_schema, init_schema_trade_only

    conn = sqlite3.connect(":memory:", factory=FailingCommitConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    init_schema_trade_only(conn)
    init_collateral_schema(conn)
    seed_command(conn, size=5)
    seed_position_baseline(conn)
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


@pytest.mark.parametrize("read_raises", [False, True], ids=["clean_read", "read_error"])
def test_cold_boot_m5_keeps_latch_and_exit_retry_blocked_without_authenticated_clear(conn, read_raises):
    import src.main as main_module
    from src.control import ws_gap_guard

    class ReadFailingM5Adapter(FakeM5Adapter):
        def get_open_orders(self):
            self.calls.append(("get_open_orders", (), {}))
            raise RuntimeError("injected fresh M5 read failure")

    seed_command(conn, size=5, side="SELL")
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
    conn.commit()
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=None,
            subscription_state="DISCONNECTED",
            gap_reason="not_configured",
            m5_reconcile_required=True,
            updated_at=NOW,
        )
    )

    try:
        adapter_cls = ReadFailingM5Adapter if read_raises else FakeM5Adapter
        adapter = adapter_cls(open_orders=[order(order_id="ord-m5")], trades=[], positions=[])
        result = main_module._run_ws_gap_reconcile_if_required(
            adapter,
            conn_factory=lambda: conn,
            now=NOW,
        )

        retry_at = conn.execute(
            "SELECT next_exit_retry_at FROM position_current WHERE position_id = ?",
            ("exit-ws-gap",),
        ).fetchone()[0]
        assert result["status"] == "failed_closed"
        assert ws_gap_guard.status().m5_reconcile_required is True
        assert ws_gap_guard.summary(now=NOW)["entry"]["allow_submit"] is False
        assert retry_at == (NOW + timedelta(minutes=40)).isoformat()
        assert conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_RETRY_RELEASED'
            """,
            ("exit-ws-gap",),
        ).fetchone()[0] == 0
        if read_raises:
            assert result["error"] == "injected fresh M5 read failure"
            assert [call[0] for call in adapter.calls] == ["get_open_orders"]
        else:
            assert "cannot clear ws gap without healthy subscription" in result["error"]
            assert [call[0] for call in adapter.calls] == ["get_open_orders", "get_trades", "get_positions"]
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
    """Operator absorption keeps its caller-side reservation incident guard."""
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
    """REGRESSION: an unreserved operator absorption proceeds normally."""
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

    row = conn.execute(
        "SELECT state FROM venue_commands WHERE token_id = ? AND intent_kind = 'EXIT' AND side = 'SELL'",
        (token_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "FILLED"


def test_external_operator_close_synthetic_exit_command_has_null_q_version(conn):
    """SCH-W1.2-ORDER-STATE: absorption creates EXIT with q_version=NULL.

    The command is a typed journal correction, not a fresh decision basis, and
    only reuses the entry's snapshot/envelope identifiers as provenance.
    """
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
         WHERE token_id = ? AND intent_kind = 'EXIT' AND side = 'SELL'
        """,
        (token,),
    ).fetchone()
    assert row is not None
    assert row["intent_kind"] == "EXIT"
    assert row["side"] == "SELL"
    assert row["q_version"] is None
