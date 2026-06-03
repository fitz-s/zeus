# Created: 2026-05-25
# Last reused or audited: 2026-05-27
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


def test_live_cap_day_slot_blocks_second_connection_same_day(tmp_path):
    db_path = tmp_path / "cap.db"
    first_conn = _file_conn(db_path)
    second_conn = _file_conn(db_path)
    first = LiveCapLedger(first_conn)
    second = LiveCapLedger(second_conn)

    first.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )
    first_conn.commit()

    with pytest.raises(LiveCapError, match="max_orders_per_day"):
        second.reserve(
            event_id="event-2",
            decision_time=NOW,
            cap_scope="live_canary",
            requested_notional_usd=1.0,
            max_notional_usd=5.0,
            max_orders_per_day=1,
        )

    assert first_conn.execute("SELECT COUNT(*) FROM edli_live_cap_day_slots").fetchone()[0] == 1


def test_live_cap_release_frees_day_slot_for_pre_command_failure(tmp_path):
    db_path = tmp_path / "cap.db"
    conn = _file_conn(db_path)
    ledger = LiveCapLedger(conn)

    first = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )
    ledger.release(first.usage_id, "final_intent_failed")
    second = ledger.reserve(
        event_id="event-2",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=1.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )

    assert second.order_count == 1
    assert conn.execute("SELECT COUNT(*) FROM edli_live_cap_day_slots").fetchone()[0] == 1


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


def test_live_cap_rate_limiter_decoupled_from_notional_blocks_n_plus_one():
    # BUG #99 antibody: prove an order-emission RATE limit bounds frequency
    # INDEPENDENT of the notional cap. With a deliberately LOOSE notional cap
    # AND a large coupled day-slot pool (max_orders_per_day=1000 — the knob that
    # was raised in lockstep with the $5->$185 notional bump), a SEPARATE
    # per-window rate limit (max_orders_per_window) must still block the N+1th
    # order. If the rate limit were coupled to notional/day-count, this would not
    # fail. The default window cap is a conservative canary value (1).
    ledger = LiveCapLedger(_conn())

    first = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=185.0,
        max_notional_usd=185.0,
        max_orders_per_day=1000,
        max_orders_per_window=1,
    )
    assert first.reservation_status == "RESERVED"

    with pytest.raises(LiveCapError, match="rate"):
        ledger.reserve(
            event_id="event-2",
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=1.0,
            max_notional_usd=185.0,
            max_orders_per_day=1000,
            max_orders_per_window=1,
        )


def test_live_cap_rate_limiter_default_is_conservative_canary():
    # The decoupled rate limit must default to a SAFE conservative value when the
    # caller omits it (fail-closed): a single order per window. A second event in
    # the same window is blocked even though the day-slot pool has 999 free slots.
    ledger = LiveCapLedger(_conn())

    ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=185.0,
        max_notional_usd=185.0,
        max_orders_per_day=1000,
    )

    with pytest.raises(LiveCapError, match="rate"):
        ledger.reserve(
            event_id="event-2",
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=1.0,
            max_notional_usd=185.0,
            max_orders_per_day=1000,
        )


def test_live_cap_rate_limiter_allows_up_to_window_budget():
    # A window budget > 1 admits exactly that many orders, then blocks. Proves the
    # rate limit is a real independent counter, not a constant.
    ledger = LiveCapLedger(_conn())

    for n in range(3):
        ledger.reserve(
            event_id=f"event-{n}",
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=10.0,
            max_notional_usd=185.0,
            max_orders_per_day=1000,
            max_orders_per_window=3,
        )

    with pytest.raises(LiveCapError, match="rate"):
        ledger.reserve(
            event_id="event-overflow",
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=10.0,
            max_notional_usd=185.0,
            max_orders_per_day=1000,
            max_orders_per_window=3,
        )


def test_live_cap_rate_limiter_released_reservation_frees_window_slot(tmp_path):
    # Releasing a pre-command-failed reservation must return its window-slot so a
    # subsequent order can take it (mirrors day-slot release semantics).
    conn = _file_conn(tmp_path / "cap.db")
    ledger = LiveCapLedger(conn)

    first = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=10.0,
        max_notional_usd=185.0,
        max_orders_per_day=1000,
        max_orders_per_window=1,
    )
    ledger.release(first.usage_id, "final_intent_failed")

    second = ledger.reserve(
        event_id="event-2",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=10.0,
        max_notional_usd=185.0,
        max_orders_per_day=1000,
        max_orders_per_window=1,
    )
    assert second.reservation_status == "RESERVED"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _file_conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
