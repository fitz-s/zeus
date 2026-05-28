# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: operator pre-MC re-audit Blocker 2 / BL-B

"""
RELATIONSHIP tests for --months scoping in rebuild_calibration_pairs.

BL-B Blocker: a per-(city, season, metric) regen must NOT delete pairs
belonging to other seasons.  The month-scoped DELETE is the critical
antibody.  Tests here use in-memory SQLite to verify:

1. _delete_canonical_v2_slice(months=(3,4,5)) deletes MAM rows but leaves
   DJF rows for the same city+metric+bin_source.

2. _fetch_eligible_snapshots_v2(months=(3,4,5)) returns only MAM snapshots
   from ensemble_snapshots — no DJF rows bleed through.

3. _scoped_pair_predicate(months=(3,4,5)) embeds the SUBSTR month-IN clause
   in the WHERE string and includes the month integers in params.
"""

import sqlite3
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Production imports — must not fail; failures indicate broken PYTHONPATH
# ---------------------------------------------------------------------------
from src.state.db import init_schema_forecasts
from src.state.schema.v2_schema import apply_v2_schema
from src.calibration.metric_specs import METRIC_SPECS
from scripts.rebuild_calibration_pairs import (
    CANONICAL_BIN_SOURCE_V2,
    _delete_canonical_v2_slice,
    _fetch_eligible_snapshots_v2,
    _scoped_pair_predicate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    apply_v2_schema(conn)
    return conn


def _high_spec():
    return next(s for s in METRIC_SPECS if s.identity.temperature_metric == "high")


def _insert_pair(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str = "high",
) -> None:
    """Insert a minimal calibration_pairs row satisfying all NOT NULL constraints."""
    conn.execute(
        """
        INSERT INTO calibration_pairs
            (city, target_date, temperature_metric, observation_field,
             range_label, p_raw, outcome, lead_days, season, cluster,
             forecast_available_at, dataset_id, decision_group_id,
             bin_source, authority, training_allowed, causality_status)
        VALUES (?, ?, ?, 'high_temp', 'low', 0.5, 0, 5.0, 'MAM', 'all',
                '2025-01-01T12:00:00', 'tigge_mx2t6_local_calendar_day_max_v1',
                'test-dgid', ?, 'VERIFIED', 1, 'OK')
        """,
        (city, target_date, temperature_metric, CANONICAL_BIN_SOURCE_V2),
    )


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str = "high",
    data_version: str = "tigge_mx2t6_local_calendar_day_max_v1",
) -> None:
    """Insert a minimal ensemble_snapshots row that passes the eligibility WHERE."""
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
            (city, target_date, temperature_metric, physical_quantity,
             observation_field, lead_hours, members_json, model_version,
             dataset_id, source_id, training_allowed, causality_status, authority,
             available_at, fetch_time)
        VALUES
            (?, ?, ?, 'mx2t6_local_calendar_day_max', 'high_temp',
             120, '[1.0,2.0]', 'ecmwf_hres_v1',
             ?, 'tigge_mars', 1, 'OK', 'VERIFIED',
             '2025-01-01T12:00:00', '2025-01-01T06:00:00')
        """,
        (city, target_date, temperature_metric, data_version),
    )


# ---------------------------------------------------------------------------
# Test 1 — RELATIONSHIP: month-scoped DELETE leaves other-season pairs intact
# ---------------------------------------------------------------------------

class TestMonthScopedDelete:
    """BL-B core relationship test: DELETE with months=(3,4,5) is MAM-only."""

    def test_mam_deleted_djf_survives(self):
        conn = _make_db()
        spec = _high_spec()

        _insert_pair(conn, city="London", target_date="2025-02-10")  # DJF — must survive
        _insert_pair(conn, city="London", target_date="2025-04-10")  # MAM — must be deleted
        conn.commit()

        # Sanity: both rows exist before the delete
        assert conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs"
        ).fetchone()[0] == 2

        # Execute month-scoped DELETE for MAM only
        _delete_canonical_v2_slice(
            conn,
            spec=spec,
            city_filter="London",
            months=(3, 4, 5),
        )
        conn.commit()

        rows = conn.execute(
            "SELECT target_date FROM calibration_pairs"
        ).fetchall()
        remaining_dates = {r["target_date"] for r in rows}

        assert "2025-02-10" in remaining_dates, "DJF pair must survive month-scoped delete"
        assert "2025-04-10" not in remaining_dates, "MAM pair must be deleted by month-scoped delete"
        assert len(remaining_dates) == 1, "Exactly one row (DJF) should remain"

    def test_no_months_deletes_all_city_pairs(self):
        """months=None (default) retains current full-scope delete behaviour."""
        conn = _make_db()
        spec = _high_spec()

        _insert_pair(conn, city="London", target_date="2025-02-10")  # DJF
        _insert_pair(conn, city="London", target_date="2025-04-10")  # MAM
        conn.commit()

        _delete_canonical_v2_slice(
            conn,
            spec=spec,
            city_filter="London",
            months=None,
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
        assert count == 0, "months=None must delete all city pairs (existing behaviour)"

    def test_months_only_deletes_target_city(self):
        """Month-scoped delete does not touch other cities."""
        conn = _make_db()
        spec = _high_spec()

        _insert_pair(conn, city="London", target_date="2025-04-10")   # MAM, target city
        _insert_pair(conn, city="HongKong", target_date="2025-04-10") # MAM, other city
        conn.commit()

        _delete_canonical_v2_slice(
            conn,
            spec=spec,
            city_filter="London",
            months=(3, 4, 5),
        )
        conn.commit()

        rows = conn.execute(
            "SELECT city FROM calibration_pairs"
        ).fetchall()
        cities = {r["city"] for r in rows}
        assert "HongKong" in cities, "Other city's pairs must not be touched"
        assert "London" not in cities, "Target city MAM pairs should be deleted"


# ---------------------------------------------------------------------------
# Test 2 — UNIT: _fetch_eligible_snapshots_v2 month filter
# ---------------------------------------------------------------------------

class TestFetchSnapshotsMonthFilter:
    """Verify that months= scope is honoured in the SELECT path."""

    def test_mam_snapshots_returned_djf_excluded(self):
        conn = _make_db()
        spec = _high_spec()

        _insert_snapshot(conn, city="London", target_date="2025-02-10")  # DJF
        _insert_snapshot(conn, city="London", target_date="2025-04-10")  # MAM
        conn.commit()

        rows = _fetch_eligible_snapshots_v2(
            conn,
            city_filter="London",
            spec=spec,
            months=(3, 4, 5),
        )
        dates = {r["target_date"] for r in rows}
        assert "2025-04-10" in dates, "MAM snapshot must be returned"
        assert "2025-02-10" not in dates, "DJF snapshot must be excluded by month filter"

    def test_no_months_returns_all(self):
        """months=None returns all snapshots regardless of month (existing behaviour)."""
        conn = _make_db()
        spec = _high_spec()

        _insert_snapshot(conn, city="London", target_date="2025-02-10")  # DJF
        _insert_snapshot(conn, city="London", target_date="2025-04-10")  # MAM
        conn.commit()

        rows = _fetch_eligible_snapshots_v2(
            conn,
            city_filter="London",
            spec=spec,
            months=None,
        )
        dates = {r["target_date"] for r in rows}
        assert "2025-02-10" in dates
        assert "2025-04-10" in dates


# ---------------------------------------------------------------------------
# Test 3 — UNIT: _scoped_pair_predicate embeds month IN-clause in WHERE/params
# ---------------------------------------------------------------------------

class TestScopedPairPredicateMonthClause:
    """Verify WHERE string and params contain the month IN-clause when months set."""

    def test_months_in_where_and_params(self):
        conn = _make_db()
        spec = _high_spec()

        where, params = _scoped_pair_predicate(
            conn=conn,
            spec=spec,
            months=(3, 4, 5),
        )

        assert "SUBSTR(target_date" in where, (
            "WHERE clause must contain SUBSTR(target_date for month extraction"
        )
        assert 3 in params and 4 in params and 5 in params, (
            "Month ints 3, 4, 5 must appear in params list"
        )

    def test_no_months_no_substr_in_where(self):
        """months=None must not add SUBSTR clause — existing behaviour unchanged."""
        conn = _make_db()
        spec = _high_spec()

        where, params = _scoped_pair_predicate(
            conn=conn,
            spec=spec,
            months=None,
        )

        assert "SUBSTR(target_date" not in where, (
            "months=None must not add any SUBSTR month clause to WHERE"
        )
