# Created: 2026-04-27
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: M1 antibodies for command-side grammar amendment without collapsing U2 order/trade facts.
# Reuse: Run when CommandState, CommandEventType, or venue_command_repo transitions change.
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M1.yaml
"""M1 command grammar amendment tests."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import yaml

from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
from src.state.db import init_schema
from src.state.snapshot_repo import insert_snapshot
from src.state.venue_command_repo import append_event, get_command, insert_command, insert_submission_envelope

NOW = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    insert_snapshot(c, _snapshot())
    insert_submission_envelope(c, _envelope(), envelope_id="env-m1")
    yield c
    c.close()


def _snapshot(snapshot_id: str = "snap-m1") -> ExecutableMarketSnapshotV2:
    return ExecutableMarketSnapshotV2(
        snapshot_id=snapshot_id,
        gamma_market_id="gamma-m1",
        event_id="event-m1",
        event_slug="weather-m1",
        condition_id="condition-m1",
        question_id="question-m1",
        yes_token_id="yes-m1",
        no_token_id="no-m1",
        selected_outcome_token_id="yes-m1",
        outcome_label="YES",
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=NOW + timedelta(hours=1),
        market_end_at=NOW + timedelta(days=1),
        market_close_at=NOW + timedelta(days=1, hours=1),
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        fee_details={"bps": 0},
        token_map_raw={"YES": "yes-m1", "NO": "no-m1"},
        rfqe=None,
        neg_risk=False,
        orderbook_top_bid=Decimal("0.49"),
        orderbook_top_ask=Decimal("0.51"),
        orderbook_depth_jsonb='{"asks":[["0.51","100"]],"bids":[["0.49","100"]]}',
        raw_gamma_payload_hash=HASH_A,
        raw_clob_market_info_hash=HASH_B,
        raw_orderbook_hash=HASH_C,
        authority_tier="CLOB",
        captured_at=NOW,
        freshness_deadline=NOW + timedelta(seconds=30),
    )


def _envelope(envelope_id: str = "env-m1") -> VenueSubmissionEnvelope:
    return VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="test",
        host="https://clob-v2.polymarket.com",
        chain_id=137,
        funder_address="0xfunder",
        condition_id="condition-m1",
        question_id="question-m1",
        yes_token_id="yes-m1",
        no_token_id="no-m1",
        selected_outcome_token_id="yes-m1",
        outcome_label="YES",
        side="SELL",
        price=Decimal("0.50"),
        size=Decimal("10"),
        order_type="GTC",
        post_only=False,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        neg_risk=False,
        fee_details={"bps": 0},
        canonical_pre_sign_payload_hash=HASH_D,
        signed_order=None,
        signed_order_hash=None,
        raw_request_hash=HASH_E,
        raw_response_json=None,
        order_id=None,
        trade_ids=(),
        transaction_hashes=(),
        error_code=None,
        error_message=None,
        captured_at=NOW.isoformat(),
    )


def _insert(conn, command_id: str = "cmd-m1") -> None:
    insert_command(
        conn,
        command_id=command_id,
        snapshot_id="snap-m1",
        envelope_id="env-m1",
        position_id="pos-m1",
        decision_id="dec-m1",
        idempotency_key=command_id.replace("-", "")[:8].ljust(32, "0"),
        intent_kind="ENTRY",
        market_id="condition-m1",
        token_id="yes-m1",
        side="SELL",
        size=10,
        price=0.50,
        created_at=NOW.isoformat(),
        snapshot_checked_at=NOW.isoformat(),
    )


def test_new_command_states_pass_grammar_check():
    from src.execution.command_bus import CommandState

    values = {state.value for state in CommandState}
    assert {
        "SNAPSHOT_BOUND",
        "SIGNED_PERSISTED",
        "POSTING",
        "POST_ACKED",
        "SUBMIT_REJECTED",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
    } <= values


def test_new_event_types_pass_grammar_check():
    from src.execution.command_bus import CommandEventType

    values = {event.value for event in CommandEventType}
    assert {
        "SNAPSHOT_BOUND",
        "SIGNED_PERSISTED",
        "POSTING",
        "POST_ACKED",
        "SUBMIT_TIMEOUT_UNKNOWN",
        "CLOSED_MARKET_UNKNOWN",
        "CANCEL_FAILED",
        "CANCEL_REPLACE_BLOCKED",
    } <= values


def test_RESTING_not_added_to_CommandState_NC_NEW_E():
    from src.execution.command_bus import CommandState

    assert "RESTING" not in {state.value for state in CommandState}
    with pytest.raises(ValueError):
        CommandState("RESTING")


def test_m1_pre_side_effect_transition_chain_is_legal(conn):
    _insert(conn)
    append_event(conn, command_id="cmd-m1", event_type="SNAPSHOT_BOUND", occurred_at=NOW.isoformat())
    append_event(conn, command_id="cmd-m1", event_type="SIGNED_PERSISTED", occurred_at=NOW.isoformat())
    append_event(conn, command_id="cmd-m1", event_type="POSTING", occurred_at=NOW.isoformat())
    append_event(conn, command_id="cmd-m1", event_type="POST_ACKED", occurred_at=NOW.isoformat())

    assert get_command(conn, "cmd-m1")["state"] == "POST_ACKED"


def test_submit_timeout_unknown_enters_side_effect_unknown_state(conn):
    _insert(conn)
    append_event(conn, command_id="cmd-m1", event_type="SUBMIT_REQUESTED", occurred_at=NOW.isoformat())
    append_event(conn, command_id="cmd-m1", event_type="SUBMIT_TIMEOUT_UNKNOWN", occurred_at=NOW.isoformat())

    assert get_command(conn, "cmd-m1")["state"] == "SUBMIT_UNKNOWN_SIDE_EFFECT"


def test_cancel_failure_events_are_grammar_bound_to_review_required(conn):
    _insert(conn)
    append_event(conn, command_id="cmd-m1", event_type="CANCEL_REQUESTED", occurred_at=NOW.isoformat())
    append_event(conn, command_id="cmd-m1", event_type="CANCEL_FAILED", occurred_at=NOW.isoformat())

    assert get_command(conn, "cmd-m1")["state"] == "REVIEW_REQUIRED"


def test_inv_29_amendment_is_incorporated_with_planning_lock_receipt():
    from src.execution.command_bus import CommandEventType, CommandState

    index = open(
        "docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/INDEX.md",
        encoding="utf-8",
    ).read()
    receipt_path = (
        "docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/"
        "inv_29_amendment_2026-04-27.md"
    )
    assert "INV-29 amendment" in index
    assert receipt_path in index

    invariants = yaml.safe_load(open("architecture/invariants.yaml", encoding="utf-8"))
    inv29 = next(item for item in invariants["invariants"] if item["id"] == "INV-29")
    amendment = inv29["amendment"]

    assert amendment["status"] == "incorporated"
    assert amendment["receipt"] == receipt_path
    assert set(amendment["command_state_values"]) == {state.value for state in CommandState}
    assert set(amendment["command_event_type_values"]) == {
        event.value for event in CommandEventType
    }
    assert "RESTING" not in amendment["command_state_values"]
