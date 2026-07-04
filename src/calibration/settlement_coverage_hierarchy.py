# Created: 2026-07-04
# Last reused or audited: 2026-07-04
# Authority basis: F1 (settled-chain audit, decisive z~=-4.5): decision-time q=0.84
#   bucket realizes 0.44 (n=36) while q~=0.7 is calibrated. K3
#   (src/calibration/settlement_backward_coverage.py) never fires because its ONLY
#   cohort is the exact (city, metric, band_template, direction) cell with min_n=30
#   -- 101 settled positions across 1000+ cells -> everything INSUFFICIENT_DATA.
#   This module GENERALIZES K3 into a hierarchical claimed-vs-realized calibrator: a
#   cohort hierarchy (exact cell -> strategy bucket -> strategy super-bucket ->
#   cross-strategy -> global) where the FIRST licensed level wins, producing an
#   EXECUTABLE pair (q_exec, q_lcb_exec) distinct from the frozen raw certificate.
#   External design verdict adopted verbatim (confidence 0.80, 2026-07-04).
#
#   K3 IS NOT MODIFIED OR REPLACED by this module (settlement_backward_coverage.py
#   and its ARM-gate wiring stay exactly as they are); this is an ADDITIONAL,
#   flag-gated executable-pair layer consumed at the Kelly/admission choke point
#   (src/engine/event_reactor_adapter.py::_event_bound_execution_probability_pair).
#
#   CLIMATOLOGY LESSON (2026-06-14, K3 rebuild, preserved here): pooling must never
#   collapse a genuinely sharp cell's conditional skill into a base rate. The
#   exact-cell LOCAL_SHIELD level is checked FIRST and, when LICENSED, blocks any
#   parent-level shrink from ever applying -- a calibrated city/metric/band/direction
#   cell is never punished for a broken sibling cohort.
#
#   NO EMERGENCY LOW-N PATH (operator minimalism): the only test gating a shrink is
#   n >= cohort min_n AND the Jeffreys one-sided 95% upper credible bound on the
#   cohort's realized win-rate posterior falling below claimed_mean_q - 0.05. There
#   is no secondary heuristic, no "if n is really small do X instead" branch.
#
#   SHRINK IS ONE-SIDED (mirrors K3): q_exec/q_lcb_exec only ever move DOWN from the
#   raw claim, never up. There is no UP arm (P3_architecture.md KILL: N7
#   bidirectional rewrite carries forward to this module).
"""settlement_coverage_hierarchy -- F1 hierarchical settlement-coverage calibrator.

Builds an EXECUTABLE (q_exec, q_lcb_exec) pair from a RAW claimed (q_raw, q_lcb_raw)
by walking a cohort hierarchy of increasing pooling breadth and applying the FIRST
level that is LICENSED-or-UNLICENSED (i.e. not INSUFFICIENT_DATA):

  Level 0  LOCAL_SHIELD         exact (city, metric, band_template, direction), min_n=30.
  Level 1  STRATEGY_BUCKET      (strategy_key, direction, q_bucket_0.05), min_n=30,
                                 >=8 distinct target dates, >=4 distinct (city, metric).
  Level 1b STRATEGY_SUPERBUCKET (strategy_key, direction, q in [0.75, 0.95]), min_n=50;
                                 tried only when no Level-1 0.05 bucket qualifies.
  Level 2  CROSS_STRATEGY       (direction, q_bucket_0.05) across canonical strategies,
                                 min_n=80, >=2 canonical strategies each n>=20, no single
                                 strategy >70% of n, leave-one-strategy-out holds.
  Level 3  GLOBAL               (q_bucket_0.05) pooled across BOTH direction and
                                 strategy, min_n=120, >=3 canonical strategies n>=20
                                 each, leave-one-strategy-out holds.

Estimator: Jeffreys beta-binomial. A cohort of (wins=w, n) observations with mean
claimed probability ``claimed_mean_q`` has realized-rate posterior Beta(w+0.5,
n-w+0.5) (the Jeffreys prior avoids the 0/1 degeneracy of a raw MLE). UNLICENSED iff
n >= min_n AND the one-sided 95% upper credible bound of that posterior is below
``claimed_mean_q - 0.05``. On UNLICENSED, q_exec = min(q_raw, posterior_mean) and
q_lcb_exec = min(q_lcb_raw, posterior_5pct_quantile - 0.01), both clamped to [0, 1].

q-buckets are 0.05-wide over the high-confidence region [0.75, 0.95] (closed at the
top edge: [0.90, 0.95]); [0.70, 0.75) is tracked as its own bucket but NEVER pooled
upward into the high-confidence super-bucket (Level 1b) or with any adjacent bucket.

Unknown/unrecognized strategy keys never form their own Level-1/1b cohort and are
excluded from the Level-2/3 canonical-strategy diversity/heterogeneity counts; they
still contribute observations to Level-2/3 POOLS (they cannot escape the shrink),
they just cannot be counted as one of the required canonical strategies.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

logger = logging.getLogger(__name__)

CoverageStatus = Literal["LICENSED", "UNLICENSED", "INSUFFICIENT_DATA"]
CohortLevel = Literal[
    "LOCAL_SHIELD",
    "STRATEGY_BUCKET",
    "STRATEGY_SUPERBUCKET",
    "CROSS_STRATEGY",
    "GLOBAL",
]

UNKNOWN_STRATEGY_KEY = "UNKNOWN"
ESTIMATOR_NAME = "jeffreys_v1"

# One-sided honesty tolerance (mirrors K3): the cohort's Jeffreys upper-95 bound
# must sit at least this far BELOW the claimed mean before the claim is refuted.
_COVERAGE_TOL = 0.05
# Honesty margin applied to the LCB shrink target (never shrink exactly TO the
# posterior 5th percentile -- always strictly below it, one-sided).
_SHRINK_MARGIN = 0.01

_JEFFREYS_UPPER_CONF = 0.95
_JEFFREYS_LOWER_CONF = 0.05

_BUCKET_WIDTH = 0.05
_HIGH_CONF_LO = 0.75
_HIGH_CONF_HI = 0.95
_MONITORED_LO = 0.70

_LEVEL0_MIN_N = 30

_LEVEL1_MIN_N = 30
_LEVEL1_MIN_DATES = 8
_LEVEL1_MIN_CITY_METRIC_PAIRS = 4

_LEVEL1B_MIN_N = 50

_LEVEL2_MIN_N = 80
_LEVEL2_MIN_QUALIFYING_STRATEGIES = 2
_LEVEL2_MIN_STRATEGY_N = 20
_LEVEL2_MAX_STRATEGY_SHARE = 0.70

_LEVEL3_MIN_N = 120
_LEVEL3_MIN_QUALIFYING_STRATEGIES = 3
_LEVEL3_MIN_STRATEGY_N = 20

# The leave-one-strategy-out control re-tests the Jeffreys-unlicensed verdict with
# each canonical strategy's observations excluded in turn. Its floor is
# DELIBERATELY decoupled from the level's own cohort min_n (Level 2/3 cohorts are
# built from exactly 2-3 qualifying strategies at their per-strategy floor, so
# gating the remainder at the FULL cohort min_n would make every single-exclusion
# remainder too small to ever test, vacuously passing the control). A small
# absolute floor is enough to keep the Jeffreys test meaningful on the remainder.
_LEAVE_ONE_OUT_MIN_REMAINDER = 10


def _canonical_strategy_keys() -> frozenset[str]:
    """Single source of truth for canonical strategy keys (src.state.db)."""
    from src.state.db import CANONICAL_STRATEGY_KEYS

    return CANONICAL_STRATEGY_KEYS


def canonicalize_strategy_key(strategy_key: str | None) -> str:
    """Canonicalize a strategy key against CANONICAL_STRATEGY_KEYS.

    Unknown / missing / unrecognized keys collapse to ``UNKNOWN_STRATEGY_KEY`` so
    they can never form their own Level-1/1b cohort nor count toward the
    Level-2/3 canonical-strategy diversity requirements (they still enter the
    Level-2/3 POOLS -- they cannot escape a pooled shrink, they simply cannot
    license one on their own).
    """
    key = str(strategy_key or "").strip()
    if key in _canonical_strategy_keys():
        return key
    return UNKNOWN_STRATEGY_KEY


# ---------------------------------------------------------------------------
# q-bucket assignment
# ---------------------------------------------------------------------------


def q_bucket_bounds(q: float) -> tuple[float, float]:
    """Return the (lo, hi) 0.05-wide bucket containing ``q``.

    Half-open [lo, hi) except the top high-confidence bucket [0.90, 0.95], which
    is closed at both ends (q == 0.95 exactly, or above, folds into it -- there is
    no bucket defined past 0.95 for this problem). q == 0.80 exactly lands in
    [0.80, 0.85), never [0.75, 0.80) (floor semantics on the 0.05 grid).
    """
    qf = float(q)
    grid = qf / _BUCKET_WIDTH
    lo_idx = math.floor(grid + 1e-9)
    lo = round(lo_idx * _BUCKET_WIDTH, 2)
    hi = round(lo + _BUCKET_WIDTH, 2)
    if lo >= _HIGH_CONF_HI:
        return (round(_HIGH_CONF_HI - _BUCKET_WIDTH, 2), _HIGH_CONF_HI)
    return (lo, hi)


def q_bucket_key(q: float) -> str:
    lo, hi = q_bucket_bounds(q)
    return f"{lo:.2f}-{hi:.2f}"


def is_high_confidence_bucket(lo: float, hi: float) -> bool:
    """True for the four 0.05 buckets spanning [0.75, 0.80) .. [0.90, 0.95]."""
    return lo >= _HIGH_CONF_LO - 1e-9 and hi <= _HIGH_CONF_HI + 1e-9


def is_monitored_only_bucket(lo: float, hi: float) -> bool:
    """[0.70, 0.75): tracked but NEVER pooled with the high-confidence buckets."""
    return abs(lo - _MONITORED_LO) < 1e-9 and abs(hi - _HIGH_CONF_LO) < 1e-9


def _in_super_bucket(q: float) -> bool:
    return _HIGH_CONF_LO - 1e-9 <= float(q) <= _HIGH_CONF_HI + 1e-9


# ---------------------------------------------------------------------------
# Jeffreys beta-binomial estimator (scipy.stats.beta primary; dependency-free
# numerical fallback verified against scipy at module test time).
# ---------------------------------------------------------------------------


def jeffreys_posterior_params(wins: int, n: int) -> tuple[float, float]:
    """Beta(w+0.5, n-w+0.5) -- the Jeffreys prior posterior on the win-rate."""
    w = float(wins)
    return (w + 0.5, float(n) - w + 0.5)


def _beta_pdf(x: float, a: float, b: float) -> float:
    if x <= 0.0 or x >= 1.0:
        return 0.0
    log_norm = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    return math.exp(log_norm + (a - 1.0) * math.log(x) + (b - 1.0) * math.log(1.0 - x))


def _beta_cdf_fallback(x: float, a: float, b: float, steps: int = 4000) -> float:
    """Dependency-free Beta CDF via Simpson's rule (used only when scipy is absent)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    n = steps if steps % 2 == 0 else steps + 1
    h = x / n
    eps = 1e-9
    total = _beta_pdf(eps, a, b) + _beta_pdf(x - eps, a, b)
    for i in range(1, n):
        xi = min(i * h, x - eps)
        coef = 4.0 if i % 2 else 2.0
        total += coef * _beta_pdf(xi, a, b)
    return min(1.0, max(0.0, total * h / 3.0))


def _beta_ppf_fallback(p: float, a: float, b: float, tol: float = 1e-9, max_iter: int = 200) -> float:
    """Dependency-free Beta quantile via bisection on ``_beta_cdf_fallback``."""
    lo, hi = 0.0, 1.0
    mid = 0.5
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        c = _beta_cdf_fallback(mid, a, b)
        if abs(c - p) < tol:
            return mid
        if c < p:
            lo = mid
        else:
            hi = mid
    return mid


def beta_quantile(p: float, a: float, b: float) -> float:
    """Beta(a, b) quantile at probability ``p``. scipy.stats.beta when available."""
    try:
        from scipy.stats import beta as _beta_dist

        return float(_beta_dist.ppf(p, a, b))
    except Exception:
        return _beta_ppf_fallback(p, a, b)


def jeffreys_posterior_mean(wins: int, n: int) -> float:
    a, b = jeffreys_posterior_params(wins, n)
    return a / (a + b)


def jeffreys_quantile(wins: int, n: int, p: float) -> float:
    a, b = jeffreys_posterior_params(wins, n)
    return beta_quantile(p, a, b)


def jeffreys_upper95(wins: int, n: int) -> float:
    return jeffreys_quantile(wins, n, _JEFFREYS_UPPER_CONF)


def jeffreys_lower05(wins: int, n: int) -> float:
    return jeffreys_quantile(wins, n, _JEFFREYS_LOWER_CONF)


def jeffreys_is_unlicensed(wins: int, n: int, claimed_mean_q: float, *, tol: float = _COVERAGE_TOL) -> bool:
    """The single Jeffreys-upper test -- NO emergency low-n path (operator law).

    UNLICENSED iff the one-sided 95% upper credible bound of the realized-rate
    posterior is below ``claimed_mean_q - tol``.
    """
    upper = jeffreys_upper95(wins, n)
    return upper < (float(claimed_mean_q) - tol)


def coverage_status_for_cohort(*, wins: int, n: int, claimed_mean_q: float, min_n: int) -> CoverageStatus:
    """INSUFFICIENT_DATA iff n < min_n; else the Jeffreys-upper verdict."""
    if n < min_n:
        return "INSUFFICIENT_DATA"
    return "UNLICENSED" if jeffreys_is_unlicensed(wins, n, claimed_mean_q) else "LICENSED"


# ---------------------------------------------------------------------------
# Observation stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HierarchyObservation:
    """One settled decision usable by the hierarchy calibrator.

    ``strategy_key`` MUST be canonicalized (``canonicalize_strategy_key``) by the
    caller before construction -- an unrecognized value must already read
    ``UNKNOWN_STRATEGY_KEY`` here, never a raw unvalidated string.
    ``settlement_time`` is the wall-clock time the settlement was finalized
    (VERIFIED authority stamp); used by ``filter_observations_prefix`` for the
    walk-forward admissibility check. ``won`` MUST be graded via the spine
    ``grade_receipt`` Direction Law -- never a hand-set bool.
    """

    condition_or_market_id: str
    target_date: str
    city: str
    metric: str
    band_template: str
    direction: str
    strategy_key: str
    q_raw: float
    won: bool
    settlement_time: str = ""


def dedupe_observations(observations: Iterable[HierarchyObservation]) -> list[HierarchyObservation]:
    """One observation per (market-or-condition-id, target_date, city, metric,
    band_template, direction, strategy_key). Last-seen wins (mirrors K3's
    "last-written receipt's claim stood" rule for same-day multi-fill dedup) --
    callers should iterate observations in chronological order.
    """
    seen: dict[tuple, HierarchyObservation] = {}
    for obs in observations:
        key = (
            str(obs.condition_or_market_id),
            str(obs.target_date),
            str(obs.city),
            str(obs.metric),
            str(obs.band_template),
            str(obs.direction),
            str(obs.strategy_key),
        )
        seen[key] = obs
    return list(seen.values())


def filter_observations_prefix(
    observations: Sequence[HierarchyObservation], as_of: str
) -> list[HierarchyObservation]:
    """STRICT walk-forward filter: keep only observations whose settlement
    finalized strictly BEFORE ``as_of`` (the decision time). An observation with
    no ``settlement_time`` stamp, or one at/after ``as_of``, is excluded -- a
    decision at time T may only see settlements PROVEN finalized before T.
    """
    return [obs for obs in observations if obs.settlement_time and obs.settlement_time < as_of]


# ---------------------------------------------------------------------------
# Cohort statistics + heterogeneity controls
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CohortStats:
    level: CohortLevel
    cohort_key: str
    n: int
    wins: int
    claimed_mean_q: float
    status: CoverageStatus
    posterior_mean: float
    posterior_upper95: float
    posterior_lower05: float


def _claimed_mean(obs: Sequence[HierarchyObservation]) -> float:
    n = len(obs)
    if not n:
        return 0.0
    return sum(float(o.q_raw) for o in obs) / n


def _cohort_stats(level: CohortLevel, cohort_key: str, obs: Sequence[HierarchyObservation], *, min_n: int) -> CohortStats:
    n = len(obs)
    wins = sum(1 for o in obs if o.won)
    claimed_mean = _claimed_mean(obs)
    if n < min_n:
        return CohortStats(
            level=level, cohort_key=cohort_key, n=n, wins=wins, claimed_mean_q=claimed_mean,
            status="INSUFFICIENT_DATA", posterior_mean=claimed_mean, posterior_upper95=claimed_mean,
            posterior_lower05=claimed_mean,
        )
    mean = jeffreys_posterior_mean(wins, n)
    upper = jeffreys_upper95(wins, n)
    lower = jeffreys_lower05(wins, n)
    status: CoverageStatus = "UNLICENSED" if upper < claimed_mean - _COVERAGE_TOL else "LICENSED"
    return CohortStats(
        level=level, cohort_key=cohort_key, n=n, wins=wins, claimed_mean_q=claimed_mean,
        status=status, posterior_mean=mean, posterior_upper95=upper, posterior_lower05=lower,
    )


def _canonical_strategy_breakdown(obs: Sequence[HierarchyObservation]) -> dict[str, list[HierarchyObservation]]:
    """Only CANONICAL strategy members count toward the diversity/heterogeneity
    controls -- unknown/missing keys still occupy the pool but are never counted
    as one of the required distinct canonical strategies."""
    canon = _canonical_strategy_keys()
    out: dict[str, list[HierarchyObservation]] = {}
    for obs_item in obs:
        if obs_item.strategy_key in canon:
            out.setdefault(obs_item.strategy_key, []).append(obs_item)
    return out


def _leave_one_strategy_out_holds(obs: Sequence[HierarchyObservation]) -> bool:
    """Re-run the Jeffreys-unlicensed test with each canonical strategy excluded
    in turn; overconfidence must SURVIVE every exclusion (when the remainder is
    still large enough to test) for the pooled cohort to be eligible. A strategy
    whose removal drops n below ``min_n`` is skipped (cannot test exclusion on a
    remainder that would itself be thin)."""
    by_strategy = _canonical_strategy_breakdown(obs)
    if not by_strategy:
        return True
    for strategy in by_strategy:
        remaining = [o for o in obs if o.strategy_key != strategy]
        n = len(remaining)
        if n < _LEAVE_ONE_OUT_MIN_REMAINDER:
            continue
        wins = sum(1 for o in remaining if o.won)
        claimed_mean = _claimed_mean(remaining)
        if not jeffreys_is_unlicensed(wins, n, claimed_mean):
            return False
    return True


def _level2_eligible(obs: Sequence[HierarchyObservation]) -> bool:
    if len(obs) < _LEVEL2_MIN_N:
        return False
    by_strategy = _canonical_strategy_breakdown(obs)
    qualifying = [s for s, members in by_strategy.items() if len(members) >= _LEVEL2_MIN_STRATEGY_N]
    if len(qualifying) < _LEVEL2_MIN_QUALIFYING_STRATEGIES:
        return False
    max_n = max((len(members) for members in by_strategy.values()), default=0)
    if (max_n / len(obs)) > _LEVEL2_MAX_STRATEGY_SHARE:
        return False
    return _leave_one_strategy_out_holds(obs)


def _level3_eligible(obs: Sequence[HierarchyObservation]) -> bool:
    if len(obs) < _LEVEL3_MIN_N:
        return False
    by_strategy = _canonical_strategy_breakdown(obs)
    qualifying = [s for s, members in by_strategy.items() if len(members) >= _LEVEL3_MIN_STRATEGY_N]
    if len(qualifying) < _LEVEL3_MIN_QUALIFYING_STRATEGIES:
        return False
    return _leave_one_strategy_out_holds(obs)


# ---------------------------------------------------------------------------
# Executable pair
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutablePair:
    """The (q_exec, q_lcb_exec) pair licensed for live consumption, plus provenance.

    ``q_raw``/``q_lcb_raw`` are carried through unchanged (audit law: the frozen
    certificate is never mutated by this module). ``level``/``cohort_key`` are
    ``None`` only when NO cohort in the hierarchy reached a licensed verdict
    (terminal INSUFFICIENT_DATA no-op).
    """

    status: CoverageStatus
    level: CohortLevel | None
    cohort_key: str | None
    n: int
    wins: int
    q_raw: float
    q_lcb_raw: float
    q_exec: float
    q_lcb_exec: float
    estimator: str = ESTIMATOR_NAME


def _apply(stats: CohortStats, *, q_raw: float, q_lcb_raw: float) -> ExecutablePair:
    q_raw_f = float(q_raw)
    q_lcb_raw_f = float(q_lcb_raw)
    if stats.status == "UNLICENSED":
        q_exec = max(0.0, min(1.0, min(q_raw_f, stats.posterior_mean)))
        q_lcb_exec = max(0.0, min(1.0, min(q_lcb_raw_f, stats.posterior_lower05 - _SHRINK_MARGIN)))
    else:
        # LICENSED: this level's evidence backs the claim -- no shrink.
        q_exec = q_raw_f
        q_lcb_exec = q_lcb_raw_f
    return ExecutablePair(
        status=stats.status, level=stats.level, cohort_key=stats.cohort_key, n=stats.n, wins=stats.wins,
        q_raw=q_raw_f, q_lcb_raw=q_lcb_raw_f, q_exec=q_exec, q_lcb_exec=q_lcb_exec,
    )


def _no_op(*, q_raw: float, q_lcb_raw: float, n: int = 0, wins: int = 0) -> ExecutablePair:
    return ExecutablePair(
        status="INSUFFICIENT_DATA", level=None, cohort_key=None, n=n, wins=wins,
        q_raw=float(q_raw), q_lcb_raw=float(q_lcb_raw), q_exec=float(q_raw), q_lcb_exec=float(q_lcb_raw),
    )


def hierarchical_coverage_check(
    *,
    city: str,
    metric: str,
    band_template: str,
    direction: str,
    strategy_key: str | None,
    q_raw: float,
    q_lcb_raw: float,
    observations: Sequence[HierarchyObservation],
) -> ExecutablePair:
    """Walk the cohort hierarchy and return the executable pair.

    ``observations`` is the FULL prefix-filtered, deduped observation stream
    (any city/metric/band/direction/strategy) -- this function does the scope
    filtering internally so callers do not need to pre-partition the stream.
    """
    strategy = canonicalize_strategy_key(strategy_key)
    direction_s = str(direction)
    city_s = str(city)
    metric_s = str(metric).lower()
    band_s = str(band_template)

    # Level 0: exact-cell local-skill shield. LICENSED or UNLICENSED here is
    # FINAL -- a shielded (LICENSED) cell never inherits a parent's shrink, and
    # an UNLICENSED exact cell IS its own proof (no need to consult a parent).
    exact_obs = [
        o for o in observations
        if o.city == city_s and o.metric.lower() == metric_s and o.band_template == band_s
        and o.direction == direction_s
    ]
    exact_stats = _cohort_stats(
        "LOCAL_SHIELD", f"{city_s}|{metric_s}|{band_s}|{direction_s}", exact_obs, min_n=_LEVEL0_MIN_N
    )
    if exact_stats.status != "INSUFFICIENT_DATA":
        return _apply(exact_stats, q_raw=q_raw, q_lcb_raw=q_lcb_raw)

    lo, hi = q_bucket_bounds(q_raw)
    bucket_key = q_bucket_key(q_raw)

    if strategy in _canonical_strategy_keys():
        # Level 1: strategy cohort at the 0.05 bucket.
        strat_bucket_obs = [
            o for o in observations
            if o.strategy_key == strategy and o.direction == direction_s
            and q_bucket_bounds(o.q_raw) == (lo, hi)
        ]
        distinct_dates = {o.target_date for o in strat_bucket_obs}
        distinct_city_metric = {(o.city, o.metric.lower()) for o in strat_bucket_obs}
        if (
            len(strat_bucket_obs) >= _LEVEL1_MIN_N
            and len(distinct_dates) >= _LEVEL1_MIN_DATES
            and len(distinct_city_metric) >= _LEVEL1_MIN_CITY_METRIC_PAIRS
        ):
            stats = _cohort_stats(
                "STRATEGY_BUCKET", f"{strategy}|{direction_s}|{bucket_key}", strat_bucket_obs, min_n=_LEVEL1_MIN_N
            )
            if stats.status != "INSUFFICIENT_DATA":
                return _apply(stats, q_raw=q_raw, q_lcb_raw=q_lcb_raw)

        # Level 1b: strategy super-bucket [0.75, 0.95] -- ONLY when q itself sits
        # in the high-confidence region AND no 0.05 bucket qualified above.
        if _in_super_bucket(q_raw):
            super_obs = [
                o for o in observations
                if o.strategy_key == strategy and o.direction == direction_s and _in_super_bucket(o.q_raw)
            ]
            if len(super_obs) >= _LEVEL1B_MIN_N:
                stats = _cohort_stats(
                    "STRATEGY_SUPERBUCKET", f"{strategy}|{direction_s}|super_0.75_0.95", super_obs,
                    min_n=_LEVEL1B_MIN_N,
                )
                if stats.status != "INSUFFICIENT_DATA":
                    return _apply(stats, q_raw=q_raw, q_lcb_raw=q_lcb_raw)

    # Level 2: cross-strategy, same direction, same 0.05 bucket.
    cross_obs = [
        o for o in observations if o.direction == direction_s and q_bucket_bounds(o.q_raw) == (lo, hi)
    ]
    if _level2_eligible(cross_obs):
        stats = _cohort_stats("CROSS_STRATEGY", f"{direction_s}|{bucket_key}", cross_obs, min_n=_LEVEL2_MIN_N)
        if stats.status != "INSUFFICIENT_DATA":
            return _apply(stats, q_raw=q_raw, q_lcb_raw=q_lcb_raw)

    # Level 3: global -- pooled across BOTH direction and strategy, same bucket.
    global_obs = [o for o in observations if q_bucket_bounds(o.q_raw) == (lo, hi)]
    if _level3_eligible(global_obs):
        stats = _cohort_stats("GLOBAL", bucket_key, global_obs, min_n=_LEVEL3_MIN_N)
        if stats.status != "INSUFFICIENT_DATA":
            return _apply(stats, q_raw=q_raw, q_lcb_raw=q_lcb_raw)

    return _no_op(q_raw=q_raw, q_lcb_raw=q_lcb_raw, n=exact_stats.n, wins=exact_stats.wins)
