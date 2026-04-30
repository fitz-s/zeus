"""Tests for Slice N — Runtime Irreversible Safeguards (Bugs #63, #67)."""
# Created: 2026-04-17
# Last reused/audited: 2026-04-30
# Authority basis: Slice N runtime irreversible safeguards; 2026-04-30 durable venue-command cancel truth remediation.

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Bug #63: Orphan order cancel triple-confirmation ──────────────────

def _make_deps(logger_name="test"):
    return SimpleNamespace(logger=logging.getLogger(logger_name))


def _make_portfolio(order_ids=None, exit_order_ids=None):
    positions = []
    for oid in (order_ids or []):
        pos = SimpleNamespace(order_id=oid, last_exit_order_id=None)
        positions.append(pos)
    for oid in (exit_order_ids or []):
        pos = SimpleNamespace(order_id=None, last_exit_order_id=oid)
        positions.append(pos)
    return SimpleNamespace(positions=positions)


def _make_clob(open_orders):
    clob = MagicMock()
    clob.get_open_orders.return_value = open_orders
    clob.cancel_order.return_value = {"cancelled": True}
    return clob


class TestOrphanOrderCleanup:
    def test_tracked_order_not_cancelled(self):
        """Orders in local tracking should never be cancelled."""
        from src.engine.cycle_runtime import cleanup_orphan_open_orders
        portfolio = _make_portfolio(order_ids=["O-001"])
        clob = _make_clob([{"id": "O-001"}])
        deps = _make_deps()
        cancelled = cleanup_orphan_open_orders(portfolio, clob, deps=deps)
        assert cancelled == 0
        clob.cancel_order.assert_not_called()

    def test_orphan_without_conn_blocks_cancel(self):
        """Without durable command truth, cleanup must fail closed."""
        from src.engine.cycle_runtime import cleanup_orphan_open_orders
        portfolio = _make_portfolio(order_ids=["O-001"])
        clob = _make_clob([{"id": "O-001"}, {"id": "O-ORPHAN"}])
        deps = _make_deps()
        cancelled = cleanup_orphan_open_orders(portfolio, clob, deps=deps, conn=None)
        assert cancelled == 0
        clob.cancel_order.assert_not_called()

    def test_orphan_in_recent_trade_decisions_quarantined(self, caplog):
        """Orphan order found in trade_decisions within 2h should be quarantined, not cancelled."""
        from src.engine.cycle_runtime import cleanup_orphan_open_orders

        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE trade_decisions (
                id INTEGER PRIMARY KEY,
                order_id TEXT,
                order_posted_at TEXT
            )
        """)
        recent_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        conn.execute("INSERT INTO trade_decisions (order_id, order_posted_at) VALUES (?, ?)",
                      ("O-RECENT", recent_time))
        conn.commit()

        portfolio = _make_portfolio(order_ids=["O-001"])
        clob = _make_clob([{"id": "O-001"}, {"id": "O-RECENT"}, {"id": "O-TRUE-ORPHAN"}])
        deps = _make_deps()

        with caplog.at_level(logging.WARNING):
            cancelled = cleanup_orphan_open_orders(portfolio, clob, deps=deps, conn=conn)

        # O-RECENT is quarantined. O-TRUE-ORPHAN also fails closed because this
        # legacy fixture has no venue_commands journal to prove durable command truth.
        assert cancelled == 0
        clob.cancel_order.assert_not_called()
        quarantine_logs = [r for r in caplog.records if "quarantining" in r.message]
        assert len(quarantine_logs) == 1
        assert "O-RECENT" in quarantine_logs[0].message
        blocked_logs = [r for r in caplog.records if "venue command journal unavailable" in r.message]
        assert len(blocked_logs) == 1
        assert "O-TRUE-ORPHAN" in blocked_logs[0].message

    def test_orphan_old_trade_decisions_blocks_without_command_journal(self):
        """Old trade_decisions rows are not command truth; no journal means no cancel."""
        from src.engine.cycle_runtime import cleanup_orphan_open_orders

        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE trade_decisions (
                id INTEGER PRIMARY KEY,
                order_id TEXT,
                order_posted_at TEXT
            )
        """)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        conn.execute("INSERT INTO trade_decisions (order_id, order_posted_at) VALUES (?, ?)",
                      ("O-OLD", old_time))
        conn.commit()

        portfolio = _make_portfolio()
        clob = _make_clob([{"id": "O-OLD"}])
        deps = _make_deps()

        cancelled = cleanup_orphan_open_orders(portfolio, clob, deps=deps, conn=conn)
        assert cancelled == 0
        clob.cancel_order.assert_not_called()


# ── Bug #67: ENS snapshot after Day0 gate ────────────────────────────

class TestSnapshotAfterGate:
    def test_snapshot_store_not_called_on_day0_rejection(self):
        """When Day0 temporal context is unavailable, snapshot must NOT be stored."""
        from pathlib import Path
        import inspect

        evaluator_path = Path(__file__).parent.parent / "src" / "engine" / "evaluator.py"
        source = evaluator_path.read_text()

        # Find the line of _store_ens_snapshot call and the Day0 rejection returns
        lines = source.splitlines()
        snapshot_line = None
        first_day0_rejection = None

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "_store_ens_snapshot" in stripped and "def " not in stripped:
                snapshot_line = i
            if first_day0_rejection is None and "Solar/DST context unavailable" in stripped:
                first_day0_rejection = i

        assert snapshot_line is not None, "Could not find _store_ens_snapshot call"
        assert first_day0_rejection is not None, "Could not find Day0 rejection"
        # Snapshot store MUST come AFTER Day0 rejection gates
        assert snapshot_line > first_day0_rejection, (
            f"_store_ens_snapshot (line {snapshot_line}) must come AFTER "
            f"Day0 rejection gates (line {first_day0_rejection})"
        )

    def test_snapshot_store_after_all_gates(self):
        """Snapshot store must come after both Day0 rejection points."""
        from pathlib import Path

        evaluator_path = Path(__file__).parent.parent / "src" / "engine" / "evaluator.py"
        source = evaluator_path.read_text()
        lines = source.splitlines()

        snapshot_line = None
        day0_forecast_rejection = None

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "_store_ens_snapshot" in stripped and "def " not in stripped:
                snapshot_line = i
            if "No Day0 forecast hours remain" in stripped:
                day0_forecast_rejection = i

        assert snapshot_line is not None
        assert day0_forecast_rejection is not None
        assert snapshot_line > day0_forecast_rejection, (
            f"_store_ens_snapshot (line {snapshot_line}) must come AFTER "
            f"forecast-hours rejection (line {day0_forecast_rejection})"
        )
