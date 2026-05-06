"""P2 u2014 Auto-Pause Entries on Exception.

Tests that any unhandled exception in the entry discovery path auto-pauses
entries (with a machine-readable reason_code), emits the alert, and leaves
monitoring/exit/settlement paths unaffected.

Closeout criteria covered:
  #2 u2014 auto-pause fires on ValueError injected into _execute_discovery_phase
  #3 u2014 reason_code recorded in control state
  #4 u2014 alert_auto_pause called exactly once
  #5 u2014 post-entry paths (save, close, summary completion) still run
  #6 u2014 clearing entries_paused via resume semantics
  #7 u2014 pause_entries() persists to DB via upsert_control_override
  #8 u2014 DB persistence failure does not break in-memory pause
"""

import sqlite3
import pytest
import src.control.control_plane as cp
import src.control.heartbeat_supervisor as heartbeat_supervisor
import src.control.ws_gap_guard as ws_gap_guard
import src.engine.cycle_runner as cr
import src.state.chain_reconciliation as cr_chain
import src.risk_allocator as risk_allocator
import src.runtime.posture as runtime_posture
from src.engine.discovery_mode import DiscoveryMode
from src.riskguard.risk_level import RiskLevel
from src.state.db import apply_architecture_kernel_schema, get_connection
from src.state.portfolio import PortfolioState
from src.state.strategy_tracker import StrategyTracker


class _NullClob:
    def __init__(self):
        pass


def _patch_cycle(monkeypatch):
    """Patch all external dependencies in run_cycle, leaving the entry path
    un-patched so individual tests can inject faults there."""
    monkeypatch.setattr(cr, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cr, "get_connection", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(cr, "load_portfolio", lambda: PortfolioState(bankroll=211.37))
    monkeypatch.setattr(cr, "save_portfolio", lambda _: None)
    monkeypatch.setattr(cr, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cr, "save_tracker", lambda _: None)
    monkeypatch.setattr(cr, "PolymarketClient", _NullClob)
    monkeypatch.setattr(cr, "is_entries_paused", lambda: False)
    monkeypatch.setattr(cr, "get_force_exit_review", lambda: False)
    monkeypatch.setattr(cr, "portfolio_heat_for_bankroll", lambda p, b: 0.0)
    monkeypatch.setattr(cr.cutover_guard, "summary", lambda: {"state": "LIVE_ENABLED", "entry": {"allow_submit": True}})
    monkeypatch.setattr(heartbeat_supervisor, "summary", lambda: {"health": "OK", "entry": {"allow_submit": True}})
    monkeypatch.setattr(
        ws_gap_guard,
        "summary",
        lambda: {"subscription_state": "SUBSCRIBED", "m5_reconcile_required": False, "entry": {"allow_submit": True}},
    )
    monkeypatch.setattr(runtime_posture, "read_runtime_posture", lambda: "NORMAL")
    monkeypatch.setattr(
        risk_allocator,
        "refresh_global_allocator",
        lambda *a, **kw: {"configured": True, "entry": {"allow_submit": True}},
    )
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

    @pytest.mark.skip(reason="auto-pause streak retired 2026-05-04")
    def test_entry_exception_pauses_entries(self, monkeypatch, tmp_path):
        """Criterion #2/#3/#4: a same-reason ValueError streak in
        _execute_discovery_phase eventually triggers auto-pause with a
        machine-readable reason_code and fires the alert.

        Live-blockers 2026-05-01: pause now requires N=3 consecutive
        failures within a 5-min window. A single transient failure logs
        but does NOT pause entries. This test injects three cycles back-
        to-back to exercise the escalation; the streak file lives in a
        per-test tmp_path so we don't collide with neighbour tests.
        """
        _patch_cycle(monkeypatch)
        # Per-test streak file isolation.
        from src.control import auto_pause_streak as streak
        monkeypatch.setattr(streak, "_streak_path", lambda: tmp_path / "streak.json")
        # Force production pause_entries to no-op the DB write so this test
        # stays focused on the cycle-runner-level contract (reason_code +
        # alert + summary). The DB persistence is covered by
        # test_pause_entries_persists_to_db below.
        original_pause = cp.pause_entries

        def _pause_in_memory_only(reason_code, **kwargs):
            cp._control_state["entries_paused"] = True
            cp._control_state["entries_pause_source"] = "auto_exception"
            cp._control_state["entries_pause_reason"] = reason_code
            cp.alert_auto_pause(reason_code)

        monkeypatch.setattr(cp, "pause_entries", _pause_in_memory_only)
        # Re-bind the cycle_runner's bound name to the patched pause_entries.
        monkeypatch.setattr(cr, "pause_entries", _pause_in_memory_only)

        def _raise_discovery(*a, **kw):
            raise ValueError("test_boom")

        monkeypatch.setattr(cr, "_execute_discovery_phase", _raise_discovery)

        alert_calls = []
        monkeypatch.setattr(cp, "alert_auto_pause", lambda r: alert_calls.append(r))

        # Cycle 1 — streak=1 — must NOT pause.
        summary1 = cr.run_cycle(DiscoveryMode.OPENING_HUNT)
        assert cp.is_entries_paused() is False, "single failure must not pause"
        assert summary1.get("entries_paused") is not True
        assert summary1.get("auto_pause_streak") == 1
        assert alert_calls == []

        # Cycle 2 — streak=2 — still must NOT pause.
        summary2 = cr.run_cycle(DiscoveryMode.OPENING_HUNT)
        assert cp.is_entries_paused() is False, "two failures must not pause"
        assert summary2.get("auto_pause_streak") == 2
        assert alert_calls == []

        # Cycle 3 — streak=3 — pause fires.
        summary = cr.run_cycle(DiscoveryMode.OPENING_HUNT)

        # Criterion #2: entries_paused flag set in control state
        assert cp.is_entries_paused() is True

        # Criterion #3: reason_code is machine-readable
        assert cp.get_entries_pause_source() == "auto_exception"
        assert cp.get_entries_pause_reason() == "auto_pause:ValueError"
        assert cp._control_state.get("entries_pause_reason") == "auto_pause:ValueError"

        # Criterion #4: alert called exactly once with the reason code
        assert alert_calls == ["auto_pause:ValueError"]

        # Criterion #5 (partial): cycle completed — summary has completed_at
        assert summary.get("completed_at") is not None

        # summary also carries the pause flag
        assert summary.get("entries_paused") is True
        assert summary.get("auto_pause_streak") == 3

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

    def test_pause_entries_persists_to_db(self, monkeypatch):
        """Criterion #7: pause_entries() calls upsert_control_override with the
        correct override_id and value so the pause survives a daemon restart."""
        monkeypatch.setattr(cp, "alert_auto_pause", lambda r: None)

        import sqlite3 as _sqlite3
        mem_conn = _sqlite3.connect(":memory:")
        monkeypatch.setattr(cp, "get_world_connection", lambda: mem_conn)

        upsert_calls = []

        def _capture_upsert(conn, *, override_id, target_type, target_key,
                            action_type, value, issued_by, issued_at, reason,
                            precedence, **kw):
            upsert_calls.append({
                "override_id": override_id,
                "value": value,
                "issued_by": issued_by,
                "reason": reason,
            })
            return {"status": "written"}

        monkeypatch.setattr(cp, "upsert_control_override", _capture_upsert)

        cp.pause_entries("auto_pause:ValueError")

        assert cp.is_entries_paused() is True
        assert len(upsert_calls) == 1
        call = upsert_calls[0]
        assert call["override_id"] == "control_plane:global:entries_paused"
        assert call["value"] == "true"
        assert call["issued_by"] == "system_auto_pause"
        assert call["reason"] == "auto_pause:ValueError"

    def test_pause_entries_db_failure_does_not_break_in_memory_pause(self, monkeypatch):
        """Criterion #8: If DB persistence raises, in-memory entries_paused is
        still True — the auto-pause is not lost."""
        monkeypatch.setattr(cp, "alert_auto_pause", lambda r: None)
        monkeypatch.setattr(
            cp, "get_world_connection",
            lambda: (_ for _ in ()).throw(RuntimeError("db_unavailable")),
        )

        cp.pause_entries("auto_pause:RuntimeError")

        # In-memory pause must survive the DB failure
        assert cp.is_entries_paused() is True
        assert cp.get_entries_pause_source() == "auto_exception"
        assert cp.get_entries_pause_reason() == "auto_pause:RuntimeError"
        assert cp._control_state.get("entries_pause_reason") == "auto_pause:RuntimeError"

    def test_auto_pause_reason_hydrates_from_active_control_override_and_clears_on_resume(self, monkeypatch, tmp_path):
        control_path = tmp_path / "control_plane.json"
        db_path = tmp_path / "zeus.db"
        conn = get_connection(db_path)
        apply_architecture_kernel_schema(conn)
        conn.close()
        monkeypatch.setattr(cp, "CONTROL_PATH", control_path)
        monkeypatch.setattr(cp, "state_path", lambda name: tmp_path / name)
        monkeypatch.setattr(cp, "get_world_connection", lambda: get_connection(db_path))
        monkeypatch.setattr(cp, "alert_auto_pause", lambda r: None)

        control_path.write_text('{"commands": [], "acks": []}')
        cp._control_state.clear()
        cp.pause_entries("auto_pause:ValueError")

        assert cp.is_entries_paused() is True
        assert cp.get_entries_pause_source() == "auto_exception"
        assert cp.get_entries_pause_reason() == "auto_pause:ValueError"

        cp._control_state.clear()
        cp.refresh_control_state()

        assert cp.is_entries_paused() is True
        assert cp.get_entries_pause_source() == "auto_exception"
        assert cp.get_entries_pause_reason() == "auto_pause:ValueError"

        control_path.write_text('{"commands": [{"command": "resume"}], "acks": []}')
        assert cp.process_commands() == ["resume"]

        assert cp.is_entries_paused() is False
        assert cp.get_entries_pause_source() is None
        assert cp.get_entries_pause_reason() is None
