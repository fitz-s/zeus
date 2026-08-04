# Lifecycle: created=2026-05-17; last_reviewed=2026-08-04; last_reused=2026-08-04
# Purpose: Relationship coverage for ingest_main scheduler job identity and source-clock timing.
# Reuse: Run when ingest_main scheduler jobs, trigger times, or startup catch-up wiring change.
# Authority basis: F35 oracle bridge plus single-live scheduler semantics; the retired
#                  calibration auto-promoter must have no callable or scheduler registration.
#                  2026-06-09: oracle snapshot listener promoted to scheduler
#                  (antibodies: snapshot job registered; fail-loud on script missing/failing).
"""Tests for F35 + F9 ingest_main scheduler job registration and tick behaviour.

Antibody coverage:
  F35 — assert ingest_oracle_bridge job is registered after main() builds the scheduler.
        assert boot catch-up runs bridge when snapshots are newer than the artifact.
  F9  — the retired alternate calibration auto-promoter has no callable or scheduler job;
        the single-live artifact refit remains scheduled.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_scheduler_jobs(*, return_jobs: bool = False):
    """Run production main() through scheduler wiring while isolating independent DB gates."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    import src.ingest_main as im

    job_ids: list[str] = []
    jobs_by_id: dict[str, object] = {}

    def _capture_start(self) -> None:
        nonlocal job_ids, jobs_by_id
        jobs_by_id = {job.id: job for job in self.get_jobs()}
        job_ids = list(jobs_by_id)

    with (
        patch.object(BlockingScheduler, "start", _capture_start),
        patch.object(im, "_assert_world_schema_ready_for_ingest"),
        patch.object(im, "_assert_forecasts_schema_ready_for_ingest"),
        patch.object(im, "_write_world_schema_ready_sentinel"),
        patch.object(im, "_write_ingest_heartbeat"),
        patch.dict(os.environ, {"ZEUS_BOOT_REGISTRY_ASSERT_ENABLED": "0"}),
    ):
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


def test_hko_final_daily_poll_is_independent_and_bounded(monkeypatch) -> None:
    """Final HKO authority retries independently; it is not an hourly WU tail."""
    import src.ingest_main as im

    monkeypatch.delenv(im.HKO_DAILY_FINAL_POLL_SECONDS_ENV, raising=False)
    specs = [
        (trigger, kwargs)
        for func, trigger, kwargs in im._ingest_main_job_specs()
        if func is im._k2_hko_daily_final_tick
    ]

    assert len(specs) == 1
    trigger, kwargs = specs[0]
    assert trigger == "interval"
    assert kwargs["seconds"] == 300.0
    assert kwargs["id"] == "ingest_k2_hko_daily_final"
    assert kwargs["next_run_time"] is not None

    from src.data.scheduler_adapter import build_job_specs

    registry_spec = {
        spec.job_id: spec
        for spec in build_job_specs(owner_daemon="ingest_main")
    }["ingest_k2_hko_daily_final"]
    assert registry_spec.max_instances == 1
    assert registry_spec.coalesce is True
    assert registry_spec.misfire_grace_time == 600
    assert registry_spec.executor_class == "hko_final_source_clock_db"


def test_hko_final_daily_poll_has_own_lock_and_connection(monkeypatch) -> None:
    """A WU batch failure cannot prevent the source-correct HKO final poll."""
    import src.data.daily_obs_append as daily_obs
    import src.data.job_lock as job_lock
    import src.ingest_main as im
    import src.state.db as state_db

    calls: list[tuple[str, object]] = []
    conn = object()

    class ReadConnection:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            calls.append((f"{self.name}_close", True))

    @contextmanager
    def fake_lock(name: str):
        calls.append(("lock", name))
        yield True

    @contextmanager
    def fake_connection(*, write_class: str, blocking: bool):
        calls.append(("connection", (write_class, blocking)))
        yield conn

    def fake_append(
        given_conn,
        *,
        now_utc,
        rebuild_run_id,
        prefetched_by_month,
        prefetch_failures_by_month,
    ):
        calls.append(("append_conn", given_conn))
        calls.append(("rebuild_run_id", rebuild_run_id))
        calls.append(("prefetched_by_month", prefetched_by_month))
        calls.append(("prefetch_failures_by_month", prefetch_failures_by_month))
        assert now_utc.tzinfo is not None
        return {
            "inserted": 0,
            "already_present": 1,
            "not_published": 0,
            "guard_rejected": 0,
            "fetch_errors": 0,
        }

    monkeypatch.setattr(job_lock, "acquire_lock", fake_lock)
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_with_world",
        fake_connection,
    )
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        lambda: calls.append(("forecasts_read", True))
        or ReadConnection("forecasts_read"),
    )
    monkeypatch.setattr(
        state_db,
        "get_world_connection_read_only",
        lambda: calls.append(("world_read", True))
        or ReadConnection("world_read"),
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_missing_dates",
        lambda *_args, **_kwargs: (
            calls.append(("missing", (date(2026, 7, 28),)))
            or (date(2026, 7, 28),)
        ),
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_coverage_repair_dates",
        lambda *_args, **_kwargs: calls.append(("coverage_repair", ())) or (),
    )
    prefetched = ({(2026, 7, 28): (29.5, 25.1)}, "url", "sha256:x")
    monkeypatch.setattr(
        daily_obs,
        "_fetch_hko_daily_extract_month",
        lambda *_args: calls.append(("fetch", True)) or prefetched,
    )
    monkeypatch.setattr(
        daily_obs,
        "append_hko_daily_extract_recent",
        fake_append,
    )

    result = im._k2_hko_daily_final_tick.__wrapped__()

    assert result["already_present"] == 1
    assert calls[:10] == [
        ("lock", "hko_daily_final"),
        ("forecasts_read", True),
        ("world_read", True),
        ("missing", (date(2026, 7, 28),)),
        ("coverage_repair", ()),
        ("world_read_close", True),
        ("forecasts_read_close", True),
        ("fetch", True),
        ("connection", ("bulk", False)),
        ("append_conn", conn),
    ]
    assert str(calls[10][1]).startswith("hko_daily_final_")
    assert calls[11] == ("prefetched_by_month", {(2026, 7): prefetched})
    assert calls[12] == ("prefetch_failures_by_month", {})


def test_hko_final_daily_poll_is_local_noop_after_publication(monkeypatch) -> None:
    import src.data.daily_obs_append as daily_obs
    import src.data.job_lock as job_lock
    import src.ingest_main as im
    import src.state.db as state_db

    @contextmanager
    def fake_lock(_name: str):
        yield True

    class ReadConnection:
        def close(self) -> None:
            return None

    monkeypatch.setattr(job_lock, "acquire_lock", fake_lock)
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        ReadConnection,
    )
    monkeypatch.setattr(
        state_db,
        "get_world_connection_read_only",
        ReadConnection,
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_missing_dates",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_coverage_repair_dates",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        daily_obs,
        "_fetch_hko_daily_extract_month",
        lambda *_args: pytest.fail("published row must avoid provider fetch"),
    )
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_with_world",
        lambda **_kwargs: pytest.fail("published row must avoid writer admission"),
    )

    result = im._k2_hko_daily_final_tick.__wrapped__()

    assert result["already_present"] == 7


def test_hko_final_daily_poll_repairs_coverage_without_provider_fetch(
    monkeypatch,
) -> None:
    import src.data.daily_obs_append as daily_obs
    import src.data.job_lock as job_lock
    import src.ingest_main as im
    import src.state.db as state_db

    calls: list[tuple[str, object]] = []
    writer_conn = object()

    @contextmanager
    def fake_lock(_name: str):
        yield True

    class ReadConnection:
        def close(self) -> None:
            return None

    @contextmanager
    def fake_connection(*, write_class: str, blocking: bool):
        assert write_class == "bulk"
        assert blocking is False
        calls.append(("writer", True))
        yield writer_conn

    monkeypatch.setattr(job_lock, "acquire_lock", fake_lock)
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        ReadConnection,
    )
    monkeypatch.setattr(
        state_db,
        "get_world_connection_read_only",
        ReadConnection,
    )
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_with_world",
        fake_connection,
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_missing_dates",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_coverage_repair_dates",
        lambda *_args, **_kwargs: (date(2026, 7, 24),),
    )
    monkeypatch.setattr(
        daily_obs,
        "_fetch_hko_daily_extract_month",
        lambda *_args: pytest.fail("coverage-only repair must not fetch HKO"),
    )

    def fake_append(given_conn, **kwargs):
        assert given_conn is writer_conn
        assert kwargs["prefetched_by_month"] == {}
        assert kwargs["prefetch_failures_by_month"] == {}
        calls.append(("append", True))
        return {
            "inserted": 0,
            "already_present": 7,
            "not_published": 0,
            "guard_rejected": 0,
            "fetch_errors": 0,
        }

    monkeypatch.setattr(
        daily_obs,
        "append_hko_daily_extract_recent",
        fake_append,
    )

    result = im._k2_hko_daily_final_tick.__wrapped__()

    assert result["already_present"] == 7
    assert calls == [("writer", True), ("append", True)]


def test_hko_final_daily_poll_surfaces_source_or_write_failure(monkeypatch) -> None:
    import src.data.daily_obs_append as daily_obs
    import src.data.job_lock as job_lock
    import src.ingest_main as im
    import src.state.db as state_db

    calls = []

    @contextmanager
    def fake_lock(_name: str):
        yield True

    class ReadConnection:
        def close(self) -> None:
            return None

    @contextmanager
    def fake_connection(*, write_class: str, blocking: bool):
        assert write_class == "bulk"
        assert blocking is False
        yield object()

    monkeypatch.setattr(job_lock, "acquire_lock", fake_lock)
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_with_world",
        fake_connection,
    )
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        ReadConnection,
    )
    monkeypatch.setattr(
        state_db,
        "get_world_connection_read_only",
        ReadConnection,
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_missing_dates",
        lambda *_args, **_kwargs: (
            date(2026, 8, 1),
            date(2026, 7, 31),
        ),
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_coverage_repair_dates",
        lambda *_args, **_kwargs: (),
    )

    def fake_fetch(year, month):
        calls.append(("fetch", (year, month)))
        if month == 7:
            raise RuntimeError("simulated July outage")
        return ({(2026, 8, 1): (29.5, 25.1)}, "url", "sha256:x")

    monkeypatch.setattr(
        daily_obs,
        "_fetch_hko_daily_extract_month",
        fake_fetch,
    )

    def fake_append(*_args, **kwargs):
        calls.append(("prefetched", kwargs["prefetched_by_month"]))
        calls.append(("failures", kwargs["prefetch_failures_by_month"]))
        return {
            "inserted": 1,
            "already_present": 0,
            "not_published": 0,
            "guard_rejected": 0,
            "fetch_errors": 1,
        }

    monkeypatch.setattr(
        daily_obs,
        "append_hko_daily_extract_recent",
        fake_append,
    )

    with pytest.raises(RuntimeError, match="HKO_DAILY_FINAL_POLL_FAILED"):
        im._k2_hko_daily_final_tick.__wrapped__()
    assert set(calls[:2]) == {
        ("fetch", (2026, 7)),
        ("fetch", (2026, 8)),
    }
    assert calls[2] == (
        "prefetched",
        {
            (2026, 8): (
                {(2026, 8, 1): (29.5, 25.1)},
                "url",
                "sha256:x",
            )
        },
    )
    assert calls[3] == (
        "failures",
        {(2026, 7): "RuntimeError: simulated July outage"},
    )


def test_hko_final_daily_poll_defers_without_waiting_on_writer(monkeypatch) -> None:
    import src.data.daily_obs_append as daily_obs
    import src.data.job_lock as job_lock
    import src.ingest_main as im
    import src.state.db as state_db

    @contextmanager
    def fake_lock(_name: str):
        yield True

    class ReadConnection:
        def close(self) -> None:
            return None

    @contextmanager
    def contended_connection(*, write_class: str, blocking: bool):
        assert write_class == "bulk"
        assert blocking is False
        raise BlockingIOError("writer busy")
        yield  # pragma: no cover

    monkeypatch.setattr(job_lock, "acquire_lock", fake_lock)
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_with_world",
        contended_connection,
    )
    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        ReadConnection,
    )
    monkeypatch.setattr(
        state_db,
        "get_world_connection_read_only",
        ReadConnection,
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_missing_dates",
        lambda *_args, **_kwargs: (date(2026, 7, 28),),
    )
    monkeypatch.setattr(
        daily_obs,
        "hko_daily_extract_recent_coverage_repair_dates",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        daily_obs,
        "_fetch_hko_daily_extract_month",
        lambda *_args: ({}, "url", "sha256:x"),
    )

    assert im._k2_hko_daily_final_tick.__wrapped__() == {
        "status": "WRITE_CONTENDED"
    }


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
        assert jobs["ingest_oracle_bridge"].executor == "health_io"

    def test_ingest_oracle_bridge_startup_catch_up_registered(self) -> None:
        """Boot catch-up must be registered so missed daily cron ticks recover."""
        job_ids, jobs = _build_scheduler_jobs(return_jobs=True)
        assert "ingest_oracle_bridge_startup_catch_up" in job_ids, (
            f"Expected ingest_oracle_bridge_startup_catch_up in scheduler jobs; got: {job_ids}"
        )
        assert jobs["ingest_oracle_bridge_startup_catch_up"].executor == "health_io"

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
        assert jobs["ingest_oracle_snapshot"].executor == "health_io"

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


class TestSingleLiveCalibrationJobs:
    def test_retired_auto_promote_has_no_callable_or_registration(self) -> None:
        """The alternate promoter cannot be revived by a stale flag or scheduler reference."""
        import src.ingest_main as im

        job_ids = {str(kwargs["id"]) for _fn, _trigger, kwargs in im._ingest_main_job_specs()}
        assert not hasattr(im, "_calibration_auto_promote_tick")
        assert "ingest_calibration_auto_promote" not in job_ids
        assert "ingest_artifact_refit" in job_ids
