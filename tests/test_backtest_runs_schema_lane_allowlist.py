# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: fix for PR #87 omission — selection_coverage lane missing
#   from backtest_runs + backtest_outcome_comparison CHECK constraints.
#   Antibody: any *_LANE module constant not in the CHECK list causes a
#   failing INSERT, caught here before it reaches production.
"""Antibody test: CHECK constraint lane allowlist covers all declared lanes.

Relationship tested
-------------------
Module-level *_LANE constants in src/engine/replay*.py  →  CHECK constraint
in src/state/db.py::init_backtest_schema.

If a new lane is added to replay_*.py but not to the DB schema, the INSERT
fails at runtime. This test catches that class of omission statically (by
parsing the schema SQL) and dynamically (by attempting an INSERT into a
temp in-memory DB using the exact DDL from init_backtest_schema).

Test design
-----------
1. Import all replay modules that export *_LANE constants; collect them.
2. Inspect the CHECK constraint text from sqlite_master to confirm each
   lane appears in the allowlist.
3. Attempt an INSERT of each lane into an in-memory DB to confirm the
   constraint accepts them.
4. Confirm that an unknown lane ('__unknown_lane__') is rejected.
"""
from __future__ import annotations

import importlib
import inspect
import re
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Lane discovery: import replay modules and collect *_LANE constants
# ---------------------------------------------------------------------------

REPLAY_MODULES = [
    "src.engine.replay",
    "src.engine.replay_selection_coverage",
]


def _collect_lanes() -> list[str]:
    lanes: list[str] = []
    for mod_name in REPLAY_MODULES:
        mod: ModuleType = importlib.import_module(mod_name)
        for name, value in inspect.getmembers(mod):
            if name.endswith("_LANE") and isinstance(value, str):
                lanes.append(value)
    return sorted(set(lanes))


DECLARED_LANES = _collect_lanes()


# ---------------------------------------------------------------------------
# DB schema helpers: build in-memory DB from init_backtest_schema
# ---------------------------------------------------------------------------


def _make_in_memory_db() -> sqlite3.Connection:
    """Create an in-memory DB with the current init_backtest_schema DDL."""
    from src.state.db import init_backtest_schema

    conn = sqlite3.connect(":memory:")
    init_backtest_schema(conn)
    return conn


def _extract_check_values(conn: sqlite3.Connection, table: str, column: str) -> list[str]:
    """Parse the CHECK(<column> IN (...)) text from sqlite_master."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    assert row is not None, f"Table {table!r} not found in schema"
    sql = row[0]
    pattern = rf"{re.escape(column)}\s+IN\s*\(([^)]+)\)"
    m = re.search(pattern, sql)
    assert m, f"No {column} IN(...) CHECK found in {table} DDL"
    raw = m.group(1)
    return [v.strip().strip("'\"") for v in raw.split(",")]


def _extract_check_lanes(conn: sqlite3.Connection, table: str) -> list[str]:
    return _extract_check_values(conn, table, "lane")


# Divergence statuses used exclusively by selection_coverage lane (replay_selection_coverage.py)
SELECTION_COVERAGE_DIVERGENCE_STATUSES = [
    "scored",
    "no_snapshot",
    "no_day0_nowcast_excluded",
    "invalid_p_raw_json",
    "empty_p_raw",
    "label_count_mismatch",
    "no_clob_best_bid",
    "fdr_scan_failed",
    "no_hypotheses",
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_declared_lanes_non_empty():
    """Sanity: at least wu_settlement_sweep + selection_coverage must exist."""
    assert "wu_settlement_sweep" in DECLARED_LANES
    assert "selection_coverage" in DECLARED_LANES


def test_all_declared_lanes_in_backtest_runs_check():
    conn = _make_in_memory_db()
    allowed = _extract_check_lanes(conn, "backtest_runs")
    missing = [lane for lane in DECLARED_LANES if lane not in allowed]
    assert missing == [], (
        f"Lanes declared in replay modules but missing from backtest_runs "
        f"CHECK constraint: {missing}. Add them to src/state/db.py::init_backtest_schema."
    )


def test_all_declared_lanes_in_backtest_outcome_comparison_check():
    conn = _make_in_memory_db()
    allowed = _extract_check_lanes(conn, "backtest_outcome_comparison")
    missing = [lane for lane in DECLARED_LANES if lane not in allowed]
    assert missing == [], (
        f"Lanes declared in replay modules but missing from backtest_outcome_comparison "
        f"CHECK constraint: {missing}. Add them to src/state/db.py::init_backtest_schema."
    )


@pytest.mark.parametrize("lane", DECLARED_LANES)
def test_insert_declared_lane_accepted(lane: str):
    """Dynamic guard: each declared lane must not trigger a CHECK violation."""
    conn = _make_in_memory_db()
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, lane, started_at, status, authority_scope, config_json, summary_json)
        VALUES (?, ?, '2026-05-07T00:00:00Z', 'completed',
                'diagnostic_non_promotion', '{}', '{}')
        """,
        (f"test-run-{lane}", lane),
    )
    row = conn.execute(
        "SELECT lane FROM backtest_runs WHERE run_id=?", (f"test-run-{lane}",)
    ).fetchone()
    assert row is not None and row[0] == lane


def test_unknown_lane_rejected():
    """Unknown lane must fail the CHECK constraint."""
    conn = _make_in_memory_db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO backtest_runs
                (run_id, lane, started_at, status, authority_scope, config_json, summary_json)
            VALUES ('bad-run', '__unknown_lane__', '2026-05-07T00:00:00Z', 'completed',
                    'diagnostic_non_promotion', '{}', '{}')
            """
        )


def test_selection_coverage_divergence_statuses_in_check():
    """All selection_coverage divergence_status values must be in the CHECK allowlist."""
    conn = _make_in_memory_db()
    allowed = _extract_check_values(conn, "backtest_outcome_comparison", "divergence_status")
    missing = [s for s in SELECTION_COVERAGE_DIVERGENCE_STATUSES if s not in allowed]
    assert missing == [], (
        f"selection_coverage divergence_status values missing from CHECK constraint: {missing}. "
        f"Add them to src/state/db.py::init_backtest_schema."
    )


@pytest.mark.parametrize("div_status", SELECTION_COVERAGE_DIVERGENCE_STATUSES)
def test_insert_selection_coverage_divergence_status_accepted(div_status: str):
    """Each selection_coverage divergence_status must not trigger a CHECK violation on INSERT."""
    conn = _make_in_memory_db()
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, lane, started_at, status, authority_scope, config_json, summary_json)
        VALUES ('test-run-div', 'selection_coverage', '2026-05-07T00:00:00Z', 'completed',
                'diagnostic_non_promotion', '{}', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO backtest_outcome_comparison
            (run_id, lane, subject_id, subject_kind, truth_source, divergence_status,
             evidence_json, missing_reason_json, created_at)
        VALUES ('test-run-div', 'selection_coverage', ?, 'selection_coverage_snapshot',
                'settlements_v2.winning_bin', ?, '{}', '[]', '2026-05-07T00:00:00Z')
        """,
        (f"subj|{div_status}", div_status),
    )
    row = conn.execute(
        "SELECT divergence_status FROM backtest_outcome_comparison WHERE subject_id=?",
        (f"subj|{div_status}",),
    ).fetchone()
    assert row is not None and row[0] == div_status


def test_unknown_divergence_status_rejected():
    """Unknown divergence_status must fail the CHECK constraint."""
    conn = _make_in_memory_db()
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, lane, started_at, status, authority_scope, config_json, summary_json)
        VALUES ('bad-div-run', 'selection_coverage', '2026-05-07T00:00:00Z', 'completed',
                'diagnostic_non_promotion', '{}', '{}')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO backtest_outcome_comparison
                (run_id, lane, subject_id, subject_kind, truth_source, divergence_status,
                 evidence_json, missing_reason_json, created_at)
            VALUES ('bad-div-run', 'selection_coverage', 'subj|bad', 'selection_coverage_snapshot',
                    'settlements_v2.winning_bin', '__unknown_divergence__',
                    '{}', '[]', '2026-05-07T00:00:00Z')
            """
        )
