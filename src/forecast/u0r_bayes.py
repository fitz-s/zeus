# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: U0R_BAYES_SPEC.md §1 observation model, §2 T2 fusion, §4 algorithm,
#   §6 F5 (u0r_bayes). PORTED VERBATIM from the proven proof engine
#   /Users/leofitz/zeus/.omc/research/polyweather_eval/scripts/run_u0r_bayes_fusion.py
#   (commit 658275e33b "U0R-Bayes forecast core: spec + settlement proof"). Verdict:
#   U0R_PROOF_RESULT.md (core PROMOTE ~15% Brier; regional shadow-grade lead-1-only).
"""U0R-Bayes settlement fusion — the production port of the proven C1 posterior.

THE ONE FUSION. This module is the single production home of the U0R-Bayes math.
It is a faithful port of the offline proof engine's per-cell fusion internals: the
SAME EB bias rule, the SAME Ledoit-Wolf shrink-to-diagonal covariance, and the SAME
T2 Bayesian posterior. It does NOT invent a second fusion. The hyperparameters are
copied byte-for-byte from the proof script so a known cell reproduces the proven
mu*/V*/q to 4 decimals (see tests/test_u0r_bayes_port_fidelity.py — Paris/high/L1
2025-12-26 -> mu*=4.3137, sd=0.7259).

WHAT THIS MODULE OWNS (per spec §4):
  - §1 observation model:  z_s = x_s - b_hat_s            (bias-corrected instruments).
  - EB bias (T):           b_hat = lam*rbar + (1-lam)*parent ,  lam = n/(n+kappa).
  - C0 diagonal Sigma ;    C1 Ledoit-Wolf shrink-to-diagonal Sigma (collapses to C0 at
                           small n; NEVER an unregularized learned S^-1).
  - T2 Bayesian fusion:    V* = (tau0^-2 + 1' Sigma^-1 1)^-1 ;
                           mu* = V*(tau0^-2 mu0 + 1' Sigma^-1 z).
                           anchor (ecmwf_ifs 0.1) = prior N(mu0, tau0^2); decorrelated
                           globals + in-domain regionals = likelihood instruments z.
  - T3 equal-weight (B):   the Sigma=sigma^2 I special case; production fallback when
                           Sigma is not reliably estimated.

PRODUCTION CONTRACT (the live caller in replacement_forecast_materializer):
  - fuse_u0r_posterior(...) takes ALREADY-bias-corrected instrument values (z), the
    anchor prior (mu0, tau0), and the residual matrix used to estimate Sigma. It
    returns FusedPosterior(mu, sd, ...). The materializer feeds mu -> anchor_value_c
    and sd -> anchor_sigma_c into the EXISTING soft-anchor construction; the downstream
    q_lcb settlement floor + EMOS + bin integration stay unchanged.
  - FAIL-SOFT by construction: T2 with p=0 likelihood terms returns the anchor prior
    (mu0, tau0) — the "all extras absent -> anchor fallback" path. A single dropped
    global simply does not appear in z; Sigma shrinks toward equal-weight.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


# ---- EB / shrink hyperparameters (PORTED VERBATIM; fixed a-priori, NOT tuned) -------
# These MUST match run_u0r_bayes_fusion.py exactly or the port-fidelity test fails.
KAPPA = 8.0          # EB shrink: lam = n/(n+kappa). kappa=8 -> ~50% trust at n=8.
MIN_TRAIN = 25       # need >=25 walk-forward rows before a model is trusted.
SIGMA_FLOOR = 0.8    # degC floor on per-source obs std (OM grid->station residual).
LOWN_INFLATE = 1.5   # sigma multiplier for thin (n<MIN_TRAIN) sources.
DISAGREE_W = 0.5     # weight on cross-source spread added into fusion sigma^2.
TAU0_FLOOR = 0.8     # floor on prior std tau0.

# Source identities (spec §3) — the production anchor + decorrelated globals + regionals.
ANCHOR_MODEL = "ecmwf_ifs"                       # universal 0.1 anchor = prior mean.
DECORR_GLOBALS = ("gfs_global", "icon_global", "gem_global", "jma_seamless")
ICON_EU_MODEL = "icon_eu"                        # DWD global/EU rep carried at all leads.
REGIONAL_MODELS = ("icon_d2", "meteofrance_arome_france_hd")


# ---- EB bias (walk-forward) ---------------------------------------------------------
def eb_bias(resids: Sequence[float], parent_bias: float) -> float:
    """b_hat = lam*rbar + (1-lam)*parent ,  lam = n/(n+kappa).

    resids = (x_s - Y) over train dates (walk-forward, strictly before the target date).
    Thin -> shrink to the structural parent prior; large-n -> trust the local mean.
    """
    n = len(resids)
    if n == 0:
        return float(parent_bias)
    rbar = sum(resids) / n
    lam = n / (n + KAPPA)
    return lam * rbar + (1.0 - lam) * parent_bias


# ---- covariance estimation ----------------------------------------------------------
def shrink_cov(resid_mat: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrinkage of the sample covariance toward its DIAGONAL (C1).

    Off-diagonal kept only to the extent it is reliably estimated; as n->small the
    shrink intensity -> 1 and C1 collapses to C0 (diagonal). PARSIMONY: covariance-aware
    only where Sigma is reliable. resid_mat: (n_obs, p). NEVER an unregularized S^-1.
    """
    n, p = resid_mat.shape
    if n < 3 or p == 1:
        v = resid_mat.var(axis=0, ddof=1) if n > 1 else np.ones(p)
        return np.diag(np.maximum(v, SIGMA_FLOOR ** 2))
    S = np.cov(resid_mat, rowvar=False, ddof=1)
    if S.ndim == 0:
        S = np.array([[float(S)]])
    target = np.diag(np.diag(S))                      # shrink toward diagonal
    # Ledoit-Wolf optimal intensity delta* = E||S-Sigma||^2 estimate / ||S-target||^2
    Xc = resid_mat - resid_mat.mean(axis=0)
    phi = 0.0
    for t in range(n):
        xt = Xc[t][:, None]
        d = xt @ xt.T - S
        phi += np.sum(d * d)
    phi /= n * n
    gamma = np.sum((S - target) ** 2)
    delta = 0.0 if gamma <= 1e-12 else max(0.0, min(1.0, phi / gamma))
    Sig = delta * target + (1.0 - delta) * S
    # PD repair + floor on the diagonal
    d = np.diag(Sig).copy()
    d = np.maximum(d, SIGMA_FLOOR ** 2)
    np.fill_diagonal(Sig, d)
    w, V = np.linalg.eigh(Sig)
    w = np.maximum(w, SIGMA_FLOOR ** 2 * 0.25)
    return (V * w) @ V.T


def diag_cov(resid_mat: np.ndarray, lown: Sequence[bool]) -> np.ndarray:
    """C0 diagonal Sigma = diag(sigma^2_s) with floor + low-n inflation."""
    n, p = resid_mat.shape
    v = resid_mat.var(axis=0, ddof=1) if n > 1 else np.full(p, SIGMA_FLOOR ** 2)
    sd = np.sqrt(np.maximum(v, SIGMA_FLOOR ** 2))
    sd = sd.astype(float)
    for j in range(p):
        if lown[j]:
            sd[j] *= LOWN_INFLATE
    return np.diag(sd ** 2)


# ---- Bayesian fusion (T2) -----------------------------------------------------------
def bayes_fuse(
    z: np.ndarray,
    Sigma: np.ndarray,
    mu0: float,
    tau0: float,
    extra_var: float,
) -> tuple[float, float]:
    """T2: V* = (tau0^-2 + 1' Sigma^-1 1)^-1 ;  mu* = V*(tau0^-2 mu0 + 1' Sigma^-1 z).

    z = likelihood instruments (NON-anchor, bias-corrected); mu0/tau0 = anchor prior.
    extra_var = sigma^2_disagree + sigma^2_bias/bridge buffers added post-hoc (widen-only).

    FAIL-SOFT: p == 0 (no likelihood instruments survived) -> the posterior IS the anchor
    prior N(mu0, tau0^2 + extra_var). This is the "all extras absent -> anchor fallback".
    """
    z = np.asarray(z, dtype=float)
    p = len(z)
    if p == 0:
        return float(mu0), float(math.sqrt(tau0 ** 2 + extra_var))
    try:
        Sinv = np.linalg.inv(Sigma)
    except np.linalg.LinAlgError:
        Sinv = np.linalg.pinv(Sigma)
    ones = np.ones(p)
    prec = (1.0 / tau0 ** 2) + ones @ Sinv @ ones
    Vstar = 1.0 / prec
    mustar = Vstar * ((mu0 / tau0 ** 2) + ones @ Sinv @ z)
    var = Vstar + extra_var
    return float(mustar), float(math.sqrt(max(var, 1e-4)))


def equal_weight(
    z: np.ndarray,
    sds: np.ndarray,
    extra_var: float,
    mu0: float | None,
    tau0: float | None,
) -> tuple[float, float]:
    """T3 equal-weight (Sigma=sigma^2 I special case). Mean of corrected reps; sigma from
    mean obs variance / K + disagreement. If a prior is supplied, blend it as one more
    equal member (shrink-to-equal posture). Production fallback when Sigma not reliable.
    """
    z = np.asarray(z, dtype=float)
    sds = np.asarray(sds, dtype=float)
    members = list(z)
    var_members = list(sds ** 2)
    if mu0 is not None:
        members.append(mu0)
        var_members.append((tau0 if tau0 is not None else TAU0_FLOOR) ** 2)
    k = len(members)
    if k == 0:
        return (float(mu0) if mu0 is not None else 0.0), float(math.sqrt(extra_var + 1.0))
    mu = sum(members) / k
    var = (sum(var_members) / (k * k)) + extra_var
    return float(mu), float(math.sqrt(max(var, 1e-4)))


# ---- production-facing fused posterior ----------------------------------------------
@dataclass(frozen=True)
class FusedPosterior:
    """The U0R fused center + spread that REPLACE the single-anchor anchor_value_c /
    anchor_sigma_c in the existing soft-anchor construction (flag-ON only).

    ``mu`` -> soft-anchor anchor_value_c ; ``sd`` -> soft-anchor anchor_sigma_c. The
    downstream q_lcb settlement floor, EMOS, and bin integration stay unchanged.

    ``method`` records which branch produced it: "T2_BAYES" (anchor prior + >=1 likelihood
    instrument), "ANCHOR_FALLBACK" (all extras absent -> posterior is the anchor prior),
    or "EQUAL_WEIGHT" (no reliable prior, shrink-to-equal of the corrected reps).
    ``used_models`` is the ordered fusion set for provenance + the EMOS model_set_hash.
    """

    mu: float
    sd: float
    method: str
    used_models: tuple[str, ...]
    anchor_model: str | None
    likelihood_models: tuple[str, ...] = field(default_factory=tuple)
    regional_models: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not math.isfinite(self.mu):
            raise ValueError("fused mu must be finite")
        if not (math.isfinite(self.sd) and self.sd > 0.0):
            raise ValueError("fused sd must be positive finite")
        if self.method not in {"T2_BAYES", "ANCHOR_FALLBACK", "EQUAL_WEIGHT"}:
            raise ValueError(f"unknown fusion method {self.method!r}")


@dataclass(frozen=True)
class ModelInstrument:
    """One bias-corrected source instrument feeding the fusion.

    ``model``: model identity (e.g. gfs_global, icon_d2). ``z``: today's bias-corrected
    value x_s - b_hat_s (degC). ``train_residuals``: walk-forward (x_s - b_hat_s - Y)
    over the COMMON estimation window (used to estimate Sigma). ``is_regional``: True for
    in-domain regional experts (icon_d2/arome) — kept for provenance only; the gate that
    decided eligibility lives in model_selection.py (out-of-domain regionals never reach
    here). ``n_train``: count of walk-forward residuals (drives low-n inflation).
    """

    model: str
    z: float
    train_residuals: tuple[float, ...]
    n_train: int
    is_regional: bool = False


def fuse_u0r_posterior(
    *,
    anchor_z: float | None,
    anchor_tau0: float | None,
    likelihood: Sequence[ModelInstrument],
    disagree_var: float = 0.0,
    use_covariance: bool = True,
) -> FusedPosterior:
    """Run the proven C1 fusion and return the center+spread for the soft-anchor.

    THE ONE FUSION. Mirrors run_u0r_bayes_fusion.run_city_metric_lead's C1/D1 branch:

      1. anchor (ecmwf_ifs 0.1) is the prior N(anchor_z, anchor_tau0^2). It is ALREADY
         bias-corrected by the caller (z_today[ANCHOR]); anchor_tau0 is the walk-forward
         std of the anchor residuals (>= TAU0_FLOOR).
      2. likelihood instruments are the decorrelated globals (+ icon_eu) and any in-domain
         regional experts, each ALREADY bias-corrected (z = x_s - b_hat_s).
      3. Sigma is estimated from the common-window residual matrix via Ledoit-Wolf
         shrink-to-diagonal (C1) when >=5 common rows exist, else diagonal (C0) — the exact
         PARSIMONY rule from the proof. ``use_covariance=False`` forces C0 (the C0 ablation).
      4. T2 posterior: mu* / V* via bayes_fuse, with extra_var = disagree_var (widen-only).

    FAIL-SOFT:
      - anchor_z is None (no trusted >=MIN_TRAIN anchor) AND likelihood present
        -> EQUAL_WEIGHT of the corrected reps (no reliable prior; shrink-to-equal).
      - likelihood empty AND anchor present -> ANCHOR_FALLBACK (posterior == anchor prior).
      - both empty -> ValueError (the caller must not invoke fusion with zero sources; the
        flag-OFF byte-identical path handles the no-extras production case upstream).
    """
    instruments = list(likelihood)
    have_anchor = anchor_z is not None and anchor_tau0 is not None

    if not have_anchor and not instruments:
        raise ValueError("U0R fusion requires at least an anchor prior or one instrument")

    # ---- no reliable prior: equal-weight the corrected reps (shrink-to-equal) ----
    if not have_anchor:
        z = np.array([ins.z for ins in instruments], dtype=float)
        sds = []
        for ins in instruments:
            vh = ins.train_residuals
            s = float(np.std(vh, ddof=1)) if len(vh) > 1 else SIGMA_FLOOR
            if ins.n_train < MIN_TRAIN:
                s *= LOWN_INFLATE
            sds.append(max(s, SIGMA_FLOOR))
        mu, sd = equal_weight(z, np.array(sds), disagree_var, None, None)
        return FusedPosterior(
            mu=mu, sd=sd, method="EQUAL_WEIGHT",
            used_models=tuple(ins.model for ins in instruments),
            anchor_model=None,
            likelihood_models=tuple(ins.model for ins in instruments),
            regional_models=tuple(ins.model for ins in instruments if ins.is_regional),
        )

    mu0 = float(anchor_z)
    tau0 = max(TAU0_FLOOR, float(anchor_tau0))

    # ---- all extras absent: posterior is the anchor prior (fail-soft fallback) ----
    if not instruments:
        mu, sd = bayes_fuse(np.array([]), np.zeros((0, 0)), mu0, tau0, disagree_var)
        return FusedPosterior(
            mu=mu, sd=sd, method="ANCHOR_FALLBACK",
            used_models=(ANCHOR_MODEL,), anchor_model=ANCHOR_MODEL,
        )

    # ---- T2 Bayesian fusion (C1 covariance-shrink, or C0 diagonal) ----
    z_lik = np.array([ins.z for ins in instruments], dtype=float)
    lown = [ins.n_train < MIN_TRAIN for ins in instruments]
    # Common estimation window: rows where EVERY instrument has a residual. The proof builds
    # the residual matrix over dates where all selected models are present; the caller passes
    # per-instrument aligned residual vectors of equal length (the common window).
    resid_lengths = {len(ins.train_residuals) for ins in instruments}
    common_n = min(resid_lengths) if resid_lengths else 0
    if common_n >= 3 and len(resid_lengths) == 1:
        M = np.array([list(ins.train_residuals) for ins in instruments], dtype=float).T
    else:
        M = None

    if M is not None and use_covariance and M.shape[0] >= 5:
        Sigma = shrink_cov(M)
    elif M is not None:
        Sigma = diag_cov(M, lown)
    else:
        Sigma = np.diag(np.full(len(instruments), (SIGMA_FLOOR * LOWN_INFLATE) ** 2))

    mu, sd = bayes_fuse(z_lik, Sigma, mu0, tau0, disagree_var)
    return FusedPosterior(
        mu=mu, sd=sd, method="T2_BAYES",
        used_models=(ANCHOR_MODEL,) + tuple(ins.model for ins in instruments),
        anchor_model=ANCHOR_MODEL,
        likelihood_models=tuple(ins.model for ins in instruments),
        regional_models=tuple(ins.model for ins in instruments if ins.is_regional),
    )
