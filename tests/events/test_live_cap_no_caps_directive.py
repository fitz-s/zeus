# Created: 2026-06-05
# Last reused/audited: 2026-06-05
# Authority basis: operator directive 2026-06-05 "no caps, no trade-count limits" (overrides the
#   2026-06-03 directive that kept the flood-guard rate window active). The artificial $250 hard
#   notional ceiling and the per-day rate-window order-COUNT cap are now FLAG-GATED: when their
#   enable flag is off, they impose NOTHING — fractional Kelly (forward-settlement risk allowance)
#   + the collateral ledger (physical wallet bound) are the sole constraints. These pin that
#   contract AND the regression that, when the flags are ON, the caps still apply unchanged.
"""no-caps directive: $250 ceiling + rate-window are flag-gated; disabled ⇒ no cap."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.live_cap import HARD_NOTIONAL_CEILING_USD, LiveCapError, LiveCapLedger


def _ledger():
    return LiveCapLedger(sqlite3.connect(":memory:"))


def _t(hour=0):
    return datetime(2026, 6, 5, hour, 0, 0, tzinfo=timezone.utc)


def test_disabled_notional_cap_does_not_clamp_at_hard_ceiling():
    big = HARD_NOTIONAL_CEILING_USD * 4
    r = _ledger().reserve(
        event_id="e1", decision_time=_t(), cap_scope="s",
        requested_notional_usd=big, max_notional_usd=1e12,
        max_orders_per_day=1000, max_orders_per_window=1,
        notional_cap_enabled=False, daily_order_cap_enabled=False,
    )
    assert r.reserved_notional_usd == big, "notional cap disabled ⇒ no arbitrary $250 clamp"


def test_enabled_notional_cap_still_clamps_at_hard_ceiling():
    r = _ledger().reserve(
        event_id="e1", decision_time=_t(), cap_scope="s",
        requested_notional_usd=HARD_NOTIONAL_CEILING_USD * 4, max_notional_usd=1e12,
        max_orders_per_day=1000, max_orders_per_window=1000,
        notional_cap_enabled=True, daily_order_cap_enabled=False,
    )
    assert r.reserved_notional_usd == HARD_NOTIONAL_CEILING_USD, "cap enabled ⇒ clamp preserved"


def test_disabled_daily_cap_allows_unlimited_orders_same_day():
    led = _ledger()
    # 5 distinct events, SAME calendar day, max_orders_per_window=1 — would be a hidden 1/day cap
    # under the old code. Disabled ⇒ no count limit, all 5 reserve.
    for i in range(5):
        led.reserve(
            event_id=f"e{i}", decision_time=_t(), cap_scope="s",
            requested_notional_usd=10.0, max_notional_usd=1e12,
            max_orders_per_day=1, max_orders_per_window=1,
            notional_cap_enabled=False, daily_order_cap_enabled=False,
        )  # no LiveCapError ⇒ no trade-count limit


def test_enabled_daily_cap_still_limits_per_day():
    led = _ledger()
    led.reserve(
        event_id="e0", decision_time=_t(), cap_scope="s",
        requested_notional_usd=10.0, max_notional_usd=1e12,
        max_orders_per_day=1, max_orders_per_window=1,
        notional_cap_enabled=False, daily_order_cap_enabled=True,
    )
    with pytest.raises(LiveCapError):
        led.reserve(
            event_id="e1", decision_time=_t(), cap_scope="s",
            requested_notional_usd=10.0, max_notional_usd=1e12,
            max_orders_per_day=1, max_orders_per_window=1,
            notional_cap_enabled=False, daily_order_cap_enabled=True,
        )  # cap enabled ⇒ second same-day order rejected (regression: caps still work when ON)
