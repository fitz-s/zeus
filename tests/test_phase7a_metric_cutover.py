# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Purpose: Phase 7A R-BH..R-BL invariants: metric-aware rebuild cutover —
#          _delete_canonical_v2_slice metric scoping, _process_snapshot_v2
#          write-time metric identity, rebuild_v2 main() METRIC_SPECS iteration,
#          outer SAVEPOINT atomicity, refit_platt_v2 main() iteration,
#          backfill_tigge_snapshot_p_raw_v2 metric-scoped writes.
# Reuse: Anchors on phase7a_contract.md (commit 9a5ef84) + master plan acceptance
#        criteria (bucket key / query / unique key 都带 metric; high/low 同城同日共存;
#        bin lookup 永不跨 metric union). Fails RED until P7A lands.

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.schema.v2_schema import apply_v2_schema
from src.state.db import init_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    apply_v2_schema(c)
    yield c
    c.close()


def _insert_canonical_pair(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    range_label: str = "80-84",
    bin_source: str = "canonical_v2",
) -> None:
    obs_field = "high_temp" if temperature_metric == "high" else "low_temp"
    data_version = (
        HIGH_LOCALDAY_MAX.data_version
        if temperature_metric == "high"
        else LOW_LOCALDAY_MIN.data_version
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs_v2
        (city, target_date, temperature_metric, observation_field, range_label,
         p_raw, outcome, lead_days, season, cluster, forecast_available_at,
         settlement_value, decision_group_id, bias_corrected, authority,
         bin_source, data_version, training_allowed, causality_status, snapshot_id)
        VALUES (?, ?, ?, ?, ?, 0.1, 1, 1.5, 'summer', 'mid_continental',
                '2026-06-15T12:00:00+00:00', 82.0, 'dgid-test', 0, 'VERIFIED',
                ?, ?, 1, 'OK', NULL)
        """,
        (city, target_date, temperature_metric, obs_field, range_label,
         bin_source, data_version),
    )


# ---------------------------------------------------------------------------
# R-BH: metric-scoped delete + write-time metric identity
# ---------------------------------------------------------------------------

class TestR_BH_DeleteSliceMetricScoped:
    """R-BH: _delete_canonical_v2_slice with spec=HIGH preserves LOW rows."""

    def test_R_BH_1_delete_slice_high_preserves_low(self, conn):
        """_delete_canonical_v2_slice(spec=HIGH_SPEC) must NOT delete LOW canonical_v2 rows."""
        from scripts.rebuild_calibration_pairs_v2 import (
            _delete_canonical_v2_slice, METRIC_SPECS,
        )
        high_spec = METRIC_SPECS[0]
        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15", temperature_metric="high")
        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15", temperature_metric="low")

        _delete_canonical_v2_slice(conn, spec=high_spec)

        remaining_low = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE temperature_metric = 'low'"
        ).fetchone()[0]
        remaining_high = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE temperature_metric = 'high'"
        ).fetchone()[0]

        assert remaining_low == 1, "LOW canonical_v2 row must survive HIGH-scoped delete"
        assert remaining_high == 0, "HIGH canonical_v2 row must be deleted by HIGH-scoped delete"

    def test_R_BH_2_delete_slice_low_preserves_high(self, conn):
        """_delete_canonical_v2_slice(spec=LOW_SPEC) must NOT delete HIGH canonical_v2 rows."""
        from scripts.rebuild_calibration_pairs_v2 import (
            _delete_canonical_v2_slice, METRIC_SPECS,
        )
        low_spec = METRIC_SPECS[1]
        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15", temperature_metric="high")
        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15", temperature_metric="low")

        _delete_canonical_v2_slice(conn, spec=low_spec)

        remaining_high = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE temperature_metric = 'high'"
        ).fetchone()[0]
        remaining_low = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE temperature_metric = 'low'"
        ).fetchone()[0]

        assert remaining_high == 1, "HIGH canonical_v2 row must survive LOW-scoped delete"
        assert remaining_low == 0, "LOW canonical_v2 row must be deleted by LOW-scoped delete"

    def test_R_BH_3_collect_pre_delete_count_metric_scoped(self, conn):
        """_collect_pre_delete_count(spec=HIGH) counts only HIGH canonical_v2 rows."""
        from scripts.rebuild_calibration_pairs_v2 import (
            _collect_pre_delete_count, METRIC_SPECS,
        )
        high_spec = METRIC_SPECS[0]
        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15", temperature_metric="high")
        _insert_canonical_pair(conn, city="NYC", target_date="2026-06-15", temperature_metric="high")
        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15", temperature_metric="low")

        high_count = _collect_pre_delete_count(conn, spec=high_spec)
        low_count = _collect_pre_delete_count(conn, spec=METRIC_SPECS[1])

        assert high_count == 2, f"expected 2 HIGH rows, got {high_count}"
        assert low_count == 1, f"expected 1 LOW row, got {low_count}"


# ---------------------------------------------------------------------------
# R-BI: rebuild_v2 main() iterates METRIC_SPECS (both HIGH + LOW)
# ---------------------------------------------------------------------------

class TestR_BI_MainIteratesMetricSpecs:
    """R-BI: main() / top-level rebuild entry iterates METRIC_SPECS, processing both tracks."""

    def test_R_BI_1_main_iterates_both_specs_in_dry_run(self, conn, capsys):
        """Top-level rebuild_all_v2 (or main) invokes rebuild_v2 for every METRIC_SPEC."""
        from scripts.rebuild_calibration_pairs_v2 import METRIC_SPECS, rebuild_all_v2

        results = rebuild_all_v2(conn, dry_run=True, force=False)

        assert isinstance(results, dict), "rebuild_all_v2 must return per-metric dict of stats"
        keys = set(results.keys())
        expected_keys = {spec.identity.temperature_metric for spec in METRIC_SPECS}
        assert keys == expected_keys, (
            f"rebuild_all_v2 must cover exactly {expected_keys}, got {keys}"
        )
        assert len(results) == 2, "must iterate both HIGH + LOW tracks"

    def test_R_BI_2_rebuild_v2_requires_explicit_spec(self, conn):
        """rebuild_v2 signature must require explicit `spec` (no HIGH default)."""
        import inspect

        from scripts.rebuild_calibration_pairs_v2 import rebuild_v2

        sig = inspect.signature(rebuild_v2)
        spec_param = sig.parameters.get("spec")
        assert spec_param is not None, "rebuild_v2 must accept `spec` kwarg"
        assert spec_param.default is inspect.Parameter.empty, (
            "rebuild_v2 must NOT default `spec` to HIGH — caller iterates METRIC_SPECS explicitly"
        )


# ---------------------------------------------------------------------------
# R-BJ: outer SAVEPOINT atomicity across METRIC_SPECS iteration
# ---------------------------------------------------------------------------

class TestR_BJ_OuterSavepointAtomicity:
    """R-BJ: LOW-side failure rolls back HIGH writes under outer SAVEPOINT."""

    def test_R_BJ_1_low_failure_rolls_back_high(self, conn):
        """If LOW rebuild raises, HIGH writes also roll back — no orphan state."""
        from scripts.rebuild_calibration_pairs_v2 import (
            METRIC_SPECS, rebuild_all_v2,
        )

        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15",
                               temperature_metric="high", bin_source="legacy_keep_me")

        call_log = []

        def fake_rebuild_v2(conn_arg, *, dry_run, force, spec, **kwargs):
            call_log.append(spec.identity.temperature_metric)
            if spec.identity.temperature_metric == "high":
                _insert_canonical_pair(
                    conn_arg, city="Chicago", target_date="2026-06-16",
                    temperature_metric="high",
                )
                from scripts.rebuild_calibration_pairs_v2 import RebuildStatsV2
                return RebuildStatsV2(pairs_written=1, snapshots_processed=1)
            else:
                raise RuntimeError("SIMULATED: LOW-side hard failure, test R-BJ-1")

        with patch("scripts.rebuild_calibration_pairs_v2.rebuild_v2", side_effect=fake_rebuild_v2):
            with pytest.raises(RuntimeError, match="SIMULATED"):
                rebuild_all_v2(conn, dry_run=False, force=True)

        remaining_2026_06_16 = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE target_date = '2026-06-16'"
        ).fetchone()[0]
        assert remaining_2026_06_16 == 0, (
            "HIGH-side write on 2026-06-16 must be rolled back when LOW fails — "
            "outer SAVEPOINT atomicity invariant"
        )

        preserved_legacy = conn.execute(
            "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE bin_source = 'legacy_keep_me'"
        ).fetchone()[0]
        assert preserved_legacy == 1, "unrelated legacy rows must remain untouched"


# ---------------------------------------------------------------------------
# R-BK: refit_platt_v2 main() iterates METRIC_SPECS
# ---------------------------------------------------------------------------

class TestR_BK_RefitPlattIteratesSpecs:
    """R-BK: refit_platt_v2 main() must iterate METRIC_SPECS (both HIGH + LOW)."""

    def test_R_BK_1_main_iterates_both_specs(self, conn):
        """refit_all_v2 (or main) invokes refit_v2 for every METRIC_SPEC."""
        from scripts.refit_platt_v2 import refit_all_v2, METRIC_SPECS

        results = refit_all_v2(conn, dry_run=True, force=False)

        assert isinstance(results, dict), "refit_all_v2 must return per-metric dict"
        keys = set(results.keys())
        expected_keys = {spec.identity.temperature_metric for spec in METRIC_SPECS}
        assert keys == expected_keys, (
            f"refit_all_v2 must cover exactly {expected_keys}, got {keys}"
        )

    def test_R_BK_2_refit_v2_requires_explicit_metric_identity(self):
        """refit_v2 signature must require explicit `metric_identity` (no HIGH default)."""
        import inspect
        from scripts.refit_platt_v2 import refit_v2

        sig = inspect.signature(refit_v2)
        param = sig.parameters.get("metric_identity")
        assert param is not None, "refit_v2 must accept `metric_identity` kwarg"
        assert param.default is inspect.Parameter.empty, (
            "refit_v2 must NOT default `metric_identity` — caller iterates METRIC_SPECS explicitly"
        )


# ---------------------------------------------------------------------------
# R-BL: backfill_tigge_snapshot_p_raw_v2 metric-scoped writes
# ---------------------------------------------------------------------------

class TestR_BL_BackfillMetricScoped:
    """R-BL: backfill_tigge_snapshot_p_raw_v2 writes p_raw only for rows matching spec metric."""

    def test_R_BL_1_backfill_script_exists(self):
        """scripts/backfill_tigge_snapshot_p_raw_v2.py must exist and be importable."""
        from scripts import backfill_tigge_snapshot_p_raw_v2  # noqa: F401

    def test_R_BL_2_backfill_has_metric_specs_iteration(self):
        """backfill_tigge_snapshot_p_raw_v2 must expose backfill_all_v2 or equivalent."""
        import scripts.backfill_tigge_snapshot_p_raw_v2 as mod

        assert hasattr(mod, "backfill_all_v2") or hasattr(mod, "backfill_v2"), (
            "backfill module must expose backfill_all_v2 (METRIC_SPECS iteration) or "
            "backfill_v2 (single-spec, iterated by main)"
        )
        assert hasattr(mod, "METRIC_SPECS"), (
            "backfill module must import/expose METRIC_SPECS for per-spec iteration"
        )

    def test_R_BL_3_backfill_writes_only_spec_metric(self, conn):
        """Synthetic-fixture: backfill for spec=HIGH writes p_raw only on HIGH snapshots."""
        from scripts.backfill_tigge_snapshot_p_raw_v2 import backfill_v2, METRIC_SPECS

        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15",
                               temperature_metric="high", range_label="80-84")
        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15",
                               temperature_metric="low", range_label="60-64")

        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2
            (city, target_date, issue_time, lead_hours, available_at,
             temperature_metric, physical_quantity, observation_field,
             fetch_time, model_version,
             data_version, authority,
             training_allowed, causality_status, members_json, p_raw_json,
             boundary_ambiguous, unit)
            VALUES
            ('Chicago', '2026-06-15', '2026-06-14T00:00:00+00:00', 24, '2026-06-14T06:00:00+00:00',
             'high', 'mx2t6_local_calendar_day_max', 'high_temp',
             '2026-06-14T06:05:00+00:00', 'tigge-ens-51',
             ?, 'VERIFIED', 1, 'OK',
             '[78.0, 80.5, 82.1, 83.2, 84.5]', NULL,
             0, 'F'),
            ('Chicago', '2026-06-15', '2026-06-14T00:00:00+00:00', 24, '2026-06-14T06:00:00+00:00',
             'low', 'mn2t6_local_calendar_day_min', 'low_temp',
             '2026-06-14T06:05:00+00:00', 'tigge-ens-51',
             ?, 'VERIFIED', 1, 'OK',
             '[60.0, 62.0, 63.5, 64.0, 65.2]', NULL,
             0, 'F')
            """,
            (HIGH_LOCALDAY_MAX.data_version, LOW_LOCALDAY_MIN.data_version),
        )

        high_spec = METRIC_SPECS[0]
        backfill_v2(conn, dry_run=False, force=True, spec=high_spec)

        high_row = conn.execute(
            "SELECT p_raw_json FROM ensemble_snapshots_v2 WHERE temperature_metric = 'high'"
        ).fetchone()
        low_row = conn.execute(
            "SELECT p_raw_json FROM ensemble_snapshots_v2 WHERE temperature_metric = 'low'"
        ).fetchone()

        assert high_row["p_raw_json"] is not None, (
            "HIGH snapshot must have p_raw_json populated after HIGH-spec backfill"
        )
        assert low_row["p_raw_json"] is None, (
            "LOW snapshot must NOT be written to during HIGH-spec backfill — "
            "bin lookup 永不跨 metric union"
        )


# ---------------------------------------------------------------------------
# R-BM: _fetch_verified_observation is metric-aware (CRITICAL-1 antibody)
# ---------------------------------------------------------------------------

class TestR_BM_FetchObservationMetricAware:
    """R-BM: _fetch_verified_observation(spec=LOW) reads low_temp column, not high_temp."""

    def _setup_observations(self, conn):
        conn.execute(
            """
            INSERT INTO observations
            (city, target_date, high_temp, low_temp, unit, authority, source)
            VALUES
            ('Chicago', '2026-06-15', 82.0, 60.0, 'F', 'VERIFIED', 'wu'),
            ('NYC', '2026-07-01', 88.0, NULL, 'F', 'VERIFIED', 'wu'),
            ('Boston', '2026-07-01', NULL, 55.0, 'F', 'VERIFIED', 'wu')
            """
        )

    def test_R_BM_1_fetch_observation_high_spec_returns_high_temp(self, conn):
        """spec=HIGH yields observed_value from high_temp column."""
        from scripts.rebuild_calibration_pairs_v2 import (
            _fetch_verified_observation, METRIC_SPECS,
        )
        self._setup_observations(conn)
        high_spec = METRIC_SPECS[0]

        obs = _fetch_verified_observation(conn, "Chicago", "2026-06-15", spec=high_spec)

        assert obs is not None, "HIGH observation must be returned for Chicago 2026-06-15"
        assert obs["observed_value"] == 82.0, (
            f"HIGH-spec fetch must return high_temp=82.0, got {dict(obs)}"
        )

    def test_R_BM_2_fetch_observation_low_spec_returns_low_temp(self, conn):
        """spec=LOW yields observed_value from low_temp column (CRITICAL-1 fix)."""
        from scripts.rebuild_calibration_pairs_v2 import (
            _fetch_verified_observation, METRIC_SPECS,
        )
        self._setup_observations(conn)
        low_spec = METRIC_SPECS[1]

        obs = _fetch_verified_observation(conn, "Chicago", "2026-06-15", spec=low_spec)

        assert obs is not None, "LOW observation must be returned for Chicago 2026-06-15"
        assert obs["observed_value"] == 60.0, (
            f"LOW-spec fetch must return low_temp=60.0, got {dict(obs) if obs else None}"
        )

    def test_R_BM_3_fetch_observation_low_ignores_high_only_rows(self, conn):
        """spec=LOW must NOT return rows where low_temp IS NULL (even if high_temp present)."""
        from scripts.rebuild_calibration_pairs_v2 import (
            _fetch_verified_observation, METRIC_SPECS,
        )
        self._setup_observations(conn)
        low_spec = METRIC_SPECS[1]

        obs = _fetch_verified_observation(conn, "NYC", "2026-07-01", spec=low_spec)

        assert obs is None, (
            "LOW-spec fetch must refuse rows with low_temp IS NULL — no cross-metric "
            "fallback onto high_temp. NYC only has high_temp."
        )


# ---------------------------------------------------------------------------
# R-BN: schema observation_field has NO silent default (MAJOR-1 antibody)
# ---------------------------------------------------------------------------

class TestR_BN_SchemaRefusesMinimalInsert:
    """R-BN: ensemble_snapshots_v2 INSERT without observation_field must raise
    (category-impossibility preserved at SQL seam)."""

    def test_R_BN_1_insert_without_observation_field_raises(self, conn):
        """INSERT with temperature_metric='low' but NO observation_field must fail NOT NULL."""
        with pytest.raises(sqlite3.IntegrityError, match="observation_field"):
            conn.execute(
                """
                INSERT INTO ensemble_snapshots_v2
                (city, target_date, temperature_metric, issue_time, available_at,
                 lead_hours, members_json, data_version, authority, training_allowed,
                 causality_status, physical_quantity, fetch_time, model_version)
                VALUES
                ('NYC', '2026-01-15', 'low',
                 '2026-01-14T00:00:00+00:00', '2026-01-14T06:00:00+00:00', 24,
                 '[5.0, 6.0, 7.0, 8.0, 9.0]',
                 'tigge_mn2t6_local_calendar_day_min_v1',
                 'VERIFIED', 1, 'OK',
                 'mn2t6_local_calendar_day_min',
                 '2026-01-14T06:05:00+00:00', 'tigge-ens-51')
                """
            )

    def test_R_BN_2_insert_without_physical_quantity_raises(self, conn):
        """Same antibody on physical_quantity — no silent default."""
        with pytest.raises(sqlite3.IntegrityError, match="physical_quantity"):
            conn.execute(
                """
                INSERT INTO ensemble_snapshots_v2
                (city, target_date, temperature_metric, issue_time, available_at,
                 lead_hours, members_json, data_version, authority, training_allowed,
                 causality_status, observation_field, fetch_time, model_version)
                VALUES
                ('NYC', '2026-01-15', 'high',
                 '2026-01-14T00:00:00+00:00', '2026-01-14T06:00:00+00:00', 24,
                 '[270.0, 271.0, 272.0]',
                 'tigge_mx2t6_local_calendar_day_max_v1',
                 'VERIFIED', 1, 'OK', 'high_temp',
                 '2026-01-14T06:05:00+00:00', 'tigge-ens-51')
                """
            )


# ---------------------------------------------------------------------------
# R-BO: backfill_v2 enforces assert_data_version_allowed (MAJOR-2 antibody)
# ---------------------------------------------------------------------------

class TestR_BO_BackfillDataVersionContract:
    """R-BO: backfill_v2 must call assert_data_version_allowed before UPDATE."""

    def test_R_BO_1_backfill_rejects_quarantined_data_version(self, conn):
        """Synthetic row with quarantined data_version → backfill raises DataVersionQuarantinedError."""
        from scripts.backfill_tigge_snapshot_p_raw_v2 import backfill_v2, METRIC_SPECS
        from src.contracts.ensemble_snapshot_provenance import DataVersionQuarantinedError

        _insert_canonical_pair(conn, city="Chicago", target_date="2026-06-15",
                               temperature_metric="high", range_label="80-84")
        # Insert snapshot with a NON-canonical data_version (not in _CANONICAL_DATA_VERSIONS)
        conn.execute(
            """
            INSERT INTO ensemble_snapshots_v2
            (city, target_date, issue_time, lead_hours, available_at,
             temperature_metric, physical_quantity, observation_field,
             fetch_time, model_version,
             data_version, authority,
             training_allowed, causality_status, members_json, p_raw_json,
             boundary_ambiguous, unit)
            VALUES
            ('Chicago', '2026-06-15', '2026-06-14T00:00:00+00:00', 24, '2026-06-14T06:00:00+00:00',
             'high', 'mx2t6_local_calendar_day_max', 'high_temp',
             '2026-06-14T06:05:00+00:00', 'tigge-ens-51',
             'tigge_experimental_v99', 'VERIFIED', 1, 'OK',
             '[78.0, 80.5, 82.1, 83.2, 84.5]', NULL,
             0, 'F')
            """
        )

        high_spec = METRIC_SPECS[0]
        with pytest.raises(DataVersionQuarantinedError, match="tigge_experimental_v99"):
            backfill_v2(conn, dry_run=False, force=True, spec=high_spec)

        # Row must not have been updated (rollback / no write)
        row = conn.execute(
            "SELECT p_raw_json FROM ensemble_snapshots_v2 WHERE data_version = 'tigge_experimental_v99'"
        ).fetchone()
        assert row["p_raw_json"] is None, (
            "Backfill must NOT write p_raw to quarantined-data_version rows"
        )
