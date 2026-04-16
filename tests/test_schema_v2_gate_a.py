"""Relationship tests for Gate A: same-(city, target_date) carries distinct
high + low rows across all 7 metric-aware v2 tables.

Phase: 2 (World DB v2 Schema + DT#1 Commit Ordering + DT#4 Chain Three-State)
R-numbers covered: R-A (Gate A dual-metric coexistence)

These tests MUST FAIL today (2026-04-16) because:
  - src/state/schema/v2_schema.py does not exist (all tests will ImportError).
  - The 7 v2 metric-aware tables do not exist in any DB.

First commit that should turn these green: executor Phase 2 implementation commit
(creates src/state/schema/v2_schema.py with apply_v2_schema() and all DDL).
"""
from __future__ import annotations

import sqlite3
import unittest

import pytest


# ---------------------------------------------------------------------------
# Helper — minimum-viable row shapes for each v2 table.
# These values satisfy NOT NULL constraints per the DDL sketch at
# zeus_dual_track_refactor_package_v2_2026-04-16/03_SCHEMA/01_world_db_v2.sql.
# Column shapes may evolve when executor refines the DDL — update INSERT
# helpers accordingly and note the dependency here.
# ---------------------------------------------------------------------------

CITY = "NYC"
TARGET_DATE = "2026-04-16"


def _insert_settlements_row(conn: sqlite3.Connection, metric: str) -> None:
    conn.execute(
        """
        INSERT INTO settlements_v2
            (city, target_date, temperature_metric, authority, provenance_json, recorded_at)
        VALUES (?, ?, ?, 'UNVERIFIED', '{}', '2026-04-16T00:00:00Z')
        """,
        (CITY, TARGET_DATE, metric),
    )


def _insert_market_events_row(conn: sqlite3.Connection, metric: str) -> None:
    conn.execute(
        """
        INSERT INTO market_events_v2
            (market_slug, city, target_date, temperature_metric, recorded_at)
        VALUES (?, ?, ?, ?, '2026-04-16T00:00:00Z')
        """,
        (f"slug-{metric}", CITY, TARGET_DATE, metric),
    )


def _insert_ensemble_snapshots_row(conn: sqlite3.Connection, metric: str) -> None:
    obs_field = "high_temp" if metric == "high" else "low_temp"
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2
            (city, target_date, temperature_metric, physical_quantity, observation_field,
             available_at, fetch_time, lead_hours, members_json, model_version,
             data_version, training_allowed, causality_status, boundary_ambiguous,
             ambiguous_member_count, provenance_json, authority, recorded_at)
        VALUES (?, ?, ?, 'mx2t6_v2', ?, '2026-04-16T06:00:00Z', '2026-04-16T06:01:00Z',
                24.0, '[]', 'v2', 'tigge_v2', 1, 'OK', 0, 0, '{}', 'VERIFIED',
                '2026-04-16T00:00:00Z')
        """,
        (CITY, TARGET_DATE, metric, obs_field),
    )


def _insert_calibration_pairs_row(conn: sqlite3.Connection, metric: str) -> None:
    obs_field = "high_temp" if metric == "high" else "low_temp"
    conn.execute(
        """
        INSERT INTO calibration_pairs_v2
            (city, target_date, temperature_metric, observation_field, range_label,
             p_raw, outcome, lead_days, season, cluster, forecast_available_at,
             bias_corrected, authority, bin_source, data_version,
             training_allowed, causality_status, recorded_at)
        VALUES (?, ?, ?, ?, 'bin_A', 0.6, 1, 1.0, 'summer', 'coastal',
                '2026-04-15T00:00:00Z', 0, 'UNVERIFIED', 'legacy',
                'tigge_v2', 1, 'OK', '2026-04-16T00:00:00Z')
        """,
        (CITY, TARGET_DATE, metric, obs_field),
    )


def _insert_platt_models_row(conn: sqlite3.Connection, metric: str) -> None:
    # Fix C (fixup pass): platt_models_v2 no longer carries city/target_date —
    # Platt models are keyed on bucket family, not city/date.
    # Gate A count for this table uses COUNT(*) on temperature_metric instead.
    conn.execute(
        """
        INSERT INTO platt_models_v2
            (model_key, temperature_metric,
             cluster, season, data_version,
             input_space, param_A, param_B, param_C, bootstrap_params_json,
             n_samples, fitted_at, is_active, authority, recorded_at)
        VALUES (?, ?, 'coastal', 'summer', 'tigge_v2', 'raw_probability',
                1.0, -1.0, 0.0, '[]', 100, '2026-04-16T00:00:00Z', 1,
                'UNVERIFIED', '2026-04-16T00:00:00Z')
        """,
        (f"{metric}|coastal|summer|tigge_v2|raw_probability", metric),
    )


def _insert_historical_forecasts_row(conn: sqlite3.Connection, metric: str) -> None:
    conn.execute(
        """
        INSERT INTO historical_forecasts_v2
            (city, target_date, source, temperature_metric, forecast_value,
             temp_unit, lead_days, recorded_at)
        VALUES (?, ?, 'NWS', ?, 72.5, 'F', 1, '2026-04-16T00:00:00Z')
        """,
        (CITY, TARGET_DATE, metric),
    )


def _insert_day0_metric_fact_row(conn: sqlite3.Connection, metric: str) -> None:
    conn.execute(
        """
        INSERT INTO day0_metric_fact
            (fact_id, city, target_date, temperature_metric, source,
             local_timestamp, utc_timestamp, temp_current, running_extreme,
             fact_status, missing_reason_json, recorded_at)
        VALUES (?, ?, ?, ?, 'wu', '2026-04-16T14:00:00', '2026-04-16T18:00:00Z',
                74.1, 76.0, 'complete', '[]', '2026-04-16T18:00:00Z')
        """,
        (f"fact-{metric}-{CITY}-{TARGET_DATE}", CITY, TARGET_DATE, metric),
    )


# Map table name → (high inserter, low inserter with same signature)
TABLE_INSERTERS = {
    "settlements_v2": _insert_settlements_row,
    "market_events_v2": _insert_market_events_row,
    "ensemble_snapshots_v2": _insert_ensemble_snapshots_row,
    "calibration_pairs_v2": _insert_calibration_pairs_row,
    "platt_models_v2": _insert_platt_models_row,
    "historical_forecasts_v2": _insert_historical_forecasts_row,
    "day0_metric_fact": _insert_day0_metric_fact_row,
}

ALL_V2_TABLES = list(TABLE_INSERTERS.keys()) + ["observation_instants_v2"]

DEAD_TABLES = [
    "promotion_registry",
    "model_eval_point",
    "model_eval_run",
    # model_skill intentionally excluded: etl_historical_forecasts.py writes to
    # it actively. Cleanup deferred to a later phase (Fix A, fixup pass).
]


def _make_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_and_get_conn() -> sqlite3.Connection:
    """Import apply_v2_schema, apply to fresh :memory: DB, return connection.

    If the import fails today, this helper raises ImportError — which causes
    all tests that call it to error (the desired RED state before Phase 2 lands).
    """
    from src.state.schema.v2_schema import apply_v2_schema  # noqa: PLC0415
    conn = _make_memory_db()
    apply_v2_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# R-A Test Suite
# ---------------------------------------------------------------------------

class TestApplyV2SchemaSmoke(unittest.TestCase):
    """Smoke tests: schema application creates expected tables."""

    def test_apply_v2_schema_creates_all_tables(self):
        """After apply_v2_schema(conn) on :memory:, all 8 v2 tables exist.

        Queries sqlite_master for all 8 table names.
        Fails today with ImportError because v2_schema.py does not exist.
        """
        conn = _apply_and_get_conn()
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in ALL_V2_TABLES:
            self.assertIn(
                table,
                existing,
                msg=f"Expected v2 table '{table}' not found after apply_v2_schema",
            )

    def test_observation_instants_v2_has_running_min(self):
        """PRAGMA table_info(observation_instants_v2) shows a running_min column.

        running_min is the schema v2's reason to exist versus v1 — confirms the
        DDL refinement from the architect opener (dual-track obs support).
        Fails today with ImportError.
        """
        conn = _apply_and_get_conn()
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(observation_instants_v2)")
        }
        self.assertIn(
            "running_min",
            columns,
            msg="observation_instants_v2 must have running_min column (v2 schema requirement)",
        )

    def test_dead_tables_dropped_after_apply_v2(self):
        """After apply_v2_schema, dead tables do NOT exist in sqlite_master.

        Dead tables per D2: promotion_registry, model_eval_point,
        model_eval_run, model_skill.
        Fails today with ImportError.
        """
        conn = _apply_and_get_conn()
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for dead in DEAD_TABLES:
            self.assertNotIn(
                dead,
                existing,
                msg=(
                    f"Dead table '{dead}' must not exist after apply_v2_schema "
                    "(it should be DROPped as part of the migration)"
                ),
            )


class TestGateADualMetricCoexistence(unittest.TestCase):
    """R-A: same-(city, target_date) carries distinct high + low rows in all 7
    metric-aware v2 tables without IntegrityError."""

    def test_gate_a_same_city_date_high_low_coexist(self):
        """For each metric-aware v2 table, INSERT one high + one low row sharing
        (city, target_date); COUNT(*) returns 2; no IntegrityError.

        platt_models_v2 is keyed on bucket family (not city/date), so its count
        is verified via COUNT(DISTINCT temperature_metric) instead.

        Fails today with ImportError.
        """
        conn = _apply_and_get_conn()
        for table, inserter in TABLE_INSERTERS.items():
            with self.subTest(table=table):
                inserter(conn, "high")
                inserter(conn, "low")
                if table == "platt_models_v2":
                    # Fix C: platt_models_v2 has no city/target_date columns.
                    # Verify 2 distinct temperature_metric values were inserted.
                    (count,) = conn.execute(
                        f"SELECT COUNT(DISTINCT temperature_metric) FROM {table}"
                    ).fetchone()
                    self.assertEqual(
                        count,
                        2,
                        msg=(
                            f"Table '{table}': expected 2 distinct temperature_metric "
                            f"values (high + low), got {count}"
                        ),
                    )
                else:
                    (count,) = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE city=? AND target_date=?",
                        (CITY, TARGET_DATE),
                    ).fetchone()
                    self.assertEqual(
                        count,
                        2,
                        msg=(
                            f"Table '{table}': expected 2 rows (high + low) for "
                            f"({CITY}, {TARGET_DATE}), got {count}"
                        ),
                    )

    def test_v2_unique_key_rejects_duplicate_metric(self):
        """Inserting a second row with identical (city, target_date, temperature_metric, ...)
        raises sqlite3.IntegrityError.

        Verifies that the UNIQUE constraints in the DDL sketch are present and
        enforced.  Tests settlements_v2 (simplest UNIQUE) and historical_forecasts_v2.
        Fails today with ImportError.
        """
        conn = _apply_and_get_conn()

        # settlements_v2 has UNIQUE(city, target_date, temperature_metric)
        _insert_settlements_row(conn, "high")
        with self.assertRaises(
            sqlite3.IntegrityError,
            msg=(
                "settlements_v2 must reject a duplicate (city, target_date, "
                "temperature_metric='high') row via UNIQUE constraint"
            ),
        ):
            _insert_settlements_row(conn, "high")

        # historical_forecasts_v2 has UNIQUE(city, target_date, source,
        # temperature_metric, lead_days)
        _insert_historical_forecasts_row(conn, "low")
        with self.assertRaises(
            sqlite3.IntegrityError,
            msg=(
                "historical_forecasts_v2 must reject a duplicate "
                "(city, target_date, source, temperature_metric, lead_days) row"
            ),
        ):
            _insert_historical_forecasts_row(conn, "low")
