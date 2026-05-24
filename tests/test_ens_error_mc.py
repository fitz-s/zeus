# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Tests for residual-aware MC (extra_member_sigma byte-identical at 0, widening) + p_raw_vector_with_error_model.
# Reuse: Inspect ens_error_model.p_raw_vector_with_error_model + ensemble_signal before reuse.
"""TDD: residual-aware Monte Carlo.

The shared p_raw_vector_from_maxes gains an optional ``extra_member_sigma`` (forecast/
station residual SD), combined in quadrature with the instrument sigma. Default 0.0 is
byte-identical to current behavior (one MC path; train==serve preserved). The wrapper
``p_raw_vector_with_error_model`` applies the predictive-error model: converts the °C
bias/residual to the members' native unit, subtracts λ·bias pre-MC, and widens the draw
by the total residual SD.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.config import cities_by_name
from src.contracts.settlement_semantics import SettlementSemantics
from src.types.market import Bin
from src.signal.ensemble_signal import p_raw_vector_from_maxes
from src.calibration.ens_bias_model import BiasPrior, LiveResidual, posterior_bias
from src.calibration.ens_error_model import (
    predictive_error_from_posterior,
    p_raw_vector_with_error_model,
)


def _setup(city_name="San Francisco"):
    city = cities_by_name[city_name]
    ss = SettlementSemantics.for_city(city)
    bins = ([Bin(None, 50.0, "F", "<=50")]
            + [Bin(float(t), float(t + 1), "F", f"{t}-{t+1}F") for t in range(50, 95)]
            + [Bin(95.0, None, "F", ">=95")])
    return city, ss, bins


def test_extra_member_sigma_zero_is_byte_identical():
    city, ss, bins = _setup()
    maxes = np.array([57.0, 58.0, 59.0, 60.0])
    a = p_raw_vector_from_maxes(maxes, city, ss, bins, n_mc=4000)
    b = p_raw_vector_from_maxes(maxes, city, ss, bins, n_mc=4000, extra_member_sigma=0.0)
    assert np.array_equal(a, b), "extra_member_sigma=0 must not change the deterministic result"


def test_extra_member_sigma_widens_distribution():
    city, ss, bins = _setup()
    maxes = np.array([60.0, 60.0, 60.0, 60.0])
    narrow = p_raw_vector_from_maxes(maxes, city, ss, bins, n_mc=8000)
    wide = p_raw_vector_from_maxes(maxes, city, ss, bins, n_mc=8000, extra_member_sigma=4.0)
    assert wide.max() < narrow.max(), "adding residual sigma must spread mass (lower peak)"


def test_wrapper_shifts_warmer_and_widens():
    city, ss, bins = _setup()
    members_F = np.array([56.0, 57.0, 58.0, 59.0, 60.0])
    # confident cold bias -> full correction; residual scale widens
    prior = BiasPrior(mu_t=-5.0, v0=0.3)
    live = LiveResidual(e_bar=-5.5, n=40, sigma2=1.0)
    em = predictive_error_from_posterior(posterior_bias(prior, live), residual_sd_c=2.0)
    base = p_raw_vector_from_maxes(members_F, city, ss, bins, n_mc=8000)
    corr = p_raw_vector_with_error_model(members_F, em, city, ss, bins, member_unit="degF", n_mc=8000)
    def exp(p): return float(sum(((b.low+b.high)/2 if b.low is not None and b.high is not None
                                  else (b.low or b.high)) * pp for b, pp in zip(bins, p)))
    assert exp(corr) > exp(base) + 3.0, "correction must shift expectation warmer (°C bias -> °F)"
    assert corr.max() < base.max(), "residual scale must widen the corrected distribution"


def test_wrapper_zero_strength_only_widens_no_shift():
    city, ss, bins = _setup()
    members_F = np.array([60.0, 61.0, 62.0, 63.0])
    # disagreement -> correction_strength 0 -> no mean shift, but widened
    prior = BiasPrior(mu_t=-1.91, v0=0.02)
    live = LiveResidual(e_bar=+0.25, n=40, sigma2=0.8)
    em = predictive_error_from_posterior(posterior_bias(prior, live), residual_sd_c=1.0)
    assert em.correction_strength == 0.0
    base = p_raw_vector_from_maxes(members_F, city, ss, bins, n_mc=8000)
    corr = p_raw_vector_with_error_model(members_F, em, city, ss, bins, member_unit="degF", n_mc=8000)
    def exp(p): return float(sum(((b.low+b.high)/2 if b.low is not None and b.high is not None
                                  else (b.low or b.high)) * pp for b, pp in zip(bins, p)))
    assert abs(exp(corr) - exp(base)) < 1.0, "lambda=0 must not shift the mean materially"
    assert corr.max() <= base.max() + 1e-9, "but uncertainty must not shrink"


def test_negative_extra_member_sigma_rejected():
    city, ss, bins = _setup()
    import pytest as _p
    with _p.raises(ValueError):
        p_raw_vector_from_maxes(np.array([60.0, 61.0]), city, ss, bins, n_mc=100, extra_member_sigma=-1.0)
