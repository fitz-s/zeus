# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.live_cap import LiveCapError, LiveCapLedger


NOW = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)


def test_live_cap_reserve_atomic():
    ledger = LiveCapLedger(_conn())

    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )

    assert reservation.reservation_status == "RESERVED"
    assert reservation.order_count == 1


def test_live_cap_duplicate_event_idempotent_same_terms():
    ledger = LiveCapLedger(_conn())
    first = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )
    second = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )

    assert second.usage_id == first.usage_id


def test_live_cap_duplicate_event_different_terms_raises():
    ledger = LiveCapLedger(_conn())
    ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )

    with pytest.raises(LiveCapError, match="drift"):
        ledger.reserve(
            event_id="event-1",
            decision_time=NOW,
            cap_scope="live_canary",
            requested_notional_usd=4.0,
            max_notional_usd=5.0,
            max_orders_per_day=1,
        )


def test_live_cap_blocks_second_canary_order():
    ledger = LiveCapLedger(_conn())
    ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )

    with pytest.raises(LiveCapError, match="max_orders_per_day"):
        ledger.reserve(
            event_id="event-2",
            decision_time=NOW,
            cap_scope="live_canary",
            requested_notional_usd=1.0,
            max_notional_usd=5.0,
            max_orders_per_day=1,
        )


def test_live_cap_blocks_notional_above_limit():
    ledger = LiveCapLedger(_conn())

    with pytest.raises(LiveCapError, match="exceeds"):
        ledger.reserve(
            event_id="event-1",
            decision_time=NOW,
            cap_scope="live_canary",
            requested_notional_usd=5.01,
            max_notional_usd=5.0,
            max_orders_per_day=1,
        )


def test_live_cap_release_on_pre_command_failure():
    ledger = LiveCapLedger(_conn())
    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )

    ledger.release(reservation.usage_id, "final_intent_failed")

    assert ledger.get(reservation.usage_id).reservation_status == "RELEASED"


def test_live_cap_consume_after_execution_command():
    ledger = LiveCapLedger(_conn())
    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )

    ledger.consume(reservation.usage_id, final_intent_id="intent-1", execution_command_id="cmd-1")
    consumed = ledger.get(reservation.usage_id)

    assert consumed.reservation_status == "CONSUMED"
    assert consumed.final_intent_id == "intent-1"
    assert consumed.execution_command_id == "cmd-1"


def test_timeout_unknown_does_not_release_cap_without_reconcile():
    ledger = LiveCapLedger(_conn())
    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )
    ledger.consume(reservation.usage_id, final_intent_id="intent-1", execution_command_id="cmd-1")

    with pytest.raises(LiveCapError, match="cannot be released"):
        ledger.release(reservation.usage_id, "timeout_unknown")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
