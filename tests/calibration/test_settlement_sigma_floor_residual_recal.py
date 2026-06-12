# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: floor-recalibration 2026-06-10 (/tmp/floor_recal_report.md; /tmp/deep_verify_report.md
#   Verification A). RELATIONSHIP antibody for the σ-floor CATEGORY ERROR: the floor must derive from
#   FORECAST RESIDUALS (fused center vs settlement), NOT the climatological std of settled values.
#   The canonical case-kill: a dataset whose SETTLED VALUES have std≈5°C but whose FORECAST RESIDUALS
#   have dispersion≈1°C must produce a floor≈1°C. Under the OLD (settled-value-std) method it produced
#   ≈5°C — the exact 2.9× over-inflation that suppressed interior edges and inflated the Paris tail.
"""RED-first relationship tests for the forecast-residual settlement σ-floor recalibration.

The cross-module invariant (Module A = the offline floor fit; Module B = the predictive sigma it
floors): the floor must measure the dispersion of (settled − fused_center), so that flooring a
residual-calibrated predictive sigma is self-consistent. A floor that measures the climatological
spread of the settled values is a category error (its source semantics ≠ the quantity it floors).

These tests make the category error UNCONSTRUCTIBLE: feed a fixture where settled-value std and
forecast-residual dispersion DIVERGE by 5× and assert the fit follows the RESIDUAL.
"""
from __future__ import annotations

import importlib

import numpy as np
import pytest

script = importlib.import_module("scripts.fit_settlement_sigma_floor")


# ----------------------------------------------------------------------------
# THE CATEGORY KILL — residual dispersion, not settled-value dispersion.
# ----------------------------------------------------------------------------
def test_floor_follows_forecast_residual_not_settled_value_std():
    """Settled values span std≈5°C; forecast residuals are tight (≈1°C). Floor must be ≈1, not ≈5.

    Construct a city×metric cohort large enough to key TIER 1 (n ≥ MIN_COHORT_N). The settled values
    march from 18→30°C (a wide climatological spread, std≈3.6) but the fused center TRACKS each
    settlement to within ±1°C, so the forecast residual dispersion is ≈1°C. The OLD method (detrended
    std of SETTLED VALUES) would have returned ~3.6°C; the residual method must return ≈1°C.
    """
    rng = np.random.default_rng(0)
    n = 40
    settled = np.linspace(18.0, 30.0, n)        # wide climatological spread
    resid = rng.normal(0.0, 0.7, size=n)        # TIGHT forecast residuals (~0.7°C)
    center = settled - resid                     # center = settled − residual
    # raw residuals fed to the fitter (settled − center) == resid
    residuals = [("Testville", "high", _td(i), float(settled[i] - center[i])) for i in range(n)]

    settled_std = float(np.std(settled, ddof=1))
    assert settled_std > 3.0, "fixture: settled values must have a WIDE climatological spread"

    cells = script.fit_floors(residuals, min_cohort_n=20)
    cell = cells["Testville|JJA|high"]
    assert cell["cohort_tier"] == "city_metric", "n=40 ≥ 20 must key TIER 1 (city×metric)"
    # The floor must track the RESIDUAL dispersion (~0.7→clipped to ABSOLUTE_FLOOR 1.0), NOT the
    # settled-value std (~3.6). Anything near the settled std is the category error.
    assert cell["sigma_floor_c"] < 1.6, (
        f"floor {cell['sigma_floor_c']} must follow the ~0.7°C residual, not the {settled_std:.1f}°C "
        "settled-value std (category error)"
    )
    assert cell["estimator"] == "mad_sigma_about_zero"


def test_floor_includes_systematic_bias_about_zero():
    """A persistent forecast bias must INFLATE the floor (MAD measured about ZERO, not the mean).

    If the center is systematically COLD by +2°C with tiny noise, the residual mean is +2 and the
    spread-about-the-mean is ~0. A bias-blind std would report ~0; the floor must instead report the
    ~2°C total miss (overconfidence insurance must include systematic bias).
    """
    n = 40
    residuals = [("BiasCity", "high", _td(i), 2.0 + (0.01 * (i % 3 - 1))) for i in range(n)]
    cells = script.fit_floors(residuals, min_cohort_n=20)
    cell = cells["BiasCity|JJA|high"]
    assert cell["sigma_floor_c"] == pytest.approx(2.0 * script.MAD_TO_SIGMA, abs=0.1), (
        "floor must capture the +2°C systematic bias (MAD about ZERO), not the ~0 spread-about-mean"
    )


# ----------------------------------------------------------------------------
# COHORT FALLBACK LADDER — thin cohorts pool to metric, then global.
# ----------------------------------------------------------------------------
def test_thin_city_metric_falls_back_to_metric_pool():
    """A city×metric with n < MIN_COHORT_N pools to the all-city same-metric MAD-σ (TIER 2)."""
    # Thin city (n=5) + a big sibling city (n=30) of the SAME metric → metric pool n=35 ≥ 20.
    residuals = []
    residuals += [("ThinCity", "high", _td(i), 1.5) for i in range(5)]       # |r|=1.5 → would be ~2.2 alone
    residuals += [("BigCity", "high", _td(i), 0.5) for i in range(30)]        # |r|=0.5
    cells = script.fit_floors(residuals, min_cohort_n=20)
    thin = cells["ThinCity|JJA|high"]
    assert thin["cohort_tier"] == "metric", "thin city must fall back to the metric pool"
    # metric-pool median(|r|) is dominated by the 30 BigCity 0.5s → median 0.5 → ~0.74 → clipped to 1.0
    assert thin["sigma_floor_c"] == pytest.approx(1.0, abs=0.05), "metric-pool MAD-σ clipped to 1.0 floor"
    assert thin["n"] == 35


def test_thin_metric_pool_falls_back_to_global():
    """When even the metric pool is thin, the cell pools to the GLOBAL all-metric MAD-σ (TIER 3)."""
    residuals = []
    residuals += [("OnlyLowCity", "low", _td(i), 3.0) for i in range(5)]      # tiny low pool (n=5)
    residuals += [("HighCity", "high", _td(i), 1.0) for i in range(40)]        # big high pool
    cells = script.fit_floors(residuals, min_cohort_n=20)
    low = cells["OnlyLowCity|JJA|low"]
    assert low["cohort_tier"] == "global", "thin metric pool must fall back to global"
    assert low["n"] == 45  # global pool size


# ----------------------------------------------------------------------------
# ABSOLUTE LOWER BOUND — chain law 1.0°C stays.
# ----------------------------------------------------------------------------
def test_absolute_1c_floor_holds():
    """A cohort whose residual MAD-σ collapses below 1.0°C must still floor at 1.0°C."""
    n = 40
    residuals = [("CalmCity", "high", _td(i), 0.2 * (i % 3 - 1)) for i in range(n)]  # tiny residuals
    cells = script.fit_floors(residuals, min_cohort_n=20)
    cell = cells["CalmCity|JJA|high"]
    raw = script.mad_sigma_about_zero(np.array([0.2 * (i % 3 - 1) for i in range(n)]))
    assert raw < 1.0, "fixture: raw residual MAD-σ must be below the 1.0°C absolute floor"
    assert cell["sigma_floor_c"] == pytest.approx(1.0), "the chain law's 1.0°C absolute floor must hold"


# ----------------------------------------------------------------------------
# MAD-σ ESTIMATOR — robust to outliers (where std is not).
# ----------------------------------------------------------------------------
def test_mad_sigma_robust_to_outliers():
    """One huge miss must not blow up the floor the way a plain std would."""
    base = np.full(39, 0.5)
    arr = np.append(base, 50.0)  # one catastrophic 50°C miss
    std = float(np.std(arr, ddof=1))
    mad = script.mad_sigma_about_zero(arr)
    assert std > 7.0, "fixture: the outlier blows up the std"
    assert mad < 1.0, "MAD-σ (median-based) must resist the single outlier"


def test_mad_sigma_about_zero_empty_is_zero():
    assert script.mad_sigma_about_zero(np.array([])) == 0.0


def _td(i: int):
    import datetime as dt
    return dt.date(2026, 6, 8) + dt.timedelta(days=0)  # season JJA; date value irrelevant to cohorting
