# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §4 (belief/executable/portfolio spaces) +
#   §5.6 (recommended q_lcb formula) + §9 Hidden #2/#3/#4 + §12.B (q_lcb math tests) +
#   §14.4 (split probability from edge) + operator directive 2026-06-08
#   (S2: q_lcb from side probability samples via ProbabilityUncertainty, native-NO
#   authority = 1 - q_ucb_yes, single primary live path).
"""S2 cross-module relationship tests — q_lcb is born from PROBABILITY samples.

These are RELATIONSHIP tests (Fitz methodology): they assert a property that holds
ACROSS the module boundary where market_analysis's per-bin YES probability samples
(``MarketAnalysis.bin_yes_probability_samples``) flow into the q_lcb authority
(``event_reactor_adapter._side_q_lcb_from_yes_samples`` ->
``probability_uncertainty.probability_uncertainty_from_samples`` /
``no_side_samples``). The disease they pin closed:

  * Hidden #2 — q_lcb was restored as ``edge_ci_lower + cost`` (NOT a probability
    lower bound). The seam now derives q_lcb from the probability samples ALONE.
  * Hidden #3 — q_lcb_no was the point-complement ``1 - q_lcb_yes``. The seam now
    uses ``1 - q_ucb_yes`` (the lower tail of the complement samples).
  * Hidden #4 — buy_no carried no native authority. It now carries a real one.

The three directive-named relationship tests are:
  test_q_lcb_no_not_one_minus_q_lcb_yes        (§12.B.1 / Hidden #3)
  test_edge_ci_lower_separate_from_q_lcb        (§12.B.2 / Hidden #2)
  test_q_lcb_le_q_point_invariant               (proof-boundary invariant, both sides)
"""
from __future__ import annotations

import numpy as np

from src.contracts.forecast_sharpness import ForecastSharpnessEvidence
from src.engine.event_reactor_adapter import _side_q_lcb_from_yes_samples
from src.strategy.market_analysis import MarketAnalysis
from src.strategy.probability_uncertainty import (
    edge_lcb,
    lower_quantile,
    no_side_samples,
    probability_uncertainty_from_samples,
    upper_quantile,
)
from src.types.market import Bin


def _analysis(*, members, p_market=0.15, rng_seed=42, representativeness_sigma=0.0):
    """A 2-bin executable MarketAnalysis whose YES bootstrap samples are real."""
    bins = [
        Bin(low=None, high=28.0, unit="C", label="28C or below"),
        Bin(low=28.0, high=None, unit="C", label="29C or above"),
    ]
    return MarketAnalysis(
        forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="F"),
        p_raw=np.array([0.75, 0.25]),
        p_cal=np.array([0.75, 0.25]),
        p_market=np.array([p_market, p_market]),
        p_market_no=np.array([p_market, p_market]),
        buy_no_quote_available=np.array([True, True]),
        alpha=0.0,
        bins=bins,
        member_maxes=np.asarray(members, dtype=float),
        executable_mask=np.array([True, True]),
        rng_seed=rng_seed,
        representativeness_sigma=representativeness_sigma,
    )


# ── §12.B.1 / Hidden #3 ──────────────────────────────────────────────────────
def test_q_lcb_no_not_one_minus_q_lcb_yes():
    """With asymmetric YES samples the native-NO q_lcb is the complement QUANTILE
    (1 - q_ucb_yes), not the point-complement (1 - q_lcb_yes).

    Cross-module: the YES samples come from the REAL MarketAnalysis bootstrap; the
    q_lcb authority comes from the reactor seam.
    """
    # Members spread ACROSS the 28C boundary so the bootstrap has genuine asymmetric
    # variance (the 5th and 95th percentiles of q_yes are not mirror images).
    a = _analysis(members=[25.0, 26.0, 27.0, 28.0, 29.0, 30.0], rng_seed=7)
    yes_samples = a.bin_yes_probability_samples(0, 4000)
    yes_point = float(a.p_posterior[0])

    q_lcb_yes, q_lcb_no = _side_q_lcb_from_yes_samples(yes_samples, q_yes_point=yes_point)

    # The blessed identity: q_lcb_no == lower_quantile(1 - q_yes) == 1 - q_ucb_yes.
    expected_no = lower_quantile(no_side_samples(yes_samples))
    assert abs(q_lcb_no - expected_no) < 1e-9
    assert abs(q_lcb_no - (1.0 - upper_quantile(yes_samples))) < 1e-9

    # And it is NOT the point-complement of the YES lower bound (Hidden #3).
    point_complement = 1.0 - q_lcb_yes
    assert abs(q_lcb_no - point_complement) > 1e-3, (
        f"q_lcb_no={q_lcb_no:.6f} collapsed onto 1 - q_lcb_yes={point_complement:.6f} "
        "— the forbidden point complement (Hidden #3)."
    )


# ── §12.B.2 / Hidden #2 ──────────────────────────────────────────────────────
def test_edge_ci_lower_separate_from_q_lcb():
    """Adding price/cost-sample uncertainty WIDENS edge_lcb but leaves q_lcb (a
    probability-only bound) UNCHANGED — they are separate authorities (Hidden #2).

    q_lcb is a pure function of the YES probability samples; edge_lcb is a function
    of the JOINT (q - cost) samples. Price uncertainty cannot move q_lcb.
    """
    a = _analysis(members=[25.5, 26.0, 26.5, 27.0, 27.5], rng_seed=11)
    yes_samples = a.bin_yes_probability_samples(0, 4000)
    yes_point = float(a.p_posterior[0])

    q_lcb_tight, _ = _side_q_lcb_from_yes_samples(yes_samples, q_yes_point=yes_point)

    rng = np.random.default_rng(99)
    cost_point = 0.15
    # (a) zero price uncertainty: cost is a constant.
    cost_samples_tight = np.full_like(yes_samples, cost_point)
    # (b) nonzero price uncertainty: cost jitters around the same mean.
    cost_samples_wide = cost_point + rng.normal(0.0, 0.05, size=yes_samples.shape)
    cost_samples_wide = np.clip(cost_samples_wide, 1e-6, 1.0 - 1e-6)

    edge_lcb_tight = edge_lcb(yes_samples, cost_samples_tight)
    edge_lcb_wide = edge_lcb(yes_samples, cost_samples_wide)

    # q_lcb does NOT depend on cost samples at all — recompute from the SAME yes
    # samples and confirm byte-equality regardless of the cost distribution.
    q_lcb_again, _ = _side_q_lcb_from_yes_samples(yes_samples, q_yes_point=yes_point)
    assert q_lcb_again == q_lcb_tight, "q_lcb moved when only cost uncertainty changed (Hidden #2)"

    # Price uncertainty widens (lowers) the edge lower bound.
    assert edge_lcb_wide < edge_lcb_tight - 1e-6, (
        f"edge_lcb did not widen with price uncertainty: wide={edge_lcb_wide:.6f} "
        f">= tight={edge_lcb_tight:.6f}"
    )


# ── proof-boundary invariant (both sides) ────────────────────────────────────
def test_q_lcb_le_q_point_invariant():
    """At the proof boundary, q_lcb <= q_point holds for BOTH sides — the
    NativeSideCandidate.tradeable / ProbabilityUncertainty invariant (Hidden #2).

    Tested across a sweep of member configurations including a near-saturated bin
    (where the OLD edge-restore could invert the bound).
    """
    for members, seed in (
        ([20.0, 21.0, 22.0, 23.0, 24.0], 1),   # deep-OTM (q_yes ~ 0)
        ([25.5, 26.0, 26.5, 27.0, 27.5], 2),   # mid
        ([29.0, 29.5, 30.0, 30.5, 31.0], 3),   # near-saturated (q_yes ~ 1)
    ):
        a = _analysis(members=members, rng_seed=seed)
        yes_samples = a.bin_yes_probability_samples(0, 4000)
        yes_point = float(a.p_posterior[0])
        no_point = 1.0 - yes_point

        q_lcb_yes, q_lcb_no = _side_q_lcb_from_yes_samples(yes_samples, q_yes_point=yes_point)

        assert q_lcb_yes <= yes_point + 1e-9, (
            f"members={members}: q_lcb_yes={q_lcb_yes:.6f} > yes_point={yes_point:.6f}"
        )
        assert q_lcb_no <= no_point + 1e-9, (
            f"members={members}: q_lcb_no={q_lcb_no:.6f} > no_point={no_point:.6f}"
        )
        assert 0.0 <= q_lcb_yes <= 1.0 and 0.0 <= q_lcb_no <= 1.0

        # The NativeSideCandidate.tradeable factory must accept these (no raise) —
        # the contract that enforces q_lcb <= q_point would reject an inverted bound.
        from src.contracts.native_side_candidate import SideProbability

        SideProbability(side="YES", q_point=yes_point, q_lcb=q_lcb_yes)
        SideProbability(side="NO", q_point=no_point, q_lcb=q_lcb_no)


# ── ONE sample-producing path (no parallel mechanism) ────────────────────────
def test_q_lcb_and_edge_ci_share_one_sample_path():
    """The FDR edge CI (_bootstrap_bin) and the q_lcb authority consume the SAME
    per-bin YES probability samples — one producer, not two.

    At zero cost-uncertainty the edge CI lower bound + cost equals the q_lcb-space
    lower quantile of the SAME samples (the algebraic identity the OLD restore
    relied on). S2 keeps the producer single; it just stops calling the edge value
    a probability bound.
    """
    a = _analysis(members=[25.0, 26.0, 27.0, 28.0, 29.0], p_market=0.15, rng_seed=5)
    yes_samples = a.bin_yes_probability_samples(0, 4000)
    ci_lo, _ci_hi, _pv = a._bootstrap_bin(0, 4000)

    # edge CI lower + fixed cost == probability lower quantile of the SAME samples.
    restored = ci_lo + 0.15
    direct = lower_quantile(yes_samples)
    assert abs(restored - direct) < 1e-6, (
        f"edge-CI restore ({restored:.6f}) and sample lower-quantile ({direct:.6f}) "
        "diverged — the two paths are not drawing the SAME samples."
    )

    # The q_lcb the seam reports equals min(that lower quantile, q_point) — a TRUE
    # probability bound, never edge_ci_lower masquerading.
    yes_point = float(a.p_posterior[0])
    pu = probability_uncertainty_from_samples(yes_samples)
    q_lcb_yes, _ = _side_q_lcb_from_yes_samples(yes_samples, q_yes_point=yes_point)
    assert abs(q_lcb_yes - min(pu.q_lcb, yes_point)) < 1e-9
