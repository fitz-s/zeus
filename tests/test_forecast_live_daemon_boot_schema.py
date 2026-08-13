# Created: 2026-07-20
# Last reused/audited: 2026-08-10
# Authority basis: operator-directed DB hot-path, fault-isolation, and committed ENS wake liveness.

from __future__ import annotations

import sqlite3

import pytest

import src.ingest.forecast_live_daemon as daemon
import src.data.replacement_forecast_production as production
from src.ingest.forecast_live_daemon import (
    _FORECAST_BOOT_REQUIRED_INDEXES,
    _FORECAST_BOOT_REQUIRED_INDEX_TABLES,
    _FORECAST_BOOT_REQUIRED_SCHEMA,
    _forecast_boot_schema_ready,
)
from src.state.db import assert_schema_current_forecasts


def _conn_with_required_schema(*, omit: tuple[str, str] | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for table, columns in _FORECAST_BOOT_REQUIRED_SCHEMA.items():
        defs = []
        for column in sorted(columns):
            if omit == (table, column):
                continue
            defs.append(f"{column} TEXT")
        conn.execute(f"CREATE TABLE {table} ({', '.join(defs)})")
    for index_name in _FORECAST_BOOT_REQUIRED_INDEXES:
        table = _FORECAST_BOOT_REQUIRED_INDEX_TABLES[index_name]
        conn.execute(f"CREATE INDEX {index_name} ON {table} (city)")
    return conn


def test_forecast_live_boot_schema_fast_check_accepts_present_core_schema() -> None:
    conn = _conn_with_required_schema()
    try:
        assert _forecast_boot_schema_ready(conn) is True
    finally:
        conn.close()


def test_forecast_live_boot_schema_fast_check_rejects_missing_required_column() -> None:
    conn = _conn_with_required_schema(omit=("forecast_posteriors", "runtime_layer"))
    try:
        assert _forecast_boot_schema_ready(conn) is False
    finally:
        conn.close()


def test_replacement_materializer_serializes_forecast_db_writer(monkeypatch) -> None:
    jobs: list[tuple[object, str, dict[str, object]]] = []

    class Scheduler:
        def add_job(self, fn, trigger, **kwargs) -> None:
            jobs.append((fn, trigger, kwargs))

    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_materialize_interval_minutes",
        lambda: 5,
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_materialize_poll_seconds",
        lambda: 1,
    )

    daemon._register_replacement_forecast_production_jobs(Scheduler())

    materialize = next(
        job
        for job in jobs
        if job[2]["id"] == daemon.REPLACEMENT_FORECAST_MATERIALIZE_JOB_ID
    )
    assert daemon.REPLACEMENT_FORECAST_MATERIALIZE_MAX_INSTANCES == 1
    assert materialize[2]["max_instances"] == 1


def test_forecast_live_boot_schema_fast_check_rejects_missing_live_index() -> None:
    conn = _conn_with_required_schema()
    try:
        conn.execute("DROP INDEX idx_raw_model_forecasts_endpoint_family_cycle_members")
        assert _forecast_boot_schema_ready(conn) is False
    finally:
        conn.close()


def test_forecast_live_boot_schema_rejects_index_bound_to_legacy_table() -> None:
    conn = _conn_with_required_schema()
    try:
        columns = ", ".join(
            f"{column} TEXT"
            for column in sorted(_FORECAST_BOOT_REQUIRED_SCHEMA["readiness_state"])
        )
        conn.execute("ALTER TABLE readiness_state RENAME TO readiness_state_legacy")
        conn.execute(f"CREATE TABLE readiness_state ({columns})")

        assert _forecast_boot_schema_ready(conn) is False
        with pytest.raises(RuntimeError, match="misbound live-required indexes"):
            assert_schema_current_forecasts(conn)
    finally:
        conn.close()


def test_committed_ens_run_wakes_only_its_exact_eligible_scopes(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_run_id TEXT,
            source_id TEXT,
            model_version TEXT,
            authority TEXT,
            causality_status TEXT,
            boundary_ambiguous INTEGER,
            forecast_window_attribution_status TEXT,
            contributes_to_target_extrema INTEGER
        )
        """
    )
    eligible = (
        "Amsterdam",
        "2026-08-11",
        "high",
        "ecmwf_open_data:mx2t6_high:2026-08-10T12Z",
        "ecmwf_open_data",
        "ecmwf_ens",
        "VERIFIED",
        "OK",
        0,
        "FULLY_INSIDE_TARGET_LOCAL_DAY",
        1,
    )
    conn.executemany(
        "INSERT INTO ensemble_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            eligible,
            ("Paris", "2026-08-11", "high", *eligible[3:]),
            ("London", "2026-08-11", "high", "other-run", *eligible[4:]),
            ("Milan", "2026-08-11", "high", *eligible[3:7], "UNKNOWN", *eligible[8:]),
        ),
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"forecast_db": "forecast.db"},
    )

    def _enqueue(cfg, *, scopes, limit, **_kwargs):
        captured.update(cfg=cfg, scopes=scopes, limit=limit)
        return {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2}

    monkeypatch.setattr(production, "_enqueue_cycle_advance_reseeds_if_needed", _enqueue)
    try:
        report = daemon._enqueue_committed_opendata_cycle_advance_reseeds(
            conn,
            {
                "snapshots_inserted": 4,
                "source_run_id": eligible[3],
            },
        )
    finally:
        conn.close()

    assert report == {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2}
    assert captured["scopes"] == (
        ("Amsterdam", "2026-08-11", "high"),
        ("Paris", "2026-08-11", "high"),
    )
    assert captured["limit"] == 2


def test_opendata_commit_precedes_cycle_advance_wake(monkeypatch) -> None:
    events: list[str] = []
    daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS.clear()

    class _Connection:
        def commit(self) -> None:
            events.append("commit")

        def close(self) -> None:
            events.append("close")

    conn = _Connection()
    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection",
        lambda **_kwargs: conn,
    )
    monkeypatch.setattr(
        daemon,
        "run_opendata_track",
        lambda _track, **_kwargs: events.append("collect")
        or {"status": "ok", "snapshots_inserted": 1, "source_run_id": "run-12z"},
    )
    monkeypatch.setattr(
        daemon,
        "_enqueue_committed_opendata_cycle_advance_reseeds",
        lambda _conn, _result: events.append("wake")
        or {"status": "CYCLE_ADVANCE_TRIGGER"},
    )

    result = daemon._run_journaled_opendata_track("mx2t6_high")

    assert events == ["collect", "commit", "wake", "close"]
    assert result["cycle_advance_reseed"]["status"] == "CYCLE_ADVANCE_TRIGGER"
    daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS.clear()


def test_journaled_opendata_run_replays_unacked_wake_once_per_process(monkeypatch) -> None:
    events: list[str] = []

    class _Connection:
        def commit(self) -> None:
            events.append("commit")

    result = {
        "status": "current_cycle_already_journaled",
        "source_run_id": "ecmwf_open_data:mn2t6_low:2026-08-13T00Z",
        "snapshots_inserted": 362,
    }
    daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS.clear()
    monkeypatch.setattr(
        daemon,
        "_enqueue_committed_opendata_cycle_advance_reseeds",
        lambda _conn, _result: events.append("wake")
        or {"status": "CYCLE_ADVANCE_TRIGGER"},
    )

    first = daemon._commit_opendata_result_and_wake(_Connection(), result)
    second = daemon._commit_opendata_result_and_wake(_Connection(), result)

    assert events == ["commit", "wake", "commit"]
    assert first["cycle_advance_reseed"]["status"] == "CYCLE_ADVANCE_TRIGGER"
    assert "cycle_advance_reseed" not in second

    daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS.clear()
    replayed = daemon._commit_opendata_result_and_wake(_Connection(), result)

    assert events == ["commit", "wake", "commit", "commit", "wake"]
    assert replayed["cycle_advance_reseed"]["status"] == "CYCLE_ADVANCE_TRIGGER"
    daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS.clear()


def test_current_journaled_run_carries_rows_needed_to_replay_wake(monkeypatch) -> None:
    source_run_id = "ecmwf_open_data:mn2t6_low:2026-08-13T00Z"
    monkeypatch.setattr(
        daemon,
        "_latest_job_run_current_for_identity",
        lambda _conn, _identity: (
            True,
            {
                "source_run_id": source_run_id,
                "scheduled_for": "2026-08-13T00:00:00+00:00",
                "rows_written": 362,
            },
        ),
    )

    result = daemon._run_opendata_track_if_due(
        "mn2t6_low",
        _job_conn=object(),
        _collector=lambda **_kwargs: pytest.fail("journaled run must not refetch"),
        _now_utc=daemon.datetime(2026, 8, 13, 12, tzinfo=daemon.timezone.utc),
    )

    assert result["status"] == "current_cycle_already_journaled"
    assert result["source_run_id"] == source_run_id
    assert result["snapshots_inserted"] == 362


@pytest.mark.parametrize(
    "status",
    (
        "OPENDATA_CYCLE_ADVANCE_TRIGGER_FAILSOFT_SKIPPED",
        "CYCLE_ADVANCE_TRIGGER_FAILSOFT_SKIPPED",
        "CYCLE_ADVANCE_FORECAST_DB_MISSING",
        "CYCLE_ADVANCE_PLAN_BLOCKED",
        "CYCLE_ADVANCE_NO_MATERIALIZABLE_CYCLE",
    ),
)
def test_failed_opendata_wake_remains_retryable(monkeypatch, status: str) -> None:
    calls = 0

    class _Connection:
        def commit(self) -> None:
            return None

    def _wake(_conn, _result):
        nonlocal calls
        calls += 1
        return {"status": status}

    result = {
        "status": "current_cycle_already_journaled",
        "source_run_id": "ecmwf_open_data:mn2t6_low:2026-08-13T00Z",
        "snapshots_inserted": 362,
    }
    daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS.clear()
    monkeypatch.setattr(daemon, "_enqueue_committed_opendata_cycle_advance_reseeds", _wake)

    daemon._commit_opendata_result_and_wake(_Connection(), result)
    daemon._commit_opendata_result_and_wake(_Connection(), result)

    assert calls == 2
    assert not daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS


@pytest.mark.parametrize(
    "status",
    (
        "CYCLE_ADVANCE_TRIGGER",
        "OPENDATA_CYCLE_ADVANCE_NO_ELIGIBLE_SCOPES",
    ),
)
def test_terminal_opendata_wake_is_acked(monkeypatch, status: str) -> None:
    calls = 0

    class _Connection:
        def commit(self) -> None:
            return None

    def _wake(_conn, _result):
        nonlocal calls
        calls += 1
        return {"status": status}

    result = {
        "status": "current_cycle_already_journaled",
        "source_run_id": "ecmwf_open_data:mn2t6_low:2026-08-13T00Z",
        "snapshots_inserted": 362,
    }
    daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS.clear()
    monkeypatch.setattr(daemon, "_enqueue_committed_opendata_cycle_advance_reseeds", _wake)

    daemon._commit_opendata_result_and_wake(_Connection(), result)
    daemon._commit_opendata_result_and_wake(_Connection(), result)

    assert calls == 1
    assert daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS == {result["source_run_id"]}
    daemon._OPENDATA_WAKE_ACKED_SOURCE_RUN_IDS.clear()
