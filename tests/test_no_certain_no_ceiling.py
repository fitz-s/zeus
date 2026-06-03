# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: operator pre-arm blocker 2026-06-03 (#89 coverage license DENIED) —
#   the deep buy_no q_lcb tail claims ~0.93 but realizes 0.645 (28-pt over-confidence). For a
#   far bin the MC almost never lands in it, so percentile(edges,5) -> q_no_point ~ (1-p_post)
#   even after sigma_repr is folded into the noise (empirically: q_no_lcb pinned at the legacy
#   ceiling for sigma in {0,1,2,3} at >6C distance). A mean-only bias correction cannot make a
#   far bin a "certain not-settle" when the irreducible residual sigma is ~2C. This pins the
#   structural antibody (iron rule 5): on the CORRECTED domain a buy_no q_lcb of 1.0 must be
#   UNCONSTRUCTABLE. The ceiling is the HONEST Gaussian YES-floor of the bin given sigma_repr
#   and the member-mean distance: q_no_lcb <= 1 - P(settlement in bin | mean, sigma_repr).
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
#   verify ceiling is capped at 1 - gaussian_bin_floor(mean_distance, sigma_repr).
"""No certain NO ceiling on the bias-corrected domain.

Contract:
  1. sigma_repr=0 -> q_no_lcb ceiling is byte-identical to legacy (1 - p_posterior); the
     existing #129 clamp is unchanged (no correction => no widening, ever).
  2. sigma_repr>0 -> the restored q_no_lcb is clamped to 1 - YES_floor where YES_floor is the
     HONEST Gaussian mass of the bin given the member mean and sigma_repr. It can NEVER be 1.0.
  3. At a realistic deep-NO distance (~3-4C, the q≈0.93 regime) the haircut is MATERIAL.
  4. A larger sigma_repr lowers the ceiling further (more irreducible YES mass).
  5. The POINT q (p_posterior) is unchanged — only the lower bound is clamped.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.strategy.market_analysis import MarketAnalysis
from src.types.market import Bin


def _q_no_lcb(a: MarketAnalysis, bin_idx: int, n: int = 6000) -> float:
    ci_lo, _ci_hi, _p = a._bootstrap_bin_no(bin_idx, n)
    return ci_lo + float(a.buy_no_market_price(bin_idx))


def _city(*, edge: float, sigma: float, members: np.ndarray, rng_seed: int = 42,
          p_far: float = 0.001) -> MarketAnalysis:
    """Binary market: near bin [.., edge) and a FAR open-high bin [edge, inf).

    p_far sets the far bin's p_cal/p_posterior; default 0.001 is the genuine deep-NO tail
    (q_no_point ≈ 0.999) where the legacy clamp saturates.
    """
    bins = [
        Bin(low=None, high=edge, unit="C", label=f"{edge:.0f}C or below"),
        Bin(low=edge, high=None, unit="C", label=f"{edge + 1.0:.0f}C or above"),
    ]
    p_near = 1.0 - p_far
    return MarketAnalysis(
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
        # The structural "no certain NO" guarantee: for ANY finite σ_repr the Gaussian YES-floor
        # is strictly > 0, so q_no_lcb < 1.0 — even at extreme distance (9C). The ceiling makes a
        # corrected-domain q_no_lcb of exactly 1.0 UNCONSTRUCTABLE.
        a = _city(edge=34.0, sigma=2.0, members=_TIGHT_MEMBERS)
        assert _q_no_lcb(a, FAR_BIN) < 1.0, "deep buy_no q_lcb reached exactly 1.0 (certain NO)"

    def test_ceiling_de_saturates_deep_tail_where_sigma_alone_does_NOT(self):
        # THE load-bearing case (#89). At ~6C distance the member-resampling bootstrap almost
        # never reaches the far bin even with σ_repr=2C, so σ ALONE leaves q_no_lcb pinned at the
        # legacy ceiling (~0.999). The structural ceiling subtracts the honest Gaussian YES-floor
        # (1-Φ(6/2)=0.00135 -> cap 0.99865), de-saturating the deep tail the noise cannot reach.
        a = _city(edge=31.0, sigma=2.0, members=_TIGHT_MEMBERS)
        q = _q_no_lcb(a, FAR_BIN)
        legacy_ceiling = 1.0 - float(a.p_posterior[FAR_BIN])  # ≈ 0.999
        assert q < legacy_ceiling, (
            f"ceiling did NOT de-saturate the deep tail: q_no_lcb={q:.6f} >= legacy "
            f"{legacy_ceiling:.6f} (σ_repr=2C is too far to reach the bin via noise)"
        )
        assert q == pytest.approx(legacy_ceiling - a._no_certain_yes_floor(FAR_BIN), abs=2e-3)

    def test_material_haircut_at_realistic_deep_no_distance(self):
        # ~3C distance, σ_repr=2C: the q≈0.93 regime the operator cited. The honest Gaussian
        # YES-floor is ~0.067 (ceiling 0.933); the member-resampling noise at this closer
        # distance does even better, so q_no_lcb lands MATERIALLY below the legacy near-certain
        # ceiling. Either way the over-confident ~0.93+ claim is gone.
        a = _city(edge=28.0, sigma=2.0, members=_TIGHT_MEMBERS)
        q = _q_no_lcb(a, FAR_BIN)
        assert q < 0.93, (
            f"q_no_lcb={q:.4f} not de-confidenced below the honest 0.93 Gaussian floor at 3C/2σ"
        )

    def test_larger_repr_sigma_lowers_the_ceiling_more(self):
        # At 6C distance the ceiling (not the noise) binds, so larger σ -> larger YES-floor ->
        # lower ceiling -> lower q_no_lcb. Isolates the ceiling's σ-monotonicity.
        q_small = _q_no_lcb(_city(edge=31.0, sigma=2.0, members=_TIGHT_MEMBERS), FAR_BIN)
        q_large = _q_no_lcb(_city(edge=31.0, sigma=3.0, members=_TIGHT_MEMBERS), FAR_BIN)
        assert q_large < q_small, (
            f"larger σ_repr did not lower the deep-tail ceiling: q(3.0)={q_large:.5f} >= "
            f"q(2.0)={q_small:.5f}"
        )

    def test_zero_repr_sigma_ceiling_is_legacy_identical(self):
        # sigma_repr=0 must reproduce the legacy clamp exactly (no ceiling applied at all):
        # _no_certain_yes_floor returns 0.0, so the NO ceiling stays at 1 - p_posterior.
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
        # When the member mean sits NEAR the bin edge, the Gaussian YES-floor is large (~0.5),
        # so 1 - floor is well BELOW the legacy ceiling. The ceiling takes the TIGHTER (min) of
        # the two, so it must NEVER RAISE q_no_lcb above the legacy clamp — only lower it.
        a = _city(edge=25.0, sigma=2.0, members=_TIGHT_MEMBERS, p_far=0.45)
        q = _q_no_lcb(a, FAR_BIN)
        legacy_ceiling = 1.0 - float(a.p_posterior[FAR_BIN])
        assert q <= legacy_ceiling + 1e-9, (
            f"ceiling RAISED q_no_lcb above legacy: q={q:.4f} legacy={legacy_ceiling:.4f}"
        )
