from __future__ import annotations

import sqlite3

from src.ingest.forecast_live_daemon import (
    _FORECAST_BOOT_REQUIRED_SCHEMA,
    _forecast_boot_schema_ready,
)


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
