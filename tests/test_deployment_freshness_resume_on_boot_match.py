# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PIPELINE_REVIEW.md §8, SYNTHESIS.md §8.7
#   5/18 incident: PR #149 deployment_freshness_4h_divergence pause persisted
#   across daemon restart because boot sequence had no auto-resume logic.
"""Antibody tests for boot-time deployment_freshness auto-resume.

When an operator restarts the daemon after deploying new code (i.e. git HEAD
now matches boot SHA), any lingering deployment_freshness_4h_divergence pause
must be automatically cleared so entries are not perpetually blocked.

Coverage:
  R1: SHA match → pause cleared (tombstone gone, control_overrides expired, log emitted)
  R2: SHA mismatch → pause NOT cleared (operator must investigate)
  R3: entries not paused → no-op (resume_entries not called)
  R4: entries paused for different reason → not touched by auto-resume
  R5: boot SHA not captured → warning logged, no-op
  R6: git fails → warning logged, no-op (fail-open for boot safety)

Meta-verify (sed-flip):
  Invert the SHA-match condition (force always-mismatch) → R1 fails RED.
  Restore → R1 GREEN.
"""

import sqlite3
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import src.main as main_module
from src.main import _boot_deployment_freshness_auto_resume, _BOOT_STATE
import src.control.control_plane as cp


BOOT_SHA = "aabbcc112233445566778899"
DIFF_SHA = "ddeeff112233445566778899"
_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_world_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    """Create a world DB with the full schema, return (path, conn)."""
    from src.state.db import init_schema_world_only
    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema_world_only(conn)
    conn.commit()
    return db_path, conn


def _seed_deployment_freshness_pause(conn: sqlite3.Connection) -> None:
    """Pre-seed the DB with an active deployment_freshness_4h_divergence pause."""
    from src.state.db import upsert_control_override, DEFAULT_CONTROL_OVERRIDE_PRECEDENCE
    now = datetime.now(_UTC).isoformat()
    upsert_control_override(
        conn,
        override_id="control_plane:global:entries_paused",
        target_type="global",
        target_key="entries",
        action_type="gate",
        value="true",
        issued_by="system_auto_pause",
        issued_at=now,
        reason="deployment_freshness_4h_divergence",
        effective_until=None,  # indefinite (operator restart must clear it)
        precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
    )
    conn.commit()


def _is_pause_active(conn: sqlite3.Connection) -> bool:
    """Return True iff the entries_paused override is still active in the DB VIEW."""
    conn.row_factory = sqlite3.Row
    from src.state.db import query_control_override_state
    state = query_control_override_state(conn)
    return bool(state.get("entries_paused", False))


def _run_auto_resume(
    *,
    boot_sha: str = BOOT_SHA,
    current_sha: str = BOOT_SHA,
    git_raises: Exception | None = None,
) -> None:
    """Run _boot_deployment_freshness_auto_resume with controlled git output."""
    def _fake_git(cmd, **kw):
        if git_raises:
            raise git_raises
        return current_sha.encode()

    with patch.object(main_module, "_BOOT_STATE", {"sha": boot_sha, "ts": datetime.now(_UTC)}):
        with patch("subprocess.check_output", side_effect=_fake_git):
            _boot_deployment_freshness_auto_resume()


# ---------------------------------------------------------------------------
# R1: SHA match → pause cleared
# ---------------------------------------------------------------------------

def _make_conn_factory(db_path: Path):
    """Return a factory that creates a new SQLite connection (with Row factory) each call."""
    db_path_str = str(db_path)
    def _factory(**_kw):
        c = sqlite3.connect(db_path_str)
        c.row_factory = sqlite3.Row
        return c
    return _factory


class TestAutoResumeOnShaMatch:
    def test_r1_sha_match_clears_db_pause(self, tmp_path):
        """SHA match: DB override expires (entries_paused becomes False)."""
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()
        assert _is_pause_active(
            sqlite3.connect(str(tmp_path / "world.db"))
        ), "pre-condition: pause must be active"

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA)

        # Reconnect to verify the DB row was expired
        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        fresh_conn.row_factory = sqlite3.Row
        from src.state.db import query_control_override_state
        state = query_control_override_state(fresh_conn)
        fresh_conn.close()
        assert state["entries_paused"] is False, (
            "entries_paused must be False after SHA-match auto-resume"
        )

    def test_r1_sha_match_emits_info_log(self, tmp_path, caplog):
        """SHA match: INFO log emitted with both SHAs."""
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                with caplog.at_level(logging.INFO, logger="zeus"):
                    _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA)

        assert "deployment_freshness_auto_resume" in caplog.text
        assert BOOT_SHA[:8] in caplog.text

    def test_r1_in_memory_state_cleared(self, tmp_path):
        """SHA match: in-memory _control_state is refreshed (entries_paused=False)."""
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                assert cp.is_entries_paused(), "pre-condition: in-memory must show paused"
                _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA)
                # resume_entries calls refresh_control_state() at the end
                assert not cp.is_entries_paused(), (
                    "in-memory entries_paused must be False after auto-resume"
                )


# ---------------------------------------------------------------------------
# R2: SHA mismatch → pause NOT cleared
# ---------------------------------------------------------------------------

class TestAutoResumeBlockedOnShaMMismatch:
    def test_r2_sha_mismatch_does_not_clear(self, tmp_path, caplog):
        """SHA still mismatched: pause remains active, WARNING logged."""
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                with caplog.at_level(logging.WARNING, logger="zeus"):
                    _run_auto_resume(boot_sha=BOOT_SHA, current_sha=DIFF_SHA)

        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        fresh_conn.row_factory = sqlite3.Row
        from src.state.db import query_control_override_state
        state = query_control_override_state(fresh_conn)
        fresh_conn.close()
        assert state["entries_paused"] is True, "pause must remain when SHA still mismatched"
        assert "NOT auto-resuming" in caplog.text


# ---------------------------------------------------------------------------
# R3: entries not paused → no-op
# ---------------------------------------------------------------------------

class TestAutoResumeNoop:
    def test_r3_no_pause_is_noop(self, tmp_path):
        """If entries are not paused, auto-resume is a no-op (no resume_entries call)."""
        _, conn = _setup_world_db(tmp_path)
        conn.close()
        # Do NOT seed any pause

        resume_mock = MagicMock()
        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                with patch("src.control.control_plane.resume_entries", resume_mock):
                    _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA)

        resume_mock.assert_not_called()

    def test_r4_different_pause_reason_not_touched(self, tmp_path):
        """Entries paused for a DIFFERENT reason are not cleared by auto-resume."""
        _, conn = _setup_world_db(tmp_path)
        # Seed a pause with a different reason
        from src.state.db import upsert_control_override, DEFAULT_CONTROL_OVERRIDE_PRECEDENCE
        now = datetime.now(_UTC).isoformat()
        upsert_control_override(
            conn,
            override_id="control_plane:global:entries_paused",
            target_type="global",
            target_key="entries",
            action_type="gate",
            value="true",
            issued_by="system_auto_pause",
            issued_at=now,
            reason="manual_operator_override",
            effective_until=None,
            precedence=DEFAULT_CONTROL_OVERRIDE_PRECEDENCE,
        )
        conn.commit()
        conn.close()

        resume_mock = MagicMock()
        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                # Still paused at this point
                assert cp.is_entries_paused()
                with patch("src.control.control_plane.resume_entries", resume_mock):
                    _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA)

        resume_mock.assert_not_called()


# ---------------------------------------------------------------------------
# R5/R6: failure modes — fail-open for boot safety
# ---------------------------------------------------------------------------

class TestAutoResumeFailOpen:
    def test_r5_no_boot_sha_warns_and_returns(self, caplog):
        """Boot SHA not captured → WARNING logged, no crash."""
        with patch.object(main_module, "_BOOT_STATE", {"sha": None, "ts": None}):
            with caplog.at_level(logging.WARNING, logger="zeus"):
                cp._control_state["entries_paused"] = True
                cp._control_state["entries_pause_reason"] = "deployment_freshness_4h_divergence"
                try:
                    _boot_deployment_freshness_auto_resume()
                finally:
                    cp._control_state["entries_paused"] = False
                    cp._control_state["entries_pause_reason"] = None
        assert "boot SHA not captured" in caplog.text

    def test_r6_git_failure_warns_and_returns(self, tmp_path, caplog):
        """Git subprocess failure → WARNING logged, pause not cleared, no crash."""
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                with caplog.at_level(logging.WARNING, logger="zeus"):
                    _run_auto_resume(
                        boot_sha=BOOT_SHA,
                        current_sha=BOOT_SHA,
                        git_raises=subprocess.TimeoutExpired(["git"], timeout=5),
                    )

        assert "git rev-parse failed" in caplog.text
        # Pause should still be active
        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        fresh_conn.row_factory = sqlite3.Row
        from src.state.db import query_control_override_state
        state = query_control_override_state(fresh_conn)
        fresh_conn.close()
        assert state["entries_paused"] is True
