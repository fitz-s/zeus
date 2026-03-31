"""Benjamini-Hochberg FDR filter for edge selection.

Spec §4.4: Controls false discovery rate across 220 simultaneous hypotheses
(10 cities × 11 bins × 2 directions per cycle).

p-values are computed via np.mean(bootstrap_edges <= 0) in MarketAnalysis,
NEVER via approximation formula.
"""

from src.config import settings
from src.types import BinEdge

# HARDCODED(setting_key="edge.fdr_alpha", note_key="edge._fdr_alpha_note",
#           tier=1, replace_after="500+ candidate evaluations",
#           data_needed="observed false positive rate versus target FDR")
DEFAULT_FDR_ALPHA = float(settings["edge"]["fdr_alpha"])


def fdr_filter(
    edges: list[BinEdge],
    fdr_alpha: float = DEFAULT_FDR_ALPHA,
) -> list[BinEdge]:
    """Benjamini-Hochberg procedure for FDR control.

    Spec §4.4: Sort by p-value ascending, find largest k where
    p_value[k] <= fdr_alpha * k / m. Return edges 1..k.

    Args:
        edges: list of BinEdge with p_value from bootstrap
        fdr_alpha: target FDR level (default 10%)

    Returns: filtered list of edges passing BH threshold
    """
    if not edges:
        return []

    m = len(edges)
    sorted_by_p = sorted(edges, key=lambda e: e.p_value)

    threshold_k = 0
    for k, e in enumerate(sorted_by_p, 1):
        if e.p_value <= fdr_alpha * k / m:
            threshold_k = k

    return sorted_by_p[:threshold_k] if threshold_k > 0 else []
