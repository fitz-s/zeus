# Created: 2026-08-24
# Last reused or audited: 2026-08-24
# Authority basis: docs/operations/current/plans/tier0_selection_lift_preregistration_2026-08-24.md
#   (FROZEN before first Tier-0 settlement) — reversal_plan_tier0_2026-08-24.md item 7.
"""Preregistered Tier-0 ordinal selection-lift test — pure computation, no DB access.

Implements the frozen design in the preregistration verbatim:

  Treatment: the selected candidate's settlement residual y - p0.
  Control:   weighted mean settlement residual of the SAME city-date opportunity
             set's eligible, NOT-selected candidates, price-matched to the
             selected candidate within +/-0.05 on side price, same lead bucket,
             logical duplicates and yes/no complements collapsed to one economic
             claim each.
  L = (y_sel - p0_sel) - weighted_mean(y_ctrl - p0_ctrl), one per city-date.
  City-dates with an empty matched control set contribute NOTHING (excluded,
  counted).

Inference: permutation test (within-set label permutation across the matched
pool), 10,000 permutations by default, two-sided p on mean(L). Dependence:
primary clustering is city-date (each observation already IS one city-date);
sensitivity is date-block resampling (city-dates sharing a calendar date form
one block); the LARGER uncertainty governs — both are produced by the same
block-bootstrap primitive so their widths are directly comparable.

Weighting note (frozen doc says "weighted mean" without specifying weights;
no per-candidate weight field exists in the data model given to this test):
this module applies UNIFORM weights over the matched control pool (a plain
mean), which is the only weighting scheme the preregistration's data model
supports without inventing an unstated field. Documented here per the "no
analytic freedom" constraint rather than silently picking a scheme.

Collapse key (this module's addition to the preregistration's literal
candidate tuple, required to implement §"logical duplicates and yes/no
complements collapsed to one economic claim each" — the frozen doc names
this rule but the (id, side, p0, lead_bucket, eligible, selected, y) tuple
alone carries no market/token-pair identity to collapse on): each Candidate
carries an additional ``market_key`` (family/market identity — e.g. the
condition_id of the underlying binary market). Within one opportunity set,
candidates are grouped by market_key; a group of >1 collapses to ONE
economic claim:
  - same-side duplicates (identical side, same market_key) collapse first,
    keeping the selected member if any, else the lowest ``id`` (deterministic).
  - a remaining YES/NO pair on the same market_key collapses to one claim
    iff |p_yes + p_no - 1| < 0.01 (yes/no complement law), keeping the
    selected member if any, else the cheaper-priced side (p0 < 0.5), else
    the lowest ``id``.
  - a same-market_key pair that does NOT satisfy the price-complement check
    is NOT collapsed (inconsistent quotes are a data anomaly, never guessed
    around) — both survive, counted via
    ``uncollapsed_inconsistent_complement``.
  - a market_key group with more than 2 distinct sides is left uncollapsed
    and counted via ``unexpected_market_key_side_count`` (a binary market
    should never have more than 2 sides; this is a data-quality signal, not
    something this module silently resolves).
  - candidates with ``market_key is None`` cannot be matched for collapse and
    are left as-is, counted via ``missing_market_key``.

No live wiring: nothing in this module is imported by the entry path.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

_PRICE_MATCH_WINDOW = 0.05
_COMPLEMENT_TOLERANCE = 0.01

# Frozen doc §"Stopping rule": evaluate ONCE at 100 qualifying city-date
# observations; earlier peeks are forbidden.
STOPPING_COUNT = 100


# ---------------------------------------------------------------------------
# Domain objects.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One considered claim in a Tier-0 auction opportunity set.

    y is SIDE settlement (did THIS side win: 1) — never the raw market
    resolution; buy_yes and buy_no candidates on the same market carry
    complementary y values by construction of how callers derive them.
    """

    id: str
    side: str
    p0: float | None
    lead_bucket: str | None
    eligible: bool
    selected: bool
    y: int | None
    market_key: str | None = None


@dataclass(frozen=True)
class OpportunitySet:
    """All candidates considered for one live Tier-0 auction decision."""

    city: str
    date: str  # calendar target date, used both for the city-date key and date-block clustering
    candidates: tuple[Candidate, ...]

    @property
    def city_date_key(self) -> str:
        return f"{self.city}|{self.date}"


@dataclass(frozen=True)
class SelectionLiftObservation:
    """One city-date's aggregate lift observation, plus the full matched pool
    (selected residual first, then matched-control residuals) needed to run
    the permutation test without re-deriving matching from raw candidates."""

    city_date_key: str
    city: str
    date: str
    treatment: float
    control_mean: float
    L: float
    n_control: int
    pool_residuals: tuple[float, ...]  # index 0 == treatment (selected) residual


@dataclass(frozen=True)
class BuildObservationsResult:
    observations: tuple[SelectionLiftObservation, ...]
    coverage: Mapping[str, int]


# ---------------------------------------------------------------------------
# Collapse: logical duplicates + yes/no complements -> one economic claim.
# ---------------------------------------------------------------------------


def _dedupe_same_side(group: list[Candidate], coverage: dict[str, int]) -> Candidate:
    """Collapse >=1 same-side, same-market_key candidates to one survivor."""
    if len(group) == 1:
        return group[0]
    coverage["same_side_duplicate_collapsed"] += len(group) - 1
    selected = [c for c in group if c.selected]
    if selected:
        return sorted(selected, key=lambda c: c.id)[0]
    return sorted(group, key=lambda c: c.id)[0]


def collapse_duplicates_and_complements(
    candidates: Sequence[Candidate], coverage: dict[str, int]
) -> list[Candidate]:
    """Collapse logical duplicates and yes/no complements to one claim each.

    See module docstring "Collapse key" for the precise rule. ``coverage`` is
    mutated in place with named counters for every non-trivial branch taken.
    """
    by_market: dict[str, list[Candidate]] = defaultdict(list)
    standalone: list[Candidate] = []
    for c in candidates:
        if c.market_key is None:
            coverage["missing_market_key"] += 1
            standalone.append(c)
        else:
            by_market[c.market_key].append(c)

    survivors: list[Candidate] = list(standalone)
    for _market_key, group in by_market.items():
        by_side: dict[str, list[Candidate]] = defaultdict(list)
        for c in group:
            by_side[c.side].append(c)
        deduped = [_dedupe_same_side(members, coverage) for members in by_side.values()]

        if len(deduped) == 1:
            survivors.append(deduped[0])
            continue
        if len(deduped) > 2:
            coverage["unexpected_market_key_side_count"] += 1
            survivors.extend(deduped)
            continue

        a, b = deduped
        if a.p0 is None or b.p0 is None or abs(a.p0 + b.p0 - 1.0) >= _COMPLEMENT_TOLERANCE:
            coverage["uncollapsed_inconsistent_complement"] += 1
            survivors.extend(deduped)
            continue

        coverage["yes_no_complement_collapsed"] += 1
        if a.selected and not b.selected:
            survivors.append(a)
        elif b.selected and not a.selected:
            survivors.append(b)
        else:
            cheaper = a if a.p0 < b.p0 else (b if b.p0 < a.p0 else min(a, b, key=lambda c: c.id))
            survivors.append(cheaper)

    return survivors


# ---------------------------------------------------------------------------
# build_observations()
# ---------------------------------------------------------------------------


def build_observations(opportunity_sets: Sequence[OpportunitySet]) -> BuildObservationsResult:
    coverage: dict[str, int] = defaultdict(int)
    observations: list[SelectionLiftObservation] = []

    for opp_set in opportunity_sets:
        collapsed = collapse_duplicates_and_complements(opp_set.candidates, coverage)

        selected_candidates = [c for c in collapsed if c.selected]
        if not selected_candidates:
            coverage["no_selected_candidate"] += 1
            continue
        if len(selected_candidates) > 1:
            coverage["multiple_selected_candidates"] += 1
            continue
        selected = selected_candidates[0]

        if selected.p0 is None:
            coverage["selected_missing_p0"] += 1
            continue
        if selected.y is None:
            coverage["selected_unsettled"] += 1
            continue

        control_residuals: list[float] = []
        for c in collapsed:
            if c.id == selected.id:
                continue
            if not c.eligible:
                continue
            if c.lead_bucket != selected.lead_bucket:
                continue
            if c.p0 is None:
                coverage["control_missing_p0"] += 1
                continue
            if abs(c.p0 - selected.p0) > _PRICE_MATCH_WINDOW:
                continue
            if c.y is None:
                coverage["control_unsettled"] += 1
                continue
            control_residuals.append(c.y - c.p0)

        if not control_residuals:
            coverage["empty_matched_control"] += 1
            continue

        treatment = selected.y - selected.p0
        control_mean = float(np.mean(control_residuals))
        L = treatment - control_mean
        observations.append(
            SelectionLiftObservation(
                city_date_key=opp_set.city_date_key,
                city=opp_set.city,
                date=opp_set.date,
                treatment=treatment,
                control_mean=control_mean,
                L=L,
                n_control=len(control_residuals),
                pool_residuals=(treatment, *control_residuals),
            )
        )
        coverage["included"] += 1

    return BuildObservationsResult(observations=tuple(observations), coverage=dict(coverage))


# ---------------------------------------------------------------------------
# permutation_test()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermutationTestResult:
    observed_statistic: float
    p_value: float
    n_perm: int
    seed: int
    n_observations: int


def permutation_test(
    observations: Sequence[SelectionLiftObservation],
    *,
    n_perm: int = 10000,
    seed: int,
) -> PermutationTestResult:
    """Two-sided permutation p-value on mean(L).

    Within each opportunity set, the "selected" label is permuted uniformly
    across the matched pool (the actual selected candidate plus its matched
    controls — the exact set precomputed by build_observations); the
    statistic is mean(L) across sets under that relabeling. ``seed`` is
    REQUIRED (never a wall-clock default) so a result is exactly
    reproducible given the same observations, n_perm, and seed.
    """
    if not observations:
        return PermutationTestResult(
            observed_statistic=float("nan"), p_value=float("nan"), n_perm=n_perm, seed=seed, n_observations=0
        )
    for obs in observations:
        if len(obs.pool_residuals) < 2:
            raise ValueError(
                f"{obs.city_date_key}: pool_residuals must have >=2 members "
                "(selected + >=1 control) to permute a label"
            )

    rng = np.random.default_rng(seed)
    n_obs = len(observations)
    observed_statistic = float(np.mean([obs.L for obs in observations]))

    perm_stats = np.zeros(n_perm, dtype=np.float64)
    for obs in observations:
        pool = np.asarray(obs.pool_residuals, dtype=np.float64)
        k = pool.size
        total = pool.sum()
        idx = rng.integers(0, k, size=n_perm)
        selected_vals = pool[idx]
        rest_mean = (total - selected_vals) / (k - 1)
        perm_stats += selected_vals - rest_mean
    perm_stats /= n_obs

    count_ge = int(np.sum(np.abs(perm_stats) >= abs(observed_statistic) - 1e-12))
    p_value = (count_ge + 1) / (n_perm + 1)

    return PermutationTestResult(
        observed_statistic=observed_statistic,
        p_value=p_value,
        n_perm=n_perm,
        seed=seed,
        n_observations=n_obs,
    )


# ---------------------------------------------------------------------------
# date_block_sensitivity() — and the shared block-bootstrap primitive it and
# the primary city-date-clustered CI both use, so their widths are directly
# comparable per the "LARGER uncertainty governs" law.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapCI:
    point_estimate: float
    lower: float
    upper: float
    confidence: float
    n_blocks: int
    n_boot: int
    seed: int


def _block_bootstrap_ci(
    observations: Sequence[SelectionLiftObservation],
    *,
    block_key,
    n_boot: int,
    seed: int,
    confidence: float = 0.95,
) -> BootstrapCI:
    if not observations:
        return BootstrapCI(
            point_estimate=float("nan"), lower=float("nan"), upper=float("nan"),
            confidence=confidence, n_blocks=0, n_boot=n_boot, seed=seed,
        )
    blocks: dict[str, list[float]] = defaultdict(list)
    for obs in observations:
        blocks[block_key(obs)].append(obs.L)
    block_arrays = [np.asarray(vals, dtype=np.float64) for vals in blocks.values()]
    n_blocks = len(block_arrays)

    rng = np.random.default_rng(seed)
    boot_means = np.empty(n_boot, dtype=np.float64)
    for i in range(n_boot):
        draw = rng.integers(0, n_blocks, size=n_blocks)
        sample = np.concatenate([block_arrays[j] for j in draw])
        boot_means[i] = sample.mean()

    alpha = 1.0 - confidence
    lower = float(np.percentile(boot_means, 100 * alpha / 2))
    upper = float(np.percentile(boot_means, 100 * (1 - alpha / 2)))
    point = float(np.mean([obs.L for obs in observations]))

    return BootstrapCI(
        point_estimate=point, lower=lower, upper=upper, confidence=confidence,
        n_blocks=n_blocks, n_boot=n_boot, seed=seed,
    )


def city_date_bootstrap_ci(
    observations: Sequence[SelectionLiftObservation],
    *,
    n_boot: int = 10000,
    seed: int,
    confidence: float = 0.95,
) -> BootstrapCI:
    """Primary clustering: each opportunity set (city-date) is one cluster —
    block = the observation itself, so this bootstraps individual city-dates."""
    return _block_bootstrap_ci(
        observations, block_key=lambda o: o.city_date_key, n_boot=n_boot, seed=seed, confidence=confidence
    )


def date_block_sensitivity(
    observations: Sequence[SelectionLiftObservation],
    *,
    n_boot: int = 10000,
    seed: int,
    confidence: float = 0.95,
) -> BootstrapCI:
    """Sensitivity clustering: all city-dates sharing a calendar date form one
    block (weather systems correlate cross-city same-date)."""
    return _block_bootstrap_ci(
        observations, block_key=lambda o: o.date, n_boot=n_boot, seed=seed, confidence=confidence
    )


def governing_ci(city_date_ci: BootstrapCI, date_block_ci: BootstrapCI) -> BootstrapCI:
    """The LARGER uncertainty governs (frozen doc, "Dependence"). Width ties
    go to the date-block CI (the more conservative, cross-city-correlation-
    aware clustering)."""
    width_cd = city_date_ci.upper - city_date_ci.lower
    width_db = date_block_ci.upper - date_block_ci.lower
    return date_block_ci if width_db >= width_cd else city_date_ci


# ---------------------------------------------------------------------------
# Evaluation lock (frozen doc §"Stopping rule").
# ---------------------------------------------------------------------------


def evaluation_is_locked(n_observations: int, *, min_observations: int = STOPPING_COUNT) -> bool:
    """True while accrual is below the frozen stopping count.

    Callers (the report CLI) must refuse to print a p-value while this is
    True — "earlier peeks are forbidden" (frozen doc). The one documented
    exception is a report-only power check on cluster variance from an
    early sample, which never functions as a stopping trigger.
    """
    return n_observations < min_observations
