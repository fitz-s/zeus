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

from src.config import cities_by_name

logger = logging.getLogger(__name__)

_MATRIX_PATH = Path(__file__).parent.parent.parent / "config" / "city_correlation_matrix.json"


@lru_cache(maxsize=1)
def _load_matrix() -> dict:
    """Load the data-driven Pearson correlation matrix if it exists."""
    if not _MATRIX_PATH.exists():
        return {}
    with open(_MATRIX_PATH) as f:
        return json.load(f).get("matrix", {})


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _haversine_fallback_correlation(city_a_name: str, city_b_name: str) -> float:
    """Geographic-distance decay when Pearson data is unavailable."""
    a = cities_by_name.get(city_a_name)
    b = cities_by_name.get(city_b_name)
    if a is None or b is None:
        return 0.05  # unknown -> weakest
    dist_km = _haversine_km(a.lat, a.lon, b.lat, b.lon)
    return max(0.05, math.exp(-dist_km / 2000.0))


def get_correlation(city_a: str, city_b: str) -> float:
    """Return pairwise temperature correlation between two cities.

    Primary source: data-driven Pearson from config/city_correlation_matrix.json
    (built offline from TIGGE ensemble snapshots by scripts/build_correlation_matrix.py).
    Fallback: haversine geographic distance decay with 2000km scale (mid-latitude
    weather system correlation scale).

    Self-correlation is 1.0.
    """
    if city_a == city_b:
        return 1.0
    matrix = _load_matrix()
    # Matrix stored as nested dict: {city_a: {city_b: value}}
    pair_a = matrix.get(city_a, {})
    if isinstance(pair_a, dict) and city_b in pair_a:
        return float(pair_a[city_b])
    pair_b = matrix.get(city_b, {})
    if isinstance(pair_b, dict) and city_a in pair_b:
        return float(pair_b[city_a])
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
