# Created: 2026-05-02
# Last reused/audited: 2026-07-20
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b readiness-state contract.

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src.state.db import (
    _READINESS_STATE_INDEX_SQL,
    _create_readiness_state,
    _migrate_readiness_state_status_checks,
    init_schema,
)
from src.state.readiness_repo import get_entry_readiness, get_readiness_state, write_readiness_state


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _conn_with_legacy_readiness_status_check() -> sqlite3.Connection:
    template = _conn()
    create_sql = template.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='readiness_state'"
    ).fetchone()[0]
    legacy_sql = create_sql.replace(
        "'READY','LIVE_ELIGIBLE','BLOCKED','UNKNOWN_BLOCKED'",
        "'READY','LIVE_ELIGIBLE','BLOCKED','DEGRADED_LOG_ONLY','UNKNOWN_BLOCKED'",
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(legacy_sql)
    return conn


def _ready_kwargs() -> dict[str, object]:
    return {
        "readiness_id": "ready-1",
        "scope_type": "city_metric",
        "status": "LIVE_ELIGIBLE",
        "computed_at": datetime(2026, 5, 2, 9, tzinfo=timezone.utc),
        "expires_at": datetime(2026, 5, 2, 10, tzinfo=timezone.utc),
        "city_id": "LONDON",
        "city": "London",
        "city_timezone": "Europe/London",
        "target_local_date": date(2026, 5, 3),
        "temperature_metric": "high",
        "physical_quantity": "temperature_2m",
        "observation_field": "daily_high",
        "data_version": "forecast_v2",
        "strategy_key": "daily_high_v1",
        "market_family": "polymarket_weather_daily_high",
        "condition_id": "0xabc",
        "token_ids_json": ["yes", "no"],
        "source_id": "ecmwf_open_data",
        "track": "mx2t6_high",
        "source_run_id": "src-run-1",
        "reason_codes_json": ["ALL_DEPENDENCIES_READY"],
        "dependency_json": {"source_run_id": "src-run-1"},
        "provenance_json": {"job_run_id": "job-1"},
    }


def test_readiness_state_schema_creates_entry_scope_columns() -> None:
    conn = _conn()

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(readiness_state)")}

    assert {
        "readiness_id",
        "scope_type",
        "city_id",
        "target_local_date",
        "temperature_metric",
        "strategy_key",
        "condition_id",
        "status",
    } <= columns


def test_readiness_state_latest_scope_query_uses_active_covering_order() -> None:
    conn = _conn()

    plan = [
        str(row["detail"])
        for row in conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT dependency_json, status
              FROM readiness_state
             WHERE strategy_key = ?
               AND city = ?
               AND target_local_date = ?
               AND temperature_metric = ?
             ORDER BY computed_at DESC, readiness_id DESC
             LIMIT 1
            """,
            ("replacement", "London", "2026-07-20", "high"),
        ).fetchall()
    ]

    assert any("idx_readiness_state_strategy_family_latest" in detail for detail in plan)
    assert all("TEMP B-TREE" not in detail for detail in plan)


def test_readiness_state_schema_reclaims_indexes_from_renamed_legacy_table() -> None:
    conn = _conn()
    write_readiness_state(conn, **_ready_kwargs())
    conn.execute("ALTER TABLE readiness_state RENAME TO readiness_state_legacy")

    _create_readiness_state(conn)

    owners = {
        str(row["name"]): str(row["tbl_name"])
        for row in conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert {
        index_name: owners[index_name]
        for index_name in _READINESS_STATE_INDEX_SQL
    } == {
        index_name: "readiness_state"
        for index_name in _READINESS_STATE_INDEX_SQL
    }
    assert conn.execute("SELECT COUNT(*) FROM readiness_state_legacy").fetchone()[0] == 1


def test_readiness_repo_round_trips_live_eligible_verdict() -> None:
    conn = _conn()

    write_readiness_state(conn, **_ready_kwargs())

    row = get_readiness_state(conn, "ready-1")
    assert row is not None
    assert row["status"] == "LIVE_ELIGIBLE"
    assert json.loads(row["dependency_json"])["source_run_id"] == "src-run-1"


def test_entry_readiness_missing_fails_closed_unknown_blocked() -> None:
    conn = _conn()

    row = get_entry_readiness(
        conn,
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
        physical_quantity="temperature_2m",
        observation_field="daily_high",
        data_version="forecast_v2",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        strategy_key="daily_high_v1",
        market_family="polymarket_weather_daily_high",
        condition_id="0xabc",
    )

    assert row["status"] == "UNKNOWN_BLOCKED"
    assert json.loads(row["reason_codes_json"]) == ["READINESS_MISSING"]


def test_entry_readiness_expired_fails_closed_unknown_blocked() -> None:
    conn = _conn()
    write_readiness_state(conn, **_ready_kwargs())

    row = get_entry_readiness(
        conn,
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
        physical_quantity="temperature_2m",
        observation_field="daily_high",
        data_version="forecast_v2",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        strategy_key="daily_high_v1",
        market_family="polymarket_weather_daily_high",
        condition_id="0xabc",
        now_utc=datetime(2026, 5, 2, 10, tzinfo=timezone.utc) + timedelta(seconds=1),
    )

    assert row["status"] == "UNKNOWN_BLOCKED"
    assert json.loads(row["reason_codes_json"]) == ["READINESS_EXPIRED"]


def test_readiness_rejects_naive_expiry_before_write() -> None:
    conn = _conn()
    kwargs = _ready_kwargs()
    kwargs["expires_at"] = datetime(2026, 5, 2, 10)

    with pytest.raises(ValueError, match="expires_at must be timezone-aware"):
        write_readiness_state(conn, **kwargs)


def test_live_eligible_readiness_requires_expiry_before_write() -> None:
    conn = _conn()
    kwargs = _ready_kwargs()
    kwargs["expires_at"] = None

    with pytest.raises(ValueError, match="LIVE_ELIGIBLE readiness requires expires_at"):
        write_readiness_state(conn, **kwargs)


def test_stored_live_eligible_without_expiry_fails_closed_on_read() -> None:
    conn = _conn()
    write_readiness_state(conn, **_ready_kwargs())
    conn.execute("UPDATE readiness_state SET expires_at = NULL WHERE readiness_id = ?", ("ready-1",))

    row = get_entry_readiness(
        conn,
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
        physical_quantity="temperature_2m",
        observation_field="daily_high",
        data_version="forecast_v2",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        strategy_key="daily_high_v1",
        market_family="polymarket_weather_daily_high",
        condition_id="0xabc",
        now_utc=datetime(2026, 5, 2, 9, tzinfo=timezone.utc),
    )

    assert row["status"] == "UNKNOWN_BLOCKED"
    assert json.loads(row["reason_codes_json"]) == ["READINESS_EXPIRY_MISSING"]


def test_readiness_malformed_stored_expiry_fails_closed_on_read() -> None:
    conn = _conn()
    write_readiness_state(conn, **_ready_kwargs())
    conn.execute(
        "UPDATE readiness_state SET expires_at = ? WHERE readiness_id = ?",
        ("2026-05-02T10:00:00", "ready-1"),
    )

    row = get_entry_readiness(
        conn,
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
        physical_quantity="temperature_2m",
        observation_field="daily_high",
        data_version="forecast_v2",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        strategy_key="daily_high_v1",
        market_family="polymarket_weather_daily_high",
        condition_id="0xabc",
        now_utc=datetime(2026, 5, 2, 9, tzinfo=timezone.utc),
    )

    assert row["status"] == "UNKNOWN_BLOCKED"
    assert json.loads(row["reason_codes_json"]) == ["READINESS_EXPIRY_INVALID"]


def test_entry_readiness_filters_full_source_metric_identity() -> None:
    conn = _conn()
    blocked_kwargs = _ready_kwargs()
    blocked_kwargs["readiness_id"] = "ready-blocked"
    blocked_kwargs["status"] = "BLOCKED"
    blocked_kwargs["reason_codes_json"] = ["SOURCE_RUN_FAILED"]
    write_readiness_state(conn, **blocked_kwargs)

    green_kwargs = _ready_kwargs()
    green_kwargs["readiness_id"] = "ready-green"
    green_kwargs["source_id"] = "openmeteo_previous_runs"
    green_kwargs["track"] = "best_match"
    green_kwargs["data_version"] = "previous_runs_v1"
    green_kwargs["reason_codes_json"] = ["ALL_DEPENDENCIES_READY"]
    write_readiness_state(conn, **green_kwargs)

    row = get_entry_readiness(
        conn,
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
        physical_quantity="temperature_2m",
        observation_field="daily_high",
        data_version="forecast_v2",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        strategy_key="daily_high_v1",
        market_family="polymarket_weather_daily_high",
        condition_id="0xabc",
        now_utc=datetime(2026, 5, 2, 9, tzinfo=timezone.utc),
    )

    assert row["readiness_id"] == "ready-blocked"
    assert row["status"] == "BLOCKED"


def test_readiness_logical_scope_upsert_overwrites_prior_green_state() -> None:
    conn = _conn()
    write_readiness_state(conn, **_ready_kwargs())
    blocked_kwargs = _ready_kwargs()
    blocked_kwargs["readiness_id"] = "ready-2"
    blocked_kwargs["status"] = "UNKNOWN_BLOCKED"
    blocked_kwargs["reason_codes_json"] = ["SOURCE_RUN_FAILED"]
    write_readiness_state(conn, **blocked_kwargs)

    rows = conn.execute("SELECT * FROM readiness_state WHERE city_id = 'LONDON'").fetchall()
    assert len(rows) == 1
    assert rows[0]["readiness_id"] == "ready-2"
    assert rows[0]["status"] == "UNKNOWN_BLOCKED"


def test_readiness_repo_rejects_unknown_status() -> None:
    conn = _conn()
    kwargs = _ready_kwargs()
    kwargs["status"] = "NOT_A_READINESS_STATUS"

    with pytest.raises(ValueError, match="invalid readiness status"):
        write_readiness_state(conn, **kwargs)


def test_readiness_state_schema_rejects_log_only_status() -> None:
    conn = _conn()
    write_readiness_state(conn, **_ready_kwargs())

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE readiness_state SET status = 'DEGRADED_LOG_ONLY' WHERE readiness_id = ?",
            ("ready-1",),
        )


def test_readiness_state_status_migration_removes_log_only_admission() -> None:
    conn = _conn_with_legacy_readiness_status_check()
    write_readiness_state(conn, **_ready_kwargs())

    _migrate_readiness_state_status_checks(conn)

    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='readiness_state'"
    ).fetchone()[0]
    assert "DEGRADED_LOG_ONLY" not in create_sql
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE readiness_state SET status = 'DEGRADED_LOG_ONLY' WHERE readiness_id = ?",
            ("ready-1",),
        )


def test_readiness_state_status_migration_fails_on_obsolete_rows() -> None:
    conn = _conn_with_legacy_readiness_status_check()
    write_readiness_state(conn, **_ready_kwargs())
    conn.execute(
        "UPDATE readiness_state SET status = 'DEGRADED_LOG_ONLY' WHERE readiness_id = ?",
        ("ready-1",),
    )

    with pytest.raises(RuntimeError, match="obsolete non-live status"):
        _migrate_readiness_state_status_checks(conn)
