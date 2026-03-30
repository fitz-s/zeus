"""Market fusion: α-weighted posterior and VWMP.

Spec §4.5: p_posterior = α × p_cal + (1-α) × p_market
α depends on calibration maturity, ensemble spread, model agreement,
lead time, and market freshness.
"""

import numpy as np


def vwmp(best_bid: float, best_ask: float,
         bid_size: float, ask_size: float) -> float:
    """Volume-Weighted Micro-Price. Spec §4.1.

    If total_size = 0: fall back to mid-price + log warning.
    Per CLAUDE.md: never use mid-price for edge calculations (VWMP required).
    """
    total = bid_size + ask_size
    if total == 0:
        # CLAUDE.md: VWMP with total size = 0 → fall back to mid-price + log
        import logging
        logging.getLogger(__name__).warning(
            "VWMP total_size=0, falling back to mid-price: bid=%.3f ask=%.3f",
            best_bid, best_ask
        )
        return (best_bid + best_ask) / 2.0
    return (best_bid * ask_size + best_ask * bid_size) / total


def compute_alpha(
    calibration_level: int,
    ensemble_spread: float,
    model_agreement: str,
    lead_days: float,
    hours_since_open: float,
) -> float:
    """Compute α for model-market blending. Spec §4.5.

    Higher α → trust model more. Lower α → trust market more.
    Clamped to [0.20, 0.85].
    """
    base = {1: 0.65, 2: 0.55, 3: 0.40, 4: 0.25}[calibration_level]
    a = base

    # Ensemble spread adjustments
    if ensemble_spread < 2.0:
        a += 0.05
    if ensemble_spread > 5.0:
        a -= 0.10

    # Model agreement adjustments
    if model_agreement == "SOFT_DISAGREE":
        a -= 0.10
    if model_agreement == "CONFLICT":
        a -= 0.20

    # Lead time adjustments
    if lead_days <= 1:
        a += 0.05
    if lead_days >= 5:
        a -= 0.05

    # Market freshness: recently-opened markets have unreliable prices
    if hours_since_open < 12:
        a += 0.10
    if hours_since_open < 6:
        a += 0.05  # Cumulative with above

    return max(0.20, min(0.85, a))


def compute_posterior(
    p_cal: np.ndarray,
    p_market: np.ndarray,
    alpha: float,
) -> np.ndarray:
    """Compute α-weighted posterior, normalized to sum=1.0. Spec §4.5.

    p_posterior = normalize(α × p_cal + (1-α) × p_market)

    p_market sums to vig (~0.95-1.05), not 1.0, so the blend must
    be re-normalized. CLAUDE.md types: p_posterior sums to 1.0.
    """
    raw = alpha * p_cal + (1.0 - alpha) * p_market
    total = raw.sum()
    if total > 0:
        return raw / total
    return raw
