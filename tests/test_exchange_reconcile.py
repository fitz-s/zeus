# Created: 2026-04-27
# Last reused/audited: 2026-05-08
# Lifecycle: created=2026-04-27; last_reviewed=2026-05-07; last_reused=2026-05-08
# Authority basis: docs/operations/task_2026-05-08_object_invariance_remaining_mainline/PLAN.md
# Purpose: R3 M5 exchange reconciliation sweep antibodies.
# Reuse: Run when exchange_reconcile, venue facts, findings, heartbeat/cutover reconciliation, or operator finding resolution changes.
"""R3 M5 exchange-reconciliation findings and trade-fact tests."""
from __future__ import annotations

import hashlib
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
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
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


def _ensure_snapshot(c, *, token_id: str = YES_TOKEN, snapshot_id: str | None = None) -> str:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    snapshot_id = snapshot_id or f"snap-{token_id}"
    if get_snapshot(c, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        c,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-m5",
            event_id="event-m5",
            event_slug="event-m5",
            condition_id="condition-m5",
            question_id="question-m5",
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
    envelope_id: str | None = None,
    side: str = "BUY",
    price: float | Decimal = 0.50,
    size: float | Decimal = 10.0,
) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    price_dec = Decimal(str(price))
    size_dec = Decimal(str(size))
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
) -> None:
    from src.state.venue_command_repo import append_event, insert_command

    insert_command(
        c,
        command_id=command_id,
        snapshot_id=_ensure_snapshot(c, token_id=token_id),
        envelope_id=_ensure_envelope(c, token_id=token_id, side=side, price=price, size=size),
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
    )
    if state in {"ACKED", "PARTIAL", "FILLED", "CANCEL_PENDING"}:
        append_event(c, command_id=command_id, event_type="SUBMIT_REQUESTED", occurred_at=created_at.isoformat())
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
    state="CONFIRMED",
):
    from src.state.venue_command_repo import append_trade_fact as append

    append(
        c,
        trade_id=trade_id,
        venue_order_id=venue_order_id,
        command_id=command_id,
        state=state,
        filled_size=size,
        fill_price="0.50",
        source="REST",
        observed_at=NOW,
        raw_payload_hash=hashlib.sha256(f"{trade_id}:{token_id}:{size}:{state}".encode()).hexdigest(),
        raw_payload_json={
            "trade_id": trade_id,
            "order_id": venue_order_id,
            "size": size,
            "state": state,
        },
    )


def findings(c):
    return c.execute(
        "SELECT * FROM exchange_reconcile_findings ORDER BY recorded_at, finding_id"
    ).fetchall()


def command_count(c):
    return c.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0]


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
    events, projection = build_entry_canonical_write(
        position_obj,
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


def test_local_RESTING_absent_at_exchange_with_no_trade_marks_canceled_or_wiped_or_suspect(conn):
    from src.execution.exchange_reconcile import run_reconcile_sweep

    for context, expected_kind in [
        ("periodic", "local_orphan_order"),
        ("heartbeat_loss", "heartbeat_suspected_cancel"),
        ("cutover", "cutover_wipe"),
    ]:
        local = sqlite3.connect(":memory:")
        local.row_factory = sqlite3.Row
        from src.state.db import init_schema

        init_schema(local)
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
        "SELECT phase, order_status FROM position_current WHERE position_id = 'pos-m5'"
    ).fetchone()
    assert dict(projection) == {"phase": "active", "order_status": "partial"}

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
