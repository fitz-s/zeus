# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/authority/statistical_calibration_authority_2026-06-12.txt (operator-run
#   clean-room consult REQ-20260612-174119), Step 1 — "Joint (b,k) interval-censored categorical
#   likelihood; DELETE variance-only k fit." Proven result #1: k_wrong² = k_true² + (δ/σ)² — a
#   variance-only spread fit ABSORBS unmodeled center bias, producing the over-confidence measured
#   on the real chain (served q 0.89 vs realized 0.72 on buy_no). This is the documented source fix
#   for "confidence align with reality"; replaces the certified-contaminated state/sigma_scale_fit.json.
"""TDD for the joint bias+scale interval-censored calibration estimator (authority Step 1).

The decisive claim (authority §1.1): fitting only a global spread scale k while omitting location
bias b is mathematically indistinguishable, to a variance-only fit, from extra variance — so a real
center bias δ inflates the fitted k (k_wrong² = k_true² + (δ/σ)²). The joint (b,k) interval-censored
likelihood recovers the TRUE k and the bias b, restoring honest dispersion.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.probability.joint_bias_scale import (
    CalibrationCell,
    fit_joint_bias_scale,
    fit_scale_only,
)


def _unit_bins(center_int: int, half_width: int = 6):
    """Integer-labelled unit bins [c-0.5, c+0.5) around a center, with open tails."""
    edges = []
    lo_tail = center_int - half_width
    hi_tail = center_int + half_width
    edges.append((None, lo_tail - 0.5))  # "<= lo_tail-1" open tail
    for c in range(lo_tail, hi_tail + 1):
        edges.append((c - 0.5, c + 0.5))
    edges.append((hi_tail + 0.5, None))  # ">= hi_tail+1" open tail
    return edges


def _winning_index(edges, settled_value):
    for i, (lo, hi) in enumerate(edges):
        lo_ok = lo is None or settled_value >= lo
        hi_ok = hi is None or settled_value < hi
        if lo_ok and hi_ok:
            return i
    return len(edges) - 1


def _synthetic_cells(n, b_true, k_true, sigma=1.5, seed_offset=0):
    """Generate settled cells: forecast center mu (integer-ish), true settle ~ N(mu+b_true, k_true*sigma)."""
    rng = np.random.RandomState(20260623 + seed_offset)
    cells = []
    for i in range(n):
        mu = float(rng.randint(-3, 4))  # forecast center varies across cells
        edges = _unit_bins(int(round(mu)))
        settled = rng.normal(mu + b_true, k_true * sigma)
        cells.append(
            CalibrationCell(mu=mu, sigma=sigma, edges=edges, winning_index=_winning_index(edges, settled))
        )
    return cells


class TestRecoversKnownParams:
    def test_recovers_true_scale_when_no_bias(self):
        cells = _synthetic_cells(4000, b_true=0.0, k_true=1.0, sigma=1.5)
        b_hat, k_hat, _ = fit_joint_bias_scale(cells)
        assert abs(b_hat) < 0.15, f"b should be ~0, got {b_hat}"
        assert abs(k_hat - 1.0) < 0.12, f"k should be ~1.0, got {k_hat}"


class TestBiasAbsorption:
    """The authority's load-bearing claim: variance-only fit absorbs bias into k; joint recovers it."""

    def test_variance_only_inflates_k_joint_recovers(self):
        # True process: center bias delta=1.2, true scale k=1.0, sigma=1.5 -> delta/sigma = 0.8.
        # Authority: k_wrong^2 = k_true^2 + (delta/sigma)^2 = 1 + 0.64 = 1.64 -> k_wrong ~ 1.28.
        b_true, k_true, sigma = 1.2, 1.0, 1.5
        cells = _synthetic_cells(6000, b_true=b_true, k_true=k_true, sigma=sigma)

        k_var_only, _ = fit_scale_only(cells)            # b forced to 0 (the contaminated fit)
        b_joint, k_joint, _ = fit_joint_bias_scale(cells)  # the Step-1 fix

        # variance-only k is INFLATED toward sqrt(1 + (delta/sigma)^2) ~ 1.28
        assert k_var_only > 1.18, f"variance-only k should inflate, got {k_var_only}"
        # joint fit RECOVERS true scale ~1.0 and the bias ~1.2
        assert abs(k_joint - k_true) < 0.15, f"joint k should recover ~1.0, got {k_joint}"
        assert abs(b_joint - b_true) < 0.25, f"joint b should recover ~1.2, got {b_joint}"
        # and the joint k is meaningfully less inflated than variance-only
        assert k_joint < k_var_only - 0.1


class TestIntervalCensored:
    def test_uses_bin_preimage_not_point_density(self):
        # A single tight cell: settle lands in the modal bin; likelihood is the bin MASS, finite.
        edges = _unit_bins(0)
        cell = CalibrationCell(mu=0.0, sigma=1.0, edges=edges, winning_index=_winning_index(edges, 0.0))
        b, k, res = fit_joint_bias_scale([cell] * 50)
        assert math.isfinite(k) and k > 0
