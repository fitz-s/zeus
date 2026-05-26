# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: operator universal predictive-error adjudication 2026-05-24.
#   Occam-minimal extension of the #334 mean-bias estimator: ONE scale parameter
#   (residual_sd) + ONE confidence gate (correction_strength from SNR). No per-city
#   rules, no feature stacking, no neural layer.
"""Universal predictive-error layer: location + scale + confidence gate.

The #334 layer estimates location bias E[forecast - actual]. That is necessary but
not sufficient for bin-probability trading: a pure mean shift cannot create tail
support when the whole ensemble sits in the wrong local regime (SF marine layer),
and a confident shift HURTS when the bias estimate is unstable (Chicago: prior
cold, live neutral). This layer adds, universally:

  - a SCALE term (residual_sd) folded into the Monte-Carlo predictive distribution
    (the forecast/station residual error, distinct from sensor/instrument noise);
  - a CONFIDENCE GATE (correction_strength λ from signal-to-noise) so the point
    shift is applied only when the bias is large relative to its uncertainty.

T_draw = member_extrema - λ·bias + N(0, total_residual_sd) + instrument_noise

The SAME parameters handle SF (large confident bias + wide residual) and Chicago
(disagreement → λ=0, widened → no-bet) with zero city-specific logic.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from src.calibration.ens_bias_model import PosteriorBias

# SNR gate breakpoints: below SNR_LO no shift; full shift at/above SNR_HI; linear between.
SNR_LO = 1.0
SNR_HI = 2.0
# Minimum paired-Δ samples required to trust the transport step.  Below this
# statistics.variance is undefined (n=1) so var_d=0.0, the prior keeps its raw
# variance but its mean is shifted by the entire single-date delta, and the SNR
# gate cannot suppress the resulting (possibly spurious) correction.
# Matches _ERROR_MODEL_MIN_LIVE_N in ens_bias_model.py (both are 5).
MIN_PAIRED_N = 5


def correction_strength(*, bias: float, bias_sd: float, heterogeneity_var: float) -> float:
    """Universal confidence gate λ ∈ [0, 1] from signal-to-noise.

    z = |bias| / sqrt(bias_sd^2 + heterogeneity_var)
      z < SNR_LO            -> 0    (uncertainty dominates: do not shift)
      SNR_LO <= z < SNR_HI  -> z-1  (partial)
      z >= SNR_HI           -> 1    (full)
    A perfectly certain bias (zero denominator) gets full strength.
    """
    denom = math.sqrt(max(0.0, bias_sd * bias_sd + heterogeneity_var))
    if denom <= 0.0:
        return 1.0
    z = abs(bias) / denom
    if z < SNR_LO:
        return 0.0
    if z >= SNR_HI:
        return 1.0
    return z - 1.0


@dataclass(frozen=True)
class PredictiveErrorModel:
    """Universal location+scale+gate forecast-error model for one bucket."""

    bias_c: float                 # posterior mean bias (forecast - actual), degC
    bias_sd_c: float              # posterior bias SD (strict precision-combine), degC
    residual_sd_c: float          # forecast/station residual scale, degC
    heterogeneity_var_c2: float   # prior<->live excess variance, degC^2
    disagreement_high: bool
    correction_strength: float    # λ ∈ [0, 1]
    effective_bias_c: float       # λ · bias_c (what is actually subtracted pre-MC)
    total_residual_sd_c: float    # sqrt(residual_sd^2 + heterogeneity_var) for the MC draw


def predictive_error_from_posterior(
    posterior: PosteriorBias,
    residual_sd_c: float,
) -> PredictiveErrorModel:
    """Build the predictive-error model from a #334 posterior + a residual scale.

    ``residual_sd_c`` is the bucket's forecast/station residual SD (from OOS residuals,
    shrunk like the bias). The total MC residual scale combines it with the
    prior<->live heterogeneity so disagreement also widens the predictive distribution.
    """
    if residual_sd_c < 0:
        raise ValueError("residual_sd_c must be non-negative")
    lam = correction_strength(
        bias=posterior.bias,
        bias_sd=posterior.sd,
        heterogeneity_var=posterior.heterogeneity_var,
    )
    total_sd = math.sqrt(residual_sd_c * residual_sd_c + posterior.heterogeneity_var)
    return PredictiveErrorModel(
        bias_c=posterior.bias,
        bias_sd_c=posterior.sd,
        residual_sd_c=residual_sd_c,
        heterogeneity_var_c2=posterior.heterogeneity_var,
        disagreement_high=posterior.disagreement_high,
        correction_strength=lam,
        effective_bias_c=lam * posterior.bias,
        total_residual_sd_c=total_sd,
    )


def _c_to_native_scale(member_unit: str | None) -> float:
    """Multiplier to convert a degC *delta* to the members' native unit (degF: x1.8)."""
    u = (member_unit or "").strip().lower()
    if u in {"f", "degf", "fahrenheit"} or (u and u.endswith("f")):
        return 1.8
    if u in {"c", "degc", "celsius"} or (u and u.endswith("c")):
        return 1.0
    raise ValueError(f"unknown member unit: {member_unit!r}")


def p_raw_vector_with_error_model(
    member_extrema,
    error_model: PredictiveErrorModel,
    city,
    settlement_semantics,
    bins,
    *,
    member_unit: str,
    n_mc: int | None = None,
    rng=None,
):
    """Residual-aware p_raw: subtract λ·bias (pre-MC) and widen the MC draw by the
    total residual SD. The °C error model is converted to the members' NATIVE unit
    (members/bins are native). Delegates to the shared ``p_raw_vector_from_maxes`` so
    training and inference share one MC path.
    """
    import numpy as np
    from src.signal.ensemble_signal import p_raw_vector_from_maxes

    scale = _c_to_native_scale(member_unit)
    eff_bias_native = error_model.effective_bias_c * scale
    resid_sd_native = error_model.total_residual_sd_c * scale
    corrected = np.asarray(member_extrema, dtype=float) - eff_bias_native
    return p_raw_vector_from_maxes(
        corrected, city, settlement_semantics, bins,
        n_mc=n_mc, rng=rng, extra_member_sigma=resid_sd_native,
    )


def fit_predictive_error_bucket(
    tigge_residuals: list[float],
    opendata_residuals: list[float],
    *,
    min_live_n: int = 20,
    residual_floor_c: float = 0.5,
    paired_delta_abs: float | None = None,
) -> PredictiveErrorModel:
    """Fit location (via #334 fit_bucket) AND scale (residual SD) for one bucket.

    Scale uses the LIVE forecast-error spread when enough live pairs exist, else the
    TIGGE-prior spread (shrinkage parallel to the bias). The scale intentionally uses
    the full sample SD (not a trimmed one) so genuine tail regimes keep predictive
    support; floored at ``residual_floor_c`` (>= sensor-ish level).
    """
    from src.calibration.ens_bias_model import fit_bucket

    post = fit_bucket(
        tigge_residuals, opendata_residuals,
        paired_delta_abs=paired_delta_abs, min_live_n=min_live_n,
    )

    def _spread(xs: list[float]) -> float:
        return statistics.stdev(xs) if len(xs) >= 2 else 0.0

    if len(opendata_residuals) >= min_live_n:
        residual_sd = _spread(opendata_residuals)
    else:
        residual_sd = _spread(tigge_residuals)
    residual_sd = max(residual_sd, residual_floor_c)
    return predictive_error_from_posterior(post, residual_sd)


def fit_city_predictive_error(
    conn,
    *,
    city: str,
    live_data_version: str,
    prior_data_version: str,
    season_months: tuple[int, ...] | None = None,
    metric: str = "high",
    lead_max: float = 48.0,
    min_live_n: int = 20,
    settled_before: str | None = None,
    kappa: float = 1.0,
    residual_floor_c: float = 0.5,
) -> PredictiveErrorModel:
    """Capstone DB-wired pipeline (location+scale+gate+transport) for one city/season.

    F50/TIGGE prior -> transported to the F25/OpenData lineage via Δ=F25-F50 ->
    updated by the OpenData live residual likelihood -> predictive-error model.
    All sub-steps are individually unit-tested; this only wires them.
    """
    import statistics
    from src.calibration.ens_bias_model import (
        LiveResidual, fit_bucket, posterior_bias, robust_mean, transport_bias_prior,
    )
    from src.calibration.ens_bias_repo import load_bucket_residuals, load_paired_delta

    common = dict(metric=metric, lead_max=lead_max, season_months=season_months,
                  settled_before=settled_before)
    tig = load_bucket_residuals(conn, city=city, data_version=prior_data_version,
                                require_verified=False,
                                contributor_policy="legacy_tigge_null_passthrough", **common)
    if not tig:
        raise ValueError(f"no TIGGE prior residuals for {city!r}")
    opd = load_bucket_residuals(conn, city=city, data_version=live_data_version,
                                contributor_policy="full_contributor_only", **common)
    delta = load_paired_delta(conn, city=city, live_data_version=live_data_version,
                              prior_data_version=prior_data_version, **common)
    # Fix B: gate transport on sufficient paired-Δ sample count.
    # n=1 makes statistics.variance undefined → var_d=0 → prior mean shifted by the
    # entire single-date delta with no variance inflation → SNR gate cannot suppress
    # it → spurious large corrections (e.g. Dallas -9.87C, Busan +5.03C).
    # When fewer than MIN_PAIRED_N samples exist, treat as no-delta (prior-only).
    delta_gated = delta if len(delta) >= MIN_PAIRED_N else []

    f50 = fit_bucket(tig, [], min_live_n=min_live_n)          # prior-only: b50, sd50
    transported = transport_bias_prior(b50=f50.bias, sd50=f50.sd, delta_samples=delta_gated, kappa=kappa)

    live = None
    if len(opd) >= min_live_n:
        var_o = statistics.variance(opd) if len(opd) >= 2 else 0.0
        live = LiveResidual(e_bar=robust_mean(opd), n=len(opd), sigma2=max(var_o, 1e-6))
    post = posterior_bias(transported, live)

    if len(opd) >= 2:
        residual_sd = statistics.stdev(opd)
    elif len(tig) >= 2:
        residual_sd = statistics.stdev(tig)
    else:
        residual_sd = residual_floor_c
    residual_sd = max(residual_sd, residual_floor_c)
    return predictive_error_from_posterior(post, residual_sd)
