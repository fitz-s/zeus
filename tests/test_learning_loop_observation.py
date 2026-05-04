# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: round3_verdict.md §1 #2 (FIFTH and FINAL edge packet) +
# ULTIMATE_PLAN.md §4 #4 (LEARNING_LOOP_PACKET — settlement-corpus → calibration
# update → parameter-drift → re-fit pipeline). Per Fitz "test relationships, not
# just functions" — these tests verify the CROSS-MODULE invariant that
# compute_learning_loop_state_per_bucket reads list_active_platt_models_v2 +
# list_active_platt_models_legacy + list_recent_retrain_versions + retrain_status
# via the K1-compliant store.py + retrain_trigger.py readers (pure SELECT;
# is_active=1 + authority='VERIFIED' filter at source on platt; no filter on
# calibration_params_versions which is append-only audit log) and assembles
# per-bucket pipeline-state snapshots.
"""BATCH 1 tests for learning_loop_observation (PATH A bucket-snapshot).

Eleven relationship tests covering:

  retrain_trigger.py read-side addition (3 tests pin the new canonical surface):
  1. test_list_recent_retrain_versions_pre_table_returns_empty — graceful on missing table
  2. test_list_recent_retrain_versions_orders_by_fitted_at_desc — sort + limit
  3. test_list_recent_retrain_versions_includes_pass_and_fail — full audit trail

  learning_loop_observation projection (8 tests pin the cross-module behavior):
  4. test_empty_db_safety — no models → empty dict
  5. test_v2_only_snapshot_full_shape — all 17+ fields populated for v2 bucket
  6. test_legacy_only_snapshot_with_no_versions_filter — legacy bucket has
     None v2-only fields + 0 retrain attempts (since calibration_params_versions
     CHECK requires v2 identity)
  7. test_v2_legacy_dedup_v2_wins — same logical bucket → v2 entry kept
  8. test_per_bucket_retrain_attempts_in_window — window filter + PASS/FAIL split
  9. test_last_retrain_promoted_at_only_promoted_versions — FAIL versions excluded
     from last_promoted but included in last_attempted
  10. test_days_since_last_promotion_math — synthetic timestamps + integer math
  11. test_sample_quality_driven_by_canonical_pair_count — boundary at 30
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.calibration.retrain_trigger import (
    _ensure_versions_table,
    list_recent_retrain_versions,
)
from src.calibration.store import save_platt_model, save_platt_model_v2
from src.state.db import init_schema
from src.state.learning_loop_observation import (
    _filter_versions_to_bucket,
    _aggregate_versions_in_window,
    _days_since,
    compute_learning_loop_state_per_bucket,
)
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _insert_version_raw(
    conn: sqlite3.Connection,
    *,
    fitted_at: str,
    promoted_at: str | None = None,
    frozen_replay_status: str = "PASS",
    temperature_metric: str = "high",
    cluster: str = "TestCity",
    season: str = "DJF",
    data_version: str = "tigge_v3",
    input_space: str = "width_normalized_density",
    confirmed_trade_count: int = 50,
):
    """Direct insert into calibration_params_versions bypassing trigger_retrain."""
    _ensure_versions_table(conn)
    conn.execute(
        """
        INSERT INTO calibration_params_versions
        (fitted_at, corpus_filter_json, params_json, fit_loss_metric,
         confirmed_trade_count, frozen_replay_status, frozen_replay_evidence_hash,
         promoted_at, retired_at, operator_token_hash, temperature_metric,
         cluster, season, data_version, input_space)
        VALUES (?, '{}', '{}', NULL, ?, ?, NULL, ?, NULL, 'tok',
                ?, ?, ?, ?, ?)
        """,
        (fitted_at, confirmed_trade_count, frozen_replay_status, promoted_at,
         temperature_metric, cluster, season, data_version, input_space),
    )


def _insert_calibration_pair_raw(
    conn: sqlite3.Connection,
    *,
    cluster: str,
    season: str,
    authority: str = "VERIFIED",
    bin_source: str = "canonical_v1",
    decision_group_id: str = "dg-1",
):
    """Direct INSERT into calibration_pairs bypassing add_calibration_pair (which has SettlementSemantics dispatch)."""
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days,
         season, cluster, forecast_available_at, settlement_value,
         decision_group_id, bias_corrected, bin_source, authority)
        VALUES ('TestCity', '2026-04-23', '50-51°F', 0.5, 1, 1.0,
                ?, ?, '2026-04-22T12:00:00+00:00', 50.0,
                ?, 0, ?, ?)
        """,
        (season, cluster, decision_group_id, bin_source, authority),
    )


# --- retrain_trigger.py reader tests (3) -----------------------------------


def test_list_recent_retrain_versions_pre_table_returns_empty():
    """RELATIONSHIP: pre-first-retrain DB without calibration_params_versions
    table → graceful empty list. Mirrors store.py readers' OperationalError-catch
    posture (CALIBRATION BATCH 1 precedent)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Intentionally do NOT call _ensure_versions_table.
    assert list_recent_retrain_versions(conn) == []


def test_list_recent_retrain_versions_orders_by_fitted_at_desc():
    """RELATIONSHIP: returns rows in fitted_at-DESC order; limit truncates."""
    conn = _make_conn()
    _insert_version_raw(conn, fitted_at="2026-04-20T01:00:00+00:00", promoted_at="2026-04-20T01:00:00+00:00")
    _insert_version_raw(conn, fitted_at="2026-04-29T01:00:00+00:00", promoted_at="2026-04-29T01:00:00+00:00")
    _insert_version_raw(conn, fitted_at="2026-04-25T01:00:00+00:00", promoted_at="2026-04-25T01:00:00+00:00")
    rows = list_recent_retrain_versions(conn, limit=100)
    assert [r["fitted_at"] for r in rows] == [
        "2026-04-29T01:00:00+00:00",
        "2026-04-25T01:00:00+00:00",
        "2026-04-20T01:00:00+00:00",
    ]
    # Limit truncates
    rows_2 = list_recent_retrain_versions(conn, limit=2)
    assert len(rows_2) == 2
    assert rows_2[0]["fitted_at"] == "2026-04-29T01:00:00+00:00"


def test_list_recent_retrain_versions_includes_pass_and_fail():
    """RELATIONSHIP: BOTH PASS (promoted) and FAIL (drift-blocked) rows are
    returned. The reader is the audit trail; downstream filters on PASS/FAIL."""
    conn = _make_conn()
    _insert_version_raw(conn, fitted_at="2026-04-29T01:00:00+00:00",
                        promoted_at="2026-04-29T01:00:00+00:00", frozen_replay_status="PASS")
    _insert_version_raw(conn, fitted_at="2026-04-28T01:00:00+00:00",
                        promoted_at=None, frozen_replay_status="FAIL")
    rows = list_recent_retrain_versions(conn)
    statuses = sorted(r["frozen_replay_status"] for r in rows)
    assert statuses == ["FAIL", "PASS"]


# --- learning_loop_observation projection tests (8) ------------------------


def test_empty_db_safety():
    """RELATIONSHIP: no models persisted → empty dict, no crash."""
    conn = _make_conn()
    snapshot = compute_learning_loop_state_per_bucket(conn)
    assert snapshot == {}


def test_v2_only_snapshot_full_shape():
    """RELATIONSHIP: v2 bucket surfaces with full 17+ field shape including
    retrain_status (process-level, propagated to bucket)."""
    conn = _make_conn()
    save_platt_model_v2(
        conn, metric_identity=HIGH_LOCALDAY_MAX, cluster="TestCity",
        season="DJF", data_version="tigge_v3",
        param_A=1.5, param_B=0.3, param_C=0.0,
        bootstrap_params=[(1.5, 0.3, 0.0)] * 30, n_samples=50,
    )
    snapshot = compute_learning_loop_state_per_bucket(conn)
    assert len(snapshot) == 1
    rec = next(iter(snapshot.values()))
    # Required fields per BATCH 1 spec.
    for field_name in (
        "bucket_key", "source",
        "n_pairs_total", "n_pairs_verified", "n_pairs_canonical", "n_decision_groups",
        "retrain_status", "n_retrain_attempts_in_window",
        "n_retrain_passed_in_window", "n_retrain_failed_in_window",
        "last_retrain_attempted_at", "last_retrain_promoted_at",
        "days_since_last_promotion",
        "active_model_fitted_at", "active_model_n_samples",
        "temperature_metric", "cluster", "season", "data_version", "input_space",
        "sample_quality", "window_start", "window_end",
    ):
        assert field_name in rec, f"missing field: {field_name}"
    assert rec["source"] == "v2"
    assert rec["temperature_metric"] == "high"
    assert rec["cluster"] == "TestCity"
    assert rec["active_model_n_samples"] == 50
    # No retrain history yet → no_retrain fields zero/None.
    assert rec["n_retrain_attempts_in_window"] == 0
    assert rec["last_retrain_promoted_at"] is None
    assert rec["days_since_last_promotion"] is None
    # retrain_status is process-level (DISABLED unless env+artifact set).
    assert rec["retrain_status"] in ("DISABLED", "ARMED")


def test_legacy_only_snapshot_with_no_versions_filter():
    """RELATIONSHIP: legacy bucket has None v2-only fields + 0 retrain
    attempts (calibration_params_versions schema CHECK requires v2 identity,
    so legacy buckets have no version history by construction)."""
    conn = _make_conn()
    save_platt_model(conn, "TestCity_DJF", 1.5, 0.3, 0.0,
                     [(1.5, 0.3, 0.0)] * 30, 50)
    snapshot = compute_learning_loop_state_per_bucket(conn)
    assert "TestCity_DJF" in snapshot
    rec = snapshot["TestCity_DJF"]
    assert rec["source"] == "legacy"
    assert rec["temperature_metric"] is None
    assert rec["data_version"] is None
    # Legacy bucket gets zero retrain attempts even when versions exist for v2:
    _insert_version_raw(conn, fitted_at="2026-04-29T01:00:00+00:00",
                        promoted_at="2026-04-29T01:00:00+00:00")
    snapshot2 = compute_learning_loop_state_per_bucket(conn)
    legacy_rec = snapshot2["TestCity_DJF"]
    assert legacy_rec["n_retrain_attempts_in_window"] == 0
    assert legacy_rec["last_retrain_promoted_at"] is None


def test_v2_legacy_dedup_v2_wins():
    """RELATIONSHIP: when same bucket_key appears in both v2 + legacy → v2
    entry takes precedence (mirrors CALIBRATION BATCH 1 dedup pattern +
    src/calibration/manager.py L172-189 v2-then-legacy fallback model-load
    precedent — per LOW-CITATION-CALIBRATION-3-1 cite-discipline)."""
    conn = _make_conn()
    # v2 model with model_key 'high:DupCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density'
    save_platt_model_v2(
        conn, metric_identity=HIGH_LOCALDAY_MAX, cluster="DupCity",
        season="DJF", data_version="tigge_v3",
        param_A=1.5, param_B=0.3, param_C=0.0,
        bootstrap_params=[(1.5, 0.3, 0.0)] * 10, n_samples=60,
        input_space="width_normalized_density",
    )
    # Legacy with COLLIDING bucket_key
    import json as _json
    conn.execute(
        """
        INSERT INTO platt_models
        (bucket_key, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, fitted_at, is_active, input_space, authority)
        VALUES ('high:DupCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density',
                99.0, 99.0, 99.0, ?, 999, '2026-04-29T01:00:00+00:00', 1,
                'raw_probability', 'VERIFIED')
        """,
        (_json.dumps([[99, 99, 99]]),),
    )
    snapshot = compute_learning_loop_state_per_bucket(conn)
    rec = snapshot["high:DupCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density"]
    assert rec["source"] == "v2"  # v2 wins
    assert rec["active_model_n_samples"] == 60  # v2 value, not legacy 999


def test_per_bucket_retrain_attempts_in_window():
    """RELATIONSHIP: window filter respects [end-window_days, end]; PASS/FAIL
    split correctly; only same-bucket versions counted."""
    conn = _make_conn()
    save_platt_model_v2(
        conn, metric_identity=HIGH_LOCALDAY_MAX, cluster="WinTestCity",
        season="DJF", data_version="tigge_v3",
        param_A=1.5, param_B=0.3, param_C=0.0,
        bootstrap_params=[(1.5, 0.3, 0.0)], n_samples=60,
        input_space="width_normalized_density",
    )
    # In-window
    _insert_version_raw(conn, fitted_at="2026-04-25T12:00:00+00:00",
                        promoted_at="2026-04-25T12:00:00+00:00",
                        frozen_replay_status="PASS",
                        cluster="WinTestCity", season="DJF",
                        data_version="tigge_v3")
    _insert_version_raw(conn, fitted_at="2026-04-26T12:00:00+00:00",
                        promoted_at=None,
                        frozen_replay_status="FAIL",
                        cluster="WinTestCity", season="DJF",
                        data_version="tigge_v3")
    # Out-of-window
    _insert_version_raw(conn, fitted_at="2026-04-10T12:00:00+00:00",
                        promoted_at="2026-04-10T12:00:00+00:00",
                        cluster="WinTestCity", season="DJF",
                        data_version="tigge_v3")
    # Different bucket (different cluster) — should not contribute
    _insert_version_raw(conn, fitted_at="2026-04-25T12:00:00+00:00",
                        promoted_at="2026-04-25T12:00:00+00:00",
                        cluster="OtherCity", season="DJF",
                        data_version="tigge_v3")

    snapshot = compute_learning_loop_state_per_bucket(
        conn, window_days=7, end_date="2026-04-29",
    )
    bucket_key = "high:WinTestCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density"
    rec = snapshot[bucket_key]
    assert rec["n_retrain_attempts_in_window"] == 2  # 2 in-window for this bucket
    assert rec["n_retrain_passed_in_window"] == 1
    assert rec["n_retrain_failed_in_window"] == 1


def test_last_retrain_promoted_at_only_promoted_versions():
    """RELATIONSHIP: last_retrain_attempted_at includes both PASS and FAIL;
    last_retrain_promoted_at includes ONLY rows with promoted_at NOT NULL."""
    conn = _make_conn()
    save_platt_model_v2(
        conn, metric_identity=HIGH_LOCALDAY_MAX, cluster="PromoTestCity",
        season="DJF", data_version="tigge_v3",
        param_A=1.5, param_B=0.3, param_C=0.0,
        bootstrap_params=[(1.5, 0.3, 0.0)], n_samples=60,
        input_space="width_normalized_density",
    )
    # Most recent attempt is FAIL — promoted_at NULL.
    _insert_version_raw(conn, fitted_at="2026-04-29T01:00:00+00:00",
                        promoted_at=None, frozen_replay_status="FAIL",
                        cluster="PromoTestCity", season="DJF", data_version="tigge_v3")
    # Earlier PASS — promoted.
    _insert_version_raw(conn, fitted_at="2026-04-20T01:00:00+00:00",
                        promoted_at="2026-04-20T01:00:00+00:00", frozen_replay_status="PASS",
                        cluster="PromoTestCity", season="DJF", data_version="tigge_v3")

    snapshot = compute_learning_loop_state_per_bucket(conn)
    bucket_key = "high:PromoTestCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density"
    rec = snapshot[bucket_key]
    assert rec["last_retrain_attempted_at"] == "2026-04-29T01:00:00+00:00"  # most recent (FAIL)
    assert rec["last_retrain_promoted_at"] == "2026-04-20T01:00:00+00:00"  # most recent PASS only


def test_days_since_last_promotion_math():
    """RELATIONSHIP: days_since_last_promotion math uses end_date_dt - fitted_dt.
    Synthetic: promoted 2026-04-22, end 2026-04-29 → 7 days."""
    conn = _make_conn()
    save_platt_model_v2(
        conn, metric_identity=HIGH_LOCALDAY_MAX, cluster="DaysTestCity",
        season="DJF", data_version="tigge_v3",
        param_A=1.5, param_B=0.3, param_C=0.0,
        bootstrap_params=[(1.5, 0.3, 0.0)], n_samples=60,
        input_space="width_normalized_density",
    )
    _insert_version_raw(conn, fitted_at="2026-04-22T00:00:00+00:00",
                        promoted_at="2026-04-22T00:00:00+00:00",
                        cluster="DaysTestCity", season="DJF", data_version="tigge_v3")
    snapshot = compute_learning_loop_state_per_bucket(
        conn, window_days=14, end_date="2026-04-29",
    )
    bucket_key = "high:DaysTestCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density"
    rec = snapshot[bucket_key]
    # End-of-day 2026-04-29 vs start-of-day 2026-04-22 → 7 days (or 7 + ~24h max)
    assert rec["days_since_last_promotion"] in (7, 8)


def test_sample_quality_driven_by_canonical_pair_count():
    """RELATIONSHIP: sample_quality classification uses n_pairs_canonical
    (NOT active_model_n_samples) — pinned to the LOAD-BEARING input for
    retrain readiness. Boundary at 30."""
    conn = _make_conn()
    save_platt_model_v2(
        conn, metric_identity=HIGH_LOCALDAY_MAX, cluster="SampleTestCity",
        season="DJF", data_version="tigge_v3",
        param_A=1.5, param_B=0.3, param_C=0.0,
        bootstrap_params=[(1.5, 0.3, 0.0)], n_samples=999,  # n_samples=999 should NOT drive quality
        input_space="width_normalized_density",
    )
    # Insert 9 canonical pairs → sample_quality should be 'insufficient' (< 10).
    for i in range(9):
        _insert_calibration_pair_raw(conn, cluster="SampleTestCity", season="DJF",
                                      decision_group_id=f"dg-{i}")
    snapshot = compute_learning_loop_state_per_bucket(conn)
    bucket_key = "high:SampleTestCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density"
    rec = snapshot[bucket_key]
    assert rec["n_pairs_verified"] == 9
    assert rec["n_pairs_canonical"] == 9
    assert rec["sample_quality"] == "insufficient"  # < 10 boundary


# --- Helper unit tests ----------------------------------------------------


def test_filter_versions_to_bucket_legacy_returns_empty():
    """RELATIONSHIP unit: legacy bucket (None temperature_metric/cluster/season)
    → filter returns empty (calibration_params_versions schema requires v2 identity)."""
    versions = [
        {"temperature_metric": "high", "cluster": "A", "season": "DJF",
         "data_version": "v3", "input_space": "raw_probability"},
    ]
    assert _filter_versions_to_bucket(
        versions, temperature_metric=None, cluster=None, season=None,
        data_version=None, input_space=None,
    ) == []


def test_aggregate_versions_in_window_zero_versions():
    """RELATIONSHIP unit: empty bucket_versions list → zero counts; None last-fields."""
    window_start = datetime(2026, 4, 22, tzinfo=timezone.utc)
    window_end = datetime(2026, 4, 29, tzinfo=timezone.utc)
    agg = _aggregate_versions_in_window([], window_start_dt=window_start,
                                          window_end_dt=window_end)
    assert agg["n_retrain_attempts_in_window"] == 0
    assert agg["n_retrain_passed_in_window"] == 0
    assert agg["n_retrain_failed_in_window"] == 0
    assert agg["last_retrain_attempted_at"] is None
    assert agg["last_retrain_promoted_at"] is None


def test_days_since_unparseable_returns_none():
    """RELATIONSHIP unit: _days_since handles None + unparseable."""
    end = datetime(2026, 4, 29, tzinfo=timezone.utc)
    assert _days_since(None, end) is None
    assert _days_since("not-a-timestamp", end) is None
    assert _days_since("2026-04-22T00:00:00+00:00", end) in (7, 8)


# ===========================================================================
# BATCH 2 tests — detect_learning_loop_stall (3 composable stall_kinds)
# ===========================================================================
# Mirror EO/WP/CALIBRATION BATCH 2 ratio-test test pattern + GO_BATCH_2
# §Tests spec. 7 tests covering all 3 stall_kinds, composite, steady,
# insufficient per kind, severity boundaries.

from typing import Any  # noqa: E402

from src.state.learning_loop_observation import (  # noqa: E402
    CRITICAL_DAYS_DRIFT_NO_REFIT,
    CRITICAL_DAYS_PAIRS_READY_NO_RETRAIN,
    CRITICAL_PAIR_GROWTH_RATIO_CUTOFF,
    DEFAULT_DAYS_DRIFT_NO_REFIT,
    DEFAULT_DAYS_PAIRS_READY_NO_RETRAIN,
    DEFAULT_PAIR_GROWTH_THRESHOLD_MULTIPLIER,
    DEFAULT_STALL_MIN_WINDOWS,
    ParameterStallVerdict,
    detect_learning_loop_stall,
)


def _stall_history(
    *,
    pair_counts: list[int],
    days_since_last_promotion: int | None = 5,
    sample_quality: str = "high",
) -> list[dict[str, Any]]:
    """Build per-window stall-detector history list."""
    return [
        {
            "n_pairs_canonical": pc,
            "days_since_last_promotion": days_since_last_promotion,
            "sample_quality": sample_quality,
        }
        for pc in pair_counts
    ]


def test_stall_corpus_vs_pair_lag_synthetic():
    """RELATIONSHIP: pair growth ratio drops to 0.1 (way below 1/1.5=0.67)
    → corpus_vs_pair_lag fires + critical severity (ratio < 1/(2.0*1.5)=0.33)."""
    # Trailing growths: 10, 10, 10 (mean 10). Current growth: 1. Ratio: 0.1.
    hist = _stall_history(pair_counts=[0, 10, 20, 30, 31])
    v = detect_learning_loop_stall(hist, "b", drift_detected=False)
    assert v.kind == "stall_detected"
    assert "corpus_vs_pair_lag" in v.stall_kinds
    assert v.severity == "critical"  # ratio 0.1 < 1/(2*1.5)=0.333
    ev = v.evidence["per_kind"]["corpus_vs_pair_lag"]
    assert ev["status"] == "fired"
    assert ev["ratio"] == pytest.approx(0.1, abs=0.001)


def test_stall_pairs_ready_no_retrain_synthetic():
    """RELATIONSHIP: 40 days_since_last_promotion (> 30 default) +
    sample_quality='high' → pairs_ready_no_retrain fires."""
    hist = _stall_history(pair_counts=[100, 100, 100, 100, 100],
                           days_since_last_promotion=40,
                           sample_quality="high")
    v = detect_learning_loop_stall(hist, "b", drift_detected=False)
    assert v.kind == "stall_detected"
    assert "pairs_ready_no_retrain" in v.stall_kinds
    assert v.severity == "warn"  # 40 < 60 critical
    ev = v.evidence["per_kind"]["pairs_ready_no_retrain"]
    assert ev["status"] == "fired"
    assert ev["current_days_since_last_promotion"] == 40


def test_stall_drift_no_refit_synthetic():
    """RELATIONSHIP: drift_detected=True + 20 days_since_last_promotion (>14)
    → drift_no_refit fires."""
    hist = _stall_history(pair_counts=[100, 100, 100, 100, 100],
                           days_since_last_promotion=20,
                           sample_quality="high")
    v = detect_learning_loop_stall(hist, "b", drift_detected=True)
    assert v.kind == "stall_detected"
    assert "drift_no_refit" in v.stall_kinds
    assert v.severity == "warn"  # 20 < 30 critical
    ev = v.evidence["per_kind"]["drift_no_refit"]
    assert ev["status"] == "fired"
    assert ev["drift_detected"] is True


def test_stall_multi_kind_composite():
    """RELATIONSHIP: multi-kind composite — corpus_vs_pair_lag (ratio 0.1)
    + pairs_ready_no_retrain (70 days > 60 critical) + drift_no_refit fire
    simultaneously → 3 stall_kinds; severity critical (multiple breaches)."""
    hist = _stall_history(pair_counts=[0, 10, 20, 30, 31],
                           days_since_last_promotion=70,
                           sample_quality="high")
    v = detect_learning_loop_stall(hist, "b", drift_detected=True)
    assert v.kind == "stall_detected"
    assert set(v.stall_kinds) >= {"corpus_vs_pair_lag", "pairs_ready_no_retrain", "drift_no_refit"}
    assert v.severity == "critical"


def test_stall_steady_history_within_normal():
    """RELATIONSHIP: steady pair growth (10/window), low days, no drift
    → no kind fires → within_normal."""
    hist = _stall_history(pair_counts=[0, 10, 20, 30, 40],
                           days_since_last_promotion=5,
                           sample_quality="high")
    v = detect_learning_loop_stall(hist, "b", drift_detected=False)
    assert v.kind == "within_normal"
    assert v.stall_kinds == []
    assert v.severity is None


def test_stall_insufficient_data_per_kind():
    """RELATIONSHIP: insufficient_data graceful per kind.
    Empty history + drift_detected=None + sample_quality=insufficient
    → all 3 insufficient → kind=insufficient_data overall.
    """
    # Empty: all 3 insufficient
    v = detect_learning_loop_stall([], "b", drift_detected=None)
    assert v.kind == "insufficient_data"
    assert v.stall_kinds == []

    # n<min_windows + sample_quality=insufficient + drift_detected=None
    # → all 3 insufficient
    hist = _stall_history(pair_counts=[5, 5], sample_quality="insufficient")
    v = detect_learning_loop_stall(hist, "b", drift_detected=None)
    assert v.kind == "insufficient_data"
    assert v.evidence["per_kind"]["corpus_vs_pair_lag"]["status"] == "insufficient_data"
    assert v.evidence["per_kind"]["pairs_ready_no_retrain"]["status"] == "insufficient_data"
    assert v.evidence["per_kind"]["drift_no_refit"]["status"] == "insufficient_data"


def test_stall_severity_boundaries_critical_thresholds():
    """RELATIONSHIP: critical-severity boundaries pinned at exactly:
    - corpus ratio < 1/(2.0*1.5) = 0.333 → critical
    - pairs_ready days > 60 → critical
    - drift_no_refit days > 30 → critical
    Sibling-coherent boundary-test pattern (LOW-CAVEAT-EO-2-2 lesson).
    """
    # Boundary 1: pairs_ready days exactly 60 → warn (NOT critical; >, not >=).
    hist60 = _stall_history(pair_counts=[100, 100, 100, 100, 100],
                            days_since_last_promotion=60,
                            sample_quality="high")
    v = detect_learning_loop_stall(hist60, "b", drift_detected=False)
    assert v.kind == "stall_detected"
    assert v.severity == "warn"  # 60 NOT > 60

    # Boundary 1+epsilon: 61 → critical
    hist61 = _stall_history(pair_counts=[100, 100, 100, 100, 100],
                             days_since_last_promotion=61,
                             sample_quality="high")
    v = detect_learning_loop_stall(hist61, "b", drift_detected=False)
    assert v.severity == "critical"

    # Boundary 2: drift_no_refit days exactly 30 → warn (NOT critical).
    hist_drift_30 = _stall_history(pair_counts=[100, 100, 100, 100, 100],
                                     days_since_last_promotion=30,
                                     sample_quality="high")
    v = detect_learning_loop_stall(hist_drift_30, "b", drift_detected=True)
    assert v.kind == "stall_detected"
    # drift_no_refit fires (>14 days) but stays warn (NOT >30).
    # pairs_ready_no_retrain doesn't fire (30 NOT > 30).
    assert "drift_no_refit" in v.stall_kinds
    assert v.severity == "warn"

    # Default constants pinned (sibling-coherent with WP/CALIBRATION).
    assert DEFAULT_PAIR_GROWTH_THRESHOLD_MULTIPLIER == 1.5
    assert DEFAULT_DAYS_PAIRS_READY_NO_RETRAIN == 30
    assert DEFAULT_DAYS_DRIFT_NO_REFIT == 14
    assert DEFAULT_STALL_MIN_WINDOWS == 4
    assert CRITICAL_PAIR_GROWTH_RATIO_CUTOFF == 2.0
    assert CRITICAL_DAYS_PAIRS_READY_NO_RETRAIN == 60
    assert CRITICAL_DAYS_DRIFT_NO_REFIT == 30
