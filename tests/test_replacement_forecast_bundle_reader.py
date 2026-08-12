# Created: 2026-06-06
# Last reused/audited: 2026-08-11
# Lifecycle: created=2026-06-06; last_reviewed=2026-08-11; last_reused=2026-08-11
# Purpose: Protect replacement posterior bundle reader no-bypass semantics.
# Reuse: Run before wiring replacement posterior into executable forecast reader or event reactor.
# Authority basis: Operator-directed live replacement forecast bundle reader semantics.
"""Replacement forecast posterior bundle reader tests."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import src.data.replacement_forecast_bundle_reader as reader
import src.data.replacement_input_hwm as input_hwm
from src.data.replacement_forecast_bundle_reader import (
    HIGH_DATA_VERSION,
    PRODUCT_ID,
    SOURCE_ID,
    read_replacement_forecast_bundle,
)
from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    TRADEABLE_GRADE_QLCB_BASIS,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import (
    PRODUCT_ID as OPENMETEO_ANCHOR_PRODUCT_ID,
    SOURCE_ID as OPENMETEO_ANCHOR_SOURCE_ID,
)
from src.data.replacement_forecast_readiness import LIVE_RUNTIME_LAYER, ReplacementForecastDependency, build_replacement_forecast_readiness
from src.data.replacement_input_hwm import (
    ReplacementInputHwmReadUnavailable,
    _exact_current_value_serving_lag,
    _exact_consumed_anchor_artifact_cycle,
    _posterior_provenance_for_cycle,
    freeze_replacement_artifact_hwm,
    frozen_replacement_artifact_hwm_unavailable,
    install_frozen_replacement_artifact_hwm,
    latest_raw_artifact_input_cycle,
    latest_raw_model_input_cycle,
    latest_used_raw_model_input_mark,
    prime_frozen_replacement_artifact_hwm,
    replacement_live_input_lag_reason,
)
from src.data.replacement_current_value_serving import (
    CurrentValueServingReadUnavailable,
    read_current_instrument_values,
)
from src.state.schema.v2_schema import apply_canonical_schema


UTC = timezone.utc


@dataclass(frozen=True)
class _Evidence:
    source_run_id: str


@dataclass(frozen=True)
class _BaselineBundle:
    evidence: _Evidence


def test_cycle_frozen_artifact_hwm_is_reused_across_connections(tmp_path) -> None:
    db_path = tmp_path / "forecast.db"
    writer = sqlite3.connect(db_path)
    writer.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT,
            source_cycle_time TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            artifact_metadata_json TEXT
        )
        """
    )
    requests = (
        ("Shanghai", "2026-08-12", "high"),
        ("Ankara", "2026-08-13", "high"),
    )
    for city, target_date, metric in requests:
        writer.execute(
            "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?)",
            (
                "openmeteo_ecmwf_ifs_9km",
                "2026-08-11T12:00:00+00:00",
                "2026-08-11T12:05:00+00:00",
                "2026-08-11T12:05:00+00:00",
                json.dumps(
                    {"city": city, "target_date": target_date, "metric": metric}
                ),
            ),
        )
    writer.commit()
    writer.close()

    decision_time = datetime(2026, 8, 11, 13, tzinfo=UTC)
    prefetch = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    prefetch.row_factory = sqlite3.Row
    prefetch.execute("BEGIN")
    snapshot = freeze_replacement_artifact_hwm(
        prefetch,
        requests=requests,
        decision_time=decision_time,
    )
    prefetch.rollback()
    prefetch.close()

    consumer = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    consumer.row_factory = sqlite3.Row
    traced: list[str] = []
    consumer.set_trace_callback(traced.append)
    release = install_frozen_replacement_artifact_hwm(snapshot)
    try:
        cycles = {
            request: latest_raw_artifact_input_cycle(
                consumer,
                city=request[0],
                target_date=request[1],
                metric=request[2],
                decision_time=decision_time,
            )
            for request in requests
        }
    finally:
        release()
        consumer.close()

    assert all(cycle is not None for cycle in cycles.values())
    assert not any(
        "FROM RAW_FORECAST_ARTIFACTS" in statement.upper() for statement in traced
    )


def test_cycle_hwm_reuses_unchanged_payload_coverage_and_rechecks_rewrite(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "forecast.db"
    payload_path = tmp_path / "openmeteo.json"
    artifact_path = tmp_path / "manifest.json"
    payload = {
        "timezone": "UTC",
        "utc_offset_seconds": 0,
        "hourly": {
            "time": ["2026-08-12T12:00"],
            "temperature_2m": [25.0],
        },
    }
    artifact_path.write_text("{}", encoding="utf-8")

    writer = sqlite3.connect(db_path)
    writer.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT,
            product_id TEXT,
            source_cycle_time TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            artifact_path TEXT,
            artifact_metadata_json TEXT
        )
        """
    )
    request = ("Shanghai", "2026-08-12", "high")
    writer.execute(
        "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            OPENMETEO_ANCHOR_SOURCE_ID,
            OPENMETEO_ANCHOR_PRODUCT_ID,
            "2026-08-11T12:00:00+00:00",
            "2026-08-11T12:05:00+00:00",
            "2026-08-11T12:05:00+00:00",
            str(artifact_path),
            json.dumps(
                {
                    "city": request[0],
                    "target_date": request[1],
                    "metric": request[2],
                    "openmeteo_payload_json": payload_path.name,
                }
            ),
        ),
    )
    writer.commit()
    writer.close()

    original_read_text = type(payload_path).read_text
    payload_reads = 0

    def counted_read_text(path, *args, **kwargs):
        nonlocal payload_reads
        if path == payload_path:
            payload_reads += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(payload_path), "read_text", counted_read_text)
    input_hwm._cached_artifact_payload_coverage.cache_clear()

    def freeze_once():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        try:
            return freeze_replacement_artifact_hwm(
                conn,
                requests=(request,),
                decision_time=datetime(2026, 8, 11, 13, tzinfo=UTC),
            )
        finally:
            conn.rollback()
            conn.close()

    assert freeze_once().artifact_cycles == {}
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    assert freeze_once().artifact_cycles[request] == datetime(
        2026, 8, 11, 12, tzinfo=UTC
    )
    assert freeze_once().artifact_cycles[request] == datetime(
        2026, 8, 11, 12, tzinfo=UTC
    )
    assert payload_reads == 1

    unchanged_stat = payload_path.stat()
    same_size_payload = json.dumps(payload).replace("25.0", "26.0")
    assert len(same_size_payload) == len(json.dumps(payload))
    payload_path.write_text(same_size_payload, encoding="utf-8")
    os.utime(
        payload_path,
        ns=(unchanged_stat.st_atime_ns, unchanged_stat.st_mtime_ns),
    )
    assert freeze_once().artifact_cycles[request] == datetime(
        2026, 8, 11, 12, tzinfo=UTC
    )
    assert payload_reads == 2

    stat = payload_path.stat()
    os.utime(
        payload_path,
        ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
    )
    assert freeze_once().artifact_cycles[request] == datetime(
        2026, 8, 11, 12, tzinfo=UTC
    )
    assert payload_reads == 3

    payload_path.write_text(
        json.dumps({**payload, "generationtime_ms": 1.0}),
        encoding="utf-8",
    )
    assert freeze_once().artifact_cycles[request] == datetime(
        2026, 8, 11, 12, tzinfo=UTC
    )
    assert payload_reads == 4


def test_cycle_hwm_sql_deadline_is_not_installed_during_payload_validation(
    tmp_path, monkeypatch
) -> None:
    class TrackingConnection(sqlite3.Connection):
        progress_active = False
        progress_transitions: list[bool]
        bounded_sql_count = 0
        wall_stall_injected = False

        def set_progress_handler(self, progress_handler, n):
            if progress_handler is not None:
                assert self.progress_active is False
                self.bounded_sql_count = 0
            else:
                assert self.bounded_sql_count <= 1
            self.progress_active = progress_handler is not None
            self.progress_transitions.append(self.progress_active)
            return super().set_progress_handler(progress_handler, n)

        def execute(self, sql, parameters=(), /):
            if self.progress_active:
                assert self.bounded_sql_count == 0
                self.bounded_sql_count += 1
                if not self.wall_stall_injected:
                    self.wall_stall_injected = True
                    clock[0] = 0.11
            return super().execute(sql, parameters)

    db_path = tmp_path / "forecast.db"
    artifact_path = tmp_path / "manifest.json"
    payload_path = tmp_path / "payload.json"
    artifact_path.write_text("{}", encoding="utf-8")
    payload_path.write_text("{}", encoding="utf-8")
    writer = sqlite3.connect(db_path)
    writer.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT,
            product_id TEXT,
            source_cycle_time TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            artifact_path TEXT,
            artifact_metadata_json TEXT
        )
        """
    )
    request = ("Shanghai", "2026-08-12", "high")
    writer.execute(
        "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            OPENMETEO_ANCHOR_SOURCE_ID,
            OPENMETEO_ANCHOR_PRODUCT_ID,
            "2026-08-11T12:00:00+00:00",
            "2026-08-11T12:05:00+00:00",
            "2026-08-11T12:05:00+00:00",
            str(artifact_path),
            json.dumps(
                {
                    "city": request[0],
                    "target_date": request[1],
                    "metric": request[2],
                    "openmeteo_payload_json": payload_path.name,
                }
            ),
        ),
    )
    writer.execute(
        "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            OPENMETEO_ANCHOR_SOURCE_ID,
            OPENMETEO_ANCHOR_PRODUCT_ID,
            "2026-08-11T06:00:00+00:00",
            "2026-08-11T06:05:00+00:00",
            "2026-08-11T06:05:00+00:00",
            str(artifact_path),
            json.dumps(
                {
                    "city": request[0],
                    "target_date": request[1],
                    "metric": request[2],
                    "openmeteo_payload_json": payload_path.name,
                }
            ),
        ),
    )
    writer.commit()
    writer.close()

    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
        factory=TrackingConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.progress_transitions = []
    conn.execute("PRAGMA busy_timeout = 777")
    validation_calls = 0
    validation_transition_counts: list[int] = []
    clock = [0.0]
    monkeypatch.setattr(input_hwm.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(input_hwm.time, "thread_time", lambda: 0.0)

    def validate_payload(**_kwargs):
        nonlocal validation_calls
        validation_calls += 1
        assert conn.progress_active is False
        validation_transition_counts.append(len(conn.progress_transitions))
        if validation_calls == 1:
            clock[0] = 0.11
        return validation_calls == 2

    monkeypatch.setattr(
        input_hwm,
        "_cached_artifact_payload_covers_target_local_day",
        validate_payload,
    )
    conn.execute("BEGIN")
    try:
        snapshot = freeze_replacement_artifact_hwm(
            conn,
            requests=(request,),
            decision_time=datetime(2026, 8, 11, 13, tzinfo=UTC),
            deadline_monotonic=1.0,
            sql_timeout_seconds=0.1,
        )
        restored_busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.rollback()
        conn.close()

    assert snapshot.artifact_cycles[request] == datetime(
        2026, 8, 11, 6, tzinfo=UTC
    )
    assert validation_calls == 2
    assert validation_transition_counts[1] > validation_transition_counts[0]
    assert True in conn.progress_transitions
    assert conn.progress_transitions[-1] is False
    assert restored_busy_timeout == 777

    clock[0] = 0.0

    def validation_exhausts_outer_deadline(**_kwargs):
        assert conn.progress_active is False
        clock[0] = 1.0
        return True

    monkeypatch.setattr(
        input_hwm,
        "_cached_artifact_payload_covers_target_local_day",
        validation_exhausts_outer_deadline,
    )
    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
        factory=TrackingConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.progress_transitions = []
    conn.execute("PRAGMA busy_timeout = 555")
    conn.execute("BEGIN")
    try:
        with pytest.raises(ReplacementInputHwmReadUnavailable) as exc_info:
            freeze_replacement_artifact_hwm(
                conn,
                requests=(request,),
                decision_time=datetime(2026, 8, 11, 13, tzinfo=UTC),
                deadline_monotonic=1.0,
                sql_timeout_seconds=0.1,
            )
        restored_busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.rollback()
        conn.close()

    assert exc_info.value.basis == (
        "raw_artifact_input_hwm_payload_validation_deadline"
    )
    assert conn.progress_active is False
    assert restored_busy_timeout == 555


def test_cycle_hwm_interrupted_sql_fails_closed_and_removes_handler(
    tmp_path,
) -> None:
    class InterruptingConnection(sqlite3.Connection):
        progress_active = False

        def set_progress_handler(self, progress_handler, n):
            self.progress_active = progress_handler is not None
            return super().set_progress_handler(progress_handler, n)

        def execute(self, sql, parameters=(), /):
            if self.progress_active and "SELECT source_cycle_time" in sql:
                raise sqlite3.OperationalError("interrupted")
            return super().execute(sql, parameters)

    db_path = tmp_path / "forecast.db"
    writer = sqlite3.connect(db_path)
    writer.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT,
            product_id TEXT,
            source_cycle_time TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            artifact_path TEXT,
            artifact_metadata_json TEXT
        )
        """
    )
    writer.commit()
    writer.close()

    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
        factory=InterruptingConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    try:
        with pytest.raises(ReplacementInputHwmReadUnavailable) as exc_info:
            freeze_replacement_artifact_hwm(
                conn,
                requests=(("Shanghai", "2026-08-12", "high"),),
                decision_time=datetime(2026, 8, 11, 13, tzinfo=UTC),
                deadline_monotonic=time.monotonic() + 1.0,
                sql_timeout_seconds=0.1,
            )
    finally:
        conn.rollback()
        conn.close()

    assert exc_info.value.basis == "raw_artifact_input_hwm_read_unavailable"
    assert conn.progress_active is False


def test_cycle_hwm_expired_sql_deadline_fails_closed(tmp_path) -> None:
    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE raw_forecast_artifacts (source_cycle_time TEXT)")
    conn.commit()
    conn.execute("BEGIN")
    try:
        with pytest.raises(ReplacementInputHwmReadUnavailable) as exc_info:
            freeze_replacement_artifact_hwm(
                conn,
                requests=(("Shanghai", "2026-08-12", "high"),),
                decision_time=datetime(2026, 8, 11, 13, tzinfo=UTC),
                deadline_monotonic=time.monotonic() - 0.001,
            )
    finally:
        conn.rollback()
        conn.close()

    assert exc_info.value.blocker_reason().startswith(
        "basis=raw_artifact_input_hwm_sql_deadline:sqlite_error="
    )


def test_cycle_hwm_sql_cpu_deadline_fails_closed_and_removes_handler(
    tmp_path,
    monkeypatch,
) -> None:
    class TrackingConnection(sqlite3.Connection):
        progress_active = False

        def set_progress_handler(self, progress_handler, n):
            self.progress_active = progress_handler is not None
            return super().set_progress_handler(progress_handler, n)

    conn = sqlite3.connect(
        tmp_path / "forecast.db",
        factory=TrackingConnection,
    )
    cpu_clock = iter((0.0, 0.11))
    monkeypatch.setattr(input_hwm.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(input_hwm.time, "thread_time", lambda: next(cpu_clock))

    try:
        with pytest.raises(ReplacementInputHwmReadUnavailable) as exc_info:
            with input_hwm._bounded_hwm_sql(
                conn,
                deadline_monotonic=1.0,
                sql_timeout_seconds=0.1,
            ):
                conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()

    assert exc_info.value.basis == "raw_artifact_input_hwm_sql_deadline"
    assert conn.progress_active is False


def test_cycle_hwm_zero_sql_timeout_without_outer_deadline_fails_closed(
    tmp_path,
) -> None:
    conn = sqlite3.connect(tmp_path / "forecast.db")
    try:
        with pytest.raises(ReplacementInputHwmReadUnavailable) as exc_info:
            with input_hwm._bounded_hwm_sql(
                conn,
                deadline_monotonic=None,
                sql_timeout_seconds=0.0,
            ):
                conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()

    assert exc_info.value.basis == "raw_artifact_input_hwm_sql_deadline"


def test_cycle_hwm_real_vm_cpu_deadline_interrupts_and_restores_connection(
    tmp_path,
) -> None:
    conn = sqlite3.connect(tmp_path / "forecast.db")
    conn.execute("PRAGMA busy_timeout = 432")
    try:
        with pytest.raises(ReplacementInputHwmReadUnavailable) as exc_info:
            with input_hwm._bounded_hwm_sql(
                conn,
                deadline_monotonic=time.monotonic() + 1.0,
                sql_timeout_seconds=0.001,
            ):
                conn.execute(
                    """
                    WITH RECURSIVE seq(value) AS (
                        VALUES(0)
                        UNION ALL
                        SELECT value + 1 FROM seq WHERE value < 10000000
                    )
                    SELECT SUM(value) FROM seq
                    """
                ).fetchone()
    finally:
        restored_busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()

    assert exc_info.value.basis == "raw_artifact_input_hwm_read_unavailable"
    assert restored_busy_timeout == 432


def test_cycle_hwm_payload_cache_preserves_absent_path_semantics(tmp_path) -> None:
    artifact_path = tmp_path / "manifest.json"
    common = {
        "artifact_path": str(artifact_path),
        "city_timezone": "UTC",
        "target_date": "2026-08-12",
    }

    assert input_hwm._cached_artifact_payload_covers_target_local_day(
        payload_path="",
        **common,
    )
    assert input_hwm._cached_artifact_payload_covers_target_local_day(
        payload_path="   ",
        **common,
    )
    assert not input_hwm._cached_artifact_payload_covers_target_local_day(
        payload_path="bad\x00path",
        **common,
    )


def test_failed_cycle_hwm_snapshot_blocks_scalar_fanout() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_forecast_artifacts (source_cycle_time TEXT)"
    )
    decision_time = datetime(2026, 8, 11, 13, tzinfo=UTC)
    request = ("Shanghai", "2026-08-12", "high")
    snapshot = frozen_replacement_artifact_hwm_unavailable(
        requests=(request,),
        decision_time=decision_time,
        blocker_reason="forced batch deadline",
    )
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    release = install_frozen_replacement_artifact_hwm(snapshot)
    try:
        with pytest.raises(ReplacementInputHwmReadUnavailable) as raised:
            latest_raw_artifact_input_cycle(
                conn,
                city=request[0],
                target_date=request[1],
                metric=request[2],
                decision_time=decision_time,
            )
    finally:
        release()
        conn.close()

    assert raised.value.basis == "frozen_artifact_input_hwm_prefetch_unavailable"
    assert not any(
        "FROM RAW_FORECAST_ARTIFACTS" in statement.upper() for statement in traced
    )


def test_held_hwm_prefetch_batches_unique_families(monkeypatch) -> None:
    import src.data.replacement_input_hwm as input_hwm
    import src.engine.cycle_runtime as runtime
    import src.engine.monitor_refresh as monitor_refresh
    import src.state.db as db

    positions = [
        SimpleNamespace(
            trade_id="shanghai-yes",
            city="Shanghai",
            target_date="2026-08-12",
            temperature_metric="high",
        ),
        SimpleNamespace(
            trade_id="shanghai-no",
            city="Shanghai",
            target_date="2026-08-12",
            temperature_metric="high",
        ),
        SimpleNamespace(
            trade_id="ankara-no",
            city="Ankara",
            target_date="2026-08-13",
            temperature_metric="high",
        ),
    ]
    captured_requests: list[frozenset[tuple[str, str, str]]] = []
    snapshot = object()

    observed_deadlines: list[float] = []

    def forecasts_connection(*, deadline_monotonic) -> sqlite3.Connection:
        observed_deadlines.append(deadline_monotonic)
        return sqlite3.connect(":memory:")

    def freeze(
        _conn,
        *,
        requests,
        decision_time,
        deadline_monotonic,
        sql_timeout_seconds,
    ):
        assert decision_time == datetime(2026, 8, 11, 13, tzinfo=UTC)
        assert deadline_monotonic > time.monotonic()
        assert sql_timeout_seconds > 0.0
        captured_requests.append(frozenset(requests))
        return snapshot

    installed: list[object] = []
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", forecasts_connection)
    monkeypatch.setattr(input_hwm, "freeze_replacement_artifact_hwm", freeze)
    monkeypatch.setattr(
        monitor_refresh,
        "install_monitor_replacement_hwm_snapshot",
        lambda _clob, value: installed.append(value) or True,
    )
    summary: dict[str, object] = {}
    runtime._prefetch_held_replacement_artifact_hwm(
        positions,
        decision_time=datetime(2026, 8, 11, 13, tzinfo=UTC),
        deadline_monotonic=time.monotonic() + 1.0,
        sql_timeout_seconds=0.25,
        clob=object(),
        summary=summary,
        deps=SimpleNamespace(logger=logging.getLogger(__name__)),
    )

    assert captured_requests == [
        frozenset(
            {
                ("Shanghai", "2026-08-12", "high"),
                ("Ankara", "2026-08-13", "high"),
            }
        )
    ]
    assert observed_deadlines and observed_deadlines[0] > time.monotonic()
    assert installed == [snapshot]
    assert summary["held_monitor_hwm_prefetch_family_count"] == 2
    assert summary["held_monitor_hwm_prefetch_status"] == "ready"


def test_preloaded_market_topology_hash_matches_database_read() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE market_events (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            range_label TEXT,
            outcome TEXT,
            range_low REAL,
            range_high REAL
        )
        """
    )
    rows = [
        {
            "condition_id": "c2",
            "range_label": "72F or above",
            "outcome": None,
            "range_low": 72.0,
            "range_high": None,
        },
        {
            "condition_id": "c0",
            "range_label": "69F or below",
            "outcome": None,
            "range_low": None,
            "range_high": 69.0,
        },
        {
            "condition_id": "c1",
            "range_label": "70-71F",
            "outcome": None,
            "range_low": 70.0,
            "range_high": 71.0,
        },
    ]
    conn.executemany(
        "INSERT INTO market_events VALUES ('Dallas', '2026-07-11', 'high', ?, ?, ?, ?, ?)",
        [
            (
                row["condition_id"],
                row["range_label"],
                row["outcome"],
                row["range_low"],
                row["range_high"],
            )
            for row in rows
        ],
    )

    assert reader.market_bin_topology_hash_from_rows(rows, city="Dallas") == (
        reader._current_market_bin_topology_hash(
            conn,
            city="Dallas",
            target_date="2026-07-11",
            temperature_metric="high",
        )
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    return conn


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _live_provenance() -> dict[str, object]:
    return {
        "reader_test": True,
        "replacement_q_mode": "FUSED_NORMAL_FULL",
        "q_lcb_basis": TRADEABLE_GRADE_QLCB_BASIS,
        "bin_topology_hash": "topology-hash",
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                "shape_lag_hours": 0.0,
                "stale_shape_reused": False,
                "translation_applied": False,
            }
        },
    }


def _with_current_value_serving(
    consumed: dict[str, dict[str, object]],
    *,
    anchor_artifact_id: int | None = None,
) -> dict[str, object]:
    provenance = _live_provenance()
    if anchor_artifact_id is not None:
        provenance["openmeteo_anchor_artifact_id"] = anchor_artifact_id
    fusion = provenance["bayes_precision_fusion"]
    assert isinstance(fusion, dict)
    fusion.update(
        {
            "used_models": list(consumed),
            "current_value_serving": consumed,
        }
    )
    return provenance


def _insert_posterior(
    conn: sqlite3.Connection,
    *,
    source_available_at: datetime | None = None,
    computed_at: datetime | None = None,
    training_allowed: int = 0,
    dependency_source_run_ids: dict[str, str] | None = None,
) -> int:
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, q_ucb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json,
            family_id, bin_topology_hash, dependency_hash, posterior_config_hash,
            posterior_identity_hash, runtime_layer, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_ID,
            PRODUCT_ID,
            HIGH_DATA_VERSION,
            "Shanghai",
            "2026-06-07",
            "high",
            "2026-06-06T00:00:00+00:00",
            (source_available_at or _dt(3)).isoformat(),
            (computed_at or _dt(3, 5)).isoformat(),
            json.dumps({"cold": 0.2, "warm": 0.8}),
            json.dumps({"cold": 0.1, "warm": 0.7}),
            json.dumps({"cold": 0.3, "warm": 0.9}),
            "openmeteo_ecmwf_ifs9_bayes_fusion",
            json.dumps(
                dependency_source_run_ids
                or {
                    "baseline_b0": "b0-run",
                    "openmeteo_ifs9_anchor": "om9-run",
                }
            ),
            json.dumps(_live_provenance()),
            "Shanghai:2026-06-07:high:topology-hash",
            "topology-hash",
            "dependency-hash",
            "config-hash",
            f"identity-{(computed_at or _dt(3, 5)).isoformat()}-{(source_available_at or _dt(3)).isoformat()}",
            LIVE_RUNTIME_LAYER,
            training_allowed,
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _readiness(*, posterior_id: int, baseline_run_id: str = "b0-run", posterior_available_at: datetime | None = None):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id=baseline_run_id,
            source_available_at=_dt(2),
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="om9-run",
            source_available_at=_dt(2),
            anchor_id=22,
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version=HIGH_DATA_VERSION,
            source_run_id="posterior-run",
            source_available_at=posterior_available_at or _dt(3),
            posterior_id=posterior_id,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        computed_at=_dt(4, 1),
        expires_at=_dt(6),
        dependencies=dependencies,
    )


def _insert_raw_model_forecast(
    conn: sqlite3.Connection,
    *,
    model: str,
    source_cycle_time: datetime,
    captured_at: datetime,
    source_available_at: datetime,
    city: str = "Shanghai",
    target_date: str = "2026-06-07",
    metric: str = "high",
    endpoint: str = "single_runs",
) -> None:
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time,
            source_available_at, captured_at, lead_days, forecast_value_c, endpoint,
            coverage_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model,
            city,
            target_date,
            metric,
            source_cycle_time.isoformat(),
            source_available_at.isoformat(),
            captured_at.isoformat(),
            1,
            28.0,
            endpoint,
            "COVERED",
        ),
    )


def _insert_openmeteo_anchor_artifact(
    conn: sqlite3.Connection,
    tmp_path,
    *,
    source_cycle_time: datetime,
    city: str = "Shanghai",
    target_date: str = "2026-06-07",
    metric: str = "high",
    payload_dates: tuple[str, ...] | None = None,
) -> int:
    payload = tmp_path / (
        f"openmeteo-{city}-{target_date}-{metric}-"
        f"{source_cycle_time.strftime('%H%M')}.json"
    )
    covered_dates = payload_dates or (target_date,)
    payload_bytes = json.dumps(
        {
            "city": city,
            "hourly": {
                "time": [
                    stamp
                    for covered_date in covered_dates
                    for stamp in (
                        f"{covered_date}T00:00",
                        f"{covered_date}T12:00",
                    )
                ],
                "temperature_2m": [
                    value
                    for _covered_date in covered_dates
                    for value in (22.0, 28.0)
                ],
            },
        },
        sort_keys=True,
    ).encode()
    payload.write_bytes(payload_bytes)
    available_at = source_cycle_time + timedelta(minutes=5)
    conn.execute(
        """
        INSERT INTO raw_forecast_artifacts (
            source_id, product_id, data_version, source_cycle_time,
            source_available_at, captured_at, artifact_path, sha256,
            byte_size, artifact_metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            OPENMETEO_ANCHOR_SOURCE_ID,
            OPENMETEO_ANCHOR_PRODUCT_ID,
            f"openmeteo_ecmwf_ifs9_anchor_localday_{metric}",
            source_cycle_time.isoformat(),
            available_at.isoformat(),
            available_at.isoformat(),
            str(payload),
            hashlib.sha256(payload_bytes).hexdigest(),
            len(payload_bytes),
            json.dumps(
                {
                    "city": city,
                    "target_date": target_date,
                    "metric": metric,
                    "openmeteo_payload_json": str(payload),
                }
            ),
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def test_current_value_hwm_uses_consumed_models_not_configured_superset() -> None:
    conn = _conn()
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "icon_eu"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(0),
            captured_at=_dt(0, 5),
            source_available_at=_dt(0, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(0).isoformat(),
            "captured_at": _dt(0, 5).isoformat(),
            "served_via": "single_runs",
        }
    provenance = _with_current_value_serving(consumed)
    fusion = provenance["bayes_precision_fusion"]
    assert isinstance(fusion, dict)
    fusion["source_clock_one_scheme"] = {
        "configured_sources": ["ecmwf_ifs", "icon_eu", "icon_d2"],
        "used_weights": {"ecmwf_ifs": 0.6, "icon_eu": 0.4},
    }

    checked, reason, _anchor = _exact_current_value_serving_lag(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(4),
        posterior_computed_at=_dt(3),
        provenance=provenance,
    )

    assert checked is True
    assert reason is None

    del consumed["icon_eu"]
    _checked, reason, _anchor = _exact_current_value_serving_lag(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(4),
        posterior_computed_at=_dt(3),
        provenance=provenance,
    )
    assert reason == "basis=current_value_serving_provenance_unverifiable:model=icon_eu"


def test_exact_anchor_artifact_accepts_consumed_day_covered_by_multiday_payload(
    tmp_path,
) -> None:
    conn = _conn()
    artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(3),
        target_date="2026-06-06",
        payload_dates=("2026-06-06", "2026-06-07"),
    )
    provenance = {"openmeteo_anchor_artifact_id": artifact_id}

    reason, cycle = _exact_consumed_anchor_artifact_cycle(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(5),
        provenance=provenance,
    )
    assert reason is None
    assert cycle == _dt(3)

    reason, cycle = _exact_consumed_anchor_artifact_cycle(
        conn,
        city="Shanghai",
        target_date="2026-06-08",
        metric="high",
        decision_time=_dt(5),
        provenance=provenance,
    )
    assert reason == f"basis=openmeteo_anchor_artifact_scope_mismatch:artifact_id={artifact_id}"
    assert cycle is None


def test_public_hwm_always_validates_declared_multiday_anchor_artifact(tmp_path) -> None:
    conn = _conn()
    _insert_raw_model_forecast(
        conn,
        model="ecmwf_ifs",
        source_cycle_time=_dt(0),
        captured_at=_dt(0, 5),
        source_available_at=_dt(0, 5),
    )
    consumed = {
        "ecmwf_ifs": {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(0).isoformat(),
            "captured_at": _dt(0, 5).isoformat(),
            "served_via": "single_runs",
        }
    }
    artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(3),
        target_date="2026-06-06",
        payload_dates=("2026-06-06", "2026-06-07"),
    )
    provenance = _with_current_value_serving(
        consumed,
        anchor_artifact_id=artifact_id,
    )

    covered = replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(5),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(3),
        posterior_provenance=provenance,
    )
    assert covered is None

    _insert_raw_model_forecast(
        conn,
        model="ecmwf_ifs",
        target_date="2026-06-08",
        source_cycle_time=_dt(0),
        captured_at=_dt(0, 5),
        source_available_at=_dt(0, 5),
    )
    uncovered_provenance = _with_current_value_serving(
        {
            "ecmwf_ifs": {
                "raw_model_forecast_id": int(
                    conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                ),
                "served_cycle": _dt(0).isoformat(),
                "captured_at": _dt(0, 5).isoformat(),
                "served_via": "single_runs",
            }
        },
        anchor_artifact_id=artifact_id,
    )
    uncovered = replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-06-08",
        metric="high",
        decision_time=_dt(5),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(3),
        posterior_provenance=uncovered_provenance,
    )
    assert uncovered == (
        f"basis=openmeteo_anchor_artifact_scope_mismatch:artifact_id={artifact_id}"
    )


def test_replacement_bundle_reader_requires_baseline_executable_bundle() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=None,
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_BASELINE_EXECUTABLE_FORECAST_REQUIRED"


def test_replacement_bundle_reader_returns_posterior_when_b0_and_readiness_match() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == posterior_id
    assert result.bundle.baseline_source_run_id == "b0-run"
    assert result.bundle.q == pytest.approx({"cold": 0.2, "warm": 0.8})
    assert result.bundle.q_lcb == pytest.approx({"cold": 0.1, "warm": 0.7})
    assert result.bundle.runtime_layer == LIVE_RUNTIME_LAYER
    assert result.bundle.posterior_identity_hash == (
        f"identity-{_dt(3, 5).isoformat()}-{_dt(3).isoformat()}"
    )
    assert result.bundle.dependency_hash == "dependency-hash"
    assert result.bundle.posterior_config_hash == "config-hash"


def test_replacement_bundle_reader_binds_to_readiness_posterior_not_latest_scope_row() -> None:
    conn = _conn()
    certified_posterior_id = _insert_posterior(conn, computed_at=_dt(3, 5))
    newer_posterior_id = _insert_posterior(conn, computed_at=_dt(3, 20))
    conn.execute(
        """
        UPDATE forecast_posteriors
           SET posterior_identity_hash = ?, dependency_hash = ?, posterior_config_hash = ?
         WHERE posterior_id = ?
        """,
        (
            "certified-identity",
            "certified-dependency",
            "certified-config",
            certified_posterior_id,
        ),
    )
    conn.execute(
        """
        UPDATE forecast_posteriors
           SET posterior_identity_hash = ?, dependency_hash = ?, posterior_config_hash = ?
         WHERE posterior_id = ?
        """,
        ("latest-identity", "latest-dependency", "latest-config", newer_posterior_id),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=certified_posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == certified_posterior_id
    assert result.bundle.posterior_id != newer_posterior_id
    assert result.bundle.posterior_identity_hash == "certified-identity"
    assert result.bundle.dependency_hash == "certified-dependency"
    assert result.bundle.posterior_config_hash == "certified-config"


def test_replacement_bundle_reader_blocks_unready_readiness_or_mismatched_ids() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)

    blocked_readiness = _readiness(posterior_id=posterior_id, posterior_available_at=_dt(5))
    blocked = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=blocked_readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert blocked.reason_code == "REPLACEMENT_READINESS_NOT_READY"

    mismatch = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("different-b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert mismatch.reason_code == "REPLACEMENT_BASELINE_READINESS_MISMATCH"

    posterior_mismatch = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id + 100),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert posterior_mismatch.reason_code == "REPLACEMENT_POSTERIOR_READINESS_MISMATCH"


def test_replacement_bundle_reader_blocks_dependency_source_run_drift() -> None:
    conn = _conn()
    openmeteo_drift_id = _insert_posterior(
        conn,
        dependency_source_run_ids={
            "baseline_b0": "b0-run",
            "openmeteo_ifs9_anchor": "wrong-om9-run",
        },
    )

    openmeteo_drift = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=openmeteo_drift_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert openmeteo_drift.reason_code == "REPLACEMENT_DEPENDENCY_SOURCE_RUN_MISMATCH"


def test_replacement_bundle_reader_blocks_missing_or_late_posterior() -> None:
    conn = _conn()
    missing = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=1),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert missing.reason_code == "REPLACEMENT_POSTERIOR_MISSING"

    late_id = _insert_posterior(conn, source_available_at=_dt(5))
    late = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=late_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert late.reason_code == "REPLACEMENT_POSTERIOR_AFTER_DECISION_TIME"

    conn = _conn()
    computed_late_id = _insert_posterior(conn, computed_at=_dt(5))
    computed_late = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=computed_late_id),
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )
    assert computed_late.reason_code == "REPLACEMENT_POSTERIOR_COMPUTED_AFTER_DECISION_TIME"


def test_replacement_bundle_reader_enforce_raw_input_hwm_blocks_stale_serve() -> None:
    """W0.1: when opted in, a raw input newer than the served posterior's source_cycle_time
    must block the read instead of serving the stale posterior."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)  # source_cycle_time = 2026-06-06T00:00:00+00:00
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is False
    assert result.reason_code.startswith("REPLACEMENT_RAW_INPUT_HWM:")
    assert "latest_raw_cycle=2026-06-06T03:00:00+00:00" in result.reason_code
    assert "posterior_cycle=2026-06-06T00:00:00+00:00" in result.reason_code


def test_replacement_bundle_reader_raw_input_hwm_default_is_byte_identical() -> None:
    """W0.1: enforce_raw_input_hwm defaults to False — a caller that never opts in must
    keep serving the SAME posterior even when a newer raw input cycle exists."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == posterior_id


def test_replacement_bundle_reader_enforce_raw_input_hwm_allows_fresh_serve() -> None:
    """W0.1: opting in must not block a posterior that is already the freshest input."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)  # source_cycle_time = 2026-06-06T00:00:00+00:00
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(0),
            captured_at=_dt(0, 5),
            source_available_at=_dt(0, 5),
        )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"


def test_replacement_bundle_reader_hwm_budget_starts_at_hwm_stage(monkeypatch) -> None:
    """A slow prior snapshot stage must not consume the independent HWM budget."""
    conn = _conn()
    hwm_conn = sqlite3.connect(":memory:")
    posterior_id = _insert_posterior(conn)
    clock = [4.0]

    def read_hwm(active_conn, **_kwargs):
        assert active_conn is hwm_conn
        clock[0] += 3.0
        return None

    monkeypatch.setattr(reader.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(reader, "replacement_live_input_lag_reason", read_hwm)

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
        raw_input_hwm_conn=hwm_conn,
        raw_input_hwm_deadline_monotonic=75.0,
        raw_input_hwm_read_max_seconds=5.0,
    )

    hwm_conn.close()
    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"


def test_replacement_bundle_reader_hwm_never_crosses_outer_deadline(
    monkeypatch,
) -> None:
    conn = _conn()
    hwm_conn = sqlite3.connect(":memory:")
    posterior_id = _insert_posterior(conn)
    monkeypatch.setattr(reader.time, "monotonic", lambda: 76.0)
    monkeypatch.setattr(
        reader,
        "replacement_live_input_lag_reason",
        lambda *_args, **_kwargs: pytest.fail("expired HWM stage must not start"),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
        raw_input_hwm_conn=hwm_conn,
        raw_input_hwm_deadline_monotonic=75.0,
        raw_input_hwm_read_max_seconds=5.0,
    )

    hwm_conn.close()
    assert result.ok is False
    assert result.reason_code == (
        "REPLACEMENT_RAW_INPUT_HWM:basis=HWM_READ_DEADLINE"
    )


def test_replacement_bundle_reader_hwm_cleanup_failure_is_not_masked(
    monkeypatch,
) -> None:
    class FaultedCleanupConnection:
        def set_progress_handler(self, callback, _instructions):
            if callback is None:
                raise sqlite3.OperationalError("handler cleanup failed")

    conn = _conn()
    posterior_id = _insert_posterior(conn)
    monkeypatch.setattr(reader.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(
        reader,
        "replacement_live_input_lag_reason",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="HWM_READ_CLEANUP_FAILED"):
        read_replacement_forecast_bundle(
            conn,
            baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
            readiness=_readiness(posterior_id=posterior_id),
            city="Shanghai",
            target_date="2026-06-07",
            temperature_metric="high",
            decision_time=_dt(4),
            current_bin_topology_hash="topology-hash",
            enforce_raw_input_hwm=True,
            raw_input_hwm_conn=FaultedCleanupConnection(),
            raw_input_hwm_deadline_monotonic=75.0,
            raw_input_hwm_read_max_seconds=5.0,
        )


def test_replacement_bundle_reader_default_hwm_read_preserves_caller_handler(
    monkeypatch,
) -> None:
    conn = _conn()
    hwm_conn = sqlite3.connect(":memory:")
    posterior_id = _insert_posterior(conn)
    monkeypatch.setattr(
        reader,
        "replacement_live_input_lag_reason",
        lambda *_args, **_kwargs: None,
    )
    hwm_conn.set_progress_handler(lambda: 1, 1)

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
        raw_input_hwm_conn=hwm_conn,
    )

    assert result.ok is True
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        hwm_conn.execute(
            """
            WITH RECURSIVE spin(value) AS (
                SELECT 1 UNION ALL SELECT value + 1 FROM spin
            )
            SELECT SUM(value) FROM spin
            """
        ).fetchone()
    hwm_conn.close()


def test_current_value_serving_sqlite_interrupt_is_typed_and_chained() -> None:
    conn = _conn()
    _insert_raw_model_forecast(
        conn,
        model="gfs",
        source_cycle_time=_dt(0),
        captured_at=_dt(0, 5),
        source_available_at=_dt(0, 5),
    )
    conn.set_progress_handler(lambda: 1, 1)

    with pytest.raises(CurrentValueServingReadUnavailable) as raised:
        read_current_instrument_values(
            conn,
            city="Shanghai",
            metric="high",
            target_date="2026-06-07",
            source_cycle_time_iso=_dt(0).isoformat(),
            include_station_sources=True,
            decision_time_iso=_dt(4).isoformat(),
        )

    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
    assert str(raised.value.__cause__) == "interrupted"
    assert str(raised.value) == "interrupted"
    assert isinstance(raised.value, sqlite3.OperationalError)


@pytest.mark.parametrize(
    "message",
    [
        "database is locked",
        "database is busy",
        "sqlite_read_deadline_exceeded",
        "sqlite_read_cancelled",
    ],
)
def test_current_value_serving_transient_read_is_typed_and_chained(message: str) -> None:
    conn = _conn()

    class FaultingConnection:
        def execute(self, sql, params=()):
            if "datetime(source_cycle_time)" in sql:
                raise sqlite3.OperationalError(message)
            return conn.execute(sql, params)

    with pytest.raises(CurrentValueServingReadUnavailable) as raised:
        read_current_instrument_values(
            FaultingConnection(),
            city="Shanghai",
            metric="high",
            target_date="2026-06-07",
            source_cycle_time_iso=_dt(0).isoformat(),
            decision_time_iso=_dt(4).isoformat(),
        )

    assert str(raised.value) == message
    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
    assert str(raised.value.__cause__) == message


@pytest.mark.parametrize(
    "message",
    ["no such table: raw_model_forecasts", "no such column: captured_at", "near SELECT: syntax error"],
)
def test_current_value_serving_schema_errors_are_not_retyped(message: str) -> None:
    conn = _conn()

    class FaultingConnection:
        def execute(self, sql, params=()):
            if "datetime(source_cycle_time)" in sql:
                raise sqlite3.OperationalError(message)
            return conn.execute(sql, params)

    with pytest.raises(sqlite3.OperationalError) as raised:
        read_current_instrument_values(
            FaultingConnection(),
            city="Shanghai",
            metric="high",
            target_date="2026-06-07",
            source_cycle_time_iso=_dt(0).isoformat(),
            decision_time_iso=_dt(4).isoformat(),
        )

    assert type(raised.value) is sqlite3.OperationalError
    assert str(raised.value) == message
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("needle", "message", "typed"),
    [
        ("pragma table_info", "interrupted", True),
        ("from raw_model_forecasts", "database is locked", True),
        ("pragma table_info", "no such table: raw_model_forecasts", False),
        ("from raw_model_forecasts", "no such column: forecast_value_c", False),
    ],
)
def test_legacy_current_value_reads_never_turn_sqlite_faults_into_empty(
    needle: str,
    message: str,
    typed: bool,
) -> None:
    conn = _conn()

    class FaultingConnection:
        def execute(self, sql, params=()):
            if needle in " ".join(str(sql).split()).lower():
                raise sqlite3.OperationalError(message)
            return conn.execute(sql, params)

    expected = CurrentValueServingReadUnavailable if typed else sqlite3.OperationalError
    with pytest.raises(expected) as raised:
        read_current_instrument_values(
            FaultingConnection(),
            city="Shanghai",
            metric="high",
            target_date="2026-06-07",
            source_cycle_time_iso=_dt(0).isoformat(),
        )

    assert str(raised.value) == message
    if typed:
        assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
    else:
        assert type(raised.value) is sqlite3.OperationalError
        assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "message",
    [
        "no such table: deadline",
        "no such column: busy",
        'near "interrupted": syntax error',
        "constraint failed after request cancelled",
    ],
)
def test_transient_words_inside_programming_errors_are_not_retyped(
    message: str,
) -> None:
    conn = _conn()

    class FaultingConnection:
        def execute(self, sql, params=()):
            if "datetime(source_cycle_time)" in sql:
                raise sqlite3.OperationalError(message)
            return conn.execute(sql, params)

    with pytest.raises(sqlite3.OperationalError) as raised:
        read_current_instrument_values(
            FaultingConnection(),
            city="Shanghai",
            metric="high",
            target_date="2026-06-07",
            source_cycle_time_iso=_dt(0).isoformat(),
            decision_time_iso=_dt(4).isoformat(),
        )

    assert type(raised.value) is sqlite3.OperationalError
    assert str(raised.value) == message
    assert raised.value.__cause__ is None


class _FaultingHwmConnection:
    def __init__(self, conn: sqlite3.Connection, *, needle: str, message: str):
        self._conn = conn
        self._needle = needle.lower()
        self._message = message

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        if self._needle in normalized:
            raise sqlite3.OperationalError(self._message)
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.mark.parametrize(
    "message",
    [
        "no such table: deadline",
        "no such column: busy",
        'near "interrupted": syntax error',
    ],
)
def test_hwm_transient_words_inside_schema_errors_are_not_retyped(
    message: str,
) -> None:
    conn = _conn()
    faulting = _FaultingHwmConnection(
        conn,
        needle="having count(distinct model)",
        message=message,
    )

    with pytest.raises(sqlite3.OperationalError) as raised:
        latest_raw_model_input_cycle(
            faulting,
            city="Shanghai",
            target_date="2026-06-07",
            metric="high",
            decision_time=_dt(4),
        )

    assert type(raised.value) is sqlite3.OperationalError
    assert str(raised.value) == message


@pytest.mark.parametrize(
    "message",
    ["sqlite_read_deadline_exceeded", "sqlite_read_cancelled"],
)
def test_hwm_owned_read_sentinels_are_typed(message: str) -> None:
    conn = _conn()
    faulting = _FaultingHwmConnection(
        conn,
        needle="having count(distinct model)",
        message=message,
    )

    with pytest.raises(ReplacementInputHwmReadUnavailable) as raised:
        latest_raw_model_input_cycle(
            faulting,
            city="Shanghai",
            target_date="2026-06-07",
            metric="high",
            decision_time=_dt(4),
        )

    assert str(raised.value) == message
    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)


@pytest.mark.parametrize(
    ("helper", "needle"),
    [
        ("raw_model", "having count(distinct model)"),
        ("raw_artifact", "pragma data_version"),
        ("provenance", "select provenance_json, computed_at"),
    ],
)
def test_hwm_read_programming_faults_are_not_absence(
    helper: str,
    needle: str,
) -> None:
    conn = _conn()

    class FaultingConnection:
        def execute(self, sql, params=()):
            if needle in " ".join(str(sql).split()).lower():
                raise RuntimeError("read programming fault")
            return conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(conn, name)

    faulting = FaultingConnection()
    with pytest.raises(RuntimeError, match="read programming fault"):
        if helper == "raw_model":
            latest_raw_model_input_cycle(
                faulting,
                city="Shanghai",
                target_date="2026-06-07",
                metric="high",
                decision_time=_dt(4),
            )
        elif helper == "raw_artifact":
            latest_raw_artifact_input_cycle(
                faulting,
                city="Shanghai",
                target_date="2026-06-07",
                metric="high",
                decision_time=_dt(4),
            )
        else:
            _posterior_provenance_for_cycle(
                faulting,
                city="Shanghai",
                target_date="2026-06-07",
                metric="high",
                posterior_source_cycle_time=_dt(0),
                posterior_computed_at=_dt(3, 5),
            )


def test_frozen_artifact_hwm_batch_fault_does_not_fall_back_to_scalar(
    monkeypatch,
) -> None:
    import src.data.replacement_input_hwm as hwm

    conn = _conn()
    conn.commit()
    conn.execute("BEGIN")

    def fail_batch(*_args, **_kwargs):
        raise RuntimeError("batch programming fault")

    monkeypatch.setattr(hwm, "_batch_artifact_cycles", fail_batch)
    with pytest.raises(RuntimeError, match="batch programming fault"):
        prime_frozen_replacement_artifact_hwm(
            conn,
            requests=(("Shanghai", "2026-06-07", "high"),),
            decision_time=_dt(4),
        )


@pytest.mark.parametrize(
    ("helper", "needle", "basis"),
    [
        ("provenance", "select provenance_json, computed_at", "posterior_provenance_hwm_read_unavailable"),
        ("raw_model", "having count(distinct model)", "raw_model_input_hwm_read_unavailable"),
        ("raw_artifact", "from raw_forecast_artifacts", "raw_artifact_input_hwm_read_unavailable"),
        ("used_raw", "and model in (", "used_raw_model_input_hwm_read_unavailable"),
    ],
)
def test_hwm_helpers_keep_locked_reads_distinct_from_absence(
    helper: str,
    needle: str,
    basis: str,
) -> None:
    conn = _conn()
    faulting = _FaultingHwmConnection(
        conn,
        needle=needle,
        message="database is locked",
    )
    common = {
        "conn": faulting,
        "city": "Shanghai",
        "target_date": "2026-06-07",
        "metric": "high",
    }

    with pytest.raises(ReplacementInputHwmReadUnavailable) as raised:
        if helper == "provenance":
            _posterior_provenance_for_cycle(
                **common,
                posterior_source_cycle_time=_dt(0),
                posterior_computed_at=_dt(0, 5),
            )
        elif helper == "raw_model":
            latest_raw_model_input_cycle(
                **common,
                decision_time=_dt(4),
            )
        elif helper == "raw_artifact":
            latest_raw_artifact_input_cycle(
                **common,
                decision_time=_dt(4),
            )
        else:
            latest_used_raw_model_input_mark(
                **common,
                decision_time=_dt(4),
                posterior_source_cycle_time=_dt(0),
                posterior_provenance={"used_models": ["gfs"]},
            )

    assert raised.value.basis == basis
    assert str(raised.value) == "database is locked"
    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)


@pytest.mark.parametrize(
    ("helper", "needle"),
    [
        ("provenance", "select provenance_json, computed_at"),
        ("raw_model", "having count(distinct model)"),
        ("raw_artifact", "from raw_forecast_artifacts"),
        ("used_raw", "and model in ("),
    ],
)
def test_hwm_helpers_do_not_retype_schema_errors(helper: str, needle: str) -> None:
    conn = _conn()
    faulting = _FaultingHwmConnection(
        conn,
        needle=needle,
        message="no such column: broken_hwm_column",
    )
    common = {
        "conn": faulting,
        "city": "Shanghai",
        "target_date": "2026-06-07",
        "metric": "high",
    }

    with pytest.raises(sqlite3.OperationalError) as raised:
        if helper == "provenance":
            _posterior_provenance_for_cycle(
                **common,
                posterior_source_cycle_time=_dt(0),
                posterior_computed_at=_dt(0, 5),
            )
        elif helper == "raw_model":
            latest_raw_model_input_cycle(
                **common,
                decision_time=_dt(4),
            )
        elif helper == "raw_artifact":
            latest_raw_artifact_input_cycle(
                **common,
                decision_time=_dt(4),
            )
        else:
            latest_used_raw_model_input_mark(
                **common,
                decision_time=_dt(4),
                posterior_source_cycle_time=_dt(0),
                posterior_provenance={"used_models": ["gfs"]},
            )

    assert type(raised.value) is sqlite3.OperationalError
    assert str(raised.value) == "no such column: broken_hwm_column"


def test_raw_hwm_does_not_label_current_value_read_failure_as_raw_unavailable(
    monkeypatch,
) -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    consumed = {
        "gfs": {
            "raw_model_forecast_id": 1,
            "served_cycle": _dt(0).isoformat(),
            "captured_at": _dt(0, 5).isoformat(),
            "served_via": "single_runs",
        }
    }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(_with_current_value_serving(consumed)), posterior_id),
    )

    import src.data.replacement_current_value_serving as serving

    def fail_read(*_args, **_kwargs):
        cause = sqlite3.OperationalError("interrupted")
        raise CurrentValueServingReadUnavailable(str(cause)) from cause

    monkeypatch.setattr(serving, "read_current_instrument_values", fail_read)
    with pytest.raises(ReplacementInputHwmReadUnavailable) as blocked:
        _exact_current_value_serving_lag(
            conn,
            city="Shanghai",
            target_date="2026-06-07",
            metric="high",
            decision_time=_dt(4),
            posterior_computed_at=_dt(0, 5),
            provenance=_with_current_value_serving(consumed),
        )
    assert isinstance(blocked.value.__cause__, CurrentValueServingReadUnavailable)
    assert isinstance(blocked.value.__cause__.__cause__, sqlite3.OperationalError)
    assert str(blocked.value) == "interrupted"

    reason = replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(4),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(0, 5),
        posterior_provenance=_with_current_value_serving(consumed),
    )

    assert reason == (
        "basis=current_value_serving_read_unavailable:sqlite_error=interrupted"
    )
    assert "raw_hwm_unavailable" not in reason


def test_raw_hwm_successful_empty_selection_still_reports_raw_unavailable() -> None:
    conn = _conn()
    consumed = {
        "gfs": {
            "raw_model_forecast_id": 1,
            "served_cycle": _dt(0).isoformat(),
            "captured_at": _dt(0, 5).isoformat(),
            "served_via": "single_runs",
        }
    }
    assert read_current_instrument_values(
        conn,
        city="Shanghai",
        metric="high",
        target_date="2026-07-07",
        source_cycle_time_iso=_dt(0).isoformat(),
        include_station_sources=True,
        decision_time_iso=_dt(4).isoformat(),
    ) == {}

    reason = replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-07-07",
        metric="high",
        decision_time=_dt(4),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(0, 5),
        posterior_provenance=_with_current_value_serving(consumed),
    )

    assert reason is not None
    assert reason.startswith("basis=current_value_serving_raw_hwm_unavailable:")


def test_raw_hwm_uses_exact_anchor_artifact_not_model_serving_clock(tmp_path) -> None:
    """The anchor artifact may be newer than the raw model row it accompanies."""
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_available_at=_dt(4, 5),
        computed_at=_dt(4, 10),
    )
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )
        raw_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        consumed[model] = {
            "raw_model_forecast_id": raw_id,
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": "single_runs",
        }
    anchor_artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(4),
    )
    provenance = _with_current_value_serving(
        consumed,
        anchor_artifact_id=anchor_artifact_id,
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), posterior_id),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(5),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"


def test_raw_hwm_fails_closed_when_available_anchor_has_no_consumed_id(
    tmp_path,
) -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_available_at=_dt(4, 5),
        computed_at=_dt(4, 10),
    )
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": "single_runs",
        }
    _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(4),
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(_with_current_value_serving(consumed)), posterior_id),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(5),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is False
    assert "openmeteo_anchor_artifact_provenance_unverifiable" in result.reason_code


def test_raw_hwm_blocks_newer_anchor_than_exact_consumed_artifact(tmp_path) -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_available_at=_dt(3, 5),
        computed_at=_dt(3, 10),
    )
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": "single_runs",
        }
    consumed_artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(3),
    )
    _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(4),
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (
            json.dumps(
                _with_current_value_serving(
                    consumed,
                    anchor_artifact_id=consumed_artifact_id,
                )
            ),
            posterior_id,
        ),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(5),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is False
    assert "source_cycle_time_raw_forecast_artifacts_lag" in result.reason_code
    assert "consumed_anchor_cycle=2026-06-06T03:00:00+00:00" in result.reason_code


def test_raw_hwm_lookup_binds_exact_same_cycle_materialization(tmp_path) -> None:
    conn = _conn()
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": "single_runs",
        }
    older_artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(3),
    )
    older_id = _insert_posterior(
        conn,
        source_available_at=_dt(3, 5),
        computed_at=_dt(3, 10),
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (
            json.dumps(
                _with_current_value_serving(
                    consumed,
                    anchor_artifact_id=older_artifact_id,
                )
            ),
            older_id,
        ),
    )
    newer_artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(4),
    )
    newer_id = _insert_posterior(
        conn,
        source_available_at=_dt(4, 5),
        computed_at=_dt(4, 10),
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (
            json.dumps(
                _with_current_value_serving(
                    consumed,
                    anchor_artifact_id=newer_artifact_id,
                )
            ),
            newer_id,
        ),
    )

    reason = replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(5),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(3, 10),
    )

    assert reason is not None
    assert "source_cycle_time_raw_forecast_artifacts_lag" in reason
    assert "consumed_anchor_cycle=2026-06-06T03:00:00+00:00" in reason


def test_raw_hwm_lookup_rejects_ambiguous_timestamp_spellings() -> None:
    conn = _conn()
    first_id = _insert_posterior(conn, computed_at=_dt(3, 10))
    second_id = _insert_posterior(conn, computed_at=_dt(3, 11))
    conn.execute(
        """
        UPDATE forecast_posteriors
           SET computed_at = ?, provenance_json = ?
         WHERE posterior_id = ?
        """,
        ("2026-06-06T03:10:00Z", '{"row":"z"}', first_id),
    )
    conn.execute(
        """
        UPDATE forecast_posteriors
           SET computed_at = ?, provenance_json = ?
         WHERE posterior_id = ?
        """,
        ("2026-06-06T03:10:00+00:00", '{"row":"offset"}', second_id),
    )

    provenance = _posterior_provenance_for_cycle(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at="2026-06-06T03:10:00Z",
    )

    assert provenance is None


@pytest.mark.parametrize(
    ("fault", "expected_basis"),
    (
        ("scope", "openmeteo_anchor_artifact_scope_mismatch"),
        ("future", "openmeteo_anchor_artifact_causality_mismatch"),
        ("hash", "openmeteo_anchor_artifact_payload_identity_mismatch"),
        ("metadata", "openmeteo_anchor_artifact_metadata_unverifiable"),
        ("coverage", "openmeteo_anchor_artifact_scope_mismatch"),
    ),
)
def test_exact_anchor_artifact_validation_fails_closed(
    tmp_path,
    fault: str,
    expected_basis: str,
) -> None:
    conn = _conn()
    artifact_id = _insert_openmeteo_anchor_artifact(
        conn,
        tmp_path,
        source_cycle_time=_dt(3),
    )
    row = conn.execute(
        """
        SELECT artifact_path, artifact_metadata_json
        FROM raw_forecast_artifacts
        WHERE artifact_id = ?
        """,
        (artifact_id,),
    ).fetchone()
    if fault == "scope":
        metadata = json.loads(row["artifact_metadata_json"])
        metadata["city"] = "Seoul"
        conn.execute(
            "UPDATE raw_forecast_artifacts SET artifact_metadata_json = ? WHERE artifact_id = ?",
            (json.dumps(metadata), artifact_id),
        )
    elif fault == "future":
        conn.execute(
            "UPDATE raw_forecast_artifacts SET source_available_at = ? WHERE artifact_id = ?",
            (_dt(6).isoformat(), artifact_id),
        )
    elif fault == "hash":
        conn.execute(
            "UPDATE raw_forecast_artifacts SET sha256 = ? WHERE artifact_id = ?",
            ("f" * 64, artifact_id),
        )
    elif fault == "metadata":
        conn.execute(
            "UPDATE raw_forecast_artifacts SET artifact_metadata_json = 'not-json' WHERE artifact_id = ?",
            (artifact_id,),
        )
    else:
        payload = {
            "hourly": {
                "time": ["2026-06-08T00:00"],
                "temperature_2m": [22.0],
            }
        }
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        path = row["artifact_path"]
        with open(path, "wb") as handle:
            handle.write(payload_bytes)
        conn.execute(
            "UPDATE raw_forecast_artifacts SET sha256 = ?, byte_size = ? WHERE artifact_id = ?",
            (hashlib.sha256(payload_bytes).hexdigest(), len(payload_bytes), artifact_id),
        )

    reason, cycle = _exact_consumed_anchor_artifact_cycle(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(5),
        provenance={"openmeteo_anchor_artifact_id": artifact_id},
    )

    assert cycle is None
    assert reason is not None
    assert expected_basis in reason


def test_raw_hwm_accepts_exact_authoritative_previous_run_substitution() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    consumed: dict[str, dict[str, object]] = {}
    for model, endpoint in (
        ("ecmwf_ifs", "single_runs"),
        ("gfs", "previous_runs"),
    ):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 5),
            source_available_at=_dt(3, 5),
            endpoint=endpoint,
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": endpoint,
        }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(_with_current_value_serving(consumed)), posterior_id),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"


def test_raw_hwm_blocks_when_exact_consumed_model_is_superseded() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(2),
            captured_at=_dt(2, 5),
            source_available_at=_dt(2, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(2).isoformat(),
            "captured_at": _dt(2, 5).isoformat(),
            "served_via": "single_runs",
        }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (
            json.dumps(_with_current_value_serving(consumed)),
            posterior_id,
        ),
    )
    _insert_raw_model_forecast(
        conn,
        model="gfs",
        source_cycle_time=_dt(3),
        captured_at=_dt(3, 5),
        source_available_at=_dt(3, 5),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is False
    assert "basis=used_raw_model_forecasts_superseded" in result.reason_code
    assert "model=gfs" in result.reason_code


def test_raw_hwm_keeps_carrier_for_isolated_next_cycle_provider() -> None:
    """One provider >3h ahead cannot invalidate the last coherent carrier."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "icon_eu"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(0),
            captured_at=_dt(0, 5),
            source_available_at=_dt(0, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(0).isoformat(),
            "captured_at": _dt(0, 5).isoformat(),
            "served_via": "single_runs",
        }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(_with_current_value_serving(consumed)), posterior_id),
    )
    _insert_raw_model_forecast(
        conn,
        model="icon_eu",
        source_cycle_time=_dt(6),
        captured_at=_dt(6, 5),
        source_available_at=_dt(6, 5),
    )

    reason = replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(7),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(0, 5),
        posterior_provenance=_with_current_value_serving(consumed),
    )

    assert reason is None


def test_raw_hwm_fails_closed_on_unverifiable_current_value_provenance() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    _insert_raw_model_forecast(
        conn,
        model="gfs",
        source_cycle_time=_dt(0),
        captured_at=_dt(0, 5),
        source_available_at=_dt(0, 5),
    )
    provenance = _with_current_value_serving(
        {
            "gfs": {
                "raw_model_forecast_id": int(
                    conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                ),
                "served_via": "single_runs",
            }
        }
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), posterior_id),
    )

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )

    assert result.ok is False
    assert "current_value_serving_provenance_unverifiable" in result.reason_code


def test_raw_hwm_reuses_bound_posterior_provenance(monkeypatch) -> None:
    import src.data.replacement_forecast_bundle_reader as reader

    conn = _conn()
    posterior_id = _insert_posterior(conn)
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(0),
            captured_at=_dt(0, 5),
            source_available_at=_dt(0, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(0).isoformat(),
            "captured_at": _dt(0, 5).isoformat(),
            "served_via": "single_runs",
        }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(_with_current_value_serving(consumed)), posterior_id),
    )
    traced: list[str] = []
    provenance_parses = 0
    original_json_mapping = reader._json_mapping

    def counted_json_mapping(value, *, field_name):
        nonlocal provenance_parses
        if field_name == "provenance_json":
            provenance_parses += 1
        return original_json_mapping(value, field_name=field_name)

    monkeypatch.setattr(reader, "_json_mapping", counted_json_mapping)
    conn.set_trace_callback(traced.append)

    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
    )
    conn.set_trace_callback(None)

    assert result.ok is True
    duplicate_provenance_reads = [
        statement
        for statement in traced
        if "SELECT PROVENANCE_JSON" in statement.upper()
        and "WHERE CITY" in statement.upper()
    ]
    assert duplicate_provenance_reads == []
    assert provenance_parses == 1
    posterior_reads = [
        statement
        for statement in traced
        if statement.lstrip().upper().startswith("SELECT")
        and "FROM FORECAST_POSTERIORS" in statement.upper()
    ]
    assert len(posterior_reads) == 1
