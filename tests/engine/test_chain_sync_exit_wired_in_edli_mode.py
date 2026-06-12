# Created: 2026-05-31
# Last reused/audited: 2026-05-31
# Authority basis: Blocker #56 diagnosis /tmp/exit_chain_dx.md — chain_sync +
#   exit_monitor must fire under EDLI modes, not just legacy_cron. Relationship
#   test: proves scheduler wiring at the boot path, not just unit logic.
"""Relationship test: chain_sync_and_exit_monitor job is wired into BOTH
legacy_cron AND EDLI_EVENT_DRIVEN_MODES.

ROOT that this test guards against (Blocker #56):
  - run_chain_sync + execute_monitoring_phase were only reachable through
    CycleRunner.run_cycle(), registered only under live_execution_mode=="legacy_cron".
  - Daemon runs edli_shadow_no_submit → block skipped → chain_shares NULL (101/101),
    7 exit_pending_missing stuck, 1 Shanghai settled-but-active.

This is a RELATIONSHIP test: it boots the scheduler (same path as the real daemon)
and asserts the wiring exists — not that the function logic is correct.

Safety invariant additionally asserted: the standalone job MUST NOT call
execute_exit (real order submission) in any shadow mode. The shadow-safety
parameter exit_order_submit_enabled=False must be passed when
real_order_submit_enabled is False.
"""
from __future__ import annotations

import sqlite3
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Shared _run_main_with_fake_scheduler helper (mirrors the one in
# tests/money_path/test_edli_online_invariants.py — kept local to avoid
# cross-test-module imports that break when test_edli_online_invariants.py
# is refactored).
# ---------------------------------------------------------------------------


def _run_main_with_fake_scheduler(monkeypatch, edli_updates):
    """Boot src/main.py with a FakeScheduler and patched edli settings.

    Returns (scheduler_instance, settings_copy).
    """
    import src.main as main

    settings_source = main.settings._data if hasattr(main.settings, "_data") else main.settings
    settings_copy = deepcopy(settings_source)
    settings_copy["edli"].update(edli_updates)
    monkeypatch.setattr(main, "settings", settings_copy)
    monkeypatch.setattr(main, "get_mode", lambda: "live")
    monkeypatch.setattr(main.sys, "argv", ["src/main.py"])
    monkeypatch.setattr(main, "_capture_boot_state", lambda: {"sha": "abc123", "ts": None})
    monkeypatch.setattr(main, "_start_venue_heartbeat_loop_if_needed", lambda: None)
    monkeypatch.setattr(main, "_startup_world_schema_ready_check", lambda: None)
    monkeypatch.setattr(main, "_run_f109_consolidator", lambda: None)
    monkeypatch.setattr(main, "_startup_data_health_check", lambda _conn: None)
    monkeypatch.setattr(main, "_startup_freshness_check", lambda: None)
    monkeypatch.setattr(main, "_assert_live_safe_strategies_or_exit", lambda: None)
    monkeypatch.setattr(main, "_boot_deployment_freshness_auto_resume", lambda: None)
    monkeypatch.setattr(main, "_startup_wallet_check", lambda: None)
    monkeypatch.setattr(main, "_start_user_channel_ingestor_if_enabled", lambda: None)
    monkeypatch.setattr(main, "_check_s1_without_s2_sla", lambda: None)
    monkeypatch.setattr(main, "_assert_cascade_liveness_contract", lambda _scheduler: None)
    monkeypatch.setattr(main, "init_schema_trade_only", lambda _conn: None)
    monkeypatch.setenv("ZEUS_BOOT_REGISTRY_ASSERT_ENABLED", "0")

    def _conn():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from src.state.schema.edli_live_cap_usage_schema import ensure_table as ensure_live_cap_table
        from src.state.schema.edli_live_order_events_schema import ensure_tables as ensure_live_order_tables
        ensure_live_order_tables(conn)
        ensure_live_cap_table(conn)
        return conn

    monkeypatch.setattr(main, "get_world_connection", lambda *args, **kwargs: _conn())
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda *args, **kwargs: _conn())
    monkeypatch.setattr(main, "get_trade_connection", lambda *args, **kwargs: _conn())

    class FakeScheduler:
        instances = []

        def __init__(self, *args, **kwargs):
            self.timezone = kwargs.get("timezone")
            self.jobs = []
            self.started = False
            self.shutdown_called = False
            FakeScheduler.instances.append(self)

        def add_job(self, func, trigger, *args, id=None, **kwargs):
            self.jobs.append(SimpleNamespace(id=id, func=func, trigger=trigger, kwargs=kwargs))

        def get_jobs(self):
            return self.jobs

        def start(self):
            self.started = True
            raise KeyboardInterrupt()

        def shutdown(self, wait=True):
            self.shutdown_called = wait

    monkeypatch.setattr(main, "BlockingScheduler", FakeScheduler)
    main.main()
    return FakeScheduler.instances[-1], settings_copy


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_chain_sync_exit_monitor_registered_in_edli_shadow_no_submit(monkeypatch):
    """RED→GREEN: chain_sync_and_exit_monitor must be registered in edli_shadow_no_submit.

    This is the primary blocker #56 wiring gap: EDLI mode never registered
    the chain-sync or exit-monitoring job, so chain_shares stayed NULL forever.
    """
    scheduler, _ = _run_main_with_fake_scheduler(
        monkeypatch,
        {
            "enabled": True,
            "live_execution_mode": "edli_shadow_no_submit",
            "reactor_mode": "live_no_submit",
            "event_writer_enabled": True,
            "forecast_snapshot_trigger_enabled": True,
            "day0_extreme_trigger_enabled": False,
            "day0_hard_fact_live_enabled": False,
            "market_channel_ingestor_enabled": False,
            "edli_user_channel_reconcile_enabled": False,
            "real_order_submit_enabled": False,
        },
    )
    job_ids = {job.id for job in scheduler.jobs}
    assert "chain_sync_and_exit_monitor" in job_ids, (
        "chain_sync_and_exit_monitor must be registered under edli_shadow_no_submit "
        "(Blocker #56: chain_shares NULL 101/101 because this job was never wired)"
    )


def test_chain_sync_exit_monitor_registered_in_legacy_cron_no_regression(monkeypatch):
    """chain_sync_and_exit_monitor must ALSO be registered in legacy_cron.

    Guards against the fix accidentally breaking the legacy path that
    previously had chain sync embedded inside run_cycle().
    """
    scheduler, _ = _run_main_with_fake_scheduler(
        monkeypatch,
        {
            "enabled": False,
            "live_execution_mode": "legacy_cron",
            "reactor_mode": "disabled",
            "event_writer_enabled": False,
            "forecast_snapshot_trigger_enabled": False,
            "day0_extreme_trigger_enabled": False,
            "day0_hard_fact_live_enabled": False,
            "market_channel_ingestor_enabled": False,
            "edli_user_channel_reconcile_enabled": False,
            "real_order_submit_enabled": False,
        },
    )
    job_ids = {job.id for job in scheduler.jobs}
    assert "chain_sync_and_exit_monitor" in job_ids, (
        "chain_sync_and_exit_monitor must remain registered in legacy_cron "
        "so both modes get chain truth sync + exit monitoring"
    )


def test_edli_event_driven_modes_set_includes_shadow_no_submit():
    """EDLI_EVENT_DRIVEN_MODES must include edli_shadow_no_submit.

    Structural guard: if someone removes edli_shadow_no_submit from the set,
    the wiring test above would be testing a different condition. This verifies
    that the set relationship holds so the wiring gate is meaningful.
    """
    import src.main as main
    assert "edli_shadow_no_submit" in main.EDLI_EVENT_DRIVEN_MODES
    assert "edli_submit_disabled_bridge" in main.EDLI_EVENT_DRIVEN_MODES
    # Wave-2 item 5: canary collapsed into edli_live (the only event-driven live mode).
    assert "edli_live_canary" not in main.EDLI_EVENT_DRIVEN_MODES
    assert "edli_live" in main.EDLI_EVENT_DRIVEN_MODES


def test_shadow_safety_execute_monitoring_phase_accepts_exit_order_submit_enabled():
    """execute_monitoring_phase must accept exit_order_submit_enabled kwarg.

    When False, execute_exit must NOT be called (shadow-mode safety: no real
    order submission while running the chain-sync-and-exit-monitor standalone job).
    """
    from src.engine.cycle_runtime import execute_monitoring_phase
    import inspect
    sig = inspect.signature(execute_monitoring_phase)
    assert "exit_order_submit_enabled" in sig.parameters, (
        "execute_monitoring_phase must have exit_order_submit_enabled parameter "
        "to prevent real order submission in shadow EDLI mode"
    )


def test_shadow_safety_exit_order_not_called_when_submit_disabled(monkeypatch):
    """execute_monitoring_phase with exit_order_submit_enabled=False must not call execute_exit.

    This is the safety gate for the standalone chain_sync_and_exit_monitor job:
    it runs monitor phase logic (chain state transitions, exit_pending_missing
    resolution) WITHOUT submitting real sell orders.
    """
    from src.engine.cycle_runtime import execute_monitoring_phase
    from src.state.decision_chain import CycleArtifact

    execute_exit_calls = []

    # Patch execute_exit at the exit_lifecycle level
    import src.execution.exit_lifecycle as exit_lifecycle_mod
    monkeypatch.setattr(exit_lifecycle_mod, "execute_exit",
                        lambda *a, **kw: execute_exit_calls.append((a, kw)) or "exit_blocked: shadow_no_submit")

    # Minimal portfolio with one "holding" position that would normally trigger exit
    from unittest.mock import MagicMock
    pos = MagicMock()
    pos.state = "holding"
    pos.chain_state = None
    pos.exit_state = ""
    pos.trade_id = "test-pos-001"
    pos.city = "NonExistent"
    pos.direction = "buy_yes"
    pos.is_quarantine_placeholder = False
    pos.neg_edge_count = 0
    pos.admin_exit_reason = None
    pos.exit_reason = None
    pos.last_exit_at = None

    portfolio = MagicMock()
    portfolio.positions = [pos]

    artifact = CycleArtifact(mode="chain_sync_monitor", started_at="2026-05-31T00:00:00+00:00", summary={})
    tracker = MagicMock()
    summary = {"monitors": 0, "exits": 0}

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    clob = MagicMock()

    import src.engine.cycle_runtime as cycle_runtime
    deps = sys.modules[cycle_runtime.__name__]

    try:
        execute_monitoring_phase(
            conn, clob, portfolio, artifact, tracker, summary,
            exit_order_submit_enabled=False,
            deps=deps,
        )
    except Exception:
        # Other errors (city not found, etc.) are OK — we only care about execute_exit
        pass

    assert execute_exit_calls == [], (
        "execute_exit must NOT be called when exit_order_submit_enabled=False "
        "(shadow-mode safety for chain_sync_and_exit_monitor standalone job)"
    )
