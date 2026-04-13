"""Data-driven pairwise city temperature correlation. Spec §5.5 (K3 revision).

Primary source: offline Pearson matrix in config/city_correlation_matrix.json
(built from TIGGE ensemble_snapshots by scripts/build_correlation_matrix.py).
Fallback: haversine geographic distance decay (2000 km scale).
"""

from __future__ import annotations

import json
import logging
import math
from functools import lru_cache
from pathlib import Path

from src.config import cities_by_name, correlation_default_cross_cluster

logger = logging.getLogger(__name__)

_MATRIX_PATH = Path(__file__).parent.parent.parent / "config" / "city_correlation_matrix.json"


@lru_cache(maxsize=1)
def _load_matrix() -> dict:
    """Load data-driven Pearson matrix + validate keys against city config.

    On validation failure (unknown city keys), logs a warning and returns
    an empty matrix — the haversine fallback path in get_correlation
    handles the missing data gracefully. This prevents stale matrix files
    from crashing the entire risk engine.
    """
    if not _MATRIX_PATH.exists():
        return {}
    with open(_MATRIX_PATH) as f:
        raw = json.load(f).get("matrix", {})
    # Schema validation: every key must be a known city.
    # Fail SAFE: log warning + return {} so haversine fallback handles all queries.
    known = set(cities_by_name.keys())
    unknown_outer = [k for k in raw.keys() if k not in known]
    if unknown_outer:
        logger.warning(
            f"city_correlation_matrix.json contains unknown outer keys: {unknown_outer}. "
            f"Treating matrix as absent; all queries will use haversine fallback. "
            f"Regenerate via scripts/build_correlation_matrix.py to fix."
        )
        return {}
    # Also validate inner keys (correlated-with cities)
    unknown_inner_total = []
    for outer, inner in raw.items():
        if not isinstance(inner, dict):
            continue
        unknown_inner = [k for k in inner.keys() if k not in known]
        if unknown_inner:
            unknown_inner_total.extend([f"{outer}/{k}" for k in unknown_inner])
    if unknown_inner_total:
        logger.warning(
            f"city_correlation_matrix.json contains unknown inner keys: "
            f"{unknown_inner_total[:10]}... "
            f"Treating matrix as absent; all queries will use haversine fallback."
        )
        return {}
    return raw


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _haversine_fallback_correlation(city_a_name: str, city_b_name: str) -> float:
    """Geographic-distance decay when Pearson data is unavailable.

    Floor is correlation_default_cross_cluster() (0.10), aligned with the
    pre-K3 static fallback value. Pairs beyond ~6000 km decay below this
    floor and are held at 0.10 as a conservative risk-correlation baseline.
    """
    fallback_floor = correlation_default_cross_cluster()
    a = cities_by_name.get(city_a_name)
    b = cities_by_name.get(city_b_name)
    if a is None or b is None:
        return fallback_floor
    dist_km = _haversine_km(a.lat, a.lon, b.lat, b.lon)
    return max(fallback_floor, math.exp(-dist_km / 2000.0))


def get_correlation(city_a: str, city_b: str) -> float:
    """Return pairwise temperature correlation between two cities.

    Primary source: data-driven Pearson from config/city_correlation_matrix.json
    (built offline from TIGGE ensemble snapshots by scripts/build_correlation_matrix.py).
    Fallback: haversine geographic distance decay with 2000km scale (mid-latitude
    weather system correlation scale).

    Values are clamped to [0.0, 1.0]: anti-correlation is treated as
    "uncorrelated" for risk accumulation purposes, not as negative
    exposure subtraction.

    Self-correlation is 1.0.
    """
    if city_a == city_b:
        return 1.0
    matrix = _load_matrix()
    # Matrix stored as nested dict: {city_a: {city_b: value}}
    pair_a = matrix.get(city_a, {})
    if isinstance(pair_a, dict) and city_b in pair_a:
        return max(0.0, min(1.0, float(pair_a[city_b])))
    pair_b = matrix.get(city_b, {})
    if isinstance(pair_b, dict) and city_a in pair_b:
        return max(0.0, min(1.0, float(pair_b[city_a])))
    return _haversine_fallback_correlation(city_a, city_b)


def correlated_exposure(
    positions: list[dict],
    new_cluster: str,
    new_size_pct: float,
    bankroll: float,
) -> float:
    """Compute effective correlated exposure for a new position. Spec §5.5.

    Sum of (existing_exposure x correlation) for all held positions.
    Used to enforce max_correlated_pct limit.

    Args:
        positions: list of dicts with 'cluster' and 'size_usd' keys
        new_cluster: city name of proposed new position (K3: cluster == city.name)
        new_size_pct: size of new position as fraction of bankroll
        bankroll: total capital

    Returns: effective correlated exposure as fraction of bankroll
    """
    if bankroll <= 0:
        return 0.0

    total = new_size_pct  # Start with the new position itself

    for pos in positions:
        pos_cluster = pos["cluster"]
        pos_pct = pos["size_usd"] / bankroll
        corr = get_correlation(new_cluster, pos_cluster)
        total += pos_pct * corr

    return total
