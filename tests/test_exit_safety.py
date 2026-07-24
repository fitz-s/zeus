# Created: 2026-04-27
# Last reused/audited: 2026-07-19
# Lifecycle: created=2026-04-27; last_reviewed=2026-07-19; last_reused=2026-07-19
# Authority basis: docs/operations/current/finite_evidence_probability_symmetry/PLAN.md
# Purpose: Lock R3 M4 cancel/replace exit mutex, typed cancel outcomes, replacement gates, and CTF preflight.
# Reuse: Run when exit_safety, executor exit submit, exit_lifecycle cancel retry, venue command transitions, or collateral sell preflight changes.
"""R3 M4 exit-safety antibodies for cancel/replace and exit mutex behavior."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
    assert json.loads(event["payload_json"])["semantic_event"] == "PARTIAL_FILL_OBSERVED"


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


def test_confirmed_partial_fak_capital_reduction_keeps_remaining_position_open(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import Position

    position = Position(
        trade_id="pos-capital-reduction",
        market_id="mkt-capital-reduction",
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
        condition_id="condition-capital-reduction",
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

    reduced = exit_lifecycle._complete_intentional_position_reduction(
        position,
        intended_shares=Decimal("6"),
        confirmed_filled_shares=Decimal("2.5"),
        fill_price=0.60,
        order_id="ord-capital-reduction",
        status="CONFIRMED",
        conn=conn,
    )

    assert reduced == Decimal("2.5")
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
    assert json.loads(reduction["payload_json"])["semantic_event"] == (
        "CAPITAL_REDUCTION_FILLED"
    )
    assert conn.execute(
        """
        SELECT COUNT(*) FROM position_events
         WHERE position_id = ? AND event_type = 'EXIT_ORDER_FILLED'
        """,
        (position.trade_id,),
    ).fetchone()[0] == 0


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
    _insert_exit_command(
        conn,
        command_id="cmd-capital-reduction-fact",
        position_id=position.trade_id,
        token_id=NO_TOKEN,
        size=6.0,
        price=0.60,
        venue_order_id=position.last_exit_order_id,
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


def test_pending_exit_without_order_releases_for_redecision(conn):
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
    from src.state.portfolio import PortfolioState, Position

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
        exit_state="sell_placed",
        order_status="sell_placed",
        order_id="0xphantom-exit-order",
        last_exit_order_id="",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
        condition_id="condition-phantom-sell-projection",
    )

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
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position

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
    )
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
    payload = json.loads(event["payload_json"])
    assert event["event_type"] == "EXIT_RETRY_RELEASED"
    assert event["phase_before"] == "pending_exit"
    assert event["phase_after"] == "active"
    assert event["venue_status"] == "ready"


def test_pending_exit_releases_terminal_exit_order_without_polling_it(conn):
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState, Position
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
    )
    _insert_exit_command(
        conn,
        command_id="cmd-terminal-exit-order-release",
        position_id=position.trade_id,
        venue_order_id=position.last_exit_order_id,
        size=37.2,
        price=0.99,
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


def test_pending_exit_existing_confirmed_trade_fact_closes_before_retry_or_cancel(conn):
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
    _insert_exit_command(
        conn,
        command_id="cmd-exit-filled",
        position_id=position.trade_id,
        token_id=YES_TOKEN,
        size=25.07,
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
            "filled_size": "25.07",
            "fill_price": "0.61",
            "tx_hash": "0xexitfilled",
        },
    )
    append_trade_fact(
        conn,
        trade_id="trade-exit-filled",
        venue_order_id="ord-exit-filled",
        command_id="cmd-exit-filled",
        state="CONFIRMED",
        filled_size="25.07",
        fill_price="0.61",
        source="REST",
        observed_at="2026-07-08T14:42:59+00:00",
        raw_payload_hash="f" * 64,
        raw_payload_json={
            "source": "place_exit_order_matched_submit",
            "filled_size": "25.07",
            "fill_price": "0.61",
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
        "exit_price": 0.61,
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
        size_usd=10.0,
        entry_price=0.50,
        shares=20.0,
        cost_basis_usd=10.0,
        state="pending_exit",
        exit_state="sell_pending",
        last_exit_order_id="ord-partial-cancel",
        token_id=YES_TOKEN,
        no_token_id=NO_TOKEN,
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
                current_price=0.033323782234957027,
                best_bid=None,
                executable_snapshot_id=snapshot_id,
                executable_snapshot_min_tick_size=Decimal("0.001"),
                executable_snapshot_min_order_size=Decimal("0.01"),
                executable_snapshot_neg_risk=False,
            ),
            conn=conn,
            decision_id="exit-dynamic-tick",
        )
        command_row = conn.execute(
            "SELECT price, state FROM venue_commands WHERE position_id = ?",
            ("pos-dynamic-tick",),
        ).fetchone()

        assert result.status == "pending"
        assert result.submitted_price == pytest.approx(0.032)
        assert calls[0]["price"] == pytest.approx(0.032)
        assert command_row["price"] == pytest.approx(0.032)
        assert Decimal(str(command_row["price"])) % Decimal("0.001") == 0
        assert command_row["state"] == "ACKED"
    finally:
        from src.risk_allocator import clear_global_allocator

        clear_global_allocator()
        configure_global_ledger(None)


def test_exit_authority_deadline_is_rechecked_at_final_venue_seam(conn, monkeypatch):
    from src.execution.executor import create_exit_order_intent, execute_exit_order

    _enable_exit_submit_prereqs(conn, monkeypatch)
    venue_calls = []

    class Client:
        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

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
    assert captured == {
        "decision_id": "exit:pos-exit-refresh",
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


def test_live_exit_quick_confirmed_without_explicit_fill_price_does_not_close(monkeypatch):
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
        conn=None,
    )

    assert result == "sell_pending: order=ord-quick-confirmed-no-fill-price, status=CONFIRMED, missing_fill_price"
    assert position.state == "pending_exit"
    assert position.exit_state == "sell_pending"
    assert position.last_exit_error == "missing_exit_fill_price"
    assert position.shares == pytest.approx(20.0)
    assert position.size_usd == pytest.approx(10.0)
    assert position.cost_basis_usd == pytest.approx(10.0)
    assert portfolio.positions == [position]


def test_live_exit_refreshes_collateral_before_sell_preflight(conn, monkeypatch):
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

    def fake_refresh(active_conn, **kwargs):
        assert active_conn is conn
        assert kwargs["token_id"] == "yes-token-001"
        calls.append("refresh")
        return {"component": "collateral_snapshot_refresh", "allowed": True}

    def fake_check(*args, **kwargs):
        assert calls == ["refresh"]
        calls.append("check")
        return False, "ctf_tokens_insufficient: token_id=yes-token-001 required=20 available=0"

    monkeypatch.setattr(exit_lifecycle, "_refresh_exit_collateral_snapshot_for_submit", fake_refresh)
    monkeypatch.setattr(exit_lifecycle, "check_sell_collateral", fake_check)
    monkeypatch.setattr(
        exit_lifecycle,
        "execute_exit_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("submit must not run")),
    )

    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome.startswith("collateral_blocked: ctf_tokens_insufficient")
    assert calls == ["refresh", "check"]
    assert position.exit_state == "retry_pending"


def test_live_exit_collateral_refresh_failure_retries_before_preflight(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.collateral_ledger import CollateralInsufficient
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
        "_refresh_exit_collateral_snapshot_for_submit",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            CollateralInsufficient("collateral_refresh_failed: network")
        ),
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "check_sell_collateral",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("preflight must not run")),
    )

    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=object(),
        conn=conn,
    )

    assert outcome == "collateral_blocked: collateral_refresh_failed: network"
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

    outcome = exit_lifecycle.execute_exit(
        portfolio,
        position,
        exit_context,
        clob=None,
        conn=conn,
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


def test_live_exit_below_min_order_rejection_enters_dust_hold_not_retry(conn, monkeypatch):
    from src.execution import exit_lifecycle
    from src.state.portfolio import ExitContext, PortfolioState, Position

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
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="SETTLEMENT_IMMINENT",
        current_market_price=0.99,
        current_market_price_is_fresh=True,
        best_bid=0.99,
        hours_to_settlement=1.0,
        position_state="day0_window",
        day0_active=True,
    )
    error = "executable_snapshot_gate: size 1.5873 is below snapshot min_order_size 5"

    monkeypatch.setattr(exit_lifecycle, "check_sell_collateral", lambda *args, **kwargs: (True, ""))
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
    portfolio = PortfolioState(positions=[position])
    exit_context = ExitContext(
        exit_reason="FLASH_CRASH_PANIC",
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
        state="quarantined",
        chain_state="entry_authority_quarantined",
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
        phase_after="quarantined",
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


def test_no_bid_retry_waits_for_fresh_positive_bid_before_release(conn):
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
        snapshot_id="snap-in-band-bid-liquidity-wake",
        captured_at=now + timedelta(seconds=2),
        freshness_deadline=now + timedelta(minutes=5),
        orderbook_top_bid=Decimal("0.05"),
        orderbook_top_ask=Decimal("0.051"),
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
        price=0.02,
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
            current_market_price=0.02,
            current_market_price_is_fresh=True,
            best_bid=0.019,
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
            current_market_price=0.02,
            current_market_price_is_fresh=True,
            best_bid=0.019,
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
                    "price": "0.023",
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
            current_market_price=0.02,
            current_market_price_is_fresh=True,
            best_bid=0.019,
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
    assert command["price"] == 0.023
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

    assert result == "exit_blocked: no_in_band_bid"
    assert pos.last_exit_order_id == "ord-venue-open-exit"
    assert pos.exit_state == "sell_pending"
    assert pos.order_status == "sell_pending_confirmation"
    assert pos.exit_retry_count == 1
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
