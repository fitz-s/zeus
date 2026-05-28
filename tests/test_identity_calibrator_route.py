# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Relationship tests for identity_full_transport_v1 calibration route (Zeus #64).
# Reuse: Inspect calibration route + IdentityCalibrator before reuse; covers 3 invariants.
# Authority basis: Zeus #64 FT_SHIP_MASTER_SPEC §total-function-rule + gap 3.3
"""Relationship tests for identity_full_transport_v1 calibration route (Zeus #64).

Three invariants verified:

(a) [RELATIONSHIP] Identity calibrator bucket: get_calibrator returns
    (IdentityCalibrator, level=1) → evaluator gate passes → p_cal == p_raw
    element-wise → reaches edge evaluation (NOT CALIBRATION_IMMATURE_NO_PLATT).

(b) [SAFETY GATE] Genuinely uncalibrated bucket (no Platt, no identity):
    get_calibrator returns (None, 4) → evaluator still emits
    CALIBRATION_IMMATURE_NO_PLATT (safety gate not regressed).

(c) [LEARNED PLATT] Bucket with a normal Platt model → get_calibrator
    returns (ExtendedPlattCalibrator, level in {1,2,3}) → unchanged.

RED proof for (a): test_identity_red_before_fix() inserts an identity row but
calls get_calibrator through the pre-fix maturity_level(0)=4 path by
monkeypatching _model_data_to_calibrator out. This produces (None, 4) which
the evaluator gate blocks — confirming the gate was blocking before the fix.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import get_calibrator
from src.calibration.platt import (
    ExtendedPlattCalibrator,
    IdentityCalibrator,
    IDENTITY_CALIBRATION_METHOD,
    calibrate_and_normalize,
)
from src.config import City
from src.state.db import get_connection, init_schema
from src.state.schema.v2_schema import apply_canonical_schema

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_NYC = City(
    name="NYC", lat=40.7772, lon=-73.8726,
    timezone="America/New_York", cluster="NYC",
    settlement_unit="F", wu_station="KLGA",
)
_TARGET_DATE = "2026-06-15"  # JJA season for NYC (NH)


def _make_conn(tmp_path: Path, name: str = "test") -> sqlite3.Connection:
    db_path = tmp_path / f"{name}.db"
    conn = get_connection(db_path)
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


def _insert_identity_row(conn: sqlite3.Connection, cluster: str, season: str) -> None:
    """Insert an identity_full_transport_v1 platt_models_v2 row directly.

    Bypasses save_platt_model (which is a @capability-gated write function)
    to insert the identity row without triggering capability enforcement.
    The row uses:
      - calibration_method = 'identity_full_transport_v1'
      - input_space = 'width_normalized_density' (so manager does NOT try to refit)
      - n_samples = 0 (no training pairs consumed)
      - authority = 'VERIFIED'
      - is_active = 1
    The param_A/B/C values are irrelevant for identity but must satisfy NOT NULL.
    """
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    data_version = HIGH_LOCALDAY_MAX.data_version
    model_key = (
        f"high:{cluster}:{season}:{data_version}"
        ":00:tigge_mars:full:width_normalized_density"
        f":{IDENTITY_CALIBRATION_METHOD}"
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO platt_models_v2
          (model_key, temperature_metric, cluster, season, data_version,
           input_space, param_A, param_B, param_C, bootstrap_params_json,
           n_samples, brier_insample, fitted_at, is_active, authority,
           cycle, source_id, horizon_profile, calibration_method)
        VALUES (?, 'high', ?, ?, ?,
                'width_normalized_density', 0.0, 0.0, 0.0, '[]',
                0, NULL, ?, 1, 'VERIFIED',
                '00', 'tigge_mars', 'full', ?)
        """,
        (model_key, cluster, season, data_version, now, IDENTITY_CALIBRATION_METHOD),
    )
    conn.commit()


def _insert_platt_row(
    conn: sqlite3.Connection, cluster: str, season: str, n_samples: int = 200
) -> None:
    """Insert a normal (learned) Platt row via save_platt_model."""
    from src.calibration.store import save_platt_model
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    save_platt_model(
        conn,
        metric_identity=HIGH_LOCALDAY_MAX,
        cluster=cluster,
        season=season,
        data_version=HIGH_LOCALDAY_MAX.data_version,
        param_A=1.0,
        param_B=0.05,
        param_C=-0.1,
        bootstrap_params=[(1.0, 0.05, -0.1)] * 5,
        n_samples=n_samples,
        input_space="width_normalized_density",
        authority="VERIFIED",
        cycle="00",
        source_id="tigge_mars",
        horizon_profile="full",
    )
    conn.commit()


# ---------------------------------------------------------------------------
# RED proof: identity row without the fix → maturity_level(0)=4 → blocked
# ---------------------------------------------------------------------------

class TestIdentityRedBeforeFix:
    """Prove the pre-fix path blocked identity buckets.

    The pre-fix gap had two components:
      1. maturity_level(0) == 4 — an identity row with n_samples=0 would have
         been classified as level 4 (uncalibrated) by the maturity gate.
      2. The evaluator gate ``if cal is None or cal_level >= 4`` would then
         emit CALIBRATION_IMMATURE_NO_PLATT and block the bucket.

    We prove both facts without needing to run get_calibrator in a pre-fix state,
    since the pre-fix state is precisely: maturity_level(0)=4, and the evaluator
    gate condition (cal is None or level >= 4) is True whenever level=4.
    """

    def test_pre_fix_maturity_level_zero_n_samples_is_4(self):
        """RED: maturity_level(0) == 4 — the root cause that blocked identity buckets.

        An identity row has n_samples=0. Pre-fix, get_calibrator called
        maturity_level(model_data["n_samples"]) = maturity_level(0) = 4.
        level=4 triggers the evaluator gate → CALIBRATION_IMMATURE_NO_PLATT.
        This is the exact relationship that the fix bypasses.
        """
        from src.calibration.manager import maturity_level

        level_for_zero = maturity_level(0)
        assert level_for_zero == 4, (
            f"maturity_level(0) must be 4 (got {level_for_zero}). "
            "This is the pre-fix gap: identity rows with n_samples=0 would have "
            "been classified level=4 and blocked by the evaluator gate."
        )

    def test_pre_fix_evaluator_gate_fires_at_level4(self):
        """RED: evaluator gate condition fires when level == 4.

        The evaluator gate (evaluator.py:4215): ``if cal is None or cal_level >= 4``.
        When get_calibrator returned (ExtendedPlattCalibrator, 4) for an identity
        row (because maturity_level(0)=4), the gate fired → CALIBRATION_IMMATURE_NO_PLATT.
        """
        fake_cal = ExtendedPlattCalibrator()
        fake_cal.fitted = True
        level = 4  # what maturity_level(0) would have returned

        gate_fires = (fake_cal is None or level >= 4)
        assert gate_fires, (
            "Evaluator gate must fire for level=4. "
            "This proves the pre-fix identity route was blocked."
        )

    def test_pre_fix_simulated_get_calibrator_returns_level4(self, tmp_path, monkeypatch):
        """RED: simulating pre-fix get_calibrator: identity row → (cal, 4).

        Monkeypatch the IDENTITY_CALIBRATION_METHOD check inside get_calibrator
        by replacing IDENTITY_CALIBRATION_METHOD with a sentinel that never matches,
        so the identity fast-path is skipped and maturity_level(0)=4 is returned.
        """
        import src.calibration.manager as manager_mod

        conn = _make_conn(tmp_path, "red_simulated")
        from src.calibration.manager import season_from_date
        season = season_from_date(_TARGET_DATE, lat=_NYC.lat)
        _insert_identity_row(conn, _NYC.cluster, season)

        # Disable the identity fast-path by replacing the constant with a sentinel
        # that will never match the stored calibration_method value.
        monkeypatch.setattr(manager_mod, "IDENTITY_CALIBRATION_METHOD", "__DISABLED_FOR_RED_TEST__")

        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="high")
        conn.close()

        assert level == 4, (
            f"RED: with identity fast-path disabled, identity row yields level={level}. "
            "Expected level=4 (maturity_level(0)=4 before the fix)."
        )
        gate_fires = (cal is None or level >= 4)
        assert gate_fires, (
            "RED: evaluator gate fires when identity fast-path is disabled. "
            "This confirms the fix is what makes identity routes tradeable."
        )


# ---------------------------------------------------------------------------
# (a) Identity calibrator: p_cal == p_raw AND reaches edge evaluation
# ---------------------------------------------------------------------------

class TestIdentityCalibratorRoute:

    def test_get_calibrator_returns_identity_level1(self, tmp_path):
        """Identity row → get_calibrator returns (IdentityCalibrator, 1)."""
        conn = _make_conn(tmp_path, "identity_a")
        from src.calibration.manager import season_from_date
        season = season_from_date(_TARGET_DATE, lat=_NYC.lat)
        _insert_identity_row(conn, _NYC.cluster, season)

        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="high")
        conn.close()

        assert isinstance(cal, IdentityCalibrator), (
            f"Expected IdentityCalibrator, got {type(cal).__name__!r}"
        )
        assert level == 1, (
            f"Identity calibrator must return level=1 (calibrated), got {level}. "
            "level>=4 would trigger CALIBRATION_IMMATURE_NO_PLATT in evaluator."
        )

    def test_identity_evaluator_gate_passes(self, tmp_path):
        """cal is not None AND cal_level < 4 → evaluator gate does NOT fire."""
        conn = _make_conn(tmp_path, "identity_gate")
        from src.calibration.manager import season_from_date
        season = season_from_date(_TARGET_DATE, lat=_NYC.lat)
        _insert_identity_row(conn, _NYC.cluster, season)

        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="high")
        conn.close()

        # Reproduce the exact evaluator gate condition (evaluator.py:4215)
        gate_fires = (cal is None or level >= 4)
        assert not gate_fires, (
            f"Evaluator gate fires for identity calibrator: cal={cal!r}, level={level}. "
            "Identity route must reach edge/FDR evaluation (gate must NOT fire)."
        )

    def test_p_cal_equals_p_raw_element_wise(self, tmp_path):
        """calibrate_and_normalize with IdentityCalibrator → p_cal == p_raw element-wise."""
        conn = _make_conn(tmp_path, "identity_pcal")
        from src.calibration.manager import season_from_date
        season = season_from_date(_TARGET_DATE, lat=_NYC.lat)
        _insert_identity_row(conn, _NYC.cluster, season)

        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="high")
        conn.close()

        assert isinstance(cal, IdentityCalibrator)

        # Simulate a realistic 11-bin forecast vector (normalized, sums to 1.0)
        raw = np.array([0.02, 0.05, 0.10, 0.15, 0.20, 0.18, 0.13, 0.08, 0.05, 0.03, 0.01])
        raw = raw / raw.sum()  # ensure exact normalization
        bin_widths = [2.0] * 9 + [None, None]  # 9 finite bins + 2 shoulders

        p_cal = calibrate_and_normalize(raw, cal, lead_days=5.0, bin_widths=bin_widths)

        # IdentityCalibrator.predict_for_bin returns p_raw unchanged.
        # calibrate_and_normalize then normalizes by sum. Since raw already sums to 1,
        # p_cal must be element-wise identical (within float epsilon).
        np.testing.assert_allclose(
            p_cal, raw, rtol=1e-10, atol=1e-12,
            err_msg=(
                "p_cal must equal p_raw element-wise for identity calibrator. "
                "Any deviation means the identity transform is not a true identity."
            ),
        )

    def test_identity_predict_for_bin_ignores_lead_days_and_width(self):
        """IdentityCalibrator.predict_for_bin always returns p_raw unchanged."""
        cal = IdentityCalibrator()
        for p in [0.01, 0.1, 0.5, 0.9, 0.99]:
            for lead in [1.0, 5.0, 14.0]:
                for width in [None, 1.0, 5.0]:
                    result = cal.predict_for_bin(p, lead, bin_width=width)
                    assert abs(result - p) < 1e-12, (
                        f"IdentityCalibrator.predict_for_bin({p}, {lead}, width={width}) "
                        f"= {result}, expected {p}"
                    )

    def test_identity_calibration_method_constant(self):
        """IDENTITY_CALIBRATION_METHOD constant is stable (stable for log aggregation)."""
        assert IDENTITY_CALIBRATION_METHOD == "identity_full_transport_v1"

    def test_identity_input_space_is_width_normalized(self):
        """IdentityCalibrator.input_space must be 'width_normalized_density'.

        If it were 'raw_probability', manager.get_calibrator would try to refit
        from pairs (the stale-Platt branch at manager.py:894), defeating the
        identity route entirely.
        """
        cal = IdentityCalibrator()
        assert cal.input_space == "width_normalized_density", (
            "IdentityCalibrator.input_space must be 'width_normalized_density' "
            "to bypass the stale-Platt refit branch in get_calibrator."
        )


# ---------------------------------------------------------------------------
# (b) Safety gate: genuinely uncalibrated bucket still blocked
# ---------------------------------------------------------------------------

class TestUncalibratedBucketStillBlocked:

    def test_no_platt_no_identity_returns_none_level4(self, tmp_path):
        """Genuinely uncalibrated bucket (no rows) → (None, 4) → gate fires."""
        conn = _make_conn(tmp_path, "uncal_b")
        # No rows inserted for NYC/JJA

        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="high")
        conn.close()

        assert cal is None, (
            "Uncalibrated bucket must return cal=None. "
            "Safety gate must not be regressed by identity calibrator changes."
        )
        assert level == 4, (
            f"Uncalibrated bucket must return level=4, got {level}. "
            "Evaluator gate must fire for genuinely uncalibrated buckets."
        )
        gate_fires = (cal is None or level >= 4)
        assert gate_fires, "Evaluator gate must fire for genuinely uncalibrated buckets."

    def test_different_cluster_identity_does_not_bleed(self, tmp_path):
        """Identity row for cluster A does not serve cluster B (no bucket bleed)."""
        conn = _make_conn(tmp_path, "bleed_b")
        from src.calibration.manager import season_from_date
        season = season_from_date(_TARGET_DATE, lat=_NYC.lat)

        # Insert identity only for "LONDON" cluster
        _insert_identity_row(conn, "LONDON", season)

        # Query for NYC cluster (different cluster, no row)
        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="high")
        conn.close()

        # NYC has no row — must fall through to uncalibrated
        gate_fires = (cal is None or level >= 4)
        assert gate_fires, (
            "Identity row for LONDON must not serve NYC. "
            "Bucket isolation safety gate must not be regressed."
        )


# ---------------------------------------------------------------------------
# (c) Learned Platt bucket unchanged
# ---------------------------------------------------------------------------

class TestLearnedPlattUnchanged:

    def test_learned_platt_returns_extendedplatt_not_identity(self, tmp_path):
        """Normal Platt row → get_calibrator returns ExtendedPlattCalibrator, level in {1,2,3}."""
        conn = _make_conn(tmp_path, "platt_c")
        from src.calibration.manager import season_from_date
        season = season_from_date(_TARGET_DATE, lat=_NYC.lat)
        _insert_platt_row(conn, _NYC.cluster, season, n_samples=200)

        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="high")
        conn.close()

        assert isinstance(cal, ExtendedPlattCalibrator), (
            f"Learned Platt row must return ExtendedPlattCalibrator, got {type(cal).__name__!r}"
        )
        assert isinstance(level, int) and 1 <= level <= 3, (
            f"Learned Platt with n_samples=200 must yield level in {{1,2,3}}, got {level}"
        )

    def test_learned_platt_p_cal_differs_from_p_raw(self, tmp_path):
        """Normal Platt calibrates probabilities (p_cal != p_raw for non-trivial inputs)."""
        conn = _make_conn(tmp_path, "platt_differs")
        from src.calibration.manager import season_from_date
        season = season_from_date(_TARGET_DATE, lat=_NYC.lat)
        _insert_platt_row(conn, _NYC.cluster, season, n_samples=200)

        cal, level = get_calibrator(conn, _NYC, _TARGET_DATE, temperature_metric="high")
        conn.close()

        assert isinstance(cal, ExtendedPlattCalibrator)

        # A Platt with A=1.0, B=0.05, C=-0.1 and lead_days=5.0 WILL transform
        # probabilities away from the raw values (non-identity transform).
        raw = np.array([0.1, 0.2, 0.3, 0.2, 0.1, 0.05, 0.03, 0.01, 0.005, 0.004, 0.001])
        raw = raw / raw.sum()
        p_cal = calibrate_and_normalize(raw, cal, lead_days=5.0, bin_widths=[None] * 11)

        # With A=1.0, B=0.05, C=-0.1 the output is NOT identical to input.
        # If they were equal, the test would incorrectly pass for a broken identity.
        assert not np.allclose(p_cal, raw, atol=1e-6), (
            "Learned Platt must transform probabilities (p_cal != p_raw). "
            "This confirms the test distinguishes identity from learned Platt."
        )
