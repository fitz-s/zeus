# Lifecycle: created=2026-05-17; last_reviewed=2026-07-24; last_reused=2026-07-24
# Purpose: Relationship coverage for ingest_main scheduler job identity and source-clock timing.
# Reuse: Run when ingest_main scheduler jobs, trigger times, or startup catch-up wiring change.
# Authority basis: F35 + F9 structural fixes — oracle bridge and calibration
#                  auto-promote jobs added to ingest_main APScheduler.
#                  2026-06-09: oracle snapshot listener promoted to scheduler
#                  (antibodies: snapshot job registered; fail-loud on script missing/failing).
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
from unittest.mock import MagicMock, patch

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
# HKO RHRREAD publication-clock relationship
# ---------------------------------------------------------------------------

def test_daily_obs_runs_after_hko_rhrread_publication_without_duplicate_writer() -> None:
    """RELATIONSHIP: HKO's hourly :02 publication gets one :05 daily-obs writer."""
    import src.ingest_main as im

    daily_obs_specs = [
        (trigger, kwargs)
        for func, trigger, kwargs in im._ingest_main_job_specs()
        if func is im._k2_daily_obs_tick
    ]

    assert daily_obs_specs == [
        (
            "cron",
            {
                "minute": 5,
                "id": "ingest_k2_daily_obs",
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 1800,
            },
        )
    ]


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
        assert jobs["ingest_oracle_bridge"].executor == "io"

    def test_ingest_oracle_bridge_startup_catch_up_registered(self) -> None:
        """Boot catch-up must be registered so missed daily cron ticks recover."""
        job_ids, jobs = _build_scheduler_jobs(return_jobs=True)
        assert "ingest_oracle_bridge_startup_catch_up" in job_ids, (
            f"Expected ingest_oracle_bridge_startup_catch_up in scheduler jobs; got: {job_ids}"
        )
        assert jobs["ingest_oracle_bridge_startup_catch_up"].executor == "io"

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


def test_live_ingest_recalibration_never_runs_diagnostic_replay(monkeypatch) -> None:
    import src.ingest_main as im

    commands: list[list[str]] = []

    def _run(command, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(im, "_etl_subprocess_python", lambda: sys.executable)
    monkeypatch.setattr(
        "src.state.db_writer_lock.subprocess_run_with_write_class",
        _run,
    )

    im._etl_recalibrate_body()

    assert [Path(command[1]).name for command in commands] == [
        "etl_diurnal_curves.py",
        "etl_temp_persistence.py",
    ]
    assert all("run_replay.py" not in command for command in commands)


# ---------------------------------------------------------------------------
# Oracle snapshot antibodies — 2026-06-09 outage post-mortem
# ---------------------------------------------------------------------------

class TestOracleSnapshotScheduled:
    """Antibody: oracle_snapshot_listener must run daily via ingest_main, not crontab.

    Root cause of 2026-06-09 outage: cron entry for oracle_snapshot_listener.py
    was commented out (ZEUS_MIGRATION_PAUSED_20260605) during home-repo migration.
    The bridge (ingest_oracle_bridge) continued running and regenerating
    oracle_error_rates.json from canonical DB data, masking the snapshot stoppage.
    Fix: promote snapshot listener to the same APScheduler in ingest_main.py
    (job id: ingest_oracle_snapshot, 10:00 UTC daily).
    """

    def test_ingest_oracle_snapshot_job_registered(self) -> None:
        """ingest_oracle_snapshot must appear in the scheduler job list at startup."""
        job_ids, jobs = _build_scheduler_jobs(return_jobs=True)
        assert "ingest_oracle_snapshot" in job_ids, (
            f"ingest_oracle_snapshot absent from scheduler jobs; got: {job_ids}\n"
            "Likely regression: job was removed or renamed in ingest_main.py."
        )
        assert jobs["ingest_oracle_snapshot"].executor == "io"

    def test_ingest_oracle_snapshot_runs_before_bridge(self) -> None:
        """Snapshot job must fire at 10:00 UTC, bridge at 10:05 UTC — order guarantees snapshot is present."""
        _, jobs = _build_scheduler_jobs(return_jobs=True)
        snap = jobs.get("ingest_oracle_snapshot")
        bridge = jobs.get("ingest_oracle_bridge")
        assert snap is not None, "ingest_oracle_snapshot not registered"
        assert bridge is not None, "ingest_oracle_bridge not registered"
        # Both are cron triggers; compare hour+minute fields
        snap_trigger = snap.trigger
        bridge_trigger = bridge.trigger
        import re
        # Trigger repr contains 'hour=10, minute=0' style strings.
        # Extract the scheduled minute from the repr to assert ordering.
        snap_repr = repr(snap_trigger)
        bridge_repr = repr(bridge_trigger)
        snap_minute = int(re.search(r"minute='?(\d+)'?", snap_repr).group(1))  # type: ignore[union-attr]
        bridge_minute = int(re.search(r"minute='?(\d+)'?", bridge_repr).group(1))  # type: ignore[union-attr]
        assert snap_minute < bridge_minute, (
            f"Snapshot job (minute={snap_minute}) must fire before bridge (minute={bridge_minute}); "
            "snapshot must land before bridge reads comparisons."
        )

    def test_snapshot_subprocess_single_writer(self) -> None:
        """RELATIONSHIP: concurrent snapshot ticks cannot spawn two WU fetch processes."""
        import src.ingest_main as im
        assert im._ORACLE_SNAPSHOT_LOCK.acquire(blocking=False)
        try:
            assert im._run_oracle_snapshot_script() == "skipped_lock_held"
        finally:
            im._ORACLE_SNAPSHOT_LOCK.release()

    def test_snapshot_script_missing_logs_warning_not_exception(self, tmp_path: Path) -> None:
        """RELATIONSHIP: missing oracle_snapshot_listener.py logs WARNING, does not raise.

        Antibody: fail-loud-not-fail-soft means we log WARNING (visible in
        scheduler_jobs_health.json) but never let the tick raise an exception
        that would kill subsequent scheduler ticks.
        """
        import src.ingest_main as im
        warnings: list[str] = []
        with patch.object(
            im.logger, "warning", side_effect=lambda msg, *a, **k: warnings.append(msg % a)
        ):
            with patch("src.ingest_main._etl_subprocess_python", return_value="/nonexistent/python"):
                # Patch Path.exists to simulate missing script
                with patch.object(Path, "exists", return_value=False):
                    result = im._run_oracle_snapshot_script()

        assert result == "missing_script", f"Expected missing_script, got {result!r}"
        assert any("ORACLE_SNAPSHOT_TICK" in w for w in warnings), (
            f"Expected WARNING with ORACLE_SNAPSHOT_TICK tag; got: {warnings}"
        )

    def test_snapshot_subprocess_failure_logs_warning_not_exception(self) -> None:
        """RELATIONSHIP: subprocess non-zero exit logs WARNING (fail-loud), does not raise."""
        import src.ingest_main as im

        failed = MagicMock()
        failed.returncode = 1
        failed.stdout = ""
        failed.stderr = "WU_API_KEY not set"

        warnings: list[str] = []
        with (
            patch("subprocess.run", return_value=failed),
            patch.object(Path, "exists", return_value=True),
            patch.object(
                im.logger, "warning",
                side_effect=lambda msg, *a, **k: warnings.append(msg % a),
            ),
        ):
            result = im._run_oracle_snapshot_script()

        assert result == "failed_subprocess"
        assert any("ORACLE_SNAPSHOT_TICK" in w for w in warnings)


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
