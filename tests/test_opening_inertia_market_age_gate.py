# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: Operator directive 2026-06-09 — restore market-age gate for
#   opening_inertia lane under EDLI; legacy semantics max_hours_since_open < 24.
#   Load-bearing reason: kelly.py phase-aware multiplier sizes by opening-tick age;
#   mislabeled old market gets wrong sizing.
"""Antibody tests: opening_inertia market-age gate under EDLI.

Pins three behaviours:
  (i)   Old market (>= 24h) is rejected with OPENING_INERTIA_MARKET_TOO_OLD reason.
  (ii)  Young market (< 24h) passes the age gate.
  (iii) Missing age data (None timestamps) fails conservatively → passes (no reject).

The helper _opening_inertia_market_age_hours is also tested directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Unit tests: _opening_inertia_market_age_hours helper
# ---------------------------------------------------------------------------

def _make_snap(market_start_at: str | None = None, captured_at: str | None = None) -> dict:
    return {
        "market_start_at": market_start_at,
        "captured_at": captured_at,
    }


def _make_topo(created_at: str | None = None) -> dict:
    return {"created_at": created_at, "condition_id": "0xabc"}


class TestOpeningInertiaMarketAgeHoursHelper:
    """Direct unit tests of _opening_inertia_market_age_hours."""

    def _call(self, snapshot_row, topology_rows, family_rows, decision_time):
        from src.engine.event_reactor_adapter import _opening_inertia_market_age_hours
        return _opening_inertia_market_age_hours(
            snapshot_row=snapshot_row,
            topology_rows=topology_rows,
            family_rows=family_rows,
            decision_time=decision_time,
        )

    def test_uses_snapshot_market_start_at(self):
        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        opened = now - timedelta(hours=30)
        snap = _make_snap(market_start_at=opened.isoformat())
        age = self._call(snap, [], [], now)
        assert age is not None
        assert abs(age - 30.0) < 0.01

    def test_fallback_topology_created_at(self):
        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        opened = now - timedelta(hours=10)
        snap = _make_snap()  # no market_start_at
        topo = _make_topo(created_at=opened.isoformat())
        age = self._call(snap, [topo], [], now)
        assert age is not None
        assert abs(age - 10.0) < 0.01

    def test_fallback_family_captured_at(self):
        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        opened = now - timedelta(hours=5)
        snap = _make_snap()
        age = self._call(snap, [], [_make_snap(captured_at=opened.isoformat())], now)
        assert age is not None
        assert abs(age - 5.0) < 0.01

    def test_missing_all_timestamps_returns_none(self):
        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        age = self._call(_make_snap(), [], [], now)
        assert age is None

    def test_topology_none_created_at_falls_through_to_family(self):
        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        opened = now - timedelta(hours=3)
        snap = _make_snap()
        topo = _make_topo(created_at=None)  # topology exists but created_at None
        fam = _make_snap(captured_at=opened.isoformat())
        age = self._call(snap, [topo], [fam], now)
        assert age is not None
        assert abs(age - 3.0) < 0.01


# ---------------------------------------------------------------------------
# Integration tests: EventSubmissionReceipt from build_event_bound_no_submit_receipt
# These test the gate in the full wiring context via a minimal DB fixture.
# ---------------------------------------------------------------------------

_MARKET_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS market_events (
    market_slug         TEXT,
    city                TEXT,
    target_date         TEXT,
    temperature_metric  TEXT,
    condition_id        TEXT,
    token_id            TEXT,
    range_label         TEXT,
    range_low           REAL,
    range_high          REAL,
    outcome             TEXT,
    created_at          TEXT
)
"""

_EXECUTABLE_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS executable_market_snapshots (
    snapshot_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id            TEXT NOT NULL,
    market_slug             TEXT,
    market_start_at         TEXT,
    market_end_at           TEXT,
    orderbook_top_bid       REAL,
    orderbook_top_ask       REAL,
    captured_at             TEXT NOT NULL,
    freshness_deadline      TEXT,
    active                  INTEGER DEFAULT 1,
    closed                  INTEGER DEFAULT 0
)
"""

_OPPORTUNITY_DDL = """
CREATE TABLE IF NOT EXISTS opportunity_event_processing (
    condition_id TEXT,
    event_status TEXT DEFAULT 'pending',
    city TEXT,
    target_date TEXT,
    temperature_metric TEXT,
    market_slug TEXT
)
"""


def _build_snapshot_db(condition_id: str, market_start_at: str | None, captured_at: str, freshness_deadline: str) -> object:
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(_EXECUTABLE_SNAPSHOTS_DDL)
    conn.execute(_OPPORTUNITY_DDL)
    conn.execute("""
        INSERT INTO executable_market_snapshots
            (condition_id, market_start_at, orderbook_top_bid, orderbook_top_ask,
             captured_at, freshness_deadline, active, closed)
        VALUES (?, ?, 0.35, 0.40, ?, ?, 1, 0)
    """, (condition_id, market_start_at, captured_at, freshness_deadline))
    conn.execute("""
        INSERT INTO opportunity_event_processing (condition_id, event_status, city, target_date, temperature_metric)
        VALUES (?, 'pending', 'London', '2026-07-01', 'max')
    """, (condition_id,))
    conn.commit()
    return conn


def _build_forecast_db(condition_id: str) -> object:
    """Minimal forecast + topology DB."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute(_MARKET_EVENTS_DDL)
    # Single-bin family for simplicity
    conn.execute("""
        INSERT INTO market_events
            (market_slug, city, target_date, temperature_metric, condition_id, token_id,
             range_label, range_low, range_high, outcome, created_at)
        VALUES ('london-max-2026-07-01', 'London', '2026-07-01', 'max', ?, '0xtok',
                '>30°C', 30.0, NULL, 'YES', NULL)
    """, (condition_id,))
    conn.commit()
    return conn


def _make_fsr_event(condition_id: str, direction: str = "buy_no"):
    """Minimal FORECAST_SNAPSHOT_READY event object using the real dataclass."""
    from src.events.opportunity_event import make_opportunity_event
    now_str = "2026-06-09T12:00:00+00:00"
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=condition_id,
        source="test",
        observed_at=now_str,
        available_at=now_str,
        received_at=now_str,
        causal_snapshot_id="snap-001",
        payload={
            "condition_id": condition_id,
            "direction": direction,
            "city": "London",
            "target_date": "2026-07-01",
            "metric": "max",
            "temperature_metric": "max",
            "market_slug": "london-max-2026-07-01",
        },
    )


class TestOpeningInertiaAgeGateIntegration:
    """Lightweight integration tests exercising the gate inside build_event_bound_no_submit_receipt.

    These tests focus only on whether the gate fires/passes correctly.  They rely on
    the function returning early with OPENING_INERTIA_MARKET_TOO_OLD when the market is
    old, and NOT returning that reason when the market is young or age is unknown.

    The function may return for other reasons (missing topology, missing forecast, etc.)
    — we only assert on the specific gate reason.
    """

    def _call_builder(self, event, trade_conn, topology_conn, decision_time):
        """Call the builder; return (rejected: bool, reason: str)."""
        from src.engine.event_reactor_adapter import build_event_bound_no_submit_receipt
        from src.riskguard.risk_level import RiskLevel

        receipt = build_event_bound_no_submit_receipt(
            event=event,
            trade_conn=trade_conn,
            topology_conn=topology_conn,
            forecast_conn=topology_conn,  # same DB for simplicity
            calibration_conn=topology_conn,
            decision_time=decision_time,
            get_current_level=lambda: RiskLevel.GREEN,
        )
        return receipt.submitted, receipt.reason

    def test_old_market_rejected_opening_inertia_gate(self):
        """Market opened 30h ago → OPENING_INERTIA_MARKET_TOO_OLD."""
        import sqlite3
        condition_id = "0xold001"
        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        opened = now - timedelta(hours=30)
        future = now + timedelta(hours=6)  # far future freshness so price-freshness gate passes

        trade_conn = _build_snapshot_db(
            condition_id=condition_id,
            market_start_at=opened.isoformat(),
            captured_at=(now - timedelta(seconds=10)).isoformat(),
            freshness_deadline=future.isoformat(),
        )
        # topology DB: market_events with created_at = old
        topo_conn = sqlite3.connect(":memory:")
        topo_conn.execute(_MARKET_EVENTS_DDL)
        topo_conn.execute("""
            INSERT INTO market_events
                (market_slug, city, target_date, temperature_metric, condition_id, token_id,
                 range_label, range_low, range_high, outcome, created_at)
            VALUES ('london-max-2026-07-01', 'London', '2026-07-01', 'max', ?, '0xtok',
                    '>30°C', 30.0, NULL, 'YES', ?)
        """, (condition_id, opened.isoformat()))
        topo_conn.commit()

        event = _make_fsr_event(condition_id)
        submitted, reason = self._call_builder(event, trade_conn, topo_conn, now)
        assert submitted is False
        assert reason is not None and "OPENING_INERTIA_MARKET_TOO_OLD" in reason, (
            f"Expected OPENING_INERTIA_MARKET_TOO_OLD in reason, got: {reason!r}"
        )

    def test_young_market_not_rejected_by_age_gate(self):
        """Market opened 6h ago → age gate must NOT fire (may fail for other reasons, but NOT age)."""
        import sqlite3
        condition_id = "0xyoung001"
        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        opened = now - timedelta(hours=6)
        future = now + timedelta(hours=6)

        trade_conn = _build_snapshot_db(
            condition_id=condition_id,
            market_start_at=opened.isoformat(),
            captured_at=(now - timedelta(seconds=10)).isoformat(),
            freshness_deadline=future.isoformat(),
        )
        topo_conn = sqlite3.connect(":memory:")
        topo_conn.execute(_MARKET_EVENTS_DDL)
        topo_conn.execute("""
            INSERT INTO market_events
                (market_slug, city, target_date, temperature_metric, condition_id, token_id,
                 range_label, range_low, range_high, outcome, created_at)
            VALUES ('london-max-2026-07-01', 'London', '2026-07-01', 'max', ?, '0xtok',
                    '>30°C', 30.0, NULL, 'YES', ?)
        """, (condition_id, opened.isoformat()))
        topo_conn.commit()

        event = _make_fsr_event(condition_id)
        submitted, reason = self._call_builder(event, trade_conn, topo_conn, now)
        # The age gate must NOT fire; other gates may reject (forecast missing etc.)
        assert reason is None or "OPENING_INERTIA_MARKET_TOO_OLD" not in (reason or ""), (
            f"Age gate must not fire for young market; reason: {reason!r}"
        )

    def test_missing_age_conservative_pass_not_age_rejected(self):
        """No market_start_at, no created_at → conservative pass (age gate must NOT fire)."""
        import sqlite3
        condition_id = "0xnoage001"
        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        future = now + timedelta(hours=6)

        trade_conn = _build_snapshot_db(
            condition_id=condition_id,
            market_start_at=None,  # no age data
            captured_at=(now - timedelta(seconds=10)).isoformat(),
            freshness_deadline=future.isoformat(),
        )
        topo_conn = sqlite3.connect(":memory:")
        topo_conn.execute(_MARKET_EVENTS_DDL)
        topo_conn.execute("""
            INSERT INTO market_events
                (market_slug, city, target_date, temperature_metric, condition_id, token_id,
                 range_label, range_low, range_high, outcome, created_at)
            VALUES ('london-max-2026-07-01', 'London', '2026-07-01', 'max', ?, '0xtok',
                    '>30°C', 30.0, NULL, 'YES', NULL)
        """, (condition_id,))
        topo_conn.commit()

        event = _make_fsr_event(condition_id)
        submitted, reason = self._call_builder(event, trade_conn, topo_conn, now)
        assert reason is None or "OPENING_INERTIA_MARKET_TOO_OLD" not in (reason or ""), (
            f"Conservative pass broken: age gate fired on missing-age market; reason: {reason!r}"
        )

    def test_age_gate_does_not_apply_to_buy_yes(self):
        """buy_yes direction → center_buy strategy, age gate must never fire."""
        import sqlite3
        condition_id = "0xbuyyes001"
        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
        opened = now - timedelta(hours=48)  # very old
        future = now + timedelta(hours=6)

        trade_conn = _build_snapshot_db(
            condition_id=condition_id,
            market_start_at=opened.isoformat(),
            captured_at=(now - timedelta(seconds=10)).isoformat(),
            freshness_deadline=future.isoformat(),
        )
        topo_conn = sqlite3.connect(":memory:")
        topo_conn.execute(_MARKET_EVENTS_DDL)
        topo_conn.execute("""
            INSERT INTO market_events
                (market_slug, city, target_date, temperature_metric, condition_id, token_id,
                 range_label, range_low, range_high, outcome, created_at)
            VALUES ('london-max-2026-07-01', 'London', '2026-07-01', 'max', ?, '0xtok',
                    '>30°C', 30.0, NULL, 'YES', ?)
        """, (condition_id, opened.isoformat()))
        topo_conn.commit()

        event = _make_fsr_event(condition_id, direction="buy_yes")
        submitted, reason = self._call_builder(event, trade_conn, topo_conn, now)
        assert reason is None or "OPENING_INERTIA_MARKET_TOO_OLD" not in (reason or ""), (
            f"Age gate must not fire for buy_yes; reason: {reason!r}"
        )
