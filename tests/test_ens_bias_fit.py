# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: operator hierarchical-bias adjudication 2026-05-24 §10.2
#   (robust estimators, minimum n_eff, TIGGE prior + OpenData likelihood, with
#   an irreducible transfer-uncertainty floor on the prior variance so abundant
#   live evidence can overcome a different-product prior).
"""TDD tests for the bucket fitter: residual samples -> PosteriorBias.

The fitter is the pure (no-DB) layer that turns per-bucket residual samples
((forecast - actual) lists for TIGGE and OpenData) into a posterior bias via
``posterior_bias``. Contract:
  - ROBUST (trimmed) mean so a few outlier days do not move the estimate;
  - prior variance = var(TIGGE)/n_TIGGE + v_transfer, where v_transfer is the
    irreducible transfer-uncertainty floor (TIGGE is a *different product*);
  - live likelihood variance-of-mean = var(OpenData)/n_OpenData;
  - live dropped below a minimum-n floor (fall back to prior);
  - measured same-window paired delta inflates the prior variance.
"""

from __future__ import annotations

import random

import pytest

from src.calibration.ens_bias_model import PosteriorBias, fit_bucket, robust_mean


def _gauss(mu, sd, n, seed):
    r = random.Random(seed)
    return [r.gauss(mu, sd) for _ in range(n)]


def test_robust_mean_ignores_outliers():
    xs = [-1.0] * 20 + [-30.0]  # one gross outlier
    rm = robust_mean(xs)
    assert abs(rm - (-1.0)) < 0.2, f"robust mean must reject the outlier, got {rm}"
    assert rm > -1.5  # plain mean ~-2.38; robust must not be near it


def test_fit_bucket_live_dominates_when_abundant_and_precise():
    tigge = _gauss(-1.0, 1.0, 200, seed=1)
    opendata = _gauss(-1.9, 1.0, 200, seed=2)   # abundant + precise live
    post = fit_bucket(tigge, opendata)
    assert isinstance(post, PosteriorBias)
    assert post.weight_live > 0.8, f"abundant precise live must dominate, w={post.weight_live}"
    assert abs(post.bias - (-1.9)) < 0.3
    assert post.n_live == 200


def test_fit_bucket_falls_back_to_prior_when_live_empty():
    tigge = _gauss(-1.0, 1.0, 200, seed=3)
    post = fit_bucket(tigge, [])
    assert post.weight_live == pytest.approx(0.0)
    assert abs(post.bias - (-1.0)) < 0.2
    assert post.n_live == 0


def test_fit_bucket_drops_live_below_min_n_floor():
    tigge = _gauss(-1.0, 1.0, 200, seed=4)
    opendata = [-3.0, -3.0, -3.0]   # n=3 < floor -> dropped
    post = fit_bucket(tigge, opendata, min_live_n=5)
    assert post.weight_live == pytest.approx(0.0), "sub-floor live must be dropped"
    assert abs(post.bias - (-1.0)) < 0.2  # prior only


def test_fit_bucket_more_tigge_tightens_prior():
    sd_small = fit_bucket(_gauss(-1.0, 2.0, 30, seed=5), []).sd
    sd_large = fit_bucket(_gauss(-1.0, 2.0, 3000, seed=6), []).sd
    assert sd_large < sd_small, "more prior data must tighten the prior"


def test_fit_bucket_paired_delta_shifts_weight_to_live():
    tigge = _gauss(-1.0, 1.0, 200, seed=7)
    opendata = _gauss(-2.5, 1.0, 30, seed=8)
    w_trust = fit_bucket(tigge, opendata, paired_delta_abs=0.0).weight_live
    w_distrust = fit_bucket(tigge, opendata, paired_delta_abs=1.5).weight_live
    assert w_distrust > w_trust


def test_fit_bucket_robust_to_a_few_outlier_days():
    # A handful of heat-event outliers must not swing the bucket bias.
    tigge = _gauss(-1.0, 1.0, 200, seed=9)
    opendata = _gauss(-1.5, 1.0, 60, seed=10) + [-12.0, -11.0, -13.0]  # 3 extreme cold misses
    post = fit_bucket(tigge, opendata)
    # robust mean keeps it near -1.5, not dragged toward the -12 tail
    assert post.bias > -2.5, f"outlier days must not dominate, bias={post.bias}"
