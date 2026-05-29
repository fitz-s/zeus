# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P3 "build gate inputs"; CRITIC_statistical S1-S4. The OOS
#   improvement gate must respect: S4 date-blocked folds (no same-date leakage), S3 daily
#   autocorrelation (IID bootstrap is anticonservative -> moving-block bootstrap + AR(1)
#   n_eff), S2 a real bootstrap LCB (none existed; n_bootstrap=0), S1 multiple-comparison
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Statistical correctness tests for OOS gate inputs — date-blocked folds, moving-block bootstrap LCB, AR(1) n_eff, BH-FDR.
# Reuse: Run after any change to oos_gate.py functions (date_blocked_folds, moving_block_bootstrap_lcb, effective_sample_size, bh_fdr_accept).
#   control across the bucket×candidate family (BH-FDR).
"""Statistical correctness of the candidate OOS accept-gate inputs.

These are pure functions; the gate (choose_candidate) consumes their outputs. At the real
n=12-18 depth the LCB is wide and almost nothing clears it — which is the point: a correction
must EARN adoption against autocorrelated, multiple-tested, blocked-OOS evidence, else raw.
"""

from __future__ import annotations

import numpy as np

from src.calibration.oos_gate import (
    bh_fdr_accept,
    date_blocked_folds,
    effective_sample_size,
    moving_block_bootstrap_lcb,
)


# ---- S4: date-blocked folds (no same-date train/test leakage) ----
def test_same_date_lands_in_same_fold():
    dates = ["2026-05-01", "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-01"]
    folds = date_blocked_folds(dates, k=3)
    # all 2026-05-01 records share one fold
    f = {d: fold for d, fold in zip(dates, folds)}
    idx_0501 = [folds[i] for i, d in enumerate(dates) if d == "2026-05-01"]
    assert len(set(idx_0501)) == 1


def test_distinct_dates_can_spread_across_folds():
    dates = [f"2026-05-{d:02d}" for d in range(1, 31)]
    folds = date_blocked_folds(dates, k=5)
    assert len(set(folds)) > 1  # not all in one fold


# ---- S3: AR(1) effective sample size ----
def test_iid_effective_n_near_n():
    rng = np.random.default_rng(0)
    x = rng.normal(size=400).tolist()
    assert effective_sample_size(x) > 300  # iid -> n_eff ~ n


def test_autocorrelated_effective_n_much_smaller():
    rng = np.random.default_rng(1)
    n, rho = 400, 0.8
    x = [0.0]
    for _ in range(n - 1):
        x.append(rho * x[-1] + float(rng.normal()))
    assert effective_sample_size(x) < 120  # strong autocorr deflates n_eff


# ---- S2: moving-block bootstrap LCB of mean improvement ----
def test_clearly_positive_improvement_has_positive_lcb():
    lcb, p = moving_block_bootstrap_lcb([0.5] * 60, seed=0)
    assert lcb > 0
    assert p < 0.05


def test_zero_mean_noise_lcb_not_positive():
    rng = np.random.default_rng(2)
    x = rng.normal(0.0, 1.0, size=60).tolist()
    lcb, p = moving_block_bootstrap_lcb(x, seed=0)
    assert lcb <= 0
    assert p > 0.05


def test_thin_sample_small_mean_cannot_clear_lcb():
    """n=14 with a small positive mean (the real OpenData depth) -> CI too wide -> LCB<=0.
    This is why raw dominates for months."""
    # tiny mean (~0.014) swamped by large day-to-day spread (±0.7) at n=14 -> wide CI -> LCB<=0
    x = [0.6, -0.5, 0.4, -0.7, 0.8, -0.3, 0.5, -0.6, 0.2, -0.4, 0.7, -0.55, 0.3, -0.25]
    lcb, _ = moving_block_bootstrap_lcb(x, seed=0)
    assert lcb <= 0


def test_empty_series_raises():
    import pytest
    with pytest.raises(Exception):
        moving_block_bootstrap_lcb([], seed=0)


# ---- S1: Benjamini-Hochberg FDR across the candidate family ----
def test_bh_fdr_accepts_clear_signal_rejects_nulls():
    pvals = {"a": 0.001, "b": 0.40, "c": 0.55, "d": 0.62, "e": 0.78}
    acc = bh_fdr_accept(pvals, q=0.10)
    assert "a" in acc
    assert "b" not in acc


def test_bh_fdr_all_null_accepts_none():
    pvals = {f"c{i}": 0.5 for i in range(10)}
    assert bh_fdr_accept(pvals, q=0.10) == set()


def test_bh_fdr_is_stricter_than_uncorrected():
    """A p=0.04 that would pass uncorrected α=0.05 must NOT pass when buried in a family of
    nulls (the S1 multiple-comparison defense)."""
    pvals = {"x": 0.04, **{f"n{i}": 0.5 for i in range(20)}}
    assert "x" not in bh_fdr_accept(pvals, q=0.05)
