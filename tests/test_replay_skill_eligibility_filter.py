# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: critic adversarial review 2026-04-28 MAJOR #2 (typed gate not wired into live consumers); F11.6 slice
"""Antibody for F11.6: replay's _forecast_rows_for actually filters by SKILL eligibility.

Locks the wiring of SKILL_ELIGIBLE_SQL into src/engine/replay.py:_forecast_rows_for
(line 312 SELECT). RECONSTRUCTED rows must NOT flow into SKILL backtest output;
DERIVED_FROM_DISSEMINATION + RECORDED + FETCH_TIME rows pass through.

Pre-F11 legacy DBs (no availability_provenance column) MAY also pass through
via the IS NULL clause — this is a deliberate tolerance for un-migrated DBs.
"""

import sqlite3

import pytest


@pytest.fixture
def db_with_mixed_provenance():
    """Forecasts with one row per AvailabilityProvenance tier."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Mirror the v2 path failure (table exists but query fails) by NOT
    # creating historical_forecasts_v2; replay's _forecast_rows_for falls
    # through to legacy `forecasts` query.

    conn.execute("""
        CREATE TABLE forecasts (
            id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, source TEXT,
            forecast_basis_date TEXT, forecast_issue_time TEXT,
            lead_days INTEGER, lead_time_hours REAL,
            forecast_high REAL, forecast_low REAL, temp_unit TEXT,
            retrieved_at TEXT, imported_at TEXT,
            rebuild_run_id TEXT, data_source_version TEXT,
            availability_provenance TEXT
        )
    """)

    rows = [
        # (id, city, target_date, source, basis, issue, lead, hours, high, low, unit, ret, imp, run, ver, prov)
        (1, "NYC", "2026-04-30", "ecmwf_previous_runs", "2026-04-28", "2026-04-28T06:48:00+00:00", 2, 48.0, 72.0, 58.0, "F", "t", "t", None, None, "derived_dissemination"),
        (2, "NYC", "2026-04-30", "icon_previous_runs", "2026-04-28", "2026-04-28T12:00:00+00:00", 2, 48.0, 71.0, 57.0, "F", "t", "t", None, None, "reconstructed"),
        (3, "NYC", "2026-04-30", "openmeteo_previous_runs", "2026-04-28", "2026-04-28T12:00:00+00:00", 2, 48.0, 73.0, 59.0, "F", "t", "t", None, None, "reconstructed"),
        (4, "NYC", "2026-04-30", "gfs_previous_runs", "2026-04-28", "2026-04-28T04:14:00+00:00", 2, 48.0, 72.5, 58.5, "F", "t", "t", None, None, "fetch_time"),
        (5, "NYC", "2026-04-30", "ukmo_previous_runs", "2026-04-28", "2026-04-28T12:00:00+00:00", 2, 48.0, 70.0, 56.0, "F", "t", "t", None, None, "recorded"),
        # Pre-F11 legacy row (NULL provenance — tolerance case)
        (6, "NYC", "2026-04-30", "openmeteo_previous_runs", "2026-04-27", "2026-04-27T00:00:00+00:00", 3, 72.0, 70.5, 56.5, "F", "t", "t", None, None, None),
    ]
    conn.executemany(
        "INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()

    # Build a minimal context that mimics the replay engine's `_sp` field
    class _Ctx:
        def __init__(self, conn):
            self.conn = conn
            self._sp = ""
            self.allow_snapshot_only_reference = False
            self._snapshot_cache = {}
            self._decision_ref_cache = {}

    yield _Ctx(conn)
    conn.close()


def test_replay_excludes_reconstructed_rows(db_with_mixed_provenance):
    """SKILL backtest forecast read must NOT return RECONSTRUCTED rows."""
    from src.engine.replay import ReplayContext

    # The fixture's _Ctx is structurally close enough; use the actual
    # _forecast_rows_for via class method call.
    rows = ReplayContext._forecast_rows_for(
        db_with_mixed_provenance,
        "NYC",
        "2026-04-30",
        temperature_metric="high",
    )
    sources = [r["source"] for r in rows]
    # ECMWF (DERIVED), GFS (FETCH_TIME), UKMO (RECORDED) → eligible
    assert "ecmwf_previous_runs" in sources
    assert "gfs_previous_runs" in sources
    assert "ukmo_previous_runs" in sources
    # ICON + OpenMeteo (both RECONSTRUCTED) → excluded
    assert "icon_previous_runs" not in sources
    # Note: openmeteo appears once for the legacy NULL-provenance row, not
    # for the RECONSTRUCTED row. We verify by checking basis_date.
    openmeteo_rows = [r for r in rows if r["source"] == "openmeteo_previous_runs"]
    assert len(openmeteo_rows) == 1
    assert openmeteo_rows[0]["forecast_basis_date"] == "2026-04-27"  # the NULL-provenance legacy row


def test_replay_includes_legacy_null_provenance_rows(db_with_mixed_provenance):
    """Pre-F11 legacy rows (availability_provenance IS NULL) are tolerated.

    This is a deliberate compatibility allowance — un-migrated DBs continue
    to produce diagnostic_non_promotion replay output. After backfill,
    every row has populated provenance and the IS NULL clause becomes inert.
    """
    from src.engine.replay import ReplayContext

    rows = ReplayContext._forecast_rows_for(
        db_with_mixed_provenance,
        "NYC",
        "2026-04-30",
        temperature_metric="high",
    )
    legacy_null_rows = [r for r in rows if r["forecast_basis_date"] == "2026-04-27"]
    assert len(legacy_null_rows) == 1


def test_replay_eligibility_count_matches_design(db_with_mixed_provenance):
    """6 fixture rows → 4 eligible (3 DERIVED/RECORDED/FETCH_TIME + 1 legacy NULL)."""
    from src.engine.replay import ReplayContext

    rows = ReplayContext._forecast_rows_for(
        db_with_mixed_provenance,
        "NYC",
        "2026-04-30",
        temperature_metric="high",
    )
    assert len(rows) == 4
