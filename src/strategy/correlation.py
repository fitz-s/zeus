"""Heuristic correlation matrix for portfolio exposure. Spec §5.5.

Temperature outcomes in nearby cities are correlated (same weather system).
We use a heuristic matrix based on cluster proximity to prevent
over-concentration without requiring historical correlation estimation.
"""

import logging

logger = logging.getLogger(__name__)

# Spec §5.5: Heuristic correlation coefficients between clusters.
# Same cluster = 1.0, nearby = 0.5-0.7, distant = 0.1-0.3
CLUSTER_CORRELATION = {
    # Keys must be in sorted() order to match the lookup in get_correlation().
    ("US-Midwest", "US-Northeast"): 0.6,
    ("US-Northeast", "US-Southeast"): 0.4,
    ("US-Northeast", "US-SouthCentral"): 0.3,
    ("US-Northeast", "US-Pacific"): 0.1,
    ("Europe", "US-Northeast"): 0.1,
    ("US-Midwest", "US-Southeast"): 0.3,
    ("US-Midwest", "US-SouthCentral"): 0.5,
    ("US-Midwest", "US-Pacific"): 0.2,
    ("Europe", "US-Midwest"): 0.1,
    ("US-SouthCentral", "US-Southeast"): 0.5,
    ("US-Pacific", "US-Southeast"): 0.1,
    ("Europe", "US-Southeast"): 0.1,
    ("US-Pacific", "US-SouthCentral"): 0.2,
    ("Europe", "US-SouthCentral"): 0.1,
    ("Europe", "US-Pacific"): 0.1,
}


def get_correlation(cluster_a: str, cluster_b: str) -> float:
    """Get heuristic correlation between two clusters."""
    if cluster_a == cluster_b:
        return 1.0

    key = tuple(sorted([cluster_a, cluster_b]))
    return CLUSTER_CORRELATION.get(key, 0.1)


def correlated_exposure(
    positions: list[dict],
    new_cluster: str,
    new_size_pct: float,
    bankroll: float,
) -> float:
    """Compute effective correlated exposure for a new position. Spec §5.5.

    Sum of (existing_exposure × correlation) for all held positions.
    Used to enforce max_correlated_pct limit.

    Args:
        positions: list of dicts with 'cluster' and 'size_usd' keys
        new_cluster: cluster of proposed new position
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
