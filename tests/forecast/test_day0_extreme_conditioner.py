# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   (Stage 4 block lines 1091-1107 RED-on-revert test names; support transforms
#   lines 320-340; center clamp lines 299-318) reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (the day0 extreme is
#   an INPUT to the integrator: high-market settlement Y = max(obs_high, X), so a
#   bin entirely below the observed high has q = 0).
"""RED-on-revert tests for the Stage-4 day0 extreme conditioner.

Each test fails if the corrected settlement-conditioned transform is reverted to
the broken behavior the spec replaces — the bare predictive Normal
``X ~ N(mu, sigma)`` integrated over the bins (which would place non-zero
probability on impossible bins below the observed high / above the observed low,
and would leave the center un-clamped at mu*).

The reference for "the broken behavior" is the ordinary Normal-interval bin mass
``normal_cdf(hi) - normal_cdf(lo)`` and an un-clamped center; the assertions below
are constructed so that bare-Normal output would be strictly non-zero (resp. would
leave the center unchanged), making the corrected transform the only thing that can
pass them.
"""
from __future__ import annotations

import math

import pytest

from src.forecast.day0_conditioner import (
    Day0Conditioning,
    Day0ObservationState,
    condition_day0,
    day0_bin_preimage_native,
    probability_high_day0_bin,
    probability_low_day0_bin,
)


def _normal_cdf(mu: float, sigma: float):
    """Standardized predictive Normal CDF folded with (mu, sigma): Phi((x-mu)/sigma).

    Handles +-inf shoulders the way the day0 integrator relies on (0.0 / 1.0).
    """

    def _cdf(x: float) -> float:
        if x == -math.inf:
            return 0.0
        if x == math.inf:
            return 1.0
        return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))

    return _cdf


# --------------------------------------------------------------------------- #
# RED-on-revert #1: high bins below the observed high have ZERO probability.
# --------------------------------------------------------------------------- #
def test_high_bins_below_observed_high_have_zero_probability():
    """A high-market bin entirely below the observed running high carries q = 0.

    Tokyo-class: observed_high so far is 21. The predictive remaining distribution
    is centered well ABOVE the impossible bin (mu = 21, sigma = 2.0) so that the
    BARE Normal interval over the [18, 19) preimage bin is clearly NON-zero — i.e.
    the only way to get 0.0 is the settlement-conditioned transform (hi <= obs_high
    -> 0.0). If the transform is reverted to the bare Normal interval
    ``normal_cdf(hi) - normal_cdf(lo)``, this bin gets positive mass and the test
    FAILS.
    """
    obs_high = 21.0
    mu, sigma = 21.0, 2.0
    cdf = _normal_cdf(mu, sigma)

    # Bin labeled 18 on a wmo_half_up integer grid -> preimage [17.5, 18.5).
    lo, hi = day0_bin_preimage_native(18.0, 18.0, rounding_rule="wmo_half_up")
    assert hi <= obs_high  # bin is entirely below the observed high

    # The bare-Normal interval over this bin is strictly positive (the broken path
    # would return THIS), so a passing 0.0 can only come from the corrected
    # transform.
    bare_normal_mass = cdf(hi) - cdf(lo)
    assert bare_normal_mass > 1e-6

    q = probability_high_day0_bin(obs_high, lo, hi, cdf)
    assert q == 0.0

    # The straddling observed bin (label 21 -> preimage [20.5, 21.5)) collects the
    # full remaining-distribution mass below its upper edge: normal_cdf(hi).
    lo_s, hi_s = day0_bin_preimage_native(21.0, 21.0, rounding_rule="wmo_half_up")
    assert lo_s <= obs_high < hi_s
    q_straddle = probability_high_day0_bin(obs_high, lo_s, hi_s, cdf)
    assert q_straddle == pytest.approx(cdf(hi_s))
    # And it strictly exceeds the bare-Normal interval over the same bin (mass from
    # below obs_high collapses into it) — a second guard against the broken path.
    assert q_straddle > (cdf(hi_s) - cdf(lo_s))


# --------------------------------------------------------------------------- #
# RED-on-revert #2: low bins above the observed low have ZERO probability.
# --------------------------------------------------------------------------- #
def test_low_bins_above_observed_low_have_zero_probability():
    """A low-market bin entirely above the observed running low carries q = 0.

    Symmetric to the high case: observed_low so far is 5.0; the predictive
    remaining distribution is centered at 5.0 (sigma = 2.0) so the bare Normal
    interval over the [7.5, 8.5) preimage bin is clearly NON-zero. The corrected
    transform (lo >= obs_low -> 0.0) returns 0.0; the broken bare-Normal path would
    return positive mass and FAIL the test.
    """
    obs_low = 5.0
    mu, sigma = 5.0, 2.0
    cdf = _normal_cdf(mu, sigma)

    # Bin labeled 8 -> preimage [7.5, 8.5); entirely above the observed low.
    lo, hi = day0_bin_preimage_native(8.0, 8.0, rounding_rule="wmo_half_up")
    assert lo >= obs_low

    bare_normal_mass = cdf(hi) - cdf(lo)
    assert bare_normal_mass > 1e-6

    q = probability_low_day0_bin(obs_low, lo, hi, cdf)
    assert q == 0.0

    # The straddling observed bin (label 5 -> preimage [4.5, 5.5)) collects the
    # full remaining-distribution mass above its lower edge: 1 - normal_cdf(lo).
    lo_s, hi_s = day0_bin_preimage_native(5.0, 5.0, rounding_rule="wmo_half_up")
    assert lo_s < obs_low <= hi_s
    q_straddle = probability_low_day0_bin(obs_low, lo_s, hi_s, cdf)
    assert q_straddle == pytest.approx(1.0 - cdf(lo_s))
    assert q_straddle > (cdf(hi_s) - cdf(lo_s))


# --------------------------------------------------------------------------- #
# RED-on-revert #3: the observed extreme clamps the center.
# --------------------------------------------------------------------------- #
def test_observed_extreme_clamps_center():
    """center_after = max(mu_before, observed_high) for high; min for low.

    The broken behavior leaves the center at mu_before (no support transform). This
    test uses mu_before BELOW the observed high (a high market) and ABOVE the
    observed low (a low market) so the clamp must MOVE the center; if reverted to
    "center_after = center_before", both assertions FAIL.
    """
    # High market: mu_before = 18.0 sits BELOW the observed high 21.0 -> clamp up.
    obs_high_state = Day0ObservationState(
        observed=True,
        station_id="RJTT",
        source="metar",
        samples_count=12,
        latest_observed_at_utc=None,
        observed_high_native=21.0,
        observed_low_native=None,
        observed_extreme_native=21.0,
        raw_observation_hash="hash-high",
    )
    cond_high = condition_day0(
        metric="high", obs=obs_high_state, center_before_native=18.0
    )
    assert isinstance(cond_high, Day0Conditioning)
    assert cond_high.active is True
    assert cond_high.status == "HIGH_CLAMPED"
    assert cond_high.center_before_native == 18.0
    # The clamp MOVED the center to the observed high (not left at 18.0).
    assert cond_high.center_after_native == 21.0
    assert cond_high.center_after_native != cond_high.center_before_native
    assert cond_high.support_lower_native == 21.0
    assert cond_high.support_upper_native is None
    assert cond_high.observed_extreme_native == 21.0

    # Low market: mu_before = 8.0 sits ABOVE the observed low 5.0 -> clamp down.
    obs_low_state = Day0ObservationState(
        observed=True,
        station_id="RJTT",
        source="metar",
        samples_count=12,
        latest_observed_at_utc=None,
        observed_high_native=None,
        observed_low_native=5.0,
        observed_extreme_native=5.0,
        raw_observation_hash="hash-low",
    )
    cond_low = condition_day0(
        metric="low", obs=obs_low_state, center_before_native=8.0
    )
    assert cond_low.active is True
    assert cond_low.status == "LOW_CLAMPED"
    assert cond_low.center_before_native == 8.0
    assert cond_low.center_after_native == 5.0
    assert cond_low.center_after_native != cond_low.center_before_native
    assert cond_low.support_upper_native == 5.0
    assert cond_low.support_lower_native is None
    assert cond_low.observed_extreme_native == 5.0

    # A center already beyond the observed extreme is left untouched by the clamp
    # (the support transform never moves the center the WRONG way): high market with
    # mu_before = 26.0 above obs_high 21.0 stays at 26.0.
    cond_high_above = condition_day0(
        metric="high", obs=obs_high_state, center_before_native=26.0
    )
    assert cond_high_above.center_after_native == 26.0


# --------------------------------------------------------------------------- #
# Fail-closed: no day0 observation -> inactive, center untouched.
# --------------------------------------------------------------------------- #
def test_no_day0_observation_is_inactive_and_does_not_clamp():
    not_observed = Day0ObservationState(
        observed=False,
        station_id="RJTT",
        source="metar",
        samples_count=0,
        latest_observed_at_utc=None,
        observed_high_native=None,
        observed_low_native=None,
        observed_extreme_native=None,
        raw_observation_hash=None,
    )
    cond = condition_day0(metric="high", obs=not_observed, center_before_native=18.0)
    assert cond.active is False
    assert cond.status == "NO_DAY0"
    assert cond.center_after_native == cond.center_before_native == 18.0
    assert cond.support_lower_native is None
    assert cond.support_upper_native is None


def test_observed_but_relevant_side_missing_is_refused():
    """Claimed observation set but the relevant-side extreme is absent -> refused."""
    obs_missing_high = Day0ObservationState(
        observed=True,
        station_id="RJTT",
        source="metar",
        samples_count=12,
        latest_observed_at_utc=None,
        observed_high_native=None,  # high market needs this; it's missing
        observed_low_native=5.0,
        observed_extreme_native=5.0,
        raw_observation_hash="hash",
    )
    cond = condition_day0(
        metric="high", obs=obs_missing_high, center_before_native=18.0
    )
    assert cond.active is False
    assert cond.status == "OBS_SOURCE_MISSING_REFUSED"
    assert cond.center_after_native == 18.0


# --------------------------------------------------------------------------- #
# HK oracle_truncate preimage threads through (asymmetric bin).
# --------------------------------------------------------------------------- #
def test_oracle_truncate_preimage_asymmetric_bin_bounds():
    """HK truncation: bin label 28 -> preimage [28, 29), not the symmetric WMO bin."""
    lo, hi = day0_bin_preimage_native(28.0, 28.0, rounding_rule="oracle_truncate")
    assert lo == 28.0
    assert hi == 29.0
    # wmo_half_up over the same label would be [27.5, 28.5) — confirm we are NOT
    # silently defaulting to the symmetric rule.
    lo_wmo, hi_wmo = day0_bin_preimage_native(28.0, 28.0, rounding_rule="wmo_half_up")
    assert (lo_wmo, hi_wmo) == (27.5, 28.5)


def test_open_shoulders_are_infinite():
    lo, hi = day0_bin_preimage_native(None, 18.0, rounding_rule="wmo_half_up")
    assert lo == -math.inf
    assert hi == 18.5
    lo2, hi2 = day0_bin_preimage_native(20.0, None, rounding_rule="wmo_half_up")
    assert lo2 == 19.5
    assert hi2 == math.inf
