# Created: 2026-05-03
# Last reused/audited: 2026-05-15
# Authority basis: RERUN_PLAN_v2.md §5 D-E (live wiring) + F2 (fail-CLOSED)
# Lifecycle: created=2026-05-03; last_reviewed=2026-05-04; last_reused=2026-05-04
# Purpose: DDD live wiring coverage, including may4math F4 city-timezone window elapsed refinement.
# Reuse: Verify DDD v2 config fixtures and city timezone semantics before relying on these tests.
"""Tests for src.engine.ddd_wiring (live DDD evaluator helper).

Coverage:
- directional_window: ±3 hour bracket, modular over 24
- fetch_directional_coverage: H1-fix semantics (zero rows → cov=0)
- compute_window_elapsed: monotonic clipping to [0, 1]
- fetch_n_platt_samples: returns 0 on miss; n_samples on hit
- evaluate_ddd_for_decision F2 fail-CLOSED paths:
    DDD_CITY_UNCONFIGURED / DDD_NO_TRAIN_DATA / DDD_EXCLUDED_WORKSTREAM_A
- evaluate_ddd_for_decision happy paths: HALT, DISCOUNT, zero-shortfall
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.engine.ddd_wiring import (
    DDDFailClosed,
    compute_window_elapsed,
    directional_window,
    evaluate_ddd_for_decision,
    fetch_directional_coverage,
    fetch_n_platt_samples,
    reset_caches,
)


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_directional_window_basic():
    assert directional_window(15.0) == [12, 13, 14, 15, 16, 17, 18]


def test_directional_window_wraps():
    assert directional_window(2.0) == [23, 0, 1, 2, 3, 4, 5]
    assert directional_window(22.0) == [19, 20, 21, 22, 23, 0, 1]


def test_compute_window_elapsed_before_window_returns_zero():
    # decision_time = day before
    decision = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    elapsed = compute_window_elapsed(
        "2026-05-02", peak_hour=15.0, decision_time=decision
    )
    assert elapsed == 0.0


def test_compute_window_elapsed_after_window_returns_one():
    decision = datetime(2026, 5, 3, 23, 59, tzinfo=timezone.utc)
    elapsed = compute_window_elapsed(
        "2026-05-02", peak_hour=15.0, decision_time=decision
    )
    assert elapsed == 1.0


def test_compute_window_elapsed_midwindow():
    """Window for peak=15, radius=3 → [12, 18] UTC = 6h span. Decision at 15:00
    UTC = 3h elapsed → 3/7 ≈ 0.43 (window length is 2*3+1 = 7 hours)."""
    decision = datetime(2026, 5, 2, 15, 0, tzinfo=timezone.utc)
    elapsed = compute_window_elapsed(
        "2026-05-02", peak_hour=15.0, decision_time=decision
    )
    assert 0.40 <= elapsed <= 0.50


def test_compute_window_elapsed_uses_city_timezone_when_provided():
    decision = datetime(2026, 5, 2, 6, 30, tzinfo=timezone.utc)
    elapsed = compute_window_elapsed(
        "2026-05-02",
        peak_hour=15.0,
        decision_time=decision,
        timezone_name="Asia/Tokyo",
    )

    assert elapsed == pytest.approx(0.5)


# ── DB-backed helpers ────────────────────────────────────────────────────────


@pytest.fixture
def conn():
    """In-memory SQLite seeded with v2 tables and a few rows."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE observation_instants_v2 (
            city TEXT, target_date TEXT, local_hour TEXT,
            source TEXT, data_version TEXT,
            running_max REAL, running_min REAL
        )
    """)
    c.execute("""
        CREATE TABLE platt_models_v2 (
            model_key TEXT PRIMARY KEY, temperature_metric TEXT,
            cluster TEXT, season TEXT, data_version TEXT,
            input_space TEXT DEFAULT 'width_normalized_density',
            n_samples INTEGER, fitted_at TEXT,
            is_active INTEGER DEFAULT 1, authority TEXT DEFAULT 'VERIFIED'
        )
    """)
    yield c
    c.close()


def test_fetch_directional_coverage_full_day(conn):
    # Insert 7 hours covering window 12..18 around peak=15
    for h in range(12, 19):
        conn.execute(
            "INSERT INTO observation_instants_v2 VALUES (?,?,?,?,?,?,?)",
            ("NYC", "2026-05-02", str(h), "wu_icao_history", "v1.wu-native", 25.0, 18.0),
        )
    cov = fetch_directional_coverage(
        conn, "NYC", directional_window(15.0), "2026-05-02"
    )
    assert cov == 1.0


def test_fetch_directional_coverage_partial(conn):
    # 3 hours of 7
    for h in (12, 14, 16):
        conn.execute(
            "INSERT INTO observation_instants_v2 VALUES (?,?,?,?,?,?,?)",
            ("NYC", "2026-05-02", str(h), "wu_icao_history", "v1.wu-native", 25.0, 18.0),
        )
    cov = fetch_directional_coverage(
        conn, "NYC", directional_window(15.0), "2026-05-02"
    )
    assert cov == pytest.approx(3 / 7)


def test_fetch_directional_coverage_zero_rows(conn):
    cov = fetch_directional_coverage(
        conn, "NYC", directional_window(15.0), "2026-05-02"
    )
    assert cov == 0.0


def test_fetch_directional_coverage_filters_by_source(conn):
    # 7 rows from wrong source → cov=0 for canonical source
    for h in range(12, 19):
        conn.execute(
            "INSERT INTO observation_instants_v2 VALUES (?,?,?,?,?,?,?)",
            ("NYC", "2026-05-02", str(h), "openmeteo_archive_hourly", "v1.wu-native", 25.0, 18.0),
        )
    cov = fetch_directional_coverage(
        conn, "NYC", directional_window(15.0), "2026-05-02"
    )
    assert cov == 0.0


def test_fetch_n_platt_samples_returns_n(conn):
    conn.execute(
        """INSERT INTO platt_models_v2
           (model_key, temperature_metric, cluster, season, data_version,
            n_samples, fitted_at)
           VALUES (?,?,?,?,?,?,?)""",
        ("k1", "high", "NYC", "DJF", "tigge_mx2t6_local_calendar_day_max_v1",
         420, "2026-04-01"),
    )
    n = fetch_n_platt_samples(conn, "NYC", "high", "DJF")
    assert n == 420


def test_fetch_n_platt_samples_returns_zero_when_missing(conn):
    n = fetch_n_platt_samples(conn, "NYC", "high", "DJF")
    assert n == 0


# ── F2 fail-CLOSED paths ─────────────────────────────────────────────────────


@pytest.fixture
def floors_and_nstar(tmp_path, monkeypatch):
    """Stand up fake floors + N* configs and point ddd_wiring to them."""
    floors = {
        "_metadata": {"schema_version": "2"},
        "policy_overrides": {"Lagos": 0.45},
        "per_city": {
            "NYC": {
                "p05": 1.0, "recommended_floor_empirical": 1.0,
                "policy_override": None, "final_floor": 1.0,
                "floor_source": "empirical_p05",
                "train_FP_rate": 0.0, "n_zero_train": 0,
                "sigma_diagnostic": 0.0,
            },
            "Lagos": {
                "p05": 0.4286, "recommended_floor_empirical": 0.4286,
                "policy_override": 0.45, "final_floor": 0.45,
                "floor_source": "physical_override",
                "train_FP_rate": 0.06, "n_zero_train": 1,
                "sigma_diagnostic": 0.188,
            },
            "Hong Kong": {
                "status": "NO_TRAIN_DATA",
                "floor_source": "excluded_no_train_data",
            },
            "Paris": {
                "status": "EXCLUDED_WORKSTREAM_A",
                "floor_source": "excluded_workstream_a_pending",
            },
        },
    }
    nstar = {
        "_metadata": {},
        "per_city_metric": {
            "NYC_high": {"city": "NYC", "metric": "high", "N_star": 100, "status": "OK"},
            "Lagos_high": {"city": "Lagos", "metric": "high", "N_star": 100, "status": "OK"},
        },
    }
    fp = tmp_path / "floors.json"
    np = tmp_path / "nstar.json"
    fp.write_text(json.dumps(floors))
    np.write_text(json.dumps(nstar))

    from src.oracle import data_density_discount as ddd_mod
    from src.engine import ddd_wiring as wiring

    monkeypatch.setattr(ddd_mod, "_DEFAULT_FLOORS_PATH", fp)
    monkeypatch.setattr(ddd_mod, "_DEFAULT_NSTAR_PATH", np)
    reset_caches()
    yield
    reset_caches()


def test_fail_closed_no_train_data(conn, floors_and_nstar):
    with pytest.raises(DDDFailClosed) as excinfo:
        evaluate_ddd_for_decision(
            conn=conn,
            city="Hong Kong",
            target_date="2026-05-02",
            metric="high",
            peak_hour=15.0,
            season="MAM",
            mismatch_rate=0.0,
        )
    assert excinfo.value.code == "DDD_NO_TRAIN_DATA"
    assert "Hong Kong" in excinfo.value.reason


def test_fail_closed_missing_nstar_config(conn, tmp_path, monkeypatch):
    floors = {
        "_metadata": {"schema_version": "2"},
        "per_city": {
            "NYC": {
                "final_floor": 1.0,
                "floor_source": "empirical_p05",
            },
        },
    }
    floors_path = tmp_path / "floors.json"
    floors_path.write_text(json.dumps(floors))

    from src.oracle import data_density_discount as ddd_mod
    monkeypatch.setattr(ddd_mod, "_DEFAULT_FLOORS_PATH", floors_path)
    monkeypatch.setattr(ddd_mod, "_DEFAULT_NSTAR_PATH", tmp_path / "missing_nstar.json")
    reset_caches()

    with pytest.raises(DDDFailClosed) as excinfo:
        evaluate_ddd_for_decision(
            conn=conn,
            city="NYC",
            target_date="2026-05-02",
            metric="high",
            peak_hour=15.0,
            season="MAM",
            mismatch_rate=0.0,
        )
    assert excinfo.value.code == "DDD_CONFIG_MISSING"
    assert "N_star config not found" in excinfo.value.reason
    reset_caches()


def test_fail_closed_excluded_workstream_a(conn, floors_and_nstar):
    with pytest.raises(DDDFailClosed) as excinfo:
        evaluate_ddd_for_decision(
            conn=conn,
            city="Paris",
            target_date="2026-05-02",
            metric="high",
            peak_hour=15.0,
            season="MAM",
            mismatch_rate=0.0,
        )
    assert excinfo.value.code == "DDD_EXCLUDED_WORKSTREAM_A"


def test_paris_no_longer_fail_closed_after_workstream_a(tmp_path, monkeypatch):
    """Regression guard: once Paris's status is replaced with an empirical
    floor entry (workstream A complete), DDD must evaluate normally and NOT
    raise DDDFailClosed.

    Authority: 11_paris_resync_log.md (2026-05-03 agent a4c238d864a25ed71).
    Paris transitioned from EXCLUDED_WORKSTREAM_A to empirical_p05.
    """
    floors = {
        "_metadata": {"schema_version": "2"},
        "policy_overrides": {},
        "per_city": {
            "Paris": {
                "p05": 1.0, "p10": 1.0, "p25": 1.0,
                "recommended_floor_empirical": 1.0,
                "policy_override": None, "final_floor": 1.0,
                "floor_source": "empirical_p05",
                "train_FP_rate": 0.0054, "n_zero_train": 0,
                "sigma_diagnostic": 0.0564,
            },
        },
    }
    nstar = {
        "_metadata": {},
        "per_city_metric": {
            "Paris_high": {
                "city": "Paris", "metric": "high",
                "N_star": 110, "status": "POST_WORKSTREAM_A_DEFAULT",
            },
        },
    }
    fp = tmp_path / "floors.json"
    np = tmp_path / "nstar.json"
    fp.write_text(json.dumps(floors))
    np.write_text(json.dumps(nstar))

    from src.oracle import data_density_discount as ddd_mod
    monkeypatch.setattr(ddd_mod, "_DEFAULT_FLOORS_PATH", fp)
    monkeypatch.setattr(ddd_mod, "_DEFAULT_NSTAR_PATH", np)
    reset_caches()

    # Empty conn — Paris has no observations seeded, so cov=0 and Rail 1 fires.
    # The point of THIS test is: it should NOT raise DDDFailClosed.
    c = sqlite3.connect(":memory:")
    c.execute("""
        CREATE TABLE observation_instants_v2 (
            city TEXT, target_date TEXT, local_hour TEXT,
            source TEXT, data_version TEXT,
            running_max REAL, running_min REAL
        )
    """)
    c.execute("""
        CREATE TABLE platt_models_v2 (
            model_key TEXT PRIMARY KEY, temperature_metric TEXT,
            cluster TEXT, season TEXT, data_version TEXT,
            input_space TEXT DEFAULT 'width_normalized_density',
            n_samples INTEGER, fitted_at TEXT,
            is_active INTEGER DEFAULT 1, authority TEXT DEFAULT 'VERIFIED'
        )
    """)
    decision = datetime(2026, 5, 2, 23, 0, tzinfo=timezone.utc)
    # Should NOT raise DDDFailClosed; should return a HALT (cov=0 < 0.35)
    result = evaluate_ddd_for_decision(
        conn=c,
        city="Paris",
        target_date="2026-05-02",
        metric="high",
        peak_hour=14.5,
        season="MAM",
        mismatch_rate=0.0,
        decision_time=decision,
    )
    assert result.action == "HALT"  # no observations seeded → Rail 1
    c.close()
    reset_caches()


def test_fail_closed_unconfigured_city(conn, floors_and_nstar):
    with pytest.raises(DDDFailClosed) as excinfo:
        evaluate_ddd_for_decision(
            conn=conn,
            city="Atlantis",  # not in fixture
            target_date="2026-05-02",
            metric="high",
            peak_hour=15.0,
            season="MAM",
            mismatch_rate=0.0,
        )
    assert excinfo.value.code == "DDD_CITY_UNCONFIGURED"


# ── happy paths ──────────────────────────────────────────────────────────────


def test_rail1_halt_when_cov_zero(conn, floors_and_nstar):
    """No observations at all → cov=0, Rail 1 fires (window_elapsed > 0.5)."""
    decision = datetime(2026, 5, 3, 3, 0, tzinfo=timezone.utc)
    result = evaluate_ddd_for_decision(
        conn=conn,
        city="NYC",
        target_date="2026-05-02",
        metric="high",
        peak_hour=15.0,
        season="MAM",
        mismatch_rate=0.0,
        decision_time=decision,
    )
    assert result.action == "HALT"
    assert result.rail == 1


def test_rail2_discount_when_partial_cov(conn, floors_and_nstar):
    """4/7 coverage on NYC (floor 1.0) → shortfall ≈ 0.43 → discount = 0.09 cap."""
    for h in (12, 13, 14, 15):  # 4 of 7 hours
        conn.execute(
            "INSERT INTO observation_instants_v2 VALUES (?,?,?,?,?,?,?)",
            ("NYC", "2026-05-02", str(h), "wu_icao_history", "v1.wu-native", 25.0, 18.0),
        )
    # Seed a Platt model so n_platt > N*=100 (no small-sample amp)
    conn.execute(
        """INSERT INTO platt_models_v2
           (model_key, temperature_metric, cluster, season, data_version,
            n_samples, fitted_at)
           VALUES (?,?,?,?,?,?,?)""",
        ("k1", "high", "NYC", "MAM", "tigge_mx2t6_local_calendar_day_max_v1",
         500, "2026-04-01"),
    )
    decision = datetime(2026, 5, 2, 23, 0, tzinfo=timezone.utc)
    result = evaluate_ddd_for_decision(
        conn=conn,
        city="NYC",
        target_date="2026-05-02",
        metric="high",
        peak_hour=15.0,
        season="MAM",
        mismatch_rate=0.0,
        decision_time=decision,
    )
    assert result.action == "DISCOUNT"
    assert result.rail == 2
    # cov = 4/7 ≈ 0.571; shortfall = 1.0 - 0.571 = 0.429; D = min(0.09, 0.20 * 0.429) = 0.0857
    assert result.discount == pytest.approx(0.20 * (1.0 - 4/7), abs=1e-3)


def test_rail2_zero_discount_when_full_cov(conn, floors_and_nstar):
    """7/7 coverage → cov=1.0 = floor → shortfall=0, discount=0."""
    for h in range(12, 19):
        conn.execute(
            "INSERT INTO observation_instants_v2 VALUES (?,?,?,?,?,?,?)",
            ("NYC", "2026-05-02", str(h), "wu_icao_history", "v1.wu-native", 25.0, 18.0),
        )
    conn.execute(
        """INSERT INTO platt_models_v2
           (model_key, temperature_metric, cluster, season, data_version,
            n_samples, fitted_at)
           VALUES (?,?,?,?,?,?,?)""",
        ("k1", "high", "NYC", "MAM", "tigge_mx2t6_local_calendar_day_max_v1",
         500, "2026-04-01"),
    )
    decision = datetime(2026, 5, 2, 23, 0, tzinfo=timezone.utc)
    result = evaluate_ddd_for_decision(
        conn=conn,
        city="NYC",
        target_date="2026-05-02",
        metric="high",
        peak_hour=15.0,
        season="MAM",
        mismatch_rate=0.0,
        decision_time=decision,
    )
    assert result.action == "DISCOUNT"
    assert result.discount == pytest.approx(0.0)


def test_lagos_zero_cov_halts(conn, floors_and_nstar):
    """Lagos with no observations → Rail 1 HALT (matches replay finding)."""
    decision = datetime(2026, 5, 3, 3, 0, tzinfo=timezone.utc)
    result = evaluate_ddd_for_decision(
        conn=conn,
        city="Lagos",
        target_date="2026-05-02",
        metric="high",
        peak_hour=14.0,
        season="MAM",
        mismatch_rate=0.0,
        decision_time=decision,
    )
    assert result.action == "HALT"


# ── source-cycle provenance regression (INV-17) ──────────────────────────────


def test_cycle_source_id_stored_in_diagnostic(conn, floors_and_nstar):
    """cycle and source_id passed to evaluate_ddd_for_decision must appear in
    the DDDResult diagnostic dict.  This is the INV-17 audit surface: the
    diagnostic must make the provenance visible so any monitoring tool can
    detect a mismatch between the floor calibration cycle (00z TIGGE) and the
    live forecast cycle (12z OpenData) rather than silently applying the wrong
    density discount.
    """
    # Seed enough coverage to avoid Rail 1 — we want to reach a result at all.
    for h in range(12, 19):
        conn.execute(
            "INSERT INTO observation_instants_v2 VALUES (?,?,?,?,?,?,?)",
            ("NYC", "2026-05-02", str(h), "wu_icao_history", "v1.wu-native", 25.0, 18.0),
        )
    conn.execute(
        """INSERT INTO platt_models_v2
           (model_key, temperature_metric, cluster, season, data_version,
            n_samples, fitted_at)
           VALUES (?,?,?,?,?,?,?)""",
        ("k1", "high", "NYC", "MAM", "tigge_mx2t6_local_calendar_day_max_v1",
         500, "2026-04-01"),
    )
    decision = datetime(2026, 5, 2, 23, 0, tzinfo=timezone.utc)
    result = evaluate_ddd_for_decision(
        conn=conn,
        city="NYC",
        target_date="2026-05-02",
        metric="high",
        peak_hour=15.0,
        season="MAM",
        mismatch_rate=0.0,
        decision_time=decision,
        cycle="12",
        source_id="ecmwf_open_data",
        horizon_profile="full",
    )
    assert result.diagnostic["cycle"] == "12"
    assert result.diagnostic["source_id"] == "ecmwf_open_data"
    assert result.diagnostic["horizon_profile"] == "full"


def test_cycle_mismatch_not_silently_swallowed(conn, floors_and_nstar):
    """A DDD result derived under cycle='00'/source_id='tigge_mars' must NOT
    be served to a forecast with cycle='12'/source_id='ecmwf_open_data'.

    Fail-closed semantics: the diagnostic must carry the LIVE cycle/source_id
    so that any downstream consumer can detect the provenance mismatch.
    Concretely: if a caller evaluates DDD for cycle='00' and then tries to use
    the result for cycle='12', the cycle fields in the two results must differ
    — asserting equality must fail.  This test documents the contract and
    guards against any regression that loses cycle from the diagnostic.
    """
    # Seed full coverage so Rail 1 does not fire on either call.
    for h in range(12, 19):
        conn.execute(
            "INSERT INTO observation_instants_v2 VALUES (?,?,?,?,?,?,?)",
            ("NYC", "2026-05-02", str(h), "wu_icao_history", "v1.wu-native", 25.0, 18.0),
        )
    conn.execute(
        """INSERT INTO platt_models_v2
           (model_key, temperature_metric, cluster, season, data_version,
            n_samples, fitted_at)
           VALUES (?,?,?,?,?,?,?)""",
        ("k1", "high", "NYC", "MAM", "tigge_mx2t6_local_calendar_day_max_v1",
         500, "2026-04-01"),
    )
    decision = datetime(2026, 5, 2, 23, 0, tzinfo=timezone.utc)

    result_00z = evaluate_ddd_for_decision(
        conn=conn,
        city="NYC",
        target_date="2026-05-02",
        metric="high",
        peak_hour=15.0,
        season="MAM",
        mismatch_rate=0.0,
        decision_time=decision,
        cycle="00",
        source_id="tigge_mars",
        horizon_profile="full",
    )
    result_12z = evaluate_ddd_for_decision(
        conn=conn,
        city="NYC",
        target_date="2026-05-02",
        metric="high",
        peak_hour=15.0,
        season="MAM",
        mismatch_rate=0.0,
        decision_time=decision,
        cycle="12",
        source_id="ecmwf_open_data",
        horizon_profile="full",
    )

    # The cycle/source_id in each result must reflect the CALLER's provenance,
    # not a stale cached value — so they must differ.
    assert result_00z.diagnostic["cycle"] != result_12z.diagnostic["cycle"], (
        "DDD silently returned 00z cycle value for a 12z forecast — "
        "INV-17 source-cycle dimension missing from diagnostic"
    )
    assert result_00z.diagnostic["source_id"] != result_12z.diagnostic["source_id"], (
        "DDD silently returned tigge_mars source_id for an ecmwf_open_data forecast — "
        "INV-17 source-cycle dimension missing from diagnostic"
    )
