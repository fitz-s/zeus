# Created: 2026-05-22
# Last reused or audited: 2026-05-23
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-D;
#   /Users/leofitz/.claude/jobs/866db2ea/P0_FOLLOWUP_BUNDLE_LAYER_SPEC.md §3, §4
"""Probability sanity validator for HIGH distribution outputs.

Gates:
  - non-finite p_raw / p_cal              → "non_finite_probability"
  - sum(p_raw) not 1 (±1e-6)             → "P_RAW_NOT_CATEGORICAL:{sum}"
  - sum(p_cal) not 1 (±1e-3)             → "P_CAL_NOT_CATEGORICAL:{sum}"
  - point bucket (low≈high) is mode,
    p_cal[mode]>0.5, member support<0.25  → "POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT:p={},support={}"
  - market px<0.05 AND p_cal>0.35         → "EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB:idx={},price={},p={}"
  - else                                  → (True, None)

§3 caller contract: ``member_samples`` MUST be in the SAME value space as the
generator of ``p_raw``/``p_cal``.  p_raw/p_cal are built from settlement-ROUNDED
samples; the point-bucket support count compares samples against the bin's
[low, high] bounds, so the caller must hand SETTLEMENT-ROUNDED member samples
(``settlement_semantics.round_values(member_extrema)``), not raw member extrema.
A raw 22.6°C member that settles to 23°C otherwise counts as 0 support for the
[23,23] bin and false-blocks near boundaries.  The validator stays sample-space-
agnostic; the caller (evaluator.py) owns the rounding.

§4 thresholds (point_bucket_high_prob, min_member_support, low_price_threshold,
low_price_high_prob) are read from settings.json ``probability_sanity`` with the
CURRENT hardcoded values as defaults — behavior is unchanged if the block is absent.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

# §4: current threshold defaults (behavior unchanged if config block absent).
_DEFAULT_POINT_BUCKET_HIGH_PROB = 0.50
_DEFAULT_MIN_MEMBER_SUPPORT = 0.25
_DEFAULT_LOW_PRICE_THRESHOLD = 0.05
_DEFAULT_LOW_PRICE_HIGH_PROB = 0.35


def _sanity_thresholds() -> dict[str, float]:
    """Read probability_sanity thresholds from settings, defaulting to the
    current hardcoded values.  Defensive: a missing block or missing key falls
    back to the default, so the gate's behavior is unchanged when config is absent.
    """
    block: dict[str, Any] = {}
    try:
        from src.config import settings

        try:
            raw = settings["probability_sanity"]
        except (KeyError, TypeError):
            raw = None
        if isinstance(raw, dict):
            block = raw
    except Exception:
        block = {}
    return {
        "point_bucket_high_prob": float(
            block.get("point_bucket_high_prob", _DEFAULT_POINT_BUCKET_HIGH_PROB)
        ),
        "min_member_support": float(
            block.get("min_member_support", _DEFAULT_MIN_MEMBER_SUPPORT)
        ),
        "low_price_threshold": float(
            block.get("low_price_threshold", _DEFAULT_LOW_PRICE_THRESHOLD)
        ),
        "low_price_high_prob": float(
            block.get("low_price_high_prob", _DEFAULT_LOW_PRICE_HIGH_PROB)
        ),
    }


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
    thresholds = _sanity_thresholds()

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
        if is_point_bucket and float(p_cal[mode_idx]) > thresholds["point_bucket_high_prob"]:
            # Member support: fraction of members whose value falls in [low, high]
            lo_f, hi_f = float(low), float(high)
            in_bucket = np.logical_and(member_samples >= lo_f, member_samples <= hi_f)
            support = float(in_bucket.mean()) if member_samples.size > 0 else 0.0
            if support < thresholds["min_member_support"]:
                return (
                    False,
                    f"POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT:"
                    f"p={float(p_cal[mode_idx]):.4f},support={support:.4f}",
                )

    # --- Gate 5: extreme market disagreement ---
    # px == 0.0 means NO QUOTE (bin unquoted/non-executable/unmapped), NOT
    # "market says impossible" — p_market is zero-initialized and only filled
    # for executable+quoted bins. Require a real quote (0.0 < px) so an
    # unquoted bin carrying high p_cal does not spuriously trip the gate.
    if market_prices is not None:
        market_prices = np.asarray(market_prices, dtype=np.float64)
        low_price = thresholds["low_price_threshold"]
        high_prob = thresholds["low_price_high_prob"]
        for i, (px, pc) in enumerate(zip(market_prices, p_cal)):
            if 0.0 < float(px) < low_price and float(pc) > high_prob:
                return (
                    False,
                    f"EXTREME_MARKET_DISAGREEMENT_LOW_PRICE_HIGH_PROB:"
                    f"idx={i},price={float(px):.4f},p={float(pc):.4f}",
                )

    return True, None
