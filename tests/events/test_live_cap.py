# Created: 2026-05-25
# Last reused/audited: 2026-06-08
# Authority basis: 2026-06-08 operator directive — DELETE the tiny_live mechanism
#   ($5 per-order notional cap + per-day/per-window order-count caps). The
#   LiveCapLedger becomes a pure exactly-once + cert-chain reservation record:
#   it records the (uncapped) Kelly notional, dedupes by (event_id, cap_scope),
#   detects reserved-notional drift, and runs the RESERVED/RELEASED/CONSUMED state
#   machine. It NEVER rejects or clamps on a notional or order-count limit.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.live_cap import (
    LIVE_EXECUTION_RESERVATION_SCOPE,
    LiveCapError,
    LiveCapLedger,
)


NOW = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)


def test_live_cap_reserve_records_requested_notional():
    ledger = LiveCapLedger(_conn())

    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=43.0,
    )

    assert reservation.reservation_status == "RESERVED"
    assert reservation.reserved_notional_usd == 43.0


def test_live_cap_duplicate_event_idempotent_same_terms():
    # EXACTLY-ONCE: a re-reserve of the same (event_id, cap_scope) returns the
    # SAME usage_id — it never creates a second reservation row, so a live order
    # can never be double-submitted.
    ledger = LiveCapLedger(_conn())
    first = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
    )
    second = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
    )

    assert second.usage_id == first.usage_id
    assert (
        ledger.conn.execute(
            "SELECT COUNT(*) FROM edli_live_cap_usage WHERE event_id = 'event-1'"
        ).fetchone()[0]
        == 1
    )


def test_live_cap_duplicate_event_different_notional_raises_drift():
    # DRIFT DETECTION: a second reserve for the same event with a DIFFERENT
    # reserved notional is a defect, not a silent overwrite.
    ledger = LiveCapLedger(_conn())
    ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
    )

    with pytest.raises(LiveCapError, match="drift"):
        ledger.reserve(
            event_id="event-1",
            decision_time=NOW,
            cap_scope="live_canary",
            requested_notional_usd=4.0,
        )


def test_live_cap_no_per_day_order_cap_admits_many_orders_same_day():
    # The deleted per-day order-count cap must be GONE: an arbitrary number of
    # distinct events reserve in the same day with no rejection.
    ledger = LiveCapLedger(_conn())

    for n in range(10):
        r = ledger.reserve(
            event_id=f"event-{n}",
            decision_time=NOW,
            cap_scope=LIVE_EXECUTION_RESERVATION_SCOPE,
            requested_notional_usd=10.0,
        )
        assert r.reservation_status == "RESERVED"

    assert (
        ledger.conn.execute(
            "SELECT COUNT(*) FROM edli_live_cap_usage WHERE reservation_status = 'RESERVED'"
        ).fetchone()[0]
        == 10
    )


def test_live_cap_no_notional_cap_passes_arbitrary_kelly_size_through():
    # The deleted $5 notional cap must be GONE: a large Kelly notional reserves
    # in full with no LiveCapError and no clamp.
    ledger = LiveCapLedger(_conn())

    over = 1250.0
    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope=LIVE_EXECUTION_RESERVATION_SCOPE,
        requested_notional_usd=over,
    )

    assert reservation.reservation_status == "RESERVED"
    assert reservation.reserved_notional_usd == over


def test_live_cap_no_rate_window_cap_admits_many_orders_same_window():
    # The deleted per-window flood-guard cap must be GONE: multiple events in the
    # same 60s window all reserve.
    ledger = LiveCapLedger(_conn())

    for n in range(5):
        r = ledger.reserve(
            event_id=f"event-{n}",
            decision_time=NOW.replace(second=n),
            cap_scope=LIVE_EXECUTION_RESERVATION_SCOPE,
            requested_notional_usd=10.0,
        )
        assert r.reservation_status == "RESERVED"


def test_live_cap_release_on_pre_command_failure():
    ledger = LiveCapLedger(_conn())
    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
    )

    ledger.release(reservation.usage_id, "final_intent_failed")

    assert ledger.get(reservation.usage_id).reservation_status == "RELEASED"


def test_live_cap_released_row_can_rereserve_with_fresh_redecision_terms():
    ledger = LiveCapLedger(_conn())
    first = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
        final_intent_id="intent-old",
        execution_command_id="cmd-old",
    )
    ledger.release(first.usage_id, "entries_paused")

    second = ledger.reserve(
        event_id="event-1",
        decision_time=NOW.replace(minute=1),
        cap_scope="live_canary",
        requested_notional_usd=4.0,
        final_intent_id="intent-new",
        execution_command_id="cmd-new",
    )

    assert second.usage_id == first.usage_id
    assert second.reservation_status == "RESERVED"
    assert second.reserved_notional_usd == 4.0
    assert second.final_intent_id == "intent-new"
    assert second.execution_command_id == "cmd-new"
    assert (
        ledger.conn.execute(
            "SELECT COUNT(*) FROM edli_live_cap_usage WHERE event_id = 'event-1'"
        ).fetchone()[0]
        == 1
    )


def test_live_cap_consume_after_execution_command():
    ledger = LiveCapLedger(_conn())
    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="live_canary",
        requested_notional_usd=5.0,
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
    )
    ledger.consume(reservation.usage_id, final_intent_id="intent-1", execution_command_id="cmd-1")

    with pytest.raises(LiveCapError, match="cannot be released"):
        ledger.release(reservation.usage_id, "timeout_unknown")


def test_live_cap_reserve_rejects_nonpositive_notional():
    # The only rejection that remains is the basic sanity floor: a real order can
    # never be <= 0.
    ledger = LiveCapLedger(_conn())

    with pytest.raises(LiveCapError, match="positive"):
        ledger.reserve(
            event_id="event-1",
            decision_time=NOW,
            cap_scope=LIVE_EXECUTION_RESERVATION_SCOPE,
            requested_notional_usd=0.0,
        )


def test_live_execution_scope_dedupes_legacy_canary_reservation():
    ledger = LiveCapLedger(_conn())
    legacy = ledger.reserve(
        event_id="event-legacy",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=12.0,
        final_intent_id="intent-1",
    )

    current = ledger.reserve(
        event_id="event-legacy",
        decision_time=NOW,
        cap_scope=LIVE_EXECUTION_RESERVATION_SCOPE,
        requested_notional_usd=12.0,
        final_intent_id="intent-1",
    )

    assert current.usage_id == legacy.usage_id
    assert current.cap_scope == "tiny_live_canary"
    assert (
        ledger.conn.execute(
            "SELECT COUNT(*) FROM edli_live_cap_usage WHERE event_id = 'event-legacy'"
        ).fetchone()[0]
        == 1
    )


def test_new_live_execution_scope_serializes_no_canary_language():
    ledger = LiveCapLedger(_conn())

    reservation = ledger.reserve(
        event_id="event-current",
        decision_time=NOW,
        cap_scope=LIVE_EXECUTION_RESERVATION_SCOPE,
        requested_notional_usd=12.0,
    )

    assert reservation.cap_scope == LIVE_EXECUTION_RESERVATION_SCOPE
    assert "canary" not in reservation.certificate_payload()["cap_scope"]


def test_reserve_signature_has_no_cap_parameters():
    # Antibody: prove the cap parameters are physically GONE from the API, not
    # merely defaulted off.
    import inspect

    params = set(inspect.signature(LiveCapLedger.reserve).parameters)
    for forbidden in (
        "max_notional_usd",
        "max_orders_per_day",
        "max_orders_per_window",
        "notional_cap_enabled",
        "daily_order_cap_enabled",
    ):
        assert forbidden not in params, forbidden


def test_module_has_no_cap_sentinel_or_normalizer():
    # Antibody: the cap_explicitly_disabled sentinel and normalize_live_cap_request
    # clamp helper are DELETED.
    import src.events.live_cap as live_cap

    assert not hasattr(live_cap, "cap_explicitly_disabled")
    assert not hasattr(live_cap, "normalize_live_cap_request")
    assert not hasattr(live_cap, "LiveCapRequest")
    assert not any(name.startswith("HARD_NOTIONAL") for name in vars(live_cap))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
