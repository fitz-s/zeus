# Created: 2026-06-06
# Last reused/audited: 2026-09-29
# Lifecycle: created=2026-06-06; last_reviewed=2026-09-29; last_reused=2026-09-29
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
    ReplacementForecastAuthorityPurpose,
    SOURCE_ID,
    read_replacement_forecast_bundle,
)
from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    TRADEABLE_GRADE_QLCB_BASIS,
)
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    GRID_SURFACE_EVIDENCE_REVISION,
    grid_surface_evidence_identity_hash,
)
from src.data.executable_forecast_reader import grid_surface_evidence_reason
from src.data.replacement_forecast_source_run_identity import (
    expected_replacement_dependency_identity_by_role,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import (
    PRODUCT_ID as OPENMETEO_ANCHOR_PRODUCT_ID,
    SOURCE_ID as OPENMETEO_ANCHOR_SOURCE_ID,
)
from src.data.replacement_forecast_readiness import (
    LIVE_RUNTIME_LAYER,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
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


def test_cycle_hwm_seeks_each_family_back_to_latest_causal_product_cycle(
    tmp_path,
) -> None:
    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
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
    conn.execute(
        """
        CREATE INDEX idx_raw_forecast_artifacts_product_cycle
        ON raw_forecast_artifacts(source_id, product_id, source_cycle_time)
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_raw_forecast_artifacts_product_family_cycle
        ON raw_forecast_artifacts(
            source_id,
            product_id,
            (CASE WHEN json_valid(artifact_metadata_json)
                  THEN CAST(json_extract(artifact_metadata_json, '$.city') AS TEXT)
             END),
            (CASE WHEN json_valid(artifact_metadata_json)
                  THEN CAST(json_extract(artifact_metadata_json, '$.target_date') AS TEXT)
             END),
            (CASE WHEN json_valid(artifact_metadata_json)
                  THEN CAST(json_extract(artifact_metadata_json, '$.metric') AS TEXT)
             END),
            source_cycle_time
        )
        """
    )
    newest = ("Shanghai", "2026-08-12", "high")
    older = ("Tokyo", "2026-08-12", "high")
    missing = ("Seoul", "2026-08-12", "high")

    def insert(request, cycle):
        conn.execute(
            "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                OPENMETEO_ANCHOR_SOURCE_ID,
                OPENMETEO_ANCHOR_PRODUCT_ID,
                cycle,
                cycle,
                cycle,
                "",
                json.dumps(
                    {"city": request[0], "target_date": request[1], "metric": request[2]}
                ),
            ),
        )

    insert(newest, "2026-08-11T14:00:00+00:00")  # Future at decision time.
    insert(newest, "2026-08-11T12:00:00+00:00")
    insert(older, "2026-08-11T06:00:00+00:00")
    conn.commit()
    plan = conn.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT source_cycle_time
          FROM raw_forecast_artifacts
         WHERE source_id = ?
           AND product_id = ?
           AND (CASE WHEN json_valid(artifact_metadata_json)
                     THEN CAST(json_extract(artifact_metadata_json, '$.city') AS TEXT)
                END) = ?
           AND (CASE WHEN json_valid(artifact_metadata_json)
                     THEN CAST(json_extract(artifact_metadata_json, '$.target_date') AS TEXT)
                END) = ?
           AND (CASE WHEN json_valid(artifact_metadata_json)
                     THEN CAST(json_extract(artifact_metadata_json, '$.metric') AS TEXT)
                END) = ?
           AND source_cycle_time <= ?
        """,
        (
            OPENMETEO_ANCHOR_SOURCE_ID,
            OPENMETEO_ANCHOR_PRODUCT_ID,
            newest[0],
            newest[1],
            newest[2],
            "2026-08-11T13:00:00+00:00",
        ),
    ).fetchall()
    assert any(
        "idx_raw_forecast_artifacts_product_family_cycle" in str(row[-1])
        for row in plan
    )
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    conn.execute("BEGIN")
    try:
        snapshot = freeze_replacement_artifact_hwm(
            conn,
            requests=(newest, older, missing),
            decision_time=datetime(2026, 8, 11, 13, tzinfo=UTC),
        )
    finally:
        conn.rollback()

    complete_traced: list[str] = []
    conn.set_trace_callback(complete_traced.append)
    conn.execute("BEGIN")
    try:
        complete_snapshot = freeze_replacement_artifact_hwm(
            conn,
            requests=(newest, older),
            decision_time=datetime(2026, 8, 11, 13, tzinfo=UTC),
        )
    finally:
        conn.rollback()
        conn.close()

    assert snapshot.artifact_cycles[newest] == datetime(
        2026, 8, 11, 12, tzinfo=UTC
    )
    assert snapshot.artifact_cycles[older] == datetime(
        2026, 8, 11, 6, tzinfo=UTC
    )
    assert missing not in snapshot.artifact_cycles
    hwm_statements = [
        " ".join(statement.upper().split())
        for statement in traced
        if "RAW_FORECAST_ARTIFACTS" in statement.upper()
    ]
    family_queries = [
        sql for sql in hwm_statements if "FROM RAW_FORECAST_ARTIFACTS AS ARTIFACT" in sql
    ]
    assert len(family_queries) == 3  # one indexed cursor per requested family.
    assert all("SOURCE_CYCLE_TIME <=" in sql for sql in family_queries)
    assert all("DATETIME(SOURCE_CYCLE_TIME) <=" in sql for sql in family_queries)
    assert all("JSON_VALID(ARTIFACT_METADATA_JSON)" in sql for sql in family_queries)
    assert not any("GROUP BY SOURCE_CYCLE_TIME" in sql for sql in hwm_statements)
    assert not any("2026-08-11T14:00:00+00:00" in sql for sql in hwm_statements)

    assert complete_snapshot.artifact_cycles == {
        newest: datetime(2026, 8, 11, 12, tzinfo=UTC),
        older: datetime(2026, 8, 11, 6, tzinfo=UTC),
    }
    complete_family_queries = [
        statement
        for statement in complete_traced
        if "FROM RAW_FORECAST_ARTIFACTS AS ARTIFACT" in statement.upper()
    ]
    assert len(complete_family_queries) == 2


def test_cycle_hwm_missing_family_is_bounded_with_many_unrelated_cycles() -> None:
    class StepBudgetConnection(sqlite3.Connection):
        vm_steps = 0

        def set_progress_handler(self, callback, instructions):
            if callback is not None:
                original = callback

                def count_steps():
                    self.vm_steps += instructions
                    return original()

                callback = count_steps
            return super().set_progress_handler(callback, instructions)

    conn = sqlite3.connect(":memory:", factory=StepBudgetConnection)
    conn.row_factory = sqlite3.Row
    conn.execute(
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
    conn.execute(
        """
        CREATE INDEX idx_raw_forecast_artifacts_product_family_cycle
        ON raw_forecast_artifacts(
            source_id,
            product_id,
            (CASE WHEN json_valid(artifact_metadata_json)
                  THEN CAST(json_extract(artifact_metadata_json, '$.city') AS TEXT)
             END),
            (CASE WHEN json_valid(artifact_metadata_json)
                  THEN CAST(json_extract(artifact_metadata_json, '$.target_date') AS TEXT)
             END),
            (CASE WHEN json_valid(artifact_metadata_json)
                  THEN CAST(json_extract(artifact_metadata_json, '$.metric') AS TEXT)
             END),
            source_cycle_time
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_raw_forecast_artifacts_product_cycle "
        "ON raw_forecast_artifacts(source_id, product_id, source_cycle_time)"
    )
    unrelated = {
        "city": "Unrelated",
        "target_date": "2026-08-12",
        "metric": "high",
    }
    rows = []
    for minute in range(4_000):
        cycle = datetime(2026, 8, 8, tzinfo=UTC) + timedelta(minutes=minute)
        cycle_iso = cycle.isoformat()
        rows.append(
            (
                OPENMETEO_ANCHOR_SOURCE_ID,
                OPENMETEO_ANCHOR_PRODUCT_ID,
                cycle_iso,
                cycle_iso,
                cycle_iso,
                "",
                json.dumps(unrelated),
            )
        )
    conn.executemany(
        "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()

    selects = 0

    def count_selects(statement):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    conn.set_trace_callback(count_selects)
    missing = ("Absent", "2026-08-12", "high")
    conn.execute("BEGIN")
    try:
        snapshot = freeze_replacement_artifact_hwm(
            conn,
            requests=(missing,),
            decision_time=datetime(2026, 8, 11, 13, tzinfo=UTC),
            sql_timeout_seconds=5.0,
        )
    finally:
        conn.rollback()
    conn.close()

    assert missing not in snapshot.artifact_cycles
    assert snapshot.artifact_cycles == {}
    assert conn.vm_steps < 10_000, (conn.vm_steps, selects)
    assert selects < 20


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
            normalized = str(sql).upper()
            if (
                self.progress_active
                and "SELECT" in normalized
                and "SOURCE_CYCLE_TIME" in normalized
            ):
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


def _insert_ensemble_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    source_cycle_time: datetime,
    available_at: datetime,
) -> None:
    from tests.test_replacement_forecast_materializer import _fixture_ens_surface_provenance

    source_run_id = f"ens-run-{snapshot_id}"
    dataset_id = expected_replacement_dependency_identity_by_role("high")[
        "baseline_b0"
    ].data_version
    assert dataset_id is not None
    physical_quantity = "mx2t3_local_calendar_day_max"
    surface = json.loads(
        _fixture_ens_surface_provenance(cycle=source_cycle_time.isoformat())
    )
    surface["grid_surface_evidence"]["mask_source_fetched_at"] = (
        source_cycle_time + timedelta(minutes=30)
    ).isoformat()
    assert grid_surface_evidence_reason({
        "city": "Shanghai",
        "dataset_id": dataset_id,
        "source_cycle_time": source_cycle_time.isoformat(),
        "source_available_at": available_at.isoformat(),
        "provenance_json": surface,
    }) is None
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            track TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            ingest_mode TEXT NOT NULL,
            origin_mode TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            imported_at TEXT,
            target_local_date TEXT,
            city_id TEXT,
            city_timezone TEXT,
            temperature_metric TEXT,
            physical_quantity TEXT,
            observation_field TEXT,
            dataset_id TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            completeness_status TEXT NOT NULL,
            partial_run INTEGER NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO source_run (
            source_run_id, source_id, track, release_calendar_key,
            ingest_mode, origin_mode, source_cycle_time, source_available_at,
            fetch_finished_at, captured_at, imported_at, target_local_date,
            city_id, city_timezone, temperature_metric, physical_quantity,
            observation_field, dataset_id, expected_members, observed_members,
            completeness_status, partial_run, status
        ) VALUES (?, 'ecmwf_open_data', 'mx2t6_high_full_horizon',
                  'ecmwf_open_data:mx2t6_high:full_horizon',
                  'SCHEDULED_LIVE', 'SCHEDULED_LIVE', ?, ?, ?, ?, ?,
                  '2026-06-07', 'Shanghai', 'Asia/Shanghai', 'high',
                  ?, 'high_temp',
                  ?, 51, 51,
                  'COMPLETE', 0, 'SUCCESS')
        """,
        (
            source_run_id,
            source_cycle_time.isoformat(),
            available_at.isoformat(),
            available_at.isoformat(),
            available_at.isoformat(),
            available_at.isoformat(),
            physical_quantity,
            dataset_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric,
            physical_quantity, observation_field, issue_time, available_at,
            fetch_time, lead_hours, members_json, model_version, dataset_id,
            causality_status, boundary_ambiguous, provenance_json, authority,
            members_unit, source_cycle_time, source_available_at,
            contributes_to_target_extrema, source_run_id, source_id,
            forecast_window_attribution_status
        ) VALUES (?, 'Shanghai', '2026-06-07', 'high', ?, ?, ?, ?, ?, 24.0,
                  ?, 'ecmwf_ens', ?, 'OK', 0, ?, 'VERIFIED', 'degC', ?, ?, 1, ?,
                  'ecmwf_open_data', 'FULLY_INSIDE_TARGET_LOCAL_DAY')
        """,
        (
            snapshot_id,
            physical_quantity,
            "high_temp",
            source_cycle_time.isoformat(),
            available_at.isoformat(),
            available_at.isoformat(),
            json.dumps([30.0] * 51),
            dataset_id,
            json.dumps(surface),
            source_cycle_time.isoformat(),
            available_at.isoformat(),
            source_run_id,
        ),
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            city TEXT NOT NULL,
            target_local_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            physical_quantity TEXT NOT NULL,
            observation_field TEXT NOT NULL,
            data_version TEXT NOT NULL,
            expected_members INTEGER NOT NULL,
            observed_members INTEGER NOT NULL,
            expected_steps_json TEXT NOT NULL,
            observed_steps_json TEXT NOT NULL,
            snapshot_ids_json TEXT NOT NULL,
            completeness_status TEXT NOT NULL,
            readiness_status TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO source_run_coverage (
            coverage_id, source_run_id, source_id, release_calendar_key, track,
            city, target_local_date, temperature_metric, physical_quantity,
            observation_field, data_version, expected_members,
            observed_members, expected_steps_json, observed_steps_json,
            snapshot_ids_json, completeness_status, readiness_status,
            computed_at, expires_at, recorded_at
        ) VALUES (
            ?, ?, 'ecmwf_open_data', 'ecmwf_open_data:mx2t6_high:full_horizon',
            'mx2t6_high_full_horizon', 'Shanghai', '2026-06-07', 'high',
            ?, 'high_temp', ?, 51, 51,
            '[0,3,6]', '[0,3,6]', ?, 'COMPLETE', 'LIVE_ELIGIBLE', ?, ?, ?
        )
        """,
        (
            f"coverage-{snapshot_id}",
            source_run_id,
            physical_quantity,
            dataset_id,
            json.dumps([snapshot_id]),
            available_at.isoformat(),
            (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            available_at.isoformat(),
        ),
    )


_READER_PROVIDER_GEOMETRY = None


def _reader_provider_geometry():
    """Derive fixture geometry via actual bytes/writer/selector, not a revision label.

    This supplies geometry for isolated reader tests; it does not fabricate a
    complete Day0 pin or replace the integration producer-to-pin relationship.
    """
    global _READER_PROVIDER_GEOMETRY
    if _READER_PROVIDER_GEOMETRY is None:
        import tempfile
        from pathlib import Path
        from src.data import replacement_forecast_materializer as materializer
        from tests.test_station_forecast_live_ingest_wiring import _hourly_schema_conn, _station_grid_cohort

        with tempfile.TemporaryDirectory(prefix="zeus-reader-geometry-") as directory, pytest.MonkeyPatch.context() as patch:
            root = Path(directory)
            patch.setattr("src.config.state_path", lambda filename: root / "state" / filename)
            path = root / "forecast.db"
            conn = _hourly_schema_conn(path)
            models = _station_grid_cohort(
                patch, conn, path, "Shanghai", target_dates=("2026-06-07",),
                cycle=_dt(0), captured=_dt(3),
            )
            served = read_current_instrument_values(
                conn, city="Shanghai", metric="high", target_date="2026-06-07",
                source_cycle_time_iso=_dt(0).isoformat(), decision_time_iso=_dt(3, 5).isoformat(),
            )
            assert set(served) == set(models)
            shape = materializer._current_evidence_shape_from_values(
                snapshot_id=1, source_cycle_time=_dt(0).isoformat(), source_available_at=_dt(3).isoformat(),
                members_c=tuple(27.0 + i * 0.01 for i in range(51)),
                provider_values_c={model: row.value_c for model, row in served.items()},
                provider_weights=dict.fromkeys(models, 0.5), center_c=32.0,
                carrier_cycle_time=_dt(0), provider_cycles=dict.fromkeys(models, _dt(0).isoformat()),
            )
            bound = materializer._bind_provider_geometry_identity(shape, served)
            _READER_PROVIDER_GEOMETRY = (bound.provider_geometry_evidence, bound.provider_geometry_identity_hash)
            conn.close()
    return json.loads(json.dumps(_READER_PROVIDER_GEOMETRY[0])), _READER_PROVIDER_GEOMETRY[1]


def _live_provenance() -> dict[str, object]:
    from tests.test_replacement_forecast_materializer import _fixture_ens_surface_provenance

    surface = json.loads(_fixture_ens_surface_provenance())
    assert grid_surface_evidence_reason({
        "city": "Shanghai",
        "dataset_id": ECMWF_OPENDATA_HIGH_DATA_VERSION,
        "source_cycle_time": _dt(0).isoformat(),
        "source_available_at": _dt(3).isoformat(),
        "provenance_json": surface,
    }) is None
    geometry, geometry_hash = _reader_provider_geometry()
    return {
        "reader_test": True,
        "replacement_q_mode": "FUSED_NORMAL_FULL",
        "q_lcb_basis": TRADEABLE_GRADE_QLCB_BASIS,
        "bin_topology_hash": "topology-hash",
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                "snapshot_id": 1,
                "source_cycle_time": _dt(0).isoformat(),
                "grid_surface_evidence_revision": GRID_SURFACE_EVIDENCE_REVISION,
                "grid_surface_evidence_identity_hash": grid_surface_evidence_identity_hash(
                    surface["grid_surface_evidence"]
                ),
                "shape_lag_hours": 0.0,
                "stale_shape_reused": False,
                "translation_applied": False,
                "provider_geometry_evidence": geometry,
                "provider_geometry_identity_hash": geometry_hash,
            }
        },
    }


@pytest.fixture
def _generic_reader_current_row(tmp_path,monkeypatch,request):
    """Normal source authority for generic reader-format faults, not a city contract.

    The underlying HKO writer uses retained official ground bytes, ordinary
    controlled whole-OM/native captures and a declared toy 51-member receipt.
    Never use this fixture to replace Shanghai/NOAA/WU source-specific tests.
    """
    from tests.integration.test_w3_solve_seam_g3 import (
        _hko_clock_native_sources,_hko_clock_normal_materializer_fixture,
    )
    from src.data.station_ground_evidence import forecast_db_from_connection
    from src.data.replacement_forecast_readiness import latest_replacement_readiness
    source = _hko_clock_native_sources.__wrapped__(tmp_path,monkeypatch)
    next(source)
    try:
        normal = _hko_clock_normal_materializer_fixture(tmp_path,monkeypatch,"high",
            include_target_station_forecast=getattr(request,"param",True))
        try:
            row = dict(normal.conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
                (normal.result.posterior_id,)).fetchone())
            namespace = forecast_db_from_connection(normal.conn)
            readiness = latest_replacement_readiness(normal.conn,city=normal.city.name,
                target_date=normal.request.target_date.isoformat(),temperature_metric="high",
                decision_time=normal.cut)
            assert readiness is not None
            baseline = next(item for item in readiness.dependency_json["dependencies"] if item["role"] == "baseline_b0")
            assert baseline["source_run_id"] == normal.request.baseline_source_run_id
            assert baseline["source_available_at"] == normal.request.baseline_source_available_at.isoformat()
            snapshot = normal.conn.execute("SELECT source_run_id FROM ensemble_snapshots WHERE snapshot_id=1").fetchone()
            assert snapshot[0] == baseline["source_run_id"]
            class ClockType(type):
                def __instancecheck__(cls,value):
                    return isinstance(value,datetime)
            class ReaderClock(datetime,metaclass=ClockType):
                @classmethod
                def now(cls,tz=None):
                    return normal.cut.astimezone(tz) if tz else normal.cut.replace(tzinfo=None)
            for purpose in ReplacementForecastAuthorityPurpose:
                assert reader._live_grade_provenance(row,authority_purpose=purpose,forecast_db=namespace) is not None
                with monkeypatch.context() as clock:
                    clock.setattr(reader,"datetime",ReaderClock)
                    public = read_replacement_forecast_bundle(normal.conn,baseline_bundle=None,
                        readiness=readiness,city=normal.city.name,target_date=normal.request.target_date,
                        temperature_metric="high",decision_time=normal.cut,
                        current_bin_topology_hash=row["bin_topology_hash"],require_baseline_bundle=False,
                        enforce_raw_input_hwm=True,raw_input_hwm_conn=normal.conn,authority_purpose=purpose)
                assert public.ok,public.reason_code
                assert public.bundle.posterior_id == normal.result.posterior_id
                assert public.bundle.baseline_source_run_id == baseline["source_run_id"]
            yield row,namespace
        finally:
            normal.conn.close()
    finally:
        next(source,None)


@pytest.mark.parametrize("_generic_reader_current_row",[False],indirect=True)
@pytest.mark.parametrize("purpose",tuple(ReplacementForecastAuthorityPurpose))
def test_live_reader_accepts_normal_hourly_v2_without_a_station_final_center(
    purpose,_generic_reader_current_row,
):
    row,namespace = _generic_reader_current_row
    provenance = json.loads(row["provenance_json"])
    assert provenance["q_shape"] == "day0_remaining_shared_carrier_v2"
    assert provenance["day0_remaining_carrier_operator"] == "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2"
    assert provenance["day0_remaining_carrier_final_extremes_c"] == []
    assert provenance["day0_remaining_carrier_station_extreme_providers"] == []
    assert reader._live_grade_provenance(row,authority_purpose=purpose,forecast_db=namespace) is not None


@pytest.mark.parametrize("purpose", tuple(ReplacementForecastAuthorityPurpose))
def test_live_reader_rejects_missing_grid_surface_identity(
    purpose: ReplacementForecastAuthorityPurpose,_generic_reader_current_row,
) -> None:
    original,namespace = _generic_reader_current_row
    row = dict(original)
    provenance = json.loads(row["provenance_json"])
    assert reader._live_grade_provenance(row,authority_purpose=purpose,forecast_db=namespace) is not None
    shape = provenance["bayes_precision_fusion"]["current_evidence_shape"]
    del shape["grid_surface_evidence_identity_hash"]
    row["provenance_json"] = json.dumps(provenance)
    assert reader._live_grade_provenance(row,authority_purpose=purpose,forecast_db=namespace) is None


@pytest.mark.parametrize("raw_provenance", ('{"incomplete":', "[]"))
def test_live_reader_does_not_accept_malformed_or_non_object_provenance(
    raw_provenance: str,
) -> None:
    row = {
        "runtime_layer": LIVE_RUNTIME_LAYER,
        "q_lcb_json": '{"cold":0.1,"warm":0.7}',
        "q_ucb_json": '{"cold":0.3,"warm":0.9}',
        "provenance_json": raw_provenance,
    }
    with pytest.raises((ValueError, json.JSONDecodeError)):
        reader._live_grade_provenance(
            row,
            authority_purpose=ReplacementForecastAuthorityPurpose.ENTRY,
        )


_CURRENT_CARRIER_PAIR_CASES = (
        ({}, True),
        ({"q_shape": "day0_remaining_shared_carrier_v1"}, False),
        ({"q_shape": "day0_remaining_shared_carrier_v2"}, False),
        ({"q_shape": "day0_remaining_shared_carrier_v3"}, False),
        ({"day0_remaining_carrier_content_identity": "old-censored-station",
          "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
          "day0_remaining_carrier_station_extreme_providers": [{"model": "hko_fnd"}]}, False),
        *(
            ({"day0_remaining_carrier_content_identity": "typed-content",
              "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
              "day0_remaining_carrier_final_extremes_c": centers,
              "day0_remaining_carrier_station_extreme_providers": [
                  {"forecast_value_c": value} for value in centers
              ] if isinstance(centers, (list, tuple)) else []}, accepted)
            for centers, accepted in (([32.0], True), ([], False),
                                      ([True], False), (["32"], False),
                                      ([float("inf")], False),
                                      ("invalid", False), ([None], False))
        ),
        ({"day0_remaining_carrier_content_identity": "typed-content",
          "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
          "day0_remaining_carrier_final_extremes_c": [32.0],
          "day0_remaining_carrier_station_extreme_providers": [{"forecast_value_c": 31.0}]}, False),
        ({"day0_remaining_carrier_content_identity": "typed-content",
          "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
          "day0_remaining_carrier_final_extremes_c": [32.0],
          "day0_remaining_carrier_station_extreme_providers": [None]}, False),
        ({"q_shape": "day0_remaining_shared_carrier_v2",
          "day0_remaining_carrier_content_identity": "typed-content",
          "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
          "day0_remaining_carrier_final_extremes_c": [32.0],
          "day0_remaining_carrier_station_extreme_providers": [{"forecast_value_c": 32.0}]}, False),

        ({"q_shape": "fused_day0_fast_residual_likelihood"}, True),
        ({"q_shape": "fused_day0_fast_residual_likelihood",
          "day0_remaining_carrier_content_identity": "content-v2",
          "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
          "day0_remaining_carrier_station_extreme_providers": [],
          "day0_remaining_carrier_final_extremes_c": []}, True),
        ({"q_shape": "fused_day0_fast_residual_likelihood",
          "day0_remaining_carrier_content_identity": "typed-content",
          "day0_remaining_carrier_operator": "typed_remaining_and_final_extreme_gaussian_v3",
          "day0_remaining_carrier_final_extremes_c": [32.0],
          "day0_remaining_carrier_station_extreme_providers": [{"forecast_value_c": 32.0}]}, True),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v1",
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_v1",
            },
            False,
        ),
        ({"day0_remaining_carrier_content_identity": "content-only"}, False),
        ({"day0_remaining_carrier_operator": "operator-only"}, False),
        (
            {
                "day0_remaining_carrier_content_identity": 17,
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": ["content-v2"],
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": {"value": "content-v2"},
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": None,
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "   ",
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v2",
                "day0_remaining_carrier_operator": " extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v2",
                "day0_remaining_carrier_operator": 17,
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v2",
                "day0_remaining_carrier_operator": None,
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-unknown",
                "day0_remaining_carrier_operator": "unknown",
            },
            False,
        ),
        (
            {
                "day0_remaining_carrier_content_identity": "content-v2",
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
            },
            True,
        ),
)


def _source_specific_carrier_case(carrier):
    return not carrier or carrier.get("q_shape") == "fused_day0_fast_residual_likelihood"


@pytest.fixture(scope="module")
def _generic_reader_format_template(tmp_path_factory, request):
    # Group by the actual V2/V3 normal producer, retaining one private DB per
    # immutable source cut. Each case changes only a deep-copied JSON payload.
    root = tmp_path_factory.mktemp("reader-v3" if request.param else "reader-v2")
    with pytest.MonkeyPatch.context() as inputs:
        normal = _generic_reader_current_row.__wrapped__(root, inputs, request)
        next_value = next(normal)
        try:
            assert next_value[1].is_relative_to(root)
            yield next_value
        finally:
            next(normal, None)


@pytest.mark.parametrize(("carrier","accepted","_generic_reader_format_template"),[
    pytest.param(carrier,accepted,
        carrier.get("day0_remaining_carrier_operator") != "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
        id=f"carrier{index}-{accepted}")
    for index,(carrier,accepted) in enumerate(_CURRENT_CARRIER_PAIR_CASES)
    if not _source_specific_carrier_case(carrier)
],indirect=["_generic_reader_format_template"])
@pytest.mark.parametrize("purpose", tuple(ReplacementForecastAuthorityPurpose))
def test_live_reader_accepts_only_complete_current_day0_carrier_pair(
    carrier: dict[str, object], accepted: bool, purpose: ReplacementForecastAuthorityPurpose,
    _generic_reader_format_template,
) -> None:
    original, namespace = _generic_reader_format_template
    row = dict(original)
    provenance = json.loads(row["provenance_json"])
    identity_key = "day0_remaining_carrier_content_identity"
    operator_key = "day0_remaining_carrier_operator"
    fields = dict(carrier)
    # Nonempty placeholder identities/centers are structural constants, not
    # new probability authority. Positive paths use the actual producer's
    # content identity, native final center and complete provider proof.
    if identity_key in fields and isinstance(fields[identity_key],str) and fields[identity_key].strip():
        fields[identity_key] = provenance[identity_key]
    if identity_key in fields and operator_key not in fields:
        provenance.pop(operator_key)
    elif operator_key in fields and identity_key not in fields:
        provenance.pop(identity_key)
    elif identity_key not in fields and operator_key not in fields:
        provenance.pop(identity_key)
        provenance.pop(operator_key)
    final_key = "day0_remaining_carrier_final_extremes_c"
    provider_key = "day0_remaining_carrier_station_extreme_providers"
    actual_final = provenance[final_key]
    offset = actual_final[0]-32.0 if actual_final else 0.0
    def native_constant(value):
        return value+offset if type(value) in (int,float) else value
    if isinstance(fields.get(final_key),(list,tuple)):
        fields[final_key] = [native_constant(value) for value in fields[final_key]]
    if isinstance(fields.get(provider_key),(list,tuple)):
        actual_providers = provenance[provider_key]
        providers = []
        for index,value in enumerate(fields[provider_key]):
            if not isinstance(value,dict):
                providers.append(value)
                continue
            copied = dict(actual_providers[index]) if index < len(actual_providers) else {}
            copied.update(value)
            if "forecast_value_c" in value:
                copied["forecast_value_c"] = native_constant(value["forecast_value_c"])
            providers.append(copied)
        fields[provider_key] = providers
    provenance.update(fields)
    row["provenance_json"] = json.dumps(provenance)
    result = reader._live_grade_provenance(
        row, authority_purpose=purpose, forecast_db=namespace
    )
    assert (result is not None) is accepted, carrier
    # Every fault remains in the copied format input. The actual licensed row,
    # including source/cut/identity/value fields, remains byte-for-byte intact.
    persisted = sqlite3.connect(f"file:{namespace}?mode=ro", uri=True)
    try:
        persisted.row_factory = sqlite3.Row
        persisted.execute("PRAGMA query_only=ON")
        unchanged = persisted.execute(
            "SELECT * FROM forecast_posteriors WHERE posterior_id=?",
            (original["posterior_id"],),
        ).fetchone()
        assert dict(unchanged) == original
    finally:
        persisted.close()


@pytest.mark.parametrize(("carrier","accepted"),[
    pytest.param(carrier,accepted,id=f"carrier{index}-{accepted}")
    for index,(carrier,accepted) in enumerate(_CURRENT_CARRIER_PAIR_CASES)
    if carrier and _source_specific_carrier_case(carrier)
])
@pytest.mark.parametrize("purpose",tuple(ReplacementForecastAuthorityPurpose))
def test_live_reader_source_specific_carrier_cases_keep_their_original_obligation(
    carrier,accepted,purpose,
):
    # These six original fast-residual cases retain their own source path.
    # A normal HKO V2/V3 certificate cannot substitute for this obligation.
    provenance = {**_live_provenance(), **carrier}
    row = {
        "runtime_layer": LIVE_RUNTIME_LAYER,
        "q_lcb_json": "{\"cold\":0.1,\"warm\":0.7}",
        "q_ucb_json": "{\"cold\":0.3,\"warm\":0.9}",
        "provenance_json": json.dumps(provenance),
    }
    result = reader._live_grade_provenance(
        row,
        authority_purpose=purpose,
    )
    assert (result is not None) is accepted, carrier


@pytest.fixture(scope="module")
def _generic_reader_noncarrier_template(tmp_path_factory):
    from tests.test_replacement_forecast_materializer import (
        _hko_dt, _hko_native_surfaces, _hko_source_surface,
        _normal_hko_writer_proof_relationship,
    )
    from src.data.replacement_forecast_readiness import latest_replacement_readiness
    from src.data.station_ground_evidence import forecast_db_from_connection
    from zoneinfo import ZoneInfo

    root = tmp_path_factory.mktemp("reader-noncarrier")
    with pytest.MonkeyPatch.context() as inputs:
        native = _hko_native_surfaces.__wrapped__(root, inputs)
        next(native)
        try:
            source = _hko_source_surface.__wrapped__(root, inputs, None)
            next(source)
            try:
                # Reuse the fixed normal writer, not its damaged certificates
                # or a V3 payload with carrier fields manually removed.
                _normal_hko_writer_proof_relationship(root, inputs, include_raw_ifs=True)
                conn = sqlite3.connect(f"file:{root / 'forecast.db'}?mode=ro", uri=True)
                try:
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA query_only=ON")
                    # readiness_state is a current projection: the completed
                    # relationship no longer has its original 20:00 READY.
                    assert latest_replacement_readiness(conn, city="Hong Kong",
                        target_date="2026-10-01", temperature_metric="low",
                        decision_time=_hko_dt(20)) is None
                    cut = _hko_dt(20, 5)
                    row = dict(conn.execute(
                        """SELECT * FROM forecast_posteriors
                           WHERE city=? AND target_date=? AND temperature_metric=?
                             AND computed_at=?""",
                        ("Hong Kong", "2026-10-01", "low", cut.isoformat()),
                    ).fetchone())
                    assert datetime.fromisoformat(row["computed_at"]) == cut
                    assert datetime.fromisoformat(row["recorded_at"]) == cut
                    namespace = forecast_db_from_connection(conn)
                    assert namespace.is_relative_to(root)
                    # This is Oct1 04:05 HKT: an actual ordinary zero-target-
                    # observation/full-prior route, not a claim of Day1.
                    assert cut.astimezone(ZoneInfo("Asia/Hong_Kong")).date() == date.fromisoformat(row["target_date"])
                    provenance = json.loads(row["provenance_json"])
                    assert "day0_remaining_carrier_content_identity" not in provenance
                    assert "day0_remaining_carrier_operator" not in provenance
                    ground = provenance["bayes_precision_fusion"]["current_evidence_shape"]["provider_geometry_audit"]["anchor_station_ground"]
                    assert ground["manifest_role"] == "source_capture_confirmation"
                    assert datetime.fromisoformat(ground["captured_at"]) == _hko_dt(20, 4)
                    assert datetime.fromisoformat(ground["recorded_at"]) == _hko_dt(20, 4) + timedelta(seconds=30)
                    assert datetime.fromisoformat(ground["recorded_at"]) <= cut
                    readiness = latest_replacement_readiness(conn, city=row["city"],
                        target_date=row["target_date"], temperature_metric=row["temperature_metric"],
                        decision_time=cut)
                    assert readiness is not None, tuple(tuple(item) for item in conn.execute(
                        """SELECT source_id, data_version, strategy_key, scope_type,
                                  status, computed_at, readiness_id
                           FROM readiness_state
                           WHERE city=? AND target_local_date=? AND temperature_metric=?
                           ORDER BY computed_at DESC LIMIT 6""",
                        (row["city"], row["target_date"], row["temperature_metric"]),
                    ).fetchall())
                    posterior_dependency = next(item for item in readiness.dependency_json["dependencies"]
                        if item["role"] == "soft_anchor_posterior")
                    assert posterior_dependency["posterior_id"] == row["posterior_id"]
                    ready_row = conn.execute(
                        "SELECT computed_at FROM readiness_state WHERE readiness_id=?",
                        (readiness.readiness_id,),
                    ).fetchone()
                    assert datetime.fromisoformat(ready_row[0]) == cut
                    baseline = next(item for item in readiness.dependency_json["dependencies"]
                        if item["role"] == "baseline_b0")
                    assert baseline["source_run_id"] == "new12"
                    assert baseline["source_available_at"] == _hko_dt(12, 5).isoformat()
                    class ClockType(type):
                        def __instancecheck__(cls, value):
                            return isinstance(value, datetime)
                    class ReaderClock(datetime, metaclass=ClockType):
                        @classmethod
                        def now(cls, tz=None):
                            return cut.astimezone(tz) if tz else cut.replace(tzinfo=None)
                    inputs.setattr(reader, "datetime", ReaderClock)
                    for purpose in ReplacementForecastAuthorityPurpose:
                        public = read_replacement_forecast_bundle(conn,
                            baseline_bundle=_BaselineBundle(_Evidence(baseline["source_run_id"])),
                            readiness=readiness, city=row["city"], target_date=date.fromisoformat(row["target_date"]),
                            temperature_metric=row["temperature_metric"], decision_time=cut,
                            current_bin_topology_hash=row["bin_topology_hash"],
                            enforce_raw_input_hwm=True, raw_input_hwm_conn=conn, authority_purpose=purpose)
                        assert public.ok, public.reason_code
                        assert public.bundle.posterior_id == row["posterior_id"]
                    yield row, namespace
                finally:
                    conn.close()
            finally:
                next(source, None)
        finally:
            next(native, None)


@pytest.mark.parametrize(("carrier", "accepted"), [
    pytest.param(carrier, accepted, id=f"carrier{index}-{accepted}")
    for index, (carrier, accepted) in enumerate(_CURRENT_CARRIER_PAIR_CASES)
    if not carrier
])
@pytest.mark.parametrize("purpose", tuple(ReplacementForecastAuthorityPurpose))
def test_live_reader_accepts_normal_noncarrier_without_inventing_carrier_fields(
    carrier, accepted, purpose, _generic_reader_noncarrier_template,
):
    row, namespace = _generic_reader_noncarrier_template
    assert carrier == {}
    assert (reader._live_grade_provenance(
        row, authority_purpose=purpose, forecast_db=namespace
    ) is not None) is accepted


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
    if conn.execute(
        "SELECT 1 FROM ensemble_snapshots WHERE snapshot_id = 1"
    ).fetchone() is None:
        _insert_ensemble_snapshot(
            conn,
            snapshot_id=1,
            source_cycle_time=_dt(0),
            available_at=_dt(3),
        )
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
                {
                    "baseline_b0": "b0-run",
                    "openmeteo_ifs9_anchor": "om9-run",
                    "current_ensemble_snapshot": 1,
                    **(dependency_source_run_ids or {}),
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


def test_live_input_hwm_blocks_posterior_when_newer_ensemble_cycle_is_available() -> None:
    conn = _conn()
    _insert_ensemble_snapshot(
        conn,
        snapshot_id=1,
        source_cycle_time=_dt(0),
        available_at=_dt(1),
    )
    _insert_ensemble_snapshot(
        conn,
        snapshot_id=2,
        source_cycle_time=_dt(2),
        available_at=_dt(3),
    )

    reason = replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(4),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(1, 30),
        posterior_provenance=_live_provenance(),
    )

    assert reason == (
        "basis=current_ensemble_snapshot_superseded:"
        "latest_snapshot_id=2:"
        "latest_ensemble_cycle=2026-06-06T02:00:00+00:00:"
        "consumed_ensemble_cycle=2026-06-06T00:00:00+00:00:"
        "lag_h=2.00"
    )

    posterior_id = _insert_posterior(conn)
    held = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
    )

    assert held.ok is False
    assert "basis=current_ensemble_snapshot_superseded" in held.reason_code


@pytest.mark.parametrize("newer_evidence", ("current", "retired", "missing_coverage"))
def test_live_input_hwm_considers_only_newer_current_covered_ensemble(
    newer_evidence: str,
) -> None:
    conn = _conn()
    _insert_ensemble_snapshot(
        conn, snapshot_id=1, source_cycle_time=_dt(0), available_at=_dt(1)
    )
    _insert_ensemble_snapshot(
        conn, snapshot_id=2, source_cycle_time=_dt(2), available_at=_dt(3)
    )
    if newer_evidence == "retired":
        retired = "ecmwf_opendata_mx2t3_local_calendar_day_max"
        conn.execute(
            "UPDATE ensemble_snapshots SET dataset_id = ? WHERE snapshot_id = 2",
            (retired,),
        )
        conn.execute(
            "UPDATE source_run SET dataset_id = ? WHERE source_run_id = 'ens-run-2'",
            (retired,),
        )
    elif newer_evidence == "missing_coverage":
        conn.execute(
            "DELETE FROM source_run_coverage WHERE source_run_id = 'ens-run-2'"
        )

    reason = replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(4),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(3, 5),
        posterior_provenance=_live_provenance(),
    )
    assert (
        reason is not None and "basis=current_ensemble_snapshot_superseded" in reason
    ) is (newer_evidence == "current")

    posterior_id = _insert_posterior(conn)
    held = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
    )
    assert held.ok is (newer_evidence != "current")
    if newer_evidence == "current":
        assert "basis=current_ensemble_snapshot_superseded" in held.reason_code


@pytest.mark.parametrize(
    ("bound_evidence", "expected_reason"),
    (
        ("retired", "REPLACEMENT_CURRENT_COORDINATE_IDENTITY_MISMATCH"),
        ("missing_coverage", "REPLACEMENT_CURRENT_ENSEMBLE_SNAPSHOT_COVERAGE_BLOCKED"),
    ),
)
def test_live_reader_does_not_serve_uncurrent_or_uncovered_bound_ensemble(
    bound_evidence: str,
    expected_reason: str,
) -> None:
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    if bound_evidence == "retired":
        conn.execute(
            "UPDATE ensemble_snapshots SET dataset_id = ? WHERE snapshot_id = 1",
            ("ecmwf_opendata_mx2t3_local_calendar_day_max",),
        )
    else:
        conn.execute(
            "DELETE FROM source_run_coverage WHERE source_run_id = 'ens-run-1'"
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
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
    )
    assert result.ok is False
    assert result.reason_code == expected_reason


def _readiness(*, posterior_id: int, baseline_run_id: str = "b0-run", posterior_available_at: datetime | None = None):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version=expected_replacement_dependency_identity_by_role("high")[
                "baseline_b0"
            ].data_version,
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
    forecast_value_c: float = 28.0,
) -> None:
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time,
            source_available_at, captured_at, lead_days, forecast_value_c, endpoint,
            coverage_status, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            forecast_value_c,
            endpoint,
            "COVERED",
            captured_at.isoformat(),
        ),
    )
    # Retain the scenario's IDs/clocks, but derive its product proof from the
    # actual response binder and ordinary artifact/row writer. No authority
    # predicate or reader result is replaced here.
    from tests.test_replacement_forecast_materializer import (
        _materializer_unit_source_surface, _qualify_raw_fixture_rows,
    )
    with pytest.MonkeyPatch.context() as inputs:
        _materializer_unit_source_surface.__wrapped__(inputs)
        _qualify_raw_fixture_rows(conn)


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
                    conn.execute("SELECT MAX(raw_model_forecast_id) FROM raw_model_forecasts").fetchone()[0]
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
    _insert_ensemble_snapshot(
        conn,
        snapshot_id=1,
        source_cycle_time=_dt(0),
        available_at=_dt(1),
    )
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

    held = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(5),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
    )

    assert held.ok is True
    assert held.reason_code == "REPLACEMENT_POSTERIOR_READY"


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
    _insert_ensemble_snapshot(
        conn,
        snapshot_id=1,
        source_cycle_time=_dt(0),
        available_at=_dt(1),
    )
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
        forecast_value_c=29.0,
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

    held = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
    )

    assert held.ok is True
    assert held.reason_code == "REPLACEMENT_POSTERIOR_READY"


def test_held_redecision_blocks_same_cycle_late_input() -> None:
    conn = _conn()
    _insert_ensemble_snapshot(
        conn,
        snapshot_id=1,
        source_cycle_time=_dt(0),
        available_at=_dt(1),
    )
    posterior_id = _insert_posterior(conn, computed_at=_dt(3, 10))
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(
            conn,
            model=model,
            source_cycle_time=_dt(3),
            captured_at=_dt(3, 15) if model == "gfs" else _dt(3, 5),
            source_available_at=_dt(3, 15) if model == "gfs" else _dt(3, 5),
        )
        consumed[model] = {
            "raw_model_forecast_id": int(
                conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ),
            "served_cycle": _dt(3).isoformat(),
            "captured_at": _dt(3, 5).isoformat(),
            "served_via": "single_runs",
        }
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(_with_current_value_serving(consumed)), posterior_id),
    )
    # The old posterior claims the same-cycle value was possessed at 03:05;
    # the actual writer proves that exact raw row only arrived after 03:10.
    # Do not manufacture an unpublishable duplicate of its immutable run key.

    held = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=_readiness(posterior_id=posterior_id),
        city="Shanghai",
        target_date="2026-06-07",
        temperature_metric="high",
        decision_time=_dt(4),
        current_bin_topology_hash="topology-hash",
        enforce_raw_input_hwm=True,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
    )

    assert held.ok is False
    assert "basis=used_raw_model_forecasts_same_cycle_late_input" in held.reason_code


def test_raw_hwm_marks_isolated_used_provider_revision_unconsumed() -> None:
    """One used provider's exact new row is stale even before peers arrive."""
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
        forecast_value_c=29.0,
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

    assert reason is not None
    assert "basis=used_raw_model_forecasts_superseded" in reason
    assert "model=icon_eu" in reason


def _hourly_relabel_reason(
    *,
    newer_value_c: float,
    newer_lead_days: int | None = None,
    consumed_row_present: bool = True,
) -> str | None:
    """One consumed NBM row, then the next hourly cycle of the same provider."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    consumed: dict[str, dict[str, object]] = {}
    for model in ("ecmwf_ifs", "ncep_nbm_conus"):
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
    if not consumed_row_present:
        conn.execute(
            "DELETE FROM raw_model_forecasts WHERE raw_model_forecast_id = ?",
            (consumed["ncep_nbm_conus"]["raw_model_forecast_id"],),
        )
    _insert_raw_model_forecast(
        conn,
        model="ncep_nbm_conus",
        source_cycle_time=_dt(1),
        captured_at=_dt(1, 5),
        source_available_at=_dt(1, 5),
        forecast_value_c=newer_value_c,
    )
    if newer_lead_days is not None:
        conn.execute(
            "UPDATE raw_model_forecasts SET lead_days = ?"
            " WHERE raw_model_forecast_id = last_insert_rowid()",
            (newer_lead_days,),
        )
    return replacement_live_input_lag_reason(
        conn,
        city="Shanghai",
        target_date="2026-06-07",
        metric="high",
        decision_time=_dt(2),
        posterior_source_cycle_time=_dt(0),
        posterior_computed_at=_dt(0, 10),
        posterior_provenance=_with_current_value_serving(consumed),
    )


def test_raw_hwm_hourly_cycle_relabel_of_consumed_evidence_stays_current() -> None:
    """A newer hourly cycle carrying the consumed value+lead is not new evidence.

    Live 2026-09-30: 141/377 NBM and 91/454 icon_global supersession blocks
    were exact relabels; each turned an hourly arrival into a family outage.
    """
    assert _hourly_relabel_reason(newer_value_c=28.0) is None


def test_raw_hwm_hourly_cycle_with_changed_value_or_lead_supersedes() -> None:
    changed_value = _hourly_relabel_reason(newer_value_c=28.3)
    assert changed_value is not None
    assert "basis=used_raw_model_forecasts_superseded" in changed_value
    assert "model=ncep_nbm_conus" in changed_value

    changed_lead = _hourly_relabel_reason(newer_value_c=28.0, newer_lead_days=0)
    assert changed_lead is not None
    assert "basis=used_raw_model_forecasts_superseded" in changed_lead


def test_raw_hwm_unreadable_consumed_evidence_stays_superseded() -> None:
    """Unknown consumed evidence can never prove a relabel (fail closed)."""
    reason = _hourly_relabel_reason(newer_value_c=28.0, consumed_row_present=False)
    assert reason is not None
    assert "basis=used_raw_model_forecasts_superseded" in reason


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


def _coverage_identity_conn(
    monkeypatch,
    *,
    coverage_status: str | None,
    coverage_readiness: str | None,
    run_status: str,
    run_completeness: str,
    partial_run: int,
) -> sqlite3.Connection:
    """Build the smallest source-run/coverage surface for the carrier gate."""

    import src.config as config

    monkeypatch.setattr(
        config,
        "runtime_coordinate_manifest_json",
        lambda: '{"coordinate_basis":"bundle-coverage-test"}',
    )
    metric = "low"
    city = "Shanghai"
    target_date = "2026-09-29"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            source_run_id TEXT NOT NULL
        );
        CREATE TABLE source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            status TEXT NOT NULL,
            completeness_status TEXT NOT NULL,
            partial_run INTEGER NOT NULL
        );
        CREATE TABLE source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            city TEXT NOT NULL,
            target_local_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            expected_members INTEGER NOT NULL,
            observed_members INTEGER NOT NULL,
            expected_steps_json TEXT NOT NULL,
            observed_steps_json TEXT NOT NULL,
            snapshot_ids_json TEXT NOT NULL,
            completeness_status TEXT NOT NULL,
            readiness_status TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            recorded_at TEXT NOT NULL
        );
        """
    )
    expected_dataset = reader.expected_replacement_dependency_identity_by_role("low")[
        "baseline_b0"
    ].data_version
    assert expected_dataset
    source_run_id = "low-run-1"
    conn.execute(
        """
        INSERT INTO source_run (
            source_run_id, source_id, release_calendar_key, track,
            source_available_at, status, completeness_status, partial_run
        ) VALUES (?, 'ecmwf_open_data', 'ecmwf_open_data.enfo', 'operational', ?, ?, ?, ?)
        """,
        (
            source_run_id,
            (now - timedelta(minutes=2)).isoformat(),
            run_status,
            run_completeness,
            partial_run,
        ),
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, dataset_id, source_id, city, target_date,
            temperature_metric, source_run_id
        ) VALUES (1, ?, 'ecmwf_open_data', ?, ?, ?, ?)
        """,
        (expected_dataset, city, target_date, metric, source_run_id),
    )
    if coverage_status is not None:
        conn.execute(
            """
            INSERT INTO source_run_coverage (
                coverage_id, source_run_id, source_id, release_calendar_key, track,
                city, target_local_date, temperature_metric, expected_members,
                observed_members, expected_steps_json, observed_steps_json,
                snapshot_ids_json, completeness_status, readiness_status,
                computed_at, expires_at, recorded_at
            ) VALUES (
                'coverage-1', ?, 'ecmwf_open_data', 'ecmwf_open_data.enfo',
                'operational', ?, ?, ?, 51, 51, '[0,3,6]', '[0,3,6]', '[1]',
                ?, ?, ?, ?, ?
            )
            """,
            (
                source_run_id,
                city,
                target_date,
                metric,
                coverage_status,
                coverage_readiness,
                (now - timedelta(minutes=1)).isoformat(),
                (now + timedelta(hours=1)).isoformat(),
                now.isoformat(),
            ),
        )
    conn.commit()
    return conn


def test_current_ensemble_snapshot_rejects_intrinsic_valid_but_blocked_target_coverage(
    monkeypatch,
):
    conn = _coverage_identity_conn(
        monkeypatch,
        coverage_status="HORIZON_OUT_OF_RANGE",
        coverage_readiness="BLOCKED",
        run_status="PARTIAL",
        run_completeness="PARTIAL",
        partial_run=1,
    )
    reason = reader._current_ensemble_snapshot_identity_reason(
        conn,
        dependency_json={"current_ensemble_snapshot": 1},
        city="Shanghai",
        target_date="2026-09-29",
        metric="low",
    )
    assert reason == "REPLACEMENT_CURRENT_ENSEMBLE_SNAPSHOT_COVERAGE_BLOCKED"


@pytest.mark.parametrize("damage", [None,"body_sha","state_value","clock_role","station","old_cut","naive_clock"])
def test_hko_minute_mean_clock_gate_replays_exact_current_only_body(damage):
    """Only the clock boundary; complete normal authority is covered in W3."""
    import copy
    from scripts.hko_ingest_tick import append_hko_current_temperature_print
    from src.config import cities_by_name
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.state.schema.observation_prints_schema import ensure_table
    conn = _conn()
    ensure_table(conn)
    observed = datetime(2026,9,29,22,30,tzinfo=timezone.utc)
    available = observed+timedelta(minutes=12,seconds=51)
    decision = available+timedelta(seconds=2)
    body = (b"Date time,Automatic Weather Station,Air Temperature(degree Celsius)\n"
            b"202609300630,HK Observatory,28.8\n")
    assert append_hko_current_temperature_print(conn,body=body,
        last_modified="Tue, 29 Sep 2026 22:38:57 GMT",fetched_at=available,
        written_at=available+timedelta(seconds=1))
    state = read_day0_current_temperature_state(conn=conn,city=cities_by_name["Hong Kong"],
        target_date="2026-09-30",decision_time=decision)
    provenance = {"day0_current_temperature_state":state.identity(),
        "day0_current_temperature_clock_evidence":copy.deepcopy(state.clock_evidence),
        "day0_preliminary_report_survival_likelihood":{},"day0_remaining_vector_witness":{},
        "day0_remaining_carrier_content_identity":"clock-test-only",
        "day0_remaining_carrier_operator":"clock-test-only","day0_remaining_carrier_q":[.5,.5],
        "day0_remaining_carrier_sample_count":1,"day0_remaining_carrier_future_extremes_c":[29],
        "day0_remaining_carrier_path_error_sigma_c":.3,
        "day0_remaining_carrier_probability_cutoff_utc":decision.isoformat(),
        "day0_remaining_carrier_probability_samples":[[.5,.5]]}
    evidence = provenance["day0_current_temperature_clock_evidence"]
    if damage == "body_sha":
        raw = json.loads(evidence["raw_report"])
        raw["body_sha256"] = "0"*64
        evidence["raw_report"] = json.dumps(raw)
        evidence["raw_report_sha256"] = hashlib.sha256(evidence["raw_report"].encode()).hexdigest()
    elif damage == "state_value":provenance["day0_current_temperature_state"]["value_native"] = 29
    elif damage == "clock_role":evidence["publication_clock_role"] = "FORMAL_BULLETIN_ISSUED"
    elif damage == "station":evidence["station_id"] = "HK_AIRPORT"
    elif damage == "old_cut":provenance["day0_remaining_carrier_probability_cutoff_utc"] = observed.isoformat()
    elif damage == "naive_clock":evidence["representation_updated_at_utc"] = "2026-09-29T22:38:57"
    reason = reader._hko_current_temperature_clock_reason(provenance,city="Hong Kong")
    assert (reason is None) is (damage is None)
    assert reader._hko_current_temperature_clock_reason(provenance,city="Paris") is None


@pytest.mark.parametrize("damage", [None, "missing", "old_clock", "raw_hash", "wrong_value", "future_record"])
def test_hko_clock_read_gate_reproduces_raw_record_not_revision_label(damage):
    """This is the clock gate only, not a fabricated complete pin authority."""
    import copy
    from src.config import cities_by_name
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.data.day0_hourly_vectors import (
        build_day0_remaining_probability_carrier,
        read_day0_current_temperature_state,
    )
    from src.state.schema.observation_prints_schema import append_print, ensure_table

    conn = _conn()
    ensure_table(conn)
    raw = json.dumps({
        "recordTime": "2026-09-29T14:00:00+08:00",
        "data": [{"place": "Hong Kong Observatory", "value": 33, "unit": "C"}],
    })
    append_print(
        conn, city="Hong Kong", station_id="HKO", source_channel="hko_rhrread_spot",
        publish_ts_utc="2026-09-29T06:02:00+00:00", value_native=33, unit="C",
        fetched_at_utc="2026-09-29T06:05:00+00:00", raw_report=raw,
    )
    state = read_day0_current_temperature_state(
        conn=conn, city=cities_by_name["Hong Kong"], target_date="2026-09-29",
        decision_time=datetime(2026, 9, 29, 6, 21, tzinfo=timezone.utc),
    )
    assert state is not None
    carrier = build_day0_remaining_probability_carrier(
        future_extremes_c=(33.2, 34.1), boundary_scenarios=((32.9, 1.0),),
        metric="high", path_error_sigma_c=0.8, instrument_sigma_c=0.3,
        bin_bounds_c=((None, 31.0), (32.0, 32.0), (33.0, None)),
        n_point=1000, n_samples=500, identity_inputs={"unit": "C"},
        settlement_semantics=SettlementSemantics.for_city(cities_by_name["Hong Kong"]),
    )
    provenance = {
        "day0_current_temperature_state": state.identity(),
        "day0_current_temperature_clock_evidence": copy.deepcopy(state.clock_evidence),
        "day0_preliminary_report_survival_likelihood": {},
        "day0_remaining_vector_witness": {},
        "day0_remaining_carrier_content_identity": carrier["content_identity"],
        "day0_remaining_carrier_operator": carrier["operator"],
        "day0_remaining_carrier_q": carrier["q"],
        "day0_remaining_carrier_sample_count": 500,
        "day0_remaining_carrier_future_extremes_c": [33.2, 34.1],
        "day0_remaining_carrier_path_error_sigma_c": 0.8,
        "day0_remaining_carrier_probability_cutoff_utc": "2026-09-29T06:21:00+00:00",
        "day0_remaining_carrier_probability_samples": carrier["samples"],
    }
    if damage == "missing":
        provenance.pop("day0_current_temperature_clock_evidence")
    elif damage == "old_clock":
        provenance["day0_current_temperature_state"]["observed_at_utc"] = "2026-09-29T06:02:00+00:00"
    elif damage == "raw_hash":
        provenance["day0_current_temperature_clock_evidence"]["raw_report_sha256"] = "unproved-version-label"
    elif damage == "wrong_value":
        provenance["day0_current_temperature_state"]["value_native"] = 32.9
    elif damage == "future_record":
        evidence = provenance["day0_current_temperature_clock_evidence"]
        data = json.loads(evidence["raw_report"])
        data["recordTime"] = "2026-09-29T06:03:00+00:00"
        evidence["raw_report"] = json.dumps(data)
        evidence["raw_report_sha256"] = hashlib.sha256(evidence["raw_report"].encode()).hexdigest()
    reason = reader._hko_current_temperature_clock_reason(provenance, city="Hong Kong")
    assert (reason is None) is (damage is None)
    assert reader._hko_current_temperature_clock_reason(provenance, city="Paris") is None
    if damage is not None:
        # The actual read boundary must reset old/malformed source-clock rows,
        # not silently stamp their old q with the current Day0 revision. This
        # stage assertion does not claim that this minimal carrier is eligible.
        posterior_id = _insert_posterior(conn)
        provenance.update(_live_provenance())
        provenance["bayes_precision_fusion"]["decorrelated_providers_complete"] = True
        conn.execute(
            "UPDATE forecast_posteriors SET city=?, target_date=?, provenance_json=? "
            "WHERE posterior_id=?",
            ("Hong Kong", "2026-09-29", json.dumps(provenance), posterior_id),
        )
        selected = reader.read_prior_complete_replacement_forecast_bundle(
            conn, city="Hong Kong", target_date="2026-09-29", temperature_metric="high",
            decision_time=datetime(2026, 9, 29, 6, 21, tzinfo=timezone.utc),
            raw_input_hwm_conn=conn,
        )
        assert selected.status == "NOT_APPLICABLE"
        assert selected.reason_code == reason
        exact = reader.read_pinned_replacement_forecast_bundle(
            conn, posterior_id=posterior_id, city="Hong Kong", target_date="2026-09-29",
            temperature_metric="high", decision_time=datetime(2026, 9, 29, 6, 21, tzinfo=timezone.utc),
            raw_input_hwm_conn=conn,
        )
        assert exact.status == "BLOCKED" and exact.reason_code == reason
    conn.close()


def test_current_ensemble_snapshot_accepts_complete_current_target_coverage(monkeypatch):
    conn = _coverage_identity_conn(
        monkeypatch,
        coverage_status="COMPLETE",
        coverage_readiness="LIVE_ELIGIBLE",
        run_status="SUCCESS",
        run_completeness="COMPLETE",
        partial_run=0,
    )
    reason = reader._current_ensemble_snapshot_identity_reason(
        conn,
        dependency_json={"current_ensemble_snapshot": 1},
        city="Shanghai",
        target_date="2026-09-29",
        metric="low",
    )
    assert reason is None


def test_current_ensemble_snapshot_rejects_missing_target_coverage(monkeypatch):
    conn = _coverage_identity_conn(
        monkeypatch,
        coverage_status=None,
        coverage_readiness=None,
        run_status="PARTIAL",
        run_completeness="PARTIAL",
        partial_run=1,
    )
    reason = reader._current_ensemble_snapshot_identity_reason(
        conn,
        dependency_json={"current_ensemble_snapshot": 1},
        city="Shanghai",
        target_date="2026-09-29",
        metric="low",
    )
    assert reason == "REPLACEMENT_CURRENT_ENSEMBLE_SNAPSHOT_COVERAGE_BLOCKED"
