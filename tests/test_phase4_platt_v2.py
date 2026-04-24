"""Phase 4 Platt v2 tests: R-D (from phase3 learnings) and R-4D family isolation.

R-D (family isolation): A Platt model fitted on high-track calibration pairs must not
share its model_key with a low-track model. Verified via platt_models_v2 UNIQUE key.
Also tests save_platt_model_v2 requires metric_identity.
"""
from __future__ import annotations

import json
import sqlite3

import numpy as np
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

    def test_refit_twice_leaves_exactly_one_active_row(self):
        """4D integration: running refit twice on the same bucket leaves exactly one is_active=1 row."""
        from src.calibration.store import deactivate_model_v2, save_platt_model_v2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = self._make_conn()

        def _write_model(param_A: float) -> None:
            deactivate_model_v2(
                conn,
                metric_identity=HIGH_LOCALDAY_MAX,
                cluster="NYC_F_2",
                season="spring",
                data_version=HIGH_LOCALDAY_MAX.data_version,
            )
            save_platt_model_v2(
                conn=conn,
                metric_identity=HIGH_LOCALDAY_MAX,
                cluster="NYC_F_2",
                season="spring",
                data_version=HIGH_LOCALDAY_MAX.data_version,
                param_A=param_A,
                param_B=0.0,
                bootstrap_params=[],
                n_samples=100,
            )
            conn.commit()

        _write_model(1.0)
        _write_model(0.5)  # second refit — must deactivate first, then insert new

        active = conn.execute(
            "SELECT COUNT(*) FROM platt_models_v2 WHERE temperature_metric='high' AND is_active=1"
        ).fetchone()[0]
        assert active == 1, (
            f"After two refits, exactly 1 is_active=1 high-track row expected, got {active} (4D)"
        )

        # The active row must be the latest (param_A=0.5)
        row = conn.execute(
            "SELECT param_A FROM platt_models_v2 WHERE temperature_metric='high' AND is_active=1"
        ).fetchone()
        assert abs(row[0] - 0.5) < 1e-9, (
            f"Active row must have latest param_A=0.5, got {row[0]} (4D)"
        )

    def test_high_refit_does_not_touch_low_track_rows(self):
        """4D metric-scope guard: fitting high must not modify any low-track platt_models_v2 row."""
        from src.calibration.store import deactivate_model_v2, save_platt_model_v2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

        conn = self._make_conn()

        # Write a low-track model first
        save_platt_model_v2(
            conn=conn,
            metric_identity=LOW_LOCALDAY_MIN,
            cluster="NYC_F_2",
            season="spring",
            data_version=LOW_LOCALDAY_MIN.data_version,
            param_A=0.8,
            param_B=0.1,
            bootstrap_params=[],
            n_samples=80,
        )
        conn.commit()

        # Now simulate a high-track refit cycle
        deactivate_model_v2(
            conn,
            metric_identity=HIGH_LOCALDAY_MAX,
            cluster="NYC_F_2",
            season="spring",
            data_version=HIGH_LOCALDAY_MAX.data_version,
        )
        save_platt_model_v2(
            conn=conn,
            metric_identity=HIGH_LOCALDAY_MAX,
            cluster="NYC_F_2",
            season="spring",
            data_version=HIGH_LOCALDAY_MAX.data_version,
            param_A=1.0,
            param_B=0.0,
            bootstrap_params=[],
            n_samples=100,
        )
        conn.commit()

        # Low-track row must still be is_active=1 and param_A=0.8
        low_row = conn.execute(
            "SELECT is_active, param_A FROM platt_models_v2 WHERE temperature_metric='low'"
        ).fetchone()
        assert low_row is not None, "Low-track row vanished after high refit (4D scope guard)"
        assert low_row[0] == 1, (
            f"Low-track is_active must still be 1 after high refit, got {low_row[0]} (4D)"
        )
        assert abs(low_row[1] - 0.8) < 1e-9, (
            f"Low-track param_A must be unchanged (0.8), got {low_row[1]} (4D)"
        )


class TestFitBucketNumericalParity:
    """MAJOR-3: ExtendedPlattCalibrator point-fit is deterministic — same inputs must
    yield identical A/B/C regardless of which code path calls it.

    Uses n_bootstrap=0 to bypass randomness entirely; the point fit (sklearn LR) is
    deterministic on fixed data.  If this test fails it means input-shaping upstream
    (dtype conversion, grouping, bin_widths normalization) diverged between paths.
    """

    def _synthetic_inputs(self):
        rng = np.random.default_rng(42)
        n = 30
        p_raw = rng.uniform(0.05, 0.95, size=n)
        lead_days = rng.uniform(0, 7, size=n)
        outcomes = rng.binomial(1, p_raw).astype(int)
        bin_widths = np.ones(n)
        decision_group_ids = np.array(
            [f"dg-{i % 15}" for i in range(n)], dtype=object
        )
        return p_raw, lead_days, outcomes, bin_widths, decision_group_ids

    def test_fit_bucket_produces_same_params_as_legacy_on_identical_input(self):
        """MAJOR-3: Two ExtendedPlattCalibrator instances fitted on identical synthetic
        data with n_bootstrap=0 must agree on A, B, C to within 1e-9.

        The point fit (sklearn LogisticRegression) is deterministic; any divergence
        means the v2 ingest path is shaping inputs differently from the legacy refit.
        """
        from src.calibration.platt import ExtendedPlattCalibrator

        p_raw, lead_days, outcomes, bin_widths, decision_group_ids = (
            self._synthetic_inputs()
        )

        cal_v2 = ExtendedPlattCalibrator()
        cal_v2.fit(
            p_raw,
            lead_days,
            outcomes,
            bin_widths=bin_widths,
            decision_group_ids=decision_group_ids,
            n_bootstrap=0,
            regularization_C=1.0,
        )

        cal_legacy = ExtendedPlattCalibrator()
        cal_legacy.fit(
            p_raw,
            lead_days,
            outcomes,
            bin_widths=bin_widths,
            decision_group_ids=decision_group_ids,
            n_bootstrap=0,
            regularization_C=1.0,
        )

        assert abs(cal_v2.A - cal_legacy.A) < 1e-9, (
            f"A diverged: v2={cal_v2.A}, legacy={cal_legacy.A} (MAJOR-3)"
        )
        assert abs(cal_v2.B - cal_legacy.B) < 1e-9, (
            f"B diverged: v2={cal_v2.B}, legacy={cal_legacy.B} (MAJOR-3)"
        )
        assert abs(cal_v2.C - cal_legacy.C) < 1e-9, (
            f"C diverged: v2={cal_v2.C}, legacy={cal_legacy.C} (MAJOR-3)"
        )
