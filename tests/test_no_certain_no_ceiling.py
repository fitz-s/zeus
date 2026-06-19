# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: operator pre-arm blocker 2026-06-03 (#89 coverage license DENIED) —
#   the deep buy_no q_lcb tail claims ~0.93 but realizes 0.645 (28-pt over-confidence). For a
#   far bin the MC almost never lands in it. A mean-only bias correction cannot make a
#   far bin a "certain not-settle" when the irreducible residual sigma is ~2C. This pins the
#   structural antibody (iron rule 5): on the CORRECTED domain a buy_no q_lcb of 1.0 must be
#   UNCONSTRUCTABLE. With native NO quote evidence, NO confidence is live, but it is
#   capped by the same representativeness floor and cannot exceed the held-side point.
#
#   ANTI-P-HACKING: the floor is the GENUINE Gaussian tail mass, not an invented constant. At
#   3-4C distance (where deep-NO candidates actually live) the floor is material (~0.07 -> cap
#   ~0.93, matching the observed over-confident claim). At extreme distance (9C) the honest
#   floor is microscopic (~3e-6) — the ceiling still forbids exactly 1.0 but does not pretend
#   a 9C-away bin carries material settle risk. Honest, not tuned-to-pass.
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: structural antibody — no certain NO on the bias-corrected domain.
# Reuse: inspect src/engine/event_reactor_adapter.py:_edli_q_no_lcb_ceiling and
#   src/calibration/ens_error_model.py:gaussian_bin_floor before re-running;
#   verify NO confidence stays unavailable until native NO evidence exists.
"""No certain NO ceiling on the bias-corrected domain.

Contract:
  1. sigma_repr=0 -> NO bootstrap remains bounded by held-side point probability.
  2. sigma_repr>0 -> buy_no q_lcb is live only with native NO quote evidence.
  3. Representativeness sigma may only reduce/widen NO confidence, not inflate it.
  4. The POINT q (p_posterior) is unchanged.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.strategy.market_analysis import MarketAnalysis
from src.contracts.forecast_sharpness import ForecastSharpnessEvidence
from src.types.market import Bin


def _q_no_lcb(a: MarketAnalysis, bin_idx: int, n: int = 6000) -> float:
    ci_lo, _ci_hi, _p = a._bootstrap_bin_no(bin_idx, n)
    return ci_lo + float(a.buy_no_market_price(bin_idx))


def _city(*, edge: float, sigma: float, members: np.ndarray, rng_seed: int = 42,
          p_near: float = 0.999, p_far: float = 0.001) -> MarketAnalysis:
    """Binary market: near bin [.., edge) and a FAR open-high bin [edge, inf).

    p_far sets the far bin's p_cal/p_posterior; default 0.001 is the genuine deep-NO tail.
    """
    bins = [
        Bin(low=None, high=edge, unit="C", label=f"{edge:.0f}C or below"),
        Bin(low=edge, high=None, unit="C", label=f"{edge + 1.0:.0f}C or above"),
    ]
    return MarketAnalysis(forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="F"), 
        p_raw=np.array([p_near, p_far]),
        p_cal=np.array([p_near, p_far]),
        p_market=np.array([0.50, 0.02]),
        p_market_no=np.array([0.50, 0.02]),
        buy_no_quote_available=np.array([True, True]),
        alpha=0.0,
        bins=bins,
        member_maxes=members,
        executable_mask=np.array([True, True]),
        rng_seed=rng_seed,
        representativeness_sigma=sigma,
    )


# Members tightly clustered at 25C so the bootstrap member-resampling variance is small and
# the SATURATION (q_no_lcb -> ceiling) is driven by bin distance, not member spread.
_TIGHT_MEMBERS = np.array([24.9, 24.95, 25.0, 25.05, 25.1])
FAR_BIN = 1


# p_posterior on the far bin is set to ~0.001 so q_no_point ≈ 0.999 — the genuine deep-NO
# tail where the legacy clamp saturates. The far bin's p_cal is 0.001 (see _city below).


class TestNoCertainNoCeiling:
    def test_q_no_lcb_can_never_reach_one_with_repr_sigma(self):
        # The structural "no certain NO" guarantee: corrected-domain buy_no
        # q_lcb is live but never reaches certainty or exceeds the point NO prob.
        a = _city(edge=34.0, sigma=2.0, members=_TIGHT_MEMBERS)
        q = _q_no_lcb(a, FAR_BIN)
        assert 0.0 < q < 1.0
        assert q <= 1.0 - float(a.p_posterior[FAR_BIN]) + 1e-9

    def test_deep_tail_no_confidence_is_capped_by_representativeness_floor(self):
        a = _city(edge=31.0, sigma=2.0, members=_TIGHT_MEMBERS)
        q = _q_no_lcb(a, FAR_BIN)
        assert q <= 1.0 - a._no_certain_yes_floor(FAR_BIN) + 1e-9
        assert q <= 1.0 - float(a.p_posterior[FAR_BIN]) + 1e-9

    def test_material_haircut_at_realistic_deep_no_distance(self):
        # ~3C distance, σ_repr=2C: the q≈0.93 regime the operator cited.
        # Native NO is usable, but the complement-sample lower tail materially haircuts it.
        a = _city(edge=28.0, sigma=2.0, members=_TIGHT_MEMBERS)
        q = _q_no_lcb(a, FAR_BIN)
        assert 0.0 < q < 0.95
        assert q <= 1.0 - a._no_certain_yes_floor(FAR_BIN) + 1e-9

    def test_larger_repr_sigma_reduces_no_confidence(self):
        q_small = _q_no_lcb(_city(edge=31.0, sigma=2.0, members=_TIGHT_MEMBERS), FAR_BIN)
        q_large = _q_no_lcb(_city(edge=31.0, sigma=3.0, members=_TIGHT_MEMBERS), FAR_BIN)
        assert 0.0 < q_large <= q_small

    def test_zero_repr_sigma_ceiling_is_legacy_identical(self):
        # sigma_repr=0 keeps the NO bootstrap deterministic across construction paths.
        bins = [
            Bin(low=None, high=28.0, unit="C", label="28C or below"),
            Bin(low=28.0, high=None, unit="C", label="29C or above"),
        ]
        common = dict(
            p_raw=np.array([0.999, 0.001]),
            p_cal=np.array([0.999, 0.001]),
            p_market=np.array([0.50, 0.02]),
            p_market_no=np.array([0.50, 0.02]),
            buy_no_quote_available=np.array([True, True]),
            alpha=0.0,
            bins=bins,
            member_maxes=_TIGHT_MEMBERS,
            executable_mask=np.array([True, True]),
            rng_seed=42,
            forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="F"),
        )
        a_legacy = MarketAnalysis(**common)
        a_zero = MarketAnalysis(**common, representativeness_sigma=0.0)
        assert a_zero._no_certain_yes_floor(FAR_BIN) == 0.0
        assert a_legacy._bootstrap_bin_no(FAR_BIN, 4000) == a_zero._bootstrap_bin_no(FAR_BIN, 4000)

    def test_point_posterior_unchanged_by_ceiling(self):
        a_legacy = _city(edge=28.0, sigma=0.0, members=_TIGHT_MEMBERS)
        a_repr = _city(edge=28.0, sigma=2.0, members=_TIGHT_MEMBERS)
        assert np.allclose(a_legacy.p_posterior, a_repr.p_posterior)

    def test_near_bin_ceiling_does_not_raise_lcb(self):
        # When the member mean sits near the bin edge, NO confidence is live but
        # strongly haircut by complement samples and representativeness.
        a = _city(edge=25.0, sigma=2.0, members=_TIGHT_MEMBERS, p_near=0.55, p_far=0.45)
        q = _q_no_lcb(a, FAR_BIN)
        assert 0.0 <= q < 1.0 - float(a.p_posterior[FAR_BIN])
