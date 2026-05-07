# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — antibody for Invariant A's
#   priority-list contract: opendata data_version is preferred over TIGGE
#   archive when both rows exist for the same (city, target_date, metric).
"""Antibody for the data_version priority helper used by readers.

``data_version_priority_for_metric(metric)`` returns the canonical priority
tuple. The first element is the freshest (Open Data) source; the second is
the 48h-lagged TIGGE archive backfill. Readers SQL-bind the tuple and
``ORDER BY CASE data_version WHEN ? THEN 0 ELSE 1 END`` to prefer
Open-Data rows when present and fall back to TIGGE for older issue dates.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
)
from src.data.ecmwf_open_data import data_version_priority_for_metric


def test_priority_order_high():
    priority = data_version_priority_for_metric("high")
    assert priority[0] == ECMWF_OPENDATA_HIGH_DATA_VERSION
    assert priority[1] == "tigge_mx2t6_local_calendar_day_max_v1"
    assert len(priority) == 2


def test_priority_order_low():
    priority = data_version_priority_for_metric("low")
    assert priority[0] == ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION
    assert priority[1] == ECMWF_OPENDATA_LOW_DATA_VERSION
    assert priority[2] == TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION
    assert priority[3] == "tigge_mn2t6_local_calendar_day_min_v1"
    assert len(priority) == 4


def test_unknown_metric_raises():
    with pytest.raises(ValueError):
        data_version_priority_for_metric("medium")


def _build_dual_row_db():
    """Build an in-memory v2 table with one opendata row and one TIGGE row
    for the same (city, target_date, metric). Confirm the priority binding
    selects opendata first."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            data_version TEXT,
            available_at TEXT,
            members_json TEXT
        )
    """)
    common = ("London", "2026-05-02", "high")
    conn.execute(
        "INSERT INTO ensemble_snapshots_v2 (city, target_date, temperature_metric,"
        " data_version, available_at, members_json) VALUES (?, ?, ?, ?, ?, ?)",
        common + ("tigge_mx2t6_local_calendar_day_max_v1",
                  "2026-04-30T00:00:00+00:00", "[]"),
    )
    conn.execute(
        "INSERT INTO ensemble_snapshots_v2 (city, target_date, temperature_metric,"
        " data_version, available_at, members_json) VALUES (?, ?, ?, ?, ?, ?)",
        common + (ECMWF_OPENDATA_HIGH_DATA_VERSION,
                  "2026-05-01T07:30:00+00:00", "[]"),
    )
    return conn


def test_priority_select_picks_opendata_when_both_present():
    conn = _build_dual_row_db()
    priority = data_version_priority_for_metric("high")
    rows = conn.execute(
        f"""
        SELECT data_version
          FROM ensemble_snapshots_v2
         WHERE temperature_metric = ?
           AND data_version IN ({",".join("?" for _ in priority)})
         ORDER BY CASE data_version WHEN ? THEN 0 ELSE 1 END,
                  available_at DESC
         LIMIT 1
        """,
        ("high", *priority, priority[0]),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["data_version"] == ECMWF_OPENDATA_HIGH_DATA_VERSION


def test_priority_select_falls_back_to_tigge_when_opendata_absent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            data_version TEXT,
            available_at TEXT,
            members_json TEXT
        )
    """)
    conn.execute(
        "INSERT INTO ensemble_snapshots_v2 (city, target_date, temperature_metric,"
        " data_version, available_at, members_json) VALUES (?, ?, ?, ?, ?, ?)",
        ("London", "2026-04-15", "high",
         "tigge_mx2t6_local_calendar_day_max_v1",
         "2026-04-13T00:00:00+00:00", "[]"),
    )
    priority = data_version_priority_for_metric("high")
    rows = conn.execute(
        f"""
        SELECT data_version
          FROM ensemble_snapshots_v2
         WHERE temperature_metric = ?
           AND data_version IN ({",".join("?" for _ in priority)})
         ORDER BY CASE data_version WHEN ? THEN 0 ELSE 1 END,
                  available_at DESC
         LIMIT 1
        """,
        ("high", *priority, priority[0]),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["data_version"] == "tigge_mx2t6_local_calendar_day_max_v1"
