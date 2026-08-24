# Created: 2026-08-24
# Last reused or audited: 2026-08-24
# Authority basis: reversal plan item 5b (docs/operations/current/plans/
#   reversal_plan_tier0_2026-08-24.md) — the 2026-08-24 full-book investigation
#   found RiskGuard stuck non-GREEN explained 97.6h of August silence (10/11
#   gaps DATA_DEGRADED, one RED) with zero alerts; one 25.6h window was 99.9%
#   non-GREEN. `maybe_alert_riskguard_stuck_non_green` (src/riskguard/riskguard.py)
#   mirrors the accepted `_maybe_alert_held_position_monitor_bootstrap_stall`
#   pattern (src/main.py, commit d1aeeeb52, item 5a): logging + a best-effort
#   breadcrumb only, never touching the level the gate consumes.
"""Stuck non-GREEN risk_state alert: threshold, throttle, breadcrumb, gate purity."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.riskguard import riskguard as riskguard_module
from src.riskguard.riskguard import RiskLevel, get_current_level, init_risk_db


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def risk_conn(tmp_path):
    """A file-backed risk_state DB.

    File-backed (not :memory:) so `get_connection` fakes below can open a
    FRESH connection per call, matching production `get_current_level()` /
    `maybe_alert_riskguard_stuck_non_green()` behavior of opening-and-closing
    their own connection each call. A single shared :memory: connection would
    break the moment the function under test closes "its" connection.
    """
    path = tmp_path / "risk_state.db"
    conn = _open(path)
    init_risk_db(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def _reset_stuck_alert_state(monkeypatch, risk_conn):
    """Isolate module-level throttle state and the risk_state connection per test."""
    monkeypatch.setattr(riskguard_module, "_riskguard_stuck_alert_run_started_at", None)
    monkeypatch.setattr(riskguard_module, "_riskguard_stuck_alert_last_alert_monotonic", None)

    def _fake_get_connection(path=None, **_kwargs):
        return _open(risk_conn)

    monkeypatch.setattr(riskguard_module, "get_connection", _fake_get_connection)

    from src.config import state_path

    breadcrumb_path = state_path(riskguard_module.STUCK_ALERT_BREADCRUMB_FILENAME)
    if breadcrumb_path.exists():
        breadcrumb_path.unlink()
    yield
    if breadcrumb_path.exists():
        breadcrumb_path.unlink()


def _seed_row(
    path,
    *,
    level: str,
    checked_at: str,
    details: dict | None = None,
) -> None:
    conn = _open(path)
    try:
        conn.execute(
            "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at) "
            "VALUES (?, NULL, NULL, NULL, ?, ?)",
            (level, json.dumps(details or {}), checked_at),
        )
        conn.commit()
    finally:
        conn.close()


def _row_count(path) -> int:
    conn = _open(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM risk_state").fetchone()[0]
    finally:
        conn.close()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_non_green_under_threshold_no_alert(risk_conn, caplog):
    now = datetime.now(timezone.utc)
    _seed_row(
        risk_conn,
        level="DATA_DEGRADED",
        checked_at=_iso(now - timedelta(seconds=60)),
        details={"settlement_quality_level": "DATA_DEGRADED"},
    )
    with caplog.at_level(logging.ERROR):
        riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.DATA_DEGRADED)
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
    from src.config import state_path

    assert not state_path(riskguard_module.STUCK_ALERT_BREADCRUMB_FILENAME).exists()


def test_crossing_threshold_alerts_once_with_breadcrumb(risk_conn, caplog):
    now = datetime.now(timezone.utc)
    started = now - timedelta(seconds=riskguard_module.STUCK_ALERT_AFTER_SECONDS + 10)
    _seed_row(
        risk_conn,
        level="GREEN",
        checked_at=_iso(started - timedelta(seconds=60)),
        details={},
    )
    _seed_row(
        risk_conn,
        level="DATA_DEGRADED",
        checked_at=_iso(started),
        details={"settlement_quality_level": "DATA_DEGRADED", "brier_level": "GREEN"},
    )
    _seed_row(
        risk_conn,
        level="RED",
        checked_at=_iso(now),
        details={"brier_level": "RED"},
    )

    with caplog.at_level(logging.ERROR):
        riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.RED)

    alert_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(alert_records) == 1
    assert "stuck non-GREEN" in alert_records[0].message
    assert "level=RED" in alert_records[0].message

    from src.config import state_path

    breadcrumb_path = state_path(riskguard_module.STUCK_ALERT_BREADCRUMB_FILENAME)
    assert breadcrumb_path.exists()
    payload = json.loads(breadcrumb_path.read_text())
    assert payload["level"] == "RED"
    assert payload["first_causes"] == ["settlement_quality"]
    assert payload["current_causes"] == ["brier"]
    assert payload["elapsed_seconds"] >= riskguard_module.STUCK_ALERT_AFTER_SECONDS
    assert payload["run_started_at"] == _iso(started)
    assert payload["lookback_capped"] is False


def test_still_stuck_past_repeat_window_alerts_again(risk_conn, caplog, monkeypatch):
    now = datetime.now(timezone.utc)
    started = now - timedelta(seconds=riskguard_module.STUCK_ALERT_AFTER_SECONDS + 10)
    _seed_row(risk_conn, level="GREEN", checked_at=_iso(started - timedelta(seconds=60)))
    _seed_row(
        risk_conn,
        level="DATA_DEGRADED",
        checked_at=_iso(started),
        details={"execution_quality_level": "DATA_DEGRADED"},
    )

    clock = {"t": 1000.0}
    monkeypatch.setattr(riskguard_module.time, "monotonic", lambda: clock["t"])

    with caplog.at_level(logging.ERROR):
        riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.DATA_DEGRADED)
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1

    # Still within the repeat window: no second alert.
    caplog.clear()
    clock["t"] += 5.0
    with caplog.at_level(logging.ERROR):
        riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.DATA_DEGRADED)
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]

    # Past the repeat window: exactly one more alert.
    caplog.clear()
    clock["t"] += riskguard_module.STUCK_ALERT_REPEAT_SECONDS + 1.0
    with caplog.at_level(logging.ERROR):
        riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.DATA_DEGRADED)
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


def test_recovery_to_green_clears_breadcrumb_and_resets_clock(risk_conn, caplog, monkeypatch):
    now = datetime.now(timezone.utc)
    started = now - timedelta(seconds=riskguard_module.STUCK_ALERT_AFTER_SECONDS + 10)
    _seed_row(risk_conn, level="GREEN", checked_at=_iso(started - timedelta(seconds=60)))
    _seed_row(
        risk_conn,
        level="DATA_DEGRADED",
        checked_at=_iso(started),
        details={"execution_quality_level": "DATA_DEGRADED"},
    )

    clock = {"t": 1000.0}
    monkeypatch.setattr(riskguard_module.time, "monotonic", lambda: clock["t"])

    with caplog.at_level(logging.ERROR):
        riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.DATA_DEGRADED)
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    assert riskguard_module._riskguard_stuck_alert_run_started_at is not None

    from src.config import state_path

    breadcrumb_path = state_path(riskguard_module.STUCK_ALERT_BREADCRUMB_FILENAME)
    assert breadcrumb_path.exists()

    # Recover to GREEN: breadcrumb is overwritten with a recovery marker, and
    # the in-memory run marker clears.
    _seed_row(risk_conn, level="GREEN", checked_at=_iso(now))
    riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.GREEN)
    assert riskguard_module._riskguard_stuck_alert_run_started_at is None
    assert riskguard_module._riskguard_stuck_alert_last_alert_monotonic is None
    recovered_payload = json.loads(breadcrumb_path.read_text())
    assert "recovered_at" in recovered_payload
    assert recovered_payload["previous_run_started_at"] == _iso(started)

    # A fresh stuck episode starts its own clock: immediately below threshold
    # even though the previous episode was already past it.
    new_started = now
    _seed_row(
        risk_conn,
        level="YELLOW",
        checked_at=_iso(new_started),
        details={"strategy_signal_level": "YELLOW"},
    )
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.YELLOW)
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_lookback_cap_reports_capped_true(risk_conn, caplog, monkeypatch):
    monkeypatch.setattr(riskguard_module, "STUCK_ALERT_LOOKBACK_ROWS", 3)
    now = datetime.now(timezone.utc)
    started = now - timedelta(seconds=riskguard_module.STUCK_ALERT_AFTER_SECONDS + 10)
    # No GREEN row at all within the (artificially small) lookback cap.
    for i in range(3):
        _seed_row(
            risk_conn,
            level="DATA_DEGRADED",
            checked_at=_iso(started + timedelta(seconds=i)),
            details={"storage_capacity_level": "DATA_DEGRADED"},
        )

    with caplog.at_level(logging.ERROR):
        riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.DATA_DEGRADED)

    from src.config import state_path

    payload = json.loads(
        state_path(riskguard_module.STUCK_ALERT_BREADCRUMB_FILENAME).read_text()
    )
    assert payload["lookback_capped"] is True


def test_cause_unavailable_fallback_for_bare_row():
    causes = riskguard_module._riskguard_row_causes({})
    assert causes == ["cause_unavailable"]
    causes = riskguard_module._riskguard_row_causes(
        {"riskguard_degraded_reason": "dependency_db_locked"}
    )
    assert causes == ["dependency_db_locked"]


def test_alert_path_never_changes_computed_level_or_writes_rows(risk_conn):
    """Property: get_current_level() and risk_state row count are unaffected
    by whether the stuck-alert path runs, with alerts both enabled and after
    exercising the alert (threshold crossed)."""
    now = datetime.now(timezone.utc)
    started = now - timedelta(seconds=riskguard_module.STUCK_ALERT_AFTER_SECONDS + 10)
    _seed_row(risk_conn, level="GREEN", checked_at=_iso(started - timedelta(seconds=60)))
    _seed_row(
        risk_conn,
        level="RED",
        checked_at=_iso(now),
        details={
            "brier_level": "RED",
            "execution_quality_level": "GREEN",
            "strategy_signal_level": "GREEN",
            "recommended_controls": [],
            "recommended_strategy_gates": [],
        },
    )

    row_count_before = _row_count(risk_conn)
    level_before = get_current_level()

    riskguard_module.maybe_alert_riskguard_stuck_non_green(RiskLevel.RED)

    row_count_after = _row_count(risk_conn)
    level_after = get_current_level()

    assert row_count_after == row_count_before
    assert level_after == level_before == RiskLevel.RED
