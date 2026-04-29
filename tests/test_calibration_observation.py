# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: round3_verdict.md §1 #2 (FOURTH edge packet) + ULTIMATE_PLAN.md §4 #2
# (CALIBRATION_HARDENING — Extended Platt parameter monitoring). Per Fitz "test
# relationships, not just functions" — these tests verify the CROSS-MODULE invariant
# that compute_platt_parameter_snapshot_per_bucket reads list_active_platt_models_v2
# + list_active_platt_models_legacy via the K1-compliant store.py readers (pure SELECT,
# is_active=1 + authority='VERIFIED' filter at source), assembles per-bucket snapshots
# with bootstrap statistics, and applies window/sample-quality classification.
"""BATCH 1 tests for calibration_observation (PATH A bucket-snapshot).

Eleven relationship tests covering:

  store.py read-side additions (3 tests pin the new canonical surface):
  1. test_list_active_platt_models_v2_filters_to_active_verified — UNVERIFIED + is_active=0 excluded
  2. test_list_active_platt_models_legacy_filters_to_active_verified — same filter on legacy
  3. test_list_active_platt_models_v2_pre_migration_returns_empty — graceful on missing table

  calibration_observation projection (8 tests pin the cross-module behavior):
  4. test_empty_db_safety — no models → empty dict
  5. test_legacy_only_snapshot_shape — full snapshot fields including source='legacy'
  6. test_v2_only_snapshot_shape — full snapshot fields including source='v2' + temperature_metric
  7. test_v2_legacy_dedup_v2_wins — same bucket_key in both → v2 entry kept
  8. test_bootstrap_stats_correctness — synthetic 100-bootstrap params → known std/p5/p95 math
  9. test_sample_quality_boundaries — exactly 10/30/100 boundaries (LOW-CAVEAT-EO-2-2 lesson)
  10. test_in_window_flag — fitted_at inside vs outside window
  11. test_unverified_quarantined_inactive_all_excluded — defense in depth: triple filter
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.calibration.store import (
    list_active_platt_models_legacy,
    list_active_platt_models_v2,
    save_platt_model,
    save_platt_model_v2,
)
from src.state.calibration_observation import (
    _stddev,
    _summarize_bootstrap,
    compute_platt_parameter_snapshot_per_bucket,
)
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX


# --- Helpers ---------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _insert_legacy_raw(
    conn: sqlite3.Connection,
    *,
    bucket_key: str,
    A: float = 1.5,
    B: float = 0.3,
    C: float = 0.0,
    n_samples: int = 50,
    is_active: int = 1,
    authority: str = "VERIFIED",
    fitted_at: str | None = None,
):
    """Direct insert into platt_models bypassing save_platt_model (lets us
    set is_active=0 + authority='UNVERIFIED' to test the filter)."""
    if fitted_at is None:
        fitted_at = datetime.now(timezone.utc).isoformat()
    import json
    conn.execute(
        """
        INSERT OR REPLACE INTO platt_models
        (bucket_key, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, brier_insample, fitted_at, is_active, input_space, authority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (bucket_key, A, B, C, json.dumps([(A, B, C)] * 10),
         n_samples, None, fitted_at, is_active, "raw_probability", authority),
    )


def _insert_v2_raw(
    conn: sqlite3.Connection,
    *,
    cluster: str = "TestCity",
    season: str = "DJF",
    data_version: str = "v1",
    input_space: str = "raw_probability",
    A: float = 1.5,
    B: float = 0.3,
    C: float = 0.0,
    n_samples: int = 50,
    is_active: int = 1,
    authority: str = "VERIFIED",
    fitted_at: str | None = None,
    bootstrap_size: int = 10,
):
    """Direct insert into platt_models_v2 bypassing save_platt_model_v2."""
    if fitted_at is None:
        fitted_at = datetime.now(timezone.utc).isoformat()
    import json
    model_key = f"high:{cluster}:{season}:{data_version}:{input_space}"
    conn.execute(
        """
        INSERT INTO platt_models_v2
        (model_key, temperature_metric, cluster, season, data_version,
         input_space, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, brier_insample, fitted_at, is_active, authority, recorded_at)
        VALUES (?, 'high', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (model_key, cluster, season, data_version, input_space,
         A, B, C, json.dumps([(A, B, C)] * bootstrap_size),
         n_samples, None, fitted_at, is_active, authority, fitted_at),
    )


# --- store.py reader tests (3) --------------------------------------------


def test_list_active_platt_models_v2_filters_to_active_verified():
    """RELATIONSHIP: list_active_platt_models_v2 returns only is_active=1 +
    authority='VERIFIED'. Mirrors load_platt_model_v2 read filter (L555-557)."""
    conn = _make_conn()
    _insert_v2_raw(conn, cluster="A", season="DJF", is_active=1, authority="VERIFIED")
    _insert_v2_raw(conn, cluster="B", season="DJF", is_active=0, authority="VERIFIED")
    _insert_v2_raw(conn, cluster="C", season="DJF", is_active=1, authority="UNVERIFIED")
    _insert_v2_raw(conn, cluster="D", season="DJF", is_active=1, authority="QUARANTINED")
    rows = list_active_platt_models_v2(conn)
    clusters = sorted(r["cluster"] for r in rows)
    assert clusters == ["A"], f"only A is active+VERIFIED; got {clusters}"


def test_list_active_platt_models_legacy_filters_to_active_verified():
    """RELATIONSHIP: legacy reader same filter. Mirror load_platt_model L497."""
    conn = _make_conn()
    _insert_legacy_raw(conn, bucket_key="A_DJF", is_active=1, authority="VERIFIED")
    _insert_legacy_raw(conn, bucket_key="B_DJF", is_active=0, authority="VERIFIED")
    _insert_legacy_raw(conn, bucket_key="C_DJF", is_active=1, authority="UNVERIFIED")
    _insert_legacy_raw(conn, bucket_key="D_DJF", is_active=1, authority="QUARANTINED")
    rows = list_active_platt_models_legacy(conn)
    keys = sorted(r["bucket_key"] for r in rows)
    assert keys == ["A_DJF"], f"only A_DJF is active+VERIFIED; got {keys}"


def test_list_active_platt_models_v2_pre_migration_returns_empty():
    """RELATIONSHIP: pre-migration DB without platt_models_v2 → graceful
    empty list, NOT a crash. Mirrors _has_authority_column posture (store.py L197)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Intentionally do NOT call init_schema or apply_v2_schema_idempotent.
    # Both readers should silently return [] on missing table.
    assert list_active_platt_models_v2(conn) == []
    assert list_active_platt_models_legacy(conn) == []


# --- calibration_observation projection tests (8) ------------------------


def test_empty_db_safety():
    """RELATIONSHIP: no Platt models persisted → empty dict, no crash."""
    conn = _make_conn()
    snapshot = compute_platt_parameter_snapshot_per_bucket(conn)
    assert snapshot == {}


def test_legacy_only_snapshot_shape():
    """RELATIONSHIP: legacy-only model surfaces with source='legacy' + full
    field shape contract. v2-only fields (temperature_metric, cluster, season,
    data_version) are None on legacy."""
    conn = _make_conn()
    save_platt_model(conn, "TestCity_DJF", 1.5, 0.3, 0.0,
                     [(1.5, 0.3, 0.0)] * 10, 50)
    snapshot = compute_platt_parameter_snapshot_per_bucket(conn)
    assert "TestCity_DJF" in snapshot
    rec = snapshot["TestCity_DJF"]
    # Required fields per BATCH 1 spec.
    for field_name in ("bucket_key", "source", "param_A", "param_B", "param_C",
                       "n_samples", "brier_insample", "fitted_at", "input_space",
                       "sample_quality", "in_window", "window_start", "window_end",
                       "bootstrap_count", "bootstrap_A_std", "bootstrap_B_std",
                       "bootstrap_C_std", "bootstrap_A_p5", "bootstrap_A_p95",
                       "bootstrap_B_p5", "bootstrap_B_p95", "bootstrap_C_p5",
                       "bootstrap_C_p95", "temperature_metric", "cluster",
                       "season", "data_version"):
        assert field_name in rec, f"missing field {field_name}"
    assert rec["source"] == "legacy"
    assert rec["param_A"] == 1.5
    assert rec["n_samples"] == 50
    assert rec["sample_quality"] == "adequate"  # 30 <= 50 < 100
    # v2-only fields should be None on legacy.
    assert rec["temperature_metric"] is None
    assert rec["cluster"] is None


def test_v2_only_snapshot_shape():
    """RELATIONSHIP: v2 model surfaces with source='v2' + temperature_metric,
    cluster, season, data_version populated."""
    conn = _make_conn()
    save_platt_model_v2(
        conn,
        metric_identity=HIGH_LOCALDAY_MAX,
        cluster="TestCity",
        season="DJF",
        data_version="ecmwf_ens_v3",
        param_A=1.6,
        param_B=0.25,
        param_C=0.05,
        bootstrap_params=[(1.6, 0.25, 0.05)] * 200,
        n_samples=120,
    )
    snapshot = compute_platt_parameter_snapshot_per_bucket(conn)
    # Single v2 entry — key is the v2 model_key string.
    assert len(snapshot) == 1
    rec = next(iter(snapshot.values()))
    assert rec["source"] == "v2"
    assert rec["temperature_metric"] == "high"
    assert rec["cluster"] == "TestCity"
    assert rec["season"] == "DJF"
    assert rec["data_version"] == "ecmwf_ens_v3"
    assert rec["n_samples"] == 120
    assert rec["sample_quality"] == "high"  # >= 100
    assert rec["bootstrap_count"] == 200


def test_v2_legacy_dedup_v2_wins():
    """RELATIONSHIP: when SAME logical bucket appears in both v2 + legacy,
    v2 entry takes precedence (mirrors manager.py L42-62 v2-then-legacy
    fallback pattern). Legacy duplicate is silently skipped."""
    conn = _make_conn()
    # Both use bucket_key 'TestCity_DJF' — but the v2 model_key is
    # 'high:TestCity:DJF:v1:raw_probability', which differs from the legacy
    # 'TestCity_DJF'. So they will have DIFFERENT keys in the result dict.
    # This test pins that the dedup is by KEY EQUALITY, not by logical-
    # bucket-equivalence — that level of dedup is out-of-scope for BATCH 1
    # (would require model_key↔bucket_key bridge logic that lives in
    # manager.py, not here).
    save_platt_model(conn, "TestCity_DJF", 1.5, 0.3, 0.0,
                     [(1.5, 0.3, 0.0)] * 10, 50)
    save_platt_model_v2(
        conn, metric_identity=HIGH_LOCALDAY_MAX, cluster="TestCity",
        season="DJF", data_version="v1", param_A=1.6, param_B=0.25, param_C=0.0,
        bootstrap_params=[(1.6, 0.25, 0.0)] * 10, n_samples=60,
    )
    snapshot = compute_platt_parameter_snapshot_per_bucket(conn)
    # Both entries exist (different keys).
    assert len(snapshot) == 2
    sources = sorted(rec["source"] for rec in snapshot.values())
    assert sources == ["legacy", "v2"]
    # Now simulate true dedup: insert raw legacy with bucket_key MATCHING the
    # v2 model_key string. v2-listed-first should win; legacy duplicate dropped.
    _insert_legacy_raw(conn, bucket_key="high:TestCity:DJF:v1:raw_probability",
                       A=99.0, B=99.0, C=99.0)
    snapshot2 = compute_platt_parameter_snapshot_per_bucket(conn)
    rec_collision = snapshot2["high:TestCity:DJF:v1:raw_probability"]
    # v2 entry has param_A=1.6 (NOT 99.0 from the planted legacy collision).
    assert rec_collision["source"] == "v2"
    assert rec_collision["param_A"] == 1.6


def test_bootstrap_stats_correctness():
    """RELATIONSHIP: synthetic bootstrap params with KNOWN distributions →
    correct std + p5 + p95 percentile bands.

    Setup: 100 bootstrap rows with A_i = i (so A ∈ {0..99}). Population
    stddev of {0..99} = sqrt(sum((i-49.5)^2)/100) = ~28.866. p5 of sorted
    = 4.95 (linear interp at rank 4.95). p95 = 94.05.
    """
    bootstrap = [(float(i), float(i) * 0.5, float(i) * 0.1) for i in range(100)]
    summary = _summarize_bootstrap(bootstrap)
    assert summary["bootstrap_count"] == 100
    # A: 0..99
    assert summary["bootstrap_A_std"] == pytest.approx(28.8661, abs=0.001)
    assert summary["bootstrap_A_p5"] == pytest.approx(4.95, abs=0.01)
    assert summary["bootstrap_A_p95"] == pytest.approx(94.05, abs=0.01)
    # B: 0..49.5 (scaled by 0.5)
    assert summary["bootstrap_B_std"] == pytest.approx(28.8661 * 0.5, abs=0.001)
    # C: 0..9.9 (scaled by 0.1)
    assert summary["bootstrap_C_std"] == pytest.approx(28.8661 * 0.1, abs=0.001)


def test_sample_quality_boundaries():
    """RELATIONSHIP: sample_quality boundaries hold at exactly 10, 30, 100
    (LOW-CAVEAT-EO-2-2 lesson — boundary tests pin strict-vs-inclusive).

    Per src/state/edge_observation.py:53 _classify_sample_quality:
      n < 10 → 'insufficient'
      10 <= n < 30 → 'low'
      30 <= n < 100 → 'adequate'
      n >= 100 → 'high'
    """
    conn = _make_conn()
    for bucket_key, n in [("b9", 9), ("b10", 10), ("b29", 29), ("b30", 30),
                           ("b99", 99), ("b100", 100)]:
        save_platt_model(conn, bucket_key, 1.0, 0.0, 0.0, [(1.0, 0.0, 0.0)], n)
    snapshot = compute_platt_parameter_snapshot_per_bucket(conn)
    assert snapshot["b9"]["sample_quality"] == "insufficient"
    assert snapshot["b10"]["sample_quality"] == "low"
    assert snapshot["b29"]["sample_quality"] == "low"
    assert snapshot["b30"]["sample_quality"] == "adequate"
    assert snapshot["b99"]["sample_quality"] == "adequate"
    assert snapshot["b100"]["sample_quality"] == "high"


def test_in_window_flag():
    """RELATIONSHIP: in_window=True iff fitted_at falls in [end-window_days, end].

    Setup: end_date=2026-04-29, window_days=7 → window [2026-04-22, 2026-04-29].
    Insert 3 models: fitted yesterday (in), fitted 30 days ago (out), fitted today (in).
    """
    conn = _make_conn()
    end = datetime(2026, 4, 29, tzinfo=timezone.utc)
    yesterday = (end - timedelta(days=1)).isoformat()
    in_window_recent = (end - timedelta(hours=2)).isoformat()
    out_of_window = (end - timedelta(days=30)).isoformat()
    _insert_legacy_raw(conn, bucket_key="b_yest", fitted_at=yesterday)
    _insert_legacy_raw(conn, bucket_key="b_today", fitted_at=in_window_recent)
    _insert_legacy_raw(conn, bucket_key="b_old",  fitted_at=out_of_window)
    snapshot = compute_platt_parameter_snapshot_per_bucket(
        conn, window_days=7, end_date="2026-04-29",
    )
    assert snapshot["b_yest"]["in_window"] is True
    assert snapshot["b_today"]["in_window"] is True
    assert snapshot["b_old"]["in_window"] is False


def test_unverified_quarantined_inactive_all_excluded():
    """RELATIONSHIP: defense-in-depth — UNVERIFIED, QUARANTINED, AND is_active=0
    rows ALL excluded from the projection. This pins the upstream-clipping
    invariant documented at module docstring (LOW-NUANCE-WP-2-1 carry-forward).
    """
    conn = _make_conn()
    _insert_legacy_raw(conn, bucket_key="ok",         is_active=1, authority="VERIFIED")
    _insert_legacy_raw(conn, bucket_key="inactive",   is_active=0, authority="VERIFIED")
    _insert_legacy_raw(conn, bucket_key="unverified", is_active=1, authority="UNVERIFIED")
    _insert_legacy_raw(conn, bucket_key="quaran",     is_active=1, authority="QUARANTINED")
    snapshot = compute_platt_parameter_snapshot_per_bucket(conn)
    assert set(snapshot.keys()) == {"ok"}


# --- Unit tests for helpers ------------------------------------------------


def test_stddev_helper_unit():
    """RELATIONSHIP unit: _stddev returns None on empty + single-value;
    returns population (ddof=0) stddev otherwise."""
    assert _stddev([]) is None
    assert _stddev([5.0]) is None  # single value: spread undefined
    # std([0, 2]) = 1.0 (population)
    assert _stddev([0.0, 2.0]) == pytest.approx(1.0)
    # std([1, 2, 3, 4, 5]) = sqrt(2) population
    assert _stddev([1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(2 ** 0.5, abs=1e-6)


def test_summarize_bootstrap_handles_2tuple_legacy():
    """RELATIONSHIP unit: legacy bootstrap rows that are 2-tuples (A, B
    only, no C) are tolerated — A and B stats computed, C stats None."""
    bootstrap_2tup = [(1.0, 0.5)] * 50
    summary = _summarize_bootstrap(bootstrap_2tup)
    assert summary["bootstrap_count"] == 50
    assert summary["bootstrap_A_std"] == pytest.approx(0.0)  # all same
    assert summary["bootstrap_C_std"] is None  # no C values


def test_summarize_bootstrap_empty():
    """RELATIONSHIP unit: empty bootstrap → all stats None, count=0."""
    summary = _summarize_bootstrap([])
    assert summary["bootstrap_count"] == 0
    for ch in ("A", "B", "C"):
        assert summary[f"bootstrap_{ch}_std"] is None
        assert summary[f"bootstrap_{ch}_p5"] is None
        assert summary[f"bootstrap_{ch}_p95"] is None
