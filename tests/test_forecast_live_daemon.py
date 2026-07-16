# Created: 2026-05-14
# Last reused/audited: 2026-06-18
# Lifecycle: created=2026-05-14; last_reviewed=2026-06-18; last_reused=2026-06-18
# Authority basis: docs/archive/2026-Q2/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md section 6.1, section 6.2, section 8 Phase 4, and Phase 6 durable work journaling; docs/archive/2026-Q2/task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md source-health gate; fix/forecast-live-partial-retry 2026-05-19 (ECMWF incremental dissemination correction); a0d51d480b507f324 root-cause (ECMWF 00z ingest schedule fix — add 12z triggers, update FORECAST_LIVE_JOB_IDS).
# Purpose: Relationship tests for the forecast-live daemon boundary — job registry, lock semantics, journaling, and source-health probe.
# Reuse: Run when forecast_live_daemon.py job specs, run_opendata_track, or job journaling logic changes.
"""Relationship tests for the dedicated forecast-live daemon boundary."""

from __future__ import annotations

import ast
import importlib
import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.data.dual_run_lock import OPENDATA_DAEMON_LOCK_KEY, acquire_lock
from src.state.db import init_schema_forecasts


REPO_ROOT = Path(__file__).resolve().parents[1]
FORECAST_LIVE_DAEMON = REPO_ROOT / "src" / "ingest" / "forecast_live_daemon.py"


def test_forecast_live_scheduler_registers_only_opendata_jobs_and_heartbeat() -> None:
    from src.ingest.forecast_live_daemon import (
        FORECAST_LIVE_HEARTBEAT_JOB_ID,
        FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID,
        FORECAST_LIVE_SOURCE_HEALTH_JOB_ID,
        FORECAST_LIVE_JOB_IDS,
        forecast_live_job_specs,
    )
    specs = forecast_live_job_specs(
        startup_run_date=datetime(2026, 5, 14, 8, 0, tzinfo=timezone.utc)
    )
    job_ids = {kwargs["id"] for _, _, kwargs in specs}

    assert job_ids == FORECAST_LIVE_JOB_IDS
    assert job_ids == {
        "forecast_live_opendata_daily_mx2t6",        # 00z trigger (08:10 UTC)
        "forecast_live_opendata_daily_mx2t6_12z",    # 12z trigger (20:10 UTC)
        "forecast_live_opendata_daily_mn2t6",        # 00z trigger (08:15 UTC)
        "forecast_live_opendata_daily_mn2t6_12z",    # 12z trigger (20:15 UTC)
        "forecast_live_opendata_startup_catch_up",
        "forecast_live_opendata_safe_cycle_poll",
        "forecast_live_heartbeat",
        "forecast_live_source_health_probe",
    }
    heartbeat_specs = [
        (trigger, kwargs)
        for _, trigger, kwargs in specs
        if kwargs["id"] == FORECAST_LIVE_HEARTBEAT_JOB_ID
    ]
    assert heartbeat_specs == [
        (
            "interval",
            {
                "seconds": 30,
                "id": FORECAST_LIVE_HEARTBEAT_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 10,
                "executor": "heartbeat",
            },
        )
    ]
    assert not any("tigge" in job_id for job_id in job_ids)
    assert not any("calibrat" in job_id or "refit" in job_id for job_id in job_ids)
    assert not any("market" in job_id or "venue" in job_id for job_id in job_ids)
    source_health_specs = [
        (trigger, kwargs)
        for _, trigger, kwargs in specs
        if kwargs["id"] == FORECAST_LIVE_SOURCE_HEALTH_JOB_ID
    ]
    assert source_health_specs == [
        (
            "interval",
            {
                "seconds": 600,
                "id": FORECAST_LIVE_SOURCE_HEALTH_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 120,
                "next_run_time": datetime(2026, 5, 14, 8, 10, tzinfo=timezone.utc),
                "executor": "source_health",
            },
        )
    ]
    safe_cycle_specs = [
        (trigger, kwargs)
        for _, trigger, kwargs in specs
        if kwargs["id"] == FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID
    ]
    assert safe_cycle_specs == [
        (
            "interval",
            {
                "seconds": 300,
                "id": FORECAST_LIVE_SAFE_CYCLE_POLL_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 120,
            },
        )
    ]


def test_forecast_live_heartbeat_payload_uses_registered_scheduler_jobs(monkeypatch, tmp_path) -> None:
    import src.ingest.forecast_live_daemon as forecast_live_daemon
    from src.ingest.forecast_live_daemon import (
        REPLACEMENT_FORECAST_DOWNLOAD_JOB_ID,
        REPLACEMENT_FORECAST_MATERIALIZE_JOB_ID,
        _write_forecast_live_heartbeat,
    )

    class _Job:
        def __init__(self, job_id: str) -> None:
            self.id = job_id

    class _Scheduler:
        def get_jobs(self):
            return [
                _Job("forecast_live_heartbeat"),
                _Job(REPLACEMENT_FORECAST_DOWNLOAD_JOB_ID),
                _Job(REPLACEMENT_FORECAST_MATERIALIZE_JOB_ID),
            ]

    monkeypatch.setattr(forecast_live_daemon, "_scheduler", _Scheduler())
    heartbeat_path = tmp_path / "forecast-live-heartbeat.json"

    _write_forecast_live_heartbeat(
        heartbeat_path=heartbeat_path,
        status="scheduler_ready",
        now_utc=datetime(2026, 6, 18, 22, 0, tzinfo=timezone.utc),
    )

    payload = json.loads(heartbeat_path.read_text())
    assert payload["jobs"] == [
        "forecast_live_heartbeat",
        REPLACEMENT_FORECAST_DOWNLOAD_JOB_ID,
        REPLACEMENT_FORECAST_MATERIALIZE_JOB_ID,
    ]


def test_scheduler_job_marks_started_before_success(monkeypatch) -> None:
    import src.observability.scheduler_health as scheduler_health
    from src.ingest.forecast_live_daemon import _scheduler_job

    writes: list[dict[str, object]] = []

    def _record(job_name: str, **kwargs: object) -> None:
        writes.append({"job_name": job_name, **kwargs})

    monkeypatch.setattr(scheduler_health, "_write_scheduler_health", _record)

    @_scheduler_job("example_long_job")
    def _job() -> None:
        writes.append({"job_name": "example_long_job", "inside_job": True})

    _job()

    assert writes == [
        {"job_name": "example_long_job", "failed": False, "started": True},
        {"job_name": "example_long_job", "inside_job": True},
        {"job_name": "example_long_job", "failed": False, "reason": None},
    ]


def test_replacement_materialize_job_calls_undecorated_production_inner(monkeypatch) -> None:
    """A nested scheduler wrapper must not convert production failure into OK health."""

    import src.data.replacement_forecast_production as production
    from src.ingest.forecast_live_daemon import _replacement_forecast_materialize_job

    calls: list[str] = []

    def _outer_wrapper() -> None:
        calls.append("outer")

    def _inner_job() -> None:
        calls.append("inner")

    _outer_wrapper.__wrapped__ = _inner_job  # type: ignore[attr-defined]
    monkeypatch.setattr(production, "_replacement_forecast_live_materialize_cycle", _outer_wrapper)

    _replacement_forecast_materialize_job.__wrapped__()

    assert calls == ["inner"]


def _materialization_queue_cfg(tmp_path) -> dict[str, object]:
    return {
        "request_dir": tmp_path / "requests",
        "processed_dir": tmp_path / "processed",
        "failed_dir": tmp_path / "failed",
        "seed_dir": tmp_path / "seeds",
        "seed_processed_dir": tmp_path / "seeds-processed",
        "seed_failed_dir": tmp_path / "seeds-failed",
        "forecast_db": tmp_path / "forecasts.db",
        "raw_manifest_dir": tmp_path / "raw",
        "seed_discovery_limit": 1,
        "seed_limit": 1,
        "limit": 1,
    }


def test_materialization_queue_publishes_wake_after_posterior_advance(
    monkeypatch, tmp_path
) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue
    import src.data.replacement_forecast_production as production
    from src.runtime import reactor_wake

    report = object()
    monkeypatch.setattr(
        queue,
        "process_replacement_forecast_live_materialization_queue",
        lambda **kwargs: report,
    )
    revisions = iter((41, 42))
    monkeypatch.setattr(
        production,
        "_forecast_posterior_revision",
        lambda cfg: next(revisions),
    )
    monkeypatch.setattr(
        production,
        "_forecast_posterior_families_between",
        lambda cfg, **kwargs: (("Shanghai", "2026-07-18", "high"),),
    )
    published: list[tuple[str, str, tuple[tuple[str, str, str], ...]]] = []
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda *, source, reason, forecast_families: published.append(
            (source, reason, forecast_families)
        )
        or reactor_wake.ReactorWake(
            "wake-1",
            "2026-07-16T12:00:00+00:00",
            source,
            reason,
            forecast_families=forecast_families,
        ),
    )

    result = production._run_replacement_forecast_live_materialization_queue_once(
        _materialization_queue_cfg(tmp_path)
    )

    assert result is report
    assert published == [
        (
            "replacement_forecast_production",
            "forecast_posterior_advanced",
            (("Shanghai", "2026-07-18", "high"),),
        )
    ]


def test_materialization_wake_families_use_only_changed_live_rows(tmp_path) -> None:
    import src.data.replacement_forecast_production as production

    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE forecast_posteriors (
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                runtime_layer TEXT,
                training_allowed INTEGER,
                q_json TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO forecast_posteriors (
                city,
                target_date,
                temperature_metric,
                runtime_layer,
                training_allowed,
                q_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ("Paris", "2026-07-18", "high", "live", 0, '{"29C":0.4,"30C":0.6}'),
                ("Shanghai", "2026-07-18", "high", "live", 0, '{"35C":0.3,"36C":0.7}'),
                ("Paris", "2026-07-18", "high", "live", 0, '{"29C":0.5,"30C":0.5}'),
                ("Shanghai", "2026-07-18", "high", "live", 0, '{"35C":0.9,"36C":0.1}'),
                ("London", "2026-07-18", "high", "shadow", 0, '{"25C":1.0}'),
                ("Toronto", "2026-07-18", "high", "live", 1, '{"25C":1.0}'),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    families = production._forecast_posterior_families_between(
        {"forecast_db": db_path},
        revision_before=2,
        revision_after=6,
    )

    assert families == (
        ("Shanghai", "2026-07-18", "high"),
        ("Paris", "2026-07-18", "high"),
    )


def test_materialization_queue_does_not_wake_without_new_posterior(
    monkeypatch, tmp_path
) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue
    import src.data.replacement_forecast_production as production
    from src.runtime import reactor_wake

    monkeypatch.setattr(
        queue,
        "process_replacement_forecast_live_materialization_queue",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(production, "_forecast_posterior_revision", lambda cfg: 42)
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("unexpected wake")),
    )

    production._run_replacement_forecast_live_materialization_queue_once(
        _materialization_queue_cfg(tmp_path)
    )


def test_materialization_queue_wake_failure_does_not_fail_committed_materialization(
    monkeypatch, tmp_path, caplog
) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue
    import src.data.replacement_forecast_production as production
    from src.runtime import reactor_wake

    report = object()
    monkeypatch.setattr(
        queue,
        "process_replacement_forecast_live_materialization_queue",
        lambda **kwargs: report,
    )
    revisions = iter((41, 42))
    monkeypatch.setattr(
        production,
        "_forecast_posterior_revision",
        lambda cfg: next(revisions),
    )
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **kwargs: (_ for _ in ()).throw(OSError("sidecar unavailable")),
    )

    result = production._run_replacement_forecast_live_materialization_queue_once(
        _materialization_queue_cfg(tmp_path)
    )

    assert result is report
    assert "periodic reactor scan remains authoritative" in caplog.text


def test_reactor_wake_sidecar_round_trip(tmp_path) -> None:
    from src.runtime.reactor_wake import (
        acknowledge_reactor_wake,
        publish_reactor_wake,
        reactor_wake_revision,
        read_reactor_wake,
    )

    path = tmp_path / "wake.json"
    assert reactor_wake_revision(path=path) is None
    published = publish_reactor_wake(
        source="test",
        reason="posterior_advanced",
        path=path,
        wake_id="wake-42",
        published_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        event_ids=("event-b", "event-a", "event-b", ""),
        forecast_families=(
            ("Shanghai", "2026-07-18", "high"),
            ("Shanghai", "2026-07-18", "high"),
            ("", "2026-07-18", "high"),
        ),
    )

    assert read_reactor_wake(path=path) == published
    assert published.event_ids == ("event-b", "event-a")
    assert published.forecast_families == (("Shanghai", "2026-07-18", "high"),)
    first_revision = reactor_wake_revision(path=path)
    assert first_revision is not None

    publish_reactor_wake(
        source="test",
        reason="day0_advanced",
        path=path,
        wake_id="wake-43",
        published_at=datetime(2026, 7, 16, 12, 0, 1, tzinfo=timezone.utc),
    )

    assert reactor_wake_revision(path=path) != first_revision
    assert acknowledge_reactor_wake(published, path=path) is True


def test_reactor_wake_queue_preserves_unconsumed_wakes(tmp_path) -> None:
    from src.runtime.reactor_wake import (
        acknowledge_reactor_wake,
        publish_reactor_wake,
        read_reactor_wake,
    )

    path = tmp_path / "wake.json"
    first = publish_reactor_wake(
        source="forecast",
        reason="forecast_posterior_advanced",
        path=path,
        wake_id="wake-first",
        published_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )
    second = publish_reactor_wake(
        source="day0",
        reason="day0_extreme_event_committed",
        path=path,
        wake_id="wake-second",
        published_at=datetime(2026, 7, 16, 12, 0, 1, tzinfo=timezone.utc),
    )

    assert read_reactor_wake(path=path) == first
    assert acknowledge_reactor_wake(first, path=path) is True
    assert read_reactor_wake(path=path) == second
    assert acknowledge_reactor_wake(second, path=path) is True
    assert read_reactor_wake(path=path) is None


def test_forecast_builder_forwards_wake_family_restriction(monkeypatch) -> None:
    import src.events.event_writer as writer_module
    import src.events.triggers.forecast_snapshot_ready as trigger_module
    import src.main as main
    import src.state.db as state_db

    family = ("Shanghai", "2026-07-18", "high")
    captured: dict[str, object] = {}

    class _Conn:
        def set_progress_handler(self, *_args) -> None:
            return None

        def close(self) -> None:
            return None

    class _Trigger:
        def __init__(self, _writer, *, live_eligibility_reader) -> None:
            captured["reader"] = live_eligibility_reader

        def build_committed_snapshot_events(self, **kwargs):
            captured["families"] = kwargs["restrict_to_families"]
            return ["event"]

    monkeypatch.setattr(
        state_db,
        "get_forecasts_connection_read_only",
        lambda: _Conn(),
    )
    monkeypatch.setattr(writer_module, "EventWriter", lambda _conn: object())
    monkeypatch.setattr(trigger_module, "ForecastSnapshotReadyTrigger", _Trigger)
    monkeypatch.setattr(
        trigger_module,
        "executable_forecast_live_eligible_reader",
        lambda _conn: "reader",
    )

    events = main._edli_build_forecast_snapshot_events(
        _Conn(),
        decision_time=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        received_at="2026-07-16T12:00:00+00:00",
        limit=None,
        source="wake",
        restrict_to_families={family},
    )

    assert events == ["event"]
    assert captured == {"reader": "reader", "families": {family}}


def test_reactor_wake_poll_defers_without_consuming_when_reactor_busy(monkeypatch) -> None:
    import src.main as main
    from src.runtime import reactor_wake

    wake = reactor_wake.ReactorWake(
        "wake-43",
        "2026-07-16T12:00:00+00:00",
        "replacement_forecast_production",
        "forecast_posterior_advanced",
        forecast_families=(("Shanghai", "2026-07-18", "high"),),
    )
    busy = {"value": True}

    class _Lock:
        def locked(self) -> bool:
            return busy["value"]

    class _Held:
        def is_set(self) -> bool:
            return False

    calls: list[str] = []

    def _run_reactor(
        *,
        producer_wake_reason=None,
        producer_wake_event_ids=(),
        producer_wake_families=(),
    ):
        calls.append(
            "reactor:"
            f"{producer_wake_reason}:{','.join(producer_wake_event_ids)}:"
            f"{len(producer_wake_families)}"
        )
        return True

    monkeypatch.setattr(reactor_wake, "read_reactor_wake", lambda: wake)
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wake",
        lambda _wake: True,
    )
    monkeypatch.setattr(main, "_edli_reactor_active_lock", _Lock())
    monkeypatch.setattr(main, "_held_position_monitor_active", _Held())
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _run_reactor)
    monkeypatch.setattr(main, "_edli_last_reactor_wake_id", None)

    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_last_reactor_wake_id is None
    busy["value"] = False
    assert main._edli_reactor_wake_poll_once() is True
    assert main._edli_reactor_wake_poll_once() is False
    assert calls == ["reactor:forecast_posterior_advanced::1"]


def test_day0_reactor_wake_runs_exit_monitor_before_reactor(monkeypatch) -> None:
    import src.main as main
    from src.runtime import reactor_wake

    wake = reactor_wake.ReactorWake(
        "wake-day0",
        "2026-07-16T12:00:00+00:00",
        "ingest_main",
        "day0_extreme_event_committed",
        ("event-day0",),
    )

    class _Lock:
        def locked(self) -> bool:
            return False

    class _Held:
        def is_set(self) -> bool:
            return False

    calls: list[str] = []

    def _run_reactor(
        *,
        producer_wake_reason=None,
        producer_wake_event_ids=(),
        producer_wake_families=(),
    ):
        calls.append(
            "reactor:"
            f"{producer_wake_reason}:{','.join(producer_wake_event_ids)}:"
            f"{len(producer_wake_families)}"
        )
        return True

    monkeypatch.setattr(reactor_wake, "read_reactor_wake", lambda: wake)
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wake",
        lambda _wake: True,
    )
    monkeypatch.setattr(main, "_edli_reactor_active_lock", _Lock())
    monkeypatch.setattr(main, "_held_position_monitor_active", _Held())
    monkeypatch.setattr(
        main,
        "_day0_wake_target_families",
        lambda event_ids: frozenset({("Paris", "2026-07-16", "high")}),
    )
    monkeypatch.setattr(
        main,
        "_exit_monitor_cycle",
        lambda *, target_families=None: calls.append(
            f"monitor:{sorted(target_families or ())}"
        ),
    )
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _run_reactor)
    monkeypatch.setattr(main, "_edli_last_reactor_wake_id", None)

    assert main._edli_reactor_wake_poll_once() is True
    assert calls == [
        "monitor:[('Paris', '2026-07-16', 'high')]",
        "reactor:day0_extreme_event_committed:event-day0:0",
    ]


def test_day0_wake_family_scope_is_canonical_and_fails_full_on_partial(
    monkeypatch,
) -> None:
    import src.main as main

    payload = json.dumps(
        {"city": "Paris", "target_date": "2026-07-16", "metric": "high"}
    )

    class _Conn:
        def __init__(self, rows):
            self.rows = rows
            self.closed = False

        def execute(self, _sql, _params):
            return self

        def fetchall(self):
            return self.rows

        def close(self):
            self.closed = True

    complete = _Conn(
        [("event-day0", "DAY0_EXTREME_UPDATED", payload)]
    )
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda: complete)

    assert main._day0_wake_target_families(("event-day0",)) == frozenset(
        {("Paris", "2026-07-16", "high")}
    )
    assert complete.closed is True

    partial = _Conn([])
    monkeypatch.setattr(main, "get_world_connection_read_only", lambda: partial)

    assert main._day0_wake_target_families(("event-day0",)) is None
    assert partial.closed is True


def test_reactor_wake_is_not_consumed_when_cycle_loses_execution_race(
    monkeypatch,
) -> None:
    import src.main as main
    from src.runtime import reactor_wake

    wake = reactor_wake.ReactorWake(
        "wake-race",
        "2026-07-16T12:00:00+00:00",
        "replacement_forecast_production",
        "forecast_posterior_advanced",
    )

    class _Lock:
        def locked(self) -> bool:
            return False

    class _Held:
        def is_set(self) -> bool:
            return False

    outcomes = iter((False, True))
    monkeypatch.setattr(reactor_wake, "read_reactor_wake", lambda: wake)
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wake",
        lambda _wake: True,
    )
    monkeypatch.setattr(main, "_edli_reactor_active_lock", _Lock())
    monkeypatch.setattr(main, "_held_position_monitor_active", _Held())
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda *,
        producer_wake_reason=None,
        producer_wake_event_ids=(),
        producer_wake_families=(): next(outcomes),
    )
    monkeypatch.setattr(main, "_edli_last_reactor_wake_id", None)

    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_last_reactor_wake_id is None
    assert main._edli_reactor_wake_poll_once() is True
    assert main._edli_last_reactor_wake_id == wake.wake_id


def test_reactor_wake_ack_failure_keeps_wake_retryable(monkeypatch) -> None:
    import src.main as main
    from src.runtime import reactor_wake

    wake = reactor_wake.ReactorWake(
        "wake-ack",
        "2026-07-16T12:00:00+00:00",
        "replacement_forecast_production",
        "forecast_posterior_advanced",
    )
    acknowledgements = iter((False, True))

    class _Lock:
        def locked(self) -> bool:
            return False

    class _Held:
        def is_set(self) -> bool:
            return False

    monkeypatch.setattr(reactor_wake, "read_reactor_wake", lambda: wake)
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wake",
        lambda _wake: next(acknowledgements),
    )
    monkeypatch.setattr(main, "_edli_reactor_active_lock", _Lock())
    monkeypatch.setattr(main, "_held_position_monitor_active", _Held())
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: True)
    monkeypatch.setattr(main, "_edli_last_reactor_wake_id", None)

    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_last_reactor_wake_id is None
    assert main._edli_reactor_wake_poll_once() is True
    assert main._edli_last_reactor_wake_id == wake.wake_id


def test_replacement_materialize_defaults_to_next_reactor_minute(monkeypatch) -> None:
    import src.ingest.forecast_live_daemon as forecast_live_daemon

    monkeypatch.setattr(forecast_live_daemon, "_replacement_forecast_live_cfg", lambda: {})

    assert forecast_live_daemon._replacement_forecast_materialize_interval_minutes() == 1


def test_forecast_live_heartbeat_write_shape(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import (
        FORECAST_LIVE_HEARTBEAT_JOB_ID,
        _write_forecast_live_heartbeat,
    )

    heartbeat_path = tmp_path / "forecast-live-heartbeat.json"
    _write_forecast_live_heartbeat(
        heartbeat_path=heartbeat_path,
        status="test",
        now_utc=datetime(2026, 5, 14, 9, 30, tzinfo=timezone.utc),
    )

    payload = json.loads(heartbeat_path.read_text())
    assert payload["daemon"] == "forecast-live"
    assert payload["status"] == "test"
    assert payload["timestamp"] == "2026-05-14T09:30:00+00:00"
    assert payload["written_at"] == "2026-05-14T09:30:00+00:00"
    assert payload["cadence_seconds"] == 30
    assert isinstance(payload["git_head"], str)
    assert FORECAST_LIVE_HEARTBEAT_JOB_ID in payload["jobs"]
    assert isinstance(payload["pid"], int)


def test_forecast_live_source_health_probe_uses_shared_lock_and_prior_state(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import _source_health_probe_tick

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    source_health_path = state_dir / "source_health.json"
    source_health_path.write_text(
        json.dumps(
            {
                "sources": {
                    "ecmwf_open_data": {"consecutive_failures": 2},
                    "wu_pws": {"last_success_at": "2026-05-14T07:00:00+00:00"},
                }
            }
        )
    )
    calls: dict[str, object] = {}

    def _state_path(name: str) -> Path:
        return state_dir / name

    def _probe(sources, timeout: float, *, _prior_state: dict) -> dict:
        calls["sources"] = tuple(sorted(sources))
        calls["timeout"] = timeout
        calls["prior_state"] = _prior_state
        return {"ecmwf_open_data": {"ok": True}}

    def _write(results: dict) -> Path:
        calls["results"] = results
        return source_health_path

    result = _source_health_probe_tick(
        _locks_dir_override=tmp_path / "locks",
        _probe_sources=_probe,
        _write_source_health=_write,
        _state_path=_state_path,
    )

    assert result == {
        "status": "ok",
        "source": "source_health",
        "sources": 1,
        "updated_sources": ["ecmwf_open_data"],
        "path": str(source_health_path),
    }
    assert calls["sources"] == ("ecmwf_open_data",)
    assert calls["timeout"] == 10.0
    assert calls["prior_state"] == {
        "ecmwf_open_data": {"consecutive_failures": 2},
        "wu_pws": {"last_success_at": "2026-05-14T07:00:00+00:00"},
    }
    assert calls["results"] == {
        "ecmwf_open_data": {"ok": True},
        "wu_pws": {"last_success_at": "2026-05-14T07:00:00+00:00"},
    }


def test_forecast_live_source_health_probe_skips_when_lock_is_held(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import _source_health_probe_tick

    from src.data.dual_run_lock import acquire_lock

    def _probe(**_kwargs):
        raise AssertionError("probe must not run while source_health lock is held")

    with acquire_lock("source_health", _locks_dir_override=tmp_path / "locks") as acquired:
        assert acquired
        result = _source_health_probe_tick(
            _locks_dir_override=tmp_path / "locks",
            _probe_sources=_probe,
        )

    assert result == {"status": "skipped_lock_held", "source": "source_health"}


def test_forecast_live_source_health_refresh_runs_before_scheduler_start() -> None:
    content = FORECAST_LIVE_DAEMON.read_text(encoding="utf-8")
    source_health_index = content.index("_source_health_probe_tick()\n")
    scheduler_index = content.index("_scheduler = build_scheduler()")

    assert source_health_index < scheduler_index


def test_forecast_live_track_runner_uses_shared_opendata_lock(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track

    def collector(*, track: str, **_kwargs) -> dict:
        raise AssertionError(f"collector must not run while {OPENDATA_DAEMON_LOCK_KEY} is held")

    with acquire_lock(OPENDATA_DAEMON_LOCK_KEY, _locks_dir_override=tmp_path) as acquired:
        assert acquired
        result = run_opendata_track(
            "mx2t6_high",
            _locks_dir_override=tmp_path,
            _collector=collector,
            _source_paused=lambda source_id: False,
        )

    assert result == {
        "status": "skipped_lock_held",
        "source": "ecmwf_open_data",
        "track": "mx2t6_high",
    }


def test_legacy_ingest_opendata_runner_uses_same_shared_lock(tmp_path, monkeypatch) -> None:
    import src.ingest_main as ingest_main

    def collector(*, track: str, **_kwargs) -> dict:
        raise AssertionError(f"legacy collector must not run while {OPENDATA_DAEMON_LOCK_KEY} is held")

    monkeypatch.setattr(ingest_main, "_is_source_paused", lambda source_id: False)
    with acquire_lock(OPENDATA_DAEMON_LOCK_KEY, _locks_dir_override=tmp_path) as acquired:
        assert acquired
        result = ingest_main._run_opendata_track(
            "mn2t6_low",
            _locks_dir_override=tmp_path,
            _collector=collector,
        )

    assert result == {
        "status": "skipped_lock_held",
        "source": "ecmwf_open_data",
        "track": "mn2t6_low",
    }


def test_forecast_live_track_runner_calls_collector_once_under_lock(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track

    calls: list[str] = []

    def collector(*, track: str, **_kwargs) -> dict:
        calls.append(track)
        return {"status": "ok", "track": track}

    result = run_opendata_track(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
    )

    assert result == {"status": "ok", "track": "mx2t6_high"}
    assert calls == ["mx2t6_high"]


def test_forecast_live_track_runner_binds_collector_to_selected_cycle(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track

    observed: dict[str, object] = {}

    def collector(*, track: str, run_date: date, run_hour: int, now_utc: datetime) -> dict:
        observed.update(
            {
                "track": track,
                "run_date": run_date,
                "run_hour": run_hour,
                "now_utc": now_utc,
            }
        )
        return {
            "status": "ok",
            "track": track,
            "source_run_id": "ecmwf_open_data:mx2t6_high:2026-05-14T00Z",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
        }

    result = run_opendata_track(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    assert observed == {
        "track": "mx2t6_high",
        "run_date": date(2026, 5, 14),
        "run_hour": 0,
        "now_utc": datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
    }


def test_forecast_live_track_runner_journals_success_in_forecasts_job_run(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str, **_kwargs) -> dict:
        return {
            "status": "ok",
            "track": track,
            "source_run_id": "ecmwf_open_data:mx2t6_high:2026-05-14T00Z",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "forecast_track": "mx2t6_high_full_horizon",
            "snapshots_inserted": 7,
            "coverage_written": 1,
            "producer_readiness_written": 1,
        }

    result = run_opendata_track(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _job_conn=conn,
        _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "SUCCESS"
    assert row["scheduled_for"] == "2026-05-14T00:00:00+00:00"
    assert row["source_id"] == "ecmwf_open_data"
    assert row["track"] == "mx2t6_high"
    assert row["source_run_id"] == "ecmwf_open_data:mx2t6_high:2026-05-14T00Z"
    assert row["release_calendar_key"] == "ecmwf_open_data:mx2t6_high:full"
    assert row["rows_written"] == 7


def test_forecast_live_track_runner_upserts_same_source_cycle_job_run(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str, **_kwargs) -> dict:
        return {
            "status": "ok",
            "track": track,
            "source_run_id": "ecmwf_open_data:mx2t6_high:2026-05-14T00Z",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "snapshots_inserted": 1,
        }

    for _ in range(2):
        run_opendata_track(
            "mx2t6_high",
            _locks_dir_override=tmp_path,
            _collector=collector,
            _source_paused=lambda source_id: False,
            _job_conn=conn,
            _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
        )

    rows = conn.execute(
        "SELECT * FROM job_run WHERE job_name = ?",
        ("forecast_live_opendata_mx2t6_high",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "SUCCESS"


def test_safe_cycle_poll_fetches_latest_safe_cycle_when_not_journaled(tmp_path, monkeypatch) -> None:
    from src.data.release_calendar import FetchDecision
    import src.ingest.forecast_live_daemon as forecast_live_daemon
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    selected_cycle = datetime(2026, 5, 17, 12, tzinfo=timezone.utc)
    safe_fetch = datetime(2026, 5, 17, 20, 5, tzinfo=timezone.utc)

    monkeypatch.setattr(
        forecast_live_daemon,
        "_forecast_work_identity",
        lambda track, now_utc: {
            "decision": FetchDecision.FETCH_ALLOWED,
            "metadata": {
                "selected_cycle_time": selected_cycle,
                "next_safe_fetch_at": safe_fetch,
            },
            "job_name": "forecast_live_opendata_mx2t6_high",
            "source_id": "ecmwf_open_data",
            "track": track,
            "scheduled_for": selected_cycle,
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "safe_fetch_not_before": safe_fetch,
        },
    )
    observed: dict[str, object] = {}

    def collector(*, track: str, run_date: date, run_hour: int, now_utc: datetime) -> dict:
        observed.update(
            {
                "track": track,
                "run_date": run_date,
                "run_hour": run_hour,
                "now_utc": now_utc,
            }
        )
        return {
            "status": "ok",
            "track": track,
            "source_run_id": "ecmwf_open_data:mx2t6_high:2026-05-17T12Z",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "forecast_track": "mx2t6_high_full_horizon",
            "snapshots_inserted": 4,
            "coverage_written": 1,
            "producer_readiness_written": 1,
        }

    result = forecast_live_daemon._run_opendata_track_if_due(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _job_conn=conn,
        _now_utc=datetime(2026, 5, 17, 20, 10, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    assert observed == {
        "track": "mx2t6_high",
        "run_date": date(2026, 5, 17),
        "run_hour": 12,
        "now_utc": datetime(2026, 5, 17, 20, 10, tzinfo=timezone.utc),
    }
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["source_run_id"] == "ecmwf_open_data:mx2t6_high:2026-05-17T12Z"
    assert row["scheduled_for"] == "2026-05-17T12:00:00+00:00"


def test_safe_cycle_poll_refetches_partial_cycle_with_pending_steps(tmp_path, monkeypatch) -> None:
    """ECMWF Open Data disseminates a cycle's steps incrementally over ~10h.
    A PARTIAL journal recorded at T+8h reflects only the steps observable at
    that moment; further steps publish over the following hours. The safe-
    cycle poll MUST refetch on PARTIAL so incremental steps enter the journal
    and live entries don't stall on MISSING_REQUIRED_STEPS until next 00Z/12Z.

    Anchor incident: 2026-05-18T20:11 UTC the 12Z cycle was journaled PARTIAL
    with NOT_RELEASED_STEPS=[...48 steps...]. The pre-fix daemon never
    re-fetched; opening_hunt rejected all 11 candidates for 8+ hours."""
    from src.data.release_calendar import FetchDecision
    import src.ingest.forecast_live_daemon as forecast_live_daemon
    from src.state.job_run_repo import get_latest_job_run, write_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    selected_cycle = datetime(2026, 5, 17, 12, tzinfo=timezone.utc)
    safe_fetch = datetime(2026, 5, 17, 20, 5, tzinfo=timezone.utc)

    monkeypatch.setattr(
        forecast_live_daemon,
        "_forecast_work_identity",
        lambda track, now_utc: {
            "decision": FetchDecision.FETCH_ALLOWED,
            "metadata": {
                "selected_cycle_time": selected_cycle,
                "next_safe_fetch_at": safe_fetch,
            },
            "job_name": "forecast_live_opendata_mx2t6_high",
            "source_id": "ecmwf_open_data",
            "track": track,
            "scheduled_for": selected_cycle,
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "safe_fetch_not_before": safe_fetch,
        },
    )
    write_job_run(
        conn,
        job_run_id="forecast-live-20260517-12z-partial",
        job_name="forecast_live_opendata_mx2t6_high",
        plane="forecast",
        scheduled_for=selected_cycle,
        started_at=datetime(2026, 5, 17, 20, 10, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 17, 20, 14, tzinfo=timezone.utc),
        status="PARTIAL",
        reason_code="NOT_RELEASED_STEPS=[150]",
        rows_written=364,
        rows_failed=0,
        source_run_id="ecmwf_open_data:mx2t6_high:2026-05-17T12Z",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
        safe_fetch_not_before=safe_fetch,
    )

    collector_calls: list[str] = []

    def collector(*, track: str, **_kwargs) -> dict:
        collector_calls.append(track)
        return {
            "status": "ok",
            "track": track,
            "source_run_id": "ecmwf_open_data:mx2t6_high:2026-05-17T12Z",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "forecast_track": "mx2t6_high_full_horizon",
            "snapshots_inserted": 1,
            "coverage_written": 1,
            "producer_readiness_written": 1,
        }

    result = forecast_live_daemon._run_opendata_track_if_due(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _job_conn=conn,
        _now_utc=datetime(2026, 5, 17, 20, 20, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok", "PARTIAL journal must not short-circuit refetch"
    assert collector_calls == ["mx2t6_high"], "collector must be invoked to pick up newly-released steps"
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["source_run_id"] == "ecmwf_open_data:mx2t6_high:2026-05-17T12Z"


def test_safe_cycle_poll_does_not_refetch_completed_success_cycle(tmp_path, monkeypatch) -> None:
    """API-spam guard: once a cycle reaches SUCCESS (all steps observed,
    completeness=COMPLETE), the safe-cycle poll must skip refetch — there is
    no incremental data left to gather and refetching would only burn ECMWF
    bandwidth. Complements test_safe_cycle_poll_refetches_partial_*."""
    from src.data.release_calendar import FetchDecision
    import src.ingest.forecast_live_daemon as forecast_live_daemon
    from src.state.job_run_repo import write_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    selected_cycle = datetime(2026, 5, 17, 12, tzinfo=timezone.utc)
    safe_fetch = datetime(2026, 5, 17, 20, 5, tzinfo=timezone.utc)

    monkeypatch.setattr(
        forecast_live_daemon,
        "_forecast_work_identity",
        lambda track, now_utc: {
            "decision": FetchDecision.FETCH_ALLOWED,
            "metadata": {
                "selected_cycle_time": selected_cycle,
                "next_safe_fetch_at": safe_fetch,
            },
            "job_name": "forecast_live_opendata_mx2t6_high",
            "source_id": "ecmwf_open_data",
            "track": track,
            "scheduled_for": selected_cycle,
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "safe_fetch_not_before": safe_fetch,
        },
    )
    write_job_run(
        conn,
        job_run_id="forecast-live-20260517-12z-success",
        job_name="forecast_live_opendata_mx2t6_high",
        plane="forecast",
        scheduled_for=selected_cycle,
        started_at=datetime(2026, 5, 17, 20, 10, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 17, 20, 14, tzinfo=timezone.utc),
        status="SUCCESS",
        reason_code=None,
        rows_written=552,
        rows_failed=0,
        source_run_id="ecmwf_open_data:mx2t6_high:2026-05-17T12Z",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
        safe_fetch_not_before=safe_fetch,
    )

    def collector(*, track: str, **_kwargs) -> dict:
        raise AssertionError(f"collector must not refetch SUCCESS-journaled cycle for {track}")

    result = forecast_live_daemon._run_opendata_track_if_due(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _job_conn=conn,
        _now_utc=datetime(2026, 5, 17, 20, 20, tzinfo=timezone.utc),
    )

    assert result["status"] == "current_cycle_already_journaled"
    assert result["source_run_id"] == "ecmwf_open_data:mx2t6_high:2026-05-17T12Z"
    rows = conn.execute(
        "SELECT * FROM job_run WHERE job_name = ?",
        ("forecast_live_opendata_mx2t6_high",),
    ).fetchall()
    assert len(rows) == 1


def test_forecast_live_track_runner_fails_on_collector_source_run_identity_mismatch(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str, **_kwargs) -> dict:
        return {
            "status": "ok",
            "track": track,
            "source_run_id": "ecmwf_open_data:mx2t6_high:2026-05-15T00Z",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "snapshots_inserted": 1,
        }

    with pytest.raises(RuntimeError, match="SOURCE_RUN_IDENTITY_MISMATCH"):
        run_opendata_track(
            "mx2t6_high",
            _locks_dir_override=tmp_path,
            _collector=collector,
            _source_paused=lambda source_id: False,
            _job_conn=conn,
            _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
        )

    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["reason_code"].startswith("SOURCE_RUN_IDENTITY_MISMATCH")


def test_forecast_live_track_runner_journals_partial_result(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str, **_kwargs) -> dict:
        return {
            "status": "ok",
            "source_run_status": "PARTIAL",
            "source_run_completeness": "PARTIAL",
            "reason_code": "coverage_short",
            "track": track,
            "snapshots_inserted": 3,
        }

    result = run_opendata_track(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _job_conn=conn,
        _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "PARTIAL"
    assert row["reason_code"] == "coverage_short"
    assert row["rows_written"] == 3
    assert row["rows_failed"] == 0


def test_forecast_live_track_runner_journals_lock_held(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str, **_kwargs) -> dict:
        raise AssertionError("collector must not run when lock is held")

    with acquire_lock(OPENDATA_DAEMON_LOCK_KEY, _locks_dir_override=tmp_path) as acquired:
        assert acquired
        result = run_opendata_track(
            "mx2t6_high",
            _locks_dir_override=tmp_path,
            _collector=collector,
            _source_paused=lambda source_id: False,
            _job_conn=conn,
            _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
        )

    assert result["status"] == "skipped_lock_held"
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "SKIPPED_LOCK_HELD"
    assert row["reason_code"] == "SKIPPED_LOCK_HELD"


def test_forecast_live_track_runner_journals_collector_exception(tmp_path) -> None:
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def collector(*, track: str, **_kwargs) -> dict:
        raise RuntimeError(f"failed {track}")

    with pytest.raises(RuntimeError, match="failed mx2t6_high"):
        run_opendata_track(
            "mx2t6_high",
            _locks_dir_override=tmp_path,
            _collector=collector,
            _source_paused=lambda source_id: False,
            _job_conn=conn,
            _now_utc=datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc),
        )

    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["reason_code"] == "EXCEPTION:RuntimeError:failed mx2t6_high"
    assert row["rows_failed"] == 1


def test_journaled_wrapper_commits_failed_job_before_reraising(tmp_path, monkeypatch) -> None:
    from src.data.release_calendar import FetchDecision
    import src.data.ecmwf_open_data as ecmwf_open_data
    import src.ingest.forecast_live_daemon as forecast_live_daemon
    import src.state.db as state_db
    from src.state.job_run_repo import get_latest_job_run

    db_path = tmp_path / "forecasts.db"
    setup_conn = sqlite3.connect(db_path)
    setup_conn.row_factory = sqlite3.Row
    init_schema_forecasts(setup_conn)
    setup_conn.commit()
    setup_conn.close()

    def get_test_conn(*, write_class: str):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(state_db, "get_forecasts_connection", get_test_conn)
    monkeypatch.setattr(
        forecast_live_daemon,
        "_forecast_work_identity",
        lambda track, now_utc: {
            "decision": FetchDecision.FETCH_ALLOWED,
            "metadata": {"selected_cycle_time": datetime(2026, 5, 14, 0, tzinfo=timezone.utc)},
            "job_name": "forecast_live_opendata_mx2t6_high",
            "source_id": "ecmwf_open_data",
            "track": track,
            "scheduled_for": datetime(2026, 5, 14, 0, tzinfo=timezone.utc),
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "safe_fetch_not_before": None,
        },
    )

    def collector(*, track: str, **_kwargs) -> dict:
        raise RuntimeError(f"failed {track}")

    monkeypatch.setattr(ecmwf_open_data, "collect_open_ens_cycle", collector)

    with pytest.raises(RuntimeError, match="failed mx2t6_high"):
        forecast_live_daemon._run_journaled_opendata_track("mx2t6_high")

    verify_conn = sqlite3.connect(db_path)
    verify_conn.row_factory = sqlite3.Row
    row = get_latest_job_run(verify_conn, "forecast_live_opendata_mx2t6_high")
    verify_conn.close()

    assert row is not None
    assert row["status"] == "FAILED"
    assert row["reason_code"] == "EXCEPTION:RuntimeError:failed mx2t6_high"


def test_journaled_wrapper_commits_running_before_collector_starts(tmp_path, monkeypatch) -> None:
    from src.data.release_calendar import FetchDecision
    import src.data.ecmwf_open_data as ecmwf_open_data
    import src.ingest.forecast_live_daemon as forecast_live_daemon
    import src.state.db as state_db
    from src.state.job_run_repo import get_latest_job_run

    db_path = tmp_path / "forecasts.db"
    setup_conn = sqlite3.connect(db_path)
    setup_conn.row_factory = sqlite3.Row
    init_schema_forecasts(setup_conn)
    setup_conn.commit()
    setup_conn.close()

    def get_test_conn(*, write_class: str):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(state_db, "get_forecasts_connection", get_test_conn)
    monkeypatch.setattr(
        forecast_live_daemon,
        "_forecast_work_identity",
        lambda track, now_utc: {
            "decision": FetchDecision.FETCH_ALLOWED,
            "metadata": {"selected_cycle_time": datetime(2026, 5, 14, 0, tzinfo=timezone.utc)},
            "job_name": "forecast_live_opendata_mx2t6_high",
            "source_id": "ecmwf_open_data",
            "track": track,
            "scheduled_for": datetime(2026, 5, 14, 0, tzinfo=timezone.utc),
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "safe_fetch_not_before": None,
        },
    )

    def collector(*, track: str, **_kwargs) -> dict:
        verify_conn = sqlite3.connect(db_path)
        verify_conn.row_factory = sqlite3.Row
        try:
            row = get_latest_job_run(verify_conn, "forecast_live_opendata_mx2t6_high")
        finally:
            verify_conn.close()
        assert row is not None
        assert row["status"] == "RUNNING"
        return {
            "status": "ok",
            "track": track,
            "source_run_id": "ecmwf_open_data:mx2t6_high:2026-05-14T00Z",
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full",
            "snapshots_inserted": 1,
        }

    monkeypatch.setattr(ecmwf_open_data, "collect_open_ens_cycle", collector)

    result = forecast_live_daemon._run_journaled_opendata_track("mx2t6_high")
    assert result["status"] == "ok"


def test_forecast_live_track_runner_journals_not_released_without_collector(tmp_path, monkeypatch) -> None:
    from src.data.release_calendar import FetchDecision
    import src.data.release_calendar as release_calendar
    from src.ingest.forecast_live_daemon import run_opendata_track
    from src.state.job_run_repo import get_latest_job_run

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    selected_cycle = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    safe_fetch = datetime(2026, 5, 14, 8, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(
        release_calendar,
        "select_source_run_for_target_horizon",
        lambda **kwargs: (
            FetchDecision.SKIPPED_NOT_RELEASED,
            {
                "selected_cycle_time": selected_cycle,
                "next_safe_fetch_at": safe_fetch,
            },
        ),
    )

    def collector(*, track: str, **_kwargs) -> dict:
        raise AssertionError("collector must not run before safe fetch")

    result = run_opendata_track(
        "mx2t6_high",
        _locks_dir_override=tmp_path,
        _collector=collector,
        _source_paused=lambda source_id: False,
        _job_conn=conn,
        _now_utc=datetime(2026, 5, 14, 7, 30, tzinfo=timezone.utc),
    )

    assert result["status"] == "skipped_not_released"
    row = get_latest_job_run(conn, "forecast_live_opendata_mx2t6_high")
    assert row is not None
    assert row["status"] == "SKIPPED_NOT_RELEASED"
    assert row["scheduled_for"] == selected_cycle.isoformat()
    assert row["release_calendar_key"] == "ecmwf_open_data:mx2t6_high:full"
    assert row["safe_fetch_not_before"] == safe_fetch.isoformat()


def test_forecast_live_daemon_has_no_trading_imports() -> None:
    tree = ast.parse(FORECAST_LIVE_DAEMON.read_text(encoding="utf-8"))
    banned_prefixes = (
        "src.engine",
        "src.execution",
        "src.riskguard",
        "src.risk_allocator",
        "src.strategy",
        "src.signal",
        "src.venue",
    )
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    assert not [
        module for module in imports
        if module == "src.main" or module.startswith(banned_prefixes)
    ]


# ---------------------------------------------------------------------------
# Replacement-forecast download cron schedule (10h production dead-zone fix)
#
# Created: 2026-06-10
# Last reused/audited: 2026-06-10
# Authority basis: 10h production dead-zone incident 2026-06-10,
#   /tmp/production_recovery_report.md (K1: download cron only fired for the
#   00Z/12Z cycles, leaving a ~12h dead zone where readiness 3h TTL expired).
# Relationship under test: the publish-cron schedule (producer) must cover ALL
#   FOUR model cycles so there is no window longer than the cycle cadence (6h)
#   without a scheduled download — otherwise readiness expires faster than it is
#   refreshed and the live engine starves. This is the cross-boundary invariant
#   (schedule cadence <= readiness TTL refresh need), not just a return-value check.
# ---------------------------------------------------------------------------


def test_replacement_publish_cron_covers_all_four_model_cycles() -> None:
    """All four AIFS-ENS cycles {00Z,06Z,12Z,18Z} are scheduled for download.

    With the default 14h release lag the available-at hours are
    (0+14, 6+14, 12+14, 18+14) % 24 = {14, 20, 2, 8}. Before the dead-zone fix
    only {14, 2} (00Z+12Z) were scheduled, so the 06Z/18Z raw inputs never
    downloaded in steady state."""
    import src.ingest.forecast_live_daemon as forecast_live_daemon

    hours = forecast_live_daemon._replacement_forecast_publish_cron_hours()
    assert set(hours) == {14, 20, 2, 8}
    assert len(hours) == 4


def test_replacement_publish_cron_gaps_never_exceed_cycle_cadence() -> None:
    """RELATIONSHIP INVARIANT: no gap between consecutive scheduled download fires
    exceeds 6h (the model cycle cadence). A gap > 6h is the dead-zone bug — readiness
    (3h TTL) would expire before the next download could refresh it. Property holds
    for ANY release lag, so sweep all 24 lags."""
    import src.ingest.forecast_live_daemon as forecast_live_daemon

    for lag in range(24):
        monkey_cfg = {"download_release_lag_hours": float(lag)}
        original = forecast_live_daemon._replacement_forecast_live_cfg
        forecast_live_daemon._replacement_forecast_live_cfg = lambda: monkey_cfg  # type: ignore[assignment]
        try:
            hours = forecast_live_daemon._replacement_forecast_publish_cron_hours()
        finally:
            forecast_live_daemon._replacement_forecast_live_cfg = original  # type: ignore[assignment]
        ordered = sorted(hours)
        # Gaps between consecutive fire times on a 24h wraparound clock.
        gaps = [
            (ordered[(i + 1) % len(ordered)] - ordered[i]) % 24
            for i in range(len(ordered))
        ]
        gaps = [g if g != 0 else 24 for g in gaps]
        assert max(gaps) <= 6, f"lag={lag}: gap {max(gaps)}h > 6h cadence (dead zone)"


def test_replacement_publish_cron_applies_release_lag(monkeypatch) -> None:
    """Each scheduled hour is (cycle_hour + release_lag) % 24 for the configured lag
    (all four cycles share one publication lag)."""
    import src.ingest.forecast_live_daemon as forecast_live_daemon

    monkeypatch.setattr(
        forecast_live_daemon,
        "_replacement_forecast_live_cfg",
        lambda: {"download_release_lag_hours": 9.0},
    )
    hours = forecast_live_daemon._replacement_forecast_publish_cron_hours()
    assert set(hours) == {(0 + 9) % 24, (6 + 9) % 24, (12 + 9) % 24, (18 + 9) % 24}
    assert set(hours) == {9, 15, 21, 3}


def test_premature_cron_fire_cannot_request_a_guessed_cycle() -> None:
    """SAFETY GUARD (rewritten 2026-06-11, run-selection single authority).

    The old guard pinned the now-minus-release-lag floor as the resolver of "newest
    available cycle". That guessed clock requested unpublished 12Z/18Z runs every night;
    the rung-2 meta guard refused them and the refusal aborted the whole
    download->materialize cycle. The guess path is now DEAD: ``_parse_cycle(None, ...)``
    raises, and cron fires (premature or not) resolve the cycle exclusively through the
    probe-resolved authority ``_probe_resolved_available_cycle`` -- so a premature fire
    can only ever fetch a cycle some provider probe CONFIRMS is published, or skip."""
    import pytest

    from scripts.download_replacement_forecast_current_targets import _parse_cycle

    # The guess path is unconstructable.
    with pytest.raises(ValueError, match="probe-resolved"):
        _parse_cycle(None, now=datetime(2026, 6, 10, 20, 10, tzinfo=timezone.utc), release_lag_hours=14.0)

    # The explicit operator path still parses and validates.
    explicit = _parse_cycle(
        "2026-06-10T06:00:00+00:00",
        now=datetime(2026, 6, 10, 20, 10, tzinfo=timezone.utc),
        release_lag_hours=14.0,
    )
    assert explicit == datetime(2026, 6, 10, 6, 0, tzinfo=timezone.utc)

    # The production resolver is the probe authority: it returns exactly the newest
    # cycle whose anchor probe confirms, independent of any lag.
    import src.data.replacement_forecast_production as production
    from src.data.replacement_cycle_availability import (
        newest_complete_cycle,
        resolve_anchor_cycle_availability,
    )

    published = {datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc),
                 datetime(2026, 6, 10, 6, 0, tzinfo=timezone.utc)}
    availability = resolve_anchor_cycle_availability(
        datetime(2026, 6, 10, 20, 10, tzinfo=timezone.utc),
        probe_anchor=lambda c: c in published,
    )
    assert newest_complete_cycle(availability) == datetime(2026, 6, 10, 6, 0, tzinfo=timezone.utc)
    # And the production module exposes the single authority the jobs call.
    assert callable(production._probe_resolved_available_cycle)


def test_replacement_forecast_live_paths_honor_primary_runtime_root(
    monkeypatch, tmp_path
) -> None:
    """Live replacement paths are runtime-state paths, not forecast daemon cwd paths."""
    import src.config as config_mod
    import src.data.replacement_forecast_production as production

    old_primary = os.environ.get("ZEUS_PRIMARY_ROOT")
    monkeypatch.setenv("ZEUS_PRIMARY_ROOT", str(tmp_path))
    try:
        config_mod = importlib.reload(config_mod)
        production = importlib.reload(production)
        cfg = dict(config_mod.settings._data.get("replacement_forecast_live", {}))
        cfg.update(
            {
                "forecast_db": "state/zeus-forecasts.db",
                "raw_manifest_dir": "state/replacement_forecast_live/raw_manifests",
                "request_dir": "state/replacement_forecast_live/requests",
                "processed_dir": "state/replacement_forecast_live/processed",
                "failed_dir": "state/replacement_forecast_live/failed",
                "seed_dir": "state/replacement_forecast_live/seeds",
                "seed_processed_dir": "state/replacement_forecast_live/seeds_processed",
                "seed_failed_dir": "state/replacement_forecast_live/seeds_failed",
                "download_output_dir": "state/replacement_forecast_live/raw_manifests",
            }
        )
        monkeypatch.setitem(
            config_mod.settings._data, "replacement_forecast_live", cfg
        )

        resolved = (
            production._replacement_forecast_live_materialization_queue_config()
        )

        runtime_state = tmp_path.resolve() / "state"
        assert resolved["forecast_db"] == runtime_state / "zeus-forecasts.db"
        assert resolved["raw_manifest_dir"] == (
            runtime_state / "replacement_forecast_live" / "raw_manifests"
        )
        assert resolved["request_dir"] == (
            runtime_state / "replacement_forecast_live" / "requests"
        )
        assert resolved["seed_dir"] == (
            runtime_state / "replacement_forecast_live" / "seeds"
        )
        assert resolved["download_output_dir"] == (
            runtime_state / "replacement_forecast_live" / "raw_manifests"
        )
    finally:
        if old_primary is None:
            monkeypatch.delenv("ZEUS_PRIMARY_ROOT", raising=False)
        else:
            monkeypatch.setenv("ZEUS_PRIMARY_ROOT", old_primary)
        importlib.reload(config_mod)
        importlib.reload(production)
