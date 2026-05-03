# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 3 executable v2 linkage contract.
"""Executable ensemble_snapshots_v2 schema contract tests."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.state.schema.v2_schema import apply_v2_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_v2_schema(conn)
    return conn


def test_ensemble_snapshots_v2_has_executable_source_columns() -> None:
    conn = _conn()

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ensemble_snapshots_v2)")}

    assert {
        "source_id",
        "source_transport",
        "source_run_id",
        "release_calendar_key",
        "source_cycle_time",
        "source_release_time",
        "source_available_at",
    } <= columns


def test_ensemble_snapshots_v2_has_executable_lookup_indexes() -> None:
    conn = _conn()

    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(ensemble_snapshots_v2)")}

    assert "idx_ens_v2_source_run" in indexes
    assert "idx_ens_v2_entry_lookup" in indexes


def test_existing_rows_without_source_linkage_are_not_executable() -> None:
    conn = _conn()
    now = datetime(2026, 5, 3, tzinfo=timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, valid_time, available_at,
            fetch_time, lead_hours, members_json, model_version, data_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "London",
            "2026-05-08",
            "high",
            "temperature_2m",
            "high_temp",
            now,
            now,
            now,
            now,
            120.0,
            "[1,2,3]",
            "test_model",
            "legacy_v2",
        ),
    )

    executable_row = conn.execute(
        """
        SELECT snapshot_id FROM ensemble_snapshots_v2
        WHERE source_id IS NOT NULL
          AND source_transport IS NOT NULL
          AND source_run_id IS NOT NULL
          AND release_calendar_key IS NOT NULL
        """
    ).fetchone()

    assert executable_row is None
