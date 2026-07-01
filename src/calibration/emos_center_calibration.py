# Created: 2026-07-01
# Last audited: 2026-07-01
# Authority basis: EMOS/NGR affine center calibration on the RUNTIME combined center. Operator
#   "使用真实参与概率计算的运行态组合数据进行精准的emos设计提升". Frontier consult REQ-20260701-010328.
#   A constant per-city offset is the WRONG instrument (b=1) — the weak-fusion residual is a
#   TEMPERATURE-DEPENDENT representativeness bias (all models run cold, and colder when hot: the
#   airport station outruns every model's grid cell in heat). The precise correction is the EMOS
#   SLOPE: μ' = a + b·μ, fit on the real runtime center, shrunk toward identity (a=0,b=1) so it is
#   ~tiny in mild conditions and precisely larger only where/when the models genuinely lag. σ untouched.
"""Per-city affine EMOS center calibration: μ' = a + b·μ_runtime.

Shrunk toward identity (a=0, b=1) by an EMPIRICAL-BAYES weight w = τ²/(τ²+se²) that is DERIVED FROM
THE RUNTIME DATA — no hand-set shrink strength, no slope clamp. se² is each city's own sampling
variance (large when its temperature range is narrow or its data thin/noisy); τ² is the cross-city
spread of true effects around the physical null, estimated by method of moments. A world-class city
stays at (0,1) (byte-identical center); a biased city with a repeatable, well-estimated tilt keeps it;
an uncertain one is pulled hard toward identity on its own evidence. The fail-soft lookup returns
(0.0, 1.0) — identity — for any city/metric absent or not gated to serve.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from typing import Mapping, Optional, Sequence

# Minimum points for a non-degenerate per-city OLS line. STRUCTURAL floor (a line needs enough
# points to estimate a slope + its sampling variance), NOT a tuning constant — the shrinkage
# strength itself is data-derived (EB), so there is no κ / clamp band to hand-set.
MIN_CITY_POINTS = 8
_ARTIFACT_FILENAME = "emos_center_calibration.json"
ARTIFACT_AUTHORITY = "emos_center_calibration_v1"


def _ols_stats(
    pairs: Sequence[tuple[float, float]],
) -> Optional[tuple[float, float, float, float, float]]:
    """Per-city OLS of settle on center. Returns (b_ols, se_b2, mean_bias, se_mb2, xbar) or None
    when degenerate. se_b2 = residual_var / Sxx is the SAMPLING variance of the slope; se_mb2 =
    var(settle−center)/n that of the mean bias. These honest standard errors are what the EB
    shrinkage weights on — computed on the INDEPENDENT unit the caller supplies (one point per
    date), so they are NOT deflated by within-date row correlation (the flaw that let a hard clamp
    look necessary)."""
    n = len(pairs)
    if n < MIN_CITY_POINTS:
        return None
    xs = [float(c) for c, _ in pairs]
    ys = [float(s) for _, s in pairs]
    mx = math.fsum(xs) / n
    my = math.fsum(ys) / n
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    if sxx <= 0.0:
        return None
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    sse = math.fsum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    s2 = sse / (n - 2) if n > 2 else 0.0
    se_b2 = s2 / sxx
    diffs = [y - x for x, y in zip(xs, ys)]
    mb = math.fsum(diffs) / n
    var_mb = math.fsum((d - mb) ** 2 for d in diffs) / (n - 1) if n > 1 else 0.0
    se_mb2 = var_mb / n
    if not all(math.isfinite(v) for v in (b, se_b2, mb, se_mb2, mx)):
        return None
    return b, se_b2, mb, se_mb2, mx


def fit_affine_eb(
    city_pairs: Mapping[str, Sequence[tuple[float, float]]],
) -> dict[str, tuple[float, float]]:
    """Empirical-Bayes per-city affine (a, b) for μ' = a + b·center, shrunk toward IDENTITY with a
    DATA-DERIVED weight — no hand-set κ, no slope clamp.

    Per city: OLS slope b_i and mean-bias mb_i with their own sampling variances se_b_i², se_mb_i².
    The prior spread of TRUE effects around the physical null is estimated ACROSS cities by method of
    moments — τ_b² = max(0, mean_i(b_i−1)² − mean_i se_b_i²), τ_mb² likewise around 0. Each city is
    shrunk by w = τ²/(τ²+se²): a city whose slope is uncertain (narrow temperature range → large
    se_b², or thin/noisy data) is pulled hard toward b=1 on its OWN evidence; a city with a sharp,
    repeatable tilt keeps it. The intercept is re-derived from the shrunk mean-bias and slope so the
    correction at the city's mean temperature equals the shrunk level. Every quantity is a function
    of the runtime data; nothing is a tuning constant. Degenerate/absent cities return identity."""
    stats = {c: _ols_stats(p) for c, p in city_pairs.items()}
    stats = {c: s for c, s in stats.items() if s is not None}
    out: dict[str, tuple[float, float]] = {c: (0.0, 1.0) for c in city_pairs}
    if len(stats) < 3:                       # need a pool to estimate the prior spread τ²
        return out
    tau_b2 = max(0.0, statistics.mean((s[0] - 1.0) ** 2 for s in stats.values())
                 - statistics.mean(s[1] for s in stats.values()))
    tau_mb2 = max(0.0, statistics.mean(s[2] ** 2 for s in stats.values())
                  - statistics.mean(s[3] for s in stats.values()))
    for c, (b, se_b2, mb, se_mb2, xbar) in stats.items():
        w_b = tau_b2 / (tau_b2 + se_b2) if (tau_b2 + se_b2) > 0.0 else 0.0
        w_mb = tau_mb2 / (tau_mb2 + se_mb2) if (tau_mb2 + se_mb2) > 0.0 else 0.0
        b_s = 1.0 + (b - 1.0) * w_b
        a_s = mb * w_mb - (b_s - 1.0) * xbar
        if math.isfinite(a_s) and math.isfinite(b_s):
            out[c] = (a_s, b_s)
    return out


def apply_affine(center: float, a: float, b: float) -> float:
    """The corrected center μ' = a + b·μ. Identity (0,1) returns μ unchanged (byte-identical)."""
    return float(a) + float(b) * float(center)


def apply_affine_in_support(
    center: float, a: float, b: float, x_lo: float | None, x_hi: float | None
) -> float:
    """μ' = a + b·μ, but the temperature TILT is applied only WITHIN the training support: the input
    is clamped to [x_lo, x_hi] before the affine, so the correction δ(μ)=a+(b−1)μ is held FLAT at its
    endpoint value outside the observed range. Guards a strong slope (b far from 1) against unearned
    extrapolation to temperatures the fit never saw — a DATA-DERIVED bound (the observed range), not a
    hand-set clamp. x_lo/x_hi None (or identity a=0,b=1) => plain μ (byte-identical)."""
    c = float(center)
    if x_lo is not None and x_hi is not None and x_lo <= x_hi:
        c = min(float(x_hi), max(float(x_lo), c))
    return float(a) + float(b) * c


def lookup_affine(
    city: str, metric: str, lead: int | None = None
) -> tuple[float, float, float | None, float | None]:
    """Fail-soft materializer seam. Returns (a, b, x_lo, x_hi) for (city, metric) — or IDENTITY
    (0.0, 1.0, None, None) when the layer is disabled, the city is absent / not-served / malformed,
    OR the requested ``lead`` is not the served decision lead. The affine was fit on ONE served
    center per date at a single day-ahead lead; applying it at another lead would extrapolate across
    the (lead-dependent) bias regime, so serving is gated to that lead. ``lead=None`` skips the lead
    gate (diagnostic use only — the live materializer always passes the real lead). Reads
    ``state/emos_center_calibration.json`` (SOLE writer: scripts/fit_emos_center_calibration.py).
    NEVER raises. (x_lo, x_hi) feed ``apply_affine_in_support`` for the range guard."""
    ident = (0.0, 1.0, None, None)
    try:
        from src.config import runtime_state_path  # noqa: PLC0415

        path = str(runtime_state_path(_ARTIFACT_FILENAME))
        if not os.path.exists(path):
            return ident
        with open(path, "r", encoding="utf-8") as fh:
            artifact = json.load(fh)
        if artifact.get("enabled") is False:  # kill switch: disable the whole layer without deleting it
            return ident
        metric_block = (artifact.get("metrics") or {}).get(str(metric)) or {}
        entry = (metric_block.get("cities") or {}).get(str(city))
        if not isinstance(entry, dict) or not entry.get("serve"):
            return ident
        # Lead gate: serve ONLY at the decision lead the coefficients were fit on.
        if lead is not None:
            served_lead = metric_block.get("served_lead", artifact.get("served_lead"))
            if served_lead is not None and int(lead) != int(served_lead):
                return ident
        a = float(entry.get("a", 0.0))
        b = float(entry.get("b", 1.0))
        if not (math.isfinite(a) and math.isfinite(b)):
            return ident
        x_lo = entry.get("x_lo")
        x_hi = entry.get("x_hi")
        x_lo = float(x_lo) if isinstance(x_lo, (int, float)) and math.isfinite(float(x_lo)) else None
        x_hi = float(x_hi) if isinstance(x_hi, (int, float)) and math.isfinite(float(x_hi)) else None
        return a, b, x_lo, x_hi
    except Exception:
        return ident
