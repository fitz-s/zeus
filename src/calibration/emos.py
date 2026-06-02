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


def emos_predictive(
    city: str,
    season: str,
    lead_days: float,
    members_c: "np.ndarray",
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

    Args:
        city:       City name matching the calibration table key (e.g. "Amsterdam").
        season:     Season code DJF/MAM/JJA/SON.
        lead_days:  Lead time in days (float; e.g. lead_hours/24).
        members_c:  1-D numpy array of ensemble member maxima in °C.
    """
    try:
        table = load_emos_table()
        cells = table.get("cells", {})
        key = f"{city}|{season}"
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
