# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1E/phase.json
"""Tests for T1E SQLite busy-timeout configurability and degrade-not-crash behavior.

Invariants asserted:
  T1E-BUSY-TIMEOUT-CONFIGURABLE: both _connect (line 40) and get_connection (line 349)
    read ZEUS_DB_BUSY_TIMEOUT_MS and apply timeout in seconds.
  T1E-ENV-OVERRIDE-WIRED: ZEUS_DB_BUSY_TIMEOUT_MS=5000 → timeout=5.0;
    unset → timeout=30.0.
  T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH: connect_or_degrade() returns None on
    'database is locked'; daemon does not raise.

Tests:
  test_default_30000ms_yields_30s       — env unset → timeout=30.0
  test_env_override_5000ms_yields_5s    — env=5000 → timeout=5.0
  test_env_override_applied_to_both_connect_sites — both _connect and get_connection honor env
  test_malformed_env_var_falls_back_or_raises     — executor choice: fall back to 30s
  test_db_write_timeout_does_not_crash_daemon     — connect_or_degrade returns None on locked DB
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_env(monkeypatch, value):
    """Set ZEUS_DB_BUSY_TIMEOUT_MS to value (or delete if None)."""
    if value is None:
        monkeypatch.delenv("ZEUS_DB_BUSY_TIMEOUT_MS", raising=False)
    else:
        monkeypatch.setenv("ZEUS_DB_BUSY_TIMEOUT_MS", str(value))


# ---------------------------------------------------------------------------
# T1E-ENV-OVERRIDE-WIRED: _db_busy_timeout_s unit tests
# ---------------------------------------------------------------------------

def test_default_30000ms_yields_30s(monkeypatch):
    """Unset env → _db_busy_timeout_s() == 30.0 seconds."""
    _set_env(monkeypatch, None)
    # Re-import to pick up clean env state; function reads env per-call.
    from src.state.db import _db_busy_timeout_s
    assert _db_busy_timeout_s() == pytest.approx(30.0)


def test_env_override_5000ms_yields_5s(monkeypatch):
    """ZEUS_DB_BUSY_TIMEOUT_MS=5000 → _db_busy_timeout_s() == 5.0 seconds."""
    _set_env(monkeypatch, "5000")
    from src.state.db import _db_busy_timeout_s
    assert _db_busy_timeout_s() == pytest.approx(5.0)


def test_malformed_env_var_falls_back_or_raises(monkeypatch):
    """Executor choice: malformed value falls back to 30.0 (catch-and-log).

    ZEUS_DB_BUSY_TIMEOUT_MS='abc' must not crash the daemon. The implementation
    logs a warning and returns 30.0 as the safe default.
    """
    _set_env(monkeypatch, "abc")
    from src.state.db import _db_busy_timeout_s
    result = _db_busy_timeout_s()
    assert result == pytest.approx(30.0), (
        f"Expected 30.0 fallback on malformed env var, got {result}"
    )


# ---------------------------------------------------------------------------
# T1E-BUSY-TIMEOUT-CONFIGURABLE: both connect sites honor env var
# ---------------------------------------------------------------------------

def test_env_override_applied_to_connect_site_1(monkeypatch, tmp_path):
    """_connect (site 1) applies ZEUS_DB_BUSY_TIMEOUT_MS env var."""
    _set_env(monkeypatch, "5000")
    captured = []

    real_connect = sqlite3.connect

    def fake_connect(path, **kwargs):
        captured.append(kwargs.get("timeout"))
        return real_connect(path, **kwargs)

    with patch("src.state.db.sqlite3.connect", side_effect=fake_connect):
        from src.state import db as _db
        db_path = tmp_path / "test.db"
        _db._connect(db_path)

    assert captured, "sqlite3.connect was not called"
    assert captured[0] == pytest.approx(5.0), (
        f"Expected timeout=5.0, got {captured[0]}"
    )


def test_env_override_applied_to_connect_site_2(monkeypatch, tmp_path):
    """get_connection (site 2) applies ZEUS_DB_BUSY_TIMEOUT_MS env var."""
    _set_env(monkeypatch, "5000")
    captured = []

    real_connect = sqlite3.connect

    def fake_connect(path, **kwargs):
        captured.append(kwargs.get("timeout"))
        return real_connect(path, **kwargs)

    with patch("src.state.db.sqlite3.connect", side_effect=fake_connect):
        from src.state import db as _db
        _db.get_connection(tmp_path / "test2.db")

    assert captured, "sqlite3.connect was not called"
    assert captured[0] == pytest.approx(5.0), (
        f"Expected timeout=5.0, got {captured[0]}"
    )


def test_env_override_applied_to_both_connect_sites(monkeypatch, tmp_path):
    """Both _connect (site 1) and get_connection (site 2) honor ZEUS_DB_BUSY_TIMEOUT_MS."""
    _set_env(monkeypatch, "7000")
    captured = []

    real_connect = sqlite3.connect

    def fake_connect(path, **kwargs):
        captured.append(kwargs.get("timeout"))
        return real_connect(path, **kwargs)

    with patch("src.state.db.sqlite3.connect", side_effect=fake_connect):
        from src.state import db as _db
        _db._connect(tmp_path / "site1.db")
        _db.get_connection(tmp_path / "site2.db")

    assert len(captured) >= 2, f"Expected at least 2 connect calls, got {len(captured)}"
    for t in captured:
        assert t == pytest.approx(7.0), f"Expected timeout=7.0, got {t}"


def test_default_timeout_is_30s_when_env_unset(monkeypatch, tmp_path):
    """Unset env → sqlite3.connect called with timeout=30.0 on both sites."""
    _set_env(monkeypatch, None)
    captured = []

    real_connect = sqlite3.connect

    def fake_connect(path, **kwargs):
        captured.append(kwargs.get("timeout"))
        return real_connect(path, **kwargs)

    with patch("src.state.db.sqlite3.connect", side_effect=fake_connect):
        from src.state import db as _db
        _db._connect(tmp_path / "d1.db")
        _db.get_connection(tmp_path / "d2.db")

    assert len(captured) >= 2
    for t in captured:
        assert t == pytest.approx(30.0), f"Expected timeout=30.0, got {t}"


# ---------------------------------------------------------------------------
# T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH: connect_or_degrade
# ---------------------------------------------------------------------------

def test_db_write_timeout_does_not_crash_daemon(tmp_path):
    """connect_or_degrade returns None on 'database is locked'; daemon does not raise.

    Simulates a long-running writer holding the DB lock such that the connection
    attempt exceeds busy-timeout. Asserts:
      1. connect_or_degrade() returns None (not raises).
      2. db_write_lock_timeout_total counter log is emitted.
    """
    from src.state.db import connect_or_degrade

    db_path = tmp_path / "locked.db"

    locked_exc = sqlite3.OperationalError("database is locked")

    import logging
    log_records = []

    class CapturingHandler(logging.Handler):
        def emit(self, record):
            log_records.append(record.getMessage())

    handler = CapturingHandler()
    import src.state.db as _db_module
    db_logger = logging.getLogger(_db_module.__name__)
    db_logger.addHandler(handler)
    original_level = db_logger.level
    db_logger.setLevel(logging.WARNING)

    try:
        with patch("src.state.db.sqlite3.connect", side_effect=locked_exc):
            result = connect_or_degrade(db_path)
    finally:
        db_logger.removeHandler(handler)
        db_logger.setLevel(original_level)

    assert result is None, (
        f"connect_or_degrade should return None on locked DB, got {result!r}"
    )
    counter_logged = any(
        "db_write_lock_timeout_total" in msg for msg in log_records
    )
    assert counter_logged, (
        f"Expected 'db_write_lock_timeout_total' in log records, got: {log_records}"
    )


def test_connect_or_degrade_reraises_non_lock_errors(tmp_path):
    """connect_or_degrade re-raises OperationalError not starting with 'database is locked'."""
    from src.state.db import connect_or_degrade

    other_exc = sqlite3.OperationalError("no such table: foo")
    with patch("src.state.db.sqlite3.connect", side_effect=other_exc):
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            connect_or_degrade(tmp_path / "x.db")


def test_connect_or_degrade_returns_connection_on_success(tmp_path):
    """connect_or_degrade returns a live connection when no lock contention."""
    from src.state.db import connect_or_degrade

    db_path = tmp_path / "ok.db"
    conn = connect_or_degrade(db_path)
    assert conn is not None
    conn.close()
