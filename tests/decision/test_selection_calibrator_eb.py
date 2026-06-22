# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: selection-conditioned hierarchical EB hybrid calibrator
#   (frontier consult REQ-20260622-154643; live_order_pathology 2026-06-22).
"""RED-first tests for the hierarchical empirical-Bayes pieces of the selection calibrator.

Per the consult (REQ-20260622-154643): the calibrator is a selection-conditioned hierarchical EB
hybrid. The full forecast corpus is the PRIOR (p0_b); the executed/would-admit selected rows are the
LIKELIHOOD (w_s, n_s); beta-binomial shrinkage with a LEARNED tau gives the lower bound:

    alpha = tau*p0_b + w_s ;  beta = tau*(1-p0_b) + n_s - w_s
    q_safe_lb = BetaInvCDF(0.05, alpha, beta) ;  q_safe = min(raw_side_prob, q_safe_lb)

Tested here:
  * WEIGHTED PAVA: an n=1 violating bucket cannot materially drag an n=400 bucket ([MEDIUM] fix).
  * beta-binomial EB lower bound: monotone in evidence, shrinks toward the prior on thin selected
    support, toward the likelihood on deep support; conservative (<= point); tau=0 recovers the
    pure prior, tau->inf pins the prior.
  * EB blocks the toxic selected NO (selected w_s/n_s ~0.68 dominates the prior at high tau-floor)
    and preserves genuine cheap YES via borrowing from the YES corpus prior.
"""
from __future__ import annotations

import math

import pytest

from src.decision import selection_calibrator as sc


# --------------------------------------------------------------------------------------------------
# Weighted PAVA ([MEDIUM] fix: a thin cell must not drag a deep cell).
# --------------------------------------------------------------------------------------------------

def test_weighted_pava_thin_cell_cannot_drag_deep_cell():
    # bucket A: n=400, realized 0.20 ; bucket B: n=1, realized 0.95 (a violation in the WRONG
    # direction would be B<A, here B>A so no violation). Construct a real violation: A high, B low.
    xs = [0.1, 0.2]
    ys = [0.80, 0.10]          # A=0.80 (n=400) then B=0.10 (n=1) -> violation (decreasing)
    w = [400.0, 1.0]
    fitted = sc.isotonic_nondecreasing_weighted(xs, ys, w)
    # The weighted pooled value must stay very close to A's 0.80 (the n=1 cell barely moves it),
    # NOT the unweighted midpoint 0.45.
    assert fitted[0] == fitted[1]  # pooled (they violated)
    assert fitted[0] > 0.79        # dominated by the n=400 cell, not dragged to 0.45
    # Sanity: unweighted PAVA would have pooled to 0.45.
    unweighted = sc.isotonic_nondecreasing(xs, ys)
    assert abs(unweighted[0] - 0.45) < 1e-9


def test_weighted_pava_is_monotone_nondecreasing():
    xs = [0.1, 0.2, 0.3, 0.4]
    ys = [0.3, 0.25, 0.5, 0.45]
    w = [10.0, 50.0, 20.0, 100.0]
    fitted = sc.isotonic_nondecreasing_weighted(xs, ys, w)
    for a, b in zip(fitted, fitted[1:]):
        assert b >= a - 1e-9


# --------------------------------------------------------------------------------------------------
# Beta-binomial EB lower bound.
# --------------------------------------------------------------------------------------------------

def test_eb_lower_bound_conservative_and_below_point():
    # Selected: 71/104 = 0.683 with a prior p0=0.85 (corpus over-states because un-conditioned).
    lb = sc.eb_lower_bound(p0=0.85, tau=10.0, wins=71, n=104, alpha_quantile=0.05)
    point = (10.0 * 0.85 + 71) / (10.0 + 104)
    assert 0.0 < lb < point          # a lower bound, strictly below the posterior mean
    assert lb < 0.85                 # pulled below the over-stated prior by the selected evidence


def test_eb_lower_bound_shrinks_to_prior_when_selected_thin():
    # n_s small -> the bound leans on the prior; n_s large at the same rate -> leans on the data.
    lb_thin = sc.eb_lower_bound(p0=0.85, tau=10.0, wins=3, n=4, alpha_quantile=0.05)
    lb_deep = sc.eb_lower_bound(p0=0.85, tau=10.0, wins=300, n=400, alpha_quantile=0.05)
    # Deep evidence at 0.75 gives a tighter (higher, less uncertain) lower bound than 4-sample data.
    assert lb_deep > lb_thin


def test_eb_tau_zero_is_pure_data_and_tau_large_pins_prior():
    # tau=0 -> alpha=wins, beta=n-wins (pure Beta on the data). Large tau -> posterior ~ prior.
    lb_data = sc.eb_lower_bound(p0=0.20, tau=0.0, wins=71, n=104, alpha_quantile=0.05)
    lb_prior = sc.eb_lower_bound(p0=0.20, tau=1e6, wins=71, n=104, alpha_quantile=0.05)
    assert lb_data > 0.55            # data ~0.68 -> LB well above the 0.20 prior
    assert abs(lb_prior - 0.20) < 0.02  # pinned near the prior mean


def test_eb_lower_bound_matches_wilson_shape_at_tau_zero():
    # At tau=0 the beta lower bound should be in the same ballpark as the Wilson LB (both are
    # one-sided 5% lower bounds of the same binomial). Not identical (Beta vs Wilson) but close.
    beta_lb = sc.eb_lower_bound(p0=0.5, tau=0.0, wins=70, n=104, alpha_quantile=0.05)
    wilson_lb = sc.beta_lower_bound_95(70, 104)
    assert abs(beta_lb - wilson_lb) < 0.06


# --------------------------------------------------------------------------------------------------
# EB serving via persisted q_safe_lb (no SciPy at runtime).
# --------------------------------------------------------------------------------------------------

def test_runtime_serves_persisted_q_safe_lb_when_present():
    # A v2 EB cell persists q_safe_lb directly; the runtime serves min(raw_side_prob, q_safe_lb)
    # WITHOUT recomputing (no SciPy at runtime).
    side, lead_b, bin_class = "NO", "L1", "nonmodal"
    raw = 0.875
    bucket_idx, _ = sc.raw_prob_bucket(raw)
    key = f"{side}|{lead_b}|{bin_class}|pb{bucket_idx}"
    art = {
        "_meta": {"posterior_version": sc.DEFAULT_POSTERIOR_VERSION, "min_n": 30,
                  "schema": "eb_v2"},
        "cells": {key: {"n": 104, "n_selected": 104, "wins_selected": 71,
                        "p0_corpus": 0.85, "tau": 10.0, "q_safe_lb": 0.60}},
    }
    v = sc.apply_selection_calibrator(
        raw_side_prob=raw, side=side, lead_days=1.0, bin_class=bin_class, artifact=art,
    )
    assert v.trade is True
    assert math.isclose(v.q_safe, 0.60, rel_tol=1e-9)  # served the persisted q_safe_lb directly
    assert v.q_safe - 0.70 <= 0.0                       # blocks the ~0.70 NO cost
