# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC review Blocker C + Stat 1 (SD2). Insufficient prior
#   must yield identity (no learned shift) + CONSERVATIVE-WIDE residual, never the
#   0.5C floor (narrow + no-correction = overconfident). MIN_PRIOR_N 2->5.
"""Relationship tests for SD2: insufficient-prior conservative uncertainty.

RELATIONSHIP (cross-module) invariant, not a function test: a row fit from an
insufficient TIGGE prior (n_prior < MIN_PRIOR_N) must, when CONSUMED by the MC
path (``p_raw_vector_with_error_model``), produce a WIDE predictive distribution
with ZERO learned correction. The dangerous pre-SD2 behaviour was identity bias
(bias_c=0) paired with the DEFAULT_RESIDUAL_FLOOR_C=0.5 narrow sigma — a confident-
looking near-delta with no correction. SD2 forces residual_sd_c >= a conservative
floor so the consumed distribution is honestly uncertain.
"""
from __future__ import annotations

import numpy as np

from src.config import cities_by_name
from src.contracts.settlement_semantics import SettlementSemantics
from src.types.market import Bin
from src.calibration.ens_bias_model import BiasPrior, LiveResidual, posterior_bias
from src.calibration.ens_error_model import (
    CONSERVATIVE_RESIDUAL_FLOOR_C,
    DEFAULT_RESIDUAL_FLOOR_C,
    MIN_PRIOR_N,
    conservative_identity_model,
    current_gate_set_hash,
    p_raw_vector_with_error_model,
    predictive_error_from_posterior,
)


def _setup(city_name="San Francisco"):
    city = cities_by_name[city_name]
    ss = SettlementSemantics.for_city(city)
    bins = ([Bin(None, 50.0, "F", "<=50")]
            + [Bin(float(t), float(t + 1), "F", f"{t}-{t+1}F") for t in range(50, 95)]
            + [Bin(95.0, None, "F", ">=95")])
    return city, ss, bins


def _confident_narrow_model():
    """A model that LOOKS confident+narrow (the pre-SD2 trap): nonzero learned bias
    and residual floored at 0.5C."""
    prior = BiasPrior(mu_t=-3.0, v0=0.2)
    live = LiveResidual(e_bar=-3.2, n=40, sigma2=1.0)
    return predictive_error_from_posterior(
        posterior_bias(prior, live), residual_sd_c=DEFAULT_RESIDUAL_FLOOR_C
    )


def test_min_prior_n_raised_to_5():
    # Stat 1: learned correction needs at least as many prior samples as the
    # paired-transport gate (MIN_PAIRED_N=5).
    assert MIN_PRIOR_N == 5


def test_conservative_floor_is_wide():
    # Must be wide enough that a serviced identity row cannot masquerade as a
    # confident near-delta. Sensor-floor 0.5C is NOT acceptable here.
    assert CONSERVATIVE_RESIDUAL_FLOOR_C >= 2.5
    assert CONSERVATIVE_RESIDUAL_FLOOR_C > DEFAULT_RESIDUAL_FLOOR_C


def test_conservative_identity_zeros_correction_and_widens_sigma():
    narrow = _confident_narrow_model()
    # Precondition: the trap model really is confident + narrow.
    assert abs(narrow.effective_bias_c) > 0.0
    assert narrow.residual_sd_c == DEFAULT_RESIDUAL_FLOOR_C

    ident = conservative_identity_model(narrow)
    assert ident.bias_c == 0.0
    assert ident.effective_bias_c == 0.0
    assert ident.correction_strength == 0.0
    # Residual lifted to the conservative floor; total never below it.
    assert ident.residual_sd_c == max(narrow.residual_sd_c, CONSERVATIVE_RESIDUAL_FLOOR_C)
    assert ident.residual_sd_c >= CONSERVATIVE_RESIDUAL_FLOOR_C
    assert ident.total_residual_sd_c >= CONSERVATIVE_RESIDUAL_FLOOR_C


def test_identity_model_widens_consumed_mc_distribution():
    """The boundary that matters: producer-stored identity row -> MC consumption.

    The conservative-identity model, when fed to the SAME MC path the live system
    uses, must spread probability mass (lower peak) vs the narrow trap model.
    """
    city, ss, bins = _setup()
    members_F = np.array([60.0, 60.0, 60.0, 60.0])
    narrow = _confident_narrow_model()
    ident = conservative_identity_model(narrow)

    p_narrow = p_raw_vector_with_error_model(
        members_F, narrow, city, ss, bins, member_unit="degF", n_mc=8000
    )
    p_ident = p_raw_vector_with_error_model(
        members_F, ident, city, ss, bins, member_unit="degF", n_mc=8000
    )
    assert p_ident.max() < p_narrow.max(), (
        "insufficient-prior identity must yield a WIDER consumed distribution "
        "(lower peak) than the narrow 0.5C-floor trap model"
    )


def test_gate_change_invalidates_pre_sd2_hash():
    # SD2 changes the gate set (MIN_PRIOR_N 2->5 + CONSERVATIVE_RESIDUAL_FLOOR_C).
    # The pre-SD2 hash (68f1a05f8af33c0a) must no longer match: every row fit under
    # the old gate set auto-rejects at read time -> this rebuild is a one-time
    # full STAGING reproduce.
    assert current_gate_set_hash() != "68f1a05f8af33c0a"
