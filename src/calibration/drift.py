"""Calibration drift detection. Spec §3.7.

Hosmer-Lemeshow χ² test on last 50 pairs per bucket.
8/20 directional failure emergency flag.
Seasonal recalibration trigger dates.
"""

import logging
from datetime import date

import numpy as np
from scipy.stats import chi2

logger = logging.getLogger(__name__)

# Spec §3.7: χ² > 7.81 (df=3, p<0.05)
HL_THRESHOLD = 7.81
HL_GROUPS = 4  # Number of bins for H-L test
HL_DF = HL_GROUPS - 1  # Degrees of freedom = 3

# Directional failure emergency
DIRECTIONAL_WINDOW = 20
DIRECTIONAL_FAIL_THRESHOLD = 8  # 8/20 misses → emergency

# Seasonal boundary dates (spec §3.7)
SEASONAL_DATES = ["03-20", "06-21", "09-22", "12-21"]


def hosmer_lemeshow(
    p_forecasts: list[float],
    outcomes: list[int],
    n_groups: int = HL_GROUPS,
) -> tuple[float, bool]:
    """Hosmer-Lemeshow goodness-of-fit test. Spec §3.7.

    Args:
        p_forecasts: predicted probabilities
        outcomes: actual outcomes (0/1)
        n_groups: number of bins for the test

    Returns: (chi2_statistic, is_drifted)
    """
    if len(p_forecasts) < n_groups * 2:
        return 0.0, False  # Not enough data for meaningful test

    p = np.array(p_forecasts)
    o = np.array(outcomes)

    # Sort by predicted probability and divide into groups
    idx = np.argsort(p)
    p_sorted = p[idx]
    o_sorted = o[idx]

    group_size = len(p) // n_groups
    chi2_stat = 0.0

    for g in range(n_groups):
        start = g * group_size
        end = start + group_size if g < n_groups - 1 else len(p)

        group_p = p_sorted[start:end]
        group_o = o_sorted[start:end]
        n_g = len(group_p)

        if n_g == 0:
            continue

        expected = float(group_p.sum())
        observed = float(group_o.sum())

        if expected > 0 and expected < n_g:
            chi2_stat += (observed - expected) ** 2 / (expected * (1 - expected / n_g))

    is_drifted = chi2_stat > HL_THRESHOLD
    return float(chi2_stat), is_drifted


def directional_failure_check(
    p_forecasts: list[float],
    outcomes: list[int],
) -> bool:
    """Check for 8/20 directional misses. Spec §3.7: emergency flag.

    Returns True if >= 8 of last 20 predictions had wrong direction.
    """
    if len(p_forecasts) < DIRECTIONAL_WINDOW:
        return False

    recent_p = p_forecasts[-DIRECTIONAL_WINDOW:]
    recent_o = outcomes[-DIRECTIONAL_WINDOW:]

    misses = sum(
        1 for p, o in zip(recent_p, recent_o)
        if (p > 0.5 and o == 0) or (p <= 0.5 and o == 1)
    )

    return misses >= DIRECTIONAL_FAIL_THRESHOLD


def is_seasonal_boundary(today: date) -> bool:
    """Check if today is a seasonal recalibration trigger date. Spec §3.7."""
    today_str = today.strftime("%m-%d")
    return today_str in SEASONAL_DATES
