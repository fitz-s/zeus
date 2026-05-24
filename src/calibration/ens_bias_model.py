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
import statistics
from dataclasses import dataclass

ESTIMATOR_NAME = "empirical_bayes_shrinkage_v1"

# Irreducible transfer-uncertainty floor (degC^2) added to the TIGGE prior
# variance. TIGGE is a *different product* from the live OpenData ENS, so even
# with infinite TIGGE history the prior bias for the live product stays
# uncertain by ~sqrt(V_TRANSFER_DEFAULT). This is what lets abundant live
# evidence overcome the prior; tune per validated equivalence.
V_TRANSFER_DEFAULT = 0.25  # (0.5 degC)^2
DISAGREEMENT_K = 2.0  # |live-prior| > K*sqrt(V0+V_O) => flag prior<->live conflict


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
    disagreement_high: bool = False   # prior<->live conflict beyond combined SD (gate live use)
    heterogeneity_var: float = 0.0    # excess between-source variance (degC^2); add to sd for haircut
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
    combined = v0 + v_o
    gap = live.e_bar - prior_mean
    het_var = max(0.0, gap * gap - combined)           # between-source excess variance
    disagreement = abs(gap) > DISAGREEMENT_K * math.sqrt(combined)
    return PosteriorBias(
        bias=bias,
        sd=math.sqrt(v_post),                            # strict precision-combine SD
        weight_live=w,
        n_live=live.n,
        disagreement_high=disagreement,
        heterogeneity_var=het_var,
    )


def robust_mean(xs: list[float], trim: float = 0.1) -> float:
    """Symmetric trimmed mean — rejects a few outlier days (heat events, misses).

    Drops floor(trim*n) samples from each tail; falls back to the plain mean for
    very small samples where trimming would leave nothing.
    """
    n = len(xs)
    if n == 0:
        raise ValueError("robust_mean of empty sample")
    if n < 3:
        return statistics.fmean(xs)
    k = int(n * trim)
    core = sorted(xs)[k : n - k] if (n - 2 * k) >= 1 else xs
    return statistics.fmean(core)


def _var_of_mean(xs: list[float], floor: float) -> float:
    """Variance of the sample mean = sample_variance / n, with a small floor."""
    n = len(xs)
    if n < 2:
        # n<2 has no sample variance; return only the small floor. The caller
        # (fit_bucket) adds the transfer-uncertainty term separately — adding it
        # here too would double-count it for a 1-sample prior (Copilot #334).
        return floor
    return max(statistics.variance(xs) / n, floor)


def fit_bucket(
    tigge_residuals: list[float],
    opendata_residuals: list[float],
    *,
    paired_delta_abs: float | None = None,
    min_live_n: int = 20,
    v_transfer: float = V_TRANSFER_DEFAULT,
    trim: float = 0.1,
    var_floor: float = 1e-6,
) -> PosteriorBias:
    """Fit the posterior live-product bias for one (city, season, ...) bucket.

    Builds a TIGGE structural prior (robust mean; variance = var-of-mean +
    transfer floor) and, when enough live settled pairs exist, an OpenData
    likelihood (robust mean; variance-of-mean), then combines via
    ``posterior_bias``. ``tigge_residuals`` and ``opendata_residuals`` are lists
    of (forecast - actual) in degC for the bucket. Live evidence below
    ``min_live_n`` is dropped (posterior == prior).
    """
    if not tigge_residuals:
        raise ValueError("fit_bucket requires a non-empty TIGGE prior sample")

    mu_t = robust_mean(tigge_residuals, trim)
    v0 = _var_of_mean(tigge_residuals, var_floor) + v_transfer
    prior = BiasPrior(mu_t=mu_t, v0=v0)

    live: LiveResidual | None = None
    if len(opendata_residuals) >= min_live_n:
        e_bar = robust_mean(opendata_residuals, trim)
        var_o = max(statistics.variance(opendata_residuals), var_floor)
        live = LiveResidual(e_bar=e_bar, n=len(opendata_residuals), sigma2=var_o)

    return posterior_bias(prior, live, paired_delta_abs=paired_delta_abs)


def apply_bias_to_extrema(member_extrema, posterior: PosteriorBias):
    """Apply the posterior bias correction to per-member daily extrema, PRE-Monte-Carlo.

    ``corrected = raw - bias`` (bias = forecast - actual), so a cold (negative)
    bias warms the forecast. Correction MUST happen here, before binning + MC +
    rounding, because those steps are non-linear (you cannot shift the posterior
    probability vector instead). Accepts a numpy array; returns a numpy array.
    """
    import numpy as np

    arr = np.asarray(member_extrema, dtype=float)
    return arr - posterior.bias


def assert_bias_state_consistent(*, live_bias_enabled: bool, platt_bias_corrected: bool) -> None:
    """Train/serve invariant: if live signals are bias-corrected, the active Platt
    model MUST have been fit on bias_corrected=1 calibration pairs.

    Enabling live correction while Platt was trained on uncorrected p_raw moves the
    live p_raw into a different calibration input space (out-of-domain inference).
    Raises ValueError on that mismatch; all other states are benign.
    """
    if live_bias_enabled and not platt_bias_corrected:
        raise ValueError(
            "train/serve bias-state mismatch: live bias correction is enabled but the "
            "active Platt model was fit on uncorrected calibration_pairs "
            "(bias_corrected=0). Recompute corrected pairs and refit Platt before "
            "enabling bias_correction."
        )


def transport_bias_prior(
    *,
    b50: float,
    sd50: float,
    delta_samples: list[float],
    kappa: float = 1.0,
) -> BiasPrior:
    """Transport a 0.5/TIGGE (F50) bias prior to the live 0.25/OpenData (F25) lineage.

    The 0.5 calibration cannot be applied losslessly to the 0.25 product. The exact
    bias bridge is b25 = b50 + E[Δ] with Δ = F25 - F50 measured on PAIRED snapshots
    (no settlement needed). Prior variance inflates for transport uncertainty:

        v0_25 = sd50^2 + Var(Δ) + kappa · sd50 · sd_Δ

    kappa is the covariance allowance (0 = independent quadrature; 2 = worst-case fully
    correlated). The mean shift uses a robust (trimmed) mean so a few paired outliers do
    not move it; the variance uses the full spread (conservative). With no paired Δ the
    0.5 prior is returned unchanged — the safe fallback to the historical lineage.
    """
    if not delta_samples:
        return BiasPrior(mu_t=b50, v0=max(sd50 * sd50, 1e-12))
    d_mean = robust_mean(delta_samples)
    var_d = statistics.variance(delta_samples) if len(delta_samples) >= 2 else 0.0
    sd_d = math.sqrt(var_d)
    v0 = sd50 * sd50 + var_d + kappa * sd50 * sd_d
    return BiasPrior(mu_t=b50 + d_mean, v0=max(v0, 1e-12))
