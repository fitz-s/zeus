# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §3 (causality: previous-runs fixed-lead train ONLY;
#   run_time != source_available_at), §5 (walk-forward, no same-day leak), §7 antibodies
#   ("top-K-uses-target-truth (walk-forward only)", "previous-runs-for-live-decision",
#   "C/F unit mix (settlement-unit residual)"); CONTINUITY_AND_WIRING.md §4 step 4 + IRON
#   RULE #3 (provenance/no-leak). RELATIONSHIP TEST (Fitz: test the cross-module boundary,
#   not the function): the invariant that holds across the raw_model_forecasts ->
#   settlement_outcomes JOIN is what is asserted here, written BEFORE the provider impl.
"""Relationship test for the BAYES_PRECISION_FUSION walk-forward history provider (no-leak JOIN).

The cross-module invariant under test (raw_model_forecasts -> settlement_outcomes):
  (1) NO LEAK: only rows with target_date STRICTLY < decision_date enter the history.
  (2) PROVENANCE GATE: only settlement authority='VERIFIED' rows contribute (UNVERIFIED /
      DISPUTED excluded).
  (3) ENDPOINT GATE: only endpoint='previous_runs' (fixed-lead) rows train; single_runs
      (live-capture, variable-lead) NEVER enter the train window (run_time != available_at).
  (4) UNIT COHERENCE: residual = forecast_value_c - settlement_in_C; an F-settlement city's
      settlement_value (degF) is converted to degC before the residual (no C/F mix).
  (5) FAIL-SOFT: the provider NEVER raises; any failure -> {} (anchor fallback / equal-weight).
  (6) CROSSING MIN_TRAIN: with >=25 VERIFIED previous-runs rows per model strictly before the
      decision date, n_train >= MIN_TRAIN so the fusion can reach T2_BAYES.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from src.forecast.bayes_precision_fusion import MIN_TRAIN
from src.state.schema.v2_schema import (
    apply_canonical_schema,
    ensure_replacement_forecast_live_schema,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    ensure_replacement_forecast_live_schema(conn)
    return conn


def _insert_raw(
    conn: sqlite3.Connection,
    *,
    model: str,
    city: str,
    target_date: str,
    metric: str,
    forecast_value_c: float,
    endpoint: str = "previous_runs",
    lead_days: int = 1,
    source_cycle_time: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model, city, target_date, metric,
            source_cycle_time or (target_date + "T00:00:00+00:00"),
            target_date + "T06:00:00+00:00",
            target_date + "T07:00:00+00:00",
            lead_days, forecast_value_c, endpoint,
        ),
    )


def _insert_settlement(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    settlement_value: float,
    authority: str = "VERIFIED",
    settlement_unit: str = "C",
) -> None:
    conn.execute(
        """
        INSERT INTO settlement_outcomes
            (city, target_date, temperature_metric, settlement_value, authority, settlement_unit)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (city, target_date, metric, settlement_value, authority, settlement_unit),
    )


def _dates(n: int, *, start: date = date(2026, 4, 1)) -> list[str]:
    from datetime import timedelta
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


# =====================================================================================
# (1) NO LEAK — target_date strictly before the decision date only
# =====================================================================================
def test_history_excludes_target_on_or_after_decision_date() -> None:
    from src.data.bayes_precision_fusion_history_provider import BayesPrecisionFusionHistoryProvider

    conn = _conn()
    # Three settled days BEFORE decision, plus one ON and one AFTER the decision date.
    for d in ("2026-04-01", "2026-04-02", "2026-04-03"):
        _insert_raw(conn, model="gfs_global", city="Paris", target_date=d, metric="high", forecast_value_c=20.0)
        _insert_settlement(conn, city="Paris", target_date=d, metric="high", settlement_value=19.0)
    # decision date == 2026-04-04: the 04-04 (==) and 04-05 (>) rows must NOT appear (no leak).
    for d in ("2026-04-04", "2026-04-05"):
        _insert_raw(conn, model="gfs_global", city="Paris", target_date=d, metric="high", forecast_value_c=20.0)
        _insert_settlement(conn, city="Paris", target_date=d, metric="high", settlement_value=19.0)

    provider = BayesPrecisionFusionHistoryProvider(conn)
    hist = provider(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 4, 4), models=["gfs_global"],
    )
    gfs = hist["gfs_global"]
    assert gfs.n_train == 3, "only target_date < decision_date rows may enter (no same-day/future leak)"
    # residual = forecast - settlement = 20.0 - 19.0 = 1.0 on each kept row
    assert all(abs(r - 1.0) < 1e-9 for r in gfs.residuals)


# =====================================================================================
# (2) PROVENANCE GATE — authority must be VERIFIED
# =====================================================================================
def test_history_excludes_non_verified_settlement() -> None:
    from src.data.bayes_precision_fusion_history_provider import BayesPrecisionFusionHistoryProvider

    conn = _conn()
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-01", metric="high", forecast_value_c=20.0)
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=19.0, authority="VERIFIED")
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-02", metric="high", forecast_value_c=20.0)
    _insert_settlement(conn, city="Paris", target_date="2026-04-02", metric="high", settlement_value=19.0, authority="UNVERIFIED")
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-03", metric="high", forecast_value_c=20.0)
    _insert_settlement(conn, city="Paris", target_date="2026-04-03", metric="high", settlement_value=19.0, authority="DISPUTED")

    provider = BayesPrecisionFusionHistoryProvider(conn)
    hist = provider(city="Paris", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global"])
    assert hist["gfs_global"].n_train == 1, "only authority=VERIFIED settlement rows may contribute"


# =====================================================================================
# (3) ENDPOINT GATE — single_runs (live capture) must NOT train
# =====================================================================================
def test_history_excludes_single_runs_endpoint() -> None:
    from src.data.bayes_precision_fusion_history_provider import BayesPrecisionFusionHistoryProvider

    conn = _conn()
    # previous_runs: trains.  single_runs: must be excluded (run_time != source_available_at).
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-01", metric="high", forecast_value_c=20.0, endpoint="previous_runs")
    _insert_settlement(conn, city="Paris", target_date="2026-04-01", metric="high", settlement_value=19.0)
    _insert_raw(conn, model="gfs_global", city="Paris", target_date="2026-04-02", metric="high", forecast_value_c=20.0, endpoint="single_runs")
    _insert_settlement(conn, city="Paris", target_date="2026-04-02", metric="high", settlement_value=19.0)

    provider = BayesPrecisionFusionHistoryProvider(conn)
    hist = provider(city="Paris", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global"])
    assert hist["gfs_global"].n_train == 1, "single_runs rows must never enter the fixed-lead train window"


# =====================================================================================
# (4) UNIT COHERENCE — F-settlement converted to degC before the residual
# =====================================================================================
def test_history_converts_fahrenheit_settlement_to_celsius() -> None:
    from src.data.bayes_precision_fusion_history_provider import BayesPrecisionFusionHistoryProvider

    conn = _conn()
    # forecast_value_c is degC by construction. Settlement stored in degF must be converted.
    # 68F == 20C ; forecast 21C -> residual should be 21 - 20 = +1.0C (NOT 21 - 68).
    _insert_raw(conn, model="gfs_global", city="NewYork", target_date="2026-04-01", metric="high", forecast_value_c=21.0)
    _insert_settlement(conn, city="NewYork", target_date="2026-04-01", metric="high", settlement_value=68.0, settlement_unit="F")

    provider = BayesPrecisionFusionHistoryProvider(conn)
    hist = provider(city="NewYork", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global"])
    gfs = hist["gfs_global"]
    assert gfs.n_train == 1
    assert abs(gfs.residuals[0] - 1.0) < 1e-6, "F settlement must convert to C before the residual (no C/F mix)"


# =====================================================================================
# (5) FAIL-SOFT — provider never raises
# =====================================================================================
def test_provider_never_raises_on_bad_input() -> None:
    from src.data.bayes_precision_fusion_history_provider import BayesPrecisionFusionHistoryProvider

    conn = _conn()
    conn.close()  # a closed connection forces a query error -> must be swallowed to {}
    provider = BayesPrecisionFusionHistoryProvider(conn)
    out = provider(city="Paris", metric="high", lead_days=1, target_date=date(2026, 5, 1), models=["gfs_global"])
    assert out == {}, "provider MUST be fail-soft: any error -> empty mapping (anchor fallback)"


# =====================================================================================
# (6) CROSSING MIN_TRAIN — >=25 verified previous-runs rows -> trustable history
# =====================================================================================
def test_history_crosses_min_train_with_25_verified_rows() -> None:
    from src.data.bayes_precision_fusion_history_provider import BayesPrecisionFusionHistoryProvider

    conn = _conn()
    days = _dates(MIN_TRAIN, start=date(2026, 4, 1))  # 25 days, all strictly < decision
    for i, d in enumerate(days):
        for m in ("ecmwf_ifs", "gfs_global", "icon_global"):
            _insert_raw(conn, model=m, city="Paris", target_date=d, metric="high", forecast_value_c=20.0 + 0.1 * i)
        _insert_settlement(conn, city="Paris", target_date=d, metric="high", settlement_value=19.5 + 0.1 * i)

    provider = BayesPrecisionFusionHistoryProvider(conn)
    hist = provider(
        city="Paris", metric="high", lead_days=1,
        target_date=date(2026, 6, 1), models=["ecmwf_ifs", "gfs_global", "icon_global"],
    )
    for m in ("ecmwf_ifs", "gfs_global", "icon_global"):
        assert hist[m].n_train >= MIN_TRAIN, f"{m} should accrue >= MIN_TRAIN={MIN_TRAIN} verified rows"
    # The anchor's n_train >= MIN_TRAIN is the switch that lets capture set anchor_z/anchor_tau0
    # (bayes_precision_fusion_capture.py:312) so fuse_bayes_precision_posterior reaches T2_BAYES (not EQUAL_WEIGHT).
    assert hist["ecmwf_ifs"].n_train >= MIN_TRAIN
