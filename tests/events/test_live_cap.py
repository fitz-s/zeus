# Created: 2026-05-25
# Last reused/audited: 2026-06-03
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment;
#   2026-06-03 operator directive: remove artificial notional + per-day caps via explicit unbounded
#   sentinel, fail-SAFE to capped on missing/malformed config.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.live_cap import (
    LiveCapError,
    LiveCapLedger,
    cap_explicitly_disabled,
)


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


def test_live_cap_rate_limiter_uses_60s_window_not_calendar_day():
    # BUG #4: with count caps enabled, max_orders_per_window is a fixed 60s
    # flood guard. A second order in the next minute must not be blocked by the
    # earlier calendar-date window_key behavior.
    ledger = LiveCapLedger(_conn())

    first = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=10.0,
        max_notional_usd=185.0,
        max_orders_per_day=1000,
        max_orders_per_window=1,
        daily_order_cap_enabled=True,
    )
    assert first.reservation_status == "RESERVED"

    second = ledger.reserve(
        event_id="event-2",
        decision_time=NOW.replace(minute=NOW.minute + 1),
        cap_scope="tiny_live_canary",
        requested_notional_usd=10.0,
        max_notional_usd=185.0,
        max_orders_per_day=1000,
        max_orders_per_window=1,
        daily_order_cap_enabled=True,
    )
    assert second.reservation_status == "RESERVED"


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


# ---------------------------------------------------------------------------
# 2026-06-03 operator directive: remove the artificial $5 notional + 1/day caps
# via an EXPLICIT unbounded sentinel, while preserving the fail-SAFE invariant:
# unbounded must be DELIBERATE. A missing or malformed cap config must STILL
# fail closed to the tight cap, never silently uncap on a config typo. Count-cap
# behavior is covered separately for disabled and enabled paths below.
# ---------------------------------------------------------------------------


def test_cap_explicitly_disabled_only_on_literal_false():
    # The disable sentinel is the literal JSON boolean false (Python False) and
    # NOTHING else. Every other value — missing, typo string, number, truthy —
    # is NOT a disable signal, so the cap stays enabled (fail-closed).
    assert cap_explicitly_disabled(False) is True

    # Fail-safe: none of these is the explicit sentinel -> cap stays ON.
    for not_a_sentinel in (None, "false", "False", "no", "0", 0, "", "true", True, 1, 1.0, {}, []):
        assert cap_explicitly_disabled(not_a_sentinel) is False, not_a_sentinel


def test_notional_cap_disabled_passes_kelly_size_through():
    # (a) RED-first: with the notional cap EXPLICITLY disabled, a Kelly-sized
    # request well above the old $5 configured limit reserves the full amount with no
    # LiveCapError. max_notional_usd is irrelevant when disabled.
    ledger = LiveCapLedger(_conn())

    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=43.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
        notional_cap_enabled=False,
    )

    assert reservation.reservation_status == "RESERVED"
    assert reservation.reserved_notional_usd == 43.0


def test_notional_cap_enabled_backward_compatible_still_blocks():
    # (b) Backward compat: with the cap ENABLED (explicit fail-closed path),
    # a request above the configured limit still raises exactly as before.
    ledger = LiveCapLedger(_conn())

    with pytest.raises(LiveCapError, match="exceeds"):
        ledger.reserve(
            event_id="event-1",
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=43.0,
            max_notional_usd=5.0,
            max_orders_per_day=1,
            notional_cap_enabled=True,
        )


def test_notional_cap_default_is_enabled_fail_closed():
    # FAIL-SAFE: the default of notional_cap_enabled is True. A caller that
    # forgets to pass the flag (e.g. malformed config dropped the key) gets the
    # tight cap, NOT unbounded.
    ledger = LiveCapLedger(_conn())

    with pytest.raises(LiveCapError, match="exceeds"):
        ledger.reserve(
            event_id="event-1",
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=43.0,
            max_notional_usd=5.0,
            max_orders_per_day=1,
        )


def test_daily_order_cap_disabled_admits_many_orders_same_day():
    # (a-day) With the per-day cap EXPLICITLY disabled, more than
    # max_orders_per_day orders are admitted in the same day.
    ledger = LiveCapLedger(_conn())

    for n in range(5):
        ledger.reserve(
            event_id=f"event-{n}",
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=10.0,
            max_notional_usd=5.0,
            max_orders_per_day=1,
            notional_cap_enabled=False,
            daily_order_cap_enabled=False,
            # window budget large so this test isolates the DAY cap, not the
            # flood-guard window cap (proven separately below).
            max_orders_per_window=1000,
        )

    # All five reserved; the day-slot pool did not cap.
    assert (
        ledger.conn.execute(
            "SELECT COUNT(*) FROM edli_live_cap_usage WHERE reservation_status = 'RESERVED'"
        ).fetchone()[0]
        == 5
    )


def test_daily_order_cap_default_is_enabled_fail_closed():
    # FAIL-SAFE: the per-day cap defaults to ENABLED. Omitting the flag keeps the
    # configured count limit enabled — a config typo cannot uncap.
    ledger = LiveCapLedger(_conn())

    ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=1.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
    )

    with pytest.raises(LiveCapError, match="max_orders_per_day"):
        ledger.reserve(
            event_id="event-2",
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=1.0,
            max_notional_usd=5.0,
            max_orders_per_day=1,
        )


def test_no_count_cap_when_daily_cap_disabled_operator_2026_06_05():
    # Operator no-cap directive: disabling the daily order cap disables count
    # caps, including the rate-window slot pool. This prevents the flood-guard
    # table from becoming a hidden order-count cap.
    ledger = LiveCapLedger(_conn())

    first = ledger.reserve(
        event_id="event-1", decision_time=NOW, cap_scope="tiny_live_canary",
        requested_notional_usd=500.0, max_notional_usd=5.0, max_orders_per_day=1,
        notional_cap_enabled=False, daily_order_cap_enabled=False, max_orders_per_window=1,
    )
    assert first.reservation_status == "RESERVED"

    same_window = ledger.reserve(
        event_id="event-2",
        decision_time=NOW.replace(second=NOW.second + 30),
        cap_scope="tiny_live_canary",
        requested_notional_usd=500.0, max_notional_usd=5.0, max_orders_per_day=1,
        notional_cap_enabled=False, daily_order_cap_enabled=False, max_orders_per_window=1,
    )
    assert same_window.reservation_status == "RESERVED"

    second = ledger.reserve(
        event_id="event-3",
        decision_time=NOW.replace(minute=NOW.minute + 1),
        cap_scope="tiny_live_canary",
        requested_notional_usd=500.0, max_notional_usd=5.0, max_orders_per_day=1,
        notional_cap_enabled=False, daily_order_cap_enabled=False, max_orders_per_window=1,
    )
    assert second.reservation_status == "RESERVED"


def test_count_cap_enabled_rate_window_blocks_second_same_60s_window():
    # Enabled count-cap path: max_orders_per_window still blocks a second order
    # in the same fixed 60s bucket.
    ledger = LiveCapLedger(_conn())

    ledger.reserve(
        event_id="event-1", decision_time=NOW, cap_scope="tiny_live_canary",
        requested_notional_usd=500.0, max_notional_usd=5.0, max_orders_per_day=1000,
        notional_cap_enabled=False, daily_order_cap_enabled=True, max_orders_per_window=1,
    )

    with pytest.raises(LiveCapError, match="rate"):
        ledger.reserve(
            event_id="event-2",
            decision_time=NOW.replace(second=NOW.second + 30),
            cap_scope="tiny_live_canary",
            requested_notional_usd=500.0,
            max_notional_usd=5.0,
            max_orders_per_day=1000,
            notional_cap_enabled=False,
            daily_order_cap_enabled=True,
            max_orders_per_window=1,
        )


def test_disabled_caps_still_reject_nonpositive_notional():
    # Disabling configured live caps does NOT disable the basic sanity floor: a
    # non-positive notional is still rejected (a real order can never be <= 0).
    ledger = LiveCapLedger(_conn())

    with pytest.raises(LiveCapError, match="positive"):
        ledger.reserve(
            event_id="event-1",
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=0.0,
            max_notional_usd=5.0,
            max_orders_per_day=1,
            notional_cap_enabled=False,
            daily_order_cap_enabled=False,
        )


def test_no_legacy_hard_notional_limit_symbol_exists():
    import src.events.live_cap as live_cap

    assert not any(name.startswith("HARD_NOTIONAL") for name in vars(live_cap))


def test_notional_cap_disabled_has_no_arbitrary_notional_limit_operator_2026_06_05():
    ledger = LiveCapLedger(_conn())
    over = 1250.0
    reservation = ledger.reserve(
        event_id="event-1", decision_time=NOW, cap_scope="tiny_live_canary",
        requested_notional_usd=over, max_notional_usd=1e12, max_orders_per_day=1,
        notional_cap_enabled=False, daily_order_cap_enabled=False,
    )
    assert reservation.reservation_status == "RESERVED"
    assert reservation.reserved_notional_usd == over


def test_notional_cap_enabled_uses_only_configured_soft_cap():
    ledger = LiveCapLedger(_conn())
    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=1000.0,
        max_notional_usd=1000.0,
        max_orders_per_day=1,
        notional_cap_enabled=True,
    )

    assert reservation.reserved_notional_usd == 1000.0


def test_notional_cap_disabled_records_requested_size_as_row_max():
    ledger = LiveCapLedger(_conn())
    reservation = ledger.reserve(
        event_id="event-1",
        decision_time=NOW,
        cap_scope="tiny_live_canary",
        requested_notional_usd=999.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
        notional_cap_enabled=False,
    )

    assert reservation.max_notional_usd == 999.0
    assert reservation.reserved_notional_usd == 999.0


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _file_conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
