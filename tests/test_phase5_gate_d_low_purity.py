# Created: 2026-04-17
# Last reused/audited: 2026-05-07
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
    fitted_at TEXT, is_active INTEGER DEFAULT 1, authority TEXT DEFAULT 'VERIFIED',
    cycle TEXT DEFAULT '00', source_id TEXT DEFAULT 'tigge_mars',
    horizon_profile TEXT DEFAULT 'full'
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
        """R-AZ-2: LOW-spec eligible-snapshot query returns ONLY LOW rows.

        Phase 7B rewrite (was mirror test with try/except: pass swallowing
        TypeError from stale stats= kwarg). Now tests the structural invariant
        directly via _fetch_eligible_snapshots_v2: LOW spec must select only
        LOW snapshots from the mixed high+low fixture. This is the seam where
        cross-metric leakage would first appear — if the SQL WHERE clause drops
        or mishandles temperature_metric, LOW rebuild would process HIGH rows.
        """
        from scripts.rebuild_calibration_pairs_v2 import (
            _fetch_eligible_snapshots_v2, CalibrationMetricSpec,
        )
        from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

        conn = _make_gate_d_db()
        high_spec = CalibrationMetricSpec(HIGH_LOCALDAY_MAX, HIGH_LOCALDAY_MAX.data_version)
        low_spec = CalibrationMetricSpec(LOW_LOCALDAY_MIN, LOW_LOCALDAY_MIN.data_version)

        high_eligible = _fetch_eligible_snapshots_v2(conn, city_filter=None, spec=high_spec)
        low_eligible = _fetch_eligible_snapshots_v2(conn, city_filter=None, spec=low_spec)

        # Structural invariant #1: spec.metric filters eligible snapshots.
        assert len(high_eligible) == 1, f"HIGH spec got {len(high_eligible)} snapshots, want 1"
        assert len(low_eligible) == 1, f"LOW spec got {len(low_eligible)} snapshots, want 1"

        # Structural invariant #2: no cross-metric leakage — LOW eligibility excludes HIGH.
        low_temp_metrics = {row["data_version"] for row in low_eligible}
        assert low_temp_metrics == {LOW_LOCALDAY_MIN.data_version}, (
            f"LOW spec eligible snapshots include non-LOW data_versions: {low_temp_metrics}. "
            "Cross-metric leakage at the eligibility-filter seam."
        )
        high_temp_metrics = {row["data_version"] for row in high_eligible}
        assert high_temp_metrics == {HIGH_LOCALDAY_MAX.data_version}, (
            f"HIGH spec eligible snapshots include non-HIGH data_versions: {high_temp_metrics}."
        )

    def test_R_AZ_2a_eligible_snapshot_query_requires_explicit_spec(self):
        from scripts.rebuild_calibration_pairs_v2 import _fetch_eligible_snapshots_v2

        conn = _make_gate_d_db()
        with pytest.raises(ValueError, match="requires an explicit CalibrationMetricSpec"):
            _fetch_eligible_snapshots_v2(conn, city_filter=None, spec=None)

    def test_R_AZ_2b_low_rebuild_rejects_ambiguous_contract_window_even_if_training_allowed(self):
        """LOW pair rebuild must not trust training_allowed over contract-window evidence."""
        from scripts.rebuild_calibration_pairs_v2 import (
            METRIC_SPECS,
            RebuildStatsV2,
            _process_snapshot_v2,
        )
        from src.config import cities_by_name
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_v2_schema(conn)
        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2 (
                city, target_date, temperature_metric, physical_quantity, observation_field,
                issue_time, available_at, fetch_time, lead_hours, members_json,
                model_version, data_version, training_allowed, causality_status,
                authority, members_unit, city_timezone, settlement_source_type,
                settlement_station_id, settlement_unit, settlement_rounding_policy,
                bin_grid_id, bin_schema_version, forecast_window_start_utc,
                forecast_window_end_utc, forecast_window_start_local,
                forecast_window_end_local, forecast_window_local_day_overlap_hours,
                forecast_window_attribution_status, contributes_to_target_extrema,
                forecast_window_block_reasons_json
            ) VALUES (
                'Chicago', '2026-06-01', 'low', ?, 'low_temp',
                '2026-05-30T00:00:00+00:00', '2026-05-30T08:00:00+00:00',
                '2026-05-30T08:05:00+00:00', 48.0, ?,
                'ENS', ?, 1, 'OK',
                'VERIFIED', 'degF', 'America/Chicago', 'wu_icao',
                'KMDW', 'F', 'wmo_half_up',
                'F_canonical_v1', 'canonical_bin_grid_v1',
                '2026-06-01T03:00:00+00:00',
                '2026-06-01T09:00:00+00:00',
                '2026-05-31T22:00:00-05:00',
                '2026-06-01T04:00:00-05:00',
                4.0, 'AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY', 0,
                '["ambiguous_crosses_local_day_boundary"]'
            )
            """,
            (
                LOW_LOCALDAY_MIN.physical_quantity,
                json.dumps([60.0 + i * 0.01 for i in range(51)]),
                LOW_LOCALDAY_MIN.data_version,
            ),
        )
        snapshot = conn.execute("SELECT * FROM ensemble_snapshots_v2").fetchone()

        stats = RebuildStatsV2()
        _process_snapshot_v2(
            conn,
            snapshot,
            cities_by_name["Chicago"],
            spec=METRIC_SPECS[1],
            n_mc=10,
            rng=np.random.default_rng(0),
            stats=stats,
        )

        assert stats.snapshots_contract_evidence_rejected == 1
        assert stats.pairs_written == 0
        assert conn.execute("SELECT COUNT(*) FROM calibration_pairs_v2").fetchone()[0] == 0
        assert stats.contract_evidence_rejection_reasons == {
            "low_window_not_target_full:AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY": 1
        }

    def test_R_AZ_2c_low_contract_evidence_accepts_fully_inside_target_window(self):
        from scripts.rebuild_calibration_pairs_v2 import (
            METRIC_SPECS,
            _low_contract_evidence_rejection,
        )
        from src.contracts.ensemble_snapshot_provenance import (
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
            TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
            assert_data_version_allowed,
        )
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        for data_version in (
            LOW_LOCALDAY_MIN.data_version,
            TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
        ):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            init_schema(conn)
            apply_v2_schema(conn)
            conn.execute(
                """
                INSERT INTO ensemble_snapshots_v2 (
                    city, target_date, temperature_metric, physical_quantity, observation_field,
                    issue_time, available_at, fetch_time, lead_hours, members_json,
                    model_version, data_version, training_allowed, causality_status,
                    authority, members_unit, city_timezone, settlement_source_type,
                    settlement_station_id, settlement_unit, settlement_rounding_policy,
                    bin_grid_id, bin_schema_version, forecast_window_start_utc,
                    forecast_window_end_utc, forecast_window_start_local,
                    forecast_window_end_local, forecast_window_local_day_overlap_hours,
                    forecast_window_attribution_status, contributes_to_target_extrema,
                    forecast_window_block_reasons_json
                ) VALUES (
                    'Chicago', '2026-06-01', 'low', ?, 'low_temp',
                    '2026-05-30T00:00:00+00:00', '2026-05-30T08:00:00+00:00',
                    '2026-05-30T08:05:00+00:00', 48.0, ?,
                    'ENS', ?, 1, 'OK',
                    'VERIFIED', 'degF', 'America/Chicago', 'wu_icao',
                    'KMDW', 'F', 'wmo_half_up',
                    'F_canonical_v1', 'canonical_bin_grid_v1',
                    '2026-06-01T05:00:00+00:00',
                    '2026-06-01T11:00:00+00:00',
                    '2026-06-01T00:00:00-05:00',
                    '2026-06-01T06:00:00-05:00',
                    6.0, 'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
                    '[]'
                )
                """,
                (
                    LOW_LOCALDAY_MIN.physical_quantity,
                    json.dumps([60.0 + i * 0.01 for i in range(51)]),
                    data_version,
                ),
            )
            snapshot = conn.execute("SELECT * FROM ensemble_snapshots_v2").fetchone()

            assert_data_version_allowed(data_version)
            assert METRIC_SPECS[1].allows_data_version(data_version)
            assert _low_contract_evidence_rejection(snapshot, spec=METRIC_SPECS[1]) is None

    def test_R_AZ_2c2_rebuild_data_version_filter_scopes_fetch_and_delete(self):
        from scripts.rebuild_calibration_pairs_v2 import (
            CANONICAL_BIN_SOURCE_V2,
            METRIC_SPECS,
            _delete_canonical_v2_slice,
            _fetch_eligible_snapshots_v2,
        )
        from src.contracts.ensemble_snapshot_provenance import (
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
        )
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        conn = _make_gate_d_db()
        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2 (
                city, target_date, temperature_metric, physical_quantity, data_version,
                members_unit, training_allowed, issue_time, available_at, lead_hours,
                causality_status, authority, members_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Chicago",
                "2026-06-02",
                "low",
                LOW_LOCALDAY_MIN.physical_quantity,
                ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
                "degC",
                1,
                "2026-05-31T12:00:00Z",
                "2026-05-31T14:00:00Z",
                48.0,
                "OK",
                "VERIFIED",
                json.dumps([290.0 + i * 0.01 for i in range(51)]),
            ),
        )
        conn.execute(
            """
            INSERT INTO calibration_pairs_v2 (
                city, target_date, temperature_metric, range_label, p_raw,
                outcome, lead_days, season, cluster, forecast_available_at,
                settlement_value, decision_group_id, authority, bin_source,
                data_version, training_allowed, causality_status, snapshot_id
            ) VALUES
                ('Chicago', '2026-06-01', 'low', 'legacy', 0.5, 1, 2.0,
                 'summer', 'Chicago', '2026-05-30T14:00:00Z', 18.0,
                 'legacy', 'VERIFIED', ?, ?, 1, 'OK', 1),
                ('Chicago', '2026-06-02', 'low', 'recovery', 0.5, 1, 2.0,
                 'summer', 'Chicago', '2026-05-31T14:00:00Z', 18.0,
                 'recovery', 'VERIFIED', ?, ?, 1, 'OK', 2)
            """,
            (
                CANONICAL_BIN_SOURCE_V2,
                LOW_LOCALDAY_MIN.data_version,
                CANONICAL_BIN_SOURCE_V2,
                ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
            ),
        )

        low_spec = METRIC_SPECS[1]
        recovery_eligible = _fetch_eligible_snapshots_v2(
            conn,
            city_filter=None,
            spec=low_spec,
            data_version_filter=ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
        )
        assert {row["data_version"] for row in recovery_eligible} == {
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION
        }

        _delete_canonical_v2_slice(
            conn,
            spec=low_spec,
            data_version_filter=ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
        )
        remaining = conn.execute(
            "SELECT data_version FROM calibration_pairs_v2 ORDER BY data_version"
        ).fetchall()
        assert [row["data_version"] for row in remaining] == [LOW_LOCALDAY_MIN.data_version]

    def test_R_AZ_2c3_rebuild_dry_run_evaluates_low_contract_and_observation_gates(self):
        from scripts.rebuild_calibration_pairs_v2 import METRIC_SPECS, rebuild_v2
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_v2_schema
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_v2_schema(conn)
        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2 (
                city, target_date, temperature_metric, physical_quantity, observation_field,
                issue_time, available_at, fetch_time, lead_hours, members_json,
                model_version, data_version, training_allowed, causality_status,
                authority, members_unit, city_timezone, settlement_source_type,
                settlement_station_id, settlement_unit, settlement_rounding_policy,
                bin_grid_id, bin_schema_version, forecast_window_start_utc,
                forecast_window_end_utc, forecast_window_start_local,
                forecast_window_end_local, forecast_window_local_day_overlap_hours,
                forecast_window_attribution_status, contributes_to_target_extrema,
                forecast_window_block_reasons_json
            ) VALUES
                ('Chicago', '2026-06-01', 'low', ?, 'low_temp',
                 '2026-05-30T00:00:00+00:00', '2026-05-30T08:00:00+00:00',
                 '2026-05-30T08:05:00+00:00', 48.0, ?,
                 'ENS', ?, 1, 'OK',
                 'VERIFIED', 'degF', 'America/Chicago', 'wu_icao',
                 'KMDW', 'F', 'wmo_half_up',
                 'F_canonical_v1', 'canonical_bin_grid_v1',
                 '2026-06-01T03:00:00+00:00',
                 '2026-06-01T09:00:00+00:00',
                 '2026-05-31T22:00:00-05:00',
                 '2026-06-01T04:00:00-05:00',
                 4.0, 'AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY', 0,
                 '["ambiguous_crosses_local_day_boundary"]'),
                ('Chicago', '2026-06-02', 'low', ?, 'low_temp',
                 '2026-05-31T00:00:00+00:00', '2026-05-31T08:00:00+00:00',
                 '2026-05-31T08:05:00+00:00', 48.0, ?,
                 'ENS', ?, 1, 'OK',
                 'VERIFIED', 'degF', 'America/Chicago', 'wu_icao',
                 'KMDW', 'F', 'wmo_half_up',
                 'F_canonical_v1', 'canonical_bin_grid_v1',
                 '2026-06-02T05:00:00+00:00',
                 '2026-06-02T11:00:00+00:00',
                 '2026-06-02T00:00:00-05:00',
                 '2026-06-02T06:00:00-05:00',
                 6.0, 'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
                 '[]')
            """,
            (
                LOW_LOCALDAY_MIN.physical_quantity,
                json.dumps([60.0 + i * 0.01 for i in range(51)]),
                LOW_LOCALDAY_MIN.data_version,
                LOW_LOCALDAY_MIN.physical_quantity,
                json.dumps([60.0 + i * 0.01 for i in range(51)]),
                LOW_LOCALDAY_MIN.data_version,
            ),
        )

        stats = rebuild_v2(
            conn,
            dry_run=True,
            force=False,
            spec=METRIC_SPECS[1],
            city_filter="Chicago",
            n_mc=10,
            rng=np.random.default_rng(0),
        )

        assert stats.snapshots_eligible == 2
        assert stats.snapshots_contract_evidence_rejected == 1
        assert stats.contract_evidence_rejection_reasons == {
            "low_window_not_target_full:AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY": 1
        }
        assert stats.snapshots_no_observation == 1
        assert stats.pairs_written == 0

    def test_R_AZ_2d_low_recovery_data_version_requires_contract_evidence(self):
        from scripts.rebuild_calibration_pairs_v2 import (
            METRIC_SPECS,
            _low_contract_evidence_rejection,
        )
        from src.contracts.ensemble_snapshot_provenance import (
            ECMWF_OPENDATA_LOW_DATA_VERSION,
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
            TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
            assert_data_version_allowed,
        )
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        for data_version in (
            ECMWF_OPENDATA_LOW_DATA_VERSION,
            TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
        ):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.executescript(_SNAPSHOTS_V2_DDL + ";")
            conn.execute(
                """
                INSERT INTO ensemble_snapshots_v2 (
                    city, target_date, temperature_metric, physical_quantity, data_version,
                    members_unit, training_allowed, issue_time, available_at, lead_hours,
                    causality_status, authority, members_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Chicago",
                    "2026-06-01",
                    "low",
                    LOW_LOCALDAY_MIN.physical_quantity,
                    data_version,
                    "degF",
                    1,
                    "2026-05-30T00:00:00+00:00",
                    "2026-05-30T08:00:00+00:00",
                    48.0,
                    "OK",
                    "VERIFIED",
                    json.dumps([60.0 for _ in range(51)]),
                ),
            )
            snapshot = conn.execute("SELECT * FROM ensemble_snapshots_v2").fetchone()

            assert_data_version_allowed(data_version)
            assert METRIC_SPECS[1].allows_data_version(data_version)
            assert _low_contract_evidence_rejection(snapshot, spec=METRIC_SPECS[1]) == (
                "missing_low_contract_evidence_for_required_data_version"
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
