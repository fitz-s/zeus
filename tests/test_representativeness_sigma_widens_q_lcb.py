# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: operator pre-arm blocker 2026-06-03 — representativeness-VARIANCE
#   term in the live q_lcb path. A mean-only EDLI bias correction shifts ensemble
#   members but does NOT widen spread; the bootstrap CI is then over-confident on
#   corrected cities. With the canary notional cap removed, an HONEST q_lcb is the
#   ONLY protection against overconfidence-ruin (iron rule 6). This test pins the
#   contract: σ_repr injected in quadrature into the MC bootstrap noise widens q_lcb
#   on corrected cities; σ_repr=0 is byte-identical (backward compatible).
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: relationship antibody — representativeness sigma -> wider (lower) q_lcb.
# Reuse: inspect src/engine/event_reactor_adapter.py:_edli_representativeness_sigma_native
#   and src/calibration/ens_error_model.py:full_predictive_residual_sd before re-running;
#   verify sigma_repr path is wired to model_bias_ens.total_residual_sd_c.
"""Representativeness variance widens q_lcb on bias-corrected domain.

The EDLI bias correction (mean-only) subtracts a per-city effective_bias_c from the
member array. The forecast-vs-settlement RESIDUAL std (residual_sd_c) — the irreducible
representativeness uncertainty the mean shift does not capture — must be folded into the
MC resampling noise IN QUADRATURE with the existing instrument/bootstrap sigma so the
lower-confidence-bound (q_lcb) widens honestly. Contract:

  1. σ_repr=0 -> q_lcb bit-identical to the legacy path (backward compatible).
  2. σ_repr>0 -> q_lcb strictly LOWER (more conservative) for the same members.
  3. buy_no q_lcb remains INDEPENDENT (not 1 - buy_yes_lcb) and also widens (#106/#129).
  4. a high-σ city (Seoul 2.5) gets a materially wider CI than a low-σ city (Tokyo 1.7)
     at the same point.

Only the LOWER bound / CI widens; the POINT q (p_posterior) is untouched.
"""
from __future__ import annotations

import numpy as np

from src.strategy.market_analysis import MarketAnalysis
from src.contracts.forecast_sharpness import ForecastSharpnessEvidence
from src.types.market import Bin


def _bins() -> list[Bin]:
    return [
        Bin(low=None, high=26.0, unit="C", label="26C or below"),
        Bin(low=26.0, high=None, unit="C", label="27C or above"),
    ]


def _make_analysis(*, representativeness_sigma: float = 0.0, rng_seed: int = 42,
                   member_maxes=None) -> MarketAnalysis:
    bins = _bins()
    if member_maxes is None:
        member_maxes = np.array([25.5, 25.8, 26.0, 26.3, 26.6])
    # Both YES and NO native markets executable so the NO leg is independently grounded.
    return MarketAnalysis(forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="F"), 
        p_raw=np.array([0.55, 0.45]),
        p_cal=np.array([0.55, 0.45]),
        p_market=np.array([0.30, 0.30]),
        p_market_no=np.array([0.30, 0.30]),
        buy_no_quote_available=np.array([True, True]),
        alpha=0.0,
        bins=bins,
        member_maxes=member_maxes,
        executable_mask=np.array([True, True]),
        rng_seed=rng_seed,
        representativeness_sigma=representativeness_sigma,
    )


# ---------------------------------------------------------------------------
# (b) Backward compatibility: σ_repr=0 -> byte-identical q_lcb
# ---------------------------------------------------------------------------

class TestZeroSigmaIsBitIdentical:
    def test_zero_repr_sigma_matches_legacy_construction(self):
        # A MarketAnalysis built WITHOUT the param at all must equal one built with
        # representativeness_sigma=0.0 at the same seed (the default path).
        bins = _bins()
        common = dict(
            p_raw=np.array([0.55, 0.45]),
            p_cal=np.array([0.55, 0.45]),
            p_market=np.array([0.30, 0.30]),
            p_market_no=np.array([0.30, 0.30]),
            buy_no_quote_available=np.array([True, True]),
            alpha=0.0,
            bins=bins,
            member_maxes=np.array([25.5, 25.8, 26.0, 26.3, 26.6]),
            executable_mask=np.array([True, True]),
            rng_seed=42,
            forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="F"),
        )
        a_legacy = MarketAnalysis(**common)
        a_zero = MarketAnalysis(**common, representativeness_sigma=0.0)
        assert a_legacy._bootstrap_bin(0, 2000) == a_zero._bootstrap_bin(0, 2000)
        assert a_legacy._bootstrap_bin_no(0, 2000) == a_zero._bootstrap_bin_no(0, 2000)


# ---------------------------------------------------------------------------
# (a) σ_repr>0 -> strictly LOWER q_lcb (wider CI) on the YES leg
# ---------------------------------------------------------------------------

class TestReprSigmaWidensYesLcb:
    def test_repr_sigma_lowers_yes_ci_lower(self):
        a_legacy = _make_analysis(representativeness_sigma=0.0, rng_seed=42)
        a_repr = _make_analysis(representativeness_sigma=2.5, rng_seed=42)
        ci_legacy = a_legacy._bootstrap_bin(0, 4000)
        ci_repr = a_repr._bootstrap_bin(0, 4000)
        assert ci_repr[0] < ci_legacy[0], (
            f"σ_repr=2.5 did not widen YES CI: ci_lower_repr={ci_repr[0]:.6f} >= "
            f"ci_lower_legacy={ci_legacy[0]:.6f}"
        )

    def test_larger_repr_sigma_widens_monotonically(self):
        a_small = _make_analysis(representativeness_sigma=0.5, rng_seed=42)
        a_large = _make_analysis(representativeness_sigma=2.5, rng_seed=42)
        ci_small = a_small._bootstrap_bin(0, 4000)
        ci_large = a_large._bootstrap_bin(0, 4000)
        assert ci_large[0] <= ci_small[0]


# ---------------------------------------------------------------------------
# (c) buy_no q_lcb is INDEPENDENT and also widens
# ---------------------------------------------------------------------------

class TestReprSigmaWidensNoLcbIndependently:
    def test_no_ci_lower_widens_with_repr_sigma(self):
        a_legacy = _make_analysis(representativeness_sigma=0.0, rng_seed=42)
        a_repr = _make_analysis(representativeness_sigma=2.5, rng_seed=42)
        no_legacy = a_legacy._bootstrap_bin_no(0, 4000)
        no_repr = a_repr._bootstrap_bin_no(0, 4000)
        assert no_repr[0] < no_legacy[0], (
            f"σ_repr=2.5 did not widen NO CI: ci_lower_repr={no_repr[0]:.6f} >= "
            f"ci_lower_legacy={no_legacy[0]:.6f}"
        )

    def test_no_lcb_is_not_one_minus_yes_lcb(self):
        # Independence (#106/#129): the NO lower bound is its own bootstrap of
        # (1 - p_post_yes) - c_no, NOT the algebraic complement 1 - yes_lcb.
        a = _make_analysis(representativeness_sigma=2.5, rng_seed=42)
        yes_ci = a._bootstrap_bin(0, 4000)
        no_ci = a._bootstrap_bin_no(0, 4000)
        # Restore probability-space q_lcb (adapter adds back the fixed cost c_b).
        c_yes = 0.30
        c_no = 0.30
        yes_q_lcb = yes_ci[0] + c_yes
        no_q_lcb = no_ci[0] + c_no
        # If NO were the algebraic complement of YES, no_q_lcb would equal 1 - yes_q_lcb.
        assert abs(no_q_lcb - (1.0 - yes_q_lcb)) > 1e-6, (
            "buy_no q_lcb must be independently grounded, not 1 - buy_yes_lcb"
        )


# ---------------------------------------------------------------------------
# (d) high-σ city gets a materially wider CI than low-σ city at the same point
# ---------------------------------------------------------------------------

class TestHighSigmaCityWiderThanLowSigmaCity:
    def test_seoul_wider_than_tokyo_at_same_members(self):
        # Same members (same point posterior), different representativeness σ:
        # Seoul residual_sd ~2.5C, Tokyo ~1.7C. Seoul's q_lcb must be lower (wider).
        # A WIDE bin (boundary 28C) with members spread across it gives the bootstrap
        # resampling genuine variance and keeps the 5th percentile below the
        # point_edge_ceiling clamp (q_lcb <= q_point) so both σ values do not saturate
        # the same ceiling. Cost kept low (0.15) so the YES ceiling has headroom.
        bins = [
            Bin(low=None, high=28.0, unit="C", label="28C or below"),
            Bin(low=28.0, high=None, unit="C", label="29C or above"),
        ]
        members = np.array([23.0, 24.0, 25.0, 26.0, 27.0])

        def _city(sig: float) -> MarketAnalysis:
            return MarketAnalysis(forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="F"), 
                p_raw=np.array([0.75, 0.25]),
                p_cal=np.array([0.75, 0.25]),
                p_market=np.array([0.15, 0.15]),
                p_market_no=np.array([0.15, 0.15]),
                buy_no_quote_available=np.array([True, True]),
                alpha=0.0,
                bins=bins,
                member_maxes=members,
                executable_mask=np.array([True, True]),
                rng_seed=42,
                representativeness_sigma=sig,
            )

        seoul_ci = _city(2.5)._bootstrap_bin(0, 4000)
        tokyo_ci = _city(1.7)._bootstrap_bin(0, 4000)
        assert seoul_ci[0] < tokyo_ci[0], (
            f"high-σ (Seoul 2.5) CI not wider than low-σ (Tokyo 1.7): "
            f"seoul_lcb={seoul_ci[0]:.6f} tokyo_lcb={tokyo_ci[0]:.6f}"
        )
        # Materiality: the gap should be a non-trivial fraction of a probability point,
        # not bootstrap jitter. The 0.8C σ gap on this wide bin yields ~0.05 separation.
        assert (tokyo_ci[0] - seoul_ci[0]) > 0.003, (
            f"Seoul-vs-Tokyo CI gap not material: {tokyo_ci[0] - seoul_ci[0]:.6f}"
        )

    def test_point_posterior_unchanged_by_repr_sigma(self):
        # The POINT q (p_posterior) must NOT move with σ_repr — only the CI widens.
        a_legacy = _make_analysis(representativeness_sigma=0.0, rng_seed=42)
        a_repr = _make_analysis(representativeness_sigma=2.5, rng_seed=42)
        assert np.allclose(a_legacy.p_posterior, a_repr.p_posterior)
