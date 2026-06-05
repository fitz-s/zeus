# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: EMOS shadow-ledger task; PIECE 1 spec.
#   Model: mu=a+b*xbar; sigma2=exp(c+d*log(S2)+e*lead_days).
#   Table: state/emos_calibration.json, schema _meta + cells{"City|SEASON": {params,n,served}}.
#   served=="raw" or missing cell → return None (caller falls back to raw ensemble).
"""EMOS predictive-serve helpers.

Provides:
  load_emos_table()        — cached loader of state/emos_calibration.json.
  season_for(date)         — DJF/MAM/JJA/SON from a date object.
  emos_predictive(...)     — (mu_c, sigma_c) | None per EMOS NGR model.
  bin_probability(...)     — CDF-based bin probability, open shoulders supported.

All internal computations are in °C.  Callers are responsible for unit
conversion when they need probabilities on °F bins (multiply sigma_c by 1.8,
convert mu_c to °F with the standard formula, then call bin_probability with
those °F values and °F bin bounds).
"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.stats import norm as _scipy_norm

logger = logging.getLogger(__name__)

_STATE_DIR = Path(__file__).parent.parent.parent / "state"
_EMOS_TABLE_PATH = _STATE_DIR / "emos_calibration.json"

_emos_table_cache: dict | None = None
_emos_table_lock = threading.Lock()

# EMPIRICAL settlement σ-floor (q=1.000 investigation 2026-06-05; iron rule 5: overconfidence = ruin).
# The EMOS σ-model is systemically under-dispersed (median σ_emos/σ_settled = 0.49). The correct
# dispersion FLOOR is the DETRENDED trailing-window settlement std per (city, season, metric),
# precomputed offline by scripts/fit_settlement_sigma_floor.py into this table.
_SIGMA_FLOOR_PATH = _STATE_DIR / "settlement_sigma_floor.json"
_sigma_floor_cache: dict | None = None
_sigma_floor_lock = threading.Lock()


def load_emos_table() -> dict:
    """Return the cached EMOS calibration table dict.

    The table is loaded once per process from state/emos_calibration.json.
    Structure: {"_meta": {...}, "cells": {"City|SEASON": {"params":[a,b,c,d,e], "n":int, "served":"emos"|"raw"}}}.
    Returns an empty dict if the file is missing or malformed (fail-closed: callers get None from emos_predictive).
    """
    global _emos_table_cache
    if _emos_table_cache is not None:
        return _emos_table_cache
    with _emos_table_lock:
        if _emos_table_cache is not None:
            return _emos_table_cache
        try:
            raw = _EMOS_TABLE_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                logger.warning("emos_calibration.json is not a dict — treating as empty")
                data = {}
            _emos_table_cache = data
        except FileNotFoundError:
            logger.debug("state/emos_calibration.json not found; EMOS serving disabled")
            _emos_table_cache = {}
        except Exception as exc:
            logger.warning("Failed to load emos_calibration.json: %s", exc)
            _emos_table_cache = {}
    return _emos_table_cache


def load_sigma_floor_table() -> dict:
    """Return the cached EMPIRICAL settlement σ-floor table dict.

    Loaded once per process from state/settlement_sigma_floor.json (cached + thread-safe,
    mirroring load_emos_table). Structure:
        {"_meta": {"created":..., "method":..., "k_default": float},
         "cells": {"City|SEASON|metric": {"sigma_floor_c": float, "n": int, "window": str}}}
    All values °C. Returns an empty dict if the file is missing or malformed (fail-soft:
    callers get None from settlement_sigma_floor and keep their model σ — no floor, no crash).
    """
    global _sigma_floor_cache
    if _sigma_floor_cache is not None:
        return _sigma_floor_cache
    with _sigma_floor_lock:
        if _sigma_floor_cache is not None:
            return _sigma_floor_cache
        try:
            raw = _SIGMA_FLOOR_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                logger.warning("settlement_sigma_floor.json is not a dict — treating as empty")
                data = {}
            _sigma_floor_cache = data
        except FileNotFoundError:
            logger.debug("state/settlement_sigma_floor.json not found; settlement σ-floor disabled")
            _sigma_floor_cache = {}
        except Exception as exc:  # noqa: BLE001 — fail-soft: no floor rather than crash the q seam
            logger.warning("Failed to load settlement_sigma_floor.json: %s", exc)
            _sigma_floor_cache = {}
    return _sigma_floor_cache


def settlement_sigma_floor(city: str, season: str, metric: str) -> Optional[float]:
    """The EMPIRICAL settlement σ-floor (°C) for a (city, season, metric) cell, or None if absent.

    Returns ``k_default · sigma_floor_c`` where ``sigma_floor_c`` is the DETRENDED trailing-window
    settlement std for the cell and ``k_default`` (default 0.8) is read from the table's ``_meta``.
    None when the cell is missing from the table — the caller then keeps its model/EMOS σ (no floor).

    The floor is applied UNIVERSALLY at the q seam as ``σ_eff = max(model_σ, this)``: conservative by
    construction (max() only WIDENS σ → lower q_lcb → fewer overconfident bets; it can NEVER tighten
    or create a wrong-side trade). This is the loop-breaker for the q=1.000 EMOS under-dispersion
    (iron rule 5: overconfidence = ruin). Metric is lowercased to match the cell key (no crossing).

    Cached + thread-safe like the EMOS table. Fail-soft: any malformed cell -> None.
    """
    try:
        table = load_sigma_floor_table()
        cells = table.get("cells", {})
        cell = cells.get(emos_cell_key(city, season, metric))
        if cell is None:
            return None
        floor_c = cell.get("sigma_floor_c")
        if floor_c is None:
            return None
        floor_c = float(floor_c)
        if not (floor_c > 0.0):
            return None
        k = float(table.get("_meta", {}).get("k_default", 0.8))
        out = k * floor_c
        return out if out > 0.0 else None
    except Exception as exc:  # noqa: BLE001 — fail-soft: no floor rather than crash the q seam
        logger.warning("settlement_sigma_floor(%r, %r, %r) error: %s", city, season, metric, exc)
        return None


def season_for(target_date: date) -> str:
    """Return the meteorological season code for a date (Northern Hemisphere).

    Returns one of: DJF, MAM, JJA, SON.
    Note: emos_calibration.json uses season keys that match
    src.contracts.season.season_from_date with lat>=0 (NH convention).
    For Southern-Hemisphere cities the table itself must use the flipped key;
    this function is NH-only (same convention as the fit script).
    """
    month = target_date.month
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def emos_season(target_date) -> str:
    """Canonical NH month-only season for EMOS cell lookup. Accepts a date or 'YYYY-MM-DD' str.

    EMOS cells are keyed by NH month-season (fit_emos_calibration.season()). A
    hemisphere-aware season (season_from_date(lat)) SH-flips and would serve the
    OPPOSITE-season cell for Southern-Hemisphere cities — the season-crossing twin of
    the metric-crossing defect. EVERY EMOS caller (seam, shadow ledger, EMOS-CI override,
    boot guard, offline scorers) MUST use THIS function so the lookup season always
    matches the fit's keying. Do not reintroduce season_from_date(lat) on an EMOS path.
    """
    mm = target_date.month if hasattr(target_date, "month") else int(str(target_date)[5:7])
    if mm in (12, 1, 2):
        return "DJF"
    if mm in (3, 4, 5):
        return "MAM"
    if mm in (6, 7, 8):
        return "JJA"
    return "SON"


def emos_cell_key(city: str, season: str, metric: str) -> str:
    """The canonical 3-key for the EMOS table: ``city|season|metric`` (metric lowercased).

    The ONLY correct way to address a cell. Direct ``f"{city}|{season}"`` 2-key reads miss
    the metric-keyed table (silently None -> served=missing). Use this everywhere.
    """
    return f"{city}|{season}|{str(metric).lower()}"


def emos_predictive(
    city: str,
    season: str,
    lead_days: float,
    members_c: "np.ndarray",
    *,
    metric: str = "high",
) -> Optional[tuple[float, float]]:
    """Compute the EMOS predictive mean and std-dev (both in °C).

    Model:
        xbar = mean(members_c)
        S2   = var(members_c, ddof=1)
        mu   = a + b * xbar
        sigma = sqrt(exp(c + d * log(S2) + e * lead_days))

    Returns (mu_c, sigma_c) for an "emos" cell, None if:
      - cell served == "raw"
      - cell missing from table
      - any computation error (fail-closed)

    METRIC-KEYED (2026-06-04): cells are keyed ``city|season|metric``. HIGH and LOW are
    physically different quantities (daily max vs daily min) with separate fits; a LOW
    lookup resolves ONLY ``…|low`` and can never return a HIGH cell. ``metric`` defaults to
    "high" so legacy HIGH callers are unchanged after the table is re-keyed to ``…|high``.

    Args:
        city:       City name matching the calibration table key (e.g. "Amsterdam").
        season:     Season code DJF/MAM/JJA/SON.
        lead_days:  Lead time in days (float; e.g. lead_hours/24).
        members_c:  1-D numpy array of ensemble member extrema in °C (max for high / min for low).
        metric:     "high" | "low" — the market's settlement metric (keys the cell lookup).
    """
    try:
        table = load_emos_table()
        cells = table.get("cells", {})
        key = f"{city}|{season}|{str(metric).lower()}"
        cell = cells.get(key)
        if cell is None:
            return None
        if cell.get("served") != "emos":
            return None
        params = cell.get("params")
        if not isinstance(params, (list, tuple)) or len(params) != 5:
            logger.warning("emos cell %r has malformed params", key)
            return None
        a, b, c, d, e = (float(p) for p in params)
        arr = np.asarray(members_c, dtype=float)
        if arr.size < 2:
            return None
        xbar = float(np.mean(arr))
        s2 = float(np.var(arr, ddof=1))
        if s2 <= 0.0:
            # Cannot take log of non-positive variance; fall back to tiny floor
            s2 = 1e-6
        mu = a + b * xbar
        sigma2 = math.exp(c + d * math.log(s2) + e * lead_days)
        sigma = math.sqrt(sigma2)
        return (mu, sigma)
    except Exception as exc:
        logger.warning("emos_predictive(%r, %r) error: %s", city, season, exc)
        return None


def emos_sigma_model(
    city: str,
    season: str,
    lead_days: float,
    members_c: "np.ndarray",
    *,
    metric: str = "high",
) -> Optional[float]:
    """The EMOS predictive std-dev (°C) from the cell's fitted params, IGNORING the served gate.

    ``emos_predictive`` returns None for a served=raw cell (the do-no-harm gate kept that cell's
    RAW MEAN because EMOS's mean did not generalize). But the σ-model (c, d, e) was still fit for
    that cell, and the counterfactual (2026-06-05) showed the residual losses are concentrated in
    raw-served cells whose raw ensemble σ (~0.6 °C) is too tight — the under-dispersion that drives
    the expensive-NO-on-the-winner loss. This accessor exposes the calibrated lead-aware σ so the
    honest-raw path can FLOOR its dispersion at it (max(raw_σ, this)) — keeping the do-no-harm raw
    mean while killing the under-dispersion. Conservative by construction: a floor only WIDENS σ →
    lowers q_lcb → fewer overconfident bets (iron rule 5). Returns None if the cell is absent or its
    params are malformed (then the caller keeps the pure raw analytic σ — no model to floor with).
    """
    try:
        table = load_emos_table()
        cells = table.get("cells", {})
        cell = cells.get(emos_cell_key(city, season, metric))
        if cell is None:
            return None
        params = cell.get("params")
        if not isinstance(params, (list, tuple)) or len(params) != 5:
            return None
        _a, _b, c, d, e = (float(p) for p in params)
        arr = np.asarray(members_c, dtype=float)
        if arr.size < 2:
            return None
        s2 = float(np.var(arr, ddof=1))
        if s2 <= 0.0:
            s2 = 1e-6
        sigma = math.sqrt(math.exp(c + d * math.log(s2) + e * lead_days))
        return sigma if sigma > 0.0 else None
    except Exception as exc:  # noqa: BLE001 — fail-soft: no floor rather than crash the q seam
        logger.warning("emos_sigma_model(%r, %r) error: %s", city, season, exc)
        return None


def bin_probability(
    mu: float,
    sigma: float,
    low: Optional[float],
    high: Optional[float],
) -> float:
    """Normal CDF probability mass in the bin [low, high).

    Open-shoulder conventions:
      low=None  → treat as -infinity  → CDF term = 0
      high=None → treat as +infinity  → CDF term = 1

    Args:
        mu:    Normal distribution mean (same unit as low/high).
        sigma: Normal distribution std-dev (same unit as low/high; must be > 0).
        low:   Bin lower bound (inclusive), or None for open-low shoulder.
        high:  Bin upper bound (exclusive), or None for open-high shoulder.

    Returns:
        Probability mass as float in [0, 1].
    """
    if sigma <= 0.0:
        raise ValueError(f"bin_probability: sigma must be positive, got {sigma}")
    cdf_high: float
    cdf_low: float
    if high is None:
        cdf_high = 1.0
    else:
        cdf_high = float(_scipy_norm.cdf((high - mu) / sigma))
    if low is None:
        cdf_low = 0.0
    else:
        cdf_low = float(_scipy_norm.cdf((low - mu) / sigma))
    return max(0.0, cdf_high - cdf_low)


def bin_probability_settlement(
    mu: float,
    sigma: float,
    bin_low: Optional[float],
    bin_high: Optional[float],
    *,
    half_step: float = 0.5,
) -> float:
    """Normal CDF probability mass for a settlement bin using WMO round-half-up rounding.

    Matches the live settlement rounding convention in analytic_p_raw_vector_from_maxes
    (ensemble_signal.py:296): a temperature display value t covers the continuous
    interval [t − half_step, t + half_step).

    This fixes the degenerate point-bin problem: when bin_low == bin_high == X
    (an interior bin labeled X), bin_probability() integrates over [X, X) = zero width
    and returns 0.  This function expands interior bins to their settlement preimage
    before integrating, so interior bins always produce non-zero probability mass.

    Integration intervals by bin type (wmo_half_up, precision=1 ⇒ half_step=0.5):
      - Interior bin (bin_low == bin_high == X):
            [X − half_step, X + half_step)  ← preimage of round(x) == X
      - Open-low shoulder (bin_low is None, bin_high == X):
            (−∞, X + half_step)             ← all x that round to ≤ X
      - Open-high shoulder (bin_low == X, bin_high is None):
            [X − half_step, +∞)             ← all x that round to ≥ X
      - Distinct-endpoint bin (bin_low == A, bin_high == B, A < B):
            [A − half_step, B + half_step)  ← preimage of round(x) ∈ {A, …, B}

    Authority: ensemble_signal.py analytic_p_raw_vector_from_maxes §preimage derivation.

    Args:
        mu:        Normal distribution mean (same unit as bin bounds).
        sigma:     Normal distribution std-dev (must be > 0).
        bin_low:   Bin lower label, or None for open-low shoulder.
        bin_high:  Bin upper label, or None for open-high shoulder.
        half_step: Rounding half-width (default 0.5 for precision=1 wmo_half_up).
                   Must match the settlement precision; 0.5 is correct for all
                   current Zeus markets.

    Returns:
        Probability mass as float in [0, 1]. Never returns 0 for a non-degenerate
        sigma (unless mu is far from the bin).
    """
    if sigma <= 0.0:
        raise ValueError(f"bin_probability_settlement: sigma must be positive, got {sigma}")

    # Derive integration bounds by expanding each bin label by ±half_step.
    # Lower integration bound:
    if bin_low is None:
        cdf_low = 0.0  # −∞
    else:
        cdf_low = float(_scipy_norm.cdf((float(bin_low) - half_step - mu) / sigma))

    # Upper integration bound:
    if bin_high is None:
        cdf_high = 1.0  # +∞
    else:
        cdf_high = float(_scipy_norm.cdf((float(bin_high) + half_step - mu) / sigma))

    return max(0.0, cdf_high - cdf_low)
