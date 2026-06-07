# Created: 2026-06-04
# Lifecycle: created=2026-06-04; last_reviewed=2026-06-04; last_reused=2026-06-04
# Purpose: The ONE q seam (#110 / ELEVATION S2). build_emos_q produces the traded bin-probability
#   distribution from the single EMOS calibrator: q[bin] = N(mu, sigma) integrated over the
#   settlement preimage, with the SAME (mu, sigma) feeding the point q AND the lcb sigma. This
#   replaces the bias/grid/identity-Platt maze. served=raw/missing -> None (the caller uses the
#   honest raw analytic p_raw, NEVER the bias maze).
# Reuse: update with src/calibration/emos.py (emos_predictive, bin_probability_settlement) or the
#   q seam in src/engine/event_reactor_adapter.py:_market_analysis_from_event_snapshot.
# Authority basis: plan compiled-foraging-quail.md; the universal-correlation decision
#   (operator 2026-06-04) — one ensemble->settlement calibrator owns the whole mapping.
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from src.calibration.emos import (
    bin_probability_settlement,
    emos_predictive,
    emos_sigma_model,
    settlement_sigma_floor,
)


def _bin_bounds(b) -> tuple[Optional[float], Optional[float]]:
    """Accept either a (low, high) 2-tuple/list or a Bin object with .low/.high."""
    if isinstance(b, (tuple, list)):
        return b[0], b[1]
    return getattr(b, "low", None), getattr(b, "high", None)


def build_emos_q(
    *,
    city: str,
    season: str,
    metric: str,
    lead_days: float,
    members_native: "np.ndarray",
    unit: str,
    bins: Sequence,
    apply_settlement_floor: bool = False,
    require_settlement_floor: bool = True,
) -> Optional[tuple["np.ndarray", float, float]]:
    """Build the traded bin-probability vector from the EMOS calibrator alone.

    Returns ``(q_vector, mu_native, sigma_native)`` where ``q_vector`` is normalized over
    ``bins`` and ``(mu_native, sigma_native)`` are the predictive mean/std-dev in the bins'
    native unit. The caller uses ``q_vector`` as the point p_cal AND draws the lcb bootstrap
    from ``N(mu_native, sigma_native)`` — so the point q and the q_lcb derive from ONE sigma
    (fixing the point under-dispersion). Returns ``None`` when the EMOS cell is served=raw or
    missing: the caller then falls back to the honest raw analytic p_raw, NEVER to the bias maze.

    METRIC FAIL-CLOSED ANTIBODY (2026-06-04): HIGH and LOW are physically different quantities
    (daily max vs daily min) with separate fits. The EMOS table is single-metric (``_meta.metric``;
    HIGH-only today). If ``metric`` does not match the table's metric, this returns ``None`` —
    serving HIGH-fit (mu, sigma) onto a LOW market's member-MIN array is UNCONSTRUCTABLE here, so
    the caller honest-falls-back instead of trading a cross-metric calibration. This makes the
    metric-crossing CATEGORY impossible, not just the mainstream-gate instance.

    Args:
        city:           city name matching the EMOS table key.
        season:         season code (DJF/MAM/JJA/SON) — must match the table's hemisphere convention.
        metric:         'high' | 'low' — the market's settlement metric. MUST match the EMOS table
                        metric or build returns None (no cross-metric serve).
        lead_days:      decision lead in days (lead enters sigma via the e*lead EMOS term).
        members_native: 1-D ensemble member extrema (max for high / min for low) in native unit.
        unit:           'F' or 'C' — the settlement-asserted native unit of members + bins.
        bins:           settlement bins as (low, high) tuples or Bin objects (None = open shoulder).
    """
    # Metric fail-closed is STRUCTURAL: cells are keyed city|season|metric, so a LOW lookup
    # resolves ONLY a LOW cell — a HIGH fit can never serve a LOW market. Missing cell -> None.
    u = (unit or "").strip().upper()
    arr = np.asarray(members_native, dtype=float)
    if arr.size < 2:
        return None
    members_c = (arr - 32.0) / 1.8 if u.startswith("F") else arr

    pred = emos_predictive(city, season, float(lead_days), members_c, metric=str(metric).lower())
    if pred is None:
        return None  # served=raw / missing -> honest raw fallback (caller decides)
    mu_c, sigma_c = pred

    # EMPIRICAL settlement σ-floor (loop-breaker, investigation 2026-06-05; iron rule 5). The EMOS
    # σ-model is systemically under-dispersed (median σ_emos/σ_settled = 0.49) → q pins near 1.0 on
    # plausible bins → expensive buy_no on the winner. Floor σ at k·σ_settled (DETRENDED trailing-window
    # settlement std, °C). Flag-gated: OFF ⇒ byte-identical. max() only WIDENS σ → lower q_lcb → fewer
    # overconfident bets; can NEVER tighten or create a wrong-side trade. σ_settled is °C, like sigma_c.
    if apply_settlement_floor:
        floor_c = settlement_sigma_floor(
            city, season, str(metric).lower(), required=require_settlement_floor
        )
        if floor_c is not None:
            sigma_c = max(sigma_c, float(floor_c))

    if u.startswith("F"):
        mu_native = mu_c * 1.8 + 32.0
        sigma_native = sigma_c * 1.8
    else:
        mu_native = mu_c
        sigma_native = sigma_c
    if not (sigma_native > 0.0):
        return None

    q = np.array(
        [bin_probability_settlement(mu_native, sigma_native, lo, hi)
         for lo, hi in (_bin_bounds(b) for b in bins)],
        dtype=float,
    )
    total = float(q.sum())
    if not np.isfinite(total) or total <= 0.0:
        return None
    return q / total, float(mu_native), float(sigma_native)


def build_honest_raw_q(
    *,
    city: str,
    season: str,
    metric: str,
    lead_days: float,
    members_native: "np.ndarray",
    unit: str,
    bins: Sequence,
    apply_settlement_floor: bool = False,
    require_settlement_floor: bool = True,
) -> Optional[tuple["np.ndarray", float, float]]:
    """Honest-raw q with a CALIBRATED DISPERSION FLOOR for served=raw / EMOS-miss cells.

    The do-no-harm gate serves RAW for cells where EMOS's MEAN did not generalize on held-out
    CRPS. But the counterfactual (2026-06-05) showed the RESIDUAL expensive-NO-on-the-winner losses
    concentrate in those raw cells, whose raw ensemble σ (~0.6 °C for e.g. Singapore JJA high) is too
    TIGHT — the under-dispersion that pins q_no near 1.0 and drives the loss. This keeps the do-no-harm
    raw MEAN (x̄) but FLOORS the dispersion at the cell's calibrated EMOS lead-aware σ:
    ``σ = max(raw_σ, emos_σ_model)``. The point q AND the lcb bootstrap both derive from N(x̄, σ) —
    one σ, fixing the residual under-dispersion. Conservative by construction: max() can only WIDEN σ
    → lower q_lcb → fewer overconfident bets (iron rule 5); it never tightens.

    Returns ``(q_vector, mu_native, sigma_native)`` (raw mean, floored σ, native unit), or ``None`` when
    NO EMOS σ-model exists for the cell (truly-absent cell) — the caller then uses the pure raw analytic
    (there is no calibrated floor to apply). Mirrors build_emos_q's unit handling and bin integration.
    """
    u = (unit or "").strip().upper()
    arr = np.asarray(members_native, dtype=float)
    if arr.size < 2:
        return None
    members_c = (arr - 32.0) / 1.8 if u.startswith("F") else arr
    floor_c = emos_sigma_model(city, season, float(lead_days), members_c, metric=str(metric).lower())
    if floor_c is None:
        return None  # no calibrated σ for this cell -> caller uses the pure raw analytic
    mu_c = float(np.mean(members_c))
    raw_sigma_c = float(np.std(members_c, ddof=1))
    sigma_c = max(raw_sigma_c, float(floor_c))
    # EMPIRICAL settlement σ-floor (loop-breaker, investigation 2026-06-05; iron rule 5) — composes
    # ON TOP OF the existing emos_σ_model floor: σ = max(raw_σ, emos_σ_model, k·σ_settled). The
    # emos_σ_model floor is itself the EMOS σ-model, which the investigation proved is ALSO too tight;
    # the EMPIRICAL settlement floor is the correct one. Flag-gated: OFF ⇒ byte-identical (existing
    # emos_σ_model floor only). max() only WIDENS → lower q_lcb → fewer overconfident bets. σ_settled °C.
    if apply_settlement_floor:
        settled_floor_c = settlement_sigma_floor(
            city, season, str(metric).lower(), required=require_settlement_floor
        )
        if settled_floor_c is not None:
            sigma_c = max(sigma_c, float(settled_floor_c))
    if not (sigma_c > 0.0):
        return None
    if u.startswith("F"):
        mu_native = mu_c * 1.8 + 32.0
        sigma_native = sigma_c * 1.8
    else:
        mu_native = mu_c
        sigma_native = sigma_c
    q = np.array(
        [bin_probability_settlement(mu_native, sigma_native, lo, hi)
         for lo, hi in (_bin_bounds(b) for b in bins)],
        dtype=float,
    )
    total = float(q.sum())
    if not np.isfinite(total) or total <= 0.0:
        return None
    return q / total, float(mu_native), float(sigma_native)
