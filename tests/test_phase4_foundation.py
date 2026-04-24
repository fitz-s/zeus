"""Phase 4 foundation tests: R-I, R-K, R-N

R-I: add_calibration_pair() without metric_identity raises TypeError.
R-K: ensemble_snapshots_v2.members_unit is NOT NULL (4A.2 column).
R-N: platt_models_v2 has no city/target_date columns; UNIQUE enforced.
"""
from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# R-I: add_calibration_pair_v2 requires metric_identity
# ---------------------------------------------------------------------------

class TestCalibrationPairRequiresMetricIdentity:
    """R-I: add_calibration_pair() called without metric_identity raises TypeError."""

    def test_add_calibration_pair_v2_missing_metric_identity_raises_type_error(self):
        """R-I: Calling add_calibration_pair_v2 without metric_identity must raise TypeError.

        The new v2 signature must declare metric_identity as a required keyword
        argument. Omitting it must raise TypeError at call time — not silently
        default to a legacy fallback.
        """
        from src.calibration.store import add_calibration_pair_v2  # noqa: F401 — must exist

        conn = sqlite3.connect(":memory:")
        with pytest.raises(TypeError):
            add_calibration_pair_v2(
                conn=conn,
                city="NYC",
                target_date="2026-04-16",
                range_label="70-71°F",
                p_raw=0.25,
                outcome=0,
                lead_days=2.0,
                season="spring",
                cluster="NYC_F_2",
                forecast_available_at="2026-04-14T12:00:00",
                decision_group_id="dg-test-001",
                training_allowed=True,
                data_version="tigge_mx2t6_local_calendar_day_max_v1",
                # metric_identity intentionally omitted
            )

    def test_add_calibration_pair_v2_with_metric_identity_does_not_raise_type_error(self):
        """R-I complement: calling with all required args including metric_identity must not raise TypeError.

        The real DB write may fail for other reasons (table not yet created, etc.)
        but TypeError specifically means the signature contract is violated.
        """
        from src.calibration.store import add_calibration_pair_v2
        from src.config import City
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = sqlite3.connect(":memory:")
        # apply schema so the INSERT has a valid target
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        init_schema(conn)
        apply_v2_schema(conn)

        nyc = City(
            name="NYC", lat=40.7772, lon=-73.8726,
            timezone="America/New_York", cluster="NYC",
            settlement_unit="F", wu_station="KLGA",
        )

        # Must not raise TypeError (may raise IntegrityError or similar for data reasons)
        try:
            add_calibration_pair_v2(
                conn=conn,
                city="NYC",
                target_date="2026-04-16",
                range_label="70-71°F",
                p_raw=0.25,
                outcome=0,
                lead_days=2.0,
                season="spring",
                cluster="NYC_F_2",
                forecast_available_at="2026-04-14T12:00:00",
                decision_group_id="dg-test-001",
                training_allowed=True,
                data_version="tigge_mx2t6_local_calendar_day_max_v1",
                metric_identity=HIGH_LOCALDAY_MAX,
                city_obj=nyc,
            )
        except TypeError:
            pytest.fail(
                "add_calibration_pair_v2 raised TypeError even though all required "
                "args including metric_identity were supplied (R-I violated)"
            )


# ---------------------------------------------------------------------------
# R-K: ensemble_snapshots_v2.members_unit NOT NULL
# ---------------------------------------------------------------------------

class TestEnsembleSnapshotsV2MembersUnit:
    """R-K: members_unit column must be NOT NULL in ensemble_snapshots_v2 (4A.2)."""

    def _make_conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def test_members_unit_column_exists_in_ensemble_snapshots_v2(self):
        """R-K pre-gate: members_unit column must exist after apply_v2_schema."""
        conn = self._make_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(ensemble_snapshots_v2)")}
        assert "members_unit" in cols, (
            "ensemble_snapshots_v2 is missing 'members_unit' column (R-K: 4A.2 schema migration not applied)"
        )

    def test_members_unit_insert_without_value_uses_default_degc(self):
        """R-K: DEFAULT 'degC' must apply when members_unit is omitted."""
        conn = self._make_conn()
        conn.execute("""
            INSERT INTO ensemble_snapshots_v2
            (city, target_date, temperature_metric, physical_quantity, observation_field,
             available_at, fetch_time, lead_hours, members_json, model_version, data_version)
            VALUES ('NYC', '2026-04-16', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
                    '2026-04-14T12:00:00', '2026-04-14T12:00:00', 48.0, '[]', 'ecmwf_ens',
                    'tigge_mx2t6_local_calendar_day_max_v1')
        """)
        conn.commit()
        (unit,) = conn.execute(
            "SELECT members_unit FROM ensemble_snapshots_v2 WHERE city='NYC'"
        ).fetchone()
        assert unit == "degC", (
            f"members_unit DEFAULT must be 'degC', got {unit!r} (R-K)"
        )

    def test_members_unit_null_explicit_insert_raises_integrity_error(self):
        """R-K: Explicit NULL for members_unit must raise IntegrityError (NOT NULL enforced)."""
        conn = self._make_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO ensemble_snapshots_v2
                (city, target_date, temperature_metric, physical_quantity, observation_field,
                 available_at, fetch_time, lead_hours, members_json, model_version, data_version,
                 members_unit)
                VALUES ('NYC', '2026-04-16', 'high', 'mx2t6_local_calendar_day_max', 'high_temp',
                        '2026-04-14T12:00:00', '2026-04-14T12:00:00', 48.0, '[]', 'ecmwf_ens',
                        'tigge_mx2t6_local_calendar_day_max_v1', NULL)
            """)


# ---------------------------------------------------------------------------
# R-N: platt_models_v2 schema — no city/target_date columns, UNIQUE enforced
# ---------------------------------------------------------------------------

class TestPlattModelsV2Schema:
    """R-N: platt_models_v2 must not have city or target_date columns (Phase 2 pollution fix).
    UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active) enforced.
    """

    def _make_conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def test_platt_models_v2_has_no_city_column(self):
        """R-N: 'city' column must not exist in platt_models_v2."""
        conn = self._make_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(platt_models_v2)")}
        assert "city" not in cols, (
            "platt_models_v2 has a 'city' column — Phase 2 semantic pollution (R-N violated). "
            "Platt models are keyed by (temperature_metric, cluster, season, data_version, input_space), "
            "not by city."
        )

    def test_platt_models_v2_has_no_target_date_column(self):
        """R-N: 'target_date' column must not exist in platt_models_v2."""
        conn = self._make_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(platt_models_v2)")}
        assert "target_date" not in cols, (
            "platt_models_v2 has a 'target_date' column — Phase 2 semantic pollution (R-N violated). "
            "Platt models are bucket-family keyed, not date-keyed."
        )

    def test_platt_models_v2_unique_key_enforced(self):
        """R-N: UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active) must fire."""
        conn = self._make_conn()
        import json

        row = dict(
            model_key="test_model_001",
            temperature_metric="high",
            cluster="NYC_F_2",
            season="spring",
            data_version="tigge_mx2t6_local_calendar_day_max_v1",
            input_space="raw_probability",
            param_A=1.0,
            param_B=0.0,
            param_C=0.0,
            bootstrap_params_json=json.dumps([]),
            n_samples=100,
            fitted_at="2026-04-16T00:00:00",
        )
        conn.execute("""
            INSERT INTO platt_models_v2
            (model_key, temperature_metric, cluster, season, data_version, input_space,
             param_A, param_B, param_C, bootstrap_params_json, n_samples, fitted_at)
            VALUES (:model_key, :temperature_metric, :cluster, :season, :data_version,
                    :input_space, :param_A, :param_B, :param_C, :bootstrap_params_json,
                    :n_samples, :fitted_at)
        """, row)
        conn.commit()

        # Attempt duplicate on the UNIQUE key — different model_key but same business key
        row2 = dict(row, model_key="test_model_002")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO platt_models_v2
                (model_key, temperature_metric, cluster, season, data_version, input_space,
                 param_A, param_B, param_C, bootstrap_params_json, n_samples, fitted_at)
                VALUES (:model_key, :temperature_metric, :cluster, :season, :data_version,
                        :input_space, :param_A, :param_B, :param_C, :bootstrap_params_json,
                        :n_samples, :fitted_at)
            """, row2)
