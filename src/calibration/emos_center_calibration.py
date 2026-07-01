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

Shrunk toward identity by n/(n+κ) so a world-class city stays at (0,1) (byte-identical center) and a
biased city moves only as far as its own settled evidence supports. Walk-forward / leak-free. The
fail-soft lookup returns (0.0, 1.0) — identity — for any city/metric absent or not gated to serve.
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional, Sequence

DEFAULT_KAPPA = 40.0     # shrink-to-identity strength: a city needs ~κ settled obs for half-weight
DEFAULT_MIN_TRAIN = 25   # warmup: identity until this many prior settled pairs
# Slope clamp: a mild temperature dependence is physical (models cold-when-hot ~±10-15%); a slope
# far from 1 is over-fitting a narrow temperature range (esp. tropical cities) and is dangerous to
# extrapolate. Clamp b to a TINY band and preserve the mean-center correction (re-derive a).
SLOPE_MIN = 0.85
SLOPE_MAX = 1.15
_ARTIFACT_FILENAME = "emos_center_calibration.json"
ARTIFACT_AUTHORITY = "emos_center_calibration_v1"


def fit_affine(pairs: Sequence[tuple[float, float]], *, kappa: float = DEFAULT_KAPPA) -> tuple[float, float]:
    """Shrunk OLS of settle on center: returns (a, b) for μ' = a + b·center.

    ``pairs`` = [(center, settle), ...]. OLS (a_ols, b_ols) is shrunk toward identity (0, 1) by
    w = n/(n+κ): a = w·a_ols, b = 1 + w·(b_ols − 1). Degenerate / thin inputs -> identity (0, 1)."""
    n = len(pairs)
    if n < 8:
        return 0.0, 1.0
    xs = [float(c) for c, _ in pairs]
    ys = [float(s) for _, s in pairs]
    mx = math.fsum(xs) / n
    my = math.fsum(ys) / n
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    if sxx <= 0.0:
        return 0.0, 1.0
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b_ols = sxy / sxx
    a_ols = my - b_ols * mx
    w = n / (n + float(kappa))
    a = w * a_ols
    b = 1.0 + w * (b_ols - 1.0)
    # Clamp the slope to a mild physical band; preserve the mean-center correction by re-deriving a
    # so a + (b−1)·mean(center) is unchanged (the clamp bounds only the temperature TILT, not the
    # average shift). Guards narrow-range over-fit + unsafe extrapolation.
    if b < SLOPE_MIN or b > SLOPE_MAX:
        b_c = min(SLOPE_MAX, max(SLOPE_MIN, b))
        a = a + (b - b_c) * mx
        b = b_c
    if not (math.isfinite(a) and math.isfinite(b)):
        return 0.0, 1.0
    return a, b


def apply_affine(center: float, a: float, b: float) -> float:
    """The corrected center μ' = a + b·μ. Identity (0,1) returns μ unchanged (byte-identical)."""
    return float(a) + float(b) * float(center)


def walk_forward_affine(
    dated: Sequence[tuple[str, float, float]],
    *,
    min_train: int = DEFAULT_MIN_TRAIN,
    kappa: float = DEFAULT_KAPPA,
) -> list[tuple[str, float, float]]:
    """Leak-free per-day (a, b): day t is fit on (center, settle) strictly before t (expanding
    window). ``dated`` = [(date, center, settle), ...]. Warmup -> identity (0, 1)."""
    ordered = sorted(dated)
    out: list[tuple[str, float, float]] = []
    hist: list[tuple[float, float]] = []
    for d, c, s in ordered:
        if len(hist) < min_train:
            out.append((d, 0.0, 1.0))
        else:
            a, b = fit_affine(hist, kappa=kappa)
            out.append((d, a, b))
        hist.append((float(c), float(s)))
    return out


def current_affine(
    dated: Sequence[tuple[str, float, float]],
    *,
    min_train: int = DEFAULT_MIN_TRAIN,
    kappa: float = DEFAULT_KAPPA,
) -> Optional[tuple[float, float]]:
    """The (a, b) to serve NOW from all settled pairs. None if < min_train (caller serves identity)."""
    pairs = [(c, s) for _, c, s in sorted(dated)]
    if len(pairs) < min_train:
        return None
    return fit_affine(pairs, kappa=kappa)


def lookup_affine(city: str, metric: str) -> tuple[float, float]:
    """Fail-soft materializer seam: the fitted (a, b) for (city, metric), or IDENTITY (0.0, 1.0)
    when absent / not-served / malformed. μ' = a + b·μ; identity => byte-identical center. Reads
    ``state/emos_center_calibration.json`` (SOLE writer: scripts/fit_emos_center_calibration.py) via
    the RUNTIME state dir, exactly like the sigma-scale artifact. NEVER raises."""
    try:
        from src.config import runtime_state_path  # noqa: PLC0415

        path = str(runtime_state_path(_ARTIFACT_FILENAME))
        if not os.path.exists(path):
            return 0.0, 1.0
        with open(path, "r", encoding="utf-8") as fh:
            artifact = json.load(fh)
        entry = (((artifact.get("metrics") or {}).get(str(metric)) or {}).get("cities") or {}).get(str(city))
        if not isinstance(entry, dict) or not entry.get("serve"):
            return 0.0, 1.0
        a = float(entry.get("a", 0.0))
        b = float(entry.get("b", 1.0))
        if not (math.isfinite(a) and math.isfinite(b)):
            return 0.0, 1.0
        return a, b
    except Exception:
        return 0.0, 1.0
