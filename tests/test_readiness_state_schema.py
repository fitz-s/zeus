# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b readiness-state contract.

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src.state.db import init_schema
from src.state.readiness_repo import get_entry_readiness, get_readiness_state, write_readiness_state


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
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
    green_kwargs["reason_codes_json"] = ["SHADOW_READY"]
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
    kwargs["status"] = "READY"

    with pytest.raises(ValueError, match="invalid readiness status"):
        write_readiness_state(conn, **kwargs)
