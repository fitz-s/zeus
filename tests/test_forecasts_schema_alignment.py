# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Lifecycle: created=2026-04-23; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: Protect forecasts writer/schema alignment across fresh and legacy DBs.
# Reuse: Run before changing forecasts table columns or src/data/forecasts_append.py inserts.
# Authority basis: REOPEN-1 (data-readiness hardening tail / REOPEN proposals,
# recorded in docs/operations/task_2026-04-23_midstream_remediation/receipt.json
# after critic-opus forensic-audit triage 2026-04-23). Closes the
# k2_forecasts_daily live-failure "table forecasts has no column named
# rebuild_run_id" via CREATE TABLE declaration + ALTER TABLE legacy path.

"""Structural alignment antibody for the `forecasts` table.

Four guarantees:

1. **Fresh-DB path**: `init_schema()` on a blank connection yields a
   `forecasts` table that contains writer provenance columns.
2. **Legacy-DB path**: a pre-existing `forecasts` table that predates the
   new columns has them added by `init_schema()` via its ALTER TABLE loop.
3. **Writer/schema alignment**: every column mentioned in the writer's
   `INSERT OR IGNORE INTO forecasts` statement at
   `src/data/forecasts_append.py` must be present after `init_schema()`
   runs. Catches future writer drift without requiring a live DB.
4. **R3 F1 provenance**: source_id/raw_payload_hash/captured_at/authority_tier
   stay present on fresh, legacy, and writer paths.

The failure mode this antibody prevents is the exact one reported by
`state/scheduler_jobs_health.json::k2_forecasts_daily`:
`"last_failure_reason": "table forecasts has no column named rebuild_run_id"`
— which was caused by the writer adding `rebuild_run_id` /
`data_source_version` to its INSERT tuple without a legacy-DB ALTER path.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from src.state.db import init_schema

ZEUS_ROOT = Path(__file__).resolve().parents[1]
FORECASTS_APPEND = ZEUS_ROOT / "src" / "data" / "forecasts_append.py"
F1_PROVENANCE_COLUMNS = {
    "source_id",
    "raw_payload_hash",
    "captured_at",
    "authority_tier",
}


def _forecasts_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(forecasts)")}


def test_fresh_db_forecasts_has_rebuild_run_id_and_data_source_version():
    """CREATE TABLE declaration includes both columns on a blank DB."""
    conn = sqlite3.connect(":memory:")
    try:
        init_schema(conn)
        cols = _forecasts_columns(conn)
        assert "rebuild_run_id" in cols, (
            f"fresh DB forecasts table missing rebuild_run_id (columns: {sorted(cols)})"
        )
        assert "data_source_version" in cols, (
            f"fresh DB forecasts table missing data_source_version (columns: {sorted(cols)})"
        )
        assert F1_PROVENANCE_COLUMNS <= cols
    finally:
        conn.close()


def test_legacy_db_forecasts_is_migrated_by_alter_path():
    """Legacy DB with pre-column-addition forecasts schema is correctly
    migrated by init_schema's ALTER TABLE loop.

    Simulates the production zeus-world.db state: forecasts table exists
    from an earlier schema snapshot that did NOT declare rebuild_run_id /
    data_source_version. CREATE TABLE IF NOT EXISTS no-ops on re-run, so
    the ALTER path is the only thing that can add the columns."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                source TEXT NOT NULL,
                forecast_basis_date TEXT,
                forecast_issue_time TEXT,
                lead_days INTEGER,
                lead_time_hours REAL,
                forecast_high REAL,
                forecast_low REAL,
                temp_unit TEXT DEFAULT 'F',
                retrieved_at TEXT,
                imported_at TEXT,
                UNIQUE(city, target_date, source, forecast_basis_date)
            )
            """
        )
        pre_cols = _forecasts_columns(conn)
        assert "rebuild_run_id" not in pre_cols
        assert "data_source_version" not in pre_cols
        assert pre_cols.isdisjoint(F1_PROVENANCE_COLUMNS)

        init_schema(conn)

        post_cols = _forecasts_columns(conn)
        assert "rebuild_run_id" in post_cols, (
            "ALTER TABLE path did NOT add rebuild_run_id on legacy-schema DB; "
            "k2_forecasts_daily would continue to fail"
        )
        assert "data_source_version" in post_cols, (
            "ALTER TABLE path did NOT add data_source_version on legacy-schema DB"
        )
        assert F1_PROVENANCE_COLUMNS <= post_cols, (
            "ALTER TABLE path did NOT add R3 F1 forecast provenance columns: "
            f"{sorted(F1_PROVENANCE_COLUMNS - post_cols)}"
        )
    finally:
        conn.close()


def test_alter_path_is_idempotent_on_already_migrated_db():
    """Re-running init_schema after legacy migration must not raise
    (duplicate-column OperationalError is swallowed). Guards against future
    refactor that removes the try/except wrap and breaks idempotency."""
    conn = sqlite3.connect(":memory:")
    try:
        init_schema(conn)
        # Second call should be a no-op, not raise
        init_schema(conn)
        cols = _forecasts_columns(conn)
        assert "rebuild_run_id" in cols
        assert "data_source_version" in cols
    finally:
        conn.close()


def _extract_insert_columns(source: str) -> set[str]:
    """Pull the column list out of the forecasts writer's INSERT statement."""
    match = re.search(
        r"INSERT OR IGNORE INTO forecasts \((.*?)\) VALUES",
        source,
        flags=re.DOTALL,
    )
    if not match:
        return set()
    raw = match.group(1)
    return {c.strip() for c in raw.split(",") if c.strip()}


def test_writer_insert_columns_are_declared_by_init_schema():
    """Every column in `INSERT OR IGNORE INTO forecasts (...)` must be a
    real column after init_schema runs.

    This is the structural-alignment antibody that would have caught the
    REOPEN-1 failure at CI time instead of runtime. If a future writer
    adds a new column without a corresponding CREATE TABLE / ALTER TABLE
    update, this test fails."""
    writer_source = FORECASTS_APPEND.read_text()
    writer_cols = _extract_insert_columns(writer_source)
    assert writer_cols, (
        "failed to locate 'INSERT OR IGNORE INTO forecasts (...)' in "
        f"{FORECASTS_APPEND}; test premise broken — grep-verify the INSERT "
        "statement location before editing"
    )

    conn = sqlite3.connect(":memory:")
    try:
        init_schema(conn)
        table_cols = _forecasts_columns(conn)
    finally:
        conn.close()

    missing = writer_cols - table_cols
    assert not missing, (
        f"forecasts writer INSERT references columns not declared by "
        f"init_schema: {sorted(missing)}. Either add them to the "
        f"CREATE TABLE declaration + ALTER TABLE path in src/state/db.py, "
        f"or drop them from the writer's INSERT tuple."
    )


def test_writer_insert_columns_include_rebuild_run_id_sanity_check():
    """Sanity: pins the two columns that triggered REOPEN-1 so future
    reverts of the writer's provenance fields flip this test red."""
    writer_source = FORECASTS_APPEND.read_text()
    writer_cols = _extract_insert_columns(writer_source)
    assert "rebuild_run_id" in writer_cols
    assert "data_source_version" in writer_cols


def test_writer_insert_columns_include_f1_provenance_sanity_check():
    """R3 F1: new forecast writes must carry source registry provenance."""
    writer_source = FORECASTS_APPEND.read_text()
    writer_cols = _extract_insert_columns(writer_source)
    assert F1_PROVENANCE_COLUMNS <= writer_cols


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
