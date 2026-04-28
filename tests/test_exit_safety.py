# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M4.yaml
# Purpose: Lock R3 M4 cancel/replace exit mutex, typed cancel outcomes, replacement gates, and CTF preflight.
# Reuse: Run when exit_safety, executor exit submit, exit_lifecycle cancel retry, venue command transitions, or collateral sell preflight changes.
"""R3 M4 exit-safety antibodies for cancel/replace and exit mutex behavior."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

_NOW = datetime(2026, 4, 27, tzinfo=timezone.utc)
YES_TOKEN = "yes-token-001"
_CTF_SCALE = 1_000_000


@pytest.fixture
def conn():
    from src.state.db import init_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
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


def _snapshot(*, pusd: int = 100_000_000, ctf: dict[str, int | float] | None = None):
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
        captured_at=_NOW,
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
            captured_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


def _ensure_envelope(
    c,
    *,
    token_id: str = YES_TOKEN,
    envelope_id: str | None = None,
    side: str = "SELL",
    price: float | Decimal = 0.49,
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
) -> None:
    from src.state.venue_command_repo import insert_command

    insert_command(
        c,
        command_id=command_id,
        snapshot_id=_ensure_snapshot(c, token_id=token_id),
        envelope_id=_ensure_envelope(c, token_id=token_id, side="SELL", price=price, size=size),
        position_id=position_id,
        decision_id=f"dec-{command_id}",
        idempotency_key=f"idem-{command_id}",
        intent_kind="EXIT",
        market_id=token_id,
        token_id=token_id,
        side="SELL",
        size=size,
        price=price,
        created_at=_NOW.isoformat(),
        venue_order_id=venue_order_id,
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
    events = [event["event_type"] for event in list_events(conn, "cmd-exit-1")]
    assert events[-2:] == ["CANCEL_REQUESTED", "CANCEL_REPLACE_BLOCKED"]
    allowed, reason = can_submit_replacement_sell(conn, "pos-1", YES_TOKEN)
    assert allowed is False
    assert "cancel_unknown_requires_m5" in (reason or "")


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
        def place_limit_order(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "orderID": "ord-1", "status": "LIVE"}

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


def test_exit_lifecycle_resolves_latest_fresh_snapshot_for_executor(conn, monkeypatch):
    from src.execution import exit_lifecycle

    captured = {}
    snapshot_id = _ensure_snapshot(conn, token_id=YES_TOKEN, snapshot_id="snap-exit-lifecycle")

    def fake_execute_exit_order(intent):
        captured.update(
            snapshot_id=intent.executable_snapshot_id,
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
        **exit_lifecycle._latest_exit_snapshot_context(conn, YES_TOKEN, now=_NOW),
    )

    assert result.status == "pending"
    assert captured == {
        "snapshot_id": snapshot_id,
        "min_tick": "0.01",
        "min_order": "0.01",
        "neg_risk": False,
    }


def test_exit_preflight_uses_token_balance_not_pusd(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.collateral_ledger import CollateralInsufficient, CollateralLedger, configure_global_ledger

    ledger = CollateralLedger(conn)
    ledger.set_snapshot(_snapshot(pusd=1_000_000_000_000, ctf={YES_TOKEN: 0}))
    configure_global_ledger(ledger)
    _allow_risk_allocator_for_exit_tests()
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.ws_gap_guard.assert_ws_allows_submit", lambda *args, **kwargs: None)

    class ClientShouldNotBeConstructed:
        def __init__(self, *args, **kwargs):  # pragma: no cover - tripwire
            raise AssertionError("token preflight must run before SDK construction")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", ClientShouldNotBeConstructed)
    try:
        with pytest.raises(CollateralInsufficient) as exc:
            execute_exit_order(
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
        assert "ctf_tokens_insufficient" in str(exc.value)
        assert "pusd" not in str(exc.value).lower()
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
