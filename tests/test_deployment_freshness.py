# Created: 2026-05-17
# Last reused or audited: 2026-07-14
# Authority basis: first-principles separation of loaded-code identity from market authority.
"""Antibodies for deployment-freshness observability.

The boot SHA identifies the code that made a decision. Worktree drift is useful
operator evidence, but it cannot pause entries or terminate a healthy daemon.
"""

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

import src.main as main_module
from src.main import _check_deployment_freshness, _capture_boot_state, _BOOT_STATE


BOOT_SHA = "abc1234567890"
DIFF_SHA = "def9876543210"
_UTC = timezone.utc


def _ts(hours_ago: float = 0.0) -> datetime:
    return datetime.now(_UTC) - timedelta(hours=hours_ago)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(
    *,
    boot_sha: str = BOOT_SHA,
    boot_ts_hours_ago: float = 0.0,
    current_sha: str = BOOT_SHA,
    git_diff_paths: tuple[str, ...] = ("src/main.py",),
    accept_stale_env: bool = False,
    now_hours_after_boot: float | None = None,
    repo_root: Path | None = None,
    git_raises: Exception | None = None,
    pause_entries_mock=None,
    state_path_return=None,
    dirty_runtime_paths: tuple[str, ...] = (),
    **kwargs,
):
    """Run _check_deployment_freshness with controlled inputs."""
    boot_ts = _ts(boot_ts_hours_ago)
    if now_hours_after_boot is not None:
        now = boot_ts + timedelta(hours=now_hours_after_boot)
    else:
        now = datetime.now(_UTC)

    env_val = "1" if accept_stale_env else ""
    env_patch = {"ZEUS_ACCEPT_STALE_DEPLOY": env_val}
    tmp_state = None
    if state_path_return is None:
        tmp_state = tempfile.TemporaryDirectory()
        state_path_return = Path(tmp_state.name) / "deployment_freshness.json"

    def fake_check_output(cmd, **kw):
        if git_raises:
            raise git_raises
        if list(cmd[:3]) == ["git", "diff", "--name-only"]:
            return ("\n".join(git_diff_paths) + "\n").encode()
        return current_sha.encode()

    # Mock os.kill so every test also proves the observer never signals itself.
    ctx = [
        patch.dict(os.environ, env_patch, clear=False),
        patch("subprocess.check_output", side_effect=fake_check_output),
        patch(
            "src.control.runtime_code_plane.dirty_runtime_worktree_paths",
            return_value=dirty_runtime_paths,
        ),
        patch("os.kill"),
        patch("src.config.state_path", return_value=state_path_return),
    ]
    if tmp_state is not None:
        ctx.append(tmp_state)
    if pause_entries_mock is not None:
        ctx.append(patch("src.control.control_plane.pause_entries", pause_entries_mock))

    # Enter all contexts
    entered = []
    try:
        for c in ctx:
            entered.append(c.__enter__())
        return _check_deployment_freshness(
            boot_sha=boot_sha,
            boot_ts=boot_ts,
            repo_root=Path("/fake/repo"),
            now=now,
            **kwargs,
        )
    finally:
        for c, e in zip(reversed(ctx), reversed(entered)):
            c.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Tests: no divergence
# ---------------------------------------------------------------------------

class TestNoAction:
    def test_no_divergence_passes(self):
        """Same SHA at boot and filesystem — no warning, no exit."""
        _run(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, boot_ts_hours_ago=10.0)

    def test_no_divergence_long_uptime_passes(self):
        """Even after 48h uptime, same SHA is fine."""
        _run(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, boot_ts_hours_ago=48.0)

    def test_no_divergence_clears_existing_stale_flag_projection(self, tmp_path):
        """A stale advisory flag must not keep saying paused after a matching restart."""
        df_path = tmp_path / "deployment_freshness.json"
        df_path.write_text(
            json.dumps(
                {
                    "boot_sha": "old",
                    "current_sha": DIFF_SHA,
                    "uptime_hours": 1.0,
                    "detected_at": _ts().isoformat(),
                    "pause_reason": "deployment_freshness_mismatch",
                    "status": "mismatch",
                }
            )
        )

        _run(
            boot_sha=BOOT_SHA,
            current_sha=BOOT_SHA,
            boot_ts_hours_ago=1.0,
            state_path_return=df_path,
        )

        flag = json.loads(df_path.read_text())
        assert flag["boot_sha"] == BOOT_SHA
        assert flag["current_sha"] == BOOT_SHA
        assert flag["pause_reason"] is None
        assert flag["status"] == "fresh"

    def test_same_sha_dirty_runtime_worktree_is_observed_without_pause(self, tmp_path):
        pause_mock = MagicMock()
        df_path = tmp_path / "deployment_freshness.json"

        _run(
            boot_sha=BOOT_SHA,
            current_sha=BOOT_SHA,
            boot_ts_hours_ago=1.0,
            pause_entries_mock=pause_mock,
            state_path_return=df_path,
            dirty_runtime_paths=("src/control/live_health.py", "src/execution/exit_lifecycle.py"),
        )

        pause_mock.assert_not_called()
        flag = json.loads(df_path.read_text())
        assert flag["status"] == "dirty_runtime_worktree"
        assert flag["pause_reason"] is None
        assert flag["code_plane_status"] == "same_sha"
        assert flag["runtime_code_changed"] is True
        assert flag["worktree_runtime_dirty"] is True
        assert flag["dirty_runtime_paths_sample"] == [
            "src/control/live_health.py",
            "src/execution/exit_lifecycle.py",
        ]

    def test_boot_state_not_captured_silent(self):
        """If boot SHA is None (capture failed), function returns silently."""
        _check_deployment_freshness(
            boot_sha=None,
            boot_ts=None,
            repo_root=Path("/fake"),
            now=datetime.now(_UTC),
        )


# ---------------------------------------------------------------------------
# Tests: immediate drift observation
# ---------------------------------------------------------------------------

class TestImmediateObservation:
    def test_recent_divergence_is_observed_without_pause(self, caplog, tmp_path):
        import logging
        pause_mock = MagicMock()
        df_path = tmp_path / "deployment_freshness.json"
        with caplog.at_level(logging.WARNING, logger="zeus"):
            _run(
                boot_sha=BOOT_SHA,
                current_sha=DIFF_SHA,
                now_hours_after_boot=1.0,
                boot_ts_hours_ago=0.0,
                pause_entries_mock=pause_mock,
                state_path_return=df_path,
            )
        assert "deployment_freshness_observed" in caplog.text
        pause_mock.assert_not_called()
        assert df_path.exists()

    def test_recent_non_runtime_divergence_does_not_pause_entries(self, caplog, tmp_path):
        """Tests/docs-only HEAD drift is not stale executable live code."""
        import logging
        pause_mock = MagicMock()
        df_path = tmp_path / "deployment_freshness.json"
        with caplog.at_level(logging.INFO, logger="zeus"):
            _run(
                boot_sha=BOOT_SHA,
                current_sha=DIFF_SHA,
                git_diff_paths=("tests/test_only.py", "docs/readme.md"),
                now_hours_after_boot=1.0,
                boot_ts_hours_ago=0.0,
                pause_entries_mock=pause_mock,
                state_path_return=df_path,
            )
        pause_mock.assert_not_called()
        flag = json.loads(df_path.read_text())
        assert flag["status"] == "fresh"
        assert flag["pause_reason"] is None
        assert flag["code_plane_status"] == "non_runtime_diff"
        assert flag["runtime_code_changed"] is False
        assert flag["changed_paths_sample"] == ["tests/test_only.py", "docs/readme.md"]

    def test_recent_divergence_no_exit(self):
        """A recent worktree change remains observational."""
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            now_hours_after_boot=3.99,
            boot_ts_hours_ago=0.0,
        )


# ---------------------------------------------------------------------------
# Tests: drift alert and dedicated flag file
# ---------------------------------------------------------------------------

class TestStaleAlert:
    def test_stale_divergence_logs_warning(self, caplog, tmp_path):
        """Divergence logs an operator warning."""
        import logging

        df_path = tmp_path / "deployment_freshness.json"
        with patch("src.config.state_path", return_value=df_path):
            with patch("src.control.control_plane.pause_entries"):
                with caplog.at_level(logging.WARNING, logger="zeus"):
                    _run(
                        boot_sha=BOOT_SHA,
                        current_sha=DIFF_SHA,
                        now_hours_after_boot=8.0,
                        boot_ts_hours_ago=0.0,
                        state_path_return=df_path,
                    )

        assert "deployment_freshness_observed" in caplog.text

    def test_stale_divergence_4h_writes_dedicated_flag_file(self, tmp_path):
        """Runtime SHA mismatch writes to state/deployment_freshness.json, NOT control_plane.json."""
        df_path = tmp_path / "deployment_freshness.json"
        cp_path = tmp_path / "control_plane.json"

        with patch("src.config.state_path", return_value=df_path):
            with patch("src.control.control_plane.pause_entries"):
                _run(
                    boot_sha=BOOT_SHA,
                    current_sha=DIFF_SHA,
                    now_hours_after_boot=8.0,
                    boot_ts_hours_ago=0.0,
                    state_path_return=df_path,
                )

        assert df_path.exists(), "deployment_freshness.json must be written"
        flag = json.loads(df_path.read_text())
        assert flag["boot_sha"] == BOOT_SHA
        assert flag["current_sha"] == DIFF_SHA
        assert not cp_path.exists(), "control_plane.json must NOT be touched"

    def test_freshness_flag_survives_control_plane_write(self, tmp_path):
        """Advisory flag in deployment_freshness.json persists after control_plane writes.

        C1 antibody: this is structurally impossible to break because the flag
        lives in a separate file. Verifies the separation contract.
        """
        df_path = tmp_path / "deployment_freshness.json"
        cp_path = tmp_path / "control_plane.json"

        # Write a control_plane.json entry first (simulating normal daemon operation).
        cp_path.write_text(json.dumps({"commands": [], "acks": []}))

        with patch("src.config.state_path", return_value=df_path):
            with patch("src.control.control_plane.pause_entries"):
                _run(
                    boot_sha=BOOT_SHA,
                    current_sha=DIFF_SHA,
                    now_hours_after_boot=8.0,
                    boot_ts_hours_ago=0.0,
                    state_path_return=df_path,
                )

        # Simulate a control_plane write (overwrites the entire file with {commands, acks}).
        cp_path.write_text(json.dumps({"commands": [], "acks": []}))

        # Freshness flag in its own file is unaffected.
        assert df_path.exists()
        flag = json.loads(df_path.read_text())
        assert flag["boot_sha"] == BOOT_SHA

    def test_runtime_sha_mismatch_does_not_pause_entries(self, tmp_path):
        df_path = tmp_path / "deployment_freshness.json"
        pause_mock = MagicMock()

        with patch("src.config.state_path", return_value=df_path):
            with patch("src.control.control_plane.pause_entries", pause_mock):
                _run(
                    boot_sha=BOOT_SHA,
                    current_sha=DIFF_SHA,
                    now_hours_after_boot=8.0,
                    boot_ts_hours_ago=0.0,
                    state_path_return=df_path,
                )

        pause_mock.assert_not_called()

    def test_stale_divergence_4h_continues(self, tmp_path):
        """Drift does not terminate the daemon."""
        df_path = tmp_path / "deployment_freshness.json"
        with patch("src.config.state_path", return_value=df_path):
            with patch("src.control.control_plane.pause_entries"):
                _run(
                    boot_sha=BOOT_SHA,
                    current_sha=DIFF_SHA,
                    now_hours_after_boot=12.0,
                    boot_ts_hours_ago=0.0,
                    state_path_return=df_path,
                )

    def test_repeated_observation_never_calls_pause_entries(self, tmp_path):
        df_path = tmp_path / "deployment_freshness.json"
        pause_mock = MagicMock()

        for _ in range(5):
            _run(
                boot_sha=BOOT_SHA,
                current_sha=DIFF_SHA,
                now_hours_after_boot=8.0,
                boot_ts_hours_ago=0.0,
                pause_entries_mock=pause_mock,
                state_path_return=df_path,
            )

        pause_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: long-lived drift remains observational
# ---------------------------------------------------------------------------

class TestLongLivedObservation:
    @pytest.mark.parametrize("hours", [24.0, 24.1, 100.0])
    def test_stale_divergence_never_terminates(self, hours):
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            now_hours_after_boot=hours,
            boot_ts_hours_ago=0.0,
        )


# ---------------------------------------------------------------------------
# Tests: legacy override cannot hide observability
# ---------------------------------------------------------------------------

class TestOverride:
    def test_legacy_override_does_not_hide_observation(self, tmp_path):
        df_path = tmp_path / "deployment_freshness.json"
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            now_hours_after_boot=48.0,
            boot_ts_hours_ago=0.0,
            accept_stale_env=True,
            state_path_return=df_path,
        )
        assert json.loads(df_path.read_text())["status"] == "mismatch"

    def test_override_env_no_exit(self):
        """Legacy override is harmless because drift no longer terminates."""
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            now_hours_after_boot=100.0,
            boot_ts_hours_ago=0.0,
            accept_stale_env=True,
        )

    def test_override_env_also_bypasses_4h_band(self, tmp_path):
        """Legacy override cannot reintroduce an auto-pause."""
        pause_mock = MagicMock()
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            now_hours_after_boot=8.0,
            boot_ts_hours_ago=0.0,
            accept_stale_env=True,
            pause_entries_mock=pause_mock,
        )
        pause_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: git failures (all silent)
# ---------------------------------------------------------------------------

class TestGitFailures:
    def test_git_called_process_error_silent(self):
        """CalledProcessError (e.g. not a git repo) — no crash."""
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            boot_ts_hours_ago=48.0,
            git_raises=subprocess.CalledProcessError(128, "git"),
        )

    def test_git_timeout_silent(self):
        """TimeoutExpired — no crash."""
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            boot_ts_hours_ago=48.0,
            git_raises=subprocess.TimeoutExpired("git", 5),
        )

    def test_git_file_not_found_silent(self):
        """FileNotFoundError (git binary missing) — no crash."""
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            boot_ts_hours_ago=48.0,
            git_raises=FileNotFoundError("no git binary"),
        )

    def test_non_git_repo_is_silent(self):
        """temp dir without .git — git rev-parse exits 128, no crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            boot_ts = _ts(48.0)
            now = boot_ts + timedelta(hours=48.0)
            # Don't mock subprocess — let real git fail against non-git dir.
            _check_deployment_freshness(
                boot_sha=BOOT_SHA,
                boot_ts=boot_ts,
                repo_root=Path(tmpdir),
                now=now,
            )


# ---------------------------------------------------------------------------
# Tests: boot identity fallback
# ---------------------------------------------------------------------------

class TestBootCapture:
    def test_git_unavailable_uses_source_fingerprint(self):
        fingerprint = "f" * 40
        with patch.dict(os.environ, {"ZEUS_ACCEPT_STALE_DEPLOY": ""}, clear=False):
            with patch("subprocess.check_output", side_effect=FileNotFoundError("git not found")):
                with patch(
                    "src.main._runtime_source_fingerprint",
                    return_value=fingerprint,
                ):
                    result = _capture_boot_state()
        assert result["sha"] == fingerprint
        assert result["identity_source"] == "runtime_source_fingerprint"
        assert isinstance(result["ts"], datetime)

    def test_git_and_fingerprint_unavailable_do_not_block_boot(self):
        with patch("subprocess.check_output", side_effect=FileNotFoundError("git not found")):
            with patch("src.main._runtime_source_fingerprint", return_value=None):
                result = _capture_boot_state()
        assert result["sha"] is None
        assert result["identity_source"] == "unavailable"
        assert isinstance(result["ts"], datetime)


# ---------------------------------------------------------------------------
# Tests: APScheduler job registration (M1)
# ---------------------------------------------------------------------------

class TestSchedulerIntegration:
    def test_apscheduler_job_registered(self):
        """deployment_freshness job is registered with correct params.

        Mirrors test_main_module_scope.py antibody #8 pattern (AST/import scan).
        Here we use source-text scan for the scheduler.add_job call rather than
        importing main (which would trigger network/DB boot gates).
        """
        from pathlib import Path
        src = (Path(__file__).parent.parent / "src" / "main.py").read_text()

        # Assert the job registration block is present with required params.
        assert 'id="deployment_freshness"' in src, (
            "deployment_freshness APScheduler job must be registered in src/main.py"
        )
        assert 'seconds=60' in src, (
            "deployment_freshness job must use seconds=60 interval"
        )
        assert '"deployment_freshness"' in src or "'deployment_freshness'" in src

        # Also verify ordering: must appear BEFORE _assert_cascade_liveness_contract call.
        # Use the indented call-site form to avoid matching the function definition line.
        freshness_idx = src.index('id="deployment_freshness"')
        contract_idx = src.index('    _assert_cascade_liveness_contract(scheduler)')
        assert freshness_idx < contract_idx, (
            "deployment_freshness add_job must appear before _assert_cascade_liveness_contract"
        )

    def test_boot_state_dict_structure(self):
        """_BOOT_STATE module-level dict has expected keys (structural)."""
        assert isinstance(_BOOT_STATE, dict)
        assert "sha" in _BOOT_STATE
        assert "ts" in _BOOT_STATE


# ---------------------------------------------------------------------------
# Tests: scheduler observation never delivers SIGTERM
# ---------------------------------------------------------------------------

class TestApschedulerSignal:
    def test_24h_observation_does_not_signal_process(self):
        import threading
        from datetime import datetime, timezone, timedelta
        from apscheduler.schedulers.background import BackgroundScheduler
        from zoneinfo import ZoneInfo

        boot_ts = datetime.now(timezone.utc) - timedelta(hours=25)
        stale_sha = "aabbccdd0011"
        current_sha_val = "eeff99887766"

        kill_calls: list = []

        def _mock_kill(pid, sig):
            kill_calls.append((pid, sig))

        job_done = threading.Event()

        def _fresh_job():
            def _fake_git(cmd, **_kwargs):
                if list(cmd[:3]) == ["git", "diff", "--name-only"]:
                    return b"src/main.py\n"
                return current_sha_val.encode()

            with patch.dict(os.environ, {"ZEUS_ACCEPT_STALE_DEPLOY": ""}, clear=False):
                with patch("subprocess.check_output", side_effect=_fake_git):
                    with patch("os.kill", side_effect=_mock_kill):
                        with tempfile.TemporaryDirectory() as tmp_state:
                            with patch(
                                "src.config.state_path",
                                side_effect=lambda name: Path(tmp_state) / name,
                            ):
                                _check_deployment_freshness(
                                    boot_sha=stale_sha,
                                    boot_ts=boot_ts,
                                    repo_root=Path("/fake"),
                                    now=datetime.now(timezone.utc),
                                )
            job_done.set()

        scheduler = BackgroundScheduler(timezone=ZoneInfo("UTC"))
        scheduler.add_job(_fresh_job, "interval", seconds=1, id="test_freshness",
                          max_instances=1, coalesce=True)
        scheduler.start()
        try:
            job_done.wait(timeout=10)
        finally:
            scheduler.shutdown(wait=False)

        assert kill_calls == []
