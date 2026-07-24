# Created: 2026-07-20
# Last reused/audited: 2026-07-20
# Authority basis: operator-directed DB hot-path and fault-isolation improvement loop.

from __future__ import annotations

import sqlite3

import pytest

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
