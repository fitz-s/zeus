# Created: 2026-06-05
# Last reused/audited: 2026-06-05
# Authority basis: operator directive 2026-06-05 "no caps, no trade-count limits".
#   BUG #4 keeps the enabled flood-guard as a real 60s fixed window, not a hidden date cap.
"""no-caps directive: disabled daily cap means no hidden count cap."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.live_cap import LiveCapError, LiveCapLedger


def _ledger():
    return LiveCapLedger(sqlite3.connect(":memory:"))


def _t(hour=0):
    return datetime(2026, 6, 5, hour, 0, 0, tzinfo=timezone.utc)


def test_disabled_notional_cap_does_not_reject_at_configured_soft_cap():
    big = 1000.0
    r = _ledger().reserve(
        event_id="e1", decision_time=_t(), cap_scope="s",
        requested_notional_usd=big, max_notional_usd=5.0,
        max_orders_per_day=1000, max_orders_per_window=1,
        notional_cap_enabled=False, daily_order_cap_enabled=False,
    )
    assert r.reserved_notional_usd == big, "notional cap disabled ⇒ no configured soft-cap rejection"


def test_disabled_notional_cap_does_not_apply_legacy_250_review_ceiling():
    # Regression antibody for the stale review finding: the no-cap path must not
    # clamp Kelly $800 to a hidden $250 hard ceiling before or inside the ledger.
    r = _ledger().reserve(
        event_id="e-hard-ceiling-review",
        decision_time=_t(),
        cap_scope="s",
        requested_notional_usd=800.0,
        max_notional_usd=5.0,
        max_orders_per_day=1,
        max_orders_per_window=1,
        notional_cap_enabled=False,
        daily_order_cap_enabled=False,
    )
    assert r.reserved_notional_usd == 800.0
    assert r.max_notional_usd == 800.0


def test_historical_cap_audits_are_superseded_by_no_cap_authority():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    current = root / "docs/operations/LIVE_CAP_NO_CAP_REGRESSION_EVIDENCE_2026-06-05.md"
    text = current.read_text()
    assert "no configured notional cap and no non-configurable notional limit" in text
    for path in (
        root / "docs/operations/EDLI_LIVE_VS_DESIGN_MASTER_SPEC_2026-06-01.md",
        root / "docs/operations/BEST_ORDER_SELECTION_ROOT_2026-06-01.md",
        root / "docs/operations/PREARM_SAFETY_AUDIT_2026-06-02.md",
        root / "docs/operations/DROPPED_CONTEXT_SEAM_LEDGER_2026-06-01.md",
    ):
        body = path.read_text()
        assert "LIVE_CAP_NO_CAP_REGRESSION_EVIDENCE_2026-06-05.md" in body
        assert "Historical" in body or "Supersession" in body


def test_disabled_daily_cap_allows_multiple_orders_same_60s_window():
    led = _ledger()
    led.reserve(
        event_id="e0", decision_time=_t(), cap_scope="s",
        requested_notional_usd=10.0, max_notional_usd=1e12,
        max_orders_per_day=1, max_orders_per_window=1,
        notional_cap_enabled=False, daily_order_cap_enabled=False,
    )
    led.reserve(
        event_id="e1", decision_time=_t().replace(second=30), cap_scope="s",
        requested_notional_usd=10.0, max_notional_usd=1e12,
        max_orders_per_day=1, max_orders_per_window=1,
        notional_cap_enabled=False, daily_order_cap_enabled=False,
    )


def test_enabled_count_cap_still_limits_same_60s_window():
    led = _ledger()
    led.reserve(
        event_id="e0", decision_time=_t(), cap_scope="s",
        requested_notional_usd=10.0, max_notional_usd=1e12,
        max_orders_per_day=1000, max_orders_per_window=1,
        notional_cap_enabled=False, daily_order_cap_enabled=True,
    )
    with pytest.raises(LiveCapError, match="rate"):
        led.reserve(
            event_id="e1", decision_time=_t().replace(second=30), cap_scope="s",
            requested_notional_usd=10.0, max_notional_usd=1e12,
            max_orders_per_day=1000, max_orders_per_window=1,
            notional_cap_enabled=False, daily_order_cap_enabled=True,
        )


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
