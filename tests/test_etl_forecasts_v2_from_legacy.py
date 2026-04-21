"""Antibodies for etl_forecasts_v2_from_legacy.

Created: 2026-04-21
Last reused/audited: 2026-04-21
Authority basis: Gate F Step 2 — docs/operations/task_2026-04-21_gate_f_data_backfill/

Structural invariants:
- Every legacy `forecasts` row fans out to 1 or 2 `historical_forecasts_v2`
  rows (one per non-null metric; zero if both high and low are null).
- The ETL stamps `temperature_metric`, `authority`, `data_version`, and
  `provenance_json` on every v2 row (these are the Gate F verify/source
  columns). Missing any stamp is a regression.
- Approved NWP sources → VERIFIED authority; unknown sources → UNVERIFIED.
- The ETL is idempotent: re-running produces the same v2 row set.
- AST guard: the INSERT SQL hits `historical_forecasts_v2` and references
  `temperature_metric` — so a future refactor that forgets metric gets caught.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _apply_v2_and_legacy_schema(conn: sqlite3.Connection) -> None:
    """Install the minimal schema the ETL needs to operate (legacy + v2).

    Uses the repo's v2_schema.apply_v2_schema for parity, and adds the
    legacy `forecasts` CREATE so tests don't depend on init_schema order.
    """
    from src.state.schema.v2_schema import apply_v2_schema

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS forecasts (
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
    """)
    apply_v2_schema(conn)


def _seed(conn: sqlite3.Connection, rows: list[dict]) -> None:
    for r in rows:
        conn.execute(
            "INSERT INTO forecasts (city, target_date, source, forecast_basis_date, "
            "forecast_issue_time, lead_days, lead_time_hours, forecast_high, forecast_low, "
            "temp_unit, retrieved_at, imported_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["city"], r["target_date"], r["source"], r.get("forecast_basis_date"),
                r.get("forecast_issue_time"), r.get("lead_days"), r.get("lead_time_hours"),
                r.get("forecast_high"), r.get("forecast_low"), r.get("temp_unit", "F"),
                r.get("retrieved_at"), r.get("imported_at"),
            ),
        )
    conn.commit()


def test_high_and_low_split_into_two_v2_rows(tmp_path):
    """Each legacy row with both high + low populated produces exactly 2 v2 rows."""
    from scripts.etl_forecasts_v2_from_legacy import run_etl

    conn = sqlite3.connect(":memory:")
    _apply_v2_and_legacy_schema(conn)
    _seed(conn, [{
        "city": "NYC", "target_date": "2024-06-01", "source": "ecmwf_previous_runs",
        "forecast_basis_date": "2024-05-30", "lead_days": 2,
        "forecast_high": 82.5, "forecast_low": 65.2, "temp_unit": "F",
        "retrieved_at": "2024-05-30T12:00:00Z",
    }])

    summary = run_etl(conn, apply=True)
    assert summary["written_high"] == 1
    assert summary["written_low"] == 1

    conn.row_factory = None  # reset (run_etl sets sqlite3.Row)
    rows = conn.execute(
        "SELECT temperature_metric, forecast_value, authority, data_version "
        "FROM historical_forecasts_v2 ORDER BY temperature_metric"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0] == ("high", 82.5, "VERIFIED", "v1.legacy-forecasts-backfill")
    assert rows[1] == ("low", 65.2, "VERIFIED", "v1.legacy-forecasts-backfill")


def test_unknown_source_gets_unverified_authority(tmp_path):
    """Sources outside the VERIFIED_SOURCES allowlist land as UNVERIFIED."""
    from scripts.etl_forecasts_v2_from_legacy import run_etl

    conn = sqlite3.connect(":memory:")
    _apply_v2_and_legacy_schema(conn)
    _seed(conn, [{
        "city": "Paris", "target_date": "2024-06-01",
        "source": "some_random_feed",  # not in VERIFIED set
        "forecast_high": 22.0, "forecast_low": 12.0, "temp_unit": "C",
        "lead_days": 1,
    }])

    run_etl(conn, apply=True)
    auths = [r[0] for r in conn.execute(
        "SELECT authority FROM historical_forecasts_v2"
    ).fetchall()]
    assert set(auths) == {"UNVERIFIED"}


def test_null_metric_skipped_counterpart_still_written(tmp_path):
    """High-null row: skip high side, still write low side (asymmetric coverage OK)."""
    from scripts.etl_forecasts_v2_from_legacy import run_etl

    conn = sqlite3.connect(":memory:")
    _apply_v2_and_legacy_schema(conn)
    _seed(conn, [{
        "city": "Seoul", "target_date": "2024-06-01", "source": "gfs_previous_runs",
        "forecast_high": None, "forecast_low": 15.0, "temp_unit": "C",
        "lead_days": 3,
    }])

    summary = run_etl(conn, apply=True)
    assert summary["written_high"] == 0
    assert summary["written_low"] == 1
    assert summary["skipped_null_high_only"] == 1

    conn.row_factory = None
    rows = conn.execute(
        "SELECT temperature_metric FROM historical_forecasts_v2"
    ).fetchall()
    assert rows == [("low",)]


def test_both_null_row_skipped_entirely(tmp_path):
    """A legacy row with both values null produces zero v2 rows."""
    from scripts.etl_forecasts_v2_from_legacy import run_etl

    conn = sqlite3.connect(":memory:")
    _apply_v2_and_legacy_schema(conn)
    _seed(conn, [{
        "city": "Tokyo", "target_date": "2024-06-01", "source": "ecmwf_previous_runs",
        "forecast_high": None, "forecast_low": None, "temp_unit": "C",
        "lead_days": 5,
    }])

    summary = run_etl(conn, apply=True)
    assert summary["written_high"] == 0
    assert summary["written_low"] == 0
    assert summary["skipped_null_both"] == 1


def test_provenance_json_captures_legacy_lineage(tmp_path):
    """provenance_json must name the legacy row id and basis date."""
    from scripts.etl_forecasts_v2_from_legacy import run_etl

    conn = sqlite3.connect(":memory:")
    _apply_v2_and_legacy_schema(conn)
    _seed(conn, [{
        "city": "London", "target_date": "2024-06-01", "source": "ecmwf_previous_runs",
        "forecast_basis_date": "2024-05-28", "lead_days": 4,
        "forecast_high": 19.5, "forecast_low": 11.0, "temp_unit": "C",
        "forecast_issue_time": "2024-05-28T00:00:00Z",
        "retrieved_at": "2024-05-28T01:00:00Z",
    }])
    run_etl(conn, apply=True)

    row = conn.execute(
        "SELECT provenance_json FROM historical_forecasts_v2 WHERE temperature_metric='high'"
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["forecast_basis_date"] == "2024-05-28"
    assert payload["retrieved_at"] == "2024-05-28T01:00:00Z"
    assert "legacy_forecasts_id" in payload


def test_idempotent_rerun(tmp_path):
    """Running the ETL twice produces the same v2 row count."""
    from scripts.etl_forecasts_v2_from_legacy import run_etl

    conn = sqlite3.connect(":memory:")
    _apply_v2_and_legacy_schema(conn)
    _seed(conn, [
        {"city": "Madrid", "target_date": "2024-06-01", "source": "ecmwf_previous_runs",
         "forecast_high": 28.0, "forecast_low": 18.0, "temp_unit": "C", "lead_days": 1},
        {"city": "Madrid", "target_date": "2024-06-02", "source": "gfs_previous_runs",
         "forecast_high": 29.5, "forecast_low": 19.5, "temp_unit": "C", "lead_days": 2},
    ])

    run_etl(conn, apply=True)
    first = conn.execute("SELECT COUNT(*) FROM historical_forecasts_v2").fetchone()[0]

    run_etl(conn, apply=True)
    second = conn.execute("SELECT COUNT(*) FROM historical_forecasts_v2").fetchone()[0]

    assert first == second == 4  # 2 legacy rows × 2 metrics = 4 v2 rows


def test_dry_run_does_not_commit(tmp_path):
    """--apply=False must rollback; v2 table unchanged after the call."""
    from scripts.etl_forecasts_v2_from_legacy import run_etl

    conn = sqlite3.connect(":memory:")
    _apply_v2_and_legacy_schema(conn)
    _seed(conn, [{
        "city": "Denver", "target_date": "2024-06-01", "source": "ecmwf_previous_runs",
        "forecast_high": 75.0, "forecast_low": 45.0, "temp_unit": "F", "lead_days": 1,
    }])

    summary = run_etl(conn, apply=False)
    assert summary["written_high"] == 1  # in-memory counted
    assert summary["written_low"] == 1

    # But the actual table should be empty (rollback)
    count = conn.execute("SELECT COUNT(*) FROM historical_forecasts_v2").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# AST guard — structural antibody that catches refactor drift
# ---------------------------------------------------------------------------


def test_ast_etl_writes_temperature_metric_column():
    """AST-level: the ETL's INSERT SQL must reference historical_forecasts_v2
    AND the temperature_metric column.

    A future refactor that writes to a different table, or forgets to
    include temperature_metric, gets caught at test collection.
    """
    script_path = REPO_ROOT / "scripts" / "etl_forecasts_v2_from_legacy.py"
    source = script_path.read_text()
    tree = ast.parse(source)

    insert_strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            # Require the string to start with INSERT (actual SQL), not just
            # mention it in a docstring.
            if text.upper().startswith("INSERT") and "historical_forecasts_v2" in text:
                insert_strings.append(text)

    assert insert_strings, (
        "No INSERT statement found targeting historical_forecasts_v2 in the ETL. "
        "A refactor may have silently dropped the v2 write."
    )
    for stmt in insert_strings:
        assert "temperature_metric" in stmt, (
            f"INSERT into historical_forecasts_v2 missing temperature_metric column: {stmt!r}. "
            "Metric-parametrization is a Gate F invariant."
        )
        assert "authority" in stmt, (
            f"INSERT missing authority column: {stmt!r}. "
            "Gate F requires verify/source stamp on every v2 row."
        )
        assert "provenance_json" in stmt, (
            f"INSERT missing provenance_json column: {stmt!r}."
        )
