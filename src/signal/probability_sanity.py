# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-D
"""Probability sanity validator for HIGH distribution outputs.

Gates:
  - non-finite p_raw / p_cal              → "non_finite_probability"
  - sum(p_raw) not 1 (±1e-6)             → "P_RAW_NOT_CATEGORICAL:{sum}"
  - sum(p_cal) not 1 (±1e-3)             → "P_CAL_NOT_CATEGORICAL:{sum}"
  - point bucket (low≈high) is mode,
    p_cal[mode]>0.5, member support<0.25  → "POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT:p={},support={}"
  - market px<0.05 AND p_cal>0.35         → "EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB:idx={},price={},p={}"
  - else                                  → (True, None)
"""
from __future__ import annotations

from typing import Sequence

import numpy as np


def validate_high_distribution(
    *,
    bins: Sequence,
    p_raw: np.ndarray,
    p_cal: np.ndarray,
    member_samples: np.ndarray,
    market_prices: np.ndarray | None,
    strategy_key: str,
) -> tuple[bool, str | None]:
    """Validate a calibrated HIGH probability distribution before Kelly sizing.

    Args:
        bins: sequence of Bin-like objects each with attributes ``low`` and
              ``high`` (float or None for open buckets).
        p_raw: raw (uncalibrated) probability array, shape (n_bins,).
        p_cal: calibrated probability array, shape (n_bins,).
        member_samples: ensemble member samples (1-D array of floats).
        market_prices: per-bin market prices as probabilities, shape (n_bins,),
                       or None to skip market-disagreement check.
        strategy_key: opaque label for logging context (not validated).

    Returns:
        (True, None) if all gates pass.
        (False, reason_code) on the first failure, where reason_code is one of:
          "non_finite_probability"
          "P_RAW_NOT_CATEGORICAL:{sum:.8g}"
          "P_CAL_NOT_CATEGORICAL:{sum:.8g}"
          "POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT:p={:.4f},support={:.4f}"
          "EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB:idx={i},price={px:.4f},p={pc:.4f}"
    """
    p_raw = np.asarray(p_raw, dtype=np.float64)
    p_cal = np.asarray(p_cal, dtype=np.float64)
    member_samples = np.asarray(member_samples, dtype=np.float64)

    # --- Gate 1: non-finite values ---
    if not (np.all(np.isfinite(p_raw)) and np.all(np.isfinite(p_cal))):
        return False, "non_finite_probability"

    # --- Gate 2: p_raw categorical (±1e-6) ---
    raw_sum = float(p_raw.sum())
    if abs(raw_sum - 1.0) > 1e-6:
        return False, f"P_RAW_NOT_CATEGORICAL:{raw_sum:.8g}"

    # --- Gate 3: p_cal categorical (±1e-3) ---
    cal_sum = float(p_cal.sum())
    if abs(cal_sum - 1.0) > 1e-3:
        return False, f"P_CAL_NOT_CATEGORICAL:{cal_sum:.8g}"

    # --- Gate 4: point-bucket mode with high p_cal and low member support ---
    mode_idx = int(np.argmax(p_cal))
    mode_bin = bins[mode_idx]
    low = getattr(mode_bin, "low", None)
    high = getattr(mode_bin, "high", None)

    # Only check when both bounds are defined (skip open buckets)
    if low is not None and high is not None:
        is_point_bucket = abs(float(high) - float(low)) < 1e-9
        if is_point_bucket and float(p_cal[mode_idx]) > 0.5:
            # Member support: fraction of members whose value falls in [low, high]
            lo_f, hi_f = float(low), float(high)
            in_bucket = np.logical_and(member_samples >= lo_f, member_samples <= hi_f)
            support = float(in_bucket.mean()) if member_samples.size > 0 else 0.0
            if support < 0.25:
                return (
                    False,
                    f"POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT:"
                    f"p={float(p_cal[mode_idx]):.4f},support={support:.4f}",
                )

    # --- Gate 5: extreme market disagreement ---
    if market_prices is not None:
        market_prices = np.asarray(market_prices, dtype=np.float64)
        for i, (px, pc) in enumerate(zip(market_prices, p_cal)):
            if float(px) < 0.05 and float(pc) > 0.35:
                return (
                    False,
                    f"EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB:"
                    f"idx={i},price={float(px):.4f},p={float(pc):.4f}",
                )

    return True, None
