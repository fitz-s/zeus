# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: R3 T1 fake/live adapter parity P0 safety scenario antibodies.
# Reuse: Run before fake/live adapter parity, adapter protocol, or live-readiness gate changes.
# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/T1.yaml
"""R3 T1 P0 live-money safety scenarios against FakePolymarketVenue."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps


@dataclass(frozen=True)
class ScenarioSnapshot:
    condition_id: str = "cond-p0"
    question_id: str = "question-p0"
    yes_token_id: str = "yes-token"
    no_token_id: str = "no-token"
    tick_size: Decimal = Decimal("0.01")
    min_order_size: Decimal = Decimal("5")
    neg_risk: bool = False
    fee_details: dict = field(default_factory=lambda: {"bps": 0, "builder_fee_bps": 0})
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    freshness_window_seconds: int = 300


def _intent(*, token_id: str = "yes-token", price: float = 0.50, size_usd: float = 10.0) -> ExecutionIntent:
    return ExecutionIntent(
        direction=Direction.YES,
        target_size_usd=size_usd,
        limit_price=price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=True,
        market_id="market-p0",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.10,
    )


def _submit(fake, *, token_id: str = "yes-token", price: float = 0.50, size_usd: float = 10.0):
    envelope = fake.create_submission_envelope(
        _intent(token_id=token_id, price=price, size_usd=size_usd),
        ScenarioSnapshot(yes_token_id=token_id),
        "GTC",
    )
    return fake.submit(envelope)


def _persist_submit_journal_shape(result, *, prefix: str) -> dict[str, object]:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.db import init_schema
    from src.state.snapshot_repo import insert_snapshot
    from src.state.venue_command_repo import append_event, insert_command, insert_submission_envelope, list_events

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    now = datetime.now(timezone.utc)
    envelope = result.envelope
    snapshot_id = f"snap-{prefix}"
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-p0",
            event_id="event-p0",
            event_slug="event-p0",
            condition_id=envelope.condition_id,
            question_id=envelope.question_id,
            yes_token_id=envelope.yes_token_id,
            no_token_id=envelope.no_token_id,
            selected_outcome_token_id=envelope.selected_outcome_token_id,
            outcome_label=envelope.outcome_label,
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=envelope.tick_size,
            min_order_size=envelope.min_order_size,
            fee_details=envelope.fee_details,
            token_map_raw={"YES": envelope.yes_token_id, "NO": envelope.no_token_id},
            rfqe=None,
            neg_risk=envelope.neg_risk,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.51"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=now,
            freshness_deadline=now + timedelta(minutes=5),
        ),
    )
    envelope_id = insert_submission_envelope(conn, envelope, envelope_id=f"env-{prefix}")
    command_id = f"cmd-{prefix}"
    insert_command(
        conn,
        command_id=command_id,
        envelope_id=envelope_id,
        position_id=f"pos-{prefix}",
        decision_id=f"dec-{prefix}",
        idempotency_key=f"idemp-{prefix}",
        intent_kind="ENTRY",
        market_id="market-p0",
        token_id=envelope.selected_outcome_token_id,
        side=envelope.side,
        size=float(envelope.size),
        price=float(envelope.price),
        created_at=now.isoformat(),
        snapshot_id=snapshot_id,
        snapshot_checked_at=now,
        expected_min_tick_size=envelope.tick_size,
        expected_min_order_size=envelope.min_order_size,
        expected_neg_risk=envelope.neg_risk,
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at=now.isoformat(),
        payload={"raw_request_hash": envelope.raw_request_hash},
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_ACKED",
        occurred_at=now.isoformat(),
        payload={"venue_order_id": envelope.order_id, "raw_response_json": envelope.raw_response_json},
    )
    events = list_events(conn, command_id)
    return {
        "event_columns": [set(event) for event in events],
        "event_types": [event["event_type"] for event in events],
        "command_columns": set(conn.execute("SELECT * FROM venue_commands").fetchone().keys()),
        "envelope_columns": set(conn.execute("SELECT * FROM venue_submission_envelopes").fetchone().keys()),
    }


def test_duplicate_submit_idempotency():
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    fake = FakePolymarketVenue()
    envelope = fake.create_submission_envelope(_intent(), ScenarioSnapshot(), "GTC")

    first = fake.submit(envelope)
    second = fake.submit(envelope)

    assert first.status == second.status == "accepted"
    assert first.envelope.order_id == second.envelope.order_id
    assert len(fake.get_open_orders()) == 1


def test_rapid_sequential_partial_fills():
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    fake = FakePolymarketVenue()
    order = _submit(fake).envelope.order_id

    fake.force_partial_fill(order, 3)
    fake.force_partial_fill(order, 4)

    trades = fake.get_trades()
    assert [t.raw["state"] for t in trades] == ["MATCHED", "MATCHED"]
    assert fake.get_order(order).status == "PARTIALLY_MATCHED"


def test_red_cancel_all_behavioral():
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    fake = FakePolymarketVenue()
    _submit(fake, token_id="yes-token-a")
    _submit(fake, token_id="yes-token-b")

    for order in list(fake.get_open_orders()):
        fake.cancel(order.order_id)

    assert fake.get_open_orders() == []


def test_market_close_while_resting():
    from tests.fakes.polymarket_v2 import FailureMode, FakePolymarketVenue

    fake = FakePolymarketVenue()
    order_id = _submit(fake).envelope.order_id
    fake.inject(FailureMode.ORACLE_CONFLICT)

    market = fake.get_clob_market_info("cond-p0")
    fake.cancel(order_id)

    assert market.raw["status"] == "oracle_conflict"
    assert fake.get_order(order_id).status == "CANCEL_CONFIRMED"


def test_resubscribe_recovery():
    from tests.fakes.polymarket_v2 import FailureMode, FakePolymarketVenue

    fake = FakePolymarketVenue()
    _submit(fake)
    fake.heartbeat_miss_window(16)
    missed = fake.post_heartbeat("hb-1")
    fake.clear_injection(FailureMode.HEARTBEAT_MISS)
    recovered = fake.post_heartbeat("hb-2")

    assert missed.ok is False
    assert recovered.ok is True
    assert len(fake.get_open_orders()) == 1


def test_restart_mid_cycle_preserves_orders_and_records_recovery_boundary():
    from tests.fakes.polymarket_v2 import FailureMode, FakePolymarketVenue

    fake = FakePolymarketVenue()
    order_id = _submit(fake).envelope.order_id

    fake.inject(FailureMode.RESTART_MID_CYCLE, reason="process_restart")
    recovered_orders = fake.get_open_orders()

    assert [order.order_id for order in recovered_orders] == [order_id]
    assert fake.submit(fake.create_submission_envelope(_intent(), ScenarioSnapshot(), "GTC")).envelope.order_id == order_id
    assert fake.restart_events() == [
        {
            "generation": 1,
            "surface": "get_open_orders",
            "open_order_ids": [order_id],
            "observed_at": fake.clock.isoformat(),
            "reason": "process_restart",
        }
    ]


def test_heartbeat_miss_auto_cancel_and_reconcile():
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    fake = FakePolymarketVenue()
    _submit(fake)
    fake.heartbeat_miss_window(16)

    if not fake.post_heartbeat("hb-red").ok:
        for order in list(fake.get_open_orders()):
            fake.cancel(order.order_id)

    assert fake.get_open_orders() == []


def test_cutover_wipe_simulation_reconciles():
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    fake = FakePolymarketVenue()
    order_id = _submit(fake).envelope.order_id

    fake.open_order_wipe()

    assert fake.get_open_orders() == []
    assert fake.get_order(order_id).order_id == order_id


def test_pusd_insufficient_blocks_buy():
    from tests.fakes.polymarket_v2 import FakeCollateralLedger, FakePolymarketVenue

    fake = FakePolymarketVenue(ledger=FakeCollateralLedger(pusd_balance_micro=1))

    result = _submit(fake)

    assert result.status == "rejected"
    assert result.error_code == "INSUFFICIENT_PUSD"
    assert fake.get_open_orders() == []


def test_token_insufficient_blocks_sell():
    from tests.fakes.polymarket_v2 import FakeCollateralLedger, FakePolymarketVenue

    fake = FakePolymarketVenue(ledger=FakeCollateralLedger(ctf_token_balances_units={"yes-token": 1}))

    result = fake.submit_limit_order(token_id="yes-token", price=0.50, size=10.0, side="SELL")

    assert result.status == "rejected"
    assert result.error_code == "INSUFFICIENT_TOKEN_BALANCE"
    assert fake.get_open_orders() == []


def test_MATCHED_then_FAILED_chain_rolls_back_optimistic_exposure():
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    fake = FakePolymarketVenue()
    order_id = _submit(fake).envelope.order_id
    fake.force_partial_fill(order_id, 3)
    assert fake.get_positions()[0].raw["state"] == "OPTIMISTIC_EXPOSURE"

    fake.MATCHED_then_FAILED_chain(order_id)

    assert fake.get_order(order_id).status == "FAILED"
    assert fake.get_positions() == []
    assert fake.get_trades()[-1].raw["state"] == "FAILED"


def test_fake_and_live_produce_identical_journal_event_shapes(tmp_path):
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter, SubmitResult
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    class MockLiveClient:
        def get_ok(self):
            return {"ok": True}

        def create_and_post_order(self, *_args, **_kwargs):
            return {"success": True, "orderID": "live-ord-1", "status": "LIVE"}

    evidence = tmp_path / "q1.txt"
    evidence.write_text("ok\n")
    live = PolymarketV2Adapter(
        host="https://clob-v2.polymarket.com",
        funder_address="0xfake-funder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=evidence,
        client_factory=lambda **_kwargs: MockLiveClient(),
        sdk_version="fake-live-sdk",
    )
    fake = FakePolymarketVenue(funder_address="0xfake-funder", sdk_version="fake-live-sdk")

    live_envelope = live.create_submission_envelope(_intent(), ScenarioSnapshot(), "GTC")
    fake_envelope = fake.create_submission_envelope(_intent(), ScenarioSnapshot(), "GTC")
    live_result = live.submit(live_envelope)
    fake_result = fake.submit(fake_envelope)
    live_journal_shape = _persist_submit_journal_shape(live_result, prefix="live")
    fake_journal_shape = _persist_submit_journal_shape(fake_result, prefix="fake")

    assert {f.name for f in fields(SubmitResult)} == {"status", "envelope", "error_code", "error_message"}
    assert set(live_result.envelope.to_dict()) == set(fake_result.envelope.to_dict())
    assert set(json.loads(live_result.envelope.raw_response_json or "{}")) == set(
        json.loads(fake_result.envelope.raw_response_json or "{}")
    )
    assert live_journal_shape == fake_journal_shape
