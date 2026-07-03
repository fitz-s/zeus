# Created: 2026-07-01
# Last audited: 2026-07-01
# Authority basis: EMOS-on-runtime-output center-bias correction. Design + universality validation
#   in docs/evidence/emos_upgrade/ (2026-07-01); frontier math consult REQ-20260701-010328-c6d43a
#   (H=30 monthly half-life; per-unit EB shrink λ=b̂²/(b̂²+v̂) with HAC v̂; additive offset — NOT a
#   weight re-fit; serve only where a city's walk-forward OOS ΔMSE has an individual 95% lower CI ≥ 0
#   → per-unit no-material-harm; universality restated as generalizable adaptive no-harm, NOT a fixed
#   law — a fixed offset table FAILS LOCO R²=-0.45 by seasonal drift).
"""Pure, walk-forward, leak-free per-city CENTER-bias correction for the served runtime forecast.

The live serving center (``forecast_posteriors.provenance_json.anchor_value_c``) is a FROZEN
fixed-weight source-clock combination of raw model values with NO de-bias (RAW law), so a minority
of single-source-dominated ("weak-fusion") cities carry a stable residual center bias vs settlement.
This module estimates a small, shrunk, recency-weighted additive offset ``μ' = μ + λ·b̂`` from the
city's own settled residual history and exposes a fail-soft artifact lookup for the materializer.

Sign convention: residual r = settlement − served_center; a POSITIVE mean residual means the center
runs COLD, so the offset ADDS ``+λ·b̂``. σ is NOT touched (center-only correction).
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional, Sequence

# ---- estimator hyperparameters (consult-fixed; the fitter passes them explicitly) -------------
DEFAULT_HALF_LIFE = 30.0   # EWMA half-life in settled-days (monthly): slow enough to be a
                           # structural-bias correction, fast enough to shed obsolete seasonal
                           # bias. Vindicated by the old-lag embargo test (H=7's edge vanishes
                           # under embargo → it was regime-tracking; H=30 keeps its gain).
DEFAULT_MIN_TRAIN = 20     # warmup: no correction until this many prior settled residuals.
MIN_N_EFF = 8.0            # cold-start hard-gate: below this EWMA effective-n, serve raw (λ→0).

_ARTIFACT_FILENAME = "emos_center_offset.json"
ARTIFACT_AUTHORITY = "emos_center_offset_v1"


def eb_lambda(*, bias: float, var: float) -> float:
    """Per-unit empirical-Bayes (SURE positive-part) shrinkage λ = b²/(b²+v̂) ∈ [0,1].

    v̂ is the sampling variance of the bias estimate (HAC — see ``ewma_bias_var``). A near-zero
    or noise-dominated bias -> λ≈0 (no correction); a strong bias with small v̂ -> λ≈1."""
    b2 = float(bias) * float(bias)
    v = max(0.0, float(var))
    denom = b2 + v
    if denom <= 0.0:
        return 0.0
    return max(0.0, min(1.0, b2 / denom))


def ewma_bias_var(resids: Sequence[float], half_life: Optional[float]) -> tuple[float, float, float]:
    """Return (bias, hac_var, n_eff) for a recency-weighted mean. Newest residual LAST;
    half_life=None -> equal weights (expanding mean).

    v̂ is the HAC (Newey-West, Bartlett-tapered) long-run variance of the recency-weighted mean,
    NOT the iid σ²/n_eff. Consult [MEDIUM]: under serial dependence the iid form underestimates v̂,
    inflates λ, and over-rewards short half-lives; the autocovariance terms keep λ honest."""
    n = len(resids)
    if n == 0:
        return 0.0, float("inf"), 0.0
    if half_life is None:
        w = [1.0] * n
    else:
        r = 0.5 ** (1.0 / float(half_life))
        w = [r ** (n - 1 - i) for i in range(n)]  # newest -> weight 1
    sw = math.fsum(w)
    b = math.fsum(wi * x for wi, x in zip(w, resids)) / sw
    n_eff = sw * sw / math.fsum(wi * wi for wi in w)
    if n < 2:
        return b, float("inf"), n_eff
    e = [x - b for x in resids]
    wn = [wi / sw for wi in w]
    L = min(int(4.0 * (n / 100.0) ** (2.0 / 9.0)) + 1, n - 1)  # Newey-West lag
    v = math.fsum(wn[k] * wn[k] * e[k] * e[k] for k in range(n))
    for h in range(1, L + 1):
        taper = 1.0 - h / (L + 1.0)  # Bartlett taper -> PSD
        v += 2.0 * taper * math.fsum(wn[k] * wn[k - h] * e[k] * e[k - h] for k in range(h, n))
    return b, max(v, 1e-9), n_eff


def current_offset(
    dated_residuals: Sequence[tuple[str, float]],
    *,
    half_life: Optional[float] = DEFAULT_HALF_LIFE,
    min_train: int = DEFAULT_MIN_TRAIN,
) -> Optional[float]:
    """The center offset to serve NOW from all settled residuals. None if < min_train or a
    cold-start (n_eff < MIN_N_EFF) — the caller then serves the raw center (byte-identical)."""
    resids = [float(r) for _, r in sorted(dated_residuals)]
    if len(resids) < min_train:
        return None
    b, v, n_eff = ewma_bias_var(resids, half_life)
    if n_eff < MIN_N_EFF:
        return None
    return eb_lambda(bias=b, var=v) * b


def walk_forward_offset_series(
    dated_residuals: Sequence[tuple[str, float]],
    *,
    half_life: Optional[float] = DEFAULT_HALF_LIFE,
    min_train: int = DEFAULT_MIN_TRAIN,
    embargo_g: int = 0,
) -> list[tuple[str, float]]:
    """Leak-free per-day offset: day t uses only residuals strictly before t (optionally
    embargoing the most-recent ``embargo_g`` before t — the structural-vs-regime test).
    Warmup / cold-start -> 0.0 (no correction)."""
    ordered = sorted(dated_residuals)
    resids = [float(r) for _, r in ordered]
    out: list[tuple[str, float]] = []
    for t, (d, _) in enumerate(ordered):
        hist = resids[: max(0, t - embargo_g)]
        if len(hist) < min_train:
            out.append((d, 0.0))
            continue
        b, v, n_eff = ewma_bias_var(hist, half_life)
        out.append((d, 0.0 if n_eff < MIN_N_EFF else eb_lambda(bias=b, var=v) * b))
    return out


def lookup_center_offset(city: str, metric: str) -> float:
    """Fail-soft materializer seam: the fitted additive center offset (°C) to ADD to the served
    runtime center for (city, metric), or 0.0 when absent/not-served/malformed.

    Mirrors ``_replacement_sigma_scale_lookup``: reads ``state/emos_center_offset.json`` (written
    ONLY by scripts/fit_emos_center_offset.py) via the RUNTIME state dir, and returns the offset
    ONLY for a city whose entry is ``serve=true`` (its walk-forward OOS ΔMSE cleared the per-unit
    no-material-harm gate at fit time). Any error / missing file / not-served -> 0.0 (byte-identical
    to the pre-EMOS served center). NEVER raises."""
    try:
        from src.config import runtime_state_path  # noqa: PLC0415

        path = str(runtime_state_path(_ARTIFACT_FILENAME))
        if not os.path.exists(path):
            return 0.0
        with open(path, "r", encoding="utf-8") as fh:
            artifact = json.load(fh)
        entry = (((artifact.get("metrics") or {}).get(str(metric)) or {}).get("cities") or {}).get(str(city))
        if not isinstance(entry, dict) or not entry.get("serve"):
            return 0.0
        off = float(entry.get("offset_c", 0.0))
        return off if math.isfinite(off) else 0.0
    except Exception:
        return 0.0
