# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T2G/phase.json
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
    monkeypatch.setattr(cr, "get_force_exit_review", lambda: False)


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
