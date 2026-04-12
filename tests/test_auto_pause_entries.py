"""P2 — Auto-Pause Entries on Exception.

Tests that any unhandled exception in the entry discovery path auto-pauses
entries (with a machine-readable reason_code), emits the alert, and leaves
monitoring/exit/settlement paths unaffected.

Closeout criteria covered:
  #2 — auto-pause fires on ValueError injected into _execute_discovery_phase
  #3 — reason_code recorded in control state
  #4 — alert_auto_pause called exactly once
  #5 — post-entry paths (save, close, summary completion) still run
  #6 — clearing entries_paused via resume semantics
"""

import sqlite3
import src.control.control_plane as cp
import src.engine.cycle_runner as cr
import src.state.chain_reconciliation as cr_chain
from src.engine.discovery_mode import DiscoveryMode
from src.riskguard.risk_level import RiskLevel
from src.state.portfolio import PortfolioState
from src.state.strategy_tracker import StrategyTracker


class _NullClob:
    def __init__(self, paper_mode=True):
        self.paper_mode = paper_mode


def _patch_cycle(monkeypatch):
    """Patch all external dependencies in run_cycle, leaving the entry path
    un-patched so individual tests can inject faults there."""
    monkeypatch.setattr(cr, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cr, "get_connection", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(cr, "load_portfolio", lambda: PortfolioState(bankroll=150.0))
    monkeypatch.setattr(cr, "save_portfolio", lambda _: None)
    monkeypatch.setattr(cr, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cr, "save_tracker", lambda _: None)
    monkeypatch.setattr(cr, "PolymarketClient", _NullClob)
    monkeypatch.setattr(cr, "is_entries_paused", lambda: False)
    monkeypatch.setattr(cr, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cr, "portfolio_heat_for_bankroll", lambda p, b: 0.0)
    monkeypatch.setattr(cp, "process_commands", lambda: [])
    monkeypatch.setattr(
        cr, "_reconcile_pending_positions",
        lambda *a, **kw: {"dirty": False, "tracker_dirty": False, "entered": 0, "voided": 0},
    )
    monkeypatch.setattr(cr, "_run_chain_sync", lambda *a, **kw: ({}, True))
    monkeypatch.setattr(cr, "_cleanup_orphan_open_orders", lambda *a, **kw: 0)
    monkeypatch.setattr(cr, "_entry_bankroll_for_cycle", lambda *a, **kw: (100.0, {}))
    monkeypatch.setattr(cr, "store_artifact", lambda *a, **kw: None)
    monkeypatch.setattr(cr_chain, "check_quarantine_timeouts", lambda p: [])

    monitor_calls = []

    def _monitoring(*a, **kw):
        monitor_calls.append(True)
        return False, False

    monkeypatch.setattr(cr, "_execute_monitoring_phase", _monitoring)
    return monitor_calls


class TestAutoRauseEntries:
    def setup_method(self):
        # Reset control state before each test.
        cp._control_state.clear()
        cp._control_state["entries_paused"] = False

    def teardown_method(self):
        # Restore clean state after each test so downstream test files are not
        # polluted by entries_paused=True left behind by auto-pause assertions.
        cp._control_state.clear()

    def test_entry_exception_pauses_entries(self, monkeypatch):
        """Criterion #2/#3/#4: ValueError in _execute_discovery_phase triggers
        auto-pause with machine-readable reason_code and fires the alert."""
        _patch_cycle(monkeypatch)

        def _raise_discovery(*a, **kw):
            raise ValueError("test_boom")

        monkeypatch.setattr(cr, "_execute_discovery_phase", _raise_discovery)

        alert_calls = []
        monkeypatch.setattr(cp, "alert_auto_pause", lambda r: alert_calls.append(r))

        summary = cr.run_cycle(DiscoveryMode.OPENING_HUNT)

        # Criterion #2: entries_paused flag set in control state
        assert cp.is_entries_paused() is True

        # Criterion #3: reason_code is machine-readable
        assert cp._control_state.get("entries_pause_reason") == "auto_pause:ValueError"

        # Criterion #4: alert called exactly once with the reason code
        assert alert_calls == ["auto_pause:ValueError"]

        # Criterion #5 (partial): cycle completed — summary has completed_at
        assert summary.get("completed_at") is not None

        # summary also carries the pause flag
        assert summary.get("entries_paused") is True

    def test_resume_clears_pause(self, monkeypatch):
        """Criterion #6: After auto-pause, clearing entries_paused (resume semantics)
        returns is_entries_paused() to False."""
        monkeypatch.setattr(cp, "alert_auto_pause", lambda r: None)

        # pause_entries must exist and set the flag
        cp.pause_entries("auto_pause:ValueError")
        assert cp.is_entries_paused() is True

        # Resume semantics: operator clears the flag
        cp._control_state["entries_paused"] = False
        assert cp.is_entries_paused() is False

    def test_exit_monitor_paths_unaffected(self, monkeypatch):
        """Criterion #5: Monitoring phase still runs and cycle completes
        (completed_at populated) even when _execute_discovery_phase raises."""
        monitor_calls = _patch_cycle(monkeypatch)

        def _raise_discovery(*a, **kw):
            raise ValueError("test_boom")

        monkeypatch.setattr(cr, "_execute_discovery_phase", _raise_discovery)
        monkeypatch.setattr(cp, "alert_auto_pause", lambda r: None)

        summary = cr.run_cycle(DiscoveryMode.OPENING_HUNT)

        # Monitoring phase ran before the entry exception
        assert monitor_calls, "Monitoring phase must have run before entry exception"

        # Cycle completed — post-entry bookkeeping executed
        assert summary.get("completed_at") is not None
