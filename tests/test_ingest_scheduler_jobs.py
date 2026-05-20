# Created: 2026-05-17
# Last reused or audited: 2026-05-20
# Authority basis: F35 + F9 structural fixes — oracle bridge and calibration
#                  auto-promote jobs added to ingest_main APScheduler.
"""Tests for F35 + F9 ingest_main scheduler job registration and tick behaviour.

Antibody coverage:
  F35 — assert ingest_oracle_bridge job is registered after main() builds the scheduler.
        assert boot catch-up runs bridge when snapshots are newer than the artifact.
  F9  — (a) auto-promote tick does NOT call promote when inspect exits non-zero (NOT READY)
        (b) auto-promote tick DOES call promote when inspect exits 0 (READY)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_scheduler_jobs(*, return_jobs: bool = False):
    """Run main() with BlockingScheduler.start patched to a no-op, return job IDs."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    job_ids: list[str] = []
    jobs_by_id: dict[str, Any] = {}

    def _noop_start(self: Any) -> None:  # noqa: ANN001
        nonlocal job_ids, jobs_by_id
        job_ids = [j.id for j in self.get_jobs()]
        jobs_by_id = {j.id: j for j in self.get_jobs()}

    with (
        patch.object(BlockingScheduler, "start", _noop_start),
        # prevent writing sentinel files during test
        patch("src.ingest_main._write_ingest_heartbeat"),
    ):
        import src.ingest_main as im
        im.main()

    if return_jobs:
        return job_ids, jobs_by_id
    return job_ids


# ---------------------------------------------------------------------------
# F35 antibody — scheduler registration
# ---------------------------------------------------------------------------

class TestF35OracleBridgeRegistered:
    def test_ingest_oracle_bridge_job_registered(self) -> None:
        """ingest_oracle_bridge must appear in the scheduler job list at startup."""
        job_ids, jobs = _build_scheduler_jobs(return_jobs=True)
        assert "ingest_oracle_bridge" in job_ids, (
            f"Expected ingest_oracle_bridge in scheduler jobs; got: {job_ids}"
        )
        assert jobs["ingest_oracle_bridge"].executor == "fast"

    def test_ingest_oracle_bridge_startup_catch_up_registered(self) -> None:
        """Boot catch-up must be registered so missed daily cron ticks recover."""
        job_ids, jobs = _build_scheduler_jobs(return_jobs=True)
        assert "ingest_oracle_bridge_startup_catch_up" in job_ids, (
            f"Expected ingest_oracle_bridge_startup_catch_up in scheduler jobs; got: {job_ids}"
        )
        assert jobs["ingest_oracle_bridge_startup_catch_up"].executor == "fast"

    def test_startup_catch_up_runs_when_snapshots_newer_than_artifact(self) -> None:
        """RELATIONSHIP: newer oracle snapshots at daemon boot -> bridge writer runs."""
        import src.ingest_main as im

        with (
            patch("src.ingest_main._latest_oracle_snapshot_mtime", return_value=200.0),
            patch("src.ingest_main._oracle_bridge_artifact_mtimes", return_value=(100.0, 100.0)),
            patch("src.ingest_main._run_bridge_oracle_script", return_value="ok") as mock_bridge,
        ):
            result = im._bridge_oracle_startup_catch_up.__wrapped__()

        assert result == {"status": "ran"}
        mock_bridge.assert_called_once_with()

    def test_startup_catch_up_skips_when_artifact_current(self) -> None:
        """RELATIONSHIP: current oracle artifact at daemon boot -> no bridge run."""
        import src.ingest_main as im

        with (
            patch("src.ingest_main._latest_oracle_snapshot_mtime", return_value=100.0),
            patch("src.ingest_main._oracle_bridge_artifact_mtimes", return_value=(200.0, 200.0)),
            patch("src.ingest_main._run_bridge_oracle_script") as mock_bridge,
        ):
            result = im._bridge_oracle_startup_catch_up.__wrapped__()

        assert result == {"status": "skipped_current"}
        mock_bridge.assert_not_called()

    def test_startup_catch_up_runs_when_only_heartbeat_is_current(self) -> None:
        """RELATIONSHIP: heartbeat freshness cannot mask stale oracle_error_rates."""
        import src.ingest_main as im

        with (
            patch("src.ingest_main._latest_oracle_snapshot_mtime", return_value=200.0),
            patch("src.ingest_main._oracle_bridge_artifact_mtimes", return_value=(100.0, 300.0)),
            patch("src.ingest_main._run_bridge_oracle_script", return_value="ok") as mock_bridge,
        ):
            result = im._bridge_oracle_startup_catch_up.__wrapped__()

        assert result == {"status": "ran"}
        mock_bridge.assert_called_once_with()

    def test_startup_catch_up_runs_when_required_artifact_is_missing(self) -> None:
        """RELATIONSHIP: both oracle JSON and heartbeat must exist before skip."""
        import src.ingest_main as im

        with (
            patch("src.ingest_main._latest_oracle_snapshot_mtime", return_value=200.0),
            patch("src.ingest_main._oracle_bridge_artifact_mtimes", return_value=(300.0,)),
            patch("src.ingest_main._run_bridge_oracle_script", return_value="ok") as mock_bridge,
        ):
            result = im._bridge_oracle_startup_catch_up.__wrapped__()

        assert result == {"status": "ran"}
        mock_bridge.assert_called_once_with()

    def test_oracle_bridge_subprocess_is_single_writer(self) -> None:
        """RELATIONSHIP: concurrent oracle bridge ticks cannot launch two writers."""
        import src.ingest_main as im

        assert im._ORACLE_BRIDGE_LOCK.acquire(blocking=False)
        try:
            assert im._run_bridge_oracle_script() == "skipped_lock_held"
        finally:
            im._ORACLE_BRIDGE_LOCK.release()

    def test_startup_catch_up_reports_lock_held(self) -> None:
        """RELATIONSHIP: boot catch-up reports lock contention instead of double-running."""
        import src.ingest_main as im

        with (
            patch("src.ingest_main._latest_oracle_snapshot_mtime", return_value=200.0),
            patch("src.ingest_main._oracle_bridge_artifact_mtimes", return_value=(100.0, 100.0)),
            patch("src.ingest_main._run_bridge_oracle_script", return_value="skipped_lock_held"),
        ):
            result = im._bridge_oracle_startup_catch_up.__wrapped__()

        assert result == {"status": "skipped_lock_held"}

    def test_startup_catch_up_reports_subprocess_failure(self) -> None:
        """RELATIONSHIP: bridge failures must not be mislabeled as lock contention."""
        import src.ingest_main as im

        with (
            patch("src.ingest_main._latest_oracle_snapshot_mtime", return_value=200.0),
            patch("src.ingest_main._oracle_bridge_artifact_mtimes", return_value=(100.0, 100.0)),
            patch("src.ingest_main._run_bridge_oracle_script", return_value="failed_subprocess"),
        ):
            result = im._bridge_oracle_startup_catch_up.__wrapped__()

        assert result == {"status": "failed_subprocess"}


# ---------------------------------------------------------------------------
# F9 antibodies — auto-promote guard and readiness gate
# ---------------------------------------------------------------------------

class TestF9CalibrationAutoPromote:
    """Tests for _calibration_auto_promote_tick behaviour."""

    def _get_tick(self):
        """Return the unwrapped tick function (bypass @_scheduler_job decorator)."""
        import src.ingest_main as im
        # _scheduler_job wraps with functools.wraps, so __wrapped__ reaches the inner fn
        return im._calibration_auto_promote_tick.__wrapped__

    def test_tick_skips_when_env_flag_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tick must not invoke any subprocess when ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED is unset."""
        monkeypatch.delenv("ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED", raising=False)
        monkeypatch.delenv("ZEUS_CALIBRATION_STAGE_DB_PATH", raising=False)

        with patch("subprocess.run") as mock_run:
            self._get_tick()()

        mock_run.assert_not_called()

    def test_tick_skips_when_stage_db_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tick must not invoke any subprocess when ZEUS_CALIBRATION_STAGE_DB_PATH is unset."""
        monkeypatch.setenv("ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED", "true")
        monkeypatch.delenv("ZEUS_CALIBRATION_STAGE_DB_PATH", raising=False)

        with patch("subprocess.run") as mock_run:
            self._get_tick()()

        mock_run.assert_not_called()

    def test_tick_does_not_promote_when_inspect_not_ready(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """(a) Tick must NOT invoke promote when inspect exits non-zero (NOT READY)."""
        stage_db = str(tmp_path / "stage.db")
        monkeypatch.setenv("ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED", "true")
        monkeypatch.setenv("ZEUS_CALIBRATION_STAGE_DB_PATH", stage_db)

        # inspect returns exit code 1 (sentinels not complete)
        not_ready_result = MagicMock()
        not_ready_result.returncode = 1
        not_ready_result.stdout = "x STATUS: NOT READY - sentinels not complete"
        not_ready_result.stderr = ""

        with (
            patch("subprocess.run", return_value=not_ready_result) as mock_run,
            patch(
                "src.state.db_writer_lock.subprocess_run_with_write_class"
            ) as mock_locked_run,
        ):
            self._get_tick()()

        # inspect subprocess called exactly once
        assert mock_run.call_count == 1
        assert "inspect" in mock_run.call_args[0][0]
        # promote subprocess must NOT be called
        mock_locked_run.assert_not_called()

    def test_tick_promotes_when_inspect_ready(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """(b) Tick MUST invoke promote --commit when inspect exits 0 (READY)."""
        stage_db = str(tmp_path / "stage.db")
        monkeypatch.setenv("ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED", "true")
        monkeypatch.setenv("ZEUS_CALIBRATION_STAGE_DB_PATH", stage_db)

        ready_result = MagicMock()
        ready_result.returncode = 0
        ready_result.stdout = "+ STATUS: READY for promote"
        ready_result.stderr = ""

        promote_result = MagicMock()
        promote_result.returncode = 0
        promote_result.stdout = "Promotion complete."
        promote_result.stderr = ""

        with (
            patch("subprocess.run", return_value=ready_result) as mock_inspect,
            patch(
                "src.state.db_writer_lock.subprocess_run_with_write_class",
                return_value=promote_result,
            ) as mock_promote,
        ):
            self._get_tick()()

        # inspect called once
        assert mock_inspect.call_count == 1
        assert "inspect" in mock_inspect.call_args[0][0]

        # promote --commit called once
        assert mock_promote.call_count == 1
        promote_cmd = mock_promote.call_args[0][0]
        assert "promote" in promote_cmd
        assert "--commit" in promote_cmd
        assert stage_db in promote_cmd


# ---------------------------------------------------------------------------
# F9 antibody — scheduler registration
# ---------------------------------------------------------------------------

class TestF9AutoPromoteRegistered:
    def test_ingest_calibration_auto_promote_job_registered(self) -> None:
        """ingest_calibration_auto_promote must appear in the scheduler job list at startup."""
        job_ids = _build_scheduler_jobs()
        assert "ingest_calibration_auto_promote" in job_ids, (
            f"Expected ingest_calibration_auto_promote in scheduler jobs; got: {job_ids}"
        )
