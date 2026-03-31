"""Market fusion: α-weighted posterior and VWMP.

Spec §4.5: p_posterior = α × p_cal + (1-α) × p_market
α depends on calibration maturity, ensemble spread, model agreement,
lead time, and market freshness.
"""

import numpy as np

from src.config import settings
from src.types.temperature import TemperatureDelta

# Spread thresholds defined in °F, auto-converted via .to() for any unit.
# This prevents the Rainstorm bug where 2.0 was used for both °F and °C cities.
# HARDCODED(setting_key="edge.spread_tight_f", note_key="edge._spread_tight_f_note",
#           tier=2, replace_after="1000+ ENS snapshots per city",
#           data_needed="per-city spread distribution percentiles")
SPREAD_TIGHT = TemperatureDelta(settings["edge"]["spread_tight_f"], "F")
# HARDCODED(setting_key="edge.spread_wide_f", note_key="edge._spread_wide_f_note",
#           tier=2, replace_after="1000+ ENS snapshots per city",
#           data_needed="per-city spread distribution percentiles")
SPREAD_WIDE = TemperatureDelta(settings["edge"]["spread_wide_f"], "F")

# HARDCODED(setting_key="edge.base_alpha", note_key="edge._base_alpha_note",
#           tier=1, replace_after="100+ settlements",
#           data_needed="Model Brier vs Market Brier per calibration level")
BASE_ALPHA_BY_LEVEL = {
    1: settings["edge"]["base_alpha"]["level1"],
    2: settings["edge"]["base_alpha"]["level2"],
    3: settings["edge"]["base_alpha"]["level3"],
    4: settings["edge"]["base_alpha"]["level4"],
}


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
    ensemble_spread: TemperatureDelta | float,
    model_agreement: str,
    lead_days: float,
    hours_since_open: float,
) -> float:
    """Compute α for model-market blending. Spec §4.5.

    Higher α → trust model more. Lower α → trust market more.
    Clamped to [0.20, 0.85].

    ensemble_spread accepts both TemperatureDelta (preferred) and float (legacy).
    When typed, thresholds auto-convert to the correct unit.
    """
    base = BASE_ALPHA_BY_LEVEL[calibration_level]
    a = base

    # Ensemble spread adjustments — typed thresholds prevent °C/°F confusion
    if isinstance(ensemble_spread, TemperatureDelta):
        tight = SPREAD_TIGHT.to(ensemble_spread.unit)
        wide = SPREAD_WIDE.to(ensemble_spread.unit)
        if ensemble_spread < tight:
            a += 0.05
        if ensemble_spread > wide:
            a -= 0.10
    else:
        # Legacy float path (will be removed after full migration)
        tight_f = float(settings["edge"]["spread_tight_f"])
        wide_f = float(settings["edge"]["spread_wide_f"])
        if ensemble_spread < tight_f:
            a += 0.05
        if ensemble_spread > wide_f:
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
