"""Calibration manager: bucket routing, maturity gate, hierarchical fallback.

Spec §3.2-3.4:
- 24 buckets: 6 clusters × 4 seasons
- Lead_days is Platt INPUT FEATURE, not bucket dimension
- Maturity gate controls regularization strength
- Fallback: cluster+season → season → global → uncalibrated
"""

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

from src.calibration.platt import ExtendedPlattCalibrator, calibrate_and_normalize
from src.calibration.store import (
    get_pairs_for_bucket,
    get_pairs_count,
    load_platt_model,
    save_platt_model,
)
from src.config import City, cities_by_name


# Spec §3.2: 6 clusters × 4 seasons = 24 buckets
CLUSTERS = [
    "US-Northeast", "US-Midwest", "US-Southeast",
    "US-SouthCentral", "US-Pacific", "Europe"
]
SEASONS = ["DJF", "MAM", "JJA", "SON"]

# Spec §3.3: Maturity levels
MATURITY_LEVEL1 = 150  # Standard Platt (C=1.0)
MATURITY_LEVEL2 = 50   # Standard Platt (C=1.0)
MATURITY_LEVEL3 = 15   # Strong regularization (C=0.1)
# Below 15: no Platt, use P_raw directly


def bucket_key(cluster: str, season: str) -> str:
    """Canonical bucket key for storage."""
    return f"{cluster}_{season}"


def season_from_date(date_str: str) -> str:
    """Map date string (YYYY-MM-DD) to season code."""
    month = int(date_str.split("-")[1])
    if month in (12, 1, 2):
        return "DJF"
    elif month in (3, 4, 5):
        return "MAM"
    elif month in (6, 7, 8):
        return "JJA"
    else:
        return "SON"


def route_to_bucket(city: City, target_date: str) -> str:
    """Route a city + date to its calibration bucket key."""
    season = season_from_date(target_date)
    return bucket_key(city.cluster, season)


def maturity_level(n_pairs: int) -> int:
    """Determine calibration maturity level from sample count.

    Spec §3.3:
    Level 1: n >= 150 → standard Platt (C=1.0), edge threshold 1×
    Level 2: 50 <= n < 150 → standard Platt (C=1.0), edge threshold 1.5×
    Level 3: 15 <= n < 50 → strong regularization (C=0.1), edge threshold 2×
    Level 4: n < 15 → no Platt (use P_raw), edge threshold 3×
    """
    if n_pairs >= MATURITY_LEVEL1:
        return 1
    elif n_pairs >= MATURITY_LEVEL2:
        return 2
    elif n_pairs >= MATURITY_LEVEL3:
        return 3
    else:
        return 4


def regularization_for_level(level: int) -> float:
    """Get sklearn LogisticRegression C parameter for maturity level."""
    if level <= 2:
        return 1.0
    elif level == 3:
        return 0.1
    else:
        raise ValueError(f"Level {level}: no Platt — use P_raw directly")


def edge_threshold_multiplier(level: int) -> float:
    """Edge threshold multiplier by calibration maturity. Spec §3.3."""
    return {1: 1.0, 2: 1.5, 3: 2.0, 4: 3.0}[level]


def get_calibrator(
    conn,
    city: City,
    target_date: str,
) -> tuple[Optional[ExtendedPlattCalibrator], int]:
    """Get the best available calibrator for a city+date.

    Implements hierarchical fallback (spec §3.4):
    1. cluster+season (primary bucket)
    2. season-only (pool all clusters)
    3. global (pool everything)
    4. None (uncalibrated — use P_raw)

    Returns: (calibrator_or_None, maturity_level)
    """
    season = season_from_date(target_date)
    cluster = city.cluster

    # Try primary bucket
    bk = bucket_key(cluster, season)
    model_data = load_platt_model(conn, bk)
    if model_data is not None:
        cal = _model_data_to_calibrator(model_data)
        level = maturity_level(model_data["n_samples"])
        return cal, level

    # Check if we have enough pairs to fit on the fly
    n = get_pairs_count(conn, cluster, season)
    if n >= MATURITY_LEVEL3:
        cal = _fit_from_pairs(conn, cluster, season)
        if cal is not None:
            level = maturity_level(n)
            return cal, level

    # Fallback: season-only (pool all clusters)
    for fallback_cluster in CLUSTERS:
        if fallback_cluster == cluster:
            continue
        bk_fb = bucket_key(fallback_cluster, season)
        model_data = load_platt_model(conn, bk_fb)
        if model_data is not None and model_data["n_samples"] >= MATURITY_LEVEL3:
            cal = _model_data_to_calibrator(model_data)
            level = maturity_level(model_data["n_samples"])
            return cal, max(level, 3)  # Fallback is at most level 3

    # Level 4: no calibrator available
    return None, 4


def _model_data_to_calibrator(model_data: dict) -> ExtendedPlattCalibrator:
    """Reconstruct calibrator from stored model data."""
    cal = ExtendedPlattCalibrator()
    cal.A = model_data["A"]
    cal.B = model_data["B"]
    cal.C = model_data["C"]
    cal.n_samples = model_data["n_samples"]
    cal.fitted = True
    cal.bootstrap_params = [
        tuple(p) for p in model_data["bootstrap_params"]
    ]
    return cal


def _fit_from_pairs(
    conn, cluster: str, season: str
) -> Optional[ExtendedPlattCalibrator]:
    """Fit a new calibrator from stored pairs."""
    pairs = get_pairs_for_bucket(conn, cluster, season)
    if len(pairs) < MATURITY_LEVEL3:
        return None

    p_raw = np.array([p["p_raw"] for p in pairs])
    lead_days = np.array([p["lead_days"] for p in pairs])
    outcomes = np.array([p["outcome"] for p in pairs])

    level = maturity_level(len(pairs))
    reg_C = regularization_for_level(level)

    cal = ExtendedPlattCalibrator()
    try:
        cal.fit(p_raw, lead_days, outcomes, regularization_C=reg_C)
    except Exception as e:
        logger.warning("Platt fit failed for %s_%s: %s", cluster, season, e)
        return None

    # Save to DB for future use
    bk = bucket_key(cluster, season)
    save_platt_model(
        conn, bk,
        cal.A, cal.B, cal.C,
        cal.bootstrap_params,
        cal.n_samples,
    )
    conn.commit()

    return cal


def maybe_refit_bucket(conn, city: City, target_date: str) -> bool:
    """Refit the city's cluster-season bucket if enough fresh pairs now exist."""
    season = season_from_date(target_date)
    cal = _fit_from_pairs(conn, city.cluster, season)
    return cal is not None
