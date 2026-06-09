# Created: 2026-06-05
# Last reused/audited: 2026-06-08
# Authority basis: 2026-06-08 operator directive "completely delete the tiny_live
#   mechanism — no notional cap, no order-count caps, no cap-enabled flags".
#   The reservation records the uncapped Kelly notional and dedupes by event_id.
"""no-caps directive: the LiveCapLedger caps nothing — no notional cap, no per-day
or per-window order-count cap, and no cap-enabled flag path exists."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.live_cap import LiveCapError, LiveCapLedger


def _ledger():
    return LiveCapLedger(sqlite3.connect(":memory:"))


def _t(hour=0, second=0):
    return datetime(2026, 6, 5, hour, 0, second, tzinfo=timezone.utc)


def test_reserve_does_not_reject_large_notional():
    big = 1000.0
    r = _ledger().reserve(
        event_id="e1", decision_time=_t(), cap_scope="s",
        requested_notional_usd=big,
    )
    assert r.reserved_notional_usd == big, "no notional cap ⇒ no rejection at any size"


def test_reserve_does_not_apply_any_hidden_notional_ceiling():
    # Regression antibody: the no-cap path must not clamp Kelly $800 to any hidden
    # ceiling ($250 review finding, the old $5 cap, etc.) before or inside the ledger.
    r = _ledger().reserve(
        event_id="e-hard-ceiling-review",
        decision_time=_t(),
        cap_scope="s",
        requested_notional_usd=800.0,
    )
    assert r.reserved_notional_usd == 800.0


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


def test_no_per_day_count_cap_allows_many_orders_same_day():
    led = _ledger()
    for n in range(8):
        led.reserve(
            event_id=f"e{n}", decision_time=_t(second=n), cap_scope="s",
            requested_notional_usd=10.0,
        )
    assert (
        led.conn.execute("SELECT COUNT(*) FROM edli_live_cap_usage").fetchone()[0] == 8
    )


def test_no_rate_window_count_cap_allows_multiple_orders_same_60s_window():
    led = _ledger()
    led.reserve(
        event_id="e0", decision_time=_t(), cap_scope="s",
        requested_notional_usd=10.0,
    )
    second = led.reserve(
        event_id="e1", decision_time=_t(second=30), cap_scope="s",
        requested_notional_usd=10.0,
    )
    assert second.reservation_status == "RESERVED"


def test_drift_still_detected_on_same_event_changed_notional():
    # Exactly-once + drift is PRESERVED even though caps are gone.
    led = _ledger()
    led.reserve(
        event_id="e0", decision_time=_t(), cap_scope="s",
        requested_notional_usd=10.0,
    )
    with pytest.raises(LiveCapError, match="drift"):
        led.reserve(
            event_id="e0", decision_time=_t(), cap_scope="s",
            requested_notional_usd=11.0,
        )
