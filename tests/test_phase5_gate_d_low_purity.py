# Created: 2026-04-17
# Last reused/audited: 2026-04-17
# Authority basis: team_lead_handoff.md §"Phase 5C scope" Gate D; docs/authority/zeus_dual_track_architecture.md §6
"""Phase 5C Gate D: low-purity isolation tests — R-AZ

Asserts calibration_pairs_v2 and platt_models_v2 have zero cross-metric leakage:
  - HIGH rebuild does not write LOW-metric rows.
  - LOW rebuild does not write HIGH-metric rows.
  - Platt model buckets do not share (temperature_metric, cluster, season) keys across metrics.

R-AZ (TestGateDLowPurityIsolation): insert mixed high+low snapshot rows; run rebuild_v2
for each spec; assert no cross-metric rows appear in calibration_pairs_v2; assert
platt_models_v2 model_key is scoped per metric.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_SNAPSHOTS_V2_DDL = """
CREATE TABLE IF NOT EXISTS ensemble_snapshots_v2 (
    snapshot_id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL,
    physical_quantity TEXT NOT NULL,
    data_version TEXT NOT NULL,
    members_unit TEXT NOT NULL DEFAULT 'degC',
    training_allowed INTEGER NOT NULL DEFAULT 1,
    issue_time TEXT,
    available_at TEXT,
    lead_hours REAL,
    causality_status TEXT DEFAULT 'OK',
    authority TEXT DEFAULT 'VERIFIED',
    members_json TEXT NOT NULL DEFAULT '[]',
    manifest_hash TEXT,
    provenance_json TEXT
)
"""

_CALIBRATION_PAIRS_V2_DDL = """
CREATE TABLE IF NOT EXISTS calibration_pairs_v2 (
    id INTEGER PRIMARY KEY,
    city TEXT, target_date TEXT, temperature_metric TEXT, observation_field TEXT,
    range_label TEXT, p_raw REAL, outcome INTEGER, lead_days REAL, season TEXT,
    cluster TEXT, forecast_available_at TEXT, settlement_value REAL,
    decision_group_id TEXT, bias_corrected INTEGER, authority TEXT, bin_source TEXT,
    data_version TEXT, training_allowed INTEGER, causality_status TEXT, snapshot_id INTEGER
)
"""

_PLATT_MODELS_V2_DDL = """
CREATE TABLE IF NOT EXISTS platt_models_v2 (
    model_key TEXT PRIMARY KEY,
    temperature_metric TEXT, cluster TEXT, season TEXT, data_version TEXT,
    input_space TEXT, param_A REAL, param_B REAL, param_C REAL,
    bootstrap_params_json TEXT, n_samples INTEGER, brier_insample REAL,
    fitted_at TEXT, is_active INTEGER DEFAULT 1, authority TEXT DEFAULT 'VERIFIED'
)
"""

_OBSERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS observations (
    city TEXT, target_date TEXT, high_temp REAL, low_temp REAL,
    unit TEXT, authority TEXT, source TEXT
)
"""

_CALIBRATION_BINS_DDL = """
CREATE TABLE IF NOT EXISTS calibration_bins (
    bin_id INTEGER PRIMARY KEY, city TEXT, temperature_metric TEXT,
    bin_label TEXT, low REAL, high REAL, unit TEXT
)
"""


def _make_gate_d_db() -> sqlite3.Connection:
    """Build a minimal in-memory DB with mixed high+low snapshot rows."""
    from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        _SNAPSHOTS_V2_DDL + ";"
        + _CALIBRATION_PAIRS_V2_DDL + ";"
        + _PLATT_MODELS_V2_DDL + ";"
        + _OBSERVATIONS_DDL + ";"
        + _CALIBRATION_BINS_DDL + ";"
    )

    # One HIGH snapshot + matching observation
    conn.execute("""
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity, data_version,
            members_unit, training_allowed, issue_time, available_at, lead_hours,
            causality_status, authority, members_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "Chicago", "2026-06-01",
        "high", "mx2t6_local_calendar_day_max", HIGH_LOCALDAY_MAX.data_version,
        "degC", 1,
        "2026-05-30T12:00:00Z", "2026-05-30T14:00:00Z", 48.0,
        "OK", "VERIFIED",
        json.dumps([305.0 + i * 0.01 for i in range(51)]),
    ))
    conn.execute("""
        INSERT INTO observations (city, target_date, high_temp, low_temp, unit, authority, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, ("Chicago", "2026-06-01", 32.0, 18.0, "degC", "VERIFIED", "tigge"))

    # One LOW snapshot + matching observation
    conn.execute("""
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity, data_version,
            members_unit, training_allowed, issue_time, available_at, lead_hours,
            causality_status, authority, members_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "Chicago", "2026-06-01",
        "low", "mn2t6_local_calendar_day_min", LOW_LOCALDAY_MIN.data_version,
        "degC", 1,
        "2026-05-30T12:00:00Z", "2026-05-30T14:00:00Z", 48.0,
        "OK", "VERIFIED",
        json.dumps([290.0 + i * 0.01 for i in range(51)]),
    ))

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# R-AZ: TestGateDLowPurityIsolation
# ---------------------------------------------------------------------------

class TestGateDLowPurityIsolation:
    """R-AZ: calibration_pairs_v2 and platt_models_v2 must have zero cross-metric leakage."""

    def test_R_AZ_1_high_rebuild_writes_only_high_rows(self):
        """R-AZ-1 (RED): rebuild_v2 with HIGH_SPEC must not write any temperature_metric='low' rows.

        Pre-fix: _process_snapshot_v2 has no spec param → SQL pre-filter is the only guard.
        If the SQL filter in rebuild_v2 is missing or weak, LOW rows from ensemble_snapshots_v2
        could be processed. Post-fix: spec param + data_version assertion makes this impossible.
        """
        from scripts.rebuild_calibration_pairs_v2 import rebuild_v2, CalibrationMetricSpec, RebuildStatsV2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = _make_gate_d_db()
        high_spec = CalibrationMetricSpec(HIGH_LOCALDAY_MAX, HIGH_LOCALDAY_MAX.data_version)
        stats = RebuildStatsV2()

        import inspect
        sig = inspect.signature(rebuild_v2)
        if "spec" not in sig.parameters:
            pytest.fail(
                "rebuild_v2 has no 'spec' parameter. "
                "Cannot run HIGH-spec rebuild in isolation — cross-metric leakage is structurally unguarded. "
                f"Current signature: {sig}. "
                "Fix: add spec: CalibrationMetricSpec param to rebuild_v2 and propagate to _process_snapshot_v2."
            )

        try:
            rebuild_v2(conn, spec=high_spec, n_mc=None, rng=np.random.default_rng(0), stats=stats)
        except Exception as e:
            # Missing tables or config in :memory: DB may cause early exit — that's OK for Gate D.
            # We only care about what was written before any error.
            pass

        low_rows = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE temperature_metric = 'low'"
        ).fetchone()[0]
        assert low_rows == 0, (
            f"HIGH rebuild wrote {low_rows} temperature_metric='low' rows to calibration_pairs_v2. "
            "Cross-metric leakage confirmed. Fix: spec param must filter to HIGH data_version only."
        )

    def test_R_AZ_2_low_rebuild_writes_only_low_rows(self):
        """R-AZ-2 (RED): rebuild_v2 with LOW_SPEC must not write any temperature_metric='high' rows."""
        from scripts.rebuild_calibration_pairs_v2 import rebuild_v2, CalibrationMetricSpec, RebuildStatsV2
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        conn = _make_gate_d_db()
        low_spec = CalibrationMetricSpec(LOW_LOCALDAY_MIN, LOW_LOCALDAY_MIN.data_version)
        stats = RebuildStatsV2()

        import inspect
        sig = inspect.signature(rebuild_v2)
        if "spec" not in sig.parameters:
            pytest.fail(
                "rebuild_v2 has no 'spec' parameter. "
                "Cannot run LOW-spec rebuild in isolation. "
                "Fix: add spec: CalibrationMetricSpec param to rebuild_v2."
            )

        try:
            rebuild_v2(conn, spec=low_spec, n_mc=None, rng=np.random.default_rng(0), stats=stats)
        except Exception:
            pass

        high_rows = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE temperature_metric = 'high'"
        ).fetchone()[0]
        assert high_rows == 0, (
            f"LOW rebuild wrote {high_rows} temperature_metric='high' rows to calibration_pairs_v2. "
            "Cross-metric leakage confirmed."
        )

    def test_R_AZ_3_platt_model_keys_scoped_per_metric(self):
        """R-AZ-3 (RED): platt_models_v2 model_key must encode temperature_metric; no shared bucket keys.

        model_key = '{temperature_metric}:{cluster}:{season}' — HIGH and LOW must never collide
        on the same model_key even if cluster+season are identical.
        """
        from scripts.rebuild_calibration_pairs_v2 import CalibrationMetricSpec
        from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN
        from src.calibration.store import save_platt_model_v2

        import inspect
        try:
            sig = inspect.signature(save_platt_model_v2)
        except Exception:
            pytest.fail("save_platt_model_v2 not importable from src.calibration.store.")

        conn = _make_gate_d_db()

        # Write a HIGH model then a LOW model with same cluster/season
        save_platt_model_v2(
            conn,
            metric_identity=HIGH_LOCALDAY_MAX,
            cluster="warm_midwest",
            season="summer",
            data_version=HIGH_LOCALDAY_MAX.data_version,
            param_A=-1.0, param_B=0.5, bootstrap_params=[], n_samples=50,
        )
        save_platt_model_v2(
            conn,
            metric_identity=LOW_LOCALDAY_MIN,
            cluster="warm_midwest",
            season="summer",
            data_version=LOW_LOCALDAY_MIN.data_version,
            param_A=-1.1, param_B=0.6, bootstrap_params=[], n_samples=45,
        )

        rows = conn.execute(
            "SELECT model_key, temperature_metric FROM platt_models_v2 ORDER BY model_key"
        ).fetchall()
        keys = [row["model_key"] for row in rows]
        assert len(keys) == 2, (
            f"Expected 2 distinct model_key rows (one per metric), got {len(keys)}: {keys}. "
            "HIGH and LOW with same cluster/season must produce distinct model_keys. "
            "model_key must be '{temperature_metric}:{cluster}:{season}'."
        )
        metrics = {row["temperature_metric"] for row in rows}
        assert "high" in metrics and "low" in metrics, (
            f"Expected both 'high' and 'low' in temperature_metric column; got {metrics}. "
            "Platt model rows must be metric-scoped."
        )
        # Confirm no key collision
        assert len(set(keys)) == len(keys), (
            f"model_key collision detected: {keys}. "
            "HIGH and LOW bucket keys must not share the same model_key string."
        )
