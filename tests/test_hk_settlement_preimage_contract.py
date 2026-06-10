# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: Operator law 2026-06-10 — "Hong Kong is a SPECIAL city: its
#   market settlement does NOT follow the WU half-round rule used elsewhere."
#   HK settles via HKO/UMA floor() truncation; the q-integration preimage MUST
#   use the truncation preimage [t, t+1), NOT the symmetric WMO [t-0.5, t+0.5).
#   K-cut: src/contracts/settlement_semantics.settlement_preimage_offsets is the
#   SINGLE source of the per-city preimage; emos.bin_probability_settlement and
#   the replacement_forecast_materializer fused-q path consume it.
"""Relationship tests for the per-city settlement PREIMAGE contract.

These pin the cross-module invariant the operator audit established:

  RELATIONSHIP: a bin's declared ``rounding_rule`` (set per-city by the seed
  builder: oracle_truncate for Hong Kong, wmo_half_up elsewhere) MUST flow into
  the q-integration preimage. The integrator may not assume a city-independent
  (WMO symmetric) preimage.

Why relationship-first (Fitz methodology): the bug was NOT "bin_probability_
settlement is wrong" — it computed the WMO preimage correctly. The bug was that
the rule declared in Module A (bins / for_city) was DROPPED when its output
flowed into Module B (the fused-Normal q integrator). The invariant under test
is the boundary property, not a single function's output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import norm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.emos import bin_probability_settlement
from src.contracts.settlement_semantics import settlement_preimage_offsets


# ---------------------------------------------------------------------------
# 1. The contract: per-rule preimage offsets
# ---------------------------------------------------------------------------


def test_wmo_preimage_is_symmetric():
    assert settlement_preimage_offsets("wmo_half_up") == (-0.5, 0.5)


def test_hk_truncate_preimage_is_asymmetric_upward():
    """floor(x) == t  ⟺  x ∈ [t, t + 1): offsets (0, +1), NOT symmetric.

    This is the whole HK fix. The preimage of label t under truncation starts AT
    t (28.0 already settles to 28) and extends a FULL quantum upward (28.999 still
    settles to 28). The symmetric WMO preimage [t-0.5, t+0.5) is wrong for HK.
    """
    assert settlement_preimage_offsets("oracle_truncate") == (0.0, 1.0)
    # alias contract: floor ≡ oracle_truncate
    assert settlement_preimage_offsets("floor") == (0.0, 1.0)


def test_ceil_preimage_is_asymmetric_downward():
    assert settlement_preimage_offsets("ceil") == (-1.0, 0.0)


def test_preimage_offsets_scale_with_half_step():
    # °F integer grid: settlement_step_c/2 differs, but the per-rule shape holds.
    assert settlement_preimage_offsets("wmo_half_up", half_step=0.25) == (-0.25, 0.25)
    assert settlement_preimage_offsets("oracle_truncate", half_step=0.25) == (0.0, 0.5)


def test_unsupported_rule_raises():
    with pytest.raises(ValueError, match="unsupported rounding rule"):
        settlement_preimage_offsets("bankers_round")


# ---------------------------------------------------------------------------
# 2. Standard-city regression: wmo_half_up byte-identical to the historical path
# ---------------------------------------------------------------------------


def _historical_symmetric_mass(mu, sigma, lo, hi, half_step=0.5):
    """The OLD hardcoded symmetric preimage (pre-fix), reproduced for regression."""
    a = -np.inf if lo is None else lo - half_step
    b = np.inf if hi is None else hi + half_step
    return max(0.0, float(norm.cdf((b - mu) / sigma) - norm.cdf((a - mu) / sigma)))


@pytest.mark.parametrize(
    "lo,hi",
    [(30.0, 30.0), (None, 23.0), (33.0, None), (26.0, 28.0), (24.0, 24.0)],
)
def test_wmo_half_up_byte_identical_to_historical_symmetric(lo, hi):
    """Default rule (wmo_half_up) reproduces the pre-fix symmetric path exactly.

    Standard cities (everyone but HK) must see ZERO change. This is the
    regression pin that lets the HK fix ship without re-validating the world.
    """
    mu, sigma = 28.32, 1.604
    new = bin_probability_settlement(mu, sigma, lo, hi)  # default wmo_half_up
    old = _historical_symmetric_mass(mu, sigma, lo, hi)
    assert new == old, f"WMO path drifted for bin ({lo},{hi}): {new} != {old}"


# ---------------------------------------------------------------------------
# 3. HK truncation differs from WMO where it must, and matches floor() preimage
# ---------------------------------------------------------------------------


def test_hk_interior_bin_mass_matches_truncation_preimage():
    """HK interior bin t integrates over [t, t+1), an analytic check."""
    mu, sigma = 28.32, 1.604
    t = 28.0
    got = bin_probability_settlement(mu, sigma, t, t, rounding_rule="oracle_truncate")
    expected = float(norm.cdf((t + 1.0 - mu) / sigma) - norm.cdf((t - mu) / sigma))
    assert got == pytest.approx(expected, abs=1e-12)


def test_hk_truncation_shifts_mass_down_relative_to_wmo():
    """With mu above a bin's lower edge, truncation moves mass to LOWER bins.

    For a predicted center between two integers, floor() truncation assigns more
    probability to the lower bins than WMO half-up. This is the systematic
    direction of the pollution the live HK position was exposed to (q[30°C]
    overstated under WMO; corrected DOWN under truncation).
    """
    mu, sigma = 28.32, 1.604
    # 30°C bin sits ABOVE the center 28.32 -> truncation pulls mass away from it.
    wmo_30 = bin_probability_settlement(mu, sigma, 30.0, 30.0, rounding_rule="wmo_half_up")
    trc_30 = bin_probability_settlement(mu, sigma, 30.0, 30.0, rounding_rule="oracle_truncate")
    assert trc_30 < wmo_30
    # 26°C bin sits BELOW the center -> truncation ADDS mass to it.
    wmo_26 = bin_probability_settlement(mu, sigma, 26.0, 26.0, rounding_rule="wmo_half_up")
    trc_26 = bin_probability_settlement(mu, sigma, 26.0, 26.0, rounding_rule="oracle_truncate")
    assert trc_26 > wmo_26


def test_hk_shoulder_preimage_matches_truncation():
    """Open shoulders under truncation use [t, +inf) / (-inf, t+1)."""
    mu, sigma = 28.32, 1.604
    # Open-high shoulder labeled 33 -> [33, +inf)
    hi_shoulder = bin_probability_settlement(
        mu, sigma, 33.0, None, rounding_rule="oracle_truncate"
    )
    expected_hi = float(1.0 - norm.cdf((33.0 - mu) / sigma))
    assert hi_shoulder == pytest.approx(expected_hi, abs=1e-12)
    # Open-low shoulder labeled 23 -> (-inf, 24)
    lo_shoulder = bin_probability_settlement(
        mu, sigma, None, 23.0, rounding_rule="oracle_truncate"
    )
    expected_lo = float(norm.cdf((23.0 + 1.0 - mu) / sigma))
    assert lo_shoulder == pytest.approx(expected_lo, abs=1e-12)


# ---------------------------------------------------------------------------
# 4. Equivalence with the HK-aware Monte-Carlo path (ensemble_signal)
# ---------------------------------------------------------------------------


def test_truncation_preimage_matches_ensemble_signal_floor_edges():
    """The contract offsets reproduce the analytic_p_raw_vector_from_maxes floor edges.

    ensemble_signal's HK-aware path defines floor(x)==t ⟺ x∈[t, t+1). Our
    contract must produce the SAME edges so the two integrators agree on HK.
    """
    # ensemble_signal floor rule: _low(t)=t, _high(t)=t+prec (prec=1)
    low_off, high_off = settlement_preimage_offsets("oracle_truncate", half_step=0.5)
    # _low(t) = t + low_off  -> low_off == 0
    # _high(t) = t + high_off -> high_off == prec == 1
    assert low_off == 0.0
    assert high_off == 1.0


# ---------------------------------------------------------------------------
# 5. Materializer boundary invariant: the fused-q path CONSUMES the bin rule
# ---------------------------------------------------------------------------

from src.data.replacement_forecast_materializer import (  # noqa: E402
    _build_fused_q_bounds,
    _family_rounding_rule,
)
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import AifsTemperatureBin  # noqa: E402


def _hk_bins():
    """A minimal HK-style bin family (all oracle_truncate, valid shoulders)."""
    return [
        AifsTemperatureBin("lo", upper_c=25.0, center_c=24.0, rounding_rule="oracle_truncate"),
        AifsTemperatureBin("b26", lower_c=26.0, upper_c=26.0, center_c=26.0, rounding_rule="oracle_truncate"),
        AifsTemperatureBin("b27", lower_c=27.0, upper_c=27.0, center_c=27.0, rounding_rule="oracle_truncate"),
        AifsTemperatureBin("hi", lower_c=28.0, center_c=29.0, rounding_rule="oracle_truncate"),
    ]


def test_family_rounding_rule_extracts_hk_truncation():
    assert _family_rounding_rule(_hk_bins()) == "oracle_truncate"


def test_family_rounding_rule_rejects_mixed_family():
    """A family that mixes rules is a provenance error — fail loud, not silent.

    The preimage is a per-CITY property; a mixed family would integrate part of
    the bins under the wrong preimage. The integrator must refuse it.
    """
    mixed = [
        AifsTemperatureBin("a", upper_c=25.0, center_c=24.0, rounding_rule="oracle_truncate"),
        AifsTemperatureBin("b", lower_c=26.0, center_c=27.0, rounding_rule="wmo_half_up"),
    ]
    with pytest.raises(ValueError, match="mixes settlement rounding rules"):
        _family_rounding_rule(mixed)


def test_fused_q_bounds_respect_hk_truncation_rule():
    """q_lcb/q_ucb bootstrap under HK truncation differs from WMO and shifts down.

    Boundary invariant: the bound bootstrap (which builds the q_lcb authority the
    live trade gate uses) must integrate under the bins' declared rule. For a bin
    ABOVE the center, truncation produces a LOWER q_ucb than WMO — exactly the
    correction that protected the live HK 30°C BUY-NO position's edge.
    """
    bins = _hk_bins()
    mu, center_sigma, pred_sigma = 27.0, 1.0, 1.6
    q_point = {b.bin_id: 0.25 for b in bins}
    lcb_wmo, ucb_wmo = _build_fused_q_bounds(
        mu_star=mu, center_sigma_c=center_sigma, predictive_sigma_c=pred_sigma,
        bins=bins, half_step=0.5, q_point=q_point, rounding_rule="wmo_half_up",
    )
    lcb_trc, ucb_trc = _build_fused_q_bounds(
        mu_star=mu, center_sigma_c=center_sigma, predictive_sigma_c=pred_sigma,
        bins=bins, half_step=0.5, q_point=q_point, rounding_rule="oracle_truncate",
    )
    # The "hi" open-high shoulder (>=28, above center 27): truncation moves mass
    # DOWN out of it, so its upper-confidence bound drops.
    assert ucb_trc["hi"] < ucb_wmo["hi"]
    # And the rules genuinely differ (not a no-op pass-through).
    assert ucb_trc != ucb_wmo
