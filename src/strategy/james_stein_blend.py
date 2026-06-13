# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: docs/authority/statistical_calibration_addendum_2026-06-13.md A10/C3
#   + measured artifact state/member_correlation_fit.json (rho_w=0.255, rho_b=0.140,
#   N_eff=3.71 over 178 AIFS events / 4163 deterministic events, 2026-06-13).
"""James–Stein shrinkage of a multinomial model-q toward the market-implied q.

Authority: statistical_calibration_addendum_2026-06-13 A10.

    q̂^JS_k = (1 − λ_JS) · q̂_k + λ_JS · q_mkt_k
    λ_JS    = clip( (K − 2) / (N_eff · χ²(q̂, q_mkt)), 0, 1 )
    χ²      = Σ_k (q̂_k − q_mkt_k)² / max(q_mkt_k, ε)

Properties:
* λ → 0   when the model strongly disagrees with the market (large χ²) — JS
          defers to the MODEL, not the market, in the high-disagreement regime.
          This is the defining admissibility property: verify sign carefully.
* λ → 1   when model ≈ market (small χ²) — blends fully toward market.
* λ scales with 1/N_eff — smaller effective sample size → LARGER λ (more shrinkage).
* Requires K ≥ 3 (inadmissibility guard; not a problem for K=12 bins).

The blend is SYMMETRIC (not a one-sided cap). The existing market-anchor cap is a
separate, untouched authority. This module has NO knowledge of the cap.

Artifact licensing (mirrors src/contracts/fee_authority.py pattern):
* Reads state/member_correlation_fit.json for N_eff.
* Artifact stale > MAX_ARTIFACT_AGE_DAYS or missing → degrades to N_eff = N_nominal
  (the raw member count passed by the caller) with a loud source label, never crashes.
* Result is mtime-cached so the hot decision path costs a stat() call only.

Pure module: no DB access, no engine imports, no logging side-effects. Shadow logging
lives at the (impure) caller in event_reactor_adapter.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Artifact licensing
# ---------------------------------------------------------------------------

ARTIFACT_PATH = Path(__file__).resolve().parents[2] / "state" / "member_correlation_fit.json"

# Evidence staleness: the correlation measurement requires a fresh run.
# Beyond this threshold we can no longer trust the stored N_eff.
MAX_ARTIFACT_AGE_DAYS: float = 30.0

# Minimum quality: artifact must have been fit on at least this many events.
MIN_EVENTS_TO_LICENSE: int = 50

_cache: dict[str, object] = {"mtime": None, "artifact": None}


def _load_artifact() -> dict | None:
    """Mtime-cached read of the member_correlation_fit artifact.

    Returns the parsed dict, or None if missing / unreadable.
    """
    try:
        mtime = os.stat(ARTIFACT_PATH).st_mtime
    except OSError:
        return None
    if _cache["mtime"] == mtime and _cache["artifact"] is not None:
        return _cache["artifact"]  # type: ignore[return-value]
    try:
        artifact = json.loads(ARTIFACT_PATH.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    _cache["mtime"] = mtime
    _cache["artifact"] = artifact
    return artifact


def load_member_correlation(
    path: str | Path | None = None,
) -> tuple[float, str]:
    """Return ``(N_eff, source)`` from the member-correlation artifact.

    Args:
        path: override the default artifact path (tests inject this).

    Returns:
        ``(N_eff, source)`` where source is a human-readable provenance tag.
        On degrade, N_eff is set to ``N_NOMINAL_FALLBACK`` (=51, raw member count)
        and source describes why.

    Degrade triggers (never crash):
        * Artifact missing or unreadable
        * Artifact age > MAX_ARTIFACT_AGE_DAYS
        * n_events_within_family < MIN_EVENTS_TO_LICENSE
        * N_eff field absent or non-positive
    """
    # N_eff nominal fallback = raw AIFS member count (what the old code implicitly assumed).
    N_NOMINAL_FALLBACK = 51.0

    # Allow test overrides
    global ARTIFACT_PATH
    if path is not None:
        target = Path(path)
    else:
        target = ARTIFACT_PATH

    try:
        mtime = os.stat(target).st_mtime
    except OSError:
        return N_NOMINAL_FALLBACK, "neff_degrade_artifact_missing"

    # Staleness check
    try:
        age_days = (
            __import__("time").time() - os.path.getmtime(target)
        ) / 86400.0
    except OSError:
        age_days = float("inf")
    if age_days > MAX_ARTIFACT_AGE_DAYS:
        return (
            N_NOMINAL_FALLBACK,
            f"neff_degrade_artifact_stale_age_days={age_days:.0f}",
        )

    # Read from cache or disk
    if _cache["mtime"] == mtime and _cache["artifact"] is not None:
        artifact = _cache["artifact"]
    else:
        try:
            artifact = json.loads(target.read_text())
            _cache["mtime"] = mtime
            _cache["artifact"] = artifact
        except (OSError, json.JSONDecodeError, ValueError):
            return N_NOMINAL_FALLBACK, "neff_degrade_artifact_unparseable"

    if not isinstance(artifact, dict):
        return N_NOMINAL_FALLBACK, "neff_degrade_artifact_not_dict"

    # License: enough events
    try:
        n_events = int(
            artifact.get("n_events_within_family")
            or artifact.get("n_aifs_events")
            or 0
        )
    except (TypeError, ValueError):
        n_events = 0
    if n_events < MIN_EVENTS_TO_LICENSE:
        return (
            N_NOMINAL_FALLBACK,
            f"neff_degrade_insufficient_events_n={n_events}",
        )

    # Extract N_eff
    try:
        n_eff_raw = artifact.get("n_eff") or artifact.get("N_eff")
        n_eff = float(n_eff_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return N_NOMINAL_FALLBACK, "neff_degrade_n_eff_missing_or_non_numeric"
    if n_eff <= 0.0:
        return N_NOMINAL_FALLBACK, "neff_degrade_n_eff_non_positive"

    fitted_at = str(artifact.get("fitted_at") or "")[:10]
    return n_eff, f"measured_n_eff={n_eff:.3f}_events={n_events}_fitted={fitted_at}"


# ---------------------------------------------------------------------------
# James–Stein blending
# ---------------------------------------------------------------------------

# Minimum denominator guard against division by zero in χ² accumulation
_EPS: float = 1e-9


def james_stein_toward_market(
    q_model: np.ndarray,
    q_market: np.ndarray,
    n_eff: float,
) -> tuple[np.ndarray, float, str]:
    """James–Stein blend of ``q_model`` toward ``q_market``.

    Args:
        q_model:  non-negative probability vector summing to ~1, shape (K,).
                  K must be >= 3 (inadmissibility guard).
        q_market: market-implied probabilities, same shape as q_model.
                  Values clipped to [ε, 1] before χ² denominator computation.
        n_eff:    effective sample size (N_eff from the correlation artifact).

    Returns:
        ``(q_js, lambda_js, source)`` where:
        * ``q_js``     — renormalized blended probability vector, shape (K,).
        * ``lambda_js``— shrinkage weight in [0, 1].
        * ``source``   — provenance tag string.

    Formula (addendum A10)::

        chi2     = Σ_k (q̂_k − q_mkt_k)² / max(q_mkt_k, ε)
        lambda   = clip( (K − 2) / (n_eff · chi2), 0, 1 )
        q̂^JS_k  = (1 − lambda) · q̂_k + lambda · q_mkt_k
        renormalize q̂^JS to sum to 1.

    SIGN PROPERTY (verify against formula): at large chi2 (model strongly
    disagrees), lambda → 0 → q_js ≈ q_model (JS defers to MODEL).
    At small chi2 (model ≈ market), lambda → 1 → q_js ≈ q_market.

    Guard: K < 3 raises ValueError (James–Stein inadmissible in K < 3).
    Guard: n_eff <= 0 raises ValueError.
    Guard: degenerate q_market (all-zero) → lambda=0, q_js=q_model, source notes it.
    """
    q_model = np.asarray(q_model, dtype=float).ravel()
    q_market = np.asarray(q_market, dtype=float).ravel()

    K = len(q_model)
    if K < 3:
        raise ValueError(
            f"James–Stein blend requires K >= 3 bins, got K={K}. "
            "The estimator is inadmissible for K < 3."
        )
    if q_model.shape != q_market.shape:
        raise ValueError(
            f"q_model shape {q_model.shape} != q_market shape {q_market.shape}"
        )
    if n_eff <= 0.0:
        raise ValueError(f"n_eff must be positive, got {n_eff}")

    # Clip q_market denominators to avoid divide-by-zero in χ²
    q_mkt_denom = np.maximum(q_market, _EPS)
    chi2 = float(np.sum((q_model - q_market) ** 2 / q_mkt_denom))

    if chi2 < _EPS:
        # Model ≈ market: lambda=1 is the formal limit; blend is idempotent.
        lambda_js = 1.0
        q_js = q_model.copy()
        # Still renormalize (handles minor float drift)
        total = float(np.sum(q_js))
        if total > _EPS:
            q_js = q_js / total
        return q_js, lambda_js, f"js_blend_chi2_near_zero_lambda=1.0"

    # Core formula
    lambda_raw = (K - 2) / (n_eff * chi2)
    lambda_js = float(np.clip(lambda_raw, 0.0, 1.0))

    q_js = (1.0 - lambda_js) * q_model + lambda_js * q_market
    # Clip to [0, 1] before renormalizing (float arithmetic guard)
    q_js = np.clip(q_js, 0.0, 1.0)
    total = float(np.sum(q_js))
    if total > _EPS:
        q_js = q_js / total
    else:
        # Both q_model and q_market were all-zero — return unnormalized model and
        # record no shrinkage. Caller is responsible for validating inputs; this
        # branch is only reachable with pathological all-zero vectors.
        q_js = q_model.copy()
        lambda_js = 0.0

    source = (
        f"js_blend_K={K}_n_eff={n_eff:.3f}_chi2={chi2:.6f}_lambda={lambda_js:.4f}"
    )
    return q_js, lambda_js, source
