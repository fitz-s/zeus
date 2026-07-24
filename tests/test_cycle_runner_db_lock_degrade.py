# Created: 2026-05-05
# Last reused or audited: 2026-06-17
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T2G/phase.json
#   + live EDLI monitor/write-lock recovery 2026-06-17.
"""Tests for T2G: cycle_runner DB-lock graceful degrade.

Invariants asserted:
  T2G-CYCLE-RUNNER-DEGRADES-NOT-CRASHES:
    connect_or_degrade returning None causes run_cycle() to return a
    'degraded' marker without raising sqlite3.OperationalError.
  T2G-CONNECT-OR-DEGRADE-COUNTER-WIRED:
    db_write_lock_timeout_total counter increments exactly once per
    degraded cycle via the typed sink (T2F).
  T2G-CYCLE-RUNNER-PROPAGATES-NON-LOCK (implicit via connect_or_degrade
    primitive test coverage; reconfirmed here at the cycle-runner boundary):
    any non-lock OperationalError still propagates through connect_or_degrade.
  T2G-LIVE-CYCLE-INVARIANT-PRESERVED:
    test_cycle_runner_smoke.py (separate file) is not broken — happy-path
    semantics unchanged.

Tests:
  test_lock_degrade_returns_summary_not_raises
      — get_connection returning None → summary contains db_write_lock_degraded=True,
        run_cycle() does NOT raise.
  test_db_lock_increments_typed_counter_via_sink
      — db_write_lock_timeout_total increments by 1 per simulated lock via
        connect_or_degrade path (read-back from src.observability.counters.read).
  test_non_lock_operational_error_propagates
      — OperationalError("no such table: foo") propagates through
        connect_or_degrade to the caller (not silently swallowed).
"""
from __future__ import annotations

import sqlite3
import inspect
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_run_cycle_deps(monkeypatch):
    """Patch the heavy IO deps of run_cycle so it can be called in tests.

    We only need the cycle to reach the conn = get_connection() site and
    past it (or short-circuit on None).  All venue + strategy IO is stubbed.
    """
    import src.engine.cycle_runner as cr

    # Freshness gate — skip the stale-sources short-circuit.
    monkeypatch.setattr(cr, "evaluate_freshness_mid_run", lambda _: None)

    # Risk level — return GREEN so no early exits.
    from src.riskguard.risk_level import RiskLevel
    monkeypatch.setattr(cr, "get_current_level", lambda: RiskLevel.GREEN)


# ---------------------------------------------------------------------------
# T2G-CYCLE-RUNNER-DEGRADES-NOT-CRASHES
# ---------------------------------------------------------------------------

def test_lock_degrade_returns_summary_not_raises(monkeypatch):
    """get_connection() returning None → run_cycle returns summary with
    db_write_lock_degraded=True; no sqlite3.OperationalError raised.

    T2G-CYCLE-RUNNER-DEGRADES-NOT-CRASHES assertion (a): None-conn path
    causes graceful return, not daemon crash.
    """
    import src.engine.cycle_runner as cr
    from src.engine.discovery_mode import DiscoveryMode

    _make_minimal_run_cycle_deps(monkeypatch)

    # Patch get_connection to simulate lock-degrade (returns None).
    monkeypatch.setattr(cr, "get_connection", lambda: None)

    # run_cycle must NOT raise.
    result = cr.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert result.get("db_write_lock_degraded") is True, (
        f"Expected db_write_lock_degraded=True in summary, got: {result}"
    )
    assert result.get("skipped") is True, (
        f"Expected skipped=True in summary on lock-degrade, got: {result}"
    )
    assert result.get("skip_reason") == "db_write_lock_degraded", (
        f"Expected skip_reason='db_write_lock_degraded', got: {result.get('skip_reason')}"
    )


def test_cycle_success_export_uses_lightweight_status_pulse():
    """Cycle success must not run full derived status_summary inside the lock."""

    import src.engine.cycle_runner as cr

    source = inspect.getsource(cr.run_cycle)
    assert "write_cycle_pulse(summary)" in source
    assert "write_status(summary)" not in source


def test_monitoring_phase_releases_write_lock_between_positions():
    """Held-position monitoring must not monopolize the trade WAL for the full pass."""

    import src.engine.cycle_runtime as cycle_runtime

    source = inspect.getsource(cycle_runtime.execute_monitoring_phase)
    assert "_release_monitor_write_lock_boundary(" in source
    assert 'boundary="exit_preflight"' in source
    assert 'boundary="day0_window_entered"' in source
    assert 'boundary="position_monitor"' in source


def test_exit_lifecycle_commits_before_live_venue_io():
    """Exit writes must be durable before live cancel/sell HTTP, not held across it."""

    import src.execution.exit_lifecycle as exit_lifecycle

    source = inspect.getsource(exit_lifecycle._execute_live_exit)
    helper_source = inspect.getsource(exit_lifecycle._record_exit_intent_before_execution_gates)
    intent_index = source.index("_record_exit_intent_before_execution_gates")
    collateral_index = source.index('_commit_before_exit_venue_io(conn, stage="collateral_refresh")')
    cancel_index = source.index("position.last_exit_order_id")
    assert '_commit_before_exit_venue_io(conn, stage="collateral_refresh")' in source
    assert '_commit_before_exit_venue_io(conn, stage="exit_intent")' in helper_source
    assert intent_index < collateral_index < cancel_index


def test_heartbeat_does_not_block_on_live_health():
    """Heartbeat freshness must not depend on any DB-derived status scan."""

    import src.main as main

    source = inspect.getsource(main._write_heartbeat)
    assert "write_cycle_pulse" not in source
    assert "compute_composite_live_health" not in source


def test_live_health_composite_runs_as_separate_scheduler_job():
    """Composite live-health remains scheduled but cannot hold the heartbeat job open."""

    import src.main as main

    main_source = inspect.getsource(main.main)
    health_job_source = inspect.getsource(main._live_health_composite_cycle)
    assert "write_cycle_pulse" in health_job_source
    assert "compute_composite_live_health" in health_job_source
    assert "_live_health_composite_cycle" in main_source
    assert 'id="live_health_composite"' in main_source
    assert 'executor="observability"' in main_source


def test_heartbeat_jobs_have_a_dedicated_executor():
    """Business jobs cannot queue process or venue-heartbeat supervision."""

    import src.main as main

    source = inspect.getsource(main.main)
    assert '"heartbeat": _APThreadPoolExecutor(2)' in source
    assert source.count('executor="heartbeat"') == 2


def test_live_health_composite_yields_to_active_entry_reactor(monkeypatch):
    """Historical health scans must not contend with the live decision lane."""

    import src.control.live_health as live_health
    import src.main as main

    monkeypatch.setattr(
        main,
        "_defer_for_active_entry_reactor",
        lambda job_name: job_name == "live_health_composite",
    )
    monkeypatch.setattr(
        live_health,
        "compute_composite_live_health",
        lambda: pytest.fail("health DB scans must yield to the active reactor"),
    )

    assert main._live_health_composite_cycle.__wrapped__() is None


def test_live_health_composite_refreshes_status_before_evaluation(monkeypatch):
    """The isolated observability lane evaluates the status cut it just wrote."""

    import src.control.live_health as live_health
    import src.main as main
    import src.observability.status_summary as status_summary

    calls = []
    monkeypatch.setattr(main, "_defer_for_active_entry_reactor", lambda _name: False)
    monkeypatch.setattr(
        status_summary,
        "write_cycle_pulse",
        lambda payload: calls.append(("pulse", payload)),
    )
    monkeypatch.setattr(
        live_health,
        "compute_composite_live_health",
        lambda: calls.append(("health", None)),
    )

    assert main._live_health_composite_cycle.__wrapped__() is None
    assert calls == [
        ("pulse", {"mode": "heartbeat_pulse", "heartbeat": True}),
        ("health", None),
    ]


def test_scheduler_job_marks_running_before_completion(monkeypatch):
    """Long scheduler jobs must be visible as RUNNING before success/failure."""

    import src.main as main

    calls = []

    def _record(job_name, **kwargs):
        calls.append((job_name, kwargs))

    monkeypatch.setattr(main, "_write_scheduler_health", _record)

    @main._scheduler_job("probe_job")
    def _probe():
        calls.append(("body", {}))
        return "done"

    assert _probe() == "done"
    assert calls[0] == ("probe_job", {"failed": False, "started": True})
    assert calls[1] == ("body", {})
    assert calls[2] == ("probe_job", {"failed": False})


def test_scheduler_max_instance_skip_listener_records_skip(monkeypatch):
    """APScheduler max-instance skips must surface as scheduler health skips."""

    import src.main as main

    calls = []

    def _record(job_name, **kwargs):
        calls.append((job_name, kwargs))

    class Event:
        job_id = "edli_event_reactor"

    monkeypatch.setattr(main, "_write_scheduler_health", _record)

    main._scheduler_max_instance_skip_listener(Event())

    assert calls == [
        (
            "edli_event_reactor",
            {
                "failed": False,
                "skipped": True,
                "skip_reason": "max_instances_reached",
            },
        )
    ]


# ---------------------------------------------------------------------------
# T2G-CONNECT-OR-DEGRADE-COUNTER-WIRED
# ---------------------------------------------------------------------------

def test_db_lock_increments_typed_counter_via_sink(tmp_path, monkeypatch):
    """Simulating 'database is locked' via connect_or_degrade increments
    db_write_lock_timeout_total in the typed sink exactly once per lock event.

    T2G-CONNECT-OR-DEGRADE-COUNTER-WIRED: counter read-back from
    src.observability.counters.read('db_write_lock_timeout_total').
    """
    from src.observability.counters import reset_all, read
    from src.state.db import connect_or_degrade

    reset_all()  # test isolation

    locked_exc = sqlite3.OperationalError("database is locked")
    with patch("src.state.db.sqlite3.connect", side_effect=locked_exc):
        result = connect_or_degrade(tmp_path / "locked.db")

    assert result is None, "connect_or_degrade must return None on lock"
    count = read("db_write_lock_timeout_total")
    assert count == 1, (
        f"Expected db_write_lock_timeout_total=1 after one lock event, got {count}"
    )

    # Second lock event increments again.
    with patch("src.state.db.sqlite3.connect", side_effect=locked_exc):
        connect_or_degrade(tmp_path / "locked2.db")

    assert read("db_write_lock_timeout_total") == 2, (
        "Counter must increment on each lock event"
    )

    reset_all()  # clean up after test


# ---------------------------------------------------------------------------
# T2G-CYCLE-RUNNER-PROPAGATES-NON-LOCK
# ---------------------------------------------------------------------------

def test_non_lock_operational_error_propagates(tmp_path):
    """OperationalError not starting with 'database is locked' propagates
    through connect_or_degrade — it is NOT silently swallowed.

    Asserts that the degrade primitive only catches DATA_DEGRADED
    (transient lock contention), not RED (genuine computation errors).
    """
    from src.state.db import connect_or_degrade

    non_lock_exc = sqlite3.OperationalError("no such table: foo")
    with patch("src.state.db.sqlite3.connect", side_effect=non_lock_exc):
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            connect_or_degrade(tmp_path / "nontable.db")


def test_non_lock_operational_error_does_not_increment_counter(tmp_path):
    """Non-lock OperationalError does NOT increment db_write_lock_timeout_total.

    Only 'database is locked' is a DATA_DEGRADED event; other errors are RED.
    """
    from src.observability.counters import reset_all, read
    from src.state.db import connect_or_degrade

    reset_all()

    non_lock_exc = sqlite3.OperationalError("disk I/O error")
    with patch("src.state.db.sqlite3.connect", side_effect=non_lock_exc):
        with pytest.raises(sqlite3.OperationalError):
            connect_or_degrade(tmp_path / "ioerr.db")

    assert read("db_write_lock_timeout_total") == 0, (
        "Non-lock OperationalError must not increment db_write_lock_timeout_total"
    )

    reset_all()
