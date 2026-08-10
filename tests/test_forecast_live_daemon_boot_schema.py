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
