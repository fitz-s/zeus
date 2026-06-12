# Created: 2026-06-02
# Last reused or audited: 2026-06-07
# Authority basis: EMOS shadow-ledger task; PIECE 1 spec.
#   Model: mu=a+b*xbar; sigma2=exp(c+d*log(S2)+e*lead_days).
#   Table: state/emos_calibration.json, schema _meta + cells{"City|SEASON": {params,n,served}}.
#   served=="raw" or missing cell → return None (caller falls back to raw ensemble).
# 2026-06-07 ITEM 3 (path provenance): _SIGMA_FLOOR_PATH + _EMOS_TABLE_PATH now resolve via
#   the SINGLE canonical state-dir resolver (src.config.state_path -> STATE_DIR) instead of a
#   module-local recomputed `Path(__file__).parent.parent.parent / "state"`, so the artifacts
#   follow the daemon's runtime state dir (a recomputed path is a silent-divergence hazard that
#   made the q_lcb settlement-σ floor no-op when the file was absent). load_sigma_floor_table
#   now FAILS LOUD (logger.warning) on an absent floor file in the legacy required=False path
#   rather than a quiet debug, so an operator sees the floor is inert.
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
from typing import Optional

import numpy as np
from scipy.stats import norm as _scipy_norm

from src.config import state_path as _state_path

logger = logging.getLogger(__name__)

# PROVENANCE (ITEM 3, 2026-06-07): resolve state artifacts through the SINGLE canonical
# state-dir resolver the rest of the daemon uses (src.config.state_path -> STATE_DIR), NOT
# a module-local recomputed dir. The old `Path(__file__).parent.parent.parent / "state"`
# happened to coincide with STATE_DIR, but a recomputed path is a silent-divergence hazard:
# if the daemon's state dir ever relocates, this loader would keep pointing at the dead path
# and the settlement σ-floor would silently no-op (0 cells -> q_lcb floor inert). Reusing the
# canonical resolver makes the floor file move WITH the daemon's runtime state. (Fitz #4:
# code provenance — one resolver, no parallel path computation.)
_STATE_DIR = _state_path("")  # canonical runtime state dir (parent of the artifacts below)
_EMOS_TABLE_PATH = _state_path("emos_calibration.json")

_emos_table_cache: dict | None = None
_emos_table_lock = threading.Lock()

# EMPIRICAL settlement σ-floor (q=1.000 investigation 2026-06-05; iron rule 5: overconfidence = ruin).
# The EMOS σ-model is systemically under-dispersed (median σ_emos/σ_settled = 0.49). The correct
# dispersion FLOOR is the DETRENDED trailing-window settlement std per (city, season, metric),
# precomputed offline by scripts/fit_settlement_sigma_floor.py into this table.
_SIGMA_FLOOR_PATH = _state_path("settlement_sigma_floor.json")
_sigma_floor_cache: dict | None = None
_sigma_floor_lock = threading.Lock()


class SettlementSigmaFloorError(RuntimeError):
    """Raised when the settlement sigma floor is required but cannot be proven valid."""


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


def load_sigma_floor_table(*, required: bool = False) -> dict:
    """Return the cached EMPIRICAL settlement σ-floor table dict.

    Loaded once per process from state/settlement_sigma_floor.json (cached + thread-safe,
    mirroring load_emos_table). Structure:
        {"_meta": {"created":..., "method":..., "k_default": float},
         "cells": {"City|SEASON|metric": {"sigma_floor_c": float, "n": int, "window": str}}}
    All values °C. Legacy callers use ``required=False`` and get an empty dict if the file is
    missing or malformed (fail-soft: callers get None from settlement_sigma_floor and keep their
    model σ — no floor, no crash). EDLI flag-on callers use ``required=True``: missing or malformed
    artifacts raise SettlementSigmaFloorError so the live candidate cannot silently bypass the floor.
    """
    global _sigma_floor_cache
    if _sigma_floor_cache is not None and (not required or _sigma_floor_cache):
        return _sigma_floor_cache
    with _sigma_floor_lock:
        if _sigma_floor_cache is not None and (not required or _sigma_floor_cache):
            return _sigma_floor_cache
        try:
            raw = _SIGMA_FLOOR_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                if required:
                    raise SettlementSigmaFloorError(
                        "SETTLEMENT_SIGMA_FLOOR_MALFORMED_ARTIFACT:not_dict"
                    )
                logger.warning("settlement_sigma_floor.json is not a dict — treating as empty")
                data = {}
            _sigma_floor_cache = data
        except FileNotFoundError:
            if required:
                raise SettlementSigmaFloorError(
                    f"SETTLEMENT_SIGMA_FLOOR_MISSING_ARTIFACT:{_SIGMA_FLOOR_PATH}"
                )
            # FAIL-LOUD (ITEM 3, 2026-06-07): an ABSENT floor file silently returning {}
            # makes the q_lcb settlement-σ floor INERT (0 cells -> max(model_σ, floor) never
            # widens). The legacy (required=False) path must NOT crash, but it MUST warn loud
            # so an operator sees the floor is disabled — not a quiet debug that hides the
            # provenance gap. (Memory: the floor only worked when repointed at the live state
            # dir's 232-cell table; a missing file at runtime is an operator-visible event.)
            logger.warning(
                "settlement_sigma_floor.json not found at %s; settlement σ-floor is DISABLED "
                "(q_lcb floor inert, 0 cells). Runtime widening will rely on model σ only.",
                _SIGMA_FLOOR_PATH,
            )
            _sigma_floor_cache = {}
        except SettlementSigmaFloorError:
            raise
        except Exception as exc:  # noqa: BLE001 — fail-soft unless the EDLI floor flag requires it
            if required:
                raise SettlementSigmaFloorError(
                    f"SETTLEMENT_SIGMA_FLOOR_MALFORMED_ARTIFACT:{type(exc).__name__}: {exc}"
                ) from exc
            logger.warning("Failed to load settlement_sigma_floor.json: %s", exc)
            _sigma_floor_cache = {}
    return _sigma_floor_cache


def settlement_sigma_floor(
    city: str,
    season: str,
    metric: str,
    *,
    required: bool = False,
) -> Optional[float]:
    """The EMPIRICAL settlement σ-floor (°C) for a (city, season, metric) cell, or None if absent.

    Returns ``k_default · sigma_floor_c`` where ``sigma_floor_c`` is the DETRENDED trailing-window
    settlement std for the cell and ``k_default`` (default 0.8) is read from the table's ``_meta``.
    Legacy callers use ``required=False``: None when the cell is missing from the table, so the
    caller keeps its model/EMOS σ (no floor). EDLI flag-on callers use ``required=True``: missing
    artifact, malformed artifact/cell, missing cell, or non-positive effective floor raises
    SettlementSigmaFloorError, making the candidate fail-closed instead of silently bypassing floor.

    The floor is applied UNIVERSALLY at the q seam as ``σ_eff = max(model_σ, this)``: conservative by
    construction (max() only WIDENS σ → lower q_lcb → fewer overconfident bets; it can NEVER tighten
    or create a wrong-side trade). This is the loop-breaker for the q=1.000 EMOS under-dispersion
    (iron rule 5: overconfidence = ruin). Metric is lowercased to match the cell key (no crossing).

    Cached + thread-safe like the EMOS table. Fail-soft only when ``required`` is false.
    """
    try:
        table = load_sigma_floor_table(required=required)
        cells = table.get("cells", {})
        if not isinstance(cells, dict):
            if required:
                raise SettlementSigmaFloorError("SETTLEMENT_SIGMA_FLOOR_MALFORMED_ARTIFACT:cells")
            return None
        key = emos_cell_key(city, season, metric)
        cell = cells.get(key)
        if cell is None:
            if required:
                raise SettlementSigmaFloorError(f"SETTLEMENT_SIGMA_FLOOR_MISSING_CELL:{key}")
            return None
        if not isinstance(cell, dict):
            if required:
                raise SettlementSigmaFloorError(f"SETTLEMENT_SIGMA_FLOOR_MALFORMED_CELL:{key}")
            return None
        floor_c = cell.get("sigma_floor_c")
        if floor_c is None:
            if required:
                raise SettlementSigmaFloorError(
                    f"SETTLEMENT_SIGMA_FLOOR_MALFORMED_CELL:missing_sigma_floor_c:{key}"
                )
            return None
        try:
            floor_c = float(floor_c)
            k = float(table.get("_meta", {}).get("k_default", 0.8))
        except Exception as exc:  # noqa: BLE001 — malformed scalar value
            if required:
                raise SettlementSigmaFloorError(
                    f"SETTLEMENT_SIGMA_FLOOR_MALFORMED_CELL:{key}: {exc}"
                ) from exc
            return None
        if not (floor_c > 0.0):
            if required:
                raise SettlementSigmaFloorError(
                    f"SETTLEMENT_SIGMA_FLOOR_NON_POSITIVE:{key}:sigma_floor_c={floor_c}"
                )
            return None
        out = k * floor_c
        if required and not (out > 0.0):
            raise SettlementSigmaFloorError(
                f"SETTLEMENT_SIGMA_FLOOR_NON_POSITIVE:{key}:effective_floor={out}"
            )
        return out if out > 0.0 else None
    except SettlementSigmaFloorError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-soft: no floor rather than crash the q seam
        if required:
            raise SettlementSigmaFloorError(
                f"SETTLEMENT_SIGMA_FLOOR_MALFORMED_ARTIFACT:{type(exc).__name__}: {exc}"
            ) from exc
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
    rounding_rule: str = "wmo_half_up",
) -> float:
    """Normal CDF probability mass for a settlement bin under the bin's rounding rule.

    The integration bounds are the SETTLEMENT PREIMAGE of the bin label set,
    derived from ``rounding_rule`` via the single contract function
    ``src.contracts.settlement_semantics.settlement_preimage_offsets`` — so this
    integrator consumes the SAME per-city rule that the bins (and grading, and
    day0 lanes) already declare, instead of hardcoding the WMO convention.

    This fixes the degenerate point-bin problem: when bin_low == bin_high == X
    (an interior bin labeled X), bin_probability() integrates over [X, X) = zero width
    and returns 0.  This function expands interior bins to their settlement preimage
    before integrating, so interior bins always produce non-zero probability mass.

    Integration intervals by bin type (precision=1 ⇒ half_step=0.5):
      ``wmo_half_up`` (SYMMETRIC, standard cities) — round(x)==X ⟺ x∈[X−0.5, X+0.5):
      - Interior bin (bin_low == bin_high == X):     [X − 0.5, X + 0.5)
      - Open-low shoulder (None, X):                 (−∞, X + 0.5)
      - Open-high shoulder (X, None):                [X − 0.5, +∞)
      - Distinct-endpoint bin (A, B), A < B:         [A − 0.5, B + 0.5)
      ``oracle_truncate`` / ``floor`` (ASYMMETRIC, Hong Kong) — floor(x)==X ⟺ x∈[X, X+1):
      - Interior bin (X, X):                         [X, X + 1)
      - Open-low shoulder (None, X):                 (−∞, X + 1)
      - Open-high shoulder (X, None):                [X, +∞)
      - Distinct-endpoint bin (A, B):                [A, B + 1)

    Authority: ensemble_signal.py analytic_p_raw_vector_from_maxes §preimage
    derivation (the HK-aware MC-equivalent path) + settlement_preimage_offsets.

    Args:
        mu:        Normal distribution mean (same unit as bin bounds).
        sigma:     Normal distribution std-dev (must be > 0).
        bin_low:   Bin lower label, or None for open-low shoulder.
        bin_high:  Bin upper label, or None for open-high shoulder.
        half_step: Rounding half-width (default 0.5 for precision=1).  Equals
                   settlement_step_c / 2; 0.5 is correct for all current markets.
        rounding_rule: Settlement rounding convention from the bin/city contract.
                   ``wmo_half_up`` (default — byte-identical to the historical
                   symmetric path) for standard cities; ``oracle_truncate`` /
                   ``floor`` for Hong Kong (HKO/UMA truncation); ``ceil``.

    Returns:
        Probability mass as float in [0, 1]. Never returns 0 for a non-degenerate
        sigma (unless mu is far from the bin).
    """
    if sigma <= 0.0:
        raise ValueError(f"bin_probability_settlement: sigma must be positive, got {sigma}")

    # Preimage offsets derived from the declared rounding rule (the SINGLE
    # contract source).  wmo_half_up -> (-half_step, +half_step) keeps the
    # historical symmetric path byte-identical; HK truncate -> (0, +2·half_step).
    from src.contracts.settlement_semantics import settlement_preimage_offsets

    low_offset, high_offset = settlement_preimage_offsets(
        rounding_rule, half_step=half_step
    )

    # Lower integration bound:
    if bin_low is None:
        cdf_low = 0.0  # −∞
    else:
        cdf_low = float(_scipy_norm.cdf((float(bin_low) + low_offset - mu) / sigma))

    # Upper integration bound:
    if bin_high is None:
        cdf_high = 1.0  # +∞
    else:
        cdf_high = float(_scipy_norm.cdf((float(bin_high) + high_offset - mu) / sigma))

    return max(0.0, cdf_high - cdf_low)
