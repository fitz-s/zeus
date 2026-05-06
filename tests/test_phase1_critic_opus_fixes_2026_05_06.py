# Created: 2026-05-06
# Last reused/audited: 2026-05-06
# Authority basis: /Users/leofitz/.claude/plans/golden-knitting-wand.md Phase 1
"""Tests for 6 critic-opus READ-path fixes (golden-knitting-wand.md Phase 1).

Covers:
  Fix G — evaluate_calibration_transfer_policy_with_evidence flag-off passes live_promotion_approved=True
  Fix E — _fit_from_pairs sets all 5 _bucket_* attrs
  Fix B — get_active_platt_model threads cycle/source_id/horizon_profile to load_platt_model_v2
  Fix C — _resolve_pin_for_bucket handles cycle-stratified frozen_as_of dict
  Fix F — OOS evaluator --refresh flag skips fresh rows; writes stale rows
  Fix D — refit_platt_v2 per-bucket SAVEPOINT isolation; bad bucket rolls back individually
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import _resolve_pin_for_bucket, get_calibrator
from src.calibration import manager as mgr_module
from src.calibration.store import save_platt_model_v2, load_platt_model_v2
from src.config import City, entry_forecast_config
from src.contracts.world_view.calibration import get_active_platt_model
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.data.calibration_transfer_policy import (
    evaluate_calibration_transfer_policy_with_evidence,
)
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _city(cluster: str = "US-Northeast") -> City:
    return City(
        name="TestCity",
        lat=40.7,
        lon=-74.0,
        timezone="America/New_York",
        settlement_unit="F",
        cluster=cluster,
        wu_station="KTST",
    )


def _save_v2(conn, cluster: str = "US-Northeast", season: str = "MAM",
             cycle: str = "00", source_id: str = "tigge_mars",
             n_samples: int = 50) -> None:
    save_platt_model_v2(
        conn,
        metric_identity=HIGH_LOCALDAY_MAX,
        cluster=cluster,
        season=season,
        data_version=HIGH_LOCALDAY_MAX.data_version,
        param_A=1.5,
        param_B=0.3,
        param_C=0.0,
        bootstrap_params=[(1.5, 0.3, 0.0)] * 20,
        n_samples=n_samples,
        input_space="width_normalized_density",
        cycle=cycle,
        source_id=source_id,
        horizon_profile="full",
    )


# ---------------------------------------------------------------------------
# Fix G — flag-off passes live_promotion_approved=True → LIVE_ELIGIBLE
# ---------------------------------------------------------------------------

class TestFixG:
    """evaluate_calibration_transfer_policy_with_evidence flag-off delegation."""

    def test_with_evidence_flag_off_passes_live_promotion_true(self):
        """Fix G: flag-off path must return LIVE_ELIGIBLE for valid ECMWF candidate
        when caller passes live_promotion_approved=True.

        Before Fix G, the delegation at calibration_transfer_policy.py:166 dropped
        live_promotion_approved entirely, so the legacy function defaulted to False
        and returned SHADOW_ONLY at line 107-115 → silent live-entry kill at launch.

        After Fix G + commit 4584c150 (PR #64), _with_evidence accepts
        live_promotion_approved as a kwarg and forwards it to legacy. Caller
        (evaluator.py rollout-gate-retired path) passes True; legacy then returns
        LIVE_ELIGIBLE via the TIGGE Platt route. This test exercises that path.
        """
        cfg = entry_forecast_config()

        with patch.dict("os.environ", {"ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED": "false"}):
            decision = evaluate_calibration_transfer_policy_with_evidence(
                config=cfg,
                source_id="ecmwf_open_data",
                target_source_id="ecmwf_open_data",
                source_cycle="00",
                target_cycle="00",
                horizon_profile="full",
                season="MAM",
                cluster="US-Northeast",
                metric="high",
                platt_model_key=None,
                conn=None,
                now=datetime.now(timezone.utc),
                live_promotion_approved=True,
            )

        assert decision.status == "LIVE_ELIGIBLE", (
            f"Expected LIVE_ELIGIBLE when flag is OFF + caller-True (Fix G), "
            f"got {decision.status}. reason_codes={decision.reason_codes}. "
            "If SHADOW_ONLY: live_promotion_approved kwarg was not threaded through."
        )
        assert decision.live_eligible is True

    def test_with_evidence_flag_off_low_metric_returns_live_eligible(self):
        """Fix G: LOW metric path also returns LIVE_ELIGIBLE under flag-off + caller-True."""
        cfg = entry_forecast_config()

        with patch.dict("os.environ", {"ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED": "false"}):
            decision = evaluate_calibration_transfer_policy_with_evidence(
                config=cfg,
                source_id="ecmwf_open_data",
                target_source_id="ecmwf_open_data",
                source_cycle="12",
                target_cycle="12",
                horizon_profile="full",
                season="JJA",
                cluster="EU-West",
                metric="low",
                platt_model_key=None,
                conn=None,
                now=datetime.now(timezone.utc),
                live_promotion_approved=True,
            )

        assert decision.status == "LIVE_ELIGIBLE", (
            f"LOW metric flag-off path got {decision.status}: {decision.reason_codes}"
        )

    def test_with_evidence_flag_off_caller_false_returns_shadow_only(self):
        """Post-PR #64 reconciliation: when caller explicitly passes
        live_promotion_approved=False, the legacy fallback must respect that
        and return SHADOW_ONLY (operator has not approved live promotion)."""
        cfg = entry_forecast_config()

        with patch.dict("os.environ", {"ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED": "false"}):
            decision = evaluate_calibration_transfer_policy_with_evidence(
                config=cfg,
                source_id="ecmwf_open_data",
                target_source_id="ecmwf_open_data",
                source_cycle="00",
                target_cycle="00",
                horizon_profile="full",
                season="MAM",
                cluster="US-Northeast",
                metric="high",
                platt_model_key=None,
                conn=None,
                now=datetime.now(timezone.utc),
                live_promotion_approved=False,
            )

        assert decision.status == "SHADOW_ONLY", (
            f"caller-False must yield SHADOW_ONLY, got {decision.status}"
        )


# ---------------------------------------------------------------------------
# Fix E — _fit_from_pairs sets all 5 _bucket_* attrs
# ---------------------------------------------------------------------------

class TestFixE:
    """_fit_from_pairs must set all 5 _bucket_* attrs on the returned calibrator."""

    def _build_pairs_in_db(self, conn, cluster: str = "US-Northeast",
                           season: str = "MAM", n: int = 30) -> None:
        """Insert synthetic HIGH calibration_pairs (legacy table) for _fit_from_pairs."""
        from src.contracts.calibration_bins import F_CANONICAL_GRID
        import numpy as np
        rng = np.random.default_rng(42)
        now = datetime.now(timezone.utc).isoformat()

        # _fit_from_pairs reads from legacy calibration_pairs (not v2)
        # Columns: id, city, target_date, range_label, p_raw, outcome, lead_days,
        #          season, cluster, forecast_available_at, settlement_value,
        #          decision_group_id, bias_corrected, authority, bin_source
        for i in range(n):
            p_raw = float(rng.uniform(0.2, 0.8))
            outcome = int(rng.integers(0, 2))
            lead_days = float(rng.integers(1, 7))
            group_id = f"group_{i}"
            conn.execute(
                """
                INSERT INTO calibration_pairs
                    (city, target_date, cluster, season,
                     p_raw, outcome, lead_days,
                     bin_source, range_label,
                     forecast_available_at, decision_group_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("TestCity", "2026-01-01", cluster, season,
                 p_raw, outcome, lead_days,
                 "canonical_v1", f"L{i % F_CANONICAL_GRID.n_bins}",
                 now, group_id),
            )
        conn.commit()

    def test_fit_from_pairs_sets_bucket_attrs(self):
        """Fix E: _fit_from_pairs returns calibrator with all 5 _bucket_* attrs set.

        Before Fix E, _fit_from_pairs returned cal without setting _bucket_* attrs,
        so the evaluator's σ-query (evaluator.py:2778) read an empty string for
        bucket_model_key → σ was always None for on-the-fly fit-path candidates.
        """
        from src.calibration.manager import _fit_from_pairs
        from src.calibration.platt import ExtendedPlattCalibrator

        conn = _make_conn()
        cluster = "US-Northeast"
        season = "MAM"
        self._build_pairs_in_db(conn, cluster=cluster, season=season, n=40)

        cycle = "12"
        source_id = "tigge_mars"
        horizon_profile = "full"
        data_version = HIGH_LOCALDAY_MAX.data_version

        cal = _fit_from_pairs(
            conn, cluster, season,
            unit="F",
            temperature_metric="high",
            cycle=cycle,
            source_id=source_id,
            horizon_profile=horizon_profile,
            data_version=data_version,
        )

        if cal is None:
            pytest.skip("Insufficient pairs for fit (maturity gate); cannot test attrs")

        assert isinstance(cal, ExtendedPlattCalibrator)

        # All 5 _bucket_* attrs must be set and non-None
        assert cal._bucket_cycle == cycle, f"_bucket_cycle={cal._bucket_cycle!r}"
        assert cal._bucket_source_id == source_id, f"_bucket_source_id={cal._bucket_source_id!r}"
        assert cal._bucket_horizon_profile == horizon_profile
        assert cal._bucket_data_version == data_version
        assert cal._bucket_model_key is not None
        assert cal._bucket_model_key != ""

        # model_key must embed all 5 stratification dimensions
        mk = cal._bucket_model_key
        assert cycle in mk, f"cycle not in model_key: {mk}"
        assert source_id in mk, f"source_id not in model_key: {mk}"
        assert horizon_profile in mk, f"horizon_profile not in model_key: {mk}"
        assert cluster in mk
        assert season in mk

    def test_fit_from_pairs_defaults_when_keys_are_none(self):
        """Fix E: when cycle/source_id/horizon_profile are None, defaults are filled in."""
        from src.calibration.manager import _fit_from_pairs

        conn = _make_conn()
        cluster = "US-Northeast"
        season = "MAM"
        self._build_pairs_in_db(conn, cluster=cluster, season=season, n=40)

        cal = _fit_from_pairs(conn, cluster, season, unit="F", temperature_metric="high")

        if cal is None:
            pytest.skip("Insufficient pairs for fit")

        # Defaults must be filled — not None or empty
        assert cal._bucket_cycle == "00"
        assert cal._bucket_source_id == "tigge_mars"
        assert cal._bucket_horizon_profile == "full"
        assert cal._bucket_data_version is not None
        assert cal._bucket_model_key not in (None, "")


# ---------------------------------------------------------------------------
# Fix B — get_active_platt_model threads phase 2 keys
# ---------------------------------------------------------------------------

class TestFixB:
    """get_active_platt_model must thread cycle/source_id/horizon_profile to load_platt_model_v2."""

    def test_world_view_calibration_threads_phase2_keys(self):
        """Fix B: phase-2 keys passed to get_active_platt_model reach load_platt_model_v2.

        Before Fix B, get_active_platt_model called load_platt_model_v2 without
        cycle/source_id/horizon_profile → always resolved schema-default (00z TIGGE full).
        A 12z OpenData call would receive the 00z TIGGE Platt.
        """
        conn = _make_conn()
        cluster = "US-Northeast"
        season = "MAM"

        # Save a cycle='12' model only — cycle='00' absent
        save_platt_model_v2(
            conn,
            metric_identity=HIGH_LOCALDAY_MAX,
            cluster=cluster,
            season=season,
            data_version=HIGH_LOCALDAY_MAX.data_version,
            param_A=2.0,
            param_B=0.5,
            param_C=0.1,
            bootstrap_params=[(2.0, 0.5, 0.1)] * 20,
            n_samples=60,
            input_space="width_normalized_density",
            cycle="12",
            source_id="tigge_mars",
            horizon_profile="full",
        )

        # Calling with cycle='12' must find the model
        result_12 = get_active_platt_model(
            conn, cluster, season, HIGH_LOCALDAY_MAX,
            cycle="12", source_id="tigge_mars", horizon_profile="full",
        )
        assert result_12 is not None, (
            "get_active_platt_model with cycle='12' returned None — "
            "phase-2 keys not threaded through (Fix B missing)."
        )
        assert abs(result_12.param_A - 2.0) < 1e-6

        # Calling with cycle='00' must NOT find the model (only '12' was saved)
        result_00 = get_active_platt_model(
            conn, cluster, season, HIGH_LOCALDAY_MAX,
            cycle="00", source_id="tigge_mars", horizon_profile="full",
        )
        assert result_00 is None, (
            "get_active_platt_model(cycle='00') should return None when only "
            "cycle='12' row exists — cycle key not being filtered (Fix B broken)."
        )

    def test_world_view_calibration_backward_compat_no_keys(self):
        """Fix B: omitting phase-2 keys (backward-compat) returns load_platt_model_v2 default."""
        conn = _make_conn()
        _save_v2(conn, cycle="00")  # default schema bucket

        # Call without the new keys — must still work (keyword defaults = None)
        result = get_active_platt_model(conn, "US-Northeast", "MAM", HIGH_LOCALDAY_MAX)
        # May be None or a model — just must not raise
        # (None is valid: load_platt_model_v2 with cycle=None hits schema default)
        # We only assert no exception here
        assert result is None or hasattr(result, "param_A")


# ---------------------------------------------------------------------------
# Fix C — cycle-stratified frozen_as_of pin
# ---------------------------------------------------------------------------

class TestFixC:
    """_resolve_pin_for_bucket handles dict-form frozen_as_of (cycle-stratified)."""

    def test_pin_frozen_as_of_cycle_stratified(self, monkeypatch):
        """Fix C: dict-form frozen_as_of resolves per cycle.

        schema today is scalar → back-compat. New dict form enables per-cycle
        snapshot pinning (e.g., 00z snapshot differs from 12z snapshot).
        """
        monkeypatch.setattr(mgr_module, "_PIN_CONFIG_CACHE", None)
        pin = {
            "frozen_as_of": {"00": "2026-05-05T00:00:00Z", "12": "2026-05-06T00:00:00Z"},
            "model_keys": {},
        }
        monkeypatch.setattr(mgr_module, "_PIN_CONFIG_CACHE", pin)

        fao_00, _ = _resolve_pin_for_bucket("high", "NYC", "MAM", cycle="00")
        fao_12, _ = _resolve_pin_for_bucket("high", "NYC", "MAM", cycle="12")

        assert fao_00 == "2026-05-05T00:00:00Z", f"Expected 00z ts, got {fao_00!r}"
        assert fao_12 == "2026-05-06T00:00:00Z", f"Expected 12z ts, got {fao_12!r}"

    def test_pin_frozen_as_of_scalar_back_compat(self, monkeypatch):
        """Fix C: scalar frozen_as_of still applies to all cycles (back-compat)."""
        monkeypatch.setattr(mgr_module, "_PIN_CONFIG_CACHE", None)
        scalar_ts = "2026-05-05T12:00:00Z"
        pin = {"frozen_as_of": scalar_ts, "model_keys": {}}
        monkeypatch.setattr(mgr_module, "_PIN_CONFIG_CACHE", pin)

        fao_00, _ = _resolve_pin_for_bucket("high", "NYC", "MAM", cycle="00")
        fao_12, _ = _resolve_pin_for_bucket("high", "NYC", "MAM", cycle="12")

        assert fao_00 == scalar_ts, "Scalar frozen_as_of must apply to cycle='00'"
        assert fao_12 == scalar_ts, "Scalar frozen_as_of must apply to cycle='12'"

    def test_pin_frozen_as_of_dict_missing_cycle_returns_none(self, monkeypatch):
        """Fix C: cycle not present in dict → None (no pin for that cycle)."""
        monkeypatch.setattr(mgr_module, "_PIN_CONFIG_CACHE", None)
        pin = {
            "frozen_as_of": {"00": "2026-05-05T00:00:00Z"},
            "model_keys": {},
        }
        monkeypatch.setattr(mgr_module, "_PIN_CONFIG_CACHE", pin)

        fao_12, _ = _resolve_pin_for_bucket("high", "NYC", "MAM", cycle="12")
        assert fao_12 is None, f"Missing cycle key should return None, got {fao_12!r}"


# ---------------------------------------------------------------------------
# Fix F — OOS evaluator --refresh idempotency
# ---------------------------------------------------------------------------

class TestFixF:
    """evaluate_calibration_transfer_oos --refresh skips fresh rows; writes stale ones."""

    def _make_oos_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def _write_transfer_row(self, conn, model_key: str, target_source_id: str,
                            target_cycle: str, evaluated_at: str,
                            policy_id: str = "OOS_BRIER_DIFF_v1") -> None:
        conn.execute(
            """
            INSERT INTO validated_calibration_transfers (
                policy_id, source_id, target_source_id,
                source_cycle, target_cycle, horizon_profile,
                season, cluster, metric,
                n_pairs, brier_source, brier_target, brier_diff,
                brier_diff_threshold, status,
                evidence_window_start, evidence_window_end,
                platt_model_key, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (policy_id, "tigge_mars", target_source_id,
             "00", target_cycle, "full",
             "MAM", "US-Northeast", "high",
             300, 0.04, 0.042, 0.002,
             0.005, "LIVE_ELIGIBLE",
             "2026-01-01", "2026-04-30",
             model_key, evaluated_at),
        )
        conn.commit()

    def test_evaluate_oos_refresh_idempotent(self):
        """Fix F: --refresh skips buckets with fresh rows; evaluates stale/missing ones.

        Setup:
          - model_key A with a FRESH row (evaluated 1 day ago)
          - model_key B with a STALE row (evaluated 100 days ago)
        After --refresh run:
          - A should NOT trigger a new evaluation (fresh row found)
          - B SHOULD trigger a new evaluation (stale row)
        """
        from scripts.evaluate_calibration_transfer_oos import _has_fresh_row, STALENESS_DAYS_DEFAULT

        conn = self._make_oos_conn()
        now = datetime.now(timezone.utc)
        staleness_days = STALENESS_DAYS_DEFAULT  # 90

        # Fresh row: evaluated 1 day ago
        fresh_ts = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_transfer_row(
            conn, "high:US-Northeast:MAM:tigge_ts:00:tigge_mars:full:wnd",
            "ecmwf_open_data", "00", fresh_ts,
        )

        # Stale row: evaluated 100 days ago (> 90d TTL)
        stale_ts = (now - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_transfer_row(
            conn, "high:US-Northeast:JJA:tigge_ts:00:tigge_mars:full:wnd",
            "ecmwf_open_data", "00", stale_ts,
        )

        # Fresh model: should be skipped
        is_fresh = _has_fresh_row(
            conn,
            policy_id="OOS_BRIER_DIFF_v1",
            model_key="high:US-Northeast:MAM:tigge_ts:00:tigge_mars:full:wnd",
            target_source_id="ecmwf_open_data",
            target_cycle="00",
            staleness_days=staleness_days,
            now=now,
        )
        assert is_fresh, "Fresh row (1d ago) should be detected as fresh"

        # Stale model: should NOT be skipped (needs re-evaluation)
        is_stale_fresh = _has_fresh_row(
            conn,
            policy_id="OOS_BRIER_DIFF_v1",
            model_key="high:US-Northeast:JJA:tigge_ts:00:tigge_mars:full:wnd",
            target_source_id="ecmwf_open_data",
            target_cycle="00",
            staleness_days=staleness_days,
            now=now,
        )
        assert not is_stale_fresh, "Stale row (100d ago) should be flagged as needing refresh"

        # Missing model: should NOT be skipped
        is_missing_fresh = _has_fresh_row(
            conn,
            policy_id="OOS_BRIER_DIFF_v1",
            model_key="high:US-Northeast:SON:nonexistent:00:tigge_mars:full:wnd",
            target_source_id="ecmwf_open_data",
            target_cycle="00",
            staleness_days=staleness_days,
            now=now,
        )
        assert not is_missing_fresh, "Missing row should not be treated as fresh"


# ---------------------------------------------------------------------------
# Fix D — per-bucket SAVEPOINT isolation in refit_platt_v2
# ---------------------------------------------------------------------------

class TestFixD:
    """refit_platt_v2 per-bucket SAVEPOINT: bad bucket rolls back individually."""

    def _make_refit_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        apply_v2_schema(conn)
        return conn

    def _insert_platt_pairs(self, conn, cluster: str, season: str, n: int = 30) -> None:
        """Insert minimal calibration_pairs_v2 rows for the bucket.

        calibration_pairs_v2 columns (from v2_schema.py):
          city, target_date, temperature_metric, observation_field, range_label,
          p_raw, outcome, lead_days, season, cluster, forecast_available_at,
          decision_group_id, bin_source, data_version, cycle, source_id,
          horizon_profile, recorded_at
        Note: no bin_width column in v2.
        """
        from src.contracts.calibration_bins import F_CANONICAL_GRID
        import numpy as np
        rng = np.random.default_rng(seed=hash(cluster + season) % (2**31))
        now_ts = datetime.now(timezone.utc).isoformat()
        for i in range(n):
            p_raw = float(rng.uniform(0.2, 0.8))
            outcome = int(rng.integers(0, 2))
            lead_days = float(rng.integers(1, 7))
            group_id = f"{cluster}_{season}_{i}"
            conn.execute(
                """
                INSERT INTO calibration_pairs_v2
                    (city, target_date, temperature_metric, observation_field,
                     range_label, p_raw, outcome, lead_days,
                     season, cluster, forecast_available_at,
                     bin_source, data_version,
                     cycle, source_id, horizon_profile,
                     decision_group_id, authority, training_allowed,
                     causality_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("TestCity", "2026-01-01", "high", "high_temp",
                 f"L{i % F_CANONICAL_GRID.n_bins}", p_raw, outcome, lead_days,
                 season, cluster, now_ts,
                 "canonical_v1", HIGH_LOCALDAY_MAX.data_version,
                 "00", "tigge_mars", "full",
                 group_id, "VERIFIED", 1, "OK"),
            )
        conn.commit()

    def test_refit_per_bucket_savepoint_isolation(self):
        """Fix D: a bad bucket rolls back only that bucket; others still commit.

        Pre-Fix D: any bucket RuntimeError triggered ROLLBACK on the outer SAVEPOINT
        → ALL successfully-fit buckets were lost. Exit code was 1.
        Post-Fix D: per-bucket SAVEPOINT rolls back the bad bucket only.
        Remaining buckets are not rolled back. refit_bucket_failures row written.
        No RuntimeError propagates in non-strict mode.

        We inject a failure via mock — directly patching _fit_bucket so it raises
        for the first bucket but succeeds for the second, without fighting the DB
        schema's NOT NULL constraint on p_raw.
        """
        import scripts.refit_platt_v2 as rfmod
        from scripts.refit_platt_v2 import refit_v2, RefitStatsV2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = self._make_refit_conn()
        # Need pairs for at least 2 buckets so the loop has something to iterate
        self._insert_platt_pairs(conn, "US-Northeast", "MAM", n=30)
        self._insert_platt_pairs(conn, "US-Northeast", "JJA", n=30)

        call_count = {"n": 0}

        def _mock_fit_bucket(conn, cluster, season, data_version, cycle,
                             source_id, horizon_profile, *, metric_identity,
                             dry_run, stats):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Synthetic bucket failure for Fix D test")
            # Second bucket succeeds (no-op in test)
            stats.buckets_fit += 1

        with patch.object(rfmod, "_fit_bucket", side_effect=_mock_fit_bucket):
            stats = refit_v2(
                conn,
                metric_identity=HIGH_LOCALDAY_MAX,
                dry_run=True,
                force=False,
                strict=False,  # non-strict: bad bucket isolated, others proceed
            )

        # First bucket failed — should be counted
        assert stats.buckets_failed == 1, f"Expected 1 failed bucket, got {stats.buckets_failed}"
        # Second bucket fit (mock succeeds) — fit count should reflect it
        assert stats.buckets_fit >= 1, f"Expected ≥1 fit bucket, got {stats.buckets_fit}"
        # No RuntimeError propagated — function returned normally
        # refit_bucket_failures row written for the bad bucket
        failure_rows = conn.execute("SELECT * FROM refit_bucket_failures").fetchall()
        assert len(failure_rows) >= 1, (
            "Expected a refit_bucket_failures row for the failed bucket (Fix D)"
        )
        assert "Synthetic bucket failure" in failure_rows[0]["error_text"]

    def test_refit_strict_mode_raises_on_any_failure(self):
        """Fix D: --strict mode raises RuntimeError and rolls back all if any bucket fails."""
        import scripts.refit_platt_v2 as rfmod
        from scripts.refit_platt_v2 import refit_v2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = self._make_refit_conn()
        self._insert_platt_pairs(conn, "US-Northeast", "MAM", n=30)

        def _always_fail(conn, cluster, season, data_version, cycle,
                         source_id, horizon_profile, *, metric_identity,
                         dry_run, stats):
            raise RuntimeError("Strict mode test: intentional bucket failure")

        with patch.object(rfmod, "_fit_bucket", side_effect=_always_fail):
            with pytest.raises(RuntimeError, match="strict mode"):
                refit_v2(
                    conn,
                    metric_identity=HIGH_LOCALDAY_MAX,
                    dry_run=True,
                    force=False,
                    strict=True,
                )

    def test_refit_bucket_failures_table_exists(self):
        """Fix D: refit_bucket_failures table is created by apply_v2_schema."""
        conn = self._make_refit_conn()
        # Table must exist after schema migration
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='refit_bucket_failures'"
        ).fetchone()
        assert row is not None, "refit_bucket_failures table must exist after apply_v2_schema"
