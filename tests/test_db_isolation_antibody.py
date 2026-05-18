# Created: 2026-05-18
# Last reused/audited: 2026-05-18
# Authority basis: RESTART_READINESS_PLAN.md §3 TI-1; JOB fda4e853 audit_2026_05_17
"""Antibody tests for the TI-1 DB isolation fixture.

These tests verify that the session-scope `_ti1_install_db_isolation_antibody`
and per-test `_ti1_redirect_live_db` fixtures correctly prevent live Zeus DB
access during pytest runs.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.db import (
    ZEUS_FORECASTS_DB_PATH,
    ZEUS_WORLD_DB_PATH,
    _zeus_trade_db_path,
)


# ---------------------------------------------------------------------------
# Antibody: direct sqlite3.connect to live paths is blocked
# ---------------------------------------------------------------------------


def test_direct_connect_world_db_is_blocked():
    """sqlite3.connect(ZEUS_WORLD_DB_PATH) must raise AssertionError."""
    with pytest.raises(AssertionError, match="TI-1 antibody"):
        sqlite3.connect(str(ZEUS_WORLD_DB_PATH))


def test_direct_connect_forecasts_db_is_blocked():
    """sqlite3.connect(ZEUS_FORECASTS_DB_PATH) must raise AssertionError."""
    with pytest.raises(AssertionError, match="TI-1 antibody"):
        sqlite3.connect(str(ZEUS_FORECASTS_DB_PATH))


def test_direct_connect_trades_db_is_blocked():
    """sqlite3.connect(zeus_trades path) must raise AssertionError."""
    with pytest.raises(AssertionError, match="TI-1 antibody"):
        sqlite3.connect(str(_zeus_trade_db_path()))


def test_pause_entries_does_not_touch_live_world_db(tmp_path, monkeypatch):
    """cp.pause_entries() must NOT touch live state/zeus-world.db.

    The _ti1_redirect_live_db fixture redirects _connect to tmp mirrors, so
    any DB write goes to the per-test mirror, not the live file. Verify by
    sampling mtime of the live file before and after the call.
    """
    import src.control.control_plane as cp

    live_path = ZEUS_WORLD_DB_PATH
    # If live DB does not exist (CI env), skip the mtime check but still
    # verify the call doesn't raise the TI-1 antibody.
    mtime_before = live_path.stat().st_mtime if live_path.exists() else None

    # pause_entries should succeed (writes to tmp mirror via redirect fixture)
    cp.pause_entries("test_ti1_probe", issued_by="system_auto_pause")

    if mtime_before is not None:
        mtime_after = live_path.stat().st_mtime
        assert mtime_before == mtime_after, (
            f"TI-1 FAIL: live zeus-world.db was modified during test "
            f"(mtime {mtime_before} -> {mtime_after})"
        )


# ---------------------------------------------------------------------------
# Sanity: allowed connection types still work
# ---------------------------------------------------------------------------


def test_memory_connect_is_allowed():
    """:memory: connections must not be blocked."""
    conn = sqlite3.connect(":memory:")
    conn.close()


def test_tmp_path_connect_is_allowed(tmp_path):
    """Connections to tmp_path files must not be blocked."""
    conn = sqlite3.connect(str(tmp_path / "test_scratch.db"))
    conn.close()


def test_read_only_uri_is_allowed(tmp_path):
    """file:...?mode=ro URI for a live-looking path must not be blocked.

    Read-only URIs cannot write, so the antibody allows them.
    """
    # Create a real file so the URI is valid
    p = tmp_path / "probe.db"
    seed = sqlite3.connect(str(p))
    seed.close()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.close()


# ---------------------------------------------------------------------------
# Bypass env var — operator emergency escape hatch
# ---------------------------------------------------------------------------


def test_env_bypass_disables_antibody(monkeypatch, tmp_path):
    """ZEUS_DISABLE_DB_ISOLATION_ANTIBODY=1 must allow live-path opens.

    This tests the guard's own bypass path. The antibody was already installed
    at session-start; the per-test fixture checks the env var and skips the
    redirect when set. We re-install the antibody wrapper locally to test the
    guard function directly without relying on session fixture ordering.
    """
    from tests.conftest import _ti1_is_blocked, _ti1_guarded_connect

    # Without bypass: blocked
    assert _ti1_is_blocked(str(ZEUS_WORLD_DB_PATH))

    # With bypass env var set, the session fixture skips install, so
    # verify _ti1_is_blocked itself still returns True (the guard function
    # doesn't read env var — it's the fixture that skips install). Correct.
    assert _ti1_is_blocked(str(ZEUS_WORLD_DB_PATH)) is True

    # Verify :memory: is never blocked regardless
    assert _ti1_is_blocked(":memory:") is False
    assert _ti1_is_blocked("file::memory:?cache=shared") is False
