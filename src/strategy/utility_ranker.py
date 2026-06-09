# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §3 (ΔU marginal-log-utility objective) +
#   §5.3 (cost-curve ELG optimizer, s* = argmax_s Σ_y π_y log(A_y + R_y(s))) +
#   §6 (best-candidate selection; why utility beats q / q-c / ROI) +
#   §11 Phase 4 (FamilyPayoffMatrix / RobustCandidateScore / PortfolioExposureVector) +
#   §14.7 (rank by robust marginal utility) +
#   §9 Hidden #3 (NATIVE NO conservatism: a NO_i candidate is scored with its
#     OWN robust NO q_lcb = 1 − q_ucb_yes, NOT the looser 1 − q_lcb_yes implied
#     by the shared YES π — see effective_outcome_pi) +
#   §9 Hidden #5 (normalize over settlement OUTCOMES, not candidate legs) +
#   §9 Hidden #10 (central NO is broad correlated exposure) +
#   §13 (no-trade gate: robust marginal log utility <= 0) +
#   operator directive 2026-06-08.
"""Robust marginal-utility ranker + FamilyPayoffMatrix (spec Phase 4).

THE OBJECTIVE (spec §3 / §5.3). For a family whose settlement random variable
``Y`` lands in exactly one of the mutually-exclusive bin outcomes OR the
``OUTSIDE`` outcome (no bin contains Y), a single new candidate leg ``j`` taken
at stake ``s`` USD changes wealth-by-outcome from ``A_y`` to ``A_y + R_{y,j}(s)``.
Its value is the robust marginal expected LOG utility::

    ΔU_j(s) = Σ_y  π_y^rob · [ log(A_y + R_{y,j}(s)) − log(A_y) ]

and the optimal stake is ``s* = argmax_s ΔU_j(s)`` over feasible stakes (bounded
below by the curve's min order, above by executable depth). The per-outcome
payoff per spec §3 (cost ``c = c(s)`` is the depth-walked ALL-IN cost from the
Phase-3 :class:`ExecutableCostCurve`, so it is size-dependent)::

    YES_i:  R_{y,j}(s) = s·(1−c)/c   if y == i   else  −s
    NO_i:   R_{y,j}(s) = −s          if y == i   else  s·(1−c)/c

WHY THIS RANKER (spec §6). It is NOT "highest q" (may be overpriced), NOT
"highest q−c" (may be too illiquid or too correlated with existing exposure),
NOT "highest ROI" (a fragile tail), NOT "highest scalar Kelly" (unstable when
q_lcb is wide). It is the marginal log-growth of wealth, computed against the
WHOLE family payoff matrix and the EXISTING/PENDING exposure baseline.

THREE STRUCTURAL ANTIBODIES (project methodology — make the wrong code hard to
write):

  1. OUTCOMES, NOT LEGS (Hidden #5). :class:`FamilyPayoffMatrix` enumerates
     EVERY bin PLUS the ``OUTSIDE`` outcome. ``π_y^rob`` is a probability vector
     over that full outcome set. A high-probability outcome that has NO candidate
     leg is still a real settlement state and still appears in every candidate's
     "lose elsewhere" (YES) / "win elsewhere" (NO) sum, so it CANNOT inflate a
     candidate's utility. The matrix never normalizes over the legs it happens
     to be scoring.

  2. ROBUST PROBABILITY, NOT POINT, AND SIDE-CONSERVATIVE (spec §3 ``π_y^rob`` /
     §4 / §5.2 / §14.7 / §9 Hidden #3). :func:`robust_probabilities` builds the
     shared ``π`` from each bin's robust YES ``q_lcb`` (the Phase-2
     :class:`ProbabilityUncertainty` lower bound, NOT ``q_point``); the shaved
     tail mass flows to the ``OUTSIDE`` residual. But that shared vector is
     conservative ONLY for a YES candidate. Before scoring a candidate,
     :func:`effective_outcome_pi` re-anchors the candidate's OWN-bin mass to the
     value conservative for its SIDE: a NO_i candidate's win-mass becomes its own
     robust NO ``q_lcb = 1 − q_ucb_yes`` (Hidden #3), NEVER the looser
     ``1 − q_lcb_yes`` the raw shared π would imply. So the win-mass of every
     candidate — YES or NO — is conservative for that side.

  3. SIZE-DEPENDENT COST (spec §5.3, Hidden #6). The payoff uses
     ``curve.avg_cost(s)`` — the depth-walked all-in cost AT the chosen stake —
     so the optimizer sees the convex cost curve and never overbets into thin
     depth. The cost enters as a typed :class:`ExecutionPrice`, never a bare
     float.

DEFAULT-OFF / SHADOW (operator directive 2026-06-08). Pure objects + pure
functions. Importing this module changes NO live trading behavior; it is NOT
wired into the live decision path (that is Phase-4 integration, later). No
existing gate is weakened, and the spec keeps live family entry single-primary
until the payoff-matrix shadow passes (§14.8).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Sequence

from src.contracts.executable_cost_curve import ExecutableCostCurve
from src.contracts.native_side_candidate import NativeSideCandidate

# The catch-all settlement outcome: Y is in NONE of the family's bins. It is a
# first-class outcome (Hidden #5) — a NO_i candidate WINS here, a YES_i
# candidate LOSES here, and its probability mass is the robust residual that the
# bins' q_lcb shaving leaves behind.
OUTSIDE_OUTCOME: str = "__OUTSIDE__"

# A floor for the wealth-by-outcome baseline. log(A_y) is undefined at 0 and the
# marginal objective needs A_y > 0 (you always start with SOME bankroll); a
# baseline of 0 would make the optimizer divide-by-zero. Callers pass a positive
# baseline (bankroll-by-outcome); this is only a defensive floor.
_MIN_BASELINE = Decimal("1e-9")

# Numerical-optimizer resolution: the 1-D maximization over stake fraction is a
# coarse-to-fine grid search (concave objective -> unimodal, so a refined grid
# around the coarse argmax converges). 0.5% bankroll resolution is finer than
# any min-order granularity that matters and keeps the scalar-Kelly anchor test
# within tolerance.
_COARSE_STEPS = 200
_REFINE_STEPS = 64
_REFINE_PASSES = 3


# ===========================================================================
# FamilyPayoffMatrix — R_{y,j}(s) over the FULL outcome set (spec §3, Hidden #5).
# ===========================================================================
@dataclass(frozen=True)
class FamilyPayoffMatrix:
    """Per-candidate, per-outcome payoff ``R_{y,j}(s)`` for one family (spec §3).

    The outcome set is EVERY bin plus :data:`OUTSIDE_OUTCOME` — the complete,
    mutually-exclusive settlement partition of ``Y`` (Hidden #5). The matrix is
    payoff-only: it does not hold probabilities or exposure (those are the
    ``π`` vector and the :class:`PortfolioExposureVector`), so the same matrix
    scores every candidate in the family through ONE shared outcome space —
    which is exactly how a central NO and an adjacent YES are compared on equal
    footing (Hidden #10).
    """

    outcomes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.outcomes:
            raise ValueError("FamilyPayoffMatrix requires at least one outcome")
        if OUTSIDE_OUTCOME not in self.outcomes:
            raise ValueError(
                "FamilyPayoffMatrix.outcomes must include OUTSIDE_OUTCOME so a "
                "settlement state with no winning bin is a real outcome "
                "(Hidden #5: normalize over outcomes, not legs)"
            )
        if len(set(self.outcomes)) != len(self.outcomes):
            raise ValueError(f"duplicate outcomes: {self.outcomes}")

    @classmethod
    def over_bins(cls, bins: Sequence[str]) -> "FamilyPayoffMatrix":
        """Build the matrix over ``bins`` plus the appended OUTSIDE outcome.

        ``bins`` are the family's mutually-exclusive bin ids in any order; the
        OUTSIDE outcome is appended automatically. This is the blessed
        constructor — it guarantees the full-outcome-set invariant (Hidden #5)
        and refuses to let a caller silently drop the outside state.
        """
        seen = list(dict.fromkeys(bins))  # de-dupe, preserve order
        if OUTSIDE_OUTCOME in seen:
            raise ValueError(
                f"bin id {OUTSIDE_OUTCOME!r} collides with the reserved OUTSIDE "
                "outcome sentinel"
            )
        return cls(outcomes=tuple(seen) + (OUTSIDE_OUTCOME,))

    @property
    def bins(self) -> tuple[str, ...]:
        """The bin outcomes only (OUTSIDE excluded)."""
        return tuple(y for y in self.outcomes if y != OUTSIDE_OUTCOME)

    def payoff(
        self,
        candidate: NativeSideCandidate,
        outcome: str,
        stake_usd: Decimal,
    ) -> Decimal:
        """Net dollar payoff ``R_{y,j}(s)`` of ``candidate`` if Y settles ``outcome``.

        Spec §3. ``c = c(s)`` is the depth-walked ALL-IN cost of the candidate's
        own native :class:`ExecutableCostCurve` at this stake (size-dependent;
        Hidden #6). Win pays ``s·(1−c)/c`` (the profit on ``s/c`` shares each
        paying $1), a loss returns ``−s``.

        WIN/LOSS geometry (spec §3):
          * YES_i wins iff ``outcome == i``  (loses on every other outcome,
            including OUTSIDE).
          * NO_i  wins iff ``outcome != i``  (wins on every other bin AND on
            OUTSIDE — this is the broad correlated exposure of a central NO,
            Hidden #10), loses only when ``outcome == i``.

        A no-trade candidate (no executable curve) has no payoff: this raises,
        because a no-trade candidate must never reach the payoff/scoring stage.
        """
        if outcome not in self.outcomes:
            raise ValueError(
                f"outcome {outcome!r} not in family outcome set {self.outcomes}"
            )
        stake = Decimal(stake_usd)
        if stake <= Decimal("0"):
            raise ValueError(f"stake_usd must be > 0, got {stake_usd}")
        if not candidate.is_tradeable or candidate.executable_cost_curve is None:
            raise ValueError(
                "FamilyPayoffMatrix.payoff requires a tradeable candidate with an "
                f"executable cost curve; got no-trade candidate for bin "
                f"{candidate.bin_id!r} side {candidate.side!r}"
            )

        c = _all_in_cost(candidate.executable_cost_curve, stake)
        win_profit = stake * (Decimal("1") - c) / c
        loss = -stake

        wins = _candidate_wins(candidate, outcome)
        return win_profit if wins else loss


def _candidate_wins(candidate: NativeSideCandidate, outcome: str) -> bool:
    """Whether ``candidate`` wins when Y settles ``outcome`` (spec §3 geometry)."""
    if candidate.side == "YES":
        return outcome == candidate.bin_id
    # NO_i wins on every outcome EXCEPT its own bin (incl. OUTSIDE).
    return outcome != candidate.bin_id


def _all_in_cost(curve: ExecutableCostCurve, stake_usd: Decimal) -> Decimal:
    """Depth-walked all-in cost fraction ``c(s)`` of ``curve`` at ``stake_usd``.

    Reuses the Phase-3 :meth:`ExecutableCostCurve.avg_cost` — the single source
    of the size-dependent all-in cost (fees + depth walk). The typed
    :class:`ExecutionPrice` is unwrapped to a Decimal in (0, 1) for the payoff
    arithmetic; the typed boundary still guarded the Kelly cost-of-entry inside
    ``avg_cost``.
    """
    price = curve.avg_cost(stake_usd)
    c = Decimal(str(price.value))
    if not (Decimal("0") < c < Decimal("1")):
        raise ValueError(
            f"all-in cost {c} outside (0, 1) probability_units; cannot form "
            "the (1-c)/c payoff ratio"
        )
    return c


# ===========================================================================
# PortfolioExposureVector — A_y existing/pending wealth by outcome (spec §3).
# ===========================================================================
@dataclass(frozen=True)
class PortfolioExposureVector:
    """Existing/pending wealth-by-outcome ``A_y`` for the family (spec §3).

    ``A_y`` is the wealth the book WOULD hold if Y settled outcome ``y``, given
    everything already on/pending in this family. It is the baseline against
    which the new candidate's marginal log utility is measured. Because log is
    concave, a candidate that wins on outcomes where ``A_y`` is already large has
    LOWER marginal value — this is how existing exposure shrinks the optimal
    stake or forces a no-trade (spec §6 "too correlated with existing exposure";
    §12.C.5 / §12.D.4).

    All entries must be strictly positive (log is undefined at 0); callers pass a
    positive bankroll baseline.
    """

    wealth: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        object.__setattr__(self, "wealth", dict(self.wealth))
        for y, w in self.wealth.items():
            if Decimal(w) <= Decimal("0"):
                raise ValueError(
                    f"PortfolioExposureVector wealth for outcome {y!r} must be > 0 "
                    f"(log baseline), got {w}"
                )

    def a(self, outcome: str) -> Decimal:
        """Baseline wealth ``A_y`` for ``outcome``."""
        if outcome not in self.wealth:
            raise ValueError(
                f"no exposure baseline for outcome {outcome!r}; "
                f"have {sorted(self.wealth)}"
            )
        return Decimal(self.wealth[outcome])

    @classmethod
    def flat(
        cls, matrix: FamilyPayoffMatrix, *, baseline: Decimal
    ) -> "PortfolioExposureVector":
        """Flat baseline ``A_y = baseline`` for every outcome (no existing exposure).

        The neutral starting point: the book holds the same wealth whatever Y
        does. Against this, the ΔU optimizer reduces to the spec §5.1 scalar
        cost-fraction Kelly for a single bin (the anchor test pins this).
        """
        base = max(Decimal(baseline), _MIN_BASELINE)
        return cls(wealth={y: base for y in matrix.outcomes})

    @classmethod
    def from_outcome_wealth(
        cls,
        matrix: FamilyPayoffMatrix,
        *,
        baseline: Decimal,
        extra_by_outcome: Mapping[str, Decimal],
    ) -> "PortfolioExposureVector":
        """Baseline plus EXISTING per-outcome winnings ``extra_by_outcome``.

        Models existing/pending family exposure: ``extra_by_outcome[y]`` is the
        additional wealth realized if Y settles ``y`` (e.g. an existing long
        position on bin ``y``). The marginal utility of a NEW candidate that also
        wins on ``y`` is then lower (concavity), shrinking the optimal stake.
        """
        base = max(Decimal(baseline), _MIN_BASELINE)
        wealth = {y: base for y in matrix.outcomes}
        for y, extra in extra_by_outcome.items():
            if y not in wealth:
                raise ValueError(
                    f"extra_by_outcome outcome {y!r} not in matrix outcomes "
                    f"{matrix.outcomes}"
                )
            wealth[y] = base + Decimal(extra)
        return cls(wealth=wealth)


# ===========================================================================
# robust_probabilities — π_y^rob over the full outcome set (spec §3, antibody 2).
# ===========================================================================
def robust_probabilities(
    matrix: FamilyPayoffMatrix,
    *,
    per_bin_yes_samples: Mapping[str, Sequence[float]] | None = None,
    per_bin_q_lcb: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Robust outcome-probability vector ``π_y^rob`` (spec §3 / §14.7).

    Each bin ``i`` is assigned its ROBUST YES probability ``q_lcb_i`` (NOT
    ``q_point`` — antibody 2). The :data:`OUTSIDE_OUTCOME` absorbs the residual
    ``max(0, 1 − Σ_i q_lcb_i)`` — the conservative tail mass the per-bin shaving
    left behind. The result is a proper distribution over the FULL outcome set;
    if the bins' q_lcb sum exceeds 1 (overlapping / over-confident bins) the
    whole vector is renormalized to restore validity.

    Provide EITHER:
      * ``per_bin_yes_samples`` — bin id -> YES probability bootstrap samples;
        ``q_lcb`` is computed via the Phase-2
        :func:`probability_uncertainty_from_samples` (penalty-free here — family
        penalties belong on the candidate's own ProbabilityUncertainty), OR
      * ``per_bin_q_lcb`` — bin id -> precomputed robust YES ``q_lcb``.

    Bins absent from the input get ``q_lcb = 0`` (an unscored bin contributes no
    win mass; its outcome still exists so candidates still lose on it — Hidden #5).
    """
    if (per_bin_yes_samples is None) == (per_bin_q_lcb is None):
        raise ValueError(
            "provide exactly one of per_bin_yes_samples / per_bin_q_lcb"
        )

    q_lcb_by_bin: dict[str, float] = {}
    if per_bin_q_lcb is not None:
        q_lcb_by_bin = {k: float(v) for k, v in per_bin_q_lcb.items()}
    else:
        # Local import keeps the module import-light and avoids a hard cycle if
        # probability_uncertainty's dependencies shift.
        from src.strategy.probability_uncertainty import (
            probability_uncertainty_from_samples,
        )

        for bin_id, samples in per_bin_yes_samples.items():
            pu = probability_uncertainty_from_samples(samples)
            q_lcb_by_bin[bin_id] = float(pu.q_lcb)

    pi: dict[str, float] = {}
    bin_mass = 0.0
    for bin_id in matrix.bins:
        q = q_lcb_by_bin.get(bin_id, 0.0)
        if not (0.0 <= q <= 1.0):
            raise ValueError(f"q_lcb for bin {bin_id!r} must be in [0, 1], got {q}")
        pi[bin_id] = q
        bin_mass += q

    pi[OUTSIDE_OUTCOME] = max(0.0, 1.0 - bin_mass)

    total = sum(pi.values())
    if total <= 0.0:
        raise ValueError(
            "robust outcome probabilities sum to 0; no bin carries robust mass "
            "and OUTSIDE residual is 0 — degenerate family"
        )
    # Renormalize to a proper distribution (no-op when bin_mass <= 1, which is
    # the common case because each q_lcb is shaved below its q_point).
    return {y: p / total for y, p in pi.items()}


# ===========================================================================
# effective_outcome_pi — side-conservative π for ONE candidate (§3, §4, Hidden #3).
# ===========================================================================
def effective_outcome_pi(
    candidate: NativeSideCandidate,
    matrix: FamilyPayoffMatrix,
    pi: Mapping[str, float],
) -> dict[str, float]:
    """Side-conservative outcome distribution used to score ``candidate`` (Hidden #3).

    THE DEFECT THIS CLOSES (spec §4 / §9 Hidden #3). The shared robust-π vector
    :func:`robust_probabilities` assigns each bin ``i`` its YES robust lower bound
    ``π[i] = q_lcb_yes_i``. Scoring a candidate against that vector directly is
    correct ONLY for a YES candidate, whose own-bin mass IS its conservative win
    probability ``q_lcb_yes_i``. For a NATIVE NO candidate it is WRONG: a NO_i
    wins on every outcome except bin ``i``, so the raw-π win-mass is
    ``Σ_{y≠i} π_y = 1 − q_lcb_yes_i``. But the lower tail of NO is the UPPER tail
    of YES (§4): the candidate's OWN robust NO lower bound is
    ``q_lcb_no_i = 1 − q_ucb_yes_i ≤ 1 − q_lcb_yes_i``. Using the looser
    ``1 − q_lcb_yes_i`` overstates the NO win-mass — exactly Hidden #3
    ("NO overconfidence").

    THE FIX (scoped to the side that is actually non-conservative — NO). The
    shared π is ALREADY conservative for a YES candidate: ``robust_probabilities``
    sets ``π[i] = q_lcb_yes_i = candidate.q_lcb``, which IS the YES win-mass. So a
    YES candidate is returned the shared π UNCHANGED. Only the NO side needs
    re-anchoring:

      * NO_i: own bin ``i`` is the LOSE outcome. Set
        ``π_eff[i] = 1 − q_lcb_no_i = q_ucb_yes_i`` so the WIN-mass over every
        OTHER outcome totals exactly ``q_lcb_no_i = candidate.q_lcb`` — the
        candidate's OWN robust NO lower bound, NOT ``1 − q_lcb_yes_i``. The
        remaining win-mass ``q_lcb_no_i`` is spread over the other outcomes IN
        PROPORTION to their shared-π weights, so OUTSIDE and unrepresented bins
        keep their relative geometry (Hidden #5 stays intact).

    ``candidate.q_lcb`` is the side's own robust lower bound: for a YES candidate
    it is ``q_lcb_yes``; for a NO candidate it is ``q_lcb_no = 1 − q_ucb_yes``
    (built upstream from :func:`no_side_samples`). So the per-side conservatism
    falls out of ONE field — there is no place to accidentally feed a NO
    candidate the YES win-mass.

    A no-trade candidate (no ``q_lcb``) cannot be scored and must never reach
    here; this raises if asked.
    """
    if not candidate.is_tradeable or candidate.q_lcb is None:
        raise ValueError(
            "effective_outcome_pi requires a tradeable candidate with a robust "
            f"q_lcb; got no-trade candidate for bin {candidate.bin_id!r} side "
            f"{candidate.side!r}"
        )
    own_bin = candidate.bin_id
    if own_bin not in matrix.outcomes:
        raise ValueError(
            f"candidate bin {own_bin!r} is not a family outcome {matrix.outcomes}"
        )

    # YES: the shared π is already side-conservative (π[i] = q_lcb_yes_i). Return
    # it verbatim — no reweighting, so the YES scoring path is byte-for-byte
    # unchanged from before this fix.
    if candidate.side == "YES":
        return {y: float(pi.get(y, 0.0)) for y in matrix.outcomes}

    # NO_i: re-anchor the own-bin (LOSE) mass to q_ucb_yes_i = 1 − q_lcb_no_i, so
    # the win-mass over the other outcomes totals the candidate's OWN q_lcb_no.
    own_q_lcb_no = float(candidate.q_lcb)
    if not (0.0 <= own_q_lcb_no <= 1.0):
        raise ValueError(f"candidate.q_lcb must be in [0, 1], got {own_q_lcb_no}")
    own_mass = 1.0 - own_q_lcb_no  # loss-mass on bin i (= q_ucb_yes_i)
    remaining = own_q_lcb_no       # win-mass spread over every other outcome

    other_total = sum(
        float(pi.get(y, 0.0)) for y in matrix.outcomes if y != own_bin
    )

    eff: dict[str, float] = {}
    if other_total <= 0.0:
        # No shared mass on the other outcomes (degenerate). Spread the win-mass
        # uniformly so the result is still a proper distribution rather than
        # collapsing all win-mass to zero.
        others = [y for y in matrix.outcomes if y != own_bin]
        share = (remaining / len(others)) if others else 0.0
        for y in matrix.outcomes:
            eff[y] = own_mass if y == own_bin else share
        return eff

    for y in matrix.outcomes:
        if y == own_bin:
            eff[y] = own_mass
        else:
            eff[y] = remaining * (float(pi.get(y, 0.0)) / other_total)
    return eff


# ===========================================================================
# RobustCandidateScore + the ΔU optimizer (spec §3, §5.3, §6, §13).
# ===========================================================================
@dataclass(frozen=True)
class RobustCandidateScore:
    """Result of maximizing ΔU for one candidate (spec §11 Phase 4).

    Attributes:
        candidate: the scored :class:`NativeSideCandidate`.
        delta_u: ΔU at the optimal stake — the robust marginal expected log
            utility (spec §3). ``<= 0`` means no-trade (spec §13 "robust marginal
            expected log utility <= 0").
        optimal_stake_usd: ``s*`` = argmax_s ΔU(s), in USD. ``0`` when no positive
            stake helps (no-trade).
        no_trade_reason: human-readable cause when ``is_no_trade``; ``""`` when
            tradeable. (Kept as a string here — the structured candidate-level
            reasons live on :class:`NativeSideCandidate`; this records WHY the
            ranker scored it zero/negative.)
    """

    candidate: NativeSideCandidate
    delta_u: float
    optimal_stake_usd: Decimal
    no_trade_reason: str = ""
    # Non-key provenance: the ΔU evaluated at a couple of probe stakes, for
    # debugging the optimizer. Excluded from eq/hash.
    n_evals: int = field(default=0, compare=False)

    @property
    def is_no_trade(self) -> bool:
        """True iff this score does not authorize a trade (spec §13)."""
        return self.delta_u <= 0.0 or self.optimal_stake_usd <= Decimal("0")


def _delta_u_at_stake(
    candidate: NativeSideCandidate,
    matrix: FamilyPayoffMatrix,
    pi: Mapping[str, float],
    exposure: PortfolioExposureVector,
    stake_usd: Decimal,
) -> float:
    """ΔU(s) = Σ_y π_y [ log(A_y + R_y(s)) − log(A_y) ] (spec §3).

    ``pi`` here is the SIDE-CONSERVATIVE effective distribution from
    :func:`effective_outcome_pi` (NOT the shared robust-π vector directly): for a
    NO candidate its win-mass is the candidate's OWN robust NO ``q_lcb``
    (= 1 − q_ucb_yes), never the looser 1 − q_lcb_yes (§9 Hidden #3). The caller
    (:func:`score_candidate`) reweights once before the stake sweep.

    Returns ``-inf`` when the stake is INFEASIBLE — either the cost curve cannot
    fill it (depth exhausted / below min order, which ``avg_cost`` raises) or a
    losing outcome would drive ``A_y + R_y`` to <= 0 (ruin on that outcome). Both
    are hard no-gos the maximizer must avoid, so they map to negative infinity.
    """
    try:
        # One avg_cost walk per stake (cost is shared across outcomes for this
        # candidate); payoff() recomputes c internally but avg_cost is cheap and
        # this keeps the matrix payoff the single source of the R_y geometry.
        total = 0.0
        for y in matrix.outcomes:
            p = float(pi.get(y, 0.0))
            if p <= 0.0:
                continue
            a = exposure.a(y)
            r = matrix.payoff(candidate, y, stake_usd)
            new_wealth = a + r
            if new_wealth <= Decimal("0"):
                # A loss that wipes out the outcome's wealth -> log undefined ->
                # infinitely bad. The optimizer will never choose such a stake.
                return float("-inf")
            total += p * (math.log(float(new_wealth)) - math.log(float(a)))
        return total
    except (ValueError, ArithmeticError):
        # Infeasible stake (depth / min order / off-grid). Treat as unrankable.
        return float("-inf")


def score_candidate(
    candidate: NativeSideCandidate,
    matrix: FamilyPayoffMatrix,
    pi: Mapping[str, float],
    exposure: PortfolioExposureVector,
    *,
    max_stake_usd: Decimal | None = None,
) -> RobustCandidateScore:
    """Maximize ΔU over feasible stakes for ``candidate`` (spec §3 / §5.3 / §6).

    ``s* = argmax_s ΔU(s)`` by a coarse-to-fine 1-D grid search over the feasible
    stake interval ``[min_order_notional, max_feasible]``. ΔU is concave in stake
    (sum of concave logs of an affine-in-s wealth), so it is unimodal and the
    refined grid around the coarse argmax converges to the optimum.

    Feasible upper bound (spec §5.3 "bounded by depth + min order"): the smaller
    of the curve's total executable depth and ``max_stake_usd`` if given. The
    lower bound is the min-order notional. A no-trade candidate, or a candidate
    whose best feasible ΔU is <= 0, returns a no-trade score (spec §13).
    """
    if not candidate.is_tradeable or candidate.executable_cost_curve is None:
        return RobustCandidateScore(
            candidate=candidate,
            delta_u=0.0,
            optimal_stake_usd=Decimal("0"),
            no_trade_reason=(
                f"candidate not tradeable "
                f"(reason={candidate.no_trade_reason})"
            ),
        )

    curve = candidate.executable_cost_curve
    lo, hi = _feasible_stake_bounds(curve, max_stake_usd)
    if lo is None or hi is None or hi <= lo:
        return RobustCandidateScore(
            candidate=candidate,
            delta_u=0.0,
            optimal_stake_usd=Decimal("0"),
            no_trade_reason="no feasible stake interval (depth/min-order)",
        )

    # Side-conservative reweighting ONCE before the stake sweep (§9 Hidden #3):
    # a NO candidate is scored with its OWN robust NO q_lcb (= 1 − q_ucb_yes),
    # never the looser 1 − q_lcb_yes implied by the shared YES π. Stake-independent,
    # so it is computed a single time here and reused for every probe stake.
    eff_pi = effective_outcome_pi(candidate, matrix, pi)

    best_u = float("-inf")
    best_s = Decimal("0")
    n_evals = 0

    span_lo, span_hi = lo, hi
    steps = _COARSE_STEPS
    for _pass in range(_REFINE_PASSES + 1):
        width = span_hi - span_lo
        if width <= Decimal("0"):
            break
        step = width / Decimal(steps)
        s = span_lo
        pass_best_u = float("-inf")
        pass_best_s = span_lo
        for _ in range(steps + 1):
            u = _delta_u_at_stake(candidate, matrix, eff_pi, exposure, s)
            n_evals += 1
            if u > pass_best_u:
                pass_best_u = u
                pass_best_s = s
            s += step
        if pass_best_u > best_u:
            best_u = pass_best_u
            best_s = pass_best_s
        # Refine a tight window around the pass argmax for the next pass.
        span_lo = max(lo, pass_best_s - step)
        span_hi = min(hi, pass_best_s + step)
        steps = _REFINE_STEPS

    if not math.isfinite(best_u) or best_u <= 0.0 or best_s <= Decimal("0"):
        return RobustCandidateScore(
            candidate=candidate,
            delta_u=(best_u if math.isfinite(best_u) else 0.0),
            optimal_stake_usd=Decimal("0"),
            no_trade_reason="robust marginal expected log utility <= 0 (§13)",
            n_evals=n_evals,
        )

    return RobustCandidateScore(
        candidate=candidate,
        delta_u=best_u,
        optimal_stake_usd=best_s,
        no_trade_reason="",
        n_evals=n_evals,
    )


def _feasible_stake_bounds(
    curve: ExecutableCostCurve, max_stake_usd: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """Feasible stake interval ``[lo, hi]`` for ``curve`` (spec §5.3 bounds).

    ``hi`` = total executable all-in notional of the book (the most a taker can
    spend walking every level), optionally capped by ``max_stake_usd``.
    ``lo`` = the min-order notional: the cheapest all-in notional that still buys
    at least ``min_order_size`` shares (priced at the best level, the lower
    bound on cost). Returns ``(None, None)`` when no feasible stake exists.
    """
    levels = curve.levels
    if not levels:
        return None, None

    # Upper bound: full-book all-in notional (spend through every level).
    full_notional = sum(
        (curve.fee_model.all_in_price(lvl.price) * lvl.size for lvl in levels),
        Decimal("0"),
    )
    hi = full_notional
    if max_stake_usd is not None:
        hi = min(hi, Decimal(max_stake_usd))

    # Lower bound: min-order shares at the best (cheapest) all-in price.
    best = levels[0]
    best_all_in = curve.fee_model.all_in_price(best.price)
    lo = best_all_in * curve.min_order_size

    if lo <= Decimal("0") or hi <= Decimal("0") or hi < lo:
        return None, None
    return lo, hi


# ===========================================================================
# rank_candidates — primary-sort by robust marginal utility (spec §6 / §14.7).
# ===========================================================================
def rank_candidates(
    candidates: Sequence[NativeSideCandidate],
    matrix: FamilyPayoffMatrix,
    pi: Mapping[str, float],
    exposure: PortfolioExposureVector,
    *,
    max_stake_usd: Decimal | None = None,
) -> list[RobustCandidateScore]:
    """Score every candidate and sort by ΔU descending (spec §6 / §14.7).

    The PRIMARY sort key is "positive robust marginal expected log utility after
    FDR and hard gates" (spec §14.7) — NOT probability, NOT ``q − price``, NOT
    ROI. No-trade candidates (untradeable, or ΔU <= 0) sort to the bottom; ties
    break on the candidate's hypothesis id for a deterministic order.

    This function does NOT itself apply FDR or the other hard gates (those are
    upstream, spec §6 pseudocode ``hard_gates_pass`` / ``apply_familywise_fdr``);
    it ranks the survivors by utility. The first element is the family primary;
    the rest are the WATCH fallback queue (spec §6 — fallback is watch-only).
    """
    scored = [
        score_candidate(c, matrix, pi, exposure, max_stake_usd=max_stake_usd)
        for c in candidates
    ]
    scored.sort(
        key=lambda s: (
            -(s.delta_u if math.isfinite(s.delta_u) else float("-inf")),
            s.candidate.hypothesis_id,
        )
    )
    return scored
