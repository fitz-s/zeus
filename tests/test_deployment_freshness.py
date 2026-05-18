# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: 2026-05-17 cascade incident (daemon ran 5h+ on pre-PR-#139 abs() code after merge)
#                  + memory feedback_pr_operations_delegate_to_git_master.md
"""Antibody tests for PR-S6 deployment freshness gate (_check_deployment_freshness).

Verifies that stale daemon detection fails-closed at 24h, warns at 4-24h,
logs-only under 4h, and is silent on git failures and non-git repos.
"""

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import src.main as main_module
from src.main import _check_deployment_freshness, _BOOT_STATE


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
    accept_stale_env: bool = False,
    now_hours_after_boot: float | None = None,
    repo_root: Path | None = None,
    git_raises: Exception | None = None,
    **kwargs,
):
    """Run _check_deployment_freshness with controlled inputs."""
    boot_ts = _ts(boot_ts_hours_ago)
    if now_hours_after_boot is not None:
        now = boot_ts + timedelta(hours=now_hours_after_boot)
    else:
        now = datetime.now(_UTC)

    env_patch = {"ZEUS_ACCEPT_STALE_DEPLOY": "1"} if accept_stale_env else {}
    env_patch.setdefault("ZEUS_ACCEPT_STALE_DEPLOY", "")

    def fake_check_output(cmd, **kw):
        if git_raises:
            raise git_raises
        return current_sha.encode()

    with patch.dict(os.environ, env_patch, clear=False):
        with patch("subprocess.check_output", side_effect=fake_check_output):
            return _check_deployment_freshness(
                boot_sha=boot_sha,
                boot_ts=boot_ts,
                repo_root=Path("/fake/repo"),
                now=now,
                **kwargs,
            )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoAction:
    def test_no_divergence_passes(self):
        """Same SHA at boot and filesystem — no warning, no exit."""
        _run(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, boot_ts_hours_ago=10.0)

    def test_no_divergence_long_uptime_passes(self):
        """Even after 48h uptime, same SHA is fine."""
        _run(boot_sha=BOOT_SHA, current_sha=BOOT_SHA, boot_ts_hours_ago=48.0)

    def test_boot_state_not_captured_silent(self):
        """If boot SHA is None (capture failed), function returns silently."""
        _check_deployment_freshness(
            boot_sha=None,
            boot_ts=None,
            repo_root=Path("/fake"),
            now=datetime.now(_UTC),
        )


class TestGraceWindow:
    def test_recent_divergence_warns_only(self, caplog):
        """Divergence within 4h emits WARNING but no SystemExit."""
        import logging
        with caplog.at_level(logging.WARNING, logger="zeus"):
            _run(
                boot_sha=BOOT_SHA,
                current_sha=DIFF_SHA,
                now_hours_after_boot=1.0,
                boot_ts_hours_ago=0.0,
            )
        assert "grace window" in caplog.text or "deployment_freshness_diverged_total" in caplog.text

    def test_recent_divergence_no_exit(self):
        """No SystemExit within grace window."""
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            now_hours_after_boot=3.99,
            boot_ts_hours_ago=0.0,
        )


class TestStaleAlert:
    def test_stale_divergence_4h_logs_error(self, caplog, tmp_path):
        """Divergence >=4h and <24h logs ERROR and writes control_plane.json flag."""
        import logging

        # state_path is locally imported inside the function; patch via src.config.
        cp_path = tmp_path / "control_plane.json"

        with patch("src.config.state_path", return_value=cp_path):
            with caplog.at_level(logging.ERROR, logger="zeus"):
                _run(
                    boot_sha=BOOT_SHA,
                    current_sha=DIFF_SHA,
                    now_hours_after_boot=8.0,
                    boot_ts_hours_ago=0.0,
                )

        assert "deployment_freshness_diverged_total" in caplog.text
        assert cp_path.exists()
        flag = json.loads(cp_path.read_text())
        assert "deployment_freshness_warning" in flag
        assert flag["deployment_freshness_warning"]["boot_sha"] == BOOT_SHA
        assert flag["deployment_freshness_warning"]["current_sha"] == DIFF_SHA

    def test_stale_divergence_4h_continues(self, tmp_path):
        """4-24h divergence does NOT raise SystemExit."""
        cp_path = tmp_path / "control_plane.json"
        with patch("src.config.state_path", return_value=cp_path):
            _run(
                boot_sha=BOOT_SHA,
                current_sha=DIFF_SHA,
                now_hours_after_boot=12.0,
                boot_ts_hours_ago=0.0,
            )


class TestFailClosed:
    def test_stale_divergence_24h_raises(self):
        """Divergence >=24h raises SystemExit."""
        with pytest.raises(SystemExit) as exc_info:
            _run(
                boot_sha=BOOT_SHA,
                current_sha=DIFF_SHA,
                now_hours_after_boot=24.1,
                boot_ts_hours_ago=0.0,
            )
        assert "DEPLOYMENT_STALE" in str(exc_info.value)
        assert BOOT_SHA[:8] in str(exc_info.value)
        assert DIFF_SHA[:8] in str(exc_info.value)

    def test_stale_divergence_exact_24h_raises(self):
        """Boundary: exactly 24.0h raises."""
        with pytest.raises(SystemExit):
            _run(
                boot_sha=BOOT_SHA,
                current_sha=DIFF_SHA,
                now_hours_after_boot=24.0,
                boot_ts_hours_ago=0.0,
            )


class TestOverride:
    def test_override_env_bypasses_fail_closed(self, caplog):
        """ZEUS_ACCEPT_STALE_DEPLOY=1 skips the fail-closed path entirely."""
        import logging
        with caplog.at_level(logging.WARNING, logger="zeus"):
            _run(
                boot_sha=BOOT_SHA,
                current_sha=DIFF_SHA,
                now_hours_after_boot=48.0,
                boot_ts_hours_ago=0.0,
                accept_stale_env=True,
            )
        assert "ZEUS_ACCEPT_STALE_DEPLOY" in caplog.text

    def test_override_env_no_exit(self):
        """No SystemExit with override even at 100h divergence."""
        _run(
            boot_sha=BOOT_SHA,
            current_sha=DIFF_SHA,
            now_hours_after_boot=100.0,
            boot_ts_hours_ago=0.0,
            accept_stale_env=True,
        )


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
