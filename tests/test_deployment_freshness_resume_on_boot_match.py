# Created: 2026-05-19
# Last reused or audited: 2026-07-14
# Authority basis: retired deployment freshness pauses are not market authority.
# Lifecycle: created=2026-05-19; last_reviewed=2026-07-14; last_reused=2026-07-14
# Purpose: antibody — exact obsolete deployment pauses retire without touching other pauses.
# Reuse: Run when modifying boot_check logic, deployment_freshness pause/resume, or control_plane boot sequence.
"""Antibody tests for boot-time deployment_freshness auto-resume.

Any lingering deployment_freshness pause is retired regardless of worktree or
Git state. Other operator, risk, and source pauses remain untouched.

Coverage:
  R1: exact deployment pause → cleared
  R2: SHA/worktree/Git differences → still cleared
  R3: entries not paused → no override expires
  R4: entries paused for different reason → not touched by auto-resume
  R5/R6: boot identity or Git unavailable → still cleared
"""

import sqlite3
import logging
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

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


def _seed_edge_tightening(conn: sqlite3.Connection) -> None:
    from src.state.db import upsert_control_override, DEFAULT_CONTROL_OVERRIDE_PRECEDENCE

    now = datetime.now(_UTC).isoformat()
    upsert_control_override(
        conn,
        override_id="control_plane:global:edge_threshold_multiplier",
        target_type="global",
        target_key="entries",
        action_type="threshold_multiplier",
        value="2.0",
        issued_by="control_plane",
        issued_at=now,
        reason="independent_risk_tightening",
        effective_until=None,
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
    git_diff_paths: tuple[str, ...] = ("src/main.py",),
    git_raises: Exception | None = None,
    state_dir: Path | None = None,
    dirty_runtime_paths: tuple[str, ...] = (),
) -> None:
    """Run _boot_deployment_freshness_auto_resume with controlled git output."""
    def _fake_git(cmd, **kw):
        if git_raises:
            raise git_raises
        if list(cmd[:3]) == ["git", "diff", "--name-only"]:
            return ("\n".join(git_diff_paths) + "\n").encode()
        return current_sha.encode()

    with patch.object(main_module, "_BOOT_STATE", {"sha": boot_sha, "ts": datetime.now(_UTC)}):
        with patch("subprocess.check_output", side_effect=_fake_git):
            with patch(
                "src.control.runtime_code_plane.dirty_runtime_worktree_paths",
                return_value=dirty_runtime_paths,
            ):
                if state_dir is not None:
                    with patch("src.config.state_path", side_effect=lambda name: state_dir / name):
                        _boot_deployment_freshness_auto_resume()
                else:
                    with tempfile.TemporaryDirectory() as tmp_state:
                        tmp_dir = Path(tmp_state)
                        with patch("src.config.state_path", side_effect=lambda name: tmp_dir / name):
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
                _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, state_dir=tmp_path)

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
        """Retirement emits an explicit reason without using SHA as authority."""
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                with caplog.at_level(logging.INFO, logger="zeus"):
                    _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, state_dir=tmp_path)

        assert "deployment_freshness_auto_resume" in caplog.text
        assert "retired obsolete deployment pause" in caplog.text

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
                _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, state_dir=tmp_path)
                # Narrow retirement refreshes the in-memory projection at the end.
                assert not cp.is_entries_paused(), (
                    "in-memory entries_paused must be False after auto-resume"
                )

    def test_r1_retirement_preserves_independent_edge_tightening(self, tmp_path):
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        _seed_edge_tightening(conn)
        conn.close()

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, state_dir=tmp_path)

        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        fresh_conn.row_factory = sqlite3.Row
        from src.state.db import query_control_override_state

        state = query_control_override_state(fresh_conn)
        fresh_conn.close()
        assert state["entries_paused"] is False
        assert state["edge_threshold_multiplier"] == 2.0

    def test_r1_sha_match_updates_freshness_state_file(self, tmp_path):
        """SHA match: stale mismatch file is replaced with a fresh state proof."""
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()
        df_path = tmp_path / "deployment_freshness.json"
        df_path.write_text(
            json.dumps(
                {
                    "boot_sha": DIFF_SHA,
                    "current_sha": BOOT_SHA,
                    "status": "mismatch",
                    "pause_reason": "deployment_freshness_mismatch",
                    "detected_at": (datetime.now(_UTC) - timedelta(minutes=5)).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, state_dir=tmp_path)

        payload = json.loads(df_path.read_text(encoding="utf-8"))
        assert payload["status"] == "fresh"
        assert payload["pause_reason"] is None
        assert payload["boot_sha"] == BOOT_SHA
        assert payload["current_sha"] == BOOT_SHA

    def test_r1_dirty_runtime_worktree_still_clears_deployment_pause(self, tmp_path):
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                _run_auto_resume(
                    boot_sha=BOOT_SHA,
                    current_sha=BOOT_SHA,
                    state_dir=tmp_path,
                    dirty_runtime_paths=("src/control/live_health.py",),
                )

        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        fresh_conn.row_factory = sqlite3.Row
        from src.state.db import query_control_override_state
        state = query_control_override_state(fresh_conn)
        fresh_conn.close()
        assert state["entries_paused"] is False
        payload = json.loads((tmp_path / "deployment_freshness.json").read_text())
        assert payload["status"] == "dirty_runtime_worktree"
        assert payload["pause_reason"] is None
        assert payload["worktree_runtime_dirty"] is True
        assert payload["dirty_runtime_paths_sample"] == ["src/control/live_health.py"]


# ---------------------------------------------------------------------------
# R2: SHA mismatch is observability, not pause authority
# ---------------------------------------------------------------------------

class TestAutoResumeAcrossShaMismatch:
    def test_r2_sha_mismatch_still_clears(self, tmp_path, caplog):
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
        assert state["entries_paused"] is False
        assert "deployment_freshness_observed" in caplog.text

    def test_r2_non_runtime_sha_mismatch_clears_deployment_pause(self, tmp_path):
        """Tests/docs-only drift is not a deployment freshness blocker."""
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                _run_auto_resume(
                    boot_sha=BOOT_SHA,
                    current_sha=DIFF_SHA,
                    git_diff_paths=("tests/test_only.py",),
                    state_dir=tmp_path,
                )

        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        fresh_conn.row_factory = sqlite3.Row
        from src.state.db import query_control_override_state
        state = query_control_override_state(fresh_conn)
        fresh_conn.close()
        assert state["entries_paused"] is False
        payload = json.loads((tmp_path / "deployment_freshness.json").read_text())
        assert payload["status"] == "fresh"
        assert payload["code_plane_status"] == "non_runtime_diff"


# ---------------------------------------------------------------------------
# R3: entries not paused → no-op
# ---------------------------------------------------------------------------

class TestAutoResumeNoop:
    def test_r3_no_pause_is_noop(self, tmp_path):
        """If entries are not paused, retirement does not create an override event."""
        _, conn = _setup_world_db(tmp_path)
        conn.close()
        # Do NOT seed any pause

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA)

        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        count = fresh_conn.execute(
            "SELECT COUNT(*) FROM control_overrides_history"
        ).fetchone()[0]
        fresh_conn.close()
        assert count == 0

    def test_r3_no_pause_still_refreshes_freshness_state_file(self, tmp_path):
        """If DB pause was already cleared, boot still replaces stale freshness state."""
        _, conn = _setup_world_db(tmp_path)
        conn.close()
        df_path = tmp_path / "deployment_freshness.json"
        df_path.write_text(
            json.dumps(
                {
                    "boot_sha": DIFF_SHA,
                    "current_sha": DIFF_SHA,
                    "status": "fresh",
                    "pause_reason": None,
                    "detected_at": (datetime.now(_UTC) - timedelta(minutes=5)).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, state_dir=tmp_path)

        payload = json.loads(df_path.read_text(encoding="utf-8"))
        assert payload["status"] == "fresh"
        assert payload["pause_reason"] is None
        assert payload["boot_sha"] == BOOT_SHA
        assert payload["current_sha"] == BOOT_SHA

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

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                # Still paused at this point
                assert cp.is_entries_paused()
                _run_auto_resume(boot_sha=BOOT_SHA, current_sha=BOOT_SHA)

        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        fresh_conn.row_factory = sqlite3.Row
        from src.state.db import query_control_override_state

        state = query_control_override_state(fresh_conn)
        fresh_conn.close()
        assert state["entries_paused"] is True
        assert state["entries_pause_reason"] == "manual_operator_override"


# ---------------------------------------------------------------------------
# R5/R6: identity observation failures do not preserve obsolete pauses
# ---------------------------------------------------------------------------

class TestAutoResumeFailOpen:
    def test_r5_no_boot_sha_still_clears(self, tmp_path):
        _, conn = _setup_world_db(tmp_path)
        _seed_deployment_freshness_pause(conn)
        conn.close()

        factory = _make_conn_factory(tmp_path / "world.db")
        with patch("src.state.db.get_world_connection", side_effect=factory):
            with patch("src.control.control_plane.get_world_connection", side_effect=factory):
                cp.refresh_control_state()
                with patch.object(main_module, "_BOOT_STATE", {"sha": None, "ts": None}):
                    _boot_deployment_freshness_auto_resume()
        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        fresh_conn.row_factory = sqlite3.Row
        from src.state.db import query_control_override_state
        state = query_control_override_state(fresh_conn)
        fresh_conn.close()
        assert state["entries_paused"] is False

    def test_r6_git_failure_warns_and_returns(self, tmp_path, caplog):
        """Git subprocess failure is observed after obsolete pause retirement."""
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
        fresh_conn = sqlite3.connect(str(tmp_path / "world.db"))
        fresh_conn.row_factory = sqlite3.Row
        from src.state.db import query_control_override_state
        state = query_control_override_state(fresh_conn)
        fresh_conn.close()
        assert state["entries_paused"] is False
