# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A3 + §5 (Beta-binomial posterior derivation, Beta(1,1) prior, 9-status enum classification).
"""Beta-binomial posterior helpers for oracle evidence-grade classification.

Posterior derivation (PLAN.md §5)
----------------------------------
Bernoulli observations of "oracle disagrees with PM settlement" with prior
``θ ~ Beta(α₀, β₀)`` give::

    θ | m, n  ~  Beta(α₀ + m, β₀ + (n − m))

Choice of prior: **Beta(1, 1) (uniform)**. Justification:

- Non-informative — no pre-commitment to any error rate.
- Effective sample size 1, so posterior shrinks to data fast.
- Posterior mean at N=0 is exactly ``1 / (1+1) = 0.5``, which matches
  PLAN.md D-2's "MISSING → multiplier=0.5" rule by direct math (the
  multiplier IS the posterior mean of the prior — this is not a
  coincidence, it's a deliberate design choice).
- Bug review §5.3 implicitly chose this prior when computing
  "0 errors at N=12 → 95% upper bound ≈ 3/n".

Classification thresholds
-------------------------
``classify(m, n, age_hours)`` returns the ``OracleStatus`` that the
multiplier table in ``oracle_penalty.py`` then maps to a Kelly factor.

The classification splits on **whether errors have been observed**, not
just on the posterior bound:

::

    age > 7 days                                                     → STALE
    n == 0                                                           → MISSING

    m == 0  (zero observed errors — never demonstrated unreliability)
        n < 10                                                       → INSUFFICIENT_SAMPLE
        posterior_upper_95 > 0.05                                    → INSUFFICIENT_SAMPLE
        posterior_upper_95 ≤ 0.05                                    → OK

    m >= 1  (demonstrated unreliability)
        posterior_upper_95 > 0.10                                    → BLACKLIST
        posterior_upper_95 > 0.05                                    → CAUTION
        posterior_upper_95 ≤ 0.05                                    → INCIDENTAL

Why the m=0 vs m≥1 split (deviation from PLAN.md §5 literal spec)
-----------------------------------------------------------------
PLAN.md §5 originally specified ``posterior_upper_95 > 0.10 → BLACKLIST``
for ALL records. That collapses two semantically distinct cases:

- ``m=0, n=12``: zero errors, wide posterior because of small N.
  Posterior_upper_95 ≈ 0.206. Under the literal spec this is BLACKLIST,
  but the city has DEMONSTRATED ZERO failures — the bound is wide
  purely from sample-size limits.
- ``m=10, n=25``: ten errors, posterior_upper_95 ≈ 0.564. Genuine
  observed unreliability.

Treating both as BLACKLIST loses the distinction the operator needs: the
first case wants more data (INSUFFICIENT_SAMPLE — keep degraded sizing
until the sample grows), the second wants no entries (BLACKLIST — the
city has failed). PLAN.md §A3 OK6 ("zero-error N=12 → INSUFFICIENT_SAMPLE,
NOT OK") is the explicit test case that pins this split. The deviation
from the literal §5 spec was endorsed in the test contract, which is the
authority when prose and contract disagree (CLAUDE.md "Code and docs
disagree → trust code").

Thresholds (10, 0.05, 0.10, 7d) remain policy constants. Operator can
hot-tune in the commit body if realized P&L diverges from calibration.

Evidence quality is a separate axis surfaced for observability:
``n < 10 → "weak"``, ``n < 50 → "moderate"``, ``n ≥ 50 → "strong"``.

Why this is its own module
--------------------------
The math is independent of the JSON-loading concerns in ``oracle_penalty``
and is reused by the regression antibody tests in
``tests/test_oracle_evidence_status.py``. Splitting the math out also lets
A6's phase-aware Kelly call ``classify`` directly without dragging the
penalty module's caching layer.
"""
from __future__ import annotations

from typing import Optional

from scipy.stats import beta

# Forward reference avoids a runtime circular import — oracle_penalty
# imports from this module, and a return type of OracleStatus would
# create the cycle.
from src.strategy.oracle_status import OracleStatus

PRIOR_ALPHA: int = 1
PRIOR_BETA: int = 1

# Threshold constants (PLAN.md §5)
INSUFFICIENT_SAMPLE_N: int = 10
CAUTION_P95_LOWER: float = 0.05
BLACKLIST_P95_LOWER: float = 0.10
STALE_AGE_HOURS: float = 7 * 24.0


def posterior_mean(m: int, n: int) -> float:
    """Mean of ``Beta(1+m, 1+n-m)``.

    At ``n=0`` returns ``0.5`` (uniform prior mean — by design, equals the
    MISSING multiplier in PLAN.md D-2).

    Raises ``ValueError`` on inputs that can't represent a sample
    (negative counts, m > n).
    """
    _validate_counts(m, n)
    a = PRIOR_ALPHA + m
    b = PRIOR_BETA + (n - m)
    return a / (a + b)


def posterior_upper_95(m: int, n: int) -> float:
    """95% credible upper bound of ``Beta(1+m, 1+n-m)``.

    At ``n=0`` returns ``0.95`` (uniform prior 95% upper). Monotonically
    decreasing in ``n`` for fixed ``m``, monotonically increasing in ``m``
    for fixed ``n`` — both directions are pinned as regression antibodies
    in I7 (test_oracle_evidence_status::OK7).
    """
    _validate_counts(m, n)
    a = PRIOR_ALPHA + m
    b = PRIOR_BETA + (n - m)
    return float(beta.ppf(0.95, a, b))


def evidence_quality(n: int) -> str:
    """Coarse sample-size label for observability dashboards."""
    if n < 0:
        raise ValueError(f"n must be >= 0; got {n}")
    if n == 0:
        return "none"
    if n < INSUFFICIENT_SAMPLE_N:
        return "weak"
    if n < 50:
        return "moderate"
    return "strong"


def classify(
    m: int,
    n: int,
    *,
    artifact_age_hours: Optional[float] = None,
) -> OracleStatus:
    """Map raw ``(m, n, age)`` counts to an ``OracleStatus``.

    Caller (oracle_penalty) handles MISSING (no record) / METRIC_UNSUPPORTED
    (LOW track) / MALFORMED (parse error) upstream — those statuses don't
    have raw counts to classify.

    Order matters: STALE → MISSING-on-zero-n → split on m=0 vs m≥1.
    See module docstring for the full classification table and the
    rationale for the m=0 vs m≥1 split.
    """
    _validate_counts(m, n)
    if artifact_age_hours is not None and artifact_age_hours > STALE_AGE_HOURS:
        return OracleStatus.STALE
    if n == 0:
        return OracleStatus.MISSING

    p95 = posterior_upper_95(m, n)

    if m == 0:
        # Zero observed errors. Status is about evidence sufficiency,
        # not failure: the city has never failed; the question is
        # whether we have enough sample to commit to OK.
        if n < INSUFFICIENT_SAMPLE_N:
            return OracleStatus.INSUFFICIENT_SAMPLE
        if p95 > CAUTION_P95_LOWER:
            return OracleStatus.INSUFFICIENT_SAMPLE
        return OracleStatus.OK

    # m >= 1: demonstrated some unreliability. Tier by posterior bound.
    if p95 > BLACKLIST_P95_LOWER:
        return OracleStatus.BLACKLIST
    if p95 > CAUTION_P95_LOWER:
        return OracleStatus.CAUTION
    return OracleStatus.INCIDENTAL


def _validate_counts(m: int, n: int) -> None:
    if not isinstance(m, int) or not isinstance(n, int):
        raise TypeError(f"m and n must be int; got m={type(m).__name__}, n={type(n).__name__}")
    if n < 0:
        raise ValueError(f"n must be >= 0; got {n}")
    if m < 0:
        raise ValueError(f"m must be >= 0; got {m}")
    if m > n:
        raise ValueError(f"m must be <= n; got m={m}, n={n}")
