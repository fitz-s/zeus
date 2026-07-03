# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §12.D + §3 (ΔU objective) + §5.3 (cost-curve ELG)
#                  + §4 (native YES/NO belief separation) + §6 (best-candidate
#                  selection) + §11 (Phase 4) + §14.7
#                  + Hidden #3 (NATIVE NO conservatism: score NO with its own
#                    robust NO q_lcb = 1 − q_ucb_yes, not 1 − q_lcb_yes)
#                  + Hidden #5 (outcome-not-leg normalization) + Hidden #10
#                  (central-NO broad correlated exposure) + operator directive 2026-06-08
"""Relationship tests for the robust marginal-utility ranker (spec Phase 4).

These are RELATIONSHIP tests, not function tests (project methodology: test the
cross-module invariant that holds when a candidate's depth-walked cost curve and
its robust probability flow THROUGH a family payoff matrix into a marginal
log-utility score). Each test pins a property the spec §6 lists as a reason the
utility ranker beats simpler rules:

  D.1 test_significant_but_dominated_candidate_rejected
        Two FDR-passing candidates; the one with HIGHER marginal log utility
        wins even though the loser also has positive edge. (spec §6, §12.D.1,
        §10 family-preselection "dominated candidate rejected".)

  D.2 test_lower_q_higher_log_utility_selected
        A lower-q but UNDERPRICED candidate beats a higher-q OVERPRICED one.
        Utility — not probability — ranks. (spec §6 "highest q may be
        overpriced"; §12.D.2.)

  D.3 test_central_no_vs_adjacent_yes_tradeoff
        NO_i (central, broad correlated exposure) vs YES_{i+1} (adjacent) are
        compared THROUGH THE SAME family payoff matrix. (Hidden #10; §12.D.3.)

  D.4 test_existing_exposure_lowers_marginal_utility
        A_y reflecting existing/pending exposure shrinks the optimal stake (or
        no-trades). (spec §3 A_y baseline; §12.C.5 / §12.D; §6.)

  D.5 test_unrepresented_outcome_not_inflated
        A high-probability outcome with NO candidate leg does NOT inflate a
        candidate's utility, because the matrix enumerates ALL bins + outside
        and π is normalized over that full outcome set. (Hidden #5; §12.D.)

  D.6 test_no_candidate_scored_with_own_robust_no_qlcb
        A NATIVE NO candidate is scored with its OWN robust NO q_lcb
        (= 1 − q_ucb_yes), NOT the looser 1 − q_lcb_yes the shared YES π would
        imply. Scoring NO via the shared 1 − q_lcb_yes win-mass is strictly more
        optimistic than the conservative own-q_lcb_no path the ranker must use.
        (spec §3 / §4 / §9 Hidden #3 "NO overconfidence".)

These exercise pure objects and pure functions. No live decision path is touched.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from src.contracts.executable_cost_curve import (
    BookLevel,
    ExecutableCostCurve,
    FeeModel,
)
from src.contracts.native_side_candidate import NativeSideCandidate
from src.strategy.probability_uncertainty import (
    no_side_samples,
    probability_uncertainty_from_samples,
)
from src.strategy.utility_ranker import (
    OUTSIDE_OUTCOME,
    FamilyPayoffMatrix,
    PortfolioExposureVector,
    effective_outcome_pi,
    rank_candidates,
    robust_probabilities,
    score_candidate,
)

FAMILY = "city=NYC|date=2026-06-09|metric=tmax"
FORECAST_SNAP = "fc-1"
MARKET_SNAP = "mkt-1"


# --------------------------------------------------------------------------
# Fixture helpers.
# --------------------------------------------------------------------------
def _curve(price, *, side="YES", token_id="tok", size="100000", fee_rate="0"):
    """Deep single-level BUY curve at all-in ask ``price`` (fee_rate default 0).

    Deep size so the optimizer is never depth-bound in these tests — we are
    isolating the utility relationship, not depth convexity (that lives in the
    Phase-3 cost-curve tests).
    """
    return ExecutableCostCurve(
        token_id=token_id,
        side=side,
        snapshot_id="snap",
        book_hash=f"h-{token_id}-{price}",
        levels=(BookLevel(price=Decimal(price), size=Decimal(size)),),
        fee_model=FeeModel(fee_rate=Decimal(fee_rate)),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=5),
    )


def _yes_candidate(bin_id, *, q_yes_samples, ask, token_id):
    """A tradeable native YES candidate for ``bin_id`` priced at all-in ``ask``."""
    pu = probability_uncertainty_from_samples(q_yes_samples)
    return NativeSideCandidate.tradeable(
        family_key=FAMILY,
        bin_id=bin_id,
        side="YES",
        token_id=token_id,
        condition_id=f"cond-{bin_id}",
        q_point=pu.q_point,
        q_lcb=pu.q_lcb,
        probability_uncertainty=pu,
        executable_cost_curve=_curve(ask, side="YES", token_id=token_id),
        forecast_snapshot_id=FORECAST_SNAP,
        market_snapshot_id=MARKET_SNAP,
        hypothesis_id=f"H-{bin_id}-YES",
    )


def _no_candidate(bin_id, *, q_yes_samples, ask, token_id):
    """A tradeable native NO candidate for ``bin_id`` priced at all-in ``ask``.

    NO probability authority uses the per-sample YES complement (spec §4 /
    Hidden #3) — never ``1 - q_lcb_yes``.
    """
    no_samples = no_side_samples(q_yes_samples)
    pu = probability_uncertainty_from_samples(no_samples)
    return NativeSideCandidate.tradeable(
        family_key=FAMILY,
        bin_id=bin_id,
        side="NO",
        token_id=token_id,
        condition_id=f"cond-{bin_id}",
        q_point=pu.q_point,
        q_lcb=pu.q_lcb,
        probability_uncertainty=pu,
        executable_cost_curve=_curve(ask, side="NO", token_id=token_id),
        forecast_snapshot_id=FORECAST_SNAP,
        market_snapshot_id=MARKET_SNAP,
        hypothesis_id=f"H-{bin_id}-NO",
    )


def _samples_centered(p, *, spread=0.02, n=400):
    """Deterministic symmetric bootstrap samples centered near ``p``.

    A tight, symmetric cloud so q_point ~= p and q_lcb is a stable function of
    ``spread``. Deterministic (no RNG) so the relationship tests are exact.
    """
    import numpy as np

    half = np.linspace(0.0, spread, n // 2)
    s = np.concatenate([p - half, p + half])
    return np.clip(s, 0.0, 1.0)


# Three-bin family: bins B0, B1, B2. Outcome set is {B0, B1, B2, OUTSIDE}.
BINS = ["B0", "B1", "B2"]


def _matrix(bins=BINS):
    return FamilyPayoffMatrix.over_bins(bins)


# ==========================================================================
# D.5 — Hidden #5: an unrepresented high-prob outcome does NOT inflate utility.
# ==========================================================================
def test_unrepresented_outcome_not_inflated():
    """A high-prob outcome with no candidate leg must not inflate utility.

    Hidden #5: if the matrix normalized over candidate LEGS instead of
    settlement OUTCOMES, a YES_i candidate's "lose elsewhere" mass would be
    understated (the unrepresented high-prob outcome dropped), inflating ΔU.

    Construction: B1 carries ~70% of the probability mass and has NO candidate
    leg at all. We score a YES_B0 candidate (B0 ~ 15%). With the full outcome
    set, YES_B0's win probability is small and its lose-mass (incl. the
    unrepresented B1) is large, so ΔU is small/negative. If the matrix dropped
    B1, the same candidate would look far better. We assert the WITH-B1 score is
    strictly lower than the buggy DROP-B1 score, and is non-positive here.
    """
    # YES samples per bin (point ~ center). B1 dominates the family mass.
    qy = {
        "B0": _samples_centered(0.15),
        "B1": _samples_centered(0.70),
        "B2": _samples_centered(0.10),
    }
    cand = _yes_candidate("B0", q_yes_samples=qy["B0"], ask="0.14", token_id="t-b0")

    # CORRECT: matrix + probabilities over the FULL settlement outcome set
    # (all bins + outside). B1's ~70% mass is present even though it has no
    # candidate leg, so YES_B0's win mass stays ~its true ~13% q_lcb and the
    # lose mass (incl. the unrepresented B1) is the full complement.
    full_matrix = _matrix(BINS)
    full_pi = robust_probabilities(full_matrix, per_bin_yes_samples=qy)
    exposure_full = PortfolioExposureVector.flat(full_matrix, baseline=Decimal("1000"))
    score_full = score_candidate(cand, full_matrix, full_pi, exposure_full)

    # BUGGY (Hidden #5): the optimizer normalizes over candidate LEGS, not
    # outcomes. With only YES_B0 as a leg, the buggy matrix has outcome set
    # {B0, OUTSIDE} but the probability vector is renormalized so the
    # REPRESENTED leg carries (almost) all the mass -> YES_B0 looks near-certain.
    drop_matrix = _matrix(["B0"])
    buggy_pi = {"B0": 0.99, OUTSIDE_OUTCOME: 0.01}
    exposure_drop = PortfolioExposureVector.flat(drop_matrix, baseline=Decimal("1000"))
    score_drop = score_candidate(cand, drop_matrix, buggy_pi, exposure_drop)

    # The correctly-normalized (full outcome set) score is strictly lower than
    # the buggy leg-normalized score: the unrepresented B1 mass is NOT inflating
    # YES_B0's utility.
    assert score_full.delta_u < score_drop.delta_u
    # And with ~85% of the mass on other outcomes at a ~breakeven price, the
    # correct robust utility is non-positive -> no-trade.
    assert score_full.delta_u <= 0.0
    assert score_full.is_no_trade
    # The buggy normalization, by contrast, would have called it a strong trade.
    assert score_drop.delta_u > 0.0


# ==========================================================================
# D.2 — utility (not probability) ranks: lower-q underpriced beats higher-q
#       overpriced.
# ==========================================================================
def test_lower_q_higher_log_utility_selected():
    """Lower q but underpriced beats higher q overpriced (spec §6, §12.D.2)."""
    # Candidate A: HIGH q (~0.60) but OVERPRICED (all-in ask 0.58 -> thin edge).
    # Candidate B: LOWER q (~0.45) but UNDERPRICED (all-in ask 0.30 -> fat edge).
    qy = {
        "B0": _samples_centered(0.60),  # A bin
        "B1": _samples_centered(0.45),  # B bin
        "B2": _samples_centered(0.05),
    }
    cand_a = _yes_candidate("B0", q_yes_samples=qy["B0"], ask="0.58", token_id="t-a")
    cand_b = _yes_candidate("B1", q_yes_samples=qy["B1"], ask="0.30", token_id="t-b")

    # Sanity: A really does have the higher probability.
    assert cand_a.q_point > cand_b.q_point

    pi = robust_probabilities(_matrix(BINS), per_bin_yes_samples=qy)
    exposure = PortfolioExposureVector.flat(_matrix(BINS), baseline=Decimal("1000"))

    ranked = rank_candidates([cand_a, cand_b], _matrix(BINS), pi, exposure)
    winner = ranked[0]
    assert winner.candidate.bin_id == "B1", (
        "lower-q underpriced candidate must win on marginal log utility, "
        f"got {winner.candidate.bin_id} (delta_u order: "
        f"{[(s.candidate.bin_id, s.delta_u) for s in ranked]})"
    )


# ==========================================================================
# D.1 — a statistically significant but dominated candidate is rejected.
# ==========================================================================
def test_significant_but_dominated_candidate_rejected():
    """An FDR-passing candidate with positive edge but LOWER marginal utility
    loses to a higher-utility one (spec §6, §12.D.1, §10).

    Both candidates have positive robust edge (q_lcb > cost), so both would pass
    a naive edge/FDR gate. The DOMINATED candidate is deliberately the one with
    the HIGHER probability — so a probability-ranker (the failure mode §6 warns
    against, "highest q may be overpriced") would wrongly pick it. The utility
    ranker must instead pick the lower-q, much-cheaper, dominant candidate and
    rank the high-q dominated one strictly below it. This makes the test
    discriminate utility from probability (not merely re-pick the high-q bin).
    """
    qy = {
        "B0": _samples_centered(0.50),  # dominant: lower q, very cheap
        "B1": _samples_centered(0.55),  # dominated: HIGHER q, but overpriced
        "B2": _samples_centered(0.05),
    }
    # Dominant: lower q (~0.50) but fat edge (ask 0.30).
    dominant = _yes_candidate("B0", q_yes_samples=qy["B0"], ask="0.30", token_id="t-dom")
    # Dominated: HIGHER q (~0.55) but overpriced (ask 0.50) -> thin edge.
    dominated = _yes_candidate("B1", q_yes_samples=qy["B1"], ask="0.50", token_id="t-sub")

    pi = robust_probabilities(_matrix(BINS), per_bin_yes_samples=qy)
    exposure = PortfolioExposureVector.flat(_matrix(BINS), baseline=Decimal("1000"))

    # The dominated candidate has the HIGHER probability (so a probability sort
    # would pick it) yet positive robust edge (the "significant" precondition).
    assert dominated.q_point > dominant.q_point
    assert dominant.q_lcb > 0.30
    assert dominated.q_lcb > 0.50

    ranked = rank_candidates([dominated, dominant], _matrix(BINS), pi, exposure)
    assert ranked[0].candidate.bin_id == "B0", (
        "utility ranker must reject the higher-q dominated candidate and pick "
        "the lower-q dominant one; a probability sort would do the opposite"
    )
    # Both positive-utility, but the high-q dominated one is strictly below.
    by_bin = {s.candidate.bin_id: s for s in ranked}
    assert by_bin["B0"].delta_u > by_bin["B1"].delta_u > 0.0


# ==========================================================================
# D.3 — central NO vs adjacent YES, through the SAME payoff matrix (Hidden #10).
# ==========================================================================
def test_central_no_vs_adjacent_yes_tradeoff():
    """NO_i vs YES_{i+1} compared through one family payoff matrix (Hidden #10).

    Central NO (NO_B1) wins across MANY outcomes (every bin except B1, plus
    outside) and loses only when Y lands in B1 — broad correlated exposure.
    Adjacent YES (YES_B2) wins only when Y lands in B2.

    We make the central NO clearly the better trade: B1 is moderately probable
    (~0.30) so NO_B1 wins ~70% of the time, and the NO ask is cheap (0.32 ->
    fat edge over a ~0.66 robust win prob). YES_B2 has low q (~0.20) and a
    breakeven-ish ask. The ranker must pick NO_B1 — and it can only do so by
    pricing BOTH through the same outcome payoff matrix (the NO payoff hits
    every non-B1 outcome). This pins that NO is scored as broad exposure, not a
    single-outcome Bernoulli.
    """
    qy = {
        "B0": _samples_centered(0.35),
        "B1": _samples_centered(0.30),
        "B2": _samples_centered(0.20),
    }
    central_no = _no_candidate("B1", q_yes_samples=qy["B1"], ask="0.32", token_id="t-no-b1")
    adjacent_yes = _yes_candidate("B2", q_yes_samples=qy["B2"], ask="0.21", token_id="t-yes-b2")

    pi = robust_probabilities(_matrix(BINS), per_bin_yes_samples=qy)
    exposure = PortfolioExposureVector.flat(_matrix(BINS), baseline=Decimal("1000"))

    ranked = rank_candidates([adjacent_yes, central_no], _matrix(BINS), pi, exposure)
    # Central NO is the broad, cheap, high-robust-win-prob exposure -> wins.
    assert ranked[0].candidate.side == "NO"
    assert ranked[0].candidate.bin_id == "B1"
    # And its win mass spans more than one outcome: the matrix must give NO_B1 a
    # positive payoff on B0, B2, and OUTSIDE, a negative one only on B1.
    matrix = _matrix(BINS)
    stake = Decimal("10")
    pos_outcomes = [
        y for y in matrix.outcomes
        if matrix.payoff(central_no, y, stake) > Decimal("0")
    ]
    neg_outcomes = [
        y for y in matrix.outcomes
        if matrix.payoff(central_no, y, stake) < Decimal("0")
    ]
    assert set(pos_outcomes) == {"B0", "B2", OUTSIDE_OUTCOME}
    assert neg_outcomes == ["B1"]


# ==========================================================================
# D.4 — existing/pending exposure lowers the marginal utility / stake.
# ==========================================================================
def test_existing_exposure_lowers_marginal_utility():
    """A_y carrying existing exposure shrinks the optimal stake (or no-trades).

    Marginal log utility is concave in wealth: a candidate that wins on the SAME
    outcomes where the book is already heavily exposed has lower MARGINAL value
    than against a flat book (spec §3 A_y baseline; §6 "highest q-price may be
    too correlated with existing exposure"; §12.C.5).

    Construction: YES_B0 candidate. Flat book A_y = baseline everywhere. Then a
    concentrated book that already holds a large winning position ON B0 (the very
    outcome this candidate also wins on). The marginal ΔU and the optimal stake
    must both be strictly lower against the concentrated book.
    """
    qy = {
        "B0": _samples_centered(0.55),
        "B1": _samples_centered(0.30),
        "B2": _samples_centered(0.10),
    }
    cand = _yes_candidate("B0", q_yes_samples=qy["B0"], ask="0.40", token_id="t-b0")
    matrix = _matrix(BINS)
    pi = robust_probabilities(matrix, per_bin_yes_samples=qy)

    flat = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))
    # Concentrated: already long B0 (extra wealth realized when Y lands in B0).
    concentrated = PortfolioExposureVector.from_outcome_wealth(
        matrix,
        baseline=Decimal("1000"),
        extra_by_outcome={"B0": Decimal("5000")},
    )

    s_flat = score_candidate(cand, matrix, pi, flat)
    s_conc = score_candidate(cand, matrix, pi, concentrated)

    # Same belief and price; only the existing-exposure baseline differs.
    assert s_conc.delta_u < s_flat.delta_u
    # The optimal marginal stake against the already-loaded outcome is smaller.
    assert s_conc.optimal_stake_usd < s_flat.optimal_stake_usd


# ==========================================================================
# Anchor: with a flat unit baseline and a single bin, the cost-curve optimizer
# recovers the scalar cost-fraction Kelly stake x* = (q - c)/(1 - c) (spec §5.1).
# This is the relationship that ties the ΔU optimizer back to the known closed
# form, so a regression in the numerical maximizer is caught.
# ==========================================================================
def test_single_bin_flat_baseline_recovers_scalar_kelly():
    """One-bin, flat-baseline ΔU optimizer recovers scalar cost-fraction Kelly.

    With outcome set {B0, OUTSIDE}, flat A_y = W, and YES_B0 at all-in cost c,
    ΔU(s) = q_lcb*log(1 + (s/W)*(1-c)/c) + (1-q_lcb)*log(1 - s/W). Its argmax
    in fraction x = s/W is the classic x* = (q_lcb - c)/(1 - c). We check the
    optimizer's stake fraction matches within numerical tolerance.
    """
    qy = {"B0": _samples_centered(0.60, spread=0.0)}  # degenerate: q_lcb == 0.60
    cand = _yes_candidate("B0", q_yes_samples=qy["B0"], ask="0.40", token_id="t-b0")
    matrix = _matrix(["B0"])
    pi = robust_probabilities(matrix, per_bin_yes_samples=qy)
    W = Decimal("1000")
    exposure = PortfolioExposureVector.flat(matrix, baseline=W)

    score = score_candidate(cand, matrix, pi, exposure)

    q = 0.60
    c = 0.40
    x_star = (q - c) / (1.0 - c)  # 0.3333...
    frac = float(score.optimal_stake_usd) / float(W)
    assert abs(frac - x_star) < 0.02, f"expected x*~{x_star}, got {frac}"


# ==========================================================================
# D.6 — Hidden #3: a NATIVE NO candidate must be scored with its OWN robust NO
#       q_lcb (= 1 - q_ucb_yes), NOT the looser shared-π win-mass (1 - q_lcb_yes).
# ==========================================================================
def test_no_candidate_scored_with_own_robust_no_qlcb():
    """A NO_i candidate's win-mass must be its OWN robust NO q_lcb, not 1-q_lcb_yes.

    THE RELATIONSHIP (spec §3 / §4 / §9 Hidden #3). The shared robust-π vector
    assigns bin i its YES q_lcb, ``π[i] = q_lcb_yes_i``. A NO_i candidate wins on
    every outcome EXCEPT bin i, so a leg-naive ΔU would give it win-mass
    ``Σ_{y≠i} π_y = 1 − q_lcb_yes_i``. But the lower tail of NO is the UPPER tail
    of YES (§4): the candidate's OWN robust NO lower bound is
    ``q_lcb_no = 1 − q_ucb_yes_i ≤ 1 − q_lcb_yes_i``. Scoring NO with the looser
    ``1 − q_lcb_yes_i`` is exactly Hidden #3 ("NO overconfidence"). The ranker
    MUST use the conservative ``q_lcb_no`` as the NO win-mass.

    This is a RELATIONSHIP test across the boundary
    ``ProbabilityUncertainty(q_lcb_no) → FamilyPayoffMatrix/ΔU``: the property is
    that the NO win-mass the optimizer sees equals the candidate's own
    ``q_lcb_no`` (the per-sample-complement lower bound), and is STRICTLY below
    the win-mass implied by the shared YES q_lcb whenever YES uncertainty is
    nonzero.
    """
    # Symmetric YES cloud centered 0.30 with real spread => q_lcb_yes < q_point <
    # q_ucb_yes, so 1 - q_ucb_yes (the conservative NO win-mass) is STRICTLY below
    # 1 - q_lcb_yes (the looser shared-π win-mass). The gap is what Hidden #3 is.
    qy = {
        "B0": _samples_centered(0.30, spread=0.10),
        "B1": _samples_centered(0.20, spread=0.02),
        "B2": _samples_centered(0.10, spread=0.02),
    }
    central_no = _no_candidate("B0", q_yes_samples=qy["B0"], ask="0.30", token_id="t-no-b0")

    matrix = _matrix(BINS)
    # The shared robust-π vector (bin i carries its YES q_lcb).
    shared_pi = robust_probabilities(matrix, per_bin_yes_samples=qy)

    pu_yes = probability_uncertainty_from_samples(qy["B0"])
    pu_no = probability_uncertainty_from_samples(no_side_samples(qy["B0"]))
    # Precondition: YES uncertainty is real, so the two NO win-masses differ.
    assert pu_yes.q_lcb < pu_yes.q_ucb
    looser_no_winmass = 1.0 - pu_yes.q_lcb          # the BUGGY (overconfident) mass
    own_no_winmass = pu_no.q_lcb                    # = 1 - q_ucb_yes (conservative)
    assert own_no_winmass < looser_no_winmass - 1e-6, (
        "test precondition: own robust NO q_lcb must be strictly below the "
        f"shared 1-q_lcb_yes win-mass; got own={own_no_winmass} "
        f"looser={looser_no_winmass}"
    )

    # (1) The side-aware effective π the ranker scores NO_B0 against must put the
    #     candidate's OWN q_lcb_no as the total win-mass (all outcomes except B0),
    #     and 1 - q_lcb_no as the loss-mass on B0 itself.
    eff = effective_outcome_pi(central_no, matrix, shared_pi)
    eff_win_mass = sum(p for y, p in eff.items() if y != "B0")
    eff_loss_mass = eff["B0"]
    assert eff_win_mass == own_no_winmass  # exact: conservative NO q_lcb
    assert abs(eff_loss_mass - (1.0 - own_no_winmass)) < 1e-12
    # And it is a proper distribution.
    assert abs(sum(eff.values()) - 1.0) < 1e-12
    # The conservative win-mass is STRICTLY below the looser shared-π one.
    shared_win_mass = sum(p for y, p in shared_pi.items() if y != "B0")
    assert eff_win_mass < shared_win_mass - 1e-6

    # (2) End-to-end, at the SAME stake: scoring NO_B0 with the looser
    #     (1 - q_lcb_yes) win-mass yields a STRICTLY HIGHER ΔU than scoring it
    #     with its own conservative q_lcb_no — i.e. the buggy path is
    #     overconfident. The ranker (which goes through effective_outcome_pi)
    #     scores the conservative, lower value. Evaluating both at a single fixed
    #     positive stake isolates the win-mass difference from any argmax shift.
    exposure = PortfolioExposureVector.flat(matrix, baseline=Decimal("1000"))
    stake = Decimal("50")
    du_conservative = _delta_u_via_raw_pi(central_no, matrix, eff, exposure, stake)
    du_overconfident = _delta_u_via_raw_pi(
        central_no, matrix, shared_pi, exposure, stake
    )
    assert du_overconfident > du_conservative + 1e-9, (
        "the looser (1-q_lcb_yes) NO scoring must be strictly more optimistic "
        "than the conservative own-q_lcb_no scoring the ranker actually uses "
        f"(over={du_overconfident}, conservative={du_conservative})"
    )

    # (3) And the ranker's own optimizer (score_candidate -> effective_outcome_pi)
    #     uses the conservative path: its ΔU at the conservative stake matches the
    #     hand-built conservative ΔU at that same stake, not the overconfident one.
    score_conservative = score_candidate(central_no, matrix, shared_pi, exposure)
    if score_conservative.optimal_stake_usd > Decimal("0"):
        du_at_opt = _delta_u_via_raw_pi(
            central_no, matrix, eff, exposure, score_conservative.optimal_stake_usd
        )
        assert abs(du_at_opt - score_conservative.delta_u) < 1e-9


def _delta_u_via_raw_pi(candidate, matrix, pi, exposure, stake):
    """ΔU(stake) using ``pi`` VERBATIM (no side-aware reweighting).

    Helper for D.6 only. With ``pi == shared_pi`` this reproduces the buggy,
    overconfident ``1 - q_lcb_yes`` NO win-mass; with ``pi == effective_outcome_pi``
    it reproduces the conservative own-``q_lcb_no`` win-mass. Both evaluated at the
    same ``stake`` so the comparison isolates the win-mass, not the argmax.
    """
    import math

    total = 0.0
    for y in matrix.outcomes:
        p = float(pi.get(y, 0.0))
        if p <= 0.0:
            continue
        a = exposure.a(y)
        r = matrix.payoff(candidate, y, Decimal(stake))
        new_wealth = a + r
        total += p * (math.log(float(new_wealth)) - math.log(float(a)))
    return total
