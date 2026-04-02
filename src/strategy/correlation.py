"""Heuristic correlation matrix for portfolio exposure. Spec §5.5.

Temperature outcomes in nearby cities are correlated (same weather system).
We use a heuristic matrix based on cluster proximity to prevent
over-concentration without requiring historical correlation estimation.
"""

import logging

from src.config import correlation_default_cross_cluster, correlation_matrix

logger = logging.getLogger(__name__)

def get_correlation(cluster_a: str, cluster_b: str) -> float:
    """Get heuristic correlation between two clusters."""
    if cluster_a == cluster_b:
        return 1.0

    matrix = correlation_matrix()
    if cluster_b in matrix.get(cluster_a, {}):
        return matrix[cluster_a][cluster_b]
    if cluster_a in matrix.get(cluster_b, {}):
        return matrix[cluster_b][cluster_a]
    return correlation_default_cross_cluster()


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
