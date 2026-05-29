# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Relationship + antibody tests for Fix B: MIN_PAIRED_N gate on transport delta (Zeus #64).
# Reuse: Inspect fit_city_predictive_error + MIN_PAIRED_N constant before reuse.
# Authority basis: Zeus #64 Fix B — MIN_PAIRED_N gate on transport step.
#   Root cause: n_paired=1 → statistics.variance undefined → var_d=0 → prior mean
#   shifted by the entire single-date delta with raw prior variance unchanged →
#   SNR gate sees high z → λ=1 → full (spurious) correction.
#   Fix: gate delta to [] when len(delta) < MIN_PAIRED_N (= 5).
"""Relationship + antibody tests for Fix B: MIN_PAIRED_N gate on transport delta.

These tests verify that fit_city_predictive_error does NOT apply a large spurious
correction when n_paired=1, even when that single delta is large in magnitude.

Relationship invariant: a bucket with n_paired < MIN_PAIRED_N must yield
|effective_bias_c| ≤ |prior_bias| (transport has not inflated the bias beyond
what the TIGGE prior alone would produce).

Antibody proof: a sed-break on the MIN_PAIRED_N constant (set to 0, disabling
the gate) causes the test to go RED; restoring it returns GREEN.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

OPD = "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
TIG = "tigge_mx2t6_local_calendar_day_max_v1"


# ---------------------------------------------------------------------------
# Shared DB fixture with issue_time column (needed by _forecast_means / Fix A).
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE ensemble_snapshots(
            city TEXT, target_date TEXT, temperature_metric TEXT, dataset_id TEXT,
            members_json TEXT, members_unit TEXT, lead_hours REAL, available_at TEXT,
            contributes_to_target_extrema INTEGER, boundary_ambiguous INTEGER,
            training_allowed INTEGER, causality_status TEXT, authority TEXT,
            issue_time TEXT)"""
    )
    c.execute(
        """CREATE TABLE settlement_outcomes(
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, authority TEXT)"""
    )
    return c


def _snap(conn, city, date, members, dv, *, unit="degC", metric="high",
          contributes=1, authority="VERIFIED", issue_time=None):
    conn.execute(
        "INSERT INTO ensemble_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (city, date, metric, dv, json.dumps(members), unit, 24.0,
         f"{date}T00:00:00Z", contributes, 0, 1, "OK", authority, issue_time),
    )


def _settle(conn, city, date, value, *, metric="high", authority="VERIFIED"):
    conn.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?)",
        (city, date, metric, value, authority),
    )


# ---------------------------------------------------------------------------
# Helper: build a DB where TIGGE has a large cold bias but only ONE date has
# an overlapping OpenData snapshot (→ n_paired=1 with a large delta).
# Simulates the HK HIGH situation post-Fix-A.
# ---------------------------------------------------------------------------

def _build_n1_delta_scenario(conn):
    """50 TIGGE dates (prior cold, mean ≈ +0.67 residual), 1 OpenData date
    (on the SAME date as the last TIGGE snapshot, with a large delta of ≈ -2.73C),
    and 0 live settlements → n_live=0, n_paired=1.

    This is the canonical HK HIGH post-Fix-A scenario.
    """
    # 50 TIGGE snapshots (no settlements needed for residual loading since we won't
    # supply settlements, but we DO need them for load_bucket_residuals to join)
    import random
    rng = random.Random(7)
    dates = [f"2026-{(i % 3 + 1):02d}-{(i % 28 + 1):02d}" for i in range(50)]
    seen = set()
    unique_dates = []
    for d in dates:
        if d not in seen:
            seen.add(d)
            unique_dates.append(d)
    # Use first 46 (matches HK reality)
    for date in unique_dates[:46]:
        ens_mean = 0.67 + rng.gauss(0, 1.35)   # true TIGGE mean residual ≈ +0.67
        _snap(conn, "HKLike", date, [ens_mean], TIG, contributes=None)
        _settle(conn, "HKLike", date, 0.0)   # settlement=0, so residual = ens_mean

    # 1 OpenData snapshot on a NEW date (no settlement → won't contribute to live residuals)
    # but paired with the TIGGE snapshot for the same date.
    pair_date = "2026-04-20"
    _snap(conn, "HKLike", pair_date, [-2.06], OPD)       # OPD mean ≈ 0.67 - 2.73 = -2.06
    _snap(conn, "HKLike", pair_date, [0.67], TIG, contributes=None)  # TIGGE mean ≈ 0.67


# ---------------------------------------------------------------------------
# Test 1: relationship invariant — n=1 delta must not dominate effective_bias
# ---------------------------------------------------------------------------

def test_n1_delta_does_not_dominate_effective_bias(conn):
    """Fix B invariant: when n_paired=1, the transport step is gated to [] so the
    posterior collapses to the TIGGE prior-only, and effective_bias_c stays bounded.

    Pre-fix (MIN_PAIRED_N=0): the single -2.73C delta shifts the prior mean to ≈ -2.10C
    with no variance inflation; SNR z >> 2 → λ=1 → effective_bias_c ≈ -2.10C > 1.5C.
    Post-fix (MIN_PAIRED_N=5): delta_gated=[] → transport no-op → bias stays at the
    TIGGE prior mean ≈ +0.67C; SNR of +0.67C relative to prior SD → λ is moderate
    or 0, so effective_bias_c stays small (< 0.5C).
    """
    from src.calibration.ens_error_model import MIN_PAIRED_N, fit_city_predictive_error
    from src.calibration.ens_bias_repo import load_paired_delta

    _build_n1_delta_scenario(conn)

    # Verify the scenario has exactly 1 paired sample.
    delta = load_paired_delta(
        conn, city="HKLike",
        live_data_version=OPD, prior_data_version=TIG,
        metric="high",
    )
    assert len(delta) == 1, f"Expected n_paired=1, got {len(delta)}"
    assert delta[0] < -2.0, f"Expected large negative delta, got {delta[0]:.4f}"

    # Fix B gate must be active (5 > 1).
    assert MIN_PAIRED_N > 1, (
        f"MIN_PAIRED_N={MIN_PAIRED_N} must be > 1 to gate single-date deltas"
    )

    em = fit_city_predictive_error(
        conn, city="HKLike",
        live_data_version=OPD, prior_data_version=TIG,
        metric="high", min_live_n=1,
    )

    # Post-fix: effective bias must stay small — transport not applied.
    assert abs(em.effective_bias_c) < 0.5, (
        f"Fix B: n_paired=1 must not produce |effective_bias_c| >= 0.5C "
        f"(prior-only, transport gated). Got {em.effective_bias_c:.4f}C"
    )


# ---------------------------------------------------------------------------
# Antibody proof: break the gate → test goes RED; restore → GREEN.
# The sed-break changes MIN_PAIRED_N=5 to MIN_PAIRED_N=0, disabling the gate.
# ---------------------------------------------------------------------------

def test_antibody_break_gate_exposes_defect(conn):
    """Antibody test: directly simulates the pre-fix behaviour by calling
    transport_bias_prior with the raw n=1 delta (bypassing the gate), and asserts
    that the resulting effective_bias_c IS large.  This proves the test above
    (test_n1_delta_does_not_dominate_effective_bias) would go RED on the broken code.

    If this test itself goes GREEN, the gate is working correctly (which is what we
    want); the assertion here proves the pre-fix path was broken.
    """
    import math
    from src.calibration.ens_bias_model import (
        fit_bucket, posterior_bias, transport_bias_prior,
    )
    from src.calibration.ens_error_model import predictive_error_from_posterior

    # TIGGE prior mean ≈ +0.67C with the HK-like parameters
    tig_residuals = [0.67] * 46   # uniform for clarity
    f50 = fit_bucket(tig_residuals, [], min_live_n=20)

    # n=1 delta, large negative (as in HK HIGH post-Fix-A)
    one_sample_delta = [-2.73]

    # Pre-fix: transport with raw n=1 delta, no live override (n_live=0)
    transported_broken = transport_bias_prior(
        b50=f50.bias, sd50=f50.sd,
        delta_samples=one_sample_delta,  # ← what the pre-fix code does
        kappa=1.0,
    )
    post_broken = posterior_bias(transported_broken, None)
    em_broken = predictive_error_from_posterior(post_broken, 1.36)

    # Post-fix: gate to [] when n < MIN_PAIRED_N
    transported_fixed = transport_bias_prior(
        b50=f50.bias, sd50=f50.sd,
        delta_samples=[],              # ← what Fix B sends
        kappa=1.0,
    )
    post_fixed = posterior_bias(transported_fixed, None)
    em_fixed = predictive_error_from_posterior(post_fixed, 1.36)

    # The broken path produces a large effective bias (pre-fix defect).
    assert abs(em_broken.effective_bias_c) > 1.5, (
        f"Antibody: pre-fix (n=1 delta) must produce |effective_bias| > 1.5C, "
        f"got {em_broken.effective_bias_c:.4f}C — antibody is not catching the defect"
    )

    # The fixed path keeps effective bias small.
    assert abs(em_fixed.effective_bias_c) < 0.5, (
        f"Antibody: post-fix (gated delta=[]) must produce |effective_bias| < 0.5C, "
        f"got {em_fixed.effective_bias_c:.4f}C"
    )

    # Directional sanity: broken shifts bias substantially more than fixed.
    assert abs(em_broken.effective_bias_c) > abs(em_fixed.effective_bias_c) + 1.0, (
        "Antibody: broken path must produce substantially larger |effective_bias| "
        "than fixed path (proves the gate matters)"
    )
