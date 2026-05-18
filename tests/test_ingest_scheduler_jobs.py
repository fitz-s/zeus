# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: F35 + F9 structural fixes — oracle bridge and calibration
#                  auto-promote jobs added to ingest_main APScheduler.
"""Tests for F35 + F9 ingest_main scheduler job registration and tick behaviour.

Antibody coverage:
  F35 — assert ingest_oracle_bridge job is registered after main() builds the scheduler.
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

def _build_scheduler_jobs() -> list[str]:
    """Run main() with BlockingScheduler.start patched to a no-op, return job IDs."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    job_ids: list[str] = []

    def _noop_start(self: Any) -> None:  # noqa: ANN001
        nonlocal job_ids
        job_ids = [j.id for j in self.get_jobs()]

    with (
        patch.object(BlockingScheduler, "start", _noop_start),
        # prevent writing sentinel files during test
        patch("src.ingest_main._write_ingest_heartbeat"),
    ):
        import src.ingest_main as im
        im.main()

    return job_ids


# ---------------------------------------------------------------------------
# F35 antibody — scheduler registration
# ---------------------------------------------------------------------------

class TestF35OracleBridgeRegistered:
    def test_ingest_oracle_bridge_job_registered(self) -> None:
        """ingest_oracle_bridge must appear in the scheduler job list at startup."""
        job_ids = _build_scheduler_jobs()
        assert "ingest_oracle_bridge" in job_ids, (
            f"Expected ingest_oracle_bridge in scheduler jobs; got: {job_ids}"
        )


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
