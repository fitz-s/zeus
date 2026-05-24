# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: operator hierarchical-bias adjudication 2026-05-24
#   (HIERARCHICAL_LIVE_PRODUCT_BIAS_CORRECTION_REQUIRED). Empirical-Bayes
#   posterior shrinkage of a TIGGE structural prior toward the OpenData live
#   settled-residual likelihood, with the TIGGE↔OpenData same-window paired
#   discrepancy treated as transfer *uncertainty* (prior-variance inflation),
#   not as a binary valid/invalid transfer.
"""Hierarchical ENS forecast-bias estimator (pure math, no DB/pipeline coupling).

Problem this solves
--------------------
The live forecast product (``ecmwf_opendata_mx2t3`` 51-member ENS) runs cold vs
settled actuals (~-1.1 degC on the contributing snapshots the engine uses). The
correction must be learned from the *live product's own* (forecast - actual)
residuals — but the live product has only ~1 month of history, insufficient for
per-(city, season) coverage. TIGGE (``mx2t6``) has ~2 years of complete
seasonal/monthly structure, but its same-window equivalence to OpenData is
empirically unresolved (underpowered, n~56), so it cannot be used as an
unconditional truth.

The mathematically optimal combine (minimum expected squared error under the
stated Gaussian model) is the posterior mean of a normal prior (TIGGE) updated
by a normal likelihood (OpenData live):

    prior:       theta ~ N(mu_T + delta_g, V0)
    likelihood:  e_bar | theta ~ N(theta, V_O),  V_O = sigma^2 / n_live
    posterior:   theta | data ~ N( w*e_bar + (1-w)*(mu_T+delta_g),  V_post )
                 w = V0 / (V0 + V_O)
                 V_post = (1/V0 + 1/V_O)^-1   (<= min(V0, V_O))

Sign convention: ``bias = mean(forecast - actual)``; negative = forecast cold.
Downstream the correction SUBTRACTS the bias from member extrema BEFORE the
Monte Carlo (``corrected = raw - bias``), so a cold (negative) bias warms the
forecast. The pre-MC application is wired by the caller, not here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ESTIMATOR_NAME = "empirical_bayes_shrinkage_v1"


@dataclass(frozen=True)
class BiasPrior:
    """TIGGE structural prior for a (city, season[, month, cluster]) bucket.

    mu_t : prior mean of (forecast - actual), degC.
    v0   : prior variance (degC^2) — TIGGE estimation variance plus any baseline
           transfer uncertainty. Larger v0 = less trust in the prior.
    """

    mu_t: float
    v0: float

    def __post_init__(self) -> None:
        if self.v0 <= 0:
            raise ValueError("prior variance v0 must be positive")


@dataclass(frozen=True)
class LiveResidual:
    """OpenData live settled-residual likelihood for the same bucket.

    e_bar  : mean of (forecast - actual) over live settled pairs, degC.
    n      : number of live settled pairs.
    sigma2 : per-observation residual variance (degC^2); V_O = sigma2 / n.
    """

    e_bar: float
    n: int
    sigma2: float

    def __post_init__(self) -> None:
        if self.sigma2 < 0:
            raise ValueError("sigma2 must be non-negative")


@dataclass(frozen=True)
class PosteriorBias:
    """Posterior live-product bias estimate for a bucket."""

    bias: float          # posterior mean of (forecast - actual), degC
    sd: float            # posterior standard deviation, degC (for Kelly haircut)
    weight_live: float   # w = V0/(V0+V_O), in [0, 1]
    n_live: int
    estimator: str = ESTIMATOR_NAME


def posterior_bias(
    prior: BiasPrior,
    live: LiveResidual | None,
    *,
    delta_g: float = 0.0,
    paired_delta_abs: float | None = None,
) -> PosteriorBias:
    """Empirical-Bayes posterior bias for one bucket.

    Parameters
    ----------
    prior : TIGGE structural prior (mean + variance).
    live  : OpenData live likelihood, or ``None`` if no live settled pairs exist
            for this bucket (then the posterior is the prior).
    delta_g : group-level transfer offset added to the prior mean (e.g. a
            coastal/cluster/unit/hemisphere correction learned from paired data).
    paired_delta_abs : absolute same-window paired OpenData-TIGGE mean delta for
            this bucket/group, if measured. When large it signals the prior is a
            poor transfer; we inflate the prior variance by ``paired_delta_abs**2``
            so live evidence dominates wherever it exists. ``None`` or 0 = no
            inflation.

    Returns
    -------
    PosteriorBias with the shrinkage mean, posterior SD, and the live weight.
    """
    prior_mean = prior.mu_t + delta_g

    v0 = prior.v0
    if paired_delta_abs is not None and paired_delta_abs > 0.0:
        # Treat the unexplained product offset as additional prior spread:
        # the prior could be wrong by ~paired_delta, so add delta^2 to its variance.
        v0 = v0 + paired_delta_abs * paired_delta_abs

    if live is None or live.n <= 0:
        return PosteriorBias(
            bias=prior_mean,
            sd=math.sqrt(v0),
            weight_live=0.0,
            n_live=0,
        )

    v_o = live.sigma2 / live.n
    if v_o <= 0.0:
        # Degenerate: live evidence is exact — collapse to the live mean.
        return PosteriorBias(
            bias=live.e_bar,
            sd=0.0,
            weight_live=1.0,
            n_live=live.n,
        )

    w = v0 / (v0 + v_o)
    bias = w * live.e_bar + (1.0 - w) * prior_mean
    v_post = 1.0 / (1.0 / v0 + 1.0 / v_o)
    return PosteriorBias(
        bias=bias,
        sd=math.sqrt(v_post),
        weight_live=w,
        n_live=live.n,
    )
