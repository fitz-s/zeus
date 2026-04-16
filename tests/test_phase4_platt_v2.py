"""Phase 4 Platt v2 tests: R-D (from phase3 learnings) and R-4D family isolation.

R-D (family isolation): A Platt model fitted on high-track calibration pairs must not
share its model_key with a low-track model. Verified via platt_models_v2 UNIQUE key.
Also tests save_platt_model_v2 requires metric_identity.
"""
from __future__ import annotations

import json
import sqlite3

import pytest


class TestPlattModelV2FamilyIsolation:
    """R-4D: High and low Platt models must be isolated — different temperature_metric
    values produce separate rows and may not share a model_key or UNIQUE business key.
    """

    def _make_conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def _base_model_row(self, temperature_metric: str, model_key: str) -> dict:
        dv = (
            "tigge_mx2t6_local_calendar_day_max_v1"
            if temperature_metric == "high"
            else "tigge_mn2t6_local_calendar_day_min_v1"
        )
        return dict(
            model_key=model_key,
            temperature_metric=temperature_metric,
            cluster="NYC_F_2",
            season="spring",
            data_version=dv,
            input_space="raw_probability",
            param_A=1.0,
            param_B=0.0,
            param_C=0.0,
            bootstrap_params_json=json.dumps([]),
            n_samples=100,
            fitted_at="2026-04-16T00:00:00",
        )

    def test_save_platt_model_v2_missing_metric_identity_raises_type_error(self):
        """R-4D pre-gate: save_platt_model_v2 must require metric_identity — no default."""
        from src.calibration.store import save_platt_model_v2  # noqa: F401 — must exist

        conn = self._make_conn()
        with pytest.raises(TypeError):
            save_platt_model_v2(
                conn=conn,
                cluster="NYC_F_2",
                season="spring",
                data_version="tigge_mx2t6_local_calendar_day_max_v1",
                input_space="raw_probability",
                param_A=1.0,
                param_B=0.0,
                bootstrap_params=[],
                n_samples=100,
                # metric_identity intentionally omitted
            )

    def test_high_and_low_models_have_different_model_keys(self):
        """R-4D: save_platt_model_v2 must not produce the same model_key for different tracks."""
        from src.calibration.store import save_platt_model_v2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

        conn = self._make_conn()

        save_platt_model_v2(
            conn=conn,
            metric_identity=HIGH_LOCALDAY_MAX,
            cluster="NYC_F_2",
            season="spring",
            data_version=HIGH_LOCALDAY_MAX.data_version,
            input_space="raw_probability",
            param_A=1.0,
            param_B=0.0,
            bootstrap_params=[],
            n_samples=100,
        )
        conn.commit()

        save_platt_model_v2(
            conn=conn,
            metric_identity=LOW_LOCALDAY_MIN,
            cluster="NYC_F_2",
            season="spring",
            data_version=LOW_LOCALDAY_MIN.data_version,
            input_space="raw_probability",
            param_A=0.9,
            param_B=0.1,
            bootstrap_params=[],
            n_samples=100,
        )
        conn.commit()

        rows = conn.execute(
            "SELECT model_key, temperature_metric FROM platt_models_v2"
        ).fetchall()
        assert len(rows) == 2, (
            f"Expected 2 platt_models_v2 rows (one high, one low), got {len(rows)} (R-4D)"
        )
        keys = {r[0] for r in rows}
        assert len(keys) == 2, (
            f"High and low Platt models must have distinct model_keys, got {keys} (R-4D). "
            "Family isolation violated."
        )
        metrics = {r[1] for r in rows}
        assert metrics == {"high", "low"}, (
            f"Expected temperature_metric values {{'high', 'low'}}, got {metrics} (R-4D)"
        )

    def test_high_track_model_has_correct_data_version(self):
        """R-4D: High-track Platt model must carry the local_calendar_day_max data_version."""
        from src.calibration.store import save_platt_model_v2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = self._make_conn()
        save_platt_model_v2(
            conn=conn,
            metric_identity=HIGH_LOCALDAY_MAX,
            cluster="NYC_F_2",
            season="spring",
            data_version=HIGH_LOCALDAY_MAX.data_version,
            input_space="raw_probability",
            param_A=1.0,
            param_B=0.0,
            bootstrap_params=[],
            n_samples=100,
        )
        conn.commit()

        row = conn.execute(
            "SELECT temperature_metric, data_version FROM platt_models_v2"
        ).fetchone()
        assert row is not None
        tm, dv = row
        assert tm == "high"
        assert dv == "tigge_mx2t6_local_calendar_day_max_v1", (
            f"High-track Platt model must use 'tigge_mx2t6_local_calendar_day_max_v1', "
            f"got {dv!r} (R-4D). Peak-window tag is quarantined."
        )

    def test_duplicate_high_model_raises_integrity_error(self):
        """R-4D: Inserting two high models with the same business key must raise IntegrityError."""
        from src.calibration.store import save_platt_model_v2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = self._make_conn()
        kwargs = dict(
            conn=conn,
            metric_identity=HIGH_LOCALDAY_MAX,
            cluster="NYC_F_2",
            season="spring",
            data_version=HIGH_LOCALDAY_MAX.data_version,
            input_space="raw_probability",
            param_A=1.0,
            param_B=0.0,
            bootstrap_params=[],
            n_samples=100,
        )
        save_platt_model_v2(**kwargs)
        conn.commit()

        with pytest.raises((sqlite3.IntegrityError, RuntimeError)):
            save_platt_model_v2(**dict(kwargs, param_A=0.5))
            conn.commit()

    def test_model_row_has_no_city_or_target_date_column(self):
        """R-4D + R-N cross-check: platt_models_v2 must not have city or target_date."""
        conn = self._make_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(platt_models_v2)")}
        assert "city" not in cols, "platt_models_v2 must not have 'city' column (R-4D/R-N)"
        assert "target_date" not in cols, "platt_models_v2 must not have 'target_date' column (R-4D/R-N)"
